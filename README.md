# Antigravity Terminal MCP

Windows-focused MCP server for terminal automation with persistent sessions and live session logs.
Created by AATHI.

## Features
- Persistent PowerShell sessions (`session_id`, default: `default`)
- Incremental `stdout`/`stderr` polling
- Send follow-up input into running sessions
- Live log file path returned for each session
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
         "git+https://github.com/aathishwar-13/antigravity-terminal-mcp.git",
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
