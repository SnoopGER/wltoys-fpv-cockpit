$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Mappings = @(
    @{
        Interface = "WiFi"
        Ssid = "WL_FPV_CAR_99613492"
        Profile = Join-Path $Root "wl-fpv-car-wifi-profile.xml"
    },
    @{
        Interface = "WiFi 3"
        Ssid = "WL_FPV_CAR_64886271"
        Profile = Join-Path $Root "wl-fpv-car-64886271-wifi-profile.xml"
    }
)

Write-Host "WLtoys fixed WiFi mapping"
Write-Host "  WiFi   -> WL_FPV_CAR_99613492"
Write-Host "  WiFi 3 -> WL_FPV_CAR_64886271"
Write-Host ""
Write-Host "Wake both cars now. Their WiFi APs sleep after about 60 seconds."
Write-Host ""

foreach ($mapping in $Mappings) {
    $interface = $mapping.Interface
    $ssid = $mapping.Ssid
    $profile = $mapping.Profile

    if (Test-Path $profile) {
        Write-Host "Ensuring profile $ssid exists on $interface..."
        netsh wlan add profile filename="$profile" interface="$interface" user=all | Out-Null
    }

    Write-Host "Connecting $interface to $ssid..."
    netsh wlan connect name="$ssid" ssid="$ssid" interface="$interface"
    Start-Sleep -Seconds 4
}

Write-Host ""
Write-Host "Current WLAN state:"
netsh wlan show interfaces

Write-Host ""
Write-Host "Current car-side IP configuration:"
foreach ($mapping in $Mappings) {
    $interface = $mapping.Interface
    $config = Get-NetIPConfiguration -InterfaceAlias $interface -ErrorAction SilentlyContinue
    $ip = $config.IPv4Address.IPAddress
    $gateway = $config.IPv4DefaultGateway.NextHop

    if ($ip) {
        Write-Host "  $interface -> IP $ip Gateway $gateway"
    } else {
        Write-Host "  $interface -> no IPv4 address yet"
    }
}

Write-Host ""
Write-Host "Note: both cars usually use 172.16.11.1, so the current dashboard can still only target one car endpoint cleanly."
Write-Host "For parallel cars we need per-car dashboard/network handling, not only two Windows WiFi connections."
