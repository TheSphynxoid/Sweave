# Orchestrator: toggle welcome mode (active_project=null), run regression, restore state.
# NOTE: start_server.py / stop_server.py resolve web.pid relative to CWD -> must run from $sweave.
$ErrorActionPreference = 'Stop'
$sweave = 'C:\Users\user\sweave'
$tmp    = 'C:\Users\user\AppData\Local\Temp\opencode'
$cfg    = Join-Path $env:USERPROFILE '.sweave\config.json'
$bak    = Join-Path $tmp 'config.json.bak'

function Wait-Server {
  param([int]$TimeoutSec = 40)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8100/api/config' -UseBasicParsing -TimeoutSec 2
      if ($r.StatusCode -eq 200) { return $true }
    } catch { Start-Sleep -Milliseconds 700 }
  }
  return $false
}

function Wait-ServerDown {
  param([int]$TimeoutSec = 15)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      Invoke-WebRequest -Uri 'http://127.0.0.1:8100/api/config' -UseBasicParsing -TimeoutSec 2 | Out-Null
      Start-Sleep -Milliseconds 500
    } catch { return $true }
  }
  return $false
}

function Restart-Sweave {
  param([string]$ActiveProject)
  Push-Location $sweave
  try {
    python .\stop_server.py 2>$null
    if (-not (Wait-ServerDown)) {
      # pid file stale/lying: kill whatever actually owns port 8100
      $conn = Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue
      if ($conn) {
        $conn | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
          Write-Host "killing real listener on 8100: PID $_"
          taskkill /F /PID $_ | Out-Null
        }
        if (-not (Wait-ServerDown)) { throw 'server did not stop' }
      } else { throw 'server did not stop' }
    }
    Remove-Item (Join-Path $sweave 'web.pid') -Force -ErrorAction SilentlyContinue
    if ($null -ne $ActiveProject) {
      Set-Content -LiteralPath $cfg -Value ('{"active_project": ' + $(if ($ActiveProject) { '"{0}"' -f $ActiveProject } else { 'null' }) + ', "active_session": null}') -Encoding ASCII
    }
    python .\start_server.py 8100 127.0.0.1
    if (-not (Wait-Server)) { throw 'server did not come up' }
  } finally { Pop-Location }
}

Set-Location $sweave
try {
  Copy-Item $cfg $bak -Force
  Write-Host "config backed up -> $bak"

  Restart-Sweave -ActiveProject ''
  Write-Host 'server up in WELCOME mode'

  Push-Location $tmp
  node sidebar-regression.js
  $code = $LASTEXITCODE
  Pop-Location
  exit $code
}
finally {
  # Restore original config (as found before testing) and leave server running.
  Restart-Sweave -ActiveProject $null
  Copy-Item $bak $cfg -Force
  Write-Host 'config restored; server running'
}

