$ErrorActionPreference = "Stop"

$Cloudflared = "C:\Users\Administrator\Downloads\cloudflared-windows-amd64.exe"
$Config = "C:\Users\Administrator\Documents\Codex\2026-05-18\snoopger-wltoys-fpv-cockpit-https-github\cloudflared-garden.yml"

Write-Host "Starting Cloudflare Tunnel for garden.zen-rc.net"
Write-Host "Config: $Config"
Write-Host "Target: http://localhost:5555"
Write-Host ""

& $Cloudflared tunnel --config $Config run garden-zen-rc
