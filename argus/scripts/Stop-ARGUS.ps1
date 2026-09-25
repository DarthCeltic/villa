[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($null -eq $dockerCommand) {
    Write-Host "ARGUS could not stop: docker.exe is not on PATH." -ForegroundColor Red
    exit 1
}

Push-Location $RepoRoot
try {
    & $dockerCommand.Source compose down
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ARGUS could not stop cleanly: Docker Compose returned exit code $LASTEXITCODE." -ForegroundColor Red
        exit 1
    }
    Write-Host "ARGUS is stopped. Its command token and audit state were preserved." -ForegroundColor Green
}
finally {
    Pop-Location
}
