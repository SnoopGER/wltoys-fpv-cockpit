# Track Deployment & Race-Day Test Checklist (v2-modern-cockpit)

For the track PC (Windows, runs the old V1 + a Hermes helper). Zero build step:
file copy + Python venv. `main` = stable V1 — if V2 misbehaves, `git checkout main`
and restart: the old cockpit is byte-identical to what runs today.

## 1. Deploy
```powershell
git clone -b v2-modern-cockpit https://github.com/SnoopGER/wltoys-fpv-cockpit  # or: git fetch && git checkout v2-modern-cockpit
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
Copy the values from the OLD deployment's env into `.env.local` (gitignored):
`DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, redirect URIs / `PUBLIC_BASE_URL`
(same tunnel domain — OAuth already worked once, keep the exact redirect string),
`SESSION_SECRET` (generate a new one), car IPs, `PORT`.

**Persistent codes — old ZENGARDEN/ZENADMIN are DEAD (removed as security
backdoors).** Replacement goes in the env file, NEVER in git (generate your own:
`python -c "import secrets,string; a=''.join(c for c in string.ascii_uppercase if c not in 'OIL')+string.digits; print('GKADMIN-'+''.join(secrets.choice(a) for _ in range(4))+'-'+''.join(secrets.choice(a) for _ in range(4)))"`):
```
PERSISTENT_CODES=GKADMIN-XXXX-XXXX:admin,GKDRIVE-XXXX-XXXX:driver
```
(Rotate any time: change the value + restart. Codes for Snoop are in his private
chat with Riko, 2026-09-06 — not in this repo.)

## 2. Gate before anyone touches a car
```
.venv\Scripts\python sim\smoke_test.py     # protocol sanity (uses simulator, no car needed)
.venv\Scripts\python webapp.py
```
Open `http://localhost:PORT/`, log in, E-STOP visible, `/api/status` answers.

## 3. Car test ladder (stop at the first failure)
| # | Test | Pass condition |
|---|------|----------------|
| 1 | Handshake: `POST /api/handshake {"car":"all"}` | every car `true` |
| 2 | MJPEG stream (proven V1 path) | live frames, note feel |
| 3 | WebCodecs relay: check `GET /api/video-token?car=carN` → `codec` field | `h264` → cockpit relay live; `hevc` → tell Riko (falls back to MJPEG for now) |
| 4 | Drive at governor default (MAX_REMOTE_SPEED_PERCENT=70) | steering/throttle feel, latency badge green <80 ms |
| 5 | E-STOP while rolling | car neutral immediately, non-admin blocked |
| 6 | Client-silence watchdog: kill the browser tab mid-drive | car neutral ≤3 s |
| 7 | Guest code redeem on a phone + admin override lock | queue/kick/override behave |
| 8 | Latency audit: `GET /api/status` → `control_latency_avg_ms` / `_p95_ms` over LAN, then over the Cloudflare tunnel | LAN target <80 ms, tunnel target <150 ms |

Log the numbers — governor goes 70 → 80 only after #8 looks sane.

## 4. Race engine smoke (no items at first!)
Admin: `race_start` → 3-2-1 (cars held neutral) → green → drive → `race_finish`.
Items (banana/boost/red shell) exist but are **not** enabled on real cars until
Snoop signs off after clean laps. Server-side safety: effects never invert
steering; worst case is a 1 s bounded one-sided turn.

## 5. Rollback
`git checkout main && restart` — done. Codes/OAuth of the old deployment untouched.
