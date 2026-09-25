# ARGUS bootstrap for Windows. Creates .venv, installs the exact pinned runtime from
# runtime/requirements.lock.txt and then ARGUS itself (no dependency resolution), verifies the
# environment against the lock, runs the demo and doctor, and optionally builds the UI.
# Stops at the first failure.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 [-WithUI]
param([switch]$WithUI)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($name, [scriptblock]$block) {
  Write-Host "==> $name"
  & $block
  if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { throw "step failed: $name (exit $LASTEXITCODE)" }
}

if (-not (Test-Path ".venv")) { Step "create .venv" { py -3.11 -m venv .venv } }
$py = Join-Path $root ".venv\Scripts\python.exe"
Step "upgrade pip" { & $py -m pip install --upgrade pip }
Step "install the pinned runtime" { & $py -m pip install -r runtime\requirements.lock.txt }
Step "install ARGUS" { & $py -m pip install --no-deps -e . }
Step "verify the runtime lock" { & $py -m argus.core.runtime_identity --require-match }
Step "demo" { & $py -m argus demo --no-render }
Write-Host "==> doctor (a non-zero exit on a new machine is expected; read its fixes)"
& $py -m argus doctor
if ($WithUI) {
  Push-Location argus\ui
  Step "npm ci" { npm ci --no-audit --no-fund }
  Step "npm run build" { npm run build }
  Pop-Location
}
Write-Host "bootstrap complete. Next: .venv\Scripts\python -m argus.serve observe --port 8787"
