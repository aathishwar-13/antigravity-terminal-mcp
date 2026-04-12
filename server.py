import sys
import json
import subprocess
import threading
import uuid
import time
import os
import signal
import queue
from pathlib import Path
from urllib.parse import quote

# --- CONFIGURATION ---
DEFAULT_SHELL = "powershell.exe"
SESSION_TIMEOUT = 3600  # 1 hour idle timeout
MONITOR_MODE = os.environ.get("ANTIGRAVITY_MONITOR_MODE", "none").strip().lower() or "none"
SESSION_END_SENTINEL = "[SESSION_ENDED]"
# --- /CONFIGURATION ---

JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602

LOGS_DIR = Path(__file__).resolve().parent / "session_logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

VSCODE_EXTENSION_ID = "aathi-local.antigravity-terminal-vscode"
VSCODE_VSIX_DIR = Path(__file__).resolve().parent / "vscode-integration"


def _find_latest_vsix():
    """Find the latest .vsix file in the vscode-integration directory."""
    if not VSCODE_VSIX_DIR.is_dir():
        return None
    vsix_files = sorted(VSCODE_VSIX_DIR.glob("*.vsix"), key=lambda p: p.stat().st_mtime, reverse=True)
    return vsix_files[0] if vsix_files else None


def _find_all_vscode_clis():
    """Find ALL VS Code-compatible CLIs on the system (code, antigravity, cursor, etc.)."""
    import shutil
    candidates = (
        "code", "code-insiders", "antigravity",
        "cursor", "windsurf", "codium", "vscodium",
    )
    found_clis = []
    
    # Check shutil.which first
    for cmd in candidates:
        path = shutil.which(cmd)
        if path:
            found_clis.append(str(Path(path).resolve()))

    # Fallback checking common Windows LOCALAPPDATA paths if PATH is missing
    if os.name == 'nt':
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            base = Path(local_app_data) / "Programs"
            # Explicit app mappings for their bin directories
            app_mappings = {
                "code": base / "Microsoft VS Code" / "bin" / "code.cmd",
                "code-insiders": base / "Microsoft VS Code Insiders" / "bin" / "code-insiders.cmd",
                "antigravity": base / "Antigravity" / "bin" / "antigravity.cmd",
                "cursor": base / "cursor" / "resources" / "app" / "bin" / "cursor.cmd",
                "windsurf": base / "Windsurf" / "bin" / "windsurf.cmd",
            }
            # Add to found list if exists and not already discovered
            for name, cli_path in app_mappings.items():
                if cli_path.exists():
                    abs_path = str(cli_path.resolve())
                    if abs_path not in found_clis:
                        found_clis.append(abs_path)

    verified_clis = []
    for abs_path in set(found_clis):
        # Valid VS Code CLIs support '--list-extensions'.
        try:
            test = subprocess.run(
                [abs_path, "--list-extensions"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            # Both 0 and "installed" handle different editor behaviors
            if test.returncode == 0 or "installed" in (test.stdout or "").lower() or test.stdout.strip() != "":
                if abs_path not in verified_clis:
                    verified_clis.append(abs_path)
        except Exception:
            continue
            
    return verified_clis



def _is_extension_installed(code_cli):
    """Check if the Antigravity Terminal extension is already installed."""
    try:
        result = subprocess.run(
            [code_cli, "--list-extensions"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        installed = [ext.strip().lower() for ext in result.stdout.splitlines()]
        return VSCODE_EXTENSION_ID.lower() in installed
    except Exception:
        return False


def _get_installed_extension_version(code_cli):
    """Get the version of the currently installed extension, or None."""
    try:
        result = subprocess.run(
            [code_cli, "--list-extensions", "--show-versions"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        for line in result.stdout.splitlines():
            if VSCODE_EXTENSION_ID.lower() in line.strip().lower():
                parts = line.strip().split("@")
                return parts[1] if len(parts) > 1 else None
        return None
    except Exception:
        return None


def _get_vsix_version(vsix_path):
    """Extract version from vsix filename (e.g., antigravity-terminal-vscode-0.0.2.vsix -> 0.0.2)."""
    name = vsix_path.stem
    parts = name.split("-")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] and parts[i][0].isdigit():
            return parts[i]
    return None



def cleanup_old_logs():
    """Remove session logs and their corresponding session metadata if older than 24 hours."""
    log_path = LOGS_DIR / "vscode_auto_install.log"
    def log_msg(msg):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

    try:
        now = time.time()
        one_day = 24 * 3600
        count = 0
        
        # Don't delete the auto_install log itself
        for f in LOGS_DIR.glob("*.log"):
            if f.name == "vscode_auto_install.log":
                continue
            
            if now - f.stat().st_mtime > one_day:
                try:
                    f.unlink()
                    count += 1
                except:
                    pass
        
        if count > 0:
            log_msg(f"[CLEANUP] Deleted {count} session logs older than 24 hours.")
            
    except Exception as e:
        try:
            log_msg(f"[ERROR] Session cleanup failed: {str(e)}")
        except:
            pass


def auto_install_vscode_extension():
    """Auto-install the VS Code extension into ALL detected VS Code variants. Runs silently but logs to a file."""
    log_path = LOGS_DIR / "vscode_auto_install.log"
    
    def log(msg):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

    try:
        vsix_path = _find_latest_vsix()
        if not vsix_path:
            log("[WARN] No .vsix file found in vscode-integration/")
            return

        clis = _find_all_vscode_clis()
        if not clis:
            log("[INFO] No VS Code-compatible CLIs (code, antigravity, etc.) found on system.")
            return

        vsix_version = _get_vsix_version(vsix_path)
        log(f"[INFO] Found latest VSIX: {vsix_path.name} (version: {vsix_version})")
        log(f"[INFO] Found CLIs: {', '.join(clis)}")

        for cli in clis:
            try:
                installed = _is_extension_installed(cli)
                installed_version = _get_installed_extension_version(cli)
                
                log(f"[INFO] Checking {cli}: installed={installed}, version={installed_version}")
                
                needs_install = not installed or (installed_version and vsix_version and installed_version != vsix_version)
                
                if needs_install:
                    log(f"[ACTION] Installing/Upgrading {cli} via {vsix_path.name}...")
                    result = subprocess.run(
                        [cli, "--install-extension", str(vsix_path), "--force"],
                        capture_output=True, text=True, timeout=60,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                    )
                    if result.returncode == 0:
                        log(f"[SUCCESS] {cli} installed successfully. Output: {result.stdout.strip()}")
                    else:
                        log(f"[ERROR] {cli} install failed (code {result.returncode}). Error: {result.stderr.strip()}")
                else:
                    log(f"[SKIP] {cli} already has up-to-date extension (version {installed_version}).")
            except Exception as e:
                log(f"[ERROR] Exception while processing CLI {cli}: {str(e)}")
        
        # Finally, cleanup old session files
        cleanup_old_logs()
                
    except Exception as e:
        try:
            log(f"[CRITICAL] Global auto-install failure: {str(e)}")
        except:
            pass




def ps_single_quote(value):
    return str(value).replace("'", "''")


def build_integrated_monitor_command(session_id, cwd):
    safe_session = ps_single_quote(session_id)
    if cwd:
        safe_cwd = ps_single_quote(cwd)
        return (
            f"Set-Location -LiteralPath '{safe_cwd}'; "
            f".\\open-monitor.ps1 -SessionId '{safe_session}'"
        )
    return f".\\open-monitor.ps1 -SessionId '{safe_session}'"


def build_vscode_command_uri(command_id, args=None):
    payload = json.dumps(args if args is not None else [], separators=(",", ":"))
    encoded = quote(payload, safe="")
    return f"command:{command_id}?{encoded}"


def build_vscode_monitor_command_uri(vscode_terminal_info):
    # VS Code command URIs expect encoded JSON args array.
    return build_vscode_command_uri("antigravityTerminal.openMonitor", [vscode_terminal_info])


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
                    "create_session": {"type": "boolean"},
                    "terminal_name": {"type": "string", "description": "Custom name for the VS Code terminal tab."}
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
        self.processes = {}  # id -> {process, stdout_thread, stderr_thread, stdout_queue, stderr_queue, start_time, command, cwd, mode, terminal_name}
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

    def _spawn_process(self, shell_cmd, cwd, command_text, mode, session_id, terminal_name=None):
        log_path = self._log_path_for_session(session_id)
        self._append_log(log_path, f"\n--- Session {session_id} started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")

        monitor_process = None
        if MONITOR_MODE == "external":
            try:
                # Use custom terminal name or default to session id
                display_name = terminal_name or session_id
                title_cmd = f"$Host.UI.RawUI.WindowTitle = '{ps_single_quote(display_name)} - Antigravity';"
                tail_cmd = f"Get-Content -Path '{ps_single_quote(log_path)}' -Wait -Tail 100"
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

        # Default terminal name if none provided
        if not terminal_name and command_text and command_text != "<persistent session>":
            terminal_name = f"Run: {command_text[:30]}..."

        sentinel_written = threading.Event()

        p_data = {
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
            "terminal_name": terminal_name,
            "sentinel_written": sentinel_written
        }

        # Watcher thread: writes SESSION_ENDED sentinel ONLY on natural exit
        # (skip if kill() already wrote it)
        def _exit_watcher():
            process.wait()
            if not sentinel_written.is_set():
                sentinel_written.set()
                self._append_log(log_path, f"\n{SESSION_END_SENTINEL}\n")

        watcher = threading.Thread(target=_exit_watcher, daemon=True)
        watcher.start()
        p_data["exit_watcher"] = watcher

        return p_data

    def _is_running(self, cmd_id):
        p_data = self.processes.get(cmd_id)
        if not p_data:
            return False
        return p_data["process"].poll() is None

    def _start_persistent_session(self, session_id, cwd=None, terminal_name=None):
        # Start a long-lived PowerShell process that accepts stdin commands.
        shell_cmd = [DEFAULT_SHELL, "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"]
        p_data = self._spawn_process(shell_cmd, cwd, command_text="<persistent session>", mode="persistent", session_id=session_id, terminal_name=terminal_name)
        self.processes[session_id] = p_data
        return session_id

    def ensure_session(self, session_id=None, cwd=None, create_session=False, terminal_name=None):
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
                created_id = self._start_persistent_session(session_id, cwd=cwd, terminal_name=terminal_name)
                return created_id, None, True
            except Exception as e:
                return None, str(e), False

    def run_in_session(self, command, session_id=None, cwd=None, create_session=False, terminal_name=None):
        if not command or not isinstance(command, str):
            return None, "'command' must be a non-empty string", False

        session_id, err, created = self.ensure_session(session_id=session_id, cwd=cwd, create_session=create_session, terminal_name=terminal_name)
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

    def _hard_kill(self, process):
        """Kill process and entire child tree. Uses taskkill /F /T on Windows."""
        pid = process.pid
        try:
            if os.name == 'nt':
                # /F = force, /T = kill child tree
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                # Unix: kill process group
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            # Fallback: terminate directly
            try:
                process.kill()
            except Exception:
                pass

    def kill(self, cmd_id):
        with self.lock:
            if cmd_id in self.processes:
                p_data = self.processes[cmd_id]
                process = p_data["process"]
                log_path = p_data.get("log_path")
                sentinel_written = p_data.get("sentinel_written")
                if process.poll() is None:
                    self._hard_kill(process)
                
                # Write sentinel exactly once (flag prevents exit_watcher from duplicating)
                if log_path and sentinel_written and not sentinel_written.is_set():
                    sentinel_written.set()
                    self._append_log(log_path, f"\n{SESSION_END_SENTINEL}\n")
                
                monitor = p_data.get("monitor_process")
                if monitor and monitor.poll() is None:
                    try:
                        self._hard_kill(monitor)
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
            tail_command = f"Get-Content -Path '{ps_single_quote(log_path)}' -Wait -Tail 100"
            vscode_terminal = {
                "recommended": True,
                "terminal_name": p_data.get("terminal_name") or f"{session_id} - Antigravity",
                "shell_path": DEFAULT_SHELL,
                "cwd": p_data.get("cwd"),
                "command": tail_command,
            }
            integrated_command = build_integrated_monitor_command(session_id, p_data.get("cwd"))
            command_uri = build_vscode_monitor_command_uri(vscode_terminal)
            markdown_link = f"[Open Monitor in VS Code]({command_uri})"
            open_terminal_uri = build_vscode_command_uri("workbench.action.terminal.new")
            open_terminal_with_cwd_uri = build_vscode_command_uri(
                "workbench.action.terminal.newWithCwd",
                [p_data.get("cwd") or ""],
            )
            run_in_active_terminal_uri = build_vscode_command_uri(
                "workbench.action.terminal.sendSequence",
                [{"text": integrated_command + "\r"}],
            )

            monitor_name = p_data.get("terminal_name") or session_id
            return {
                "session_id": session_id,
                "log_path": log_path,
                "watch_command": tail_command,
                "is_running": p_data["process"].poll() is None,
                "monitor_mode": MONITOR_MODE,
                "vscode_terminal": vscode_terminal,
                "monitor_open": {
                    "label": f"Open Live Monitor: {monitor_name}",
                    "command_uri": command_uri,
                    "markdown_link": markdown_link,
                    "integrated_terminal_command": integrated_command,
                    "open_terminal_uri": open_terminal_uri,
                    "open_terminal_with_cwd_uri": open_terminal_with_cwd_uri,
                    "run_in_active_terminal_uri": run_in_active_terminal_uri,
                    "notes": "Use command_uri as a clickable command link if your client supports VS Code command URIs.",
                },
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
            terminal_name=args.get("terminal_name")
        )
        if err:
            return None, {"code": JSONRPC_INVALID_PARAMS, "message": err}

        visual_info = manager.get_session_visual_info(cmd_id)
        monitor_open = visual_info.get("monitor_open") if visual_info else None
        uri = monitor_open.get("command_uri") if monitor_open else None
        markdown_link = monitor_open.get("markdown_link") if monitor_open else None
        integrated_cmd = monitor_open.get("integrated_terminal_command") if monitor_open else None
        open_terminal_uri = monitor_open.get("open_terminal_uri") if monitor_open else None
        open_terminal_with_cwd_uri = monitor_open.get("open_terminal_with_cwd_uri") if monitor_open else None
        run_in_active_terminal_uri = monitor_open.get("run_in_active_terminal_uri") if monitor_open else None
        monitor_text = (
            f"Command sent to session: {cmd_id}\n"
            f"Monitor link: {markdown_link or 'n/a'}\n"
            f"Monitor command URI: {uri or 'n/a'}\n"
            f"Open terminal URI: {open_terminal_uri or 'n/a'}\n"
            f"Open terminal with cwd URI: {open_terminal_with_cwd_uri or 'n/a'}\n"
            f"Run monitor in active terminal URI: {run_in_active_terminal_uri or 'n/a'}\n"
            f"Integrated terminal command: {integrated_cmd or 'n/a'}"
        )

        return {
            "content": [{"type": "text", "text": monitor_text}],
            "command_id": cmd_id,
            "session_id": cmd_id,
            "session_created": created,
            "log_path": visual_info.get("log_path") if visual_info else None,
            "watch_command": visual_info.get("watch_command") if visual_info else None,
            "monitor_mode": visual_info.get("monitor_mode") if visual_info else MONITOR_MODE,
            "vscode_terminal": visual_info.get("vscode_terminal") if visual_info else None,
            "monitor_open": monitor_open,
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
            "serverInfo": {"name": "antigravity-terminal-mcp", "version": "1.2.0"}
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
    # Auto-install VS Code extension in background (never blocks MCP loop)
    threading.Thread(target=auto_install_vscode_extension, daemon=True).start()

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
