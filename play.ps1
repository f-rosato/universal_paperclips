<#
Launch the game (dev.py) and the Playwright harness, wait until both are
ready, then keep running until Ctrl+C (which stops whatever this script
started). Afterwards, any Claude session with the "paperclips" MCP server
(.mcp.json) can play. Windows equivalent of play.sh.

  .\play.ps1                                  # game on :8100, harness API on :8765
  .\play.ps1 -GamePort 8200 -HarnessPort 8766
  $env:GAME_PORT=8200; .\play.ps1             # env vars work too

If a game server or harness is already answering on its port, it is reused
instead of starting a second one.

If scripts are blocked by execution policy:
  powershell -ExecutionPolicy Bypass -File .\play.ps1
#>
param(
  [int]$GamePort = $(if ($env:GAME_PORT) { [int]$env:GAME_PORT } else { 8100 }),
  [int]$HarnessPort = $(if ($env:HARNESS_PORT) { [int]$env:HARNESS_PORT } else { 8765 })
)

$ProgressPreference = 'SilentlyContinue'   # keep Invoke-WebRequest quiet

$Root = $PSScriptRoot
$GameUrl = "http://127.0.0.1:$GamePort/"
$HarnessUrl = "http://127.0.0.1:$HarnessPort"
$LogDir = Join-Path $Root '.logs'

function Write-Play([string]$Msg, [string]$Color = 'Gray') {
  Write-Host "[play] $Msg" -ForegroundColor $Color
}

function Test-Up([string]$Url) {
  try {
    $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    return $true
  } catch {
    return $false
  }
}

# Start python with stdout+stderr merged into one log. Runs through cmd.exe
# because Start-Process can't send both streams to the same file; cleanup
# kills the whole process tree, so python (and Chromium) go down with it.
function Start-Logged([string[]]$PyArgs, [string]$Log) {
  $quoted = ($PyArgs | ForEach-Object { '"' + $_ + '"' }) -join ' '
  $cmdLine = "/d /s /c `"`"$Py`" -u $quoted > `"$Log`" 2>&1`""
  Start-Process -FilePath $env:ComSpec -ArgumentList $cmdLine `
    -WorkingDirectory $Root -NoNewWindow -PassThru
}

# Poll until Url answers; fail if the process dies or 30s pass.
function Wait-For([string]$Url, [string]$Name, $Proc, [string]$Log) {
  $deadline = (Get-Date).AddSeconds(30)
  while ((Get-Date) -lt $deadline) {
    if (Test-Up $Url) { return }
    if ($Proc.HasExited) {
      Write-Play "$Name exited during startup; last log lines:" Red
      if (Test-Path $Log) { Get-Content $Log -Tail 20 | Write-Host }
      throw "$Name failed to start"
    }
    Start-Sleep -Milliseconds 500
  }
  throw "$Name not ready after 30s; see $Log"
}

$venvWin = Join-Path $Root '.venv\Scripts\python.exe'
$venvNix = Join-Path $Root '.venv/bin/python'
if (Test-Path $venvWin) { $Py = $venvWin }
elseif (Test-Path $venvNix) { $Py = $venvNix }
else {
  Write-Play 'no .venv found (expected .venv with playwright + mcp)' Red
  exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$procs = @()
$exitCode = 0
$reader = $null

try {
  $devLog = Join-Path $LogDir 'dev.log'
  if (Test-Up $GameUrl) {
    Write-Play "game server already running at $GameUrl (reusing)"
  } else {
    Write-Play "starting game server on :$GamePort (log: .logs\dev.log)"
    $p = Start-Logged @('dev.py', '--no-open', '--port', "$GamePort") $devLog
    $procs += $p
    Wait-For $GameUrl 'game server' $p $devLog
  }

  $harnessLog = Join-Path $LogDir 'harness.log'
  if (Test-Up "$HarnessUrl/observe") {
    Write-Play "harness already running at $HarnessUrl (reusing)"
  } else {
    Write-Play "starting harness on :$HarnessPort (log: .logs\harness.log)"
    $p = Start-Logged @('harness.py', '--url', $GameUrl, '--port', "$HarnessPort") $harnessLog
    $procs += $p
    Wait-For "$HarnessUrl/observe" 'harness' $p $harnessLog
  }

  Write-Play "READY: game $GameUrl | harness $HarnessUrl | MCP server 'paperclips' via .mcp.json" Green
  if ($procs.Count -eq 0) {
    Write-Play 'nothing started by this script; exiting'
    return
  }

  # Tail new harness log lines until Ctrl+C or every started process exits.
  Write-Play 'tailing harness actions (Ctrl+C to stop everything)'
  if (-not (Test-Path $harnessLog)) { New-Item -ItemType File -Path $harnessLog | Out-Null }
  $fs = [IO.File]::Open($harnessLog, 'Open', 'Read', 'ReadWrite, Delete')
  $null = $fs.Seek(0, 'End')
  $reader = New-Object IO.StreamReader($fs)
  while ($true) {
    $line = $reader.ReadLine()
    if ($null -ne $line) { Write-Host $line; continue }
    if (-not ($procs | Where-Object { -not $_.HasExited })) {
      Write-Play 'all started processes exited'
      break
    }
    Start-Sleep -Milliseconds 300
  }
} catch {
  Write-Play "$_" Red
  $exitCode = 1
} finally {
  # Runs on normal exit, errors, and Ctrl+C.
  if ($reader) { $reader.Dispose() }
  $alive = @($procs | Where-Object { -not $_.HasExited })
  if ($alive.Count -gt 0) {
    Write-Play "stopping ($(($alive | ForEach-Object { $_.Id }) -join ' '))"
    foreach ($p in $alive) {
      & taskkill.exe /T /F /PID $p.Id 2>&1 | Out-Null
    }
  }
}
exit $exitCode
