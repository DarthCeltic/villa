[CmdletBinding()]
param(
    [switch]$NoBuild,
    [switch]$NoBrowser,
    [ValidateRange(30, 1800)]
    [int]$StartupTimeoutSeconds = 600,
    [ValidateRange(15, 300)]
    [int]$DockerTimeoutSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$UiUrl = "http://127.0.0.1:8792"
$UiHealthUrl = "$UiUrl/health"
$ObserveHealthUrl = "http://127.0.0.1:18787/api/ready"

function Stop-WithMessage {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "ARGUS could not start: $Message" -ForegroundColor Red
    exit 1
}

function Test-DockerEngine {
    & $script:DockerExe info --format "{{.ServerVersion}}" *> $null
    return $LASTEXITCODE -eq 0
}

function Test-HttpEndpoint {
    param([Parameter(Mandatory = $true)][string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 4
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($null -eq $dockerCommand) {
    Stop-WithMessage "Docker Desktop is not installed or docker.exe is not on PATH. Install Docker Desktop, start it once, and double-click Start-ARGUS.cmd again."
}
$script:DockerExe = $dockerCommand.Source

if (-not (Test-DockerEngine)) {
    $desktopCandidates = @(@(
        $(if (${env:ProgramFiles}) { Join-Path ${env:ProgramFiles} "Docker\Docker\Docker Desktop.exe" }),
        $(if (${env:LOCALAPPDATA}) { Join-Path ${env:LOCALAPPDATA} "Docker\Docker Desktop.exe" })
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })

    if ($desktopCandidates.Count -eq 0) {
        Stop-WithMessage "The Docker engine is not running. Start Docker Desktop, wait until it reports ready, and try again."
    }

    Write-Host "Starting Docker Desktop..." -ForegroundColor Cyan
    Start-Process -FilePath $desktopCandidates[0] -WindowStyle Hidden
    $dockerDeadline = (Get-Date).AddSeconds($DockerTimeoutSeconds)
    while ((Get-Date) -lt $dockerDeadline -and -not (Test-DockerEngine)) {
        Start-Sleep -Seconds 2
    }
    if (-not (Test-DockerEngine)) {
        Stop-WithMessage "Docker Desktop did not become ready within $DockerTimeoutSeconds seconds. Open Docker Desktop to inspect its status, then try again."
    }
}

Push-Location $RepoRoot
try {
    Write-Host "Starting the governed ARGUS stack..." -ForegroundColor Cyan
    $composeArgs = @("compose", "up", "-d")
    if (-not $NoBuild) {
        $composeArgs += "--build"
    }
    & $script:DockerExe @composeArgs
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "Docker Compose returned exit code $LASTEXITCODE."
    }

    $startupDeadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $uiReady = $false
    $observeReady = $false
    while ((Get-Date) -lt $startupDeadline) {
        $uiReady = Test-HttpEndpoint -Url $UiHealthUrl
        $observeReady = Test-HttpEndpoint -Url $ObserveHealthUrl
        if ($uiReady -and $observeReady) {
            break
        }
        Start-Sleep -Seconds 2
    }

    if (-not ($uiReady -and $observeReady)) {
        Write-Host ""
        & $script:DockerExe compose ps
        Stop-WithMessage "The containers started, but the UI and read-only service did not both become healthy within $StartupTimeoutSeconds seconds."
    }

    Write-Host ""
    Write-Host "ARGUS is ready at $UiUrl" -ForegroundColor Green
    Write-Host "The command service remains private inside Docker; scientific actions still require their governed enable flags." -ForegroundColor DarkGray
    if (-not $NoBrowser) {
        Start-Process $UiUrl
    }
}
finally {
    Pop-Location
}
