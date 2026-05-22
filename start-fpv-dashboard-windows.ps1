$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$OutLog = Join-Path $Root "data\server.out.log"
$ErrLog = Join-Path $Root "data\server.err.log"
$TunnelScript = Join-Path $Root "start-cloudflare-garden-tunnel.ps1"
$Cloudflared = "C:\Users\Administrator\Downloads\cloudflared-windows-amd64.exe"
$CarSsids = @(
    "WL_FPV_CAR_64886271",
    "WL_FPV_CAR_99613492"
)

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

Write-Host "Connecting TP-Link WiFi adapter to a saved WLtoys car profile if available..."
foreach ($ssid in $CarSsids) {
    Write-Host "Trying WiFi SSID: $ssid"
    netsh wlan connect name="$ssid" ssid="$ssid" interface="WiFi" | Out-Null
    Start-Sleep -Seconds 3
    $wifiConfig = Get-NetIPConfiguration -InterfaceAlias WiFi -ErrorAction SilentlyContinue
    $wifiIp = $wifiConfig.IPv4Address.IPAddress
    if ($wifiIp -like "172.16.11.*") {
        Write-Host "Connected to car WiFi: $ssid ($wifiIp)"
        break
    }
}

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
Write-Host "Starting Cloudflare tunnel for https://garden.zen-rc.net ..."
$tunnelRunning = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq "cloudflared-windows-amd64.exe" -and
        $_.CommandLine -like "*garden-zen-rc*"
    }

if ($tunnelRunning) {
    Write-Host "Cloudflare tunnel is already running."
} elseif ((Test-Path $Cloudflared) -and (Test-Path $TunnelScript)) {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$TunnelScript`"" `
        -WorkingDirectory $Root `
        -WindowStyle Minimized
    Start-Sleep -Seconds 6
    try {
        & $Cloudflared tunnel info garden-zen-rc
    } catch {
        Write-Host "Tunnel start was requested, but status check failed. Check the tunnel window/log output."
    }
} else {
    Write-Host "Tunnel not started. Missing cloudflared exe or tunnel script."
}

Write-Host ""
Write-Host "Tip: if car connect/video is slow, run .\check-latency-state.ps1 and confirm the car route uses WiFi."
