# FPV Dashboard V1 — Completion Milestone

Status: **complete / stable baseline**
Date: 2026-05-17
Branch: `main`

FPV Dashboard V1 is the completed local/LAN cockpit for the WLtoys 6405 FPV RC car. It remains the stable baseline for direct browser control and protocol reference work.

## Completed scope

- Reverse-engineered the WLtoys FPV car's unencrypted UDP protocol from the Android app and packet capture.
- Implemented the UDP handshake needed to wake the car and trigger the video stream.
- Implemented H.264 video ingest on UDP port `1234` and in-browser MJPEG display.
- Implemented motor control on UDP port `23458` with continuous command heartbeat.
- Fixed the motor checksum to `steering_byte XOR throttle_byte XOR 0x80`, which is required for dual-axis commands.
- Fixed direction mapping: high throttle byte is forward, low throttle byte is reverse; high steering byte is right, low steering byte is left.
- Implemented keyboard, D-pad, Xbox controller, and sim-racing controller input support.
- Added speed/steering sliders, debug logs, protocol reference endpoints, and raw packet tooling for local debugging.
- Preserved the working decoder behavior: H.264 frames must not be skipped because P-frames depend on preceding keyframes.

## Stable V1 operating model

V1 is intended for local/LAN operation:

1. Connect the host machine to the car WiFi.
2. Start the Flask cockpit manually.
3. Open the dashboard in a browser on `http://localhost:5555` or the host LAN IP.
4. Use keyboard, D-pad, or controller input to drive.

V1 does not include the Discord race lobby, remote-driver queue, admin moderation, or Cloudflare/LAN dual OAuth routing. Those features belong to the V2 test branch.

## Known hardware behavior

- Xbox controller over Bluetooth exposes four axes for sticks. LT/RT are buttons with analog `.value`, not axes.
- Moza MBoster pedals and R9 wheelbase can appear as separate browser gamepads; combined mode is needed to use pedals and wheel together.
- The car requires a continuous heartbeat/command stream. If packets stop for roughly half a second, it returns to neutral.

## V2 handoff

V2 development continues on branch `codex-discord-race-lobby` as a separate test instance. V2 adds:

- Discord OAuth allowlist/admin access.
- Race lobby and timed driver queue.
- Admin monitoring, kick, ban, and unban controls.
- LAN and Cloudflare access with dynamic Discord redirect URI selection.

The completed V1 baseline should remain available on `main` for reference and rollback.
