# Windows Garden Track Deployment

This setup runs the FPV dashboard directly on Windows for the car UDP path and uses Cloudflare Tunnel only for public web access.

## Why direct Python instead of Docker Desktop?

Docker Desktop works for the web dashboard, but the WLtoys car uses fixed UDP traffic for control and video. On this Windows track PC, Docker Desktop NAT did not reliably pass the car video packets back into the container. The direct Windows Python runtime is the working path for:

- dashboard on `http://localhost:5555`
- LAN dashboard on `http://192.168.22.75:5555`
- car WiFi adapter connected to `WL_FPV_CAR_64886271` or `WL_FPV_CAR_99613492`
- car IP `172.16.11.1`
- video UDP listen port `1234`
- control/heartbeat UDP port `23458`
- handshake UDP port `23459`

## Start the local dashboard

Double-click the desktop launcher:

```text
C:\Users\Administrator\Desktop\Garden FPV Control.bat
```

The control app can:

- start, stop, and restart the local dashboard
- start and stop the Cloudflare Tunnel
- show dashboard/tunnel/WiFi status
- open the split view and both car pages
- update car bind IPs after manually connecting the car WiFi networks
- send handshake bursts for debugging
- run the latency/network check

Or run the dashboard-only starter from the project folder:

```powershell
.\start-fpv-dashboard-windows.ps1
```

The launcher:

- stops any old listener on TCP `5555`
- stops stale `webapp.py` Python processes from this project
- updates `.env.local` with the current per-car bind IPs
- starts `webapp.py` through the local virtualenv
- writes logs to `data\server.out.log` and `data\server.err.log`

Car WiFi connection is manual: connect car 1 on `WiFi`, connect car 2 on `WiFi 3`,
then press CONNECT in each dashboard page.

## Two car setup

The Garden Windows build supports two saved WLtoys car WiFi profiles:

- `car1`: `WL_FPV_CAR_99613492` on Windows interface `WiFi`, local bind IP `172.16.11.3`
- `car2`: `WL_FPV_CAR_64886271` on Windows interface `WiFi 3`, local bind IP `172.16.11.2`
- `car3`: `WL FPV CAR 10335160` on Windows interface `WiFi 4`, local bind IP `172.16.11.4`

The `car3` WiFi password is the numeric suffix from the SSID. Keep the password in the
local Windows WiFi profile; do not commit personal WiFi profile secrets to the public repo.
`car3` appears to send the same WLtoys UDP framing, but may use a different video codec
than the larger cars. The Windows decoder auto-detects H.264 and H.265/HEVC; check
`/api/status?car=car3` and the `decoder_codec` field while testing.

Run this after both cars are awake, or choose the matching action in `Garden FPV Control.bat`:

```powershell
.\connect-two-cars-wifi.ps1
.\update-car-bind-ips.ps1
```

If `car1` connects but Windows shows a `169.254.x.x` address, run this once as Administrator:

```powershell
.\set-car1-static-ip-admin.ps1
```

For the three-car setup, the Garden control app can open all static IP helpers from menu item `10`.
The scripts are:

```powershell
.\set-car1-static-ip-admin.ps1  # WiFi   -> 172.16.11.3
.\set-car2-static-ip-admin.ps1  # WiFi 3 -> 172.16.11.2
.\set-car3-static-ip-admin.ps1  # WiFi 4 -> 172.16.11.4
```

Dashboard URLs:

- `http://localhost:5555/car/car1`
- `http://localhost:5555/car/car2`
- `http://localhost:5555/car/car3`
- `http://localhost:5555/admin/cars`

For packet captures or debugging, run this while both car APs are awake:

```powershell
.\send-car-handshake.ps1
```

It sends the WLtoys wake/trigger handshake from each connected adapter, which helps keep the cars awake while debugging.

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
