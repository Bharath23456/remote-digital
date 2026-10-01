# ADMIEZO GitHub Actions Self-Hosted Runner Setup Script
# Configures and starts the self-hosted runner for instant automatic deployments.

param(
    [string]$RunnerToken = "",
    [string]$RepoUrl = "https://github.com/Bharath23456/remote-digital",
    [string]$RunnerDir = "C:\actions-runner"
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ">> ADMIEZO GITHUB ACTIONS RUNNER SETUP" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Repository : $RepoUrl" -ForegroundColor Gray
Write-Host "Directory  : $RunnerDir" -ForegroundColor Gray

# 1. Ensure directory exists
if (-not (Test-Path $RunnerDir)) {
    Write-Host "`n[CREATE] Creating runner directory at $RunnerDir..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $RunnerDir -Force | Out-Null
}

Set-Location -Path $RunnerDir

# 2. Check if runner is already downloaded
if (-not (Test-Path "$RunnerDir\config.cmd")) {
    Write-Host "[DOWNLOAD] Downloading GitHub Actions runner v2.322.0..." -ForegroundColor Yellow
    $runnerZip = "$RunnerDir\actions-runner-win-x64.zip"
    $downloadUrl = "https://github.com/actions/runner/releases/download/v2.322.0/actions-runner-win-x64-2.322.0.zip"
    
    Invoke-WebRequest -Uri $downloadUrl -OutFile $runnerZip -UseBasicParsing
    Write-Host "[EXTRACT] Extracting runner package..." -ForegroundColor Yellow
    Expand-Archive -Path $runnerZip -DestinationPath $RunnerDir -Force
    Remove-Item -Path $runnerZip -Force
}

# 3. Configure runner if token provided and not yet configured
if (-not (Test-Path "$RunnerDir\.runner")) {
    if (-not $RunnerToken) {
        Write-Host "`n[ACTION REQUIRED] Runner is not yet configured with GitHub." -ForegroundColor Yellow
        Write-Host "1. Go to: $RepoUrl/settings/actions/runners/new" -ForegroundColor Cyan
        Write-Host "2. Copy the registration token from GitHub" -ForegroundColor Cyan
        Write-Host "3. Run this script again with: .\scripts\setup_runner.ps1 -RunnerToken <YOUR_TOKEN>" -ForegroundColor Green
        exit 0
    }

    Write-Host "`n[CONFIG] Registering runner with GitHub..." -ForegroundColor Yellow
    $hostname = $env:COMPUTERNAME
    & .\config.cmd --url $RepoUrl --token $RunnerToken --name "$hostname-live-runner" --labels "self-hosted,Windows,X64,live-server" --unattended --replace
    Write-Host "[OK] Runner registered successfully." -ForegroundColor Green
} else {
    Write-Host "`n[OK] Runner is already configured." -ForegroundColor Green
}

# 4. Prompt to start or install as Windows Service
Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host "To run the runner continuously in background as a Windows Service:" -ForegroundColor White
Write-Host "  cd $RunnerDir" -ForegroundColor Gray
Write-Host "  .\svc.cmd install" -ForegroundColor Gray
Write-Host "  .\svc.cmd start" -ForegroundColor Gray
Write-Host "`nOr to start the runner immediately in foreground:" -ForegroundColor White
Write-Host "  cd $RunnerDir; .\run.cmd" -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan
