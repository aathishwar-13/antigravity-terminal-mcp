param(
    [Parameter(Mandatory = $true)]
    [string]$SessionId,

    [int]$Tail = 100
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$logPath = Join-Path $scriptRoot (Join-Path "session_logs" ($SessionId + ".log"))

if (-not (Test-Path -LiteralPath $logPath)) {
    Write-Error "Session log not found: $logPath"
    exit 1
}

Write-Output ""
Write-Output "========================================"
Write-Output "  Antigravity Monitor: $SessionId"
Write-Output "========================================"
Write-Output "[INFO] Log: $logPath"
Write-Output "[INFO] Monitor is persistent. Close this terminal manually when done."
Write-Output ""

# Open log with shared read access
$reader = New-Object System.IO.StreamReader(
    [System.IO.File]::Open($logPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
)

# Read all existing lines and show last $Tail
$lines = @()
while ($null -ne ($line = $reader.ReadLine())) {
    $lines += $line
}

$startIdx = [Math]::Max(0, $lines.Count - $Tail)
for ($i = $startIdx; $i -lt $lines.Count; $i++) {
    Write-Output $lines[$i]
}

# Persistent tail: keep reading new lines forever
# Monitor NEVER auto-exits. User closes terminal when done.
while ($true) {
    $line = $reader.ReadLine()
    if ($null -ne $line) {
        Write-Output $line
    } else {
        Start-Sleep -Milliseconds 250
    }
}
