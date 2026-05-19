# Windows Garden Track Deployment

This setup runs the FPV dashboard directly on Windows for the car UDP path and uses Cloudflare Tunnel only for public web access.

## Why direct Python instead of Docker Desktop?

Docker Desktop works for the web dashboard, but the WLtoys car uses fixed UDP traffic for control and video. On this Windows track PC, Docker Desktop NAT did not reliably pass the car video packets back into the container. The direct Windows Python runtime is the working path for:

- dashboard on `http://localhost:5555`
- LAN dashboard on `http://192.168.22.75:5555`
- car WiFi adapter connected to `WL_FPV_CAR_99613492`
- car IP `172.16.11.1`
- video UDP listen port `1234`
- control/heartbeat UDP port `23458`
- handshake UDP port `23459`

## Start the local dashboard

Double-click the desktop launcher:

```text
C:\Users\Administrator\Desktop\Start FPV Dashboard.bat
```

Or run from the project folder:

```powershell
.\start-fpv-dashboard-windows.ps1
```

The launcher:

- stops any old listener on TCP `5555`
- tries to connect the TP-Link WiFi adapter to `WL_FPV_CAR_99613492`
- starts `webapp.py` through the local virtualenv
- writes logs to `data\server.out.log` and `data\server.err.log`

## Guest drive code

The persistent local test code is:

```text
ZENGARDEN
```

## Latency checks

If controls or video feel delayed, run:

```powershell
.\check-latency-state.ps1
```

The most important check is that the route to `172.16.11.1` uses `WiFi`, not `Ethernet`.

## Local latency tuning

The Windows deployment uses these environment values in `.env.local` / `local.env`:

```env
JPEG_QUALITY=55
VIDEO_DECODE_QUEUE=8
```

The dashboard also uses Socket.IO for live motor commands when available, with HTTP as a fallback.

## Cloudflare Tunnel

Public hostname:

```text
https://garden.zen-rc.net
```

Local target:

```text
http://localhost:5555
```

The actual local tunnel config is intentionally not committed because it points at a local Cloudflare credential file. Use:

```text
cloudflared-garden.example.yml
```

as the template, then create the real local file:

```text
cloudflared-garden.yml
```

Start the tunnel manually:

```powershell
.\start-cloudflare-garden-tunnel.ps1
```

The Garden track tunnel created during setup:

```text
garden-zen-rc
7c7bc9b2-5b6c-4e9e-b39d-24e43cf9d2ad
```

Keep the matching `.json` credential file in `C:\Users\Administrator\.cloudflared\` private.
