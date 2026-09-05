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
- [ ] Discord OAuth = admins (`ADMIN_DISCORD_IDS`) + race friends (role allowlist)
- [ ] Guest one-time codes stay (generate/revoke UI in admin panel); NO Cloudflare Access
- [ ] Session hardening: httpOnly cookies, expiry, per-session car assignment
- [ ] Lock every control API behind role checks (currently raw endpoints must not be reachable by guests/non-drivers)
- [ ] Rate-limit control commands; input validation on all API bodies
- [ ] Bans/kick preserved from lobby branch

## Phase 2 — Control plane (WebSocket)
- [ ] Replace 20Hz HTTP POSTs with WebSocket control channel (fallback: HTTP)
- [ ] Server-side command scheduler: heartbeat continuity, deadman failsafe (car → neutral if ws drops)
- [ ] Input priority: admin override > active driver > queue
- [ ] Latency measurement hooks: input→TX timestamp, display one-way + round-trip estimates

## Phase 3 — Video pipeline (latency is the game)
- [ ] Send raw H.264 over WebSocket to browser; **WebCodecs `VideoDecoder`** render (Chrome/Edge/Safari 17+)
- [ ] Fallback ladder: WebCodecs → MSE → existing MJPEG (never a dead screen)
- [ ] Drop server-side JPEG re-encode from the hot path (keeps CPU for race engine)
- [ ] HEVC path decision `[?]` (browser support is weak — likely transcode or force H.264 per car)
- [ ] Target: <150 ms glass-to-glass over tunnel `[?]` (needs real track measurement)
- [ ] Per-car video health watchdog + auto-reconnect ("NO SIGNAL" recovery without page reload)

## Phase 4 — Race engine (server-authoritative)
- [ ] Race session state machine: lobby → countdown → green → checkered → results
- [ ] Speed governor in car command layer (default 75–80%, boost → 100%, never > protocol max)
- [ ] Effect system: timed modifiers on throttle/steer (stackable, priority-safe)
- [ ] **Safety gates (hard rule):** spin implemented as time-boxed steering lock, never full control inversion at speed; global admin kill switch; per-car disable
- [ ] Persistence: SQLite for races, results, drivers, guest codes (replace scattered JSON files)

## Phase 5 — Items: Garden Kart, phase 1
> Cars have NO position telemetry. Virtual positions = race-order bookkeeping,
> updated by admin as race director (or manual lap marks). `[?]` confirm UX.
- [ ] 🍄 Boost: unlock 100% throttle for N seconds
- [ ] 🍌 Banana: place in admin panel → when a car passes the zone `[?]` (manual tap-back initially) → steer-limit + slowdown effect + banana graphic on victim HUD
- [ ] 🔴 Red shell: admin targets player → victim screen flashes red → impact after delay (distance tiers) → brake/spin effect
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

## Error / incident log
| # | Date | What happened | Resolution |
|---|------|---------------|------------|
| — | — | (empty so far) | — |

## Open questions for Snoop
1. ~~Design/stack~~ → answered 2026-09-05 (hybrid design, RD console, soft safety, vanilla ES modules)
2. Track PC specs/Windows version + does it already run cloudflared today? (deploy target = "different PC, different LAN, Windows")
3. Real-hardware test session slot (needed to validate latency targets)
