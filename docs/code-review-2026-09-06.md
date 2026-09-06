# Code Review — 2026-09-06 (pre-track)

Scope: `webapp.py`, `race_engine.py`, `video_relay.py`, `car_protocol.py`,
`static/app.js`, `static/video.js`, `static/mobile.js`, templates.
Method: two independent deep reviews + lead verification — **every finding
below was re-checked against the code at `83e21d0` by quoting the cited lines**.
Status: **flagged for review — no fixes applied yet.**
Severity: P0 = must fix before racing through the tunnel · P1 = fix today if
time allows · P2 = backlog.

---

## P0 — fix before the track session

### SRV-1 · Cloudflare tunnel makes "local-only" routes public
`webapp.py:971` `local_request()` trusts `request.remote_addr`. cloudflared
connects to the origin **over loopback**, so every internet visitor arrives as
`127.0.0.1`. No ProxyFix, no `CF-Connecting-IP` handling anywhere.
Consequences (gates verified at 1026 / 1396 / 1406 / 1609):
- `GET /api/guest/debug` → dumps **every drive code + active sessions** to anyone
- `POST /api/guest/debug-generate` → free driver codes, no auth
- `/admin/cars` renders for anonymous visitors
Also `rate_limited(f"redeem:{remote_addr}")` (1074/1379) collapses to ONE bucket
for the whole internet → 10 scripted attempts lock out every legitimate login
(entry DoS).
**Fix:** real client IP from `CF-Connecting-IP` (single trusted hop = the tunnel);
gate `/api/guest/debug*` on admin session, not loopback; bind origin to localhost.
⚠ Same pattern likely exists in the **currently deployed V1** — check it too.

### WEB-1 · WebCodecs → MJPEG fallback always shows a permanently blank screen
`video.js:86-101` `fallbackToMjpeg()` calls the original `startVideoStream()`,
but the wrapper already set `streaming = true` (`video.js:351` → `app.js:47`
`if (streaming) return;`). The original early-returns, `#videoFeed.src` is never
assigned, canvas is hidden → **black video area on every fallback path**
(no H.264 profile, auth error, unrecoverable stall). Directly contradicts the
file's "Never a dead screen" contract — and fallback is exactly what a HEVC car
or flaky tunnel will hit today.
**Fix:** in `fallbackToMjpeg`: `window.setStreaming(false)` (+ un-hide overlay)
before calling `__startVideoStreamOriginal()`. One-liner.

### WEB-2 · Car keeps driving after gamepad disconnect
`app.js:685-694`: on `gamepaddisconnected`, `stopMotor()` is never called — the
50 ms motor interval keeps re-sending the held command at 20 Hz with fresh
timestamps, so the server's silence watchdog never fires. Battery dies → car
drives on unattended.
**Fix:** `stopMotor()` unconditionally in the disconnect handler.

### WEB-3 · Car keeps driving after Alt-Tab / tab switch
No `blur`/`visibilitychange` handler anywhere in `app.js`/`mobile.js` (verified:
only input-field blurs). keyup goes to the newly focused window → `activeKeys`
keeps 'w' → commands keep flowing indefinitely.
**Fix:** `window.blur` + `visibilitychange:hidden` → `stopMotor()` +
`activeKeys.clear()` (mobile: `releaseAllButtons()`).

### SRV-2 · Safety watchdog dies silently on any exception
`timer_loop` (`webapp.py:841-856`) has **no try/except** and `ensure_timer`
(808-814) never respawns (`timer_started` stays True). One exception from
kick/emit/snapshot/init_car kills the daemon forever: control-silence watchdog
OFF, guest budgets frozen, driver rotation dead, a stuck countdown freezes all
cars.
**Fix:** wrap per-tick body in try/except-log; supervisor respawns on death.

---

## P1 — fix today if time allows

### SRV-3 · `race.tick()` runs outside `state_lock`
`webapp.py:849` vs every other race call site (679, 952, 1255-1278 all locked).
Concrete failures: tick-vs-reset → engine stuck GREEN (governor silently clamps
everyone with no race running); tick/green vs `race.snapshot()` →
`race_engine.py:230-232` re-reads `countdown_until` after its truthiness check →
TypeError inside `lobby_snapshot` → HTTP 500 / timer-thread death (→ SRV-2).
**Fix:** move tick inside `with state_lock:` (or lock inside RaceEngine).

### SRV-4 · `state_lock` held across disk + socket I/O on admin paths
`kick_user`/`ban_user` do `write_text`/`chmod`/`socketio.emit` **while holding
state_lock** (verified 776-796, 306-309; also `expire_guest_sessions` 539-541).
The 20 Hz control path queues behind those writes → 100 ms+ stalls mid-race,
in bursts when guest codes expire together at an event.
**Fix:** collect side effects under the lock, execute them after release.

### SRV-5 · Expired/kicked guests keep live video forever
`ws_video` (`webapp.py:1708-1751`): token verified once at connect; the 5 s
recheck only tests ban + a captured expiry snapshot. A guest kicked mid-session
keeps streaming; a *re-connect* with a stale token passes the entry check
(uid `guest-*` skips `is_allowed_user`, `guest_info=None` → never revoked).
**Fix:** recheck `active_guest_sessions` membership live; revoke on absence.

### SRV-6 · Malformed control frames break the driver's loop
`handle_control_command` assumes `data` is a dict: `data='x'` → AttributeError
(924), `car:5` → `.strip()` AttributeError (351), `command:{}` → unhashable (930).
Process survives (socketio catches) but the victim's acks stop arriving.
**Fix:** `isinstance(data, dict)` guard + `str(car_id)` coercion.

*(SRV-1 rate-limit collapse is P0-adjacent — fix together with SRV-1.)*

---

## P2 — backlog (all verified, low urgency)

| ID | Where | Issue |
|----|-------|-------|
| SRV-7 | webapp.py:1399 | `/api/guest/debug` 500s when a dormant (unredeemed) code exists (`expires_at=None` arithmetic) |
| SRV-8 | webapp.py:1311-1323 | `/api/guest/revoke` marks code inactive but the **redeemed session keeps full drive access** until natural expiry — "revoke" ≠ egress; call `kick_user` too |
| SRV-9 | webapp.py:674 | full banned-ID list broadcast to every spectator/guest in every `lobby:update` |
| SRV-10 | webapp.py:1618-1628 | `api_send_raw` leaks a UDP fd per failed send (`close()` not in `finally`); local `sock` shadows flask-sock |
| SRV-11 | webapp.py:808-814 | `ensure_timer` check-then-act race → two timer threads if two clients connect simultaneously at startup |
| SRV-12 | webapp.py:470/498/783 vs 407 | `guest_codes_lock` held across disk writes → admin code ops inject jitter into guest drivers' per-command path |
| SRV-13 | webapp.py:1843-1844 | ws_connect does private emit AND broadcast → double full snapshot per join; per-second broadcast materializes `all_car_snapshots()` (hot-path jitter) |
| SRV-14 | webapp.py:37/1915 | SESSION_SECRET unset → silent `os.urandom` fallback (all tokens die on restart); public deploy on Werkzeug dev server, thread-per-connection, no cap |
| WEB-4 | video.js:119-137+224 | `restartWs` captures `gen` BEFORE `stopWebCodecsKeepMode()` increments it → new socket's guard discards every frame: first decode desync permanently kills WebCodecs until MJPEG watchdog (~8 s). Also pending `restartTimer` never cleared → orphan sockets streaming over the tunnel |
| WEB-5 | video.js:241-258 | `_pending` buffer uncapped (grows forever if stream never hits a keyframe) and not cleared on stop |
| WEB-6 | video.js:166-174/259-261 | no decode backpressure: frames fed regardless of `decodeQueueSize` → latency ratchets up on weak devices |
| WEB-7 | video.js:139-150 | `frame.close()` skipped if `drawImage` throws → hardware VideoFrame leak |
| WEB-8 | video.js:214 | video token travels in WS URL query → captured by proxy/tunnel access logs |
| WEB-9 | app.js:504-508 | `setInterval(syncClockOffset, 5000)` re-armed on EVERY socket reconnect, never cleared → N intervals after N reconnects (tunnel blips) |
| WEB-10 | app.js:290/482-495 | drive expiry flips `canControl` false but never stops a running motorInterval → 20 Hz of rejected commands + WARN log spam (server gates it, safe but wasteful) |
| WEB-11 | app.js:902-944 | duplicated apply-input block in `pollGamepad` (accident; currently masked by `currentCommand` guard) |
| WEB-12 | app.js:749+959 | combined pedals+wheel share `gpBtnPrev` keyed by button index only → phantom triggers incl. button 0 = E-STOP |
| WEB-13 | app.js:229-244 | HTTP fallback sends a POST every 50 ms with no in-flight guard → unbounded stale-command pileup on high-RTT links |
| WEB-14 | app.js:1411-1419/1475-82 | guest/persistent code rows interpolated raw into `innerHTML` + inline `onclick` → stored XSS in admin pages (codes are admin-entered text) |
| WEB-15 | app.js:1160-6 + video.js:311 | 📷 snapshot button dead in WebCodecs mode (`videoFeed.src` empty; `FPVVideo.snapshot()` exists, unwired) |
| WEB-16 | app.js:1105 vs HTML | log filter id typo (`logFilterError` vs `logFilterErr`) → ERROR rows render despite filter off |
| WEB-17 | index.html:204-210 | desktop dpad lacks `ontouchcancel="stopMotor()"` (mobile.js has it) → held command on touch-cancel |

## Not issues (checked, clean)
- `car_protocol.send_command` never raises (catch-all) — dead car degrades to `ok:false` gracefully.
- UDP send sits **outside** `state_lock` (958) — hot path correct.
- No lock-order inversion found (state_lock → guest_codes_lock is the only nesting).
- Relay: bounded queues, `need_sync` resync design sound, unsubscribe in `finally`.
- Latency deques bounded; `rate_limited` table bounded.
- Lobby dict mutation consistently under `state_lock` (except SRV-3/11).
- JS intervals can't be killed by per-tick exceptions; logs capped at 500.

## Recommended action order (Snoop's call)
1. **SRV-1** (tunnel IP collapse) — before anything goes public today; also audit deployed V1
2. **WEB-1** (blank fallback) + **WEB-4** (restart bug) — video reliability today
3. **WEB-2/3** (runaway on disconnect/tab-away) — safety today
4. **SRV-2 + SRV-3** (watchdog death, race tick) — stability today
5. SRV-4/5/6 + WEB-9 — today if time
6. Everything else → backlog
