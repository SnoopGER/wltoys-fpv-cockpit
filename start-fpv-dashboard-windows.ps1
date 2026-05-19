$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$OutLog = Join-Path $Root "data\server.out.log"
$ErrLog = Join-Path $Root "data\server.err.log"

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null

Write-Host "WLtoys FPV Cockpit - Windows direct runner"
Write-Host "Project: $Root"
Write-Host ""

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment..."
    py -m venv .venv
    & $Python -m pip install -r requirements.txt
}

Write-Host "Stopping any existing dashboard listener on TCP 5555..."
$listeners = Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($listener in $listeners) {
    Stop-Process -Id $listener -Force -ErrorAction SilentlyContinue
}

Write-Host "Connecting TP-Link WiFi adapter to WL_FPV_CAR_99613492 if available..."
netsh wlan connect name="WL_FPV_CAR_99613492" ssid="WL_FPV_CAR_99613492" interface="WiFi" | Out-Null

Write-Host "Starting dashboard..."
Remove-Item $OutLog, $ErrLog -ErrorAction SilentlyContinue
Start-Process -FilePath $Python `
    -ArgumentList "-u webapp.py" `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden

Start-Sleep -Seconds 4

Write-Host ""
Write-Host "Dashboard URLs:"
Write-Host "  http://localhost:5555"
Write-Host "  http://192.168.22.75:5555"
Write-Host ""
Write-Host "Guest code:"
Write-Host "  ZENGARDEN"
Write-Host ""
Write-Host "Logs:"
Write-Host "  $OutLog"
Write-Host "  $ErrLog"
Write-Host ""

try {
    Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5555/api/status |
        Select-Object -ExpandProperty Content
} catch {
    Write-Host "Dashboard did not answer yet. Check logs above."
}

Write-Host ""
Write-Host "Tip: if car connect/video is slow, run .\check-latency-state.ps1 and confirm the car route uses WiFi."
