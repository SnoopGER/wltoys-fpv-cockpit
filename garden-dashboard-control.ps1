$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$DashboardScript = Join-Path $Root "start-fpv-dashboard-windows.ps1"
$TunnelScript = Join-Path $Root "start-cloudflare-garden-tunnel.ps1"
$BindIpsScript = Join-Path $Root "update-car-bind-ips.ps1"
$HandshakeScript = Join-Path $Root "send-car-handshake.ps1"
$LatencyScript = Join-Path $Root "check-latency-state.ps1"
$Cloudflared = "C:\Users\Administrator\Downloads\cloudflared-windows-amd64.exe"
$LocalBase = "http://localhost:5555"
$LanBase = "http://192.168.22.75:5555"
$PublicBase = "https://garden.zen-rc.net"

function Wait-Key {
    Write-Host ""
    Read-Host "Press Enter to continue" | Out-Null
}

function Get-DashboardProcesses {
    $escapedRoot = [Regex]::Escape($Root)
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -like "python*" -and
            $_.CommandLine -match $escapedRoot -and
            $_.CommandLine -match "webapp.py"
        }
}

function Get-TunnelProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "cloudflared-windows-amd64.exe" -and
            $_.CommandLine -like "*garden-zen-rc*"
        }
}

function Show-WifiStatus {
    Write-Host ""
    Write-Host "WiFi adapters:"
    Get-NetAdapter |
        Where-Object { $_.Name -like "WiFi*" -or $_.InterfaceDescription -like "*Wireless*" } |
        Format-Table -Auto Name,Status,MacAddress,LinkSpeed

    Write-Host ""
    Write-Host "Car IPs:"
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -like "WiFi*" } |
        Format-Table -Auto InterfaceAlias,IPAddress,PrefixLength,PrefixOrigin,AddressState
}

function Show-Status {
    Clear-Host
    Write-Host "Garden FPV Dashboard Control"
    Write-Host "============================"

    $dash = @(Get-DashboardProcesses)
    $listener = @(Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue)
    $tunnel = @(Get-TunnelProcesses)

    Write-Host ""
    Write-Host ("Dashboard:  " + ($(if ($listener.Count -gt 0) { "ONLINE on :5555" } else { "OFFLINE" })))
    if ($dash.Count -gt 0) {
        Write-Host ("Processes:  " + (($dash | ForEach-Object { $_.ProcessId }) -join ", "))
    }

    Write-Host ("Tunnel:     " + ($(if ($tunnel.Count -gt 0) { "ONLINE" } else { "OFFLINE" })))
    if ($tunnel.Count -gt 0) {
        Write-Host ("Tunnel PID: " + (($tunnel | ForEach-Object { $_.ProcessId }) -join ", "))
    }

    Write-Host ""
    Write-Host "URLs:"
    Write-Host "  Split: $LocalBase/admin/cars"
    Write-Host "  Car 1: $LocalBase/car/car1"
    Write-Host "  Car 2: $LocalBase/car/car2"
    Write-Host "  LAN:   $LanBase"
    Write-Host "  Web:   $PublicBase"

    Show-WifiStatus
}

function Start-Dashboard {
    Write-Host "Starting dashboard..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $DashboardScript
}

function Stop-Dashboard {
    Write-Host "Stopping dashboard..."
    $listeners = Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $listeners) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Get-DashboardProcesses | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Write-Host "Dashboard stopped."
}

function Restart-Dashboard {
    Stop-Dashboard
    Start-Dashboard
}

function Start-Tunnel {
    if (Get-TunnelProcesses) {
        Write-Host "Cloudflare tunnel is already online."
        return
    }
    if (-not (Test-Path $Cloudflared)) {
        Write-Host "Missing cloudflared exe: $Cloudflared"
        return
    }
    if (-not (Test-Path $TunnelScript)) {
        Write-Host "Missing tunnel script: $TunnelScript"
        return
    }
    Write-Host "Starting Cloudflare tunnel..."
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$TunnelScript`"" `
        -WorkingDirectory $Root `
        -WindowStyle Minimized
    Start-Sleep -Seconds 4
    Write-Host "Tunnel start requested."
}

function Stop-Tunnel {
    Write-Host "Stopping Cloudflare tunnel..."
    Get-TunnelProcesses | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Write-Host "Tunnel stopped."
}

function Open-DashboardLinks {
    Start-Process "$LocalBase/admin/cars"
    Start-Process "$LocalBase/car/car1"
    Start-Process "$LocalBase/car/car2"
}

function Update-BindIps {
    if (Test-Path $BindIpsScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $BindIpsScript
    } else {
        Write-Host "Missing script: $BindIpsScript"
    }
}

function Send-Handshakes {
    if (Test-Path $HandshakeScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $HandshakeScript
    } else {
        Write-Host "Missing script: $HandshakeScript"
    }
}

function Run-LatencyCheck {
    if (Test-Path $LatencyScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $LatencyScript
    } else {
        Write-Host "Missing script: $LatencyScript"
    }
}

while ($true) {
    Show-Status
    Write-Host ""
    Write-Host "Actions"
    Write-Host "  1  Start dashboard"
    Write-Host "  2  Stop dashboard"
    Write-Host "  3  Restart dashboard"
    Write-Host "  4  Open dashboard pages"
    Write-Host "  5  Start Cloudflare tunnel"
    Write-Host "  6  Stop Cloudflare tunnel"
    Write-Host "  7  Update car bind IPs"
    Write-Host "  8  Send car handshakes"
    Write-Host "  9  Run latency/network check"
    Write-Host "  R  Refresh"
    Write-Host "  Q  Quit"
    Write-Host ""
    $choice = (Read-Host "Select").Trim().ToUpperInvariant()

    switch ($choice) {
        "1" { Start-Dashboard; Wait-Key }
        "2" { Stop-Dashboard; Wait-Key }
        "3" { Restart-Dashboard; Wait-Key }
        "4" { Open-DashboardLinks; Wait-Key }
        "5" { Start-Tunnel; Wait-Key }
        "6" { Stop-Tunnel; Wait-Key }
        "7" { Update-BindIps; Wait-Key }
        "8" { Send-Handshakes; Wait-Key }
        "9" { Run-LatencyCheck; Wait-Key }
        "R" { }
        "Q" { break }
        default { Write-Host "Unknown choice."; Start-Sleep -Seconds 1 }
    }
}
