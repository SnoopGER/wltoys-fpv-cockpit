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
- [~] HEVC path decision → **D14 (2026-09-06): H.264 primary via WebCodecs; probe per-car codec at track; HEVC → MJPEG for now**, transcode-vs-car-config decided after track numbers
- [ ] Target: <150 ms glass-to-glass over tunnel `[?]` (needs real track measurement)
- [ ] Per-car video health watchdog + auto-reconnect ("NO SIGNAL" recovery without page reload)

## Phase 4 — Race engine (server-authoritative)
- [~] Race session state machine: idle → countdown (3s, all cars neutral) → green → finished + finish-order bookkeeping — **engine done 2026-09-05** (`race_engine.py`, injected clock, 21 tests); HUD display pending Phase 6
- [x] Speed governor in car command layer — **D15 semantics implemented 2026-09-06**: race governor (default 70, `RACE_GOVERNOR_PERCENT`) owns the ceiling; 🍄 boost → 100% power (`RACE_BOOST_MAX_PERCENT`) for `RACE_BOOST_SECONDS` (5s) with `RACE_BOOST_COOLDOWN_SECONDS` (15s) lockout; free-drive lobby cap untouched. HUD cooldown ring pending Phase 6
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
- [~] Design pass: 3 concepts built & browser-QA'd (design/sketches/) → direction picked 2026-09-05 (D13): merged cockpit Glass⇄Apex density toggle + Pitlane lobby; production implementation pending
- [x] Stack decision `[?]`: keep vanilla ES-modules (zero build, easy track-PC deploy) — decided, D10
- [~] Drive view: video fullscreen-first, HUD overlay (speed est., item, latency, lap, position) — concept done (cockpit.html, Glass⇄Apex) incl. car selector (CAR 1-3); production pending
- [~] Race lobby view, admin race-director console (items, kill switch, driver queue) — concept done (pitlane.html); production pending
- [~] Split view for spectators (car1..3 grid) — concept done as GRID view in cockpit.html (works in both densities, tile click-through); production pending
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
| 13 | 2026-09-05 | **Frontend direction:** ONE drive cockpit with a **Glass ⇄ Apex density toggle** (persisted per browser), **Pitlane** as lobby/RD-console style; NO three-skin dropdown | Snoop's call after design lab review; maintenance stays one-layout + one party theme |
| 14 | 2026-09-06 | **Video: speed wins.** WebCodecs H.264 WS relay = primary; per-car codec probe at track; HEVC cars ride MJPEG fallback until transcode/car-config decision. Speed ceilings: run 70 first, bump to 80 only if it feels slow | Snoop delegated ("fast video, controls must not feel laggy"); latency target ≤150 ms tunnel, lower is better |
| 15 | 2026-09-06 | **Speed model (supersedes D4 numbers):** during a race the ceiling lives in the race engine — governor limits all cars (default **70**, env `RACE_GOVERNOR_PERCENT`); 🍄 boost unlocks **100% car power** for `RACE_BOOST_SECONDS` (5s), then `RACE_BOOST_COOLDOWN_SECONDS` (15s) lockout. Free-drive keeps the lobby/MAX_REMOTE cap. Lobby clamp must NOT pre-clamp race commands | Snoop: "default speed is 100 limit but cars limited to 70/80 unless BOOST is active" — old layering made boost == baseline; fixed + locked by tests |
| 16 | 2026-09-06 | **Virtual Race = custom clone, no ROM/emulator.** MAME netplay + item injection into a black box is worse on every axis; Sega IP hosting risk avoided entirely | Snoop+Bunny locked; Riko verified patterns fit repo |
| 17 | 2026-09-06 | Road renderer forks the **javascript-racer projection architecture** (MIT, credit header kept in `static/virtual/road.js`). Its bundled music is licensed only for that project → BGM is an **original procedural WebAudio synthwave loop** (`bgm.js`, zero assets) | License text covers code, not media |
| 18 | 2026-09-06 | Per-car state = `(track_pos increasing metres, lane ∈ [-1,1])`, NOT full 3D. Collisions = z-window (`CAR_LEN`) + lane-overlap; banana traps use absolute coords (lap-safe; owner can't self-hit until they lap) | Cheap authority, no desync surface |
| 19 | 2026-09-06 | Sim speed FREE (not tied to real-car governor D15) but **identical model for every car** (top 60 m/s, accel 15): fairness comes from items + lines only | Snoop: all cars same model; fairness via items |
| 20 | 2026-09-06 | `virtual_race.py` pure-state engine (injectable clock+rng, no sockets), same discipline as `race_engine.py`: unit tests per mechanic (26 green) | Repo-proven pattern |
| 21 | 2026-09-06 | Reconnect: car persists under same uid; input silence > 5s coasts to neutral; socket reconnect re-attaches **without page reload** (verified E2E) | Final-gate requirement |
| 22 | 2026-09-07 | E-STOP freezes the virtual world too (tick + inputs gated); virtual race NEVER touches the real-car control path | Safety rule parity with real game; soft-safety only, controls never invert |

## Error / incident log
| # | Date | What happened | Resolution |
|---|------|---------------|------------|
| 1 | 2026-09-05 | Attempted to delete the AUDIT-flagged "duplicated" `pollGamepad` block in app.js; file broke (`node --check`). Brace analysis showed the AUDIT finding was a **misdiagnosis** — both copies are live (single-device vs combined mode). | Reverted file untouched; AUDIT §6.9 corrected. Lesson: `node --check` + brace analysis BEFORE trusting any "dead code" claim; app.js dedupe deferred to a controller-on hardware session (Phase 3). |
| 2 | 2026-09-05 | Removing hardcoded ZENGARDEN/ZENADMIN broke 2 old tests that depended on the backdoor | Tests re-seeded via the new `PERSISTENT_CODES`-style fixture (test_lobby_admin) — semantics still covered |
| 3 | 2026-09-05 | First `race_engine.modify()` treated `steer_range` as a wheel POSITION (center=50) — wrong: **it is a deflection magnitude; direction lives in `command`** (see `car_protocol.send_command`). Caught by own tests before touching hardware. | Reworked: banana clamps magnitude, red-shell pins `command` to a bounded one-sided turn. Protocol fact recorded here |
| 4 | 2026-09-07 | VR snapshot loop stopped emitting the moment FINISHED flipped, so the results-bearing snapshot never reached clients. Found only by the 2-browser QA (protocol-level E2E had not asserted state delivery). | Loop broadcasts 20 extra beats after FINISHED; P3 E2E now asserts `state==finished` arrives. Lesson: **assert the last frame, not just the flow** |
| 5 | 2026-09-07 | Jinja `{{ 'dev' if cond else 'live' | tojson }}` — filter bound tighter than the conditional → browser got raw `mode: dev` (ReferenceError). Also: two cars side-by-side at green means the leader has NO red-shell target (`dz>0` strict) — QA harness must stagger starts. | Parenthesized the conditional; harness orders starts. Both are gameplay-real facts, not just test trivia |

## Open questions for Snoop
1. ~~Design/stack~~ → answered 2026-09-05 (hybrid design, RD console, soft safety, vanilla ES modules); drive-view direction locked D13
2. ~~Track PC~~ → answered 2026-09-06: old V1 already deployed and running there, a Hermes instance on that box assists with ports + git clone → use `docs/track-session-checklist.md`
3. ~~Real-hardware test slot~~ → answered: track session 2026-09-06 afternoon
4. Discord OAuth — **worked previously**, so creds + redirect URI exist in the old deployment's env; TASK: copy them verbatim into the V2 env (exact same redirect string). Persistent codes: old ZEN-* backdoors dead; replacement env pattern in checklist, new codes handed to Snoop privately.
5. Speed ceilings → answered + superseded by **D15**: race ceiling lives in the engine (governor 70 default, boost → 100% power, 5s/15s cooldown, all env-tunable). Free-drive stays capped at 70.
6. HEVC → **Riko's call (D14, speed wins)**: WebCodecs H.264 relay is the primary path; probe `codec` per car at the track via `/api/video-token?car=carN`. HEVC-streaming cars ride the MJPEG fallback until we decide transcode-vs-car-config. Decision after track numbers.
7. Item UX → answered 2026-09-06: manual RD bookkeeping OK for now; **IR lap/time tracker exists in Snoop's gear shed — not fitted on the tiny cars yet → future Phase-5 upgrade path** (auto positions/laps). Item-use button mapping to be decided on the real controllers at the track.
8. Latency target → answered: ≤150 ms tunnel acceptable, faster is better; measure LAN vs tunnel in test #8
