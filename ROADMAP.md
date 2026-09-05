# 🏎️ FPV Cockpit V2 — "Garden Kart" Roadmap

> Living document. Maintained by Riko (strix-worker). Bunny: pick up any `[ ]` task.
> Branch: `v2-modern-cockpit` (based on `codex/windows-garden-dashboard` @ 52a0dad).
> `main` = stable V1 — never force-push, never break.

## Vision

Real-world Mario Kart on the garden track in Karben. Multiple WLtoys 6405 FPV cars,
driven through a modern web cockpit over LAN or Cloudflare tunnel, with a virtual
item layer that changes the race:

| Item | Real-world effect (agreed direction) |
|---|---|
| 🍄 Speed Boost | Cars are governor-limited to 75–80% by default; boost unlocks 100% |
| 🍌 Banana | Control break: steering limited/reversed for a short time, brief slowdown |
| 🐢 Red Shell | Targeted: victim's screen flashes red, impact after X sec (distance-based), then brake/spin |
| …more | green shell, star, triple shell — later phases |

## Status legend
`[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked/error · `[?]` needs Snoop decision

---

## Phase 0 — Foundations & Harness
- [~] Full audit of garden-branch codebase (routes, auth flow, stream bridge, HEVC autodetect) → `docs/AUDIT.md` (Bunny/agent, in progress)
- [ ] Repo structure: split 1600-line `webapp.py` into modules (`auth/`, `race/`, `video/`, `cars/`, `ws/`) — no behavior change
- [x] Local dev harness: **car simulator** (`sim/car_sim.py`) — protocol-accurate fake car, multi-sim ready, `sim/README.md`
- [x] Golden-path smoke test script (`sim/smoke_test.py`) — 5/5 PASS on 2026-09-05 (handshake, fragment reassembly, H.264→JPEG decode, deadman stops stream)
- [ ] Pin runtime deps + document track-PC environment `[?]` (Windows — specs unknown, see open Q2)
- [x] Per-commit discipline: small commits, push each to `v2-modern-cockpit`

## Phase 1 — Security & Auth cleanup (keep existing model)
- [x] **CRITICAL fixed 2026-09-05**: `/api/command` + Socket.IO now enforce server-side active-driver/admin (was: any logged-in identity could drive any car — AUDIT §6.1)
- [x] **CRITICAL fixed**: E-STOP blocks non-admin commands server-side; new **client-silence watchdog** — active driver's client silent >3s → forced neutral (was: heartbeat reinforced last throttle forever — AUDIT §6.2)
- [x] **HIGH fixed**: `/api/guest/test` backdoor (served ZENADMIN admin code publicly) disabled; hardcoded ZENGARDEN/ZENADMIN seeds removed → env `PERSISTENT_CODES=CODE:role,...` (AUDIT §6.3/6.4)
- [x] Guest codes now CSPRNG (`secrets`), `/api/guest/clear` dormant-code crash fixed, `/api/lights` guest-driver bug fixed, `/api/command` proper 403/400/503 mapping
- [x] Regression suite `test_control_auth.py` — 10 tests lock all of the above (22/22 total unit tests green, sim smoke 5/5, `hermes verify` green)
- [ ] Discord OAuth = admins (`ADMIN_DISCORD_IDS`) + race friends (role allowlist) — works, needs live creds (open Q4)
- [ ] Guest one-time codes stay; add generate/revoke UI polish in admin panel
- [x] Session hardening: rate limits on redeem + OAuth callback (sliding window), plaintext-credential debug log deleted, `init_car` double-init race fixed (AUDIT §6.5/6.7)
- [x] `/api/lobby` anonymous leak (banned IDs + usernames) → now requires login
- [x] CSRF posture review (SameSite=Lax only today)
- [x] `/api/status` now requires login (was anonymous car IP/SSID/stats)
- [x] Stream bridge default now `127.0.0.1` (was 0.0.0.0 no-auth LAN exposure); opt back in via `STREAM_BRIDGE_HOST=0.0.0.0`
- [x] Lock discipline: guest-expiry kick path now holds `state_lock` (AUDIT §6.7)
- [ ] Lock remaining control API behind role checks / input validation sweep
- [ ] Bans/kick preserved from lobby branch ✓ (verified by tests)

## Phase 2 — Control plane (WebSocket)
- [x] WebSocket control channel with HTTP fallback — verified E2E (sim/socket_e2e.py, was already Socket.IO-first on this branch; now locked by regression test)
- [x] Server-side command scheduler: heartbeat continuity, deadman failsafe (client-silence watchdog, Phase 1) + latency bookkeeping per command
- [x] Input priority: admin override > active driver > queue — new `admin_override` lobby lock (admin action `admin_override` {value:bool}: engages → car neutral, non-admin commands rejected 403 `admin_override`; RELEASE CONTROL button in admin panel)
- [x] Latency measurement hooks: `control:rtt` clock-sync ping, per-command `server_ts`/`echo_ts` in ack, server rolling input→RX and RX→UDP-TX stats in `/api/status` (`control_latency_*`, `control_tx_*`), cockpit latency badge (green <80ms / yellow <200ms / red)

## Phase 3 — Video pipeline (latency is the game)
- [~] Send raw H.264 over WebSocket to browser; **WebCodecs `VideoDecoder`** render (Chrome/Edge/Safari 17+) — **backend + client built & sim-E2E green 2026-09-05** (commit 7d0896d: `/ws/video/<car>` + signed tokens + `static/video.js`); real-browser tunnel test pending
- [~] Fallback ladder: WebCodecs → MSE → existing MJPEG (never a dead screen) — WebCodecs→MJPEG ladder implemented in video.js; MSE rung not needed yet
- [~] Drop server-side JPEG re-encode from the hot path — MJPEG decoder now runs only when a `/api/stream` viewer is attached `[needs verify at track]`
- [ ] HEVC path decision `[?]` (browser support is weak — likely transcode or force H.264 per car)
- [ ] Target: <150 ms glass-to-glass over tunnel `[?]` (needs real track measurement)
- [ ] Per-car video health watchdog + auto-reconnect ("NO SIGNAL" recovery without page reload)

## Phase 4 — Race engine (server-authoritative)
- [~] Race session state machine: idle → countdown (3s, all cars neutral) → green → finished + finish-order bookkeeping — **engine done 2026-09-05** (`race_engine.py`, injected clock, 21 tests); HUD display pending Phase 6
- [~] Speed governor in car command layer (default 80% via `RACE_GOVERNOR_PERCENT`, boost → 100%, never above `MAX_REMOTE_SPEED_PERCENT` — set that env to 100 at the track if you want full-speed boost)
- [~] Effect system: timed modifiers on throttle/steer (stackable, priority-safe) — boost/banana/redshell/star server-side; item bag UX pending
- [x] **Safety gates (hard rule):** red-shell "spin" = 1s ONE-SIDED bounded turn (`forward_right`, never through center, soft-throttled); banana = steer-magnitude limit + slowdown, never inversion; E-STOP still outranks everything (locked by test); global admin kill switch; per-car disable
- [ ] Persistence: SQLite for races, results, drivers, guest codes (replace scattered JSON files)

## Phase 5 — Items: Garden Kart, phase 1
> Cars have NO position telemetry. Virtual positions = race-order bookkeeping,
> updated by admin as race director (or manual lap marks). `[?]` confirm UX.
- [~] 🍄 Boost: unlock 100% throttle for N seconds — **server-side done** (`item_grant {user_id, item:"boost"}`), HUD item box pending
- [~] 🍌 Banana: place in admin panel → when a car passes the zone `[?]` (manual tap-back initially) → steer-limit + slowdown effect + banana graphic on victim HUD — **effect server-side done**; zone-tap UI pending
- [~] 🔴 Red shell: admin targets player → victim screen flashes red → impact after delay (distance tiers) → brake/spin effect — **impact effect server-side done** (1s one-sided lock); flash/delay UX pending
- [ ] Item distribution: ranking-based (place → item bag, Mario Kart style)
- [ ] HUD item UI: item box, use key/button (Xbox: RT+LB or face button `[?]`)
- [ ] Green shell / star / triple shell — later

## Phase 6 — Frontend: "Garden Kart" cockpit
- [ ] Design pass `[?]`: mockups first (GPT-image concepts + selection), then implement — NOT before
- [ ] Stack decision `[?]`: keep vanilla ES-modules (zero build, easy track-PC deploy) vs Vite+TS
- [ ] Drive view: video fullscreen-first, HUD overlay (speed est., item, latency, lap, position)
- [ ] Race lobby view, admin race-director console (items, kill switch, driver queue)
- [ ] Split view for spectators (car1..3 grid)
- [ ] Mobile guest view polish
- [ ] Reconnect/offline UX everywhere (tunnel blips must not kill a race)

## Phase 7 — Hardening & deploy
- [ ] waitress/gunicorn prod server (no Flask dev server over a tunnel)
- [ ] Log rotation, error reporting channel
- [ ] Deploy docs: LAN mode + Cloudflare tunnel mode; Docker for both Linux and Windows track PC
- [ ] Race-day runbook (start order, car wake, failure playbook)

---

## Decision log
| # | Date | Decision | Why |
|---|------|----------|-----|
| 1 | 2026-09-05 | V2 based on `codex/windows-garden-dashboard` tip (52a0dad) | Newest strict superset of all branches (Discord OAuth, admin, multi-car split view, mobile) |
| 2 | 2026-09-05 | Auth model kept: Discord OAuth admins, one-time guest codes, **no Cloudflare Access for guests** | Snoop's explicit requirement |
| 3 | 2026-09-05 | `main` stays V1 stable fallback | Running service at track must survive anything we do |
| 4 | 2026-09-05 | Governor 75–80% default / 100% on boost | Mario Kart feel + real-world safety margin |
| 5 | 2026-09-05 | Banana = steer-limit/slowdown, NOT full control inversion; red shell spin = time-boxed steering lock | Physical safety on a real track with people around |
| 6 | 2026-09-05 | All item effects server-authoritative | Client code is visible/editable — no trust boundary at the browser |
| 7 | 2026-09-05 | Design = **Hybrid**: serious sim cockpit while driving, Mario-Kart party look in lobby + item effects | Snoop's pick; mockups (GPT-image) before any UI code |
| 8 | 2026-09-05 | Item triggering = **admin race-director console** (manual drag/place onto cars or zones) | No position telemetry; overheat later if we add camera tracking |
| 9 | 2026-09-05 | Safety dial = **Soft**: steer-angle limit + mild slowdown 2–3s for banana/shell hits | Real track, real people; 'Wild' rejected |
| 10 | 2026-09-05 | Frontend = **vanilla ES modules + modern CSS** (Riko's call per Snoop) | Prod runs on a Windows PC in a different LAN — zero build step, file copy deploy, no Node on track PC |
| 11 | 2026-09-05 | ZENGARDEN/ZENADMIN persistent codes **removed**; replace via `PERSISTENT_CODES` env if the track still needs them | Source-visible admin backdoor + public `/api/guest/test` leak; **breaking** if the track relied on those codes — Snoop must re-grant via env or admin panel |
| 12 | 2026-09-05 | E-STOP semantics: blocks all non-admin control until admin resumes | A driver's 20Hz loop must not override safety (AUDIT §6.2) |

## Error / incident log
| # | Date | What happened | Resolution |
|---|------|---------------|------------|
| 1 | 2026-09-05 | Attempted to delete the AUDIT-flagged "duplicated" `pollGamepad` block in app.js; file broke (`node --check`). Brace analysis showed the AUDIT finding was a **misdiagnosis** — both copies are live (single-device vs combined mode). | Reverted file untouched; AUDIT §6.9 corrected. Lesson: `node --check` + brace analysis BEFORE trusting any "dead code" claim; app.js dedupe deferred to a controller-on hardware session (Phase 3). |
| 2 | 2026-09-05 | Removing hardcoded ZENGARDEN/ZENADMIN broke 2 old tests that depended on the backdoor | Tests re-seeded via the new `PERSISTENT_CODES`-style fixture (test_lobby_admin) — semantics still covered |
| 3 | 2026-09-05 | First `race_engine.modify()` treated `steer_range` as a wheel POSITION (center=50) — wrong: **it is a deflection magnitude; direction lives in `command`** (see `car_protocol.send_command`). Caught by own tests before touching hardware. | Reworked: banana clamps magnitude, red-shell pins `command` to a bounded one-sided turn. Protocol fact recorded here |

## Open questions for Snoop
1. ~~Design/stack~~ → answered 2026-09-05 (hybrid design, RD console, soft safety, vanilla ES modules)
2. Track PC specs/Windows version + does it already run cloudflared today? (deploy target = "different PC, different LAN, Windows")
3. Real-hardware test session slot (needed to validate latency targets)
4. Discord OAuth app credentials + redirect URIs for the tunnel domain (PUBLIC_BASE_URL suggests `https://race.zen-rc.net`) — needed to test the admin login path at all; plus which of the persistent codes (ZENGARDEN/ZENADMIN) stay active
