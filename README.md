# Antigravity Terminal MCP

Windows-focused MCP server for terminal automation with persistent sessions and live session logs.
Created by AATHI.

## Features
- Persistent PowerShell sessions (`session_id`, default: `default`)
- Incremental `stdout`/`stderr` polling
- Send follow-up input into running sessions
- Live log file path returned for each session
- Configurable monitor mode via `ANTIGRAVITY_MONITOR_MODE`
- VS Code integrated terminal metadata returned for each session
- MCP standard methods (`tools/list`, `tools/call`) with legacy aliases

## MCP Tools
1. `run_command`
2. `command_status`
3. `send_input`
4. `list_sessions`
5. `session_visual_info`
6. `kill_command`

## Publish-Ready One-Command Usage (From GitHub)

After pushing this project to your GitHub, users can seamlessly install and attach it to Claude using `uvx` (or the more robust `python -m uv` variant for Windows):

```bash
# Standard uvx (if in PATH)
claude mcp add antigravity-terminal-mcp uvx --from git+https://github.com/aathishwar-13/antigravity-terminal-mcp.git antigravity-terminal-mcp

# More robust Windows version (if uvx is not in PATH)
claude mcp add antigravity-terminal-mcp python -m uv tool run --from git+https://github.com/aathishwar-13/antigravity-terminal-mcp.git antigravity-terminal-mcp
```

*(Note: If the code is nested in a subdirectory, simply append `#subdirectory=folder_name` to the git URL).*

## Local Run

```powershell
uvx --from . antigravity-terminal-mcp
```

or

```powershell
python server.py
```

## Monitor Modes

By default, the server does not open an external monitor window (`ANTIGRAVITY_MONITOR_MODE=none`).
This keeps monitoring in VS Code when you use the integrated terminal bridge.

If you explicitly want a separate PowerShell window for each session monitor, enable external mode:

```powershell
$env:ANTIGRAVITY_MONITOR_MODE = "external"
python server.py
```

To force no external monitor window:

```powershell
$env:ANTIGRAVITY_MONITOR_MODE = "none"
python server.py
```

When `run_command` or `session_visual_info` is called, the response now includes:

- `log_path`
- `watch_command`
- `monitor_mode`
- `vscode_terminal`
- `monitor_open` (quick-open metadata for clickable/open actions)

`monitor_open` includes:

- `command_uri` (VS Code command URI for `antigravityTerminal.openMonitor`)
- `markdown_link` (prebuilt markdown clickable link for chat UIs)
- `integrated_terminal_command` (copy/paste command to open live monitor in integrated terminal)
- `open_terminal_uri` (opens a VS Code integrated terminal)
- `open_terminal_with_cwd_uri` (opens integrated terminal in session cwd)
- `run_in_active_terminal_uri` (runs monitor command in active integrated terminal)

The `vscode_terminal` object is designed for a VS Code extension to open the same session monitor inside an integrated terminal.

## VS Code Integrated Terminal Bridge

A lightweight extension scaffold lives in `vscode-integration/`.

### What it does

- Opens an integrated PowerShell terminal in the current VS Code window
- Runs the returned `watch_command` / `vscode_terminal.command`
- Lets you paste either the full `session_visual_info` JSON or just the `log_path`
- Shows a status-bar button: `MCP Monitor` (bottom-right) to pick and open any existing session monitor
- Auto-detects `session_logs/*.log` create/change activity and shows an `Open Monitor` popup action

### How to try it locally

1. Open `antigravity_mcp/vscode-integration` in VS Code.
2. Press `F5` to launch the extension host.
3. Run `Antigravity: Open Monitor From Session Info JSON` from the Command Palette.
4. Paste the JSON returned by `session_visual_info` or `run_command`.

### Where to click

- Bottom-right VS Code status bar: click `MCP Monitor` and choose a session
- On new session creation: click `Open Monitor` in the popup notification

This uses the VS Code extension API, which is the correct way to open an integrated terminal in the current editor window. A plain Python process cannot directly create a terminal tab inside VS Code by itself.

## Quick Integrated Monitor Command (No Copy/Paste)

Use the helper script from a VS Code integrated terminal:

```powershell
Set-Location d:\Projects\Client_projects\Velyx_Chatbot\antigravity_mcp
.\open-monitor.ps1 -SessionId live-agent
```

Optional short alias for the current terminal session:

```powershell
Set-Alias agmwatch .\open-monitor.ps1
agmwatch -SessionId live-agent
```

This keeps monitoring inside VS Code and does not open an external terminal window.

## Example MCP Config

```json
{
  "mcpServers": {
    "antigravity-terminal-mcp": {
      "command": "python",
      "args": [
        "-m",
        "uv",
        "tool",
        "run",
        "--from",
        "git+https://github.com/aathishwar-13/antigravity-terminal-mcp.git",
        "antigravity-terminal-mcp"
      ]
    }
  }
}
```
Or 
```json

{
  "mcpServers": {
    "antigravity-terminal-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/AATHI/antigravity-terminal-mcp.git",
        "antigravity-terminal-mcp"
      ]
    }
  }
}
```

---

## 👨‍💻 About the Author

**AATHI** is a developer focused on building high-performance, developer-centric automation tools for the next generation of AI agents. With a specialization in bridging the gap between headless LLM execution and rich user-facing terminal interactions, AATHI created this tool to provide a portable, professional terminal experience beyond the standard VS Code integration limits.

Feel free to reach out for feedback or technical support via this repository!
