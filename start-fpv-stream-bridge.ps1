$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$OutLog = Join-Path $Root "data\stream-bridge.out.log"
$ErrLog = Join-Path $Root "data\stream-bridge.err.log"

$Port = $env:STREAM_BRIDGE_PORT
if (-not $Port) { $Port = "8080" }

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment..."
    py -m venv .venv
    & $Python -m pip install -r requirements.txt
}

Write-Host "Stopping existing stream bridge on TCP $Port..."
$listeners = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $listeners) {
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}

Write-Host "Starting no-auth FPV stream bridge..."
Remove-Item $OutLog, $ErrLog -ErrorAction SilentlyContinue
Start-Process -FilePath $Python `
    -ArgumentList "-u fpv_stream_bridge.py --port $Port" `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden

Start-Sleep -Seconds 4

Write-Host ""
Write-Host "Stream Bridge URLs:"
Write-Host "  http://localhost:$Port/stream.mjpg"
Write-Host "  http://localhost:$Port/status"
Write-Host ""
Write-Host "Logs:"
Write-Host "  $OutLog"
Write-Host "  $ErrLog"
Write-Host ""

try {
    Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/status" |
        Select-Object -ExpandProperty Content
} catch {
    Write-Host "Stream bridge did not answer yet. Check logs above."
}
