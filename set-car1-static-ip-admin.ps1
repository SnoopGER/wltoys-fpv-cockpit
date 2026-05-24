$ErrorActionPreference = "Continue"

$Interface = "WiFi"
$Car1BindIp = "172.16.11.3"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env.local"

Write-Host "Preparing $Interface for car1 with static IP $Car1BindIp/24"
Write-Host "This script must run as Administrator."
Write-Host ""

try {
    Set-NetIPInterface -InterfaceAlias $Interface -AddressFamily IPv4 -Dhcp Disabled -ErrorAction Stop
    Write-Host "DHCP disabled on $Interface"
} catch {
    Write-Host "Failed to disable DHCP on ${Interface}: $($_.Exception.Message)"
}

Get-NetIPAddress -InterfaceAlias $Interface -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -ne $Car1BindIp } |
    ForEach-Object {
        try {
            Remove-NetIPAddress -InterfaceAlias $Interface -IPAddress $_.IPAddress -Confirm:$false -ErrorAction SilentlyContinue
        } catch {}
    }

if (-not (Get-NetIPAddress -InterfaceAlias $Interface -IPAddress $Car1BindIp -ErrorAction SilentlyContinue)) {
    try {
        New-NetIPAddress -InterfaceAlias $Interface -IPAddress $Car1BindIp -PrefixLength 24 -ErrorAction Stop | Out-Null
        Write-Host "$Interface static IP set to $Car1BindIp/24"
    } catch {
        Write-Host "Failed to set static IP on ${Interface}: $($_.Exception.Message)"
    }
} else {
    Write-Host "$Interface already has $Car1BindIp"
}

if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File -Path $EnvFile -Force | Out-Null
}

$content = Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue
if ($content -match "^FPV_CAR1_BIND_IP=") {
    $content = $content -replace "^FPV_CAR1_BIND_IP=.*$", "FPV_CAR1_BIND_IP=$Car1BindIp"
} else {
    $content += "FPV_CAR1_BIND_IP=$Car1BindIp"
}
Set-Content -LiteralPath $EnvFile -Value $content -Encoding ASCII

Write-Host ""
Write-Host "Current IPv4 state:"
Get-NetIPAddress -InterfaceAlias $Interface -AddressFamily IPv4 | Format-Table -Auto InterfaceAlias,IPAddress,PrefixLength,PrefixOrigin,AddressState
Write-Host ""
Write-Host "Updated $EnvFile"
