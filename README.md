# 🏎️ WLtoys 6405 FPV Cockpit

**Control your WLtoys 6405 FPV RC car from a web browser — live video stream + keyboard/gamepad motor control.**

Reverse-engineered from the Android app (`com.lg.wltechfpvcar`). No encryption, no cloud, pure local UDP.

![Python](https://img.shields.io/badge/python-3.10+-blue) ![License](https://img.shields.io/badge/license-MIT-green) ![Status](https://img.shields.io/badge/status-working-brightgreen)

---

## Features

- **Live FPV video** — H.264 stream decoded to MJPEG in-browser (~25fps)
- **Discord OAuth race lobby** — allowlisted Discord users can join the driver queue; admins can pause, emergency-stop, advance drivers, and tune limits
- **Admin moderation** — live connected Discord account list with kick, ban, and unban controls; bans persist locally outside git
- **Remote-safe controls** — stale command rejection, active-driver validation, max speed cap, and admin-only car connect/disconnect
- **Motor control** — WASD keys + D-pad buttons at 20Hz (matches original app rate)
- **Dual-axis combos** — steer while accelerating (W+A, W+D, S+A, S+D)
- **Speed & steering sliders** — adjust throttle power (5–100%) and steering angle (5–100%)
- **Debug panel** — raw hex sender, live stats, protocol documentation, filterable logs
- **Dark racing UI** — cyberpunk-themed cockpit interface

## Quick Start

### Requirements

- Python 3.10+ with `pip`
- Linux (tested on Kali/Debian), macOS, or WSL
- A WLtoys 6405 (or compatible) FPV RC car

### Install & Run

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/wltoys-fpv-cockpit.git
cd wltoys-fpv-cockpit

# Install dependencies
pip install flask av Pillow numpy

# Connect to the car's WiFi (SSID: WL_FPV_CAR_XXXXXXXX)
# The car creates its own access point — no internet needed

# Launch the cockpit
bash start.sh
```

The cockpit starts at **http://localhost:5555**. Open it in your browser.

To access from other devices on your LAN, use your machine's IP:
```bash
# Find your LAN IP
hostname -I | awk '{print $1}'
# Then open http://<your-lan-ip>:5555
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FPV_CAR_IP` | `172.16.11.1` | Car's WiFi IP address |
| `FPV_LISTEN_PORT` | `1234` | UDP port for video stream |
| `DISCORD_CLIENT_ID` | unset | Discord OAuth application client ID |
| `DISCORD_CLIENT_SECRET` | unset | Discord OAuth client secret. Keep this local and never commit it. |
| `DISCORD_REDIRECT_URI` | unset | Legacy single public callback URL, for example `https://<tunnel>/auth/discord/callback` |
| `DISCORD_REDIRECT_URIS` | unset | Comma-separated allowlist of callback URLs for public, LAN, and localhost access. Hosts must match the browser route that starts `/login`. |
| `DISCORD_PUBLIC_REDIRECT_URI` | `DISCORD_REDIRECT_URI` or HTTPS public URL | Safe fallback callback URL for unknown hosts. Use the Cloudflare/public HTTPS route. |
| `PUBLIC_BASE_URL` | `https://race.zen-rc.net` | Public base URL used to derive the fallback callback when no explicit public redirect is set. |
| `SESSION_SECRET` | random per process | Flask session signing secret; set a stable long random value in production |
| `ADMIN_DISCORD_IDS` | unset | Comma-separated Discord user IDs with admin controls |
| `ALLOWED_DISCORD_IDS` | unset | Comma-separated allowlist. Use `user_id` for drivers or `user_id:spectator` for spectator-only users |
| `DEFAULT_DRIVE_SECONDS` | `120` | Default active-driver session length, clamped to 15–3600 seconds |
| `MAX_REMOTE_SPEED_PERCENT` | `70` | Server-side speed cap for remote drivers, clamped to 5–100% |
| `BANNED_DISCORD_IDS_FILE` | `.banned_discord_ids` | Local persistent banlist file. Keep it private and out of git. |

```bash
# Example: custom car IP
FPV_CAR_IP=172.16.11.1 bash start.sh
```

### Discord Race Lobby Setup

1. Create a Discord application at <https://discord.com/developers/applications>.
2. Add redirect URLs for every route you want Discord to return to. For example, a public Cloudflare route plus LAN/localhost callbacks:
   ```text
   https://your-tunnel.example.com/auth/discord/callback
   http://192.168.178.142:5555/auth/discord/callback
   http://localhost:5555/auth/discord/callback
   ```
   The app selects the matching callback from the request host that starts `/login`; unknown hosts safely fall back to the public HTTPS callback.
3. Create a local env file that is **not committed**:
   ```bash
   touch .env.local
   chmod 600 .env.local
   ```
4. Put the OAuth and lobby settings in `.env.local`:
   ```bash
   DISCORD_CLIENT_ID=your_client_id
   DISCORD_CLIENT_SECRET=your_client_secret
   DISCORD_REDIRECT_URI=https://your-tunnel.example.com/auth/discord/callback
   DISCORD_REDIRECT_URIS=https://your-tunnel.example.com/auth/discord/callback,http://192.168.178.142:5555/auth/discord/callback,http://localhost:5555/auth/discord/callback
   DISCORD_PUBLIC_REDIRECT_URI=https://your-tunnel.example.com/auth/discord/callback
   SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
   ADMIN_DISCORD_IDS=your_discord_user_id
   ALLOWED_DISCORD_IDS=your_discord_user_id
   DEFAULT_DRIVE_SECONDS=120
   MAX_REMOTE_SPEED_PERCENT=70
   FPV_CAR_IP=172.16.11.1
   FPV_LISTEN_PORT=1234
   ```
5. Load the file safely before starting the app, or use a wrapper script such as `start-v2.sh` in a local test deployment.

For a Cloudflare Quick Tunnel, point the tunnel service at the Flask app on `http://<kali-lan-ip>:5555` and configure Discord with the public HTTPS callback URL. For LAN driving, also add the LAN callback URL in Discord and in `DISCORD_REDIRECT_URIS`; starting login from the LAN host will then return to the LAN host instead of the Cloudflare route.

### Docker (Windows / Mac / Linux)

```bash
# Clone and connect to car WiFi first, then:
cd wltoys-fpv-cockpit

# Linux (requires host networking for UDP)
docker compose up --build

# Windows Docker Desktop (host networking not supported)
docker compose -f docker-compose.windows.yml up --build
```

Open **http://localhost:5555** in your browser.

> **⚠️ Windows note:** `network_mode: host` does not work on Docker Desktop for Windows. Use the `docker-compose.windows.yml` variant which maps ports explicitly. Make sure your PC is connected to the car's WiFi before starting the container.

---

## Controls

### Keyboard (WASD + Arrows)

| Key | Action |
|-----|--------|
| `W` / `↑` | Forward |
| `S` / `↓` | Reverse |
| `A` / `←` | Steer left |
| `D` / `→` | Steer right |
| `W+A` | Forward + left |
| `W+D` | Forward + right |
| `S+A` | Reverse + left |
| `S+D` | Reverse + right |
| `Space` | Emergency stop |

### Sliders

- **⚡ Speed** (5–100%) — Throttle power. Default: 100%. Lower for indoor/careful driving.
- **🔄 Steering** (5–100%) — Steering angle. Default: 100%. Lower for gentle turns.

### D-Pad (Mouse/Touch)

Click or tap the D-pad buttons in the cockpit. Hold for continuous movement.

### Gamepad / USB Controller

Xbox One and standard HID gamepads are supported via the HTML5 Gamepad API.

---

## Protocol Documentation

The WLtoys 6405 uses a custom UDP protocol — **not RTSP, not encrypted**.

### Network Architecture

```
┌──────────────┐         WiFi (172.16.11.x)         ┌──────────────┐
│  Your PC     │ ◄──────────────────────────────────► │  FPV Car     │
│  (browser)   │                                      │  172.16.11.1 │
└──────┬───────┘                                      └──────┬───────┘
       │                                                     │
       │  Flask :5555                              UDP ports: │
       │  (web UI)                                   1234    │  Video stream
       │                                            23458   │  Motor/heartbeat
       │                                            23459   │  Handshake
```

### Connection Sequence

1. **Connect to car WiFi** — SSID: `WL_FPV_CAR_XXXXXXXX` (check your car's label)
2. **Handshake** (port 23459) — Wake the car + trigger video stream
3. **Heartbeat** (port 23458) — Keep-alive at 2Hz (every 0.5s)
4. **Video** arrives on port 1234 as fragmented UDP packets

### Video Stream Protocol (Port 1234)

Each video frame is split into ~40 UDP fragments. Each fragment has a 32-byte header:

```
Offset  Size  Field           Description
------  ----  -----           -----------
0       4     magic           0x5AA56CC6 (little-endian)
4       4     frame_size      Total H.264 frame size (LE)
8       4     seq_num         Frame sequence number (LE)
12      4     timestamp       Timestamp/counter (LE)
16      4     padding         Usually 0x00000000
20      2     total_frags     Number of fragments in this frame (LE)
22      2     frag_idx        Fragment index, 0-based (LE)
24      4     data_offset     Byte offset within the frame (LE)
28      4     data_len        Payload length of this fragment (LE)
32+     var   payload         Raw H.264 Annex B data
```

**Codec:** H.264 Constrained Baseline, Level 3.1, 640×360 @ 20fps

**Keyframes:** The car sends SPS+PPS+SEI+I-frame as a single large frame (~55KB, ~42 fragments) every ~15 frames. These are essential for the decoder to start/resume.

### Motor Control Protocol (Port 23458)

Motor commands are 16-byte UDP packets sent at 20Hz (every 50ms):

```
Offset  Byte   Description
------  ----   -----------
0       0xCA   Magic byte 1
1       0x47   Magic byte 2
2       0xD5   Magic byte 3
3-7     0x00   Reserved (zeros)
8       0x66   Command type
9       var    Steering: 0x00=full left, 0x80=center, 0xFF=full right
10      var    Throttle: 0x00=full reverse, 0x80=neutral, 0xFF=full forward
11      0x80   Reserved
12-13   0x00   Reserved
14      var    Checksum: byte9 XOR byte10 XOR 0x80
15      0x99   End marker
```

**Critical:** The car requires **continuous** commands. If it doesn't receive a packet for ~0.5s, it returns to neutral. The heartbeat thread sends the last motor state to keep the car in its current position.

**Checksum formula:** `byte14 = byte9 XOR byte10 XOR 0x80`

This was discovered by analyzing 402 captured packets (including 33 dual-axis commands) from the original Android app. Single-axis commands happen to work with simpler checksum logic, but **dual-axis commands require the XOR formula** — without it, the car silently rejects the packet.

### Handshake Protocol (Port 23459)

```
# Wake the car
→ A8 8A 21 00 06 00 00 00 01 00 00 00 00 00

# Trigger video stream
→ A8 8A 20 00 08 00 00 00 01 00 02 00 00 00 D2 04

# Car responds with ack packets:
← A8 8A 21 00 02 00 00 00 01 00
← A8 8A 20 00 02 00 00 00 01 00
```

Send 3x wake+trigger pairs with 200–300ms delays for reliability.

### Neutral/Heartbeat Packet

```
CA 47 D5 00 00 00 00 00 66 80 80 80 00 00 80 99
```

All axes at center (0x80). Used as heartbeat when no motor input is active.

---

## Project Structure

```
wltoys-fpv-cockpit/
├── README.md                   ← You are here
├── LICENSE                     ← MIT License
├── .gitignore
├── .dockerignore
├── Dockerfile                  ← Docker image definition
├── docker-compose.yml          ← Linux (host networking)
├── docker-compose.windows.yml  ← Windows Docker Desktop
├── requirements.txt            ← Python dependencies
├── start.sh                    ← Launcher script
├── start-fpv-debug.sh          ← LAN launcher script
├── CAR.pcap                    ← Reference packet capture from the original app
├── CAR WIFI INFO               ← Car connection reference
├── car_protocol.py             ← UDP protocol implementation
├── video_decoder.py            ← H.264 → JPEG decoder (via PyAV)
├── webapp.py                   ← Flask web server (REST API + MJPEG + SSE)
├── templates/
│   └── index.html              ← Cockpit UI
└── static/
    ├── style.css               ← Dark racing theme
    └── app.js                  ← Frontend logic (controls, gamepad, polling, logs)
```

## Architecture

```
Browser (WASD/D-pad)
    │
    ▼  REST API (POST /api/command)
Flask webapp.py
    │
    ├──► car_protocol.py ──► UDP socket :23458 ──► Car motors
    │         │
    │         ├── Handshake thread (:23459)
    │         ├── Heartbeat thread (:23458, 2Hz)
    │         └── Receiver thread (:1234)
    │                    │
    │                    ▼  Raw H.264 frames
    └──► video_decoder.py (PyAV) ──► JPEG
                │
                ▼  MJPEG stream
           Browser <img> (/api/stream)
```

---

## Video Decoder

The H.264 video decoder evolved through several iterations:

- **v4:** file-based ffmpeg subprocess per frame (~8-10fps)
- **v9:** PyAV in-process decode + PIL JPEG encode (<1ms per frame, ~25fps)
- **Key insight:** H.264 P-frames need the preceding keyframe in the bitstream to decode correctly

## Current V2 Roadmap

The Discord-authenticated V2 race lobby is working in local testing. The next performance pass is video-stream focused:

1. Make LAN streaming butter smooth first: stabilize frame pacing, reduce jitter, and verify decode/encode latency on the local network before changing the remote path.
2. After LAN output is smooth, refine web/Cloudflare streaming: tune browser delivery, remote buffering, and tunnel behavior without compromising local control latency.

---

## Troubleshooting

### Car doesn't respond to controls
- Check that you're connected to the car's WiFi (not your home WiFi)
- The car sleeps after ~60s without a handshake — reconnect if idle
- Try lowering the speed slider — the car may need a gentle start

### Video shows "NO SIGNAL"
- The video stream starts after the handshake completes
- Check the debug log panel for handshake/status messages
- The car's battery may be low — charge it

### Steering twitches or resets to center
- This was a known bug (now fixed) — the heartbeat was sending center values
- Make sure you're running the latest version

### Combos (W+A etc.) don't work
- Another fixed bug — the checksum formula was wrong for dual-axis commands
- Make sure you're running the latest version

### Can't connect from another device on LAN
- Make sure your firewall allows connections on port 5555
- The device must also be connected to the car's WiFi
- Use your machine's LAN IP, not localhost

---

## How It Was Built

This project was reverse-engineered from the Android APK (`com.lg.wltechfpvcar`) and a packet capture of the original app in action.

Key discoveries:
1. The car uses **custom UDP**, not RTSP or TCP
2. Video is **H.264 Baseline** (not H.265 as initially suspected)
3. The protocol is **not encrypted** — just raw bytes
4. The checksum is `B9 XOR B10 XOR 0x80`, not a simple copy
5. The car requires **continuous 20Hz commands** — it auto-centers without input
6. Motor byte mapping: **HIGH = forward/right, LOW = reverse/left** (counter-intuitive!)

See `CAR.pcap` for the original packet capture used for analysis.

---

## Compatible Cars

Tested with:
- **WLtoys 6405** (1/64 Mini RC with FPV camera)

May work with other WLtoys FPV cars that use the same `WL_FPV_CAR_XXXXXXXX` SSID pattern and the `com.lg.wltechfpvcar` app. If your car uses a different app, the protocol may differ.

---

## License

MIT — See [LICENSE](LICENSE) for details.

## Credits

- WLtoys for building a fun little car with an unencrypted protocol
- ffmpeg / PyAV for the H.264 decoding backend
- Flask for the lightweight web server
