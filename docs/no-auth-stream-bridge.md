# No-auth FPV Stream Bridge

This helper is for VLC/OBS use without the race dashboard or login.

It receives the WLtoys UDP video stream, decodes it, and exposes a plain MJPEG
HTTP endpoint:

```text
http://localhost:8080/stream.mjpg
```

## Copy to Another Windows PC

Option A: GitHub

```powershell
git clone https://github.com/SnoopGER/wltoys-fpv-cockpit.git
cd wltoys-fpv-cockpit
git checkout codex/windows-garden-dashboard
.\setup-stream-bridge-windows.ps1
```

Option B: USB copy

Copy the whole project folder to the other PC, then run from that copied folder:

```powershell
.\setup-stream-bridge-windows.ps1
```

Python 3.11+ must be installed on the target PC.

## Start on Windows

Connect the PC to the car WiFi first, then run:

```powershell
.\start-fpv-stream-bridge.ps1
```

Or run directly:

```powershell
.\.venv\Scripts\python.exe -u fpv_stream_bridge.py --port 8080
```

Status:

```text
http://localhost:8080/status
```

## VLC

Open `Media -> Open Network Stream` and paste:

```text
http://localhost:8080/stream.mjpg
```

## OBS

Add a `Browser` source and use:

```text
http://localhost:8080/stream.mjpg
```

Use width `640` and height `360` for the original feed, or scale it in OBS.

## Useful Options

Force a specific local WiFi adapter IP:

```powershell
.\.venv\Scripts\python.exe -u fpv_stream_bridge.py --bind-ip 172.16.11.4 --port 8080
```

Use a different bridge port:

```powershell
$env:STREAM_BRIDGE_PORT=8081
.\start-fpv-stream-bridge.ps1
```

Force H.265/HEVC decoding for smaller cars:

```powershell
$env:VIDEO_CODEC="hevc"
.\start-fpv-stream-bridge.ps1
```

The raw car video is still UDP `1234`; VLC usually cannot consume it directly
because WLtoys wraps and fragments the video frames before the encoded payload.
