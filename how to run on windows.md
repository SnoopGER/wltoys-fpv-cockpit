# How to Run FPV Dashboard V2 on Windows

This guide is for the Windows garden-track deployment.

Use the native Windows Python runner for the live RC car path. Docker Desktop on Windows can serve the web dashboard, but its NAT/WSL networking did not reliably pass the WLtoys UDP video stream back from the car.

For Linux track servers, keep using the normal `Dockerfile` and `docker-compose.yml` with native Linux host networking.

## Runtime Model

```text
Browser / LAN / Cloudflare Tunnel
        |
Windows Python dashboard
        |
TP-Link USB WiFi adapter
        |
WL_FPV_CAR_64886271
        |
172.16.11.1
```

The dashboard listens on:

```text
http://localhost:5555
http://192.168.22.75:5555
```

The public tunnel target is:

```text
https://garden.zen-rc.net -> http://localhost:5555
```

## Start

Double-click:

```text
C:\Users\Administrator\Desktop\Start FPV Dashboard.bat
```

Or run from this project folder:

```powershell
.\start-fpv-dashboard-windows.ps1
```

The script:

- creates `.venv` if missing
- installs `requirements.txt` if the venv is created
- stops any old process listening on TCP `5555`
- tries to connect the TP-Link WiFi adapter to a saved WLtoys car SSID
- starts `webapp.py` through `.venv`
- writes logs to `data\server.out.log` and `data\server.err.log`

## Stop

Find and stop the listener:

```powershell
$ids = Get-NetTCPConnection -LocalPort 5555 -State Listen |
  Select-Object -ExpandProperty OwningProcess -Unique
$ids | ForEach-Object { Stop-Process -Id $_ -Force }
```

## Car WiFi

Saved SSID:

```text
WL_FPV_CAR_64886271
WL_FPV_CAR_99613492
```

Car IP:

```text
172.16.11.1
```

Expected Windows adapter IP when connected:

```text
172.16.11.2
```

The car AP sleeps quickly. Wake/connect the car WiFi before pressing Connect in the dashboard.

## Guest Code

Persistent local test code:

```text
ZENGARDEN
```

## Latency Checks

Run:

```powershell
.\check-latency-state.ps1
```

The important route must be:

```text
172.16.11.1 -> WiFi
```

If it routes through `Ethernet`, reconnect the car WiFi:

```powershell
netsh wlan connect name="WL_FPV_CAR_64886271" ssid="WL_FPV_CAR_64886271" interface="WiFi"
```

## Cloudflare Tunnel

Tunnel name:

```text
garden-zen-rc
```

Tunnel ID:

```text
7c7bc9b2-5b6c-4e9e-b39d-24e43cf9d2ad
```

Start manually:

```powershell
.\start-cloudflare-garden-tunnel.ps1
```

The local tunnel config is not committed because it references a private credential file. Use `cloudflared-garden.example.yml` as the template.
