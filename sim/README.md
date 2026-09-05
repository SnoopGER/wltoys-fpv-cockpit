# 🏁 Dev Harness — Car Simulator

Develop the cockpit **without** the physical cars or the track PC.
`car_sim.py` emulates one WLtoys 6405 on localhost with a bit-accurate
protocol implementation (32-byte fragment header, motor frame, checksum,
deadman behavior).

## Requirements
- Python venv: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- `ffmpeg` on PATH

## Run

```bash
# terminal 1 — fake car (streams immediately, no handshake required)
.venv/bin/python sim/car_sim.py --always-on

# terminal 2 — cockpit pointed at the sim
FPV_CAR1_IP=127.0.0.1 FPV_CAR_IP=127.0.0.1 .venv/bin/python webapp.py
# → http://localhost:5555

# regression check (starts its own sim, exits non-zero on failure)
.venv/bin/python sim/smoke_test.py
```

Multiple sims for multi-car work:

```bash
.venv/bin/python sim/car_sim.py --always-on --ctl-port 23458 --hs-port 23459 --video-port 1234
.venv/bin/python sim/car_sim.py --always-on --ctl-port 33458 --hs-port 33459 --video-port 1235
# FPV_CAR2_LISTEN_PORT=1235 ... (the webapp supports per-car listen ports)
```

## Behavior modeled
| Real car | Simulator |
|---|---|
| Wakes on `a88a21…` / starts stream on `a88a20…` (UDP 23459) | ✅ same packets recognized |
| Streams H.264 baseline 640×360@20 to host:1234, fragmented (≤1440B + 32B header) | ✅ ffmpeg testsrc + wall-clock overlay, same framing |
| `ca47d5…` motor frame at 20Hz on 23458, checksum `B9^B10^0x80` | ✅ decoded + checksum-validated, logged on change |
| Returns to neutral / stream stalls when commands stop (~0.5–1s) | ✅ 1s deadman stops streaming (`--always-on` disables) |

## Not modeled (yet)
- Lights command, actual wheel physics/position (no telemetry exists in the real protocol either), packet loss/jitter simulation (`--loss` TODO).
