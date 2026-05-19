$ErrorActionPreference = "SilentlyContinue"

Write-Host "Dashboard"
try {
    Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5555/api/status |
        Select-Object -ExpandProperty Content
} catch {
    Write-Host "  Dashboard not reachable on localhost:5555"
}

Write-Host ""
Write-Host "Car Route"
Find-NetRoute -RemoteIPAddress 172.16.11.1 |
    Format-List InterfaceAlias,IPAddress,NextHop,RouteMetric,InterfaceMetric

Write-Host ""
Write-Host "WiFi IP"
Get-NetIPConfiguration -InterfaceAlias WiFi |
    Format-List InterfaceAlias,IPv4Address,IPv4DefaultGateway

Write-Host ""
Write-Host "Server Process"
$listener = Get-NetTCPConnection -LocalPort 5555 -State Listen | Select-Object -First 1
if ($listener) {
    Get-Process -Id $listener.OwningProcess |
        Format-List Id,ProcessName,CPU,WorkingSet64,StartTime,Path
} else {
    Write-Host "  No process is listening on TCP 5555"
}
