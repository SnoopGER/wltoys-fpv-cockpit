$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -m venv .venv
    & ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
}

Write-Host ""
Write-Host "Starting WLtoys FPV Cockpit"
Write-Host "Local: http://localhost:5555"
Write-Host "LAN:   http://192.168.22.75:5555"
Write-Host "Guest code: ZENGARDEN"
Write-Host ""

& ".\.venv\Scripts\python.exe" webapp.py
