const vscode = require("vscode");
const path = require("path");

let lastMonitorInfo = null;
const monitorTerminalsBySession = new Map();

function escapePsPath(rawPath) {
  return String(rawPath || "").replace(/'/g, "''");
}

function normalizeMonitorInfo(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  const vscodeTerminal = raw.vscode_terminal && typeof raw.vscode_terminal === "object"
    ? raw.vscode_terminal
    : {};

  const command = vscodeTerminal.command || raw.command || raw.watch_command;
  const terminalName = vscodeTerminal.terminal_name || raw.terminal_name || `${raw.session_id || "session"} - Antigravity`;
  const cwd = vscodeTerminal.cwd || raw.cwd;

  if (!command) {
    return null;
  }

  return {
    command,
    cwd,
    shellPath: vscodeTerminal.shell_path || "powershell.exe",
    terminalName,
    sessionId: raw.session_id || raw.sessionId,
    logPath: raw.log_path || raw.logPath
  };
}

function buildMonitorInfoFromLogPath(logPath) {
  const parsed = path.parse(logPath);
  const sessionId = parsed.name || "session";
  const escapedPath = escapePsPath(logPath);

  return {
    command: `Get-Content -Path '${escapedPath}' -Wait -Tail 100`,
    cwd: path.dirname(path.dirname(logPath)),
    shellPath: "powershell.exe",
    terminalName: `${sessionId} - Antigravity`,
    sessionId,
    logPath
  };
}

function buildResilientMonitorCommand(info) {
  if (!info || !info.logPath) {
    return info?.command;
  }

  const escapedPath = escapePsPath(info.logPath);
  const sessionLabel = info.sessionId || "session";
  // Persistent monitor: tails log forever, never auto-exits.
  // User closes this terminal manually when done.
  return [
    `Write-Output ''`,
    `Write-Output '========================================'`,
    `Write-Output '  Antigravity Monitor: ${sessionLabel}'`,
    `Write-Output '========================================'`,
    `$agLogPath = '${escapedPath}'`,
    `Write-Output "[INFO] Log: $agLogPath"`,
    `Write-Output '[INFO] Monitor is persistent. Close this terminal manually when done.'`,
    `Write-Output ''`,
    "while ($true) {",
    "  try {",
    "    if (Test-Path -LiteralPath $agLogPath) {",
    "      $reader = New-Object System.IO.StreamReader(",
    "        [System.IO.File]::Open($agLogPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)",
    "      )",
    "      $lines = @()",
    "      while ($null -ne ($line = $reader.ReadLine())) { $lines += $line }",
    "      $startIdx = [Math]::Max(0, $lines.Count - 100)",
    "      for ($i = $startIdx; $i -lt $lines.Count; $i++) { Write-Output $lines[$i] }",
    "      while ($true) {",
    "        $line = $reader.ReadLine()",
    "        if ($null -ne $line) { Write-Output $line }",
    "        else { Start-Sleep -Milliseconds 250 }",
    "      }",
    "    } else {",
    "      Write-Output \"[WARN] Waiting for log file...\"",
    "      Start-Sleep -Seconds 1",
    "    }",
    "  } catch {",
    "    Write-Output \"[WARN] Monitor stream restarted\"",
    "    Start-Sleep -Seconds 1",
    "  }",
    "}"
  ].join("\n");
}

function sessionKeyForInfo(info) {
  return info?.sessionId || info?.logPath || info?.terminalName;
}

function cleanupTerminalMappings() {
  for (const [key, terminal] of monitorTerminalsBySession.entries()) {
    if (!vscode.window.terminals.includes(terminal)) {
      monitorTerminalsBySession.delete(key);
    }
  }
}

function findMonitorTerminal(terminalName) {
  return vscode.window.terminals.find((terminal) => terminal.name === terminalName);
}

function saveLastMonitorInfo(context, info) {
  lastMonitorInfo = info;
  void context.workspaceState.update("antigravityTerminal.lastMonitorInfo", info);
}

function loadLastMonitorInfo(context) {
  const saved = context.workspaceState.get("antigravityTerminal.lastMonitorInfo");
  if (saved && typeof saved === "object") {
    lastMonitorInfo = saved;
  }
}

async function promptForMonitorInfo() {
  const raw = await vscode.window.showInputBox({
    title: "Antigravity Monitor",
    prompt: "Paste session_visual_info JSON or enter a log file path",
    ignoreFocusOut: true
  });

  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw);
    const info = normalizeMonitorInfo(parsed);
    if (info) {
      return info;
    }
  } catch (error) {
    // Fall back to treating the input as a path.
  }

  return buildMonitorInfoFromLogPath(raw);
}

function openMonitorTerminal(info) {
  cleanupTerminalMappings();

  const key = sessionKeyForInfo(info);
  if (key && monitorTerminalsBySession.has(key)) {
    const existingByKey = monitorTerminalsBySession.get(key);
    if (existingByKey && vscode.window.terminals.includes(existingByKey)) {
      existingByKey.show(true);
      return existingByKey;
    }
    monitorTerminalsBySession.delete(key);
  }

  const existing = findMonitorTerminal(info.terminalName);
  if (existing) {
    if (key) {
      monitorTerminalsBySession.set(key, existing);
    }
    existing.show(true);
    return existing;
  }

  const terminal = vscode.window.createTerminal({
    name: info.terminalName,
    cwd: info.cwd,
    shellPath: info.shellPath
  });

  terminal.show(true);

  // Give shell integrations a brief moment before sending long-running command.
  setTimeout(() => {
    terminal.sendText(buildResilientMonitorCommand(info), true);
  }, 400);

  if (key) {
    monitorTerminalsBySession.set(key, terminal);
  }

  return terminal;
}

async function getSessionLogFiles() {
  const uris = await vscode.workspace.findFiles("**/session_logs/*.log", "**/{.git,node_modules}/**", 200);
  const withStats = [];

  for (const uri of uris) {
    try {
      const stat = await vscode.workspace.fs.stat(uri);
      withStats.push({ uri, mtime: stat.mtime });
    } catch (error) {
      withStats.push({ uri, mtime: 0 });
    }
  }

  withStats.sort((a, b) => b.mtime - a.mtime);
  return withStats;
}

async function promptOpenMonitor(context, info, promptSource) {
  saveLastMonitorInfo(context, info);

  const action = await vscode.window.showInformationMessage(
    `Antigravity session detected (${promptSource}): ${info.sessionId}. Open live monitor in Terminal?`,
    "Open Monitor",
    "Not now"
  );

  if (action === "Open Monitor") {
    openMonitorTerminal(info);
  }
}

function setupStatusBarButton(context) {
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.name = "Antigravity Monitor";
  statusBar.text = "$(terminal) MCP Monitor v2";
  statusBar.tooltip = "Pick and open an Antigravity session monitor";
  statusBar.command = "antigravityTerminal.openExistingSession";
  statusBar.show();
  context.subscriptions.push(statusBar);
}

function registerSessionLogWatcher(context) {
  const lastPromptAtBySession = new Map();
  const PROMPT_COOLDOWN_MS = 5000;
  const watcher = vscode.workspace.createFileSystemWatcher("**/session_logs/*.log");

  const maybePromptForUri = async (uri, source) => {
    const info = buildMonitorInfoFromLogPath(uri.fsPath);
    const key = info.sessionId || uri.toString();
    saveLastMonitorInfo(context, info);

    // 1. Check if we already have an active monitor terminal for this session
    cleanupTerminalMappings();
    const existing = monitorTerminalsBySession.get(key);
    if (existing && vscode.window.terminals.includes(existing)) {
      // Monitor is already open, no need to prompt
      return;
    }

    // 2. Rate limiting for activity events
    const now = Date.now();
    const lastPromptAt = lastPromptAtBySession.get(key) || 0;
    const isActivityEvent = source === "session activity";
    if (isActivityEvent && (now - lastPromptAt) < PROMPT_COOLDOWN_MS) {
      return;
    }

    lastPromptAtBySession.set(key, now);
    await promptOpenMonitor(context, info, source);
  };

  watcher.onDidCreate((uri) => {
    void maybePromptForUri(uri, "new session log");
  }, null, context.subscriptions);

  watcher.onDidChange((uri) => {
    void maybePromptForUri(uri, "session activity");
  }, null, context.subscriptions);

  // Initialize lastMonitorInfo without prompting on startup
  void (async () => {
    const logs = await getSessionLogFiles();
    if (logs.length > 0) {
      const latest = logs[0].uri;
      const latestInfo = buildMonitorInfoFromLogPath(latest.fsPath);
      saveLastMonitorInfo(context, latestInfo);
      // Removed promptOpenMonitor here to avoid annoying popup on every startup
    }
  })();


  context.subscriptions.push(watcher);
}

function registerCommands(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("antigravityTerminal.openMonitor", async (infoArg) => {
      const info = normalizeMonitorInfo(infoArg) || await promptForMonitorInfo();
      if (!info) {
        vscode.window.showWarningMessage("No Antigravity session info was provided.");
        return;
      }

      openMonitorTerminal(info);
      saveLastMonitorInfo(context, info);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("antigravityTerminal.openMonitorFromJson", async () => {
      const info = await promptForMonitorInfo();
      if (!info) {
        vscode.window.showWarningMessage("No Antigravity session info was provided.");
        return;
      }

      openMonitorTerminal(info);
      saveLastMonitorInfo(context, info);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("antigravityTerminal.reopenLastMonitor", async () => {
      if (!lastMonitorInfo) {
        const info = await promptForMonitorInfo();
        if (!info) {
          vscode.window.showWarningMessage("No Antigravity session info was provided.");
          return;
        }
        openMonitorTerminal(info);
        saveLastMonitorInfo(context, info);
        return;
      }

      openMonitorTerminal(lastMonitorInfo);
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("antigravityTerminal.openExistingSession", async () => {
      let logs = await getSessionLogFiles();
      if (logs.length === 0) {
        vscode.window.showWarningMessage("No Antigravity session logs were found.");
        return;
      }

      const quickPick = vscode.window.createQuickPick();
      quickPick.title = "Open Antigravity Existing Session Monitor";
      quickPick.placeholder = "Select a session log";
      quickPick.matchOnDescription = true;

      const deleteButton = {
        iconPath: new vscode.ThemeIcon('trash'),
        tooltip: 'Delete Session Log'
      };

      const clearAllButton = {
        iconPath: new vscode.ThemeIcon('clear-all'),
        tooltip: 'Clear All Session Logs'
      };

      quickPick.buttons = [clearAllButton];

      const refreshItems = async () => {
        logs = await getSessionLogFiles();
        quickPick.items = logs.map((entry) => {
          const info = buildMonitorInfoFromLogPath(entry.uri.fsPath);
          return {
            label: info.sessionId || "session",
            description: entry.uri.fsPath,
            info,
            buttons: [deleteButton]
          };
        });
      };

      await refreshItems();

      quickPick.onDidTriggerButton(async (e) => {
        if (e === clearAllButton) {
          const confirm = await vscode.window.showWarningMessage('Are you sure you want to delete all session logs?', { modal: true }, 'Yes');
          if (confirm === 'Yes') {
            for (const log of logs) {
              try { await vscode.workspace.fs.delete(log.uri); } catch (err) {}
            }
            quickPick.hide();
            vscode.window.showInformationMessage('All session logs cleared.');
          }
        }
      });

      quickPick.onDidTriggerItemButton(async (e) => {
        if (e.button === deleteButton) {
          try {
            await vscode.workspace.fs.delete(vscode.Uri.file(e.item.info.logPath));
            await refreshItems();
            if (quickPick.items.length === 0) { quickPick.hide(); }
          } catch (err) {
            vscode.window.showErrorMessage('Failed to delete session log.');
          }
        }
      });

      quickPick.onDidAccept(() => {
        const picked = quickPick.selectedItems[0];
        if (picked) {
          openMonitorTerminal(picked.info);
          saveLastMonitorInfo(context, picked.info);
          quickPick.hide();
        }
      });

      quickPick.onDidHide(() => quickPick.dispose());
      quickPick.show();
    })
  );
}

function activate(context) {
  context.subscriptions.push(
    vscode.window.onDidCloseTerminal((terminal) => {
      for (const [key, mapped] of monitorTerminalsBySession.entries()) {
        if (mapped === terminal) {
          monitorTerminalsBySession.delete(key);
        }
      }
    })
  );

  loadLastMonitorInfo(context);
  registerCommands(context);
  setupStatusBarButton(context);
  registerSessionLogWatcher(context);
}

function deactivate() {}

module.exports = {
  activate,
  deactivate
};
