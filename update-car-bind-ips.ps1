$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env.local"

$Mappings = @(
    @{ Key = "FPV_CAR1_BIND_IP"; Interface = "WiFi"; Ssid = "WL_FPV_CAR_99613492"; FallbackIp = "172.16.11.3" },
    @{ Key = "FPV_CAR2_BIND_IP"; Interface = "WiFi 3"; Ssid = "WL_FPV_CAR_64886271"; FallbackIp = "172.16.11.2" },
    @{ Key = "FPV_CAR3_BIND_IP"; Interface = "WiFi 4"; Ssid = "WL FPV CAR 10335160"; FallbackIp = "172.16.11.4" }
)

if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File -Path $EnvFile -Force | Out-Null
}

$content = Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue

foreach ($mapping in $Mappings) {
    $config = Get-NetIPConfiguration -InterfaceAlias $mapping.Interface -ErrorAction SilentlyContinue
    $expectedIp = $mapping.FallbackIp
    $assignedIps = @($config.IPv4Address.IPAddress | Where-Object { $_ -like "172.16.11.*" })
    $ip = $assignedIps | Where-Object { $_ -eq $expectedIp } | Select-Object -First 1
    if (-not $ip) {
        $wrongIp = $assignedIps | Select-Object -First 1
        $ip = $expectedIp
        if ($wrongIp) {
            Write-Host "$($mapping.Interface) has $wrongIp, but $($mapping.Key) expects $expectedIp. Run the static IP admin helper."
        } else {
            Write-Host "$($mapping.Interface) has no 172.16.11.x address; using configured fallback $ip for $($mapping.Key)."
        }
    }

    $line = "$($mapping.Key)=$ip"
    if ($content -match "^$($mapping.Key)=") {
        $content = $content -replace "^$($mapping.Key)=.*$", $line
    } else {
        $content += $line
    }
    Write-Host "$($mapping.Key)=$ip"
}

Set-Content -LiteralPath $EnvFile -Value $content -Encoding ASCII
Write-Host "Updated $EnvFile"
