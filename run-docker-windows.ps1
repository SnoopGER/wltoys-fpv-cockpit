$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "Starting Docker Desktop if needed..."
Start-Process -FilePath "C:\Program Files\Docker\Docker\Docker Desktop.exe" -WindowStyle Hidden -ErrorAction SilentlyContinue

Write-Host "Building and starting FPV dashboard container..."
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" compose -f docker-compose.windows.yml up -d --build

Write-Host ""
Write-Host "Dashboard:"
Write-Host "  http://localhost:5555"
Write-Host "  http://192.168.22.75:5555"
Write-Host ""
Write-Host "Logs:"
Write-Host "  docker compose -f docker-compose.windows.yml logs -f fpv-dashboard-v2"
