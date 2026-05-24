$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root ".env.local"

$Mappings = @(
    @{ Key = "FPV_CAR1_BIND_IP"; Interface = "WiFi"; Ssid = "WL_FPV_CAR_99613492"; FallbackIp = "172.16.11.3" },
    @{ Key = "FPV_CAR2_BIND_IP"; Interface = "WiFi 3"; Ssid = "WL_FPV_CAR_64886271"; FallbackIp = "172.16.11.2" }
)

if (-not (Test-Path $EnvFile)) {
    New-Item -ItemType File -Path $EnvFile -Force | Out-Null
}

$content = Get-Content -LiteralPath $EnvFile -ErrorAction SilentlyContinue

foreach ($mapping in $Mappings) {
    $config = Get-NetIPConfiguration -InterfaceAlias $mapping.Interface -ErrorAction SilentlyContinue
    $ip = $config.IPv4Address.IPAddress | Where-Object { $_ -like "172.16.11.*" } | Select-Object -First 1
    if (-not $ip) {
        $ip = $mapping.FallbackIp
        Write-Host "$($mapping.Interface) has no 172.16.11.x DHCP address; using configured fallback $ip for $($mapping.Key)."
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
