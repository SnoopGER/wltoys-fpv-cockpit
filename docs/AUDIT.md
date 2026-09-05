# FPV Cockpit Codebase Audit — V2 (Garden Kart) Groundwork

Audited: branch `v2-modern-cockpit`, September 05 2026.
Scope: `webapp.py` (1598 lines), `car_protocol.py` (589), `video_decoder.py` (173), `fpv_stream_bridge.py` (135), `templates/*.html`, `static/app.js` (1490), `static/mobile.js` (356), `static/style.css`, tests, Docker/scripts config.
Every line reference was read in this audit. Items not directly verifiable from code are marked *(unverified)*.

---

## 1. Route Table (webapp.py)

Auth tiers used below:
- **none** — no session check at all
- **user** — `require_user()` (webapp.py:537-541): any logged-in identity (Discord admin/driver/spectator **or** redeemed guest)
- **can_connect** — `require_can_connect()` (webapp.py:553-562): admin or guest with `can_connect`
- **admin** — `require_admin()` (webapp.py:544-550)
- **local** — `local_request()` (webapp.py:822-823): `remote_addr` in {127.0.0.1, ::1, localhost}

| Route | Method | Auth | Purpose | Ref |
|---|---|---|---|---|
| `/` | GET | none | Cockpit page (desktop `index.html` or UA-detected `mobile.html`); renders with `user=None` for anonymous | webapp.py:838-871, is_mobile 828-835 |
| `/car/<car_id>` | GET | none | Same cockpit bound to a specific car id | webapp.py:839-871 |
| `/admin/cars` | GET | admin **or local** | Split view: iframes of `/car/<id>?embed=1` per car | webapp.py:874-884; admin_cars.html:28-33 |
| `/static/<path>` | GET | none | Static files (JS/CSS) | webapp.py:887-889 |
| `/login` | GET | none | Starts Discord OAuth; stores `discord_oauth_state` + `discord_oauth_redirect_uri` in session | webapp.py:892-914 |
| `/auth/discord/callback` | GET | none (OAuth callback) | State check → code→token exchange → `/users/@me` → ban/allowlist gate → `session["user"]` | webapp.py:917-967 |
| `/logout` | GET | none | Clears session, deletes cookie, no-cache headers | webapp.py:970-979 |
| `/api/me` | GET | none | Current public user or null | webapp.py:984-986 |
| `/api/lobby` | GET | **none** | Full lobby snapshot: connected users, queue, **banned Discord IDs**, car states | webapp.py:989-991, lobby_snapshot 615-640 |
| `/api/cars` | GET | user | Snapshots of all configured cars + default id | webapp.py:994-999 |
| `/api/handshake` | POST | can_connect | Send wake/trigger handshake to one car or `"all"` (2x each) | webapp.py:1002-1014 |
| `/api/queue/join` | POST | user (driver role required, 1022) | Join lobby queue or become active driver | webapp.py:1017-1032 |
| `/api/queue/leave` | POST | user | Leave queue | webapp.py:1035-1043 |
| `/api/admin/<action>` | POST | admin | `emergency_stop`, `pause`, `resume`, `next_driver`, `kick_current_driver`, `kick_user`, `ban_user`, `unban_user`, `clear_queue`, `set_max_speed`, `set_session_duration` | webapp.py:1046-1097 |
| `/api/guest/generate` | POST | admin | Generate one-time drive code (1-120 min) | webapp.py:1100-1114 |
| `/api/guest/codes` | GET | admin | List all guest codes + status | webapp.py:1117-1123 |
| `/api/guest/revoke` | POST | admin | Deactivate a code | webapp.py:1126-1138 |
| `/api/guest/clear` | POST | admin | Delete used/expired (`mode=used`) or all non-persistent codes — **crashes on dormant codes, see §6** | webapp.py:1141-1163, bug 1158 |
| `/api/redeem-code` | POST | **none** | Guest redeems drive code → sets `session["user"]`, `session["is_guest"]`; writes plaintext code+IP+UA debug log | webapp.py:1166-1192 |
| `/api/guest/debug` | GET | local | Dump all codes incl. live ones + active guest sessions | webapp.py:1195-1202 |
| `/api/guest/debug-generate` | POST | local | Generate a 30-min code without admin auth | webapp.py:1205-1211 |
| `/api/guest/test` | GET | **none** | Returns `ZENGARDEN`/`ZENADMIN` persistent codes **and the full list of all known code strings** | webapp.py:1214-1224 |
| `/api/guest/toggle-persistent` | POST | admin | Enable/disable a persistent code | webapp.py:1227-1252 |
| `/api/guest/add-persistent` | POST | admin | Create/restore persistent code | webapp.py:1255-1288 |
| `/api/guest/remaining` | GET | user (self, guest) | Remaining drive seconds for current guest | webapp.py:1291-1303 |
| `/api/connect` | POST | can_connect | Connect car (handshake + heartbeat + UDP receiver) and start decoder | webapp.py:1306-1319 |
| `/api/disconnect` | POST | can_connect | Stop decoder + disconnect car | webapp.py:1322-1336 |
| `/api/status` | GET | **none** | Car connection state/stats for requested `?car=` | webapp.py:1339-1351 |
| `/api/logs` | GET | user | Drain car-side log queue | webapp.py:1354-1362 |
| `/api/command` | POST | **user only** | Motor command. **No active-driver or e-stop check server-side** (see §6.1) | webapp.py:1365-1374, handle_control_command 805-819 |
| `/api/lights` | POST | user + validate_control_user | Toggle lights; enforces active-driver (but broken for guests, see §6.4) | webapp.py:1377-1390, check 1383, validate 790-794 |
| `/api/send_raw` | POST | admin + local | Send arbitrary raw UDP hex to car IP on any port | webapp.py:1393-1417 |
| `/api/protocol` | GET | admin | Static protocol reference dump (header layout, ports, handshake hex, heartbeat, SPS/PPS) | webapp.py:1420-1445 |
| `/api/stream` | GET | user | MJPEG (`multipart/x-mixed-replace; boundary=frame`) from decoder's latest JPEG; admin per-sid pause/smart-5fps | webapp.py:1455-1494 |
| `/api/stream/pause` | POST | admin | Toggle admin stream pause for `car:sid` key | webapp.py:1497-1511 |
| `/api/stream/smart` | POST | admin | Toggle admin 5fps smart mode for `car:sid` key | webapp.py:1514-1528 |

Socket.IO events (flask_socketio): see §5.

---

## 2. Auth Flow Summary

### Discord OAuth (webapp.py:892-967)
1. `GET /login`: requires `DISCORD_CLIENT_ID` + `DISCORD_CLIENT_SECRET` (else 503, webapp.py:894-897). Generates `secrets.token_urlsafe(32)` state and stores state + chosen redirect_uri in the Flask session (webapp.py:902-904). Redirects to `https://discord.com/api/oauth2/authorize` with `scope=identify`, `prompt=none` (webapp.py:906-914).
2. Redirect-URI selection (`redirect_uri_for_request`, webapp.py:216-227): request host matched against allowlist from `DISCORD_REDIRECT_URIS` (comma list) / legacy `DISCORD_REDIRECT_URI` / derived from `PUBLIC_BASE_URL` (webapp.py:167-192); only URIs with path `/auth/discord/callback` accepted; unknown hosts always fall back to the public URI (webapp.py:195-213) — prevents arbitrary redirect targets.
3. Callback: constant-time state comparison via `secrets.compare_digest` (webapp.py:925), then POST `/oauth2/token` (webapp.py:933-944) and GET `/users/@me` (webapp.py:947-951). Both use `raise_for_status()` with no error handling → any Discord hiccup yields an unhandled 500.
4. Gate: banned → 403 + session clear (webapp.py:960-962); not allowlisted → 403 + session clear (webapp.py:963-965). Else `session["user"] = {id, username, display_name, avatar}` (webapp.py:954-966).

### Roles
- `ADMIN_DISCORD_IDS` → CSV set (webapp.py:291, 230-231) ⇒ role `admin` (role_for_user, webapp.py:402-405).
- `ALLOWED_DISCORD_IDS` → `csv_role_map` (webapp.py:234-248): plain ids default to `driver`; `id:spectator` supported (invalid roles coerced to driver). Access requires membership in one of the two sets (`is_allowed_user`, webapp.py:398-399) and not banned.
- Enforcement re-checked on **every** request via `current_user()` (webapp.py:366-392): revoking an id from env cuts access on next request (env read at import only — env changes need a restart *(unverified at runtime; static reading shows sets built once at webapp.py:291-293)*).

### Guest one-time codes
- **Generation** (webapp.py:416-439): `DRIVE-XXXX-XXXX` from a 31-char ambiguity-free alphabet using stdlib **`random`** (not `secrets`) — webapp.py:418-423. Entry: `{created, expires_at: None (dormant), duration, redeemed_by: None, active: True}`. Saved to `.guest_codes.json` (`GUEST_CODES_FILE`, webapp.py:75-91).
- **Redemption** (webapp.py:442-491): code uppercased, `active=False` marks one-time consumption (webapp.py:456); countdown starts at first redemption, not generation (webapp.py:463-465); persistent codes immediately re-activate (webapp.py:459-461). Builds a guest user `id="guest-<CODE>"`, role from entry (default driver), `can_connect` for admin/driver (webapp.py:471-485); registers `active_guest_sessions[guest_id] = {code, expires_at}` (webapp.py:487-490).
- **Session validity**: guest session revalidated against `active_guest_sessions` on every `current_user()` call; expired ⇒ `session.clear()` (webapp.py:375-383). `timer_loop` (webapp.py:774-787) calls `expire_guest_sessions()` (webapp.py:494-504) each second → expired guests get `kick_user`.
- **Persistence lifecycle**: `_save_guest_codes` writes only non-persistent entries (webapp.py:87); `_load_guest_codes` restores only entries that are `active` and unexpired at boot (webapp.py:94-104); `.guest_codes.json` is gitignored (`.gitignore`).
- **Hardcoded persistent codes**: `ZENGARDEN` (role driver) and `ZENADMIN` (role **admin**), active for 365 days, never consumed — webapp.py:108-126. Additionally `/api/guest/test` (webapp.py:1214-1224) serves both codes publicly.

### Ban / kick
- `kick_user` (webapp.py:729-747): for guests pops `active_guest_sessions` and revokes the underlying code (`active=False`, `redeemed_by="kicked"`); emits Socket.IO `kicked{reason}` to the user's sockets (client force-logs-out, app.js:466-472); removes from lobby/queue, disconnects sockets (webapp.py:704-727); if kicked user was active driver, `next_driver_locked` fires `send_neutral` first (webapp.py:673-684).
- `ban_user` (webapp.py:750-754): adds to `lobby["banned_ids"]`, removes from lobby, persists sorted ids to `.banned_discord_ids` mode 0600 (`save_banned_ids`, webapp.py:308-311). Ban checked in `current_user` (webapp.py:384-386) and at callback (webapp.py:960). Admins (`ADMIN_DISCORD_IDS`) cannot be banned via API (webapp.py:1078-1079). `unban_user` webapp.py:757-762.
- Note: ban file is loaded once at import into a `set` (webapp.py:324); edits to the file at disk level are not hot-reloaded.

### Session cookie handling
- `app.secret_key = SESSION_SECRET` else random per boot (webapp.py:33) — unset secret silently invalidates all sessions on restart.
- `HostAwareSessionInterface` (webapp.py:39-63): when request host == public redirect host → cookie `Secure=True`, `Domain=public host`; on LAN/localhost → host-only, non-Secure HTTP cookie. SameSite=`Lax` everywhere (webapp.py:34-36, 59-60) — sufficient for the top-level OAuth redirect and doubles as the (only) CSRF defense; no CSRF tokens exist anywhere.
- `/logout` explicitly deletes the cookie with the matching domain (webapp.py:970-979).

---

## 3. Multi-Car Architecture

- **Config**: `CAR_CONFIGS` for exactly `car1..car3` built at import from env with defaults (webapp.py:263-288): label, SSID, IP (all default `172.16.11.1`), `bind_ip` (empty = default interface), UDP `listen_port` (default 1234). `DEFAULT_CAR_ID` env (webapp.py:68). Duplicated/overwritten dict: an earlier empty `CAR_CONFIGS` at webapp.py:69.
- **Per-car runtime**: `car_slots[car_id] = {car: CarProtocol, decoder: VideoDecoder(640×360,q75), config}` created lazily by `init_car()` (webapp.py:333-349); each car gets its own UDP receive/heartbeat/command sockets and its own decoder thread. All cars initialized at startup (webapp.py:1579-1580).
- **Car selection**: every API takes `?car=` / body `car`, normalized by `normalize_car_id()` with fallback to default (webapp.py:328-330); frontend appends it via `apiUrl()` from `body[data-car-id]` (app.js:20-25). Page routes `/` and `/car/<car_id>` (webapp.py:838-842); header car switcher (index.html:47-54).
- **Split view**: `/admin/cars` (webapp.py:874-884) renders `admin_cars.html` with one `<iframe src="/car/<cid>?embed=1">` per car (admin_cars.html:28-33). **Nothing reads the `embed` parameter** — no template or JS branch exists (grep over templates/static: zero matches), so each iframe renders the full cockpit chrome. Each iframe independently polls `/api/status?car=…` and opens its own `/api/stream?car=…` MJPEG connection.
- **Aggregation**: `all_car_snapshots()` (webapp.py:352-363) embeds per-car status into every lobby snapshot; `/api/cars` (webapp.py:994-999). Handshake supports `car="all"` (webapp.py:1008-1013).
- Practical constraint: all three cars share IP `172.16.11.1`, so simultaneous control of more than one car requires distinct NICs and `bind_ip` per car (also implied by per-car static-IP scripts `set-car{1,2,3}-static-ip-admin.ps1`, `update-car-bind-ips.ps1`).

---

## 4. Video Pipeline

### Main path (webapp.py + car_protocol.py + video_decoder.py)
1. Car → UDP port 1234. `_receive_loop` (car_protocol.py:468-537): `recvfrom(65535)`, **drops packets not from `car_ip`** (car_protocol.py:484-485), 2 MB SO_RCVBUF (car_protocol.py:245).
2. `_parse_packet` (car_protocol.py:539-570): 32-byte header, magic `0x5aa56cc6` (car_protocol.py:31-32,544-546), LE fields frame_size/seq/timestamp/total_frags/frag_idx/data_offset/data_len; rejects `data_len` 0 or >1500 (car_protocol.py:556-557).
3. `FrameAssembler` (car_protocol.py:93-142): buffers fragments per seq, reassembles in frag order, **discards frame on any missing fragment**, caps pending at `max_pending=20` (oldest evicted).
4. Complete Annex-B frame → `on_frame` → `VideoDecoder.feed_frame` (webapp.py:346 wires it): codec sniff, keyframe gate, drop-oldest queue of 8 (`VIDEO_DECODE_QUEUE`), single decode thread converts via PyAV → PIL → JPEG (`JPEG_QUALITY` env override) and stores only the **latest JPEG** under a lock (video_decoder.py:47-123).
5. `/api/stream` (webapp.py:1455-1494): auth'd MJPEG generator polling `get_latest_jpeg()` every 5 ms, emits only when `frame_count` changed, boundary `frame`. Admin per-`sid` pause and 5 fps "smart" mode via unbounded dicts `admin_stream_paused/smart` (webapp.py:1451-1452, 1467-1488). Client binds it to an `<img>` tag (app.js:44-50; index.html:74-80). No frame is ever buffered client-side beyond the img.

### Codec auto-detection (H264/HEVC)
`video_decoder.py:125-147`: if `VIDEO_CODEC` env is `h264`/`hevc`, forced; otherwise scan Annex-B start codes (3- and 4-byte, video_decoder.py:149-159) and accept H264 on NAL types {1,5,7,8}, HEVC on {1,19,20,21,32,33,34}; falls back to last detected codec. Keyframe gate: nothing decodes before an IDR/SPS (H264 types 5/7; HEVC 19/20/21/32/33) — video_decoder.py:56-60,142-147. Any decode error resets the codec context and re-waits for a keyframe (video_decoder.py:112-123). Car's known stream: H.264 Constrained Baseline L3.1 640×360@20fps with known SPS+PPS (car_protocol.py:34-38).

### `fpv_stream_bridge.py` — the "no-auth" bridge
- Deliberately authless by design (docstring fpv_stream_bridge.py:2-7; docs/no-auth-stream-bridge.md) for VLC/OBS.
- **Exposed**: `GET /` text help (fpv_stream_bridge.py:59-69), `GET /stream.mjpg` + alias `/stream` — live FPV video (fpv_stream_bridge.py:72-97), `GET /status` — car state JSON incl. `car_ip`, ports, packet/frame stats, uptime (fpv_stream_bridge.py:100-107, car_protocol.py:429-452).
- **Binds `0.0.0.0:8080` by default** (`STREAM_BRIDGE_HOST`, fpv_stream_bridge.py:32-33) → every host on the LAN (and on the internet if the machine is exposed) can watch live video and read telemetry. Windows startup scripts (`start-fpv-stream-bridge.ps1`, `setup-stream-bridge-windows.ps1`) run it with these defaults.
- No control endpoints exist in the bridge, but it calls `car.connect()` unless `--no-connect` (fpv_stream_bridge.py:55-56), which starts the heartbeat thread — meaning the bridge keeps sending 2 Hz **neutral** UDP commands to the car (car_protocol.py:454-466). Two bridges (or bridge + webapp) binding UDP 1234 both succeed because `SO_REUSEADDR` is set (car_protocol.py:244) and split inbound packets unpredictably — a known footgun *(consequence verified from code; runtime behavior unverified)*.

---

## 5. WebSocket Usage

No raw WebSocket API; transport is **Socket.IO** (websocket + long-polling fallback) on the same Flask port 5555 via `flask_socketio`, `async_mode="threading"`, `manage_session=False` (webapp.py:64). Client loads socket.io from CDN in index.html:311; mobile.html:76 references a **local `/static/socket.io.min.js` that does not exist** in `static/` (404 — harmless only because mobile.js never calls `io()`).

Server events:
- `connect` (webapp.py:1533-1545): rejects sockets without a valid session (`return False`), registers `sid→user`, joins room `lobby`, sends snapshot + broadcast; starts the timer thread.
- `disconnect` (webapp.py:1548-1565): removes user when last sid drops; if active driver left → `next_driver_locked` ⇒ `send_neutral`.
- `control:command` (webapp.py:1568-1574): requires any logged-in user, then `handle_control_command` — **same missing active-driver/e-stop check as `/api/command`** (§6.1). Reply via `control:ack`.
Server→client broadcasts: `lobby:update` (every second while a driver session runs, webapp.py:774-787, 643-644), `kicked` (webapp.py:742-746).

Clients: desktop uses `volatile.emit` for 20 Hz control with HTTP POST fallback (app.js:192-206), renders lobby from `lobby:update` (app.js:452-473). Mobile does **not** use sockets at all — no lobby, no queue presence (mobile.js:326-346).

---

## 6. Code-Quality & Security Findings (must NOT be replicated in V2)

### 6.1 CRITICAL — motor control has no server-side driver authorization
`/api/command` checks only `require_user()` (webapp.py:1365-1374); `handle_control_command` (webapp.py:805-819) validates command name and clamps speed, but never checks the caller is the **active driver** (or even an admin). The Socket.IO path is identical (webapp.py:1568-1574). Any logged-in identity — a `spectator`, any redeemed guest, any allowlisted Discord account not in the queue — can drive **any** car by POSTing `/api/command` or emitting `control:command`. The gating (`canControl`, active-driver match) exists **only in the browser** (app.js:14, 245, 447-449, 994-1001). `/api/lights` proves the intended check was `validate_control_user` (webapp.py:1383) — it was simply never applied to the drive path.

### 6.2 CRITICAL — emergency stop is not enforced
`emergency_stop` is set by the admin action (webapp.py:1053-1055) and fires one `send_neutral` burst; it is **never checked** in the control path, so a driver's still-running 20 Hz client loop (app.js:176-178; mobile.js:151-154) immediately overrides E-STOP. `lobby["emergency_stop"]` is otherwise read nowhere except the snapshot (webapp.py:629) — verified by grep: only webapp.py:315, 629, 670, 1054, 1066.
Related: the heartbeat thread re-sends `_last_motor_cmd` forever (car_protocol.py:173, 391, 454-466). If the active driver's browser dies *without* a Socket.IO disconnect being processed (e.g. mobile clients never connect a socket, §5), the **server keeps reinforcing the last non-neutral command indefinitely** — there is no client-silence watchdog. The only neutralization paths are explicit `send_neutral` calls (webapp.py:647-658) triggered by socket disconnect/queue/admin actions. (The car itself is reported to fail to neutral when UDP stops — car_protocol.py:324-326 — but that is car-side behavior *(unverified here)* and the heartbeat keeps UDP alive.)

### 6.3 HIGH — information disclosure without authentication
- `/api/guest/test` (webapp.py:1214-1224): publicly returns the persistent codes `ZENGARDEN`/`ZENADMIN` **and every currently-known code string** — instant admin from the internet.
- `/api/lobby` (webapp.py:989-991): anonymous full lobby snapshot including all connected usernames/IDs and the **banned-ID list** (webapp.py:618-619, 632-635).
- `/api/status` (webapp.py:1339-1351): anonymous car state/IP/stats.
- Redeem debug log writes every attempt's **plaintext code, client IP, UA** to `data/redeem-debug.log` (webapp.py:133-136, 1174-1191).

### 6.4 HIGH — hardcoded backdoor codes & inconsistent control validation
Persistent `ZENGARDEN`/`ZENADMIN` are seeded in source (webapp.py:108-126) and re-created on every boot. Separately, `validate_control_user` (webapp.py:790-794) calls `role_for_user(user_id)` which returns `None` for guest IDs (webapp.py:402-405) — so a **guest active driver fails the check for `/api/lights`** while the very same guest can drive freely through `/api/command`. The two paths disagree on who may act.

### 6.5 HIGH — weak code generation, no rate limiting, no CSRF tokens
- Guest codes generated with stdlib `random` (Mersenne, not CSPRNG) (webapp.py:418-423) despite being bearer credentials.
- `/api/redeem-code` (webapp.py:1166-1192) has no rate limit, no lockout; same for login/guessing in general.
- All state-changing POSTs rely solely on SameSite=Lax (webapp.py:36); no CSRF tokens; Socket.IO has its own transport but also no origin pinning configured.

### 6.6 MEDIUM — confirmed crash bug in `/api/guest/clear`
Line 1158: `not e["active"] or time.time() > e["expires_at"] and not e.get("persistent")` raises `TypeError: '>' not supported between instances of 'float' and 'NoneType'` for any dormant (generated-but-never-redeemed) code whose `expires_at` is `None` (set at webapp.py:432). **Reproduced live** in this audit against the installed code → the endpoint 500s whenever a dorm ant code exists.

### 6.7 MEDIUM — thread-safety gaps
- `init_car` creates slots lazily with **no lock** (webapp.py:333-349): concurrent first-requests for the same car can build two `CarProtocol`s and double-bind UDP 1234 (SO_REUSEADDR, car_protocol.py:244).
- `expire_guest_sessions` → `kick_user` → `remove_user_from_lobby` mutates `lobby` **without `state_lock`** when called from the timer thread (webapp.py:503-504 vs. lock discipline elsewhere, webapp.py:1024, 1052, 1539, 1551).
- `admin_stream_paused/smart` are unbounded dicts keyed by a **client-supplied** `sid` string (webapp.py:1451-1452, 1465, 1508, 1525): unbounded memory growth per unique sid, and an admin who guesses another admin's sid can pause/throttle their stream.
- `_save_guest_codes`/`save_banned_ids` do file I/O while holding locks (webapp.py:83-91 under `guest_codes_lock`; webapp.py:310 called under `state_lock` via ban path, webapp.py:1080).
- `_frame_count` incremented without the decoder lock (video_decoder.py:108) while readers use it (webapp.py:1477).

### 6.8 MEDIUM — error handling gaps
- OAuth token/user calls `raise_for_status()` uncaught → raw 500 on Discord failure (webapp.py:933-952).
- Most `except Exception: pass` swallows failures silently (guest code save/load webapp.py:90-91,103-104; decoder errors video_decoder.py:119-123; kick socket errors webapp.py:707-709,745-746).
- `/api/command` maps **every** failure (including "not connected", car_protocol.py:328-330) to HTTP 403 (webapp.py:1373), conflating auth and transport errors.
- `subprocess` ping spawned inside `connect()` (car_protocol.py:207-223) — blocks the request thread up to 5 s per connect.

### 6.9 MEDIUM — deployment / hygiene
- Serves on `werkzeug` dev server with `allow_unsafe_werkzeug=True`, host `0.0.0.0` (webapp.py:1598); docker-compose uses host networking (docker-compose.yml) — any LAN device reaches 5555 and the unauth bridge on 8080.
- Committed junk: `webapp.py.bak`, `static/app.js.bak`, `CAR.pcap`, `webapp.py.bak` at repo root.
- `/admin/cars` grants access to any **local** request without login (webapp.py:877) — a local process or SSRF via 127.0.0.1 reaches the admin split view.
- UA-sniffing for the mobile template (webapp.py:828-835).
- app.js `pollGamepad`: **CORRECTED 2026-09-05 (was misdiagnosed as removable duplication)** — the first "Apply input or stop" copy (800-844) is NOT dead: brace analysis shows it is the open of the single-device `else` branch, and the second copy (848-890) is the only apply path for `_combined` mode. Both execute; removing either breaks a mode. Net effect today: single-device input is applied twice per 50ms poll (benign only because `startMotor` dedupes). Refactor ONLY with a controller-on hardware test — do not blind-delete.
- mobile.js hardwires no `car` field → mobile always talks to `DEFAULT_CAR_ID` (mobile.js:121-128 + normalize at webapp.py:328-330); mobile users are invisible to the lobby (no socket).
- MJPEG generator duplicated between webapp.py:1467-1488 and fpv_stream_bridge.py:75-87; both poll with 5 ms sleeps instead of event signalling.
- `filterMap` typo: `'ERROR'/'ERR' → 'logFilterError'` but the DOM id is `logFilterErr` (app.js:1051 vs index.html:265) — the ERROR filter checkbox doesn't gate at add-time (works only via `filterLogs`, app.js:1088-1098).

### 6.10 Tests
`test_lobby_admin.py` + `test_oauth_redirects.py` **run and pass (12/12, OK)** once the repo's own `requirements.txt` is installed (verified in `.venv`; on a bare python they fail at import — flask/dotenv missing, and neither file is in a CI config here *(unverified: no CI config found)*).
Coverage: driver-timer advance semantics incl. "expired driver stays active when queue empty" (test_lobby_admin.py:38-60), ban/unban removes driver + advances queue (76-92), persistent code redemption roles (95-110), OAuth redirect-URI selection per host incl. unknown-host fallback (test_oauth_redirects.py:32-50), bad-state rejection before token exchange (52-61), host-aware cookie flags (64-75).
**No tests exist** for: `/api/command` authorization (the §6.1 hole), guest code lifecycle beyond redemption, video path, bridge, or the multi-car routes.

---

## 7. Assets Worth Keeping in V2

Protocol correctness (car_protocol.py — the crown jewels, PCAP-derived):
1. 32-byte video header layout + magic `0x5aa56cc6` + sane bounds (`data_len ≤ 1500`) — car_protocol.py:5-15, 31-32, 539-570; also documented in-page (index.html:278-308) and `/api/protocol` (webapp.py:1420-1445).
2. Control packet framing `ca 47 d5 00 00 00 00 00 66 [STR] [THR] 80 00 00 [CHK] 99` with checksum **B14 = B9 ^ B10 ^ 0x80**, "verified against 402 captured packets including 33 dual-axis commands" — car_protocol.py:309-395 (checksum 381-383).
3. Direction mapping (byte 9 steering / byte 10 throttle, 0x80 center/neutral; HIGH=forward/right, LOW=reverse/left) with known-good reference values — car_protocol.py:51-54, 336-380.
4. Handshake: wake + trigger packets to UDP 23459, repeated (2-3x, 0.2/0.3 s spacing) — car_protocol.py:41-42, 261-278.
5. Neutral heartbeat packet and the 0.5 s heartbeat loop on 23458 — car_protocol.py:45, 454-466 (fix the last-cmd reinforcement per §6.2, keep the cadence).
6. Car-side failsafe documented by capture: continuous 20 Hz commands required; single packets decay to neutral; the app ramps smoothly — car_protocol.py:324-326, mirrored by the 50 ms client loop (app.js:164-190) and 100 ms mobile loop (mobile.js:119-159).
7. `FrameAssembler` fragment logic (in-order rebuild, drop-on-hole, bounded pending) — car_protocol.py:93-142; source-IP filter on video (car_protocol.py:484-485).
8. Known SPS/PPS for the 640×360 stream — car_protocol.py:34-38.
9. Keyframe-gated decode + codec auto-sniffing + reset-on-error (works for future HEVC cars) — video_decoder.py:56-60, 112-147.
10. Light-toggle attempt is explicitly documented as *unconfirmed* reverse-engineering — keep that honesty marker — car_protocol.py:397-420.

Safety & lobby behaviors worth preserving (with the §6.1/6.2 fixes applied server-side):
11. `send_neutral` fired on driver change, kick, ban, disconnect, pause, E-STOP — webapp.py:647-658, 673-684, 1053-1060; active-driver-disconnect → next driver (webapp.py:1548-1565).
12. Timer semantics "expired driver stays active if nobody is queued" + "advance only when queued driver available" — webapp.py:694-701, locked down by tests (test_lobby_admin.py:38-60).
13. Server-side speed clamp `speed = min(speed, lobby max_speed_percent)` — webapp.py:813-816 (default cap 70%, webapp.py:295).
14. Guest code one-time lifecycle w/ countdown-from-redeem, revocation on kick, expiry sweep — webapp.py:442-504 (drop the persistent backdoors).
15. OAuth state check + host-locked redirect allowlist + host-aware cookie scheme (LAN HTTP vs public HTTPS dual-origin works and is unit-tested) — webapp.py:39-63, 167-227, 925; test_oauth_redirects.py.
16. Ban persistence to a 0600 file, admin-ban-immunity — webapp.py:302-311, 1078-1079.
17. Gamepad abstraction: profile auto-detect (Xbox/PlayStation/Moza wheel/pedals/combined), circular+1D deadzones, edge-detected buttons incl. A=e-stop, space=hard stop — app.js:512-961, 1003-1033; genuinely reusable for Garden Kart.
18. Volatile socket emits with HTTP fallback for the control hot path — app.js:192-206.

---

## 8. V2 Verdict Summary

Keep the protocol layer (car_protocol.py) and the gamepad/lobby logic nearly as-is (modularized). Rewrite the HTTP layer with: uniform auth middleware, server-side active-driver + e-stop enforcement on every control path, CSPRNG codes, no public code leaks, no hardcoded codes, no unauth bridge on 0.0.0.0 (or bind localhost + explicit opt-in), rate limiting, and an `embed` mode that actually exists. Tests today cover auth selection and lobby timers but not the drive path — add authorization tests for `/api/command` as V2's first regression suite.
