import sys
import json
import subprocess
import threading
import uuid
import time
import os
import queue
from pathlib import Path

# --- CONFIGURATION ---
DEFAULT_SHELL = "powershell.exe"
SESSION_TIMEOUT = 3600  # 1 hour idle timeout
# --- /CONFIGURATION ---

JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602

LOGS_DIR = Path(__file__).resolve().parent / "session_logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def build_tools():
    return [
        {
            "name": "run_command",
            "description": "Execute a command in a reusable terminal session. Reuses session_id (or default) unless create_session=true.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "session_id": {"type": "string"},
                    "create_session": {"type": "boolean"}
                },
                "required": ["command"]
            }
        },
        {
            "name": "command_status",
            "description": "Check status and get incremental output from a running command.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command_id": {"type": "string"}
                },
                "required": ["command_id"]
            }
        },
        {
            "name": "send_input",
            "description": "Send text input to the stdin of an active command.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command_id": {"type": "string"},
                    "text": {"type": "string"}
                },
                "required": ["command_id", "text"]
            }
        },
        {
            "name": "list_sessions",
            "description": "List existing terminal sessions and whether they are running.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "session_visual_info",
            "description": "Get live log path and watch command for a session so users can open output visually.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"}
                },
                "required": ["session_id"]
            }
        },
        {
            "name": "kill_command",
            "description": "Terminate a running command by command_id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command_id": {"type": "string"}
                },
                "required": ["command_id"]
            }
        }
    ]

class ProcessManager:
    def __init__(self):
        self.processes = {}  # id -> {process, stdout_thread, stderr_thread, stdout_queue, stderr_queue, start_time, command, cwd, mode}
        self.lock = threading.RLock()
        self.default_session_id = "default"

    def _log_path_for_session(self, session_id):
        safe_id = str(session_id).replace("/", "_").replace("\\", "_")
        return str(LOGS_DIR / f"{safe_id}.log")

    def _append_log(self, log_path, line):
        try:
            with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(line)
        except Exception:
            pass

    def _spawn_process(self, shell_cmd, cwd, command_text, mode, session_id):
        log_path = self._log_path_for_session(session_id)
        self._append_log(log_path, f"\n--- Session {session_id} started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")

        monitor_process = None
        try:
            # Pop open a real, visible PowerShell window tracing the log file for the user
            title_cmd = f"$Host.UI.RawUI.WindowTitle = 'Antigravity Monitor: {session_id}';"
            tail_cmd = f"Get-Content -Path '{log_path}' -Wait -Tail 100"
            monitor_process = subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoExit", "-Command", f"{title_cmd} {tail_cmd}"],
                cwd=cwd,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        except Exception:
            pass

        process = subprocess.Popen(
            shell_cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )

        stdout_queue = queue.Queue()
        stderr_queue = queue.Queue()

        def reader(pipe, q, source):
            try:
                for line in iter(pipe.readline, ''):
                    q.put(line)
                    self._append_log(log_path, line)
                pipe.close()
            except Exception:
                pass

        t_out = threading.Thread(target=reader, args=(process.stdout, stdout_queue, "stdout"), daemon=True)
        t_err = threading.Thread(target=reader, args=(process.stderr, stderr_queue, "stderr"), daemon=True)
        t_out.start()
        t_err.start()

        return {
            "process": process,
            "monitor_process": monitor_process,
            "stdout_thread": t_out,
            "stderr_thread": t_err,
            "stdout_queue": stdout_queue,
            "stderr_queue": stderr_queue,
            "start_time": time.time(),
            "last_activity": time.time(),
            "command": command_text,
            "cwd": cwd,
            "mode": mode,
            "log_path": log_path,
        }

    def _is_running(self, cmd_id):
        p_data = self.processes.get(cmd_id)
        if not p_data:
            return False
        return p_data["process"].poll() is None

    def _start_persistent_session(self, session_id, cwd=None):
        # Start a long-lived PowerShell process that accepts stdin commands.
        shell_cmd = [DEFAULT_SHELL, "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"]
        p_data = self._spawn_process(shell_cmd, cwd, command_text="<persistent session>", mode="persistent", session_id=session_id)
        self.processes[session_id] = p_data
        return session_id

    def ensure_session(self, session_id=None, cwd=None, create_session=False):
        session_id = session_id or self.default_session_id

        with self.lock:
            if create_session:
                # Force a fresh session id if caller asks to create a new one.
                if session_id in self.processes and self._is_running(session_id):
                    session_id = str(uuid.uuid4())

            if session_id in self.processes and self._is_running(session_id):
                if cwd:
                    # Keep shell location aligned when caller provides cwd.
                    self.send_input(session_id, f"Set-Location -LiteralPath '{cwd}'")
                return session_id, None, False

            try:
                created_id = self._start_persistent_session(session_id, cwd=cwd)
                return created_id, None, True
            except Exception as e:
                return None, str(e), False

    def run_in_session(self, command, session_id=None, cwd=None, create_session=False):
        if not command or not isinstance(command, str):
            return None, "'command' must be a non-empty string", False

        session_id, err, created = self.ensure_session(session_id=session_id, cwd=cwd, create_session=create_session)
        if err:
            return None, err, False

        ok = self.send_input(session_id, command)
        if not ok:
            return None, "Failed to send command to session", created

        return session_id, None, created

    def get_output(self, cmd_id):
        with self.lock:
            if cmd_id not in self.processes:
                return None
            
            p_data = self.processes[cmd_id]
            process = p_data["process"]
            p_data["last_activity"] = time.time()
            
            # Collect all currently available output
            stdout_lines = []
            while not p_data["stdout_queue"].empty():
                stdout_lines.append(p_data["stdout_queue"].get())
            
            stderr_lines = []
            while not p_data["stderr_queue"].empty():
                stderr_lines.append(p_data["stderr_queue"].get())
            
            # Check status
            exit_code = process.poll()
            is_running = (exit_code is None)
            
            return {
                "stdout": "".join(stdout_lines),
                "stderr": "".join(stderr_lines),
                "is_running": is_running,
                "exit_code": exit_code,
                "mode": p_data.get("mode"),
            }

    def send_input(self, cmd_id, text):
        with self.lock:
            if cmd_id not in self.processes:
                return False
            
            process = self.processes[cmd_id]["process"]
            if process.poll() is not None:
                return False
            
            try:
                process.stdin.write(text + "\n")
                process.stdin.flush()
                self.processes[cmd_id]["last_activity"] = time.time()
                self._append_log(self.processes[cmd_id].get("log_path"), f"\n[INPUT] {text}\n")
                return True
            except Exception:
                return False

    def kill(self, cmd_id):
        with self.lock:
            if cmd_id in self.processes:
                p_data = self.processes[cmd_id]
                process = p_data["process"]
                if process.poll() is None:
                    process.terminate()
                
                monitor = p_data.get("monitor_process")
                if monitor and monitor.poll() is None:
                    try:
                        monitor.terminate()
                    except Exception:
                        pass
                
                return True
            return False

    def list_sessions(self):
        with self.lock:
            data = []
            now = time.time()
            for cmd_id, p_data in self.processes.items():
                process = p_data["process"]
                data.append({
                    "session_id": cmd_id,
                    "is_running": process.poll() is None,
                    "mode": p_data.get("mode"),
                    "cwd": p_data.get("cwd"),
                    "log_path": p_data.get("log_path"),
                    "uptime_seconds": int(now - p_data.get("start_time", now)),
                    "idle_seconds": int(now - p_data.get("last_activity", now)),
                })
            return data

    def get_session_visual_info(self, session_id):
        with self.lock:
            p_data = self.processes.get(session_id)
            if not p_data:
                return None

            log_path = p_data.get("log_path")
            return {
                "session_id": session_id,
                "log_path": log_path,
                "watch_command": f"Get-Content -Path '{log_path}' -Wait",
                "is_running": p_data["process"].poll() is None,
            }

    def cleanup(self):
        with self.lock:
            process_ids = list(self.processes.keys())

        for cmd_id in process_ids:
            self.kill(cmd_id)

manager = ProcessManager()
SHOULD_EXIT = False

def mcp_respond(request_id, result=None, error=None):
    response = {"jsonrpc": "2.0", "id": request_id}
    if error:
        response["error"] = error
    else:
        response["result"] = result
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()


def call_tool(name, args):
    if name == "run_command":
        cmd_id, err, created = manager.run_in_session(
            args.get("command"),
            session_id=args.get("session_id"),
            cwd=args.get("cwd"),
            create_session=bool(args.get("create_session", False)),
        )
        if err:
            return None, {"code": JSONRPC_INVALID_PARAMS, "message": err}

        visual_info = manager.get_session_visual_info(cmd_id)

        return {
            "content": [{"type": "text", "text": f"Command sent to session: {cmd_id}"}],
            "command_id": cmd_id,
            "session_id": cmd_id,
            "session_created": created,
            "log_path": visual_info.get("log_path") if visual_info else None,
            "watch_command": visual_info.get("watch_command") if visual_info else None,
        }, None

    if name == "command_status":
        status = manager.get_output(args.get("command_id"))
        if not status:
            return None, {"code": JSONRPC_INVALID_PARAMS, "message": "Invalid command_id"}

        resp_text = (
            f"Running: {status['is_running']}\n"
            f"Exit Code: {status['exit_code']}\n\n"
            f"STDOUT:\n{status['stdout']}\n\n"
            f"STDERR:\n{status['stderr']}"
        )
        return {
            "content": [{"type": "text", "text": resp_text}],
            "status": status
        }, None

    if name == "send_input":
        success = manager.send_input(args.get("command_id"), args.get("text"))
        return {
            "content": [{"type": "text", "text": "Input sent" if success else "Failed to send input"}],
            "success": success
        }, None

    if name == "list_sessions":
        sessions = manager.list_sessions()
        text = json.dumps(sessions, indent=2)
        return {
            "content": [{"type": "text", "text": text}],
            "sessions": sessions
        }, None

    if name == "session_visual_info":
        session_id = args.get("session_id")
        info = manager.get_session_visual_info(session_id)
        if not info:
            return None, {"code": JSONRPC_INVALID_PARAMS, "message": "Invalid session_id"}

        return {
            "content": [{"type": "text", "text": json.dumps(info, indent=2)}],
            "visual": info,
        }, None

    if name == "kill_command":
        success = manager.kill(args.get("command_id"))
        return {
            "content": [{"type": "text", "text": "Command terminated" if success else "Invalid command_id"}],
            "success": success
        }, None

    return None, {"code": JSONRPC_METHOD_NOT_FOUND, "message": f"Unknown tool: {name}"}

def handle_request(request):
    global SHOULD_EXIT

    if not isinstance(request, dict):
        return

    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {}) if isinstance(request.get("params", {}), dict) else {}

    if method == "initialize":
        mcp_respond(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {"name": "antigravity-terminal-mcp", "version": "1.0.0"}
        })

    elif method in ("tools/list", "list_tools"):
        mcp_respond(req_id, {"tools": build_tools()})

    elif method in ("tools/call", "call_tool"):
        name = params.get("name")
        args = params.get("arguments", {}) if isinstance(params.get("arguments", {}), dict) else {}
        result, error = call_tool(name, args)
        mcp_respond(req_id, result=result, error=error)

    elif method == "shutdown":
        mcp_respond(req_id, {})

    elif method == "exit":
        SHOULD_EXIT = True
        manager.cleanup()

    elif method == "notifications/initialized":
        return

    elif req_id is not None:
        mcp_respond(req_id, error={"code": JSONRPC_METHOD_NOT_FOUND, "message": f"Method not found: {method}"})

def main():
    # Main MCP standard I/O loop
    for line in sys.stdin:
        try:
            request = json.loads(line)
            handle_request(request)
            if SHOULD_EXIT:
                break
        except Exception as e:
            # Continue serving even if one request is malformed.
            continue

    manager.cleanup()


if __name__ == "__main__":
    main()
