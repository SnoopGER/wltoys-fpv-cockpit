param(
    [string]$BindIp = "172.16.11.3",
    [int]$Seconds = 180
)

$ErrorActionPreference = "Continue"

$Wake = [byte[]](0xa8,0x8a,0x21,0x00,0x06,0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x00,0x00)
$Trigger = [byte[]](0xa8,0x8a,0x20,0x00,0x08,0x00,0x00,0x00,0x01,0x00,0x02,0x00,0x00,0x00,0xd2,0x04)
$Heartbeat = [byte[]](0xca,0x47,0xd5,0x00,0x00,0x00,0x00,0x00,0x66,0x80,0x80,0x80,0x00,0x00,0x80,0x99)
$HandshakeTarget = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse("172.16.11.1"), 23459)
$HeartbeatTarget = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse("172.16.11.1"), 23458)

$client = [System.Net.Sockets.UdpClient]::new([System.Net.IPEndPoint]::new([System.Net.IPAddress]::Parse($BindIp), 0))
$until = (Get-Date).AddSeconds($Seconds)
$i = 0
Write-Host "Keeping car awake from $BindIp for $Seconds seconds..."
while ((Get-Date) -lt $until) {
    $i++
    if (($i % 10) -eq 1) {
        [void]$client.Send($Wake, $Wake.Length, $HandshakeTarget)
        Start-Sleep -Milliseconds 80
        [void]$client.Send($Trigger, $Trigger.Length, $HandshakeTarget)
    }
    [void]$client.Send($Heartbeat, $Heartbeat.Length, $HeartbeatTarget)
    Start-Sleep -Milliseconds 500
}
$client.Close()
Write-Host "Keepalive finished for $BindIp"
