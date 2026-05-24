$ErrorActionPreference = "Continue"

$Wake = [byte[]](0xa8,0x8a,0x21,0x00,0x06,0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x00,0x00)
$Trigger = [byte[]](0xa8,0x8a,0x20,0x00,0x08,0x00,0x00,0x00,0x01,0x00,0x02,0x00,0x00,0x00,0xd2,0x04)
$Target = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse("172.16.11.1"), 23459)

$Mappings = @(
    @{ Name = "car1"; Interface = "WiFi"; Ssid = "WL_FPV_CAR_99613492"; FallbackIp = "172.16.11.3" },
    @{ Name = "car2"; Interface = "WiFi 3"; Ssid = "WL_FPV_CAR_64886271"; FallbackIp = "172.16.11.2" }
)

foreach ($mapping in $Mappings) {
    $config = Get-NetIPConfiguration -InterfaceAlias $mapping.Interface -ErrorAction SilentlyContinue
    $ip = $config.IPv4Address.IPAddress | Where-Object { $_ -like "172.16.11.*" } | Select-Object -First 1
    if (-not $ip) {
        $ip = $mapping.FallbackIp
        $assigned = Get-NetIPAddress -InterfaceAlias $mapping.Interface -IPAddress $ip -ErrorAction SilentlyContinue
        if (-not $assigned) {
            Write-Host "$($mapping.Name): $($mapping.Interface) has no usable $ip address; skipping until static IP is applied."
            continue
        }
        Write-Host "$($mapping.Name): $($mapping.Interface) using static fallback source $ip."
    }

    Write-Host "$($mapping.Name): sending handshake from $ip to 172.16.11.1:23459"
    $client = [System.Net.Sockets.UdpClient]::new([System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse($ip), 0))
    for ($i = 1; $i -le 3; $i++) {
        [void]$client.Send($Wake, $Wake.Length, $Target)
        Start-Sleep -Milliseconds 200
        [void]$client.Send($Trigger, $Trigger.Length, $Target)
        Start-Sleep -Milliseconds 300
    }
    $client.Close()
}
