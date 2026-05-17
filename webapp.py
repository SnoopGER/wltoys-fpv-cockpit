#!/usr/bin/env python3
"""
WLtoys FPV Car - Debug Cockpit Web Server
Flask app providing REST API + MJPEG video stream + lobby auth/control.
"""

import os
import sys
import time
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask.sessions import SecureCookieSessionInterface
from flask_socketio import SocketIO, emit, join_room, leave_room

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from car_protocol import CarProtocol, ConnectionState, SPS_PPS
from car_protocol import HANDSHAKE_WAKE, HANDSHAKE_TRIGGER, HEARTBEAT
from video_decoder import VideoDecoder

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SESSION_SECRET", os.urandom(32))
# SameSite=Lax is enough for Discord's top-level OAuth callback redirect and avoids
# the cross-site cookie behavior that SameSite=None would require.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


class HostAwareSessionInterface(SecureCookieSessionInterface):
    """Use secure public cookies without breaking HTTP LAN sessions.

    The dashboard serves two legitimate origins from one Flask process:
    - https://race.zen-rc.net through Cloudflare (public route)
    - http://192.168.178.142:5555 or localhost on the LAN (low-latency route)

    A single global Secure=true/Domain=race.zen-rc.net cookie would prevent LAN HTTP
    login from working. Keeping Domain unset for LAN creates a host-only cookie for
    the IP/localhost origin, while the public host still gets Secure=true.
    """

    def get_cookie_secure(self, app):
        return request_host() == public_redirect_host()

    def get_cookie_domain(self, app):
        if request_host() == public_redirect_host():
            return public_redirect_host()
        return None

    def get_cookie_samesite(self, app):
        return "Lax"


app.session_interface = HostAwareSessionInterface()
socketio = SocketIO(app, async_mode="threading", manage_session=False)

# Global State

car = None
decoder = None
state_lock = threading.RLock()
timer_started = False

DISCORD_API = "https://discord.com/api"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://race.zen-rc.net").rstrip("/")
COMMANDS = {
    "stop",
    "forward",
    "reverse",
    "left",
    "right",
    "forward_left",
    "forward_right",
    "reverse_left",
    "reverse_right",
}


def request_host():
    """Return the current request host in lower-case, including port if present."""
    return request.host.lower()


def uri_host(uri):
    """Return host[:port] from an absolute URI."""
    return urlparse(uri).netloc.lower()


def redirect_uri_from_base_url(base_url):
    return f"{base_url.rstrip('/')}/auth/discord/callback"


def configured_redirect_uris():
    """Return all Discord callback URIs allowed by local config.

    DISCORD_REDIRECT_URIS is the preferred comma-separated list. The older
    DISCORD_REDIRECT_URI remains supported and is included for backwards
    compatibility, so existing single-route deployments keep working.
    """
    candidates = []
    candidates.extend(item.strip() for item in os.environ.get("DISCORD_REDIRECT_URIS", "").split(","))
    single_uri = os.environ.get("DISCORD_REDIRECT_URI", "").strip()
    if single_uri:
        candidates.append(single_uri)
    if not candidates:
        candidates.append(redirect_uri_from_base_url(PUBLIC_BASE_URL))

    seen = set()
    uris = []
    for uri in candidates:
        if not uri or uri in seen:
            continue
        parsed = urlparse(uri)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path != "/auth/discord/callback":
            continue
        seen.add(uri)
        uris.append(uri)
    return uris


def public_redirect_uri():
    """Return the safe public Cloudflare redirect URI used as fallback."""
    explicit = os.environ.get("DISCORD_PUBLIC_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    legacy = os.environ.get("DISCORD_REDIRECT_URI", "").strip()
    if legacy and uri_host(legacy) == uri_host(redirect_uri_from_base_url(PUBLIC_BASE_URL)):
        return legacy
    for uri in configured_redirect_uris():
        if urlparse(uri).scheme == "https" and uri_host(uri) == uri_host(redirect_uri_from_base_url(PUBLIC_BASE_URL)):
            return uri
    for uri in configured_redirect_uris():
        if urlparse(uri).scheme == "https":
            return uri
    return redirect_uri_from_base_url(PUBLIC_BASE_URL)


def public_redirect_host():
    return uri_host(public_redirect_uri())


def redirect_uri_for_request():
    """Select the Discord callback URI for the origin that started login.

    Known LAN hosts keep the OAuth round-trip on LAN. Unknown hosts always fall
    back to the configured public Cloudflare URI, which avoids opening arbitrary
    redirect targets and keeps the public route locked to Discord's allowlist.
    """
    host = request_host()
    for uri in configured_redirect_uris():
        if uri_host(uri) == host:
            return uri
    return public_redirect_uri()


def csv_ids(name):
    return {item.strip() for item in os.environ.get(name, "").split(",") if item.strip()}


def csv_role_map(name):
    roles = {}
    for item in os.environ.get(name, "").split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            user_id, role = item.split(":", 1)
            role = role.strip().lower()
            if role not in {"driver", "spectator"}:
                role = "driver"
            roles[user_id.strip()] = role
        else:
            roles[item] = "driver"
    return roles


def env_int(name, default, min_value=None, max_value=None):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


ADMIN_IDS = csv_ids("ADMIN_DISCORD_IDS")
ALLOWED_ROLES = csv_role_map("ALLOWED_DISCORD_IDS")
ALLOWED_IDS = set(ALLOWED_ROLES)
DEFAULT_DRIVE_SECONDS = env_int("DEFAULT_DRIVE_SECONDS", 120, 15, 3600)
MAX_REMOTE_SPEED_PERCENT = env_int("MAX_REMOTE_SPEED_PERCENT", 70, 5, 100)
BANNED_IDS_FILE = Path(os.environ.get(
    "BANNED_DISCORD_IDS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".banned_discord_ids"),
))


def load_banned_ids():
    if not BANNED_IDS_FILE.exists():
        return set()
    return {line.strip() for line in BANNED_IDS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_banned_ids():
    ids = sorted(lobby.get("banned_ids", set()))
    BANNED_IDS_FILE.write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")
    BANNED_IDS_FILE.chmod(0o600)

lobby = {
    "paused": False,
    "emergency_stop": False,
    "max_speed_percent": MAX_REMOTE_SPEED_PERCENT,
    "session_duration": DEFAULT_DRIVE_SECONDS,
    "active_driver": None,
    "driver_started_at": None,
    "queue": [],
    "users": {},
    "sid_to_user": {},
    "user_sids": {},
    "banned_ids": load_banned_ids(),
}


def init_car():
    global car, decoder
    if car is None:
        car = CarProtocol(
            car_ip=os.environ.get("FPV_CAR_IP", "172.16.11.1"),
            listen_port=int(os.environ.get("FPV_LISTEN_PORT", "1234")),
            on_log=lambda level, msg: None,
        )
    if decoder is None:
        decoder = VideoDecoder(width=640, height=360, quality=75)

    car.on_frame = lambda data: decoder.feed_frame(data)


def current_user():
    try:
        user = session.get("user")
    except RuntimeError:
        # Outside request context (e.g. timer_loop thread) — no session available
        return None
    if not user:
        return None
    if is_banned_user(user["id"]):
        session.clear()
        return None
    if not is_allowed_user(user["id"]):
        return None
    user = dict(user)
    user["role"] = role_for_user(user["id"])
    return user


def is_banned_user(user_id):
    return user_id in lobby.get("banned_ids", set())


def is_allowed_user(user_id):
    return not is_banned_user(user_id) and (user_id in ADMIN_IDS or user_id in ALLOWED_ROLES)


def role_for_user(user_id):
    if user_id in ADMIN_IDS:
        return "admin"
    return ALLOWED_ROLES.get(user_id)


def can_drive(user):
    return bool(user and user["role"] in {"admin", "driver"})


def is_admin(user):
    return bool(user and user["role"] == "admin")


def require_user():
    user = current_user()
    if not user:
        return None, (jsonify({"ok": False, "error": "login_required"}), 401)
    return user, None


def require_admin():
    user, error = require_user()
    if error:
        return None, error
    if not is_admin(user):
        return None, (jsonify({"ok": False, "error": "admin_required"}), 403)
    return user, None


def public_user(user):
    if not user:
        return None
    user_id = user["id"]
    return {
        "id": user_id,
        "username": user.get("username", "unknown"),
        "display_name": user.get("display_name") or user.get("username", "unknown"),
        "avatar": user.get("avatar"),
        "role": role_for_user(user_id),
        "connections": len(lobby.get("user_sids", {}).get(user_id, set())),
        "banned": is_banned_user(user_id),
        "active": lobby.get("active_driver") == user_id,
        "queued": user_id in lobby.get("queue", []),
    }


def seconds_remaining(now=None):
    now = now or time.time()
    active = lobby["active_driver"]
    if not active or not lobby["driver_started_at"]:
        return 0
    if lobby["paused"]:
        return max(0, int(lobby.get("paused_remaining", lobby["session_duration"])))
    elapsed = now - lobby["driver_started_at"]
    return max(0, int(lobby["session_duration"] - elapsed))


def car_online():
    if not car:
        return False
    return car.state in {ConnectionState.CONNECTED, ConnectionState.STREAMING}


def lobby_snapshot():
    init_car()
    with state_lock:
        users = [public_user(u) for u in lobby["users"].values()]
        banned = sorted(lobby.get("banned_ids", set()))
        active_id = lobby["active_driver"]
        try:
            me = public_user(current_user())
        except Exception:
            me = None
        return {
            "ok": True,
            "me": me,
            "paused": lobby["paused"],
            "emergency_stop": lobby["emergency_stop"],
            "active_driver": public_user(lobby["users"].get(active_id)) if active_id else None,
            "remaining_drive_time": seconds_remaining(),
            "queue": [public_user(lobby["users"].get(uid, {"id": uid, "username": uid})) for uid in lobby["queue"]],
            "connected_spectators": [u for u in users if u and u["role"] == "spectator"],
            "connected_users": users,
            "banned_users": banned,
            "car_online": car_online(),
            "max_speed_percent": lobby["max_speed_percent"],
            "session_duration": lobby["session_duration"],
        }


def broadcast_lobby():
    socketio.emit("lobby:update", lobby_snapshot(), room="lobby")


def send_neutral(reason):
    init_car()
    try:
        car.send_command("stop", speed=0, steer_range=0)
        if hasattr(car, "log"):
            car.log("SAFETY", f"Neutral stop: {reason}")
    except Exception as exc:
        if car and hasattr(car, "log"):
            car.log("ERROR", f"Neutral stop failed: {exc}")


def remove_from_queue(user_id):
    lobby["queue"] = [uid for uid in lobby["queue"] if uid != user_id]


def start_driver(user_id):
    remove_from_queue(user_id)
    lobby["active_driver"] = user_id
    lobby["driver_started_at"] = time.time()
    lobby.pop("paused_remaining", None)
    lobby["emergency_stop"] = False


def next_driver_locked(reason):
    if lobby["active_driver"]:
        send_neutral(reason)
    lobby["active_driver"] = None
    lobby["driver_started_at"] = None
    lobby.pop("paused_remaining", None)
    while lobby["queue"]:
        uid = lobby["queue"].pop(0)
        user = lobby["users"].get(uid)
        if user and can_drive(user) and not is_banned_user(uid):
            start_driver(uid)
            break


def queued_driver_available():
    return any(
        uid in lobby["users"] and can_drive(lobby["users"][uid]) and not is_banned_user(uid)
        for uid in lobby["queue"]
    )


def advance_driver_if_due_locked(reason):
    """Advance only when the active driver's timer expired and another driver is queued."""
    if not lobby["active_driver"] or lobby["paused"] or seconds_remaining() > 0:
        return False
    if not queued_driver_available():
        return False
    next_driver_locked(reason)
    return True


def disconnect_user_sockets(user_id):
    for sid in list(lobby.get("user_sids", {}).get(user_id, set())):
        try:
            socketio.server.disconnect(sid, namespace="/")
        except Exception:
            pass


def remove_user_from_lobby(user_id, reason, disconnect_sockets=True):
    changed = False
    if disconnect_sockets:
        disconnect_user_sockets(user_id)
    remove_from_queue(user_id)
    if user_id in lobby["users"]:
        lobby["users"].pop(user_id, None)
        changed = True
    for sid in list(lobby.get("user_sids", {}).get(user_id, set())):
        lobby["sid_to_user"].pop(sid, None)
    lobby["user_sids"].pop(user_id, None)
    if lobby["active_driver"] == user_id:
        next_driver_locked(reason)
        changed = True
    return changed


def kick_user(user_id, reason="admin kick"):
    return remove_user_from_lobby(user_id, reason, disconnect_sockets=True)


def ban_user(user_id, reason="admin ban"):
    lobby.setdefault("banned_ids", set()).add(user_id)
    remove_user_from_lobby(user_id, reason, disconnect_sockets=True)
    save_banned_ids()
    return True


def unban_user(user_id):
    if user_id not in lobby.get("banned_ids", set()):
        return False
    lobby["banned_ids"].discard(user_id)
    save_banned_ids()
    return True


def ensure_timer():
    global timer_started
    if timer_started:
        return
    timer_started = True
    thread = threading.Thread(target=timer_loop, daemon=True)
    thread.start()


def timer_loop():
    while True:
        time.sleep(1)
        changed = False
        should_broadcast = False
        with state_lock:
            if lobby["active_driver"] and not lobby["paused"]:
                changed = advance_driver_if_due_locked("driver timer expired")
                # Broadcast every second while a session is active so clients see the countdown.
                should_broadcast = True
        if changed or should_broadcast:
            broadcast_lobby()


def validate_control_user(user):
    active = lobby["active_driver"]
    if is_admin(user):
        return True
    return bool(active and user["id"] == active and role_for_user(user["id"]) == "driver")


def clamp_int(value, default, min_value, max_value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def handle_control_command(user, data):
    init_car()
    with state_lock:
        cmd = data.get("command", "stop")
        if cmd not in COMMANDS:
            return {"ok": False, "error": "invalid_command"}

        max_speed = clamp_int(lobby.get("max_speed_percent", 100), MAX_REMOTE_SPEED_PERCENT, 5, 100)
        speed = clamp_int(data.get("speed", 100), 100, 0, 100)
        steer_range = clamp_int(data.get("steer_range", 100), 100, 0, 100)
        speed = min(speed, max_speed)

    success = car.send_command(cmd, speed=speed, steer_range=steer_range)
    return {"ok": success, "command": cmd, "speed": speed, "steer_range": steer_range}


def local_request():
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


# HTML/Auth Routes

@app.route("/")
def index():
    user = current_user()
    return render_template(
        "index.html",
        user=public_user(user),
        discord_configured=bool(os.environ.get("DISCORD_CLIENT_ID") and os.environ.get("DISCORD_CLIENT_SECRET")),
        is_local=local_request(),
    )


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


@app.route("/login")
def login():
    client_id = os.environ.get("DISCORD_CLIENT_ID")
    redirect_uri = redirect_uri_for_request()
    if not client_id or not os.environ.get("DISCORD_CLIENT_SECRET") or not redirect_uri:
        return "Discord OAuth is not configured. Set DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, and DISCORD_REDIRECT_URI(S).", 503

    # Store both values in the Flask session so the callback can prove it belongs
    # to this browser and can exchange the code with the exact redirect_uri that
    # Discord saw during /login. This keeps LAN and Cloudflare sessions separate.
    oauth_state = secrets.token_urlsafe(32)
    session["discord_oauth_state"] = oauth_state
    session["discord_oauth_redirect_uri"] = redirect_uri

    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "prompt": "none",
        "state": oauth_state,
    })
    return redirect(f"{DISCORD_API}/oauth2/authorize?{params}")


@app.route("/auth/discord/callback")
def discord_callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))

    expected_state = session.pop("discord_oauth_state", None)
    received_state = request.args.get("state")
    if not expected_state or not received_state or not secrets.compare_digest(expected_state, received_state):
        session.pop("discord_oauth_redirect_uri", None)
        return "Invalid Discord OAuth state.", 400

    redirect_uri = session.pop("discord_oauth_redirect_uri", None) or redirect_uri_for_request()
    if redirect_uri not in configured_redirect_uris():
        redirect_uri = public_redirect_uri()

    token_resp = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "client_id": os.environ.get("DISCORD_CLIENT_ID"),
            "client_secret": os.environ.get("DISCORD_CLIENT_SECRET"),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]
    user_resp = requests.get(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    user_resp.raise_for_status()
    discord_user = user_resp.json()
    user = {
        "id": discord_user["id"],
        "username": discord_user.get("username", "unknown"),
        "display_name": discord_user.get("global_name") or discord_user.get("username", "unknown"),
        "avatar": discord_user.get("avatar"),
    }
    if is_banned_user(user["id"]):
        session.clear()
        return "This Discord account is banned from this cockpit.", 403
    if not is_allowed_user(user["id"]):
        session.clear()
        return "This Discord account is not allowlisted for this cockpit.", 403
    session["user"] = user
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# API Routes

@app.route("/api/me")
def api_me():
    return jsonify({"ok": True, "user": public_user(current_user())})


@app.route("/api/lobby")
def api_lobby():
    return jsonify(lobby_snapshot())


@app.route("/api/queue/join", methods=["POST"])
def api_queue_join():
    user, error = require_user()
    if error:
        return error
    if not can_drive(user):
        return jsonify({"ok": False, "error": "driver_role_required"}), 403
    with state_lock:
        lobby["users"][user["id"]] = user
        if not lobby["active_driver"]:
            start_driver(user["id"])
        elif user["id"] != lobby["active_driver"] and user["id"] not in lobby["queue"]:
            lobby["queue"].append(user["id"])
            advance_driver_if_due_locked("queued driver joined after timer expired")
    broadcast_lobby()
    return jsonify(lobby_snapshot())


@app.route("/api/queue/leave", methods=["POST"])
def api_queue_leave():
    user, error = require_user()
    if error:
        return error
    with state_lock:
        remove_from_queue(user["id"])
    broadcast_lobby()
    return jsonify(lobby_snapshot())


@app.route("/api/admin/<action>", methods=["POST"])
def api_admin(action):
    user, error = require_admin()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    with state_lock:
        if action == "emergency_stop":
            lobby["emergency_stop"] = True
            send_neutral("admin emergency stop")
        elif action == "pause":
            if not lobby["paused"]:
                lobby["paused_remaining"] = seconds_remaining()
                lobby["paused"] = True
                send_neutral("race paused")
        elif action == "resume":
            if lobby["paused"]:
                remaining = lobby.pop("paused_remaining", lobby["session_duration"])
                lobby["driver_started_at"] = time.time() - max(0, lobby["session_duration"] - remaining)
                lobby["paused"] = False
                lobby["emergency_stop"] = False
        elif action in {"next_driver", "kick_current_driver"}:
            next_driver_locked(f"admin {action}")
        elif action == "kick_user":
            target_id = str(data.get("user_id", "")).strip()
            if not target_id:
                return jsonify({"ok": False, "error": "missing_user_id"}), 400
            kick_user(target_id, "admin kick")
        elif action == "ban_user":
            target_id = str(data.get("user_id", "")).strip()
            if not target_id:
                return jsonify({"ok": False, "error": "missing_user_id"}), 400
            if target_id in ADMIN_IDS:
                return jsonify({"ok": False, "error": "cannot_ban_admin"}), 403
            ban_user(target_id, "admin ban")
        elif action == "unban_user":
            target_id = str(data.get("user_id", "")).strip()
            if not target_id:
                return jsonify({"ok": False, "error": "missing_user_id"}), 400
            unban_user(target_id)
        elif action == "clear_queue":
            lobby["queue"] = []
        elif action == "set_max_speed":
            lobby["max_speed_percent"] = clamp_int(data.get("value"), MAX_REMOTE_SPEED_PERCENT, 5, 100)
        elif action == "set_session_duration":
            lobby["session_duration"] = clamp_int(data.get("value"), DEFAULT_DRIVE_SECONDS, 15, 3600)
            if lobby["active_driver"]:
                lobby["driver_started_at"] = time.time()
        else:
            return jsonify({"ok": False, "error": "unknown_admin_action"}), 404
    broadcast_lobby()
    return jsonify(lobby_snapshot())


@app.route("/api/connect", methods=["POST"])
def api_connect():
    user, error = require_admin()
    if error:
        return error
    init_car()
    success = car.connect()
    if success:
        decoder.start()
    broadcast_lobby()
    return jsonify({"ok": success, "state": car.state.value})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    user, error = require_admin()
    if error:
        return error
    global car, decoder
    if decoder:
        decoder.stop()
    if car:
        car.disconnect()
    broadcast_lobby()
    return jsonify({"ok": True, "state": "disconnected"})


@app.route("/api/status")
def api_status():
    init_car()
    status = car.get_status()
    status["decoder_frames"] = decoder.frame_count if decoder else 0
    return jsonify(status)


@app.route("/api/logs")
def api_logs():
    user, error = require_user()
    if error:
        return error
    init_car()
    logs = car.get_logs()
    return jsonify({"logs": logs})


@app.route("/api/command", methods=["POST"])
def api_command():
    user, error = require_user()
    if error:
        return error
    result = handle_control_command(user, request.get_json(silent=True) or {})
    status = 200 if result.get("ok") else 403
    return jsonify(result), status


@app.route("/api/lights", methods=["POST"])
def api_lights():
    user, error = require_user()
    if error:
        return error
    with state_lock:
        if not validate_control_user(user):
            return jsonify({"ok": False, "error": "not_active_driver"}), 403
    init_car()
    data = request.get_json(silent=True) or {}
    on = bool(data.get("on", True))
    success = car.toggle_lights(on=on)
    return jsonify({"ok": success, "lights": on})


@app.route("/api/send_raw", methods=["POST"])
def api_send_raw():
    user, error = require_admin()
    if error:
        return error
    if not local_request():
        return jsonify({"ok": False, "error": "raw_sender_local_only"}), 403
    init_car()
    data = request.get_json(silent=True) or {}
    hex_data = data.get("hex", "")
    port = data.get("port", 23458)

    try:
        import socket
        raw = bytes.fromhex(hex_data.replace(" ", "").replace(":", ""))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(raw, (car.car_ip, port))
        sock.close()
        car.log("TX", f"Raw {len(raw)}B -> {car.car_ip}:{port} [{hex_data[:40]}...]")
        return jsonify({"ok": True, "sent": len(raw)})
    except Exception as e:
        car.log("ERROR", f"Raw send failed: {e}")
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/protocol")
def api_protocol():
    user, error = require_admin()
    if error:
        return error
    return jsonify({
        "header": {
            "size": 32,
            "fields": [
                {"offset": 0, "size": 4, "name": "magic", "type": "uint32_le", "value": "0x5aa56cc6"},
                {"offset": 4, "size": 4, "name": "frame_size", "type": "uint32_le", "desc": "Total H.264 frame size"},
                {"offset": 8, "size": 4, "name": "seq_num", "type": "uint32_le", "desc": "Frame sequence number"},
                {"offset": 12, "size": 4, "name": "timestamp", "type": "uint32_le", "desc": "Timestamp/counter"},
                {"offset": 16, "size": 4, "name": "padding", "type": "uint32_le", "value": "0x00000000"},
                {"offset": 20, "size": 2, "name": "total_frags", "type": "uint16_le", "desc": "Fragment count"},
                {"offset": 22, "size": 2, "name": "frag_idx", "type": "uint16_le", "desc": "Fragment index (0-based)"},
                {"offset": 24, "size": 4, "name": "data_offset", "type": "uint32_le", "desc": "Byte offset in frame"},
                {"offset": 28, "size": 4, "name": "data_len", "type": "uint32_le", "desc": "Payload length"},
            ],
        },
        "ports": {"video": 1234, "handshake": 23459, "control": 23458},
        "codec": {"type": "H.264", "profile": "Constrained Baseline", "level": "3.1", "resolution": "640x360", "fps": 20},
        "handshake": {"wake": HANDSHAKE_WAKE.hex(), "trigger": HANDSHAKE_TRIGGER.hex()},
        "heartbeat": HEARTBEAT.hex(),
        "sps_pps": SPS_PPS.hex(),
    })


# Video Stream

@app.route("/api/stream")
def api_stream():
    user, error = require_user()
    if error:
        return error
    init_car()

    def generate():
        last_count = -1
        while True:
            jpeg = decoder.get_latest_jpeg() if decoder else None
            if jpeg:
                frame_count = decoder.frame_count
                if frame_count != last_count:
                    last_count = frame_count
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n\r\n"
                           + jpeg + b"\r\n")
            time.sleep(0.005)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


# Socket.IO

@socketio.on("connect")
def ws_connect():
    user = current_user()
    if not user:
        return False
    ensure_timer()
    with state_lock:
        lobby["users"][user["id"]] = user
        lobby["sid_to_user"][request.sid] = user["id"]
        lobby["user_sids"].setdefault(user["id"], set()).add(request.sid)
    join_room("lobby")
    emit("lobby:update", lobby_snapshot())
    broadcast_lobby()


@socketio.on("disconnect")
def ws_disconnect():
    changed = False
    with state_lock:
        user_id = lobby["sid_to_user"].pop(request.sid, None)
        if user_id:
            sids = lobby["user_sids"].get(user_id, set())
            sids.discard(request.sid)
            if not sids:
                lobby["user_sids"].pop(user_id, None)
                lobby["users"].pop(user_id, None)
                remove_from_queue(user_id)
                if lobby["active_driver"] == user_id:
                    next_driver_locked("active driver disconnected")
                changed = True
    leave_room("lobby")
    if changed:
        broadcast_lobby()


@socketio.on("control:command")
def ws_control_command(data):
    user = current_user()
    if not user:
        emit("control:ack", {"ok": False, "error": "login_required"})
        return
    emit("control:ack", handle_control_command(user, data or {}))


if __name__ == "__main__":
    ensure_timer()
    # Auto-connect to car on startup
    init_car()
    try:
        success = car.connect()
        if success:
            decoder.start()
            print(f"  Car connected: {car.state.value}")
        else:
            print(f"  Car connection failed: {car.state.value}")
    except Exception as e:
        print(f"  Car connect error: {e}")
    print("=" * 60)
    print("  WLtoys FPV Car - Race Lobby Cockpit")
    print("  http://localhost:5555")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=5555, debug=False, allow_unsafe_werkzeug=True)
