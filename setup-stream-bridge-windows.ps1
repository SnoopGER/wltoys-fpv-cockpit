$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "Setting up FPV Stream Bridge in:"
Write-Host "  $Root"
Write-Host ""

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Host "Python launcher 'py' was not found."
    Write-Host "Install Python 3.11+ from https://www.python.org/downloads/windows/ and enable 'Add python.exe to PATH'."
    exit 1
}

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment..."
    py -m venv .venv
}

Write-Host "Installing dependencies..."
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt

Write-Host ""
Write-Host "Setup complete."
Write-Host "Start the bridge with:"
Write-Host "  .\start-fpv-stream-bridge.ps1"
Write-Host ""
Write-Host "VLC/OBS URL:"
Write-Host "  http://localhost:8080/stream.mjpg"
