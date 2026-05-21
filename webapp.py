#!/usr/bin/env python3
"""
WLtoys FPV Car - Debug Cockpit Web Server
Flask app providing REST API + MJPEG video stream + lobby auth/control.
"""

import os
import sys
import json
import time
import secrets
import threading
from pathlib import Path
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
import requests
from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, send_from_directory, session, url_for
from flask.sessions import SecureCookieSessionInterface
from flask_socketio import SocketIO, emit, join_room, leave_room

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env.local if present (Discord OAuth, session secret, etc.)
load_dotenv(Path(__file__).parent / ".env.local")

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

TIRE_TEMP_MIN = 0.2
TIRE_TEMP_MAX = 1.0
TIRE_WARMUP_RATE = 0.023
TIRE_COOLING_DELAY = 10.0
TIRE_COOLING_RATE = 0.025
TIRE_STEER_THRESHOLD = 10
ENGINE_TEMP_MIN = 40.0
ENGINE_TEMP_MAX = 110.0
ENGINE_OPTIMAL_TEMP = 100.0
ENGINE_HEAT_RATE = 2.4
ENGINE_COOLING_DELAY = 10.0
ENGINE_COOLING_RATE = 1.2
ENGINE_THROTTLE_THRESHOLD = 20
SESSION_DT_MAX = 1.0
STEERING_COMMANDS = {"left", "right", "forward_left", "forward_right", "reverse_left", "reverse_right"}
THROTTLE_COMMANDS = {"forward", "reverse", "forward_left", "forward_right", "reverse_left", "reverse_right"}

# Guest Drive Codes: {code: {"created": time, "expires_at": time, "duration": int, "redeemed_by": str|None, "active": bool}}
GUEST_CODES_FILE = Path(os.environ.get(
    "GUEST_CODES_FILE",
    Path(__file__).parent / ".guest_codes.json",
))
guest_codes = {}
guest_codes_lock = threading.Lock()


def _save_guest_codes():
    """Save non-persistent codes to disk. Caller MUST hold guest_codes_lock."""
    try:
        GUEST_CODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in guest_codes.items() if not v.get("persistent")}
        with open(GUEST_CODES_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _load_guest_codes():
    try:
        if GUEST_CODES_FILE.exists():
            with open(GUEST_CODES_FILE) as f:
                data = json.load(f)
            with guest_codes_lock:
                for code, entry in data.items():
                    if entry.get("active") and time.time() < entry.get("expires_at", 0):
                        guest_codes[code] = entry
    except Exception:
        pass


# Persistent test code — always valid, never consumed
guest_codes["ZENGARDEN"] = {
    "created": time.time(),
    "expires_at": time.time() + (365 * 24 * 3600),  # 1 year
    "duration": 365 * 24 * 3600,
    "redeemed_by": None,
    "active": True,
    "persistent": True,
}

# Load saved codes from disk
_load_guest_codes()

# Active guest sessions: {guest_user_id: {"code": str, "expires_at": time}}
active_guest_sessions = {}
REDEEM_DEBUG_LOG = Path(os.environ.get(
    "REDEEM_DEBUG_LOG",
    Path(__file__).parent / "data" / "redeem-debug.log",
))

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
    "driver_sessions": {},
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
    # Guest users: check if session still valid
    if session.get("is_guest"):
        guest_id = user.get("id", "")
        with guest_codes_lock:
            info = active_guest_sessions.get(guest_id)
            if not info or time.time() > info["expires_at"]:
                session.clear()
                return None
        user = dict(user)
        return user
    if is_banned_user(user["id"]):
        session.clear()
        return None
    if not is_allowed_user(user["id"]):
        session.clear()
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


def generate_guest_code(duration_minutes=10):
    """Generate a random drive code like DRIVE-AX7K-M9P2."""
    import random
    import string
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No ambiguous chars
    while True:
        part1 = ''.join(random.choices(chars, k=4))
        part2 = ''.join(random.choices(chars, k=4))
        code = f"DRIVE-{part1}-{part2}"
        with guest_codes_lock:
            if code not in guest_codes:
                break
    now = time.time()
    with guest_codes_lock:
        guest_codes[code] = {
            "created": now,
            "expires_at": None,  # Starts when first redeemed
            "duration": duration_minutes * 60,
            "redeemed_by": None,
            "redeemed_at": None,
            "active": True,
        }
        _save_guest_codes()
    return code


def redeem_guest_code(code):
    """Redeem a drive code. Returns (user_dict, error_msg) or (user, None)."""
    code = code.strip().upper()
    with guest_codes_lock:
        entry = guest_codes.get(code)
        if not entry:
            return None, "Invalid code."
        if not entry["active"]:
            return None, "Code already used."
        # Check expiry only if code was already redeemed (has expires_at)
        if entry["expires_at"] and time.time() > entry["expires_at"]:
            entry["active"] = False
            return None, "Code expired."
        # Mark as redeemed
        entry["active"] = False
        entry["redeemed_by"] = "guest"
        # Persistent codes re-activate immediately
        if entry.get("persistent"):
            entry["active"] = True
            entry["redeemed_by"] = None
        # Start countdown on first redemption (not generation)
        if entry["expires_at"] is None:
            entry["expires_at"] = time.time() + entry["duration"]
            entry["redeemed_at"] = time.time()
        _save_guest_codes()
        # Generate guest user
        guest_id = f"guest-{code}"
        remaining = int(entry["expires_at"] - time.time())
        user = {
            "id": guest_id,
            "username": f"Guest-{code[-4:]}",
            "display_name": f"Guest Driver ({code[-4:]})",
            "avatar": None,
            "role": "driver",
            "is_guest": True,
            "guest_expires_at": entry["expires_at"],
            "guest_code": code,
            "can_connect": True,
        }
        # Track active guest session
        active_guest_sessions[guest_id] = {
            "code": code,
            "expires_at": entry["expires_at"],
        }
        return user, None


def expire_guest_sessions():
    """Check and expire guest sessions. Called from timer_loop."""
    now = time.time()
    expired = []
    with guest_codes_lock:
        for guest_id, info in list(active_guest_sessions.items()):
            if now > info["expires_at"]:
                expired.append(guest_id)
                del active_guest_sessions[guest_id]
    for guest_id in expired:
        kick_user(guest_id, "Drive code expired")


def get_active_codes():
    """Return list of active/redeemed codes for admin view."""
    now = time.time()
    result = []
    with guest_codes_lock:
        for code, entry in guest_codes.items():
            expires_at = entry.get("expires_at")
            if expires_at is None:
                # Dormant code — not yet redeemed
                remaining = entry["duration"]
                expired = False
                dormant = True
            else:
                remaining = max(0, int(expires_at - now))
                expired = now > expires_at
                dormant = False
            result.append({
                "code": code,
                "created": entry["created"],
                "duration": entry["duration"],
                "remaining": remaining,
                "redeemed_by": entry["redeemed_by"],
                "active": entry["active"],
                "expired": expired,
                "dormant": dormant,
                "persistent": entry.get("persistent", False),
            })
    return sorted(result, key=lambda x: x["created"], reverse=True)


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


def require_can_connect():
    """Allow admins and guests with can_connect flag."""
    user, error = require_user()
    if error:
        return None, error
    if is_admin(user):
        return user, None
    if user.get("is_guest") and user.get("can_connect"):
        return user, None
    return None, (jsonify({"ok": False, "error": "connect_not_allowed"}), 403)


def public_user(user):
    if not user:
        return None
    user_id = user["id"]
    # Guest users have role stored directly in their dict
    if user.get("is_guest"):
        role = user.get("role", "driver")
    else:
        role = role_for_user(user_id)
    # can_connect: admins always, guests if flagged, others never
    if role == "admin":
        can_connect = True
    elif user.get("is_guest") and user.get("can_connect"):
        can_connect = True
    else:
        can_connect = False
    return {
        "id": user_id,
        "username": user.get("username", "unknown"),
        "display_name": user.get("display_name") or user.get("username", "unknown"),
        "avatar": user.get("avatar"),
        "role": role,
        "is_guest": user.get("is_guest", False),
        "can_connect": can_connect,
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
        active_session = None
        if active_id:
            state = driver_session_for(active_id)
            update_tire_warmup(state, state.get("last_command", "stop"), state.get("last_steer_range", 100))
            active_session = update_engine_temperature(state, state.get("last_command", "stop"), state.get("effective_speed", 100))
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
            "active_session": active_session,
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
    lobby.setdefault("driver_sessions", {})[user_id] = new_driver_session(lobby["driver_started_at"])


def next_driver_locked(reason):
    if lobby["active_driver"]:
        send_neutral(reason)
        lobby.setdefault("driver_sessions", {}).pop(lobby["active_driver"], None)
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
    # For guests: revoke their code and invalidate session
    if user_id.startswith("guest-"):
        with guest_codes_lock:
            guest_info = active_guest_sessions.pop(user_id, None)
            if guest_info:
                code = guest_info.get("code")
                if code and code in guest_codes:
                    # Revoke the code (mark inactive, won't work if re-entered)
                    guest_codes[code]["active"] = False
                    guest_codes[code]["redeemed_by"] = "kicked"
                    _save_guest_codes()
    # Notify the user they've been kicked via socket.io
    for sid in list(lobby.get("user_sids", {}).get(user_id, set())):
        try:
            socketio.emit("kicked", {"reason": reason}, room=sid, namespace="/")
        except Exception:
            pass
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
        # Expire guest sessions
        expire_guest_sessions()
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


def new_driver_session(now=None):
    now = now or time.time()
    return {
        "tire_temp": TIRE_TEMP_MIN,
        "last_steering_time": now,
        "last_update": now,
        "last_engine_update": now,
        "effective_steer_range": 100,
        "effective_speed": 100,
        "last_steer_range": 100,
        "engine_temp": ENGINE_TEMP_MIN,
        "last_throttle_time": now,
        "corner_speed_cap": int(round(60 + (40 * TIRE_TEMP_MIN))),
        "last_command": "stop",
    }


def driver_session_for(user_id, now=None):
    now = now or time.time()
    sessions = lobby.setdefault("driver_sessions", {})
    if user_id not in sessions:
        sessions[user_id] = new_driver_session(now)
    return sessions[user_id]


def tire_visual_state(tire_temp):
    if tire_temp >= 0.9:
        return "optimal"
    if tire_temp >= 0.6:
        return "hot"
    if tire_temp >= 0.3:
        return "warming"
    return "cold"


def session_snapshot(state):
    tire_temp = max(TIRE_TEMP_MIN, min(TIRE_TEMP_MAX, float(state.get("tire_temp", TIRE_TEMP_MIN))))
    engine_temp = max(ENGINE_TEMP_MIN, min(ENGINE_TEMP_MAX, float(state.get("engine_temp", ENGINE_TEMP_MIN))))
    steering_multiplier = 0.6 + (0.4 * tire_temp)
    corner_speed_cap = int(round(60 + (40 * tire_temp)))
    max_throttle_pct = min(100.0, 60 + ((engine_temp - ENGINE_TEMP_MIN) * (40 / 60)))
    return {
        "tire_temp": round(tire_temp, 3),
        "tire_percent": int(round(tire_temp * 100)),
        "tire_state": tire_visual_state(tire_temp),
        "steering_multiplier": round(steering_multiplier, 3),
        "corner_speed_cap": corner_speed_cap,
        "effective_steer_range": int(round(state.get("effective_steer_range", 100))),
        "engine_temp": round(engine_temp, 1),
        "engine_percent": int(round(max(0, min(100, ((engine_temp - ENGINE_TEMP_MIN) / (ENGINE_OPTIMAL_TEMP - ENGINE_TEMP_MIN)) * 100)))),
        "engine_state": engine_visual_state(engine_temp),
        "max_throttle_pct": int(round(max_throttle_pct)),
        "effective_speed": int(round(state.get("effective_speed", 100))),
    }


def engine_visual_state(engine_temp):
    if engine_temp >= 95:
        return "optimal"
    if engine_temp >= 80:
        return "hot"
    if engine_temp >= 55:
        return "warming"
    return "cold"


def update_tire_warmup(state, command, steer_range, now=None):
    now = now or time.time()
    last_update = float(state.get("last_update", now))
    dt = max(0.0, min(SESSION_DT_MAX, now - last_update))
    tire_temp = max(TIRE_TEMP_MIN, min(TIRE_TEMP_MAX, float(state.get("tire_temp", TIRE_TEMP_MIN))))
    active_steering = command in STEERING_COMMANDS and steer_range >= TIRE_STEER_THRESHOLD

    if active_steering:
        tire_temp = min(TIRE_TEMP_MAX, tire_temp + (TIRE_WARMUP_RATE * dt))
        state["last_steering_time"] = now
    elif now - float(state.get("last_steering_time", now)) > TIRE_COOLING_DELAY:
        tire_temp = max(TIRE_TEMP_MIN, tire_temp - (TIRE_COOLING_RATE * dt))

    state["tire_temp"] = tire_temp
    state["last_update"] = now
    state["last_command"] = command
    state["last_steer_range"] = steer_range
    return session_snapshot(state)


def update_engine_temperature(state, command, speed, now=None):
    now = now or time.time()
    last_update = float(state.get("last_engine_update", now))
    dt = max(0.0, min(SESSION_DT_MAX, now - last_update))
    engine_temp = max(ENGINE_TEMP_MIN, min(ENGINE_TEMP_MAX, float(state.get("engine_temp", ENGINE_TEMP_MIN))))
    active_throttle = command in THROTTLE_COMMANDS and speed >= ENGINE_THROTTLE_THRESHOLD

    if active_throttle:
        engine_temp = min(ENGINE_TEMP_MAX, engine_temp + (ENGINE_HEAT_RATE * (speed / 100) * dt))
        state["last_throttle_time"] = now
    elif now - float(state.get("last_throttle_time", now)) > ENGINE_COOLING_DELAY:
        engine_temp = max(ENGINE_TEMP_MIN, engine_temp - (ENGINE_COOLING_RATE * dt))

    state["engine_temp"] = engine_temp
    state["last_engine_update"] = now
    return session_snapshot(state)


def apply_session_modifiers(user_id, command, speed, steer_range, now=None):
    state = driver_session_for(user_id, now)
    telemetry = update_tire_warmup(state, command, steer_range, now)
    telemetry = update_engine_temperature(state, command, speed, now)

    steering_multiplier = telemetry["steering_multiplier"]
    effective_steer_range = int(round(steer_range * steering_multiplier))
    effective_speed = min(speed, telemetry["max_throttle_pct"])

    if command in STEERING_COMMANDS and command in THROTTLE_COMMANDS:
        effective_speed = min(effective_speed, int(round(speed * (telemetry["corner_speed_cap"] / 100))))

    state["effective_steer_range"] = effective_steer_range
    state["effective_speed"] = effective_speed
    telemetry["effective_steer_range"] = effective_steer_range
    telemetry["effective_speed"] = effective_speed
    return effective_speed, effective_steer_range, telemetry


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
        speed, steer_range, telemetry = apply_session_modifiers(user["id"], cmd, speed, steer_range)

    success = car.send_command(cmd, speed=speed, steer_range=steer_range)
    return {"ok": success, "command": cmd, "speed": speed, "steer_range": steer_range, "telemetry": telemetry}


def local_request():
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


# HTML/Auth Routes

def is_mobile():
    """Detect mobile browsers from User-Agent."""
    ua = request.headers.get("User-Agent", "").lower()
    mobile_keywords = [
        "iphone", "ipod", "android", "blackberry", "windows phone",
        "opera mini", "mobile", "tablet", "ipad"
    ]
    return any(kw in ua for kw in mobile_keywords)


@app.route("/")
def index():
    user = current_user()
    
    # Mobile detection - use mobile template for mobile browsers
    if is_mobile():
        resp = make_response(render_template(
            "mobile.html",
            user=public_user(user),
            discord_configured=bool(os.environ.get("DISCORD_CLIENT_ID") and os.environ.get("DISCORD_CLIENT_SECRET")),
            is_local=local_request(),
        ))
    else:
        resp = make_response(render_template(
            "index.html",
            user=public_user(user),
            discord_configured=bool(os.environ.get("DISCORD_CLIENT_ID") and os.environ.get("DISCORD_CLIENT_SECRET")),
            is_local=local_request(),
        ))
    
    # No-cache headers to prevent stale templates
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


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
    resp = redirect(url_for("index"))
    # Explicitly delete the session cookie so Cloudflare/browser caches clear it
    resp.delete_cookie("session", domain=public_redirect_host() if request_host() == public_redirect_host() else None)
    # Also add no-cache headers
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


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


@app.route("/api/guest/generate", methods=["POST"])
def api_guest_generate():
    """Admin: generate a drive code."""
    user, error = require_admin()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    duration = data.get("duration_minutes", 10)
    try:
        duration = int(duration)
        duration = max(1, min(duration, 120))  # 1 min to 2 hours
    except (TypeError, ValueError):
        duration = 10
    code = generate_guest_code(duration)
    return jsonify({"ok": True, "code": code, "duration_minutes": duration})


@app.route("/api/guest/codes", methods=["GET"])
def api_guest_codes():
    """Admin: list all guest codes."""
    user, error = require_admin()
    if error:
        return error
    return jsonify({"ok": True, "codes": get_active_codes()})


@app.route("/api/guest/revoke", methods=["POST"])
def api_guest_revoke():
    """Admin: revoke a guest code."""
    user, error = require_admin()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip().upper()
    with guest_codes_lock:
        if code in guest_codes:
            guest_codes[code]["active"] = False
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "code_not_found"}), 404


@app.route("/api/guest/clear", methods=["POST"])
def api_guest_clear():
    """Admin: clear used/expired codes."""
    user, error = require_admin()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "used")  # "used" or "all"
    removed = 0
    with guest_codes_lock:
        if mode == "all":
            # Preserve persistent codes when clearing all
            to_remove = [c for c, e in guest_codes.items() if not e.get("persistent")]
            for c in to_remove:
                del guest_codes[c]
                removed += 1
        else:
            to_remove = [c for c, e in guest_codes.items() if not e["active"] or time.time() > e["expires_at"] and not e.get("persistent")]
            for c in to_remove:
                del guest_codes[c]
                removed += 1
    _save_guest_codes()
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/redeem-code", methods=["POST"])
def api_redeem_code():
    """Guest: redeem a drive code to get driver access."""
    import datetime
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    src = request.remote_addr
    ua = request.headers.get("User-Agent", "")[:60]
    REDEEM_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(REDEEM_DEBUG_LOG, "a") as _f:
        _f.write(f"{datetime.datetime.now()} REDEEM from={src} raw_code={code!r} content_type={request.content_type} ua={ua}\n")
    if not code:
        with open(REDEEM_DEBUG_LOG, "a") as _f:
            _f.write(f"  -> REJECTED: no_code_provided, data={data!r}\n")
        return jsonify({"ok": False, "error": "no_code_provided"}), 400
    user, error = redeem_guest_code(code)
    if error:
        with guest_codes_lock:
            known = list(guest_codes.keys())
        with open(REDEEM_DEBUG_LOG, "a") as _f:
            _f.write(f"  -> FAILED: error={error!r} known_codes_count={len(known)}\n")
        return jsonify({"ok": False, "error": error}), 403
    session["user"] = user
    session["is_guest"] = True
    with open(REDEEM_DEBUG_LOG, "a") as _f:
        _f.write(f"  -> OK: guest_id={user['id']}\n")
    return jsonify({"ok": True, "user": public_user(user), "expires_at": user["guest_expires_at"]})


@app.route("/api/guest/debug", methods=["GET"])
def api_guest_debug():
    """Temporary debug: list all codes (local only)."""
    if not local_request():
        return jsonify({"ok": False, "error": "local_only"}), 403
    with guest_codes_lock:
        codes = {k: {**v, "expires_in": max(0, int(v["expires_at"] - time.time()))} for k, v in guest_codes.items()}
    return jsonify({"ok": True, "count": len(codes), "codes": codes, "active_sessions": list(active_guest_sessions.keys())})


@app.route("/api/guest/debug-generate", methods=["POST"])
def api_guest_debug_generate():
    """Temporary: generate a code without admin auth (local only)."""
    if not local_request():
        return jsonify({"ok": False, "error": "local_only"}), 403
    code = generate_guest_code(30)
    return jsonify({"ok": True, "code": code})


@app.route("/api/guest/test", methods=["GET"])
def api_guest_test():
    """Public test endpoint — returns a persistent code that never gets consumed."""
    return jsonify({
        "ok": True,
        "code": "ZENGARDEN",
        "message": "This is a persistent test code. It should always be redeemable.",
        "known_codes_count": len(guest_codes),
        "known_codes": list(guest_codes.keys()),
    })


@app.route("/api/guest/toggle-persistent", methods=["POST"])
def api_guest_toggle_persistent():
    """Admin: enable/disable a persistent code."""
    user, error = require_admin()
    if error:
        return error
    
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip().upper()
    enabled = data.get("enabled", True)
    
    if not code:
        return jsonify({"ok": False, "error": "no_code"}), 400
    
    with guest_codes_lock:
        if code not in guest_codes:
            return jsonify({"ok": False, "error": "code_not_found"}), 404
        
        if not guest_codes[code].get("persistent"):
            return jsonify({"ok": False, "error": "not_persistent"}), 400
        
        guest_codes[code]["active"] = enabled
        guest_codes[code]["redeemed_by"] = None if enabled else "disabled"
        _save_guest_codes()
    
    return jsonify({"ok": True, "code": code, "enabled": enabled})


@app.route("/api/guest/add-persistent", methods=["POST"])
def api_guest_add_persistent():
    """Admin: add or restore a persistent code."""
    user, error = require_admin()
    if error:
        return error
    
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip().upper()
    
    if not code:
        return jsonify({"ok": False, "error": "no_code"}), 400
    
    with guest_codes_lock:
        if code in guest_codes:
            # Re-activate existing persistent code
            if guest_codes[code].get("persistent"):
                guest_codes[code]["active"] = True
                guest_codes[code]["redeemed_by"] = None
                _save_guest_codes()
                return jsonify({"ok": True, "code": code, "restored": True})
        
        # Create new persistent code
        guest_codes[code] = {
            "created": time.time(),
            "expires_at": time.time() + 365 * 24 * 3600,  # 1 year (effectively unlimited)
            "duration": 0,  # No time limit for persistent codes
            "redeemed_by": None,
            "active": True,
            "persistent": True,
        }
        _save_guest_codes()
    
    return jsonify({"ok": True, "code": code, "created": True})


@app.route("/api/guest/remaining", methods=["GET"])
def api_guest_remaining():
    """Get remaining time for current guest session."""
    user = current_user()
    if not user or not user.get("is_guest"):
        return jsonify({"ok": False, "error": "not_guest"})
    guest_id = user["id"]
    with guest_codes_lock:
        info = active_guest_sessions.get(guest_id)
        if not info:
            return jsonify({"ok": True, "remaining": 0, "expired": True})
        remaining = max(0, int(info["expires_at"] - time.time()))
        return jsonify({"ok": True, "remaining": remaining, "expired": remaining <= 0})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    user, error = require_can_connect()
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
    user, error = require_can_connect()
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

# Admin stream state (per-session, not global)
admin_stream_paused = {}  # sid -> bool
admin_stream_smart = {}   # sid -> bool


@app.route("/api/stream")
def api_stream():
    user, error = require_user()
    if error:
        return error
    init_car()

    is_admin = user.get("role") == "admin"
    sid = request.args.get("sid", str(id(request)))

    def generate():
        last_count = -1
        while True:
            # Check admin pause state
            if is_admin and admin_stream_paused.get(sid, False):
                time.sleep(0.5)
                continue

            jpeg = decoder.get_latest_jpeg() if decoder else None
            if jpeg:
                frame_count = decoder.frame_count
                if frame_count != last_count:
                    last_count = frame_count
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n\r\n"
                           + jpeg + b"\r\n")

            # Smart mode: limit admin to 5fps when enabled
            if is_admin and admin_stream_smart.get(sid, False):
                time.sleep(0.2)  # 5fps
            else:
                time.sleep(0.005)  # ~200fps max (normal)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


@app.route("/api/stream/pause", methods=["POST"])
def api_stream_pause():
    """Toggle admin stream pause state."""
    user, error = require_user()
    if error:
        return error
    if user.get("role") != "admin":
        return jsonify({"ok": False, "error": "admin_required"}), 403

    data = request.get_json(silent=True) or {}
    paused = data.get("paused", False)
    sid = data.get("sid", str(id(request)))

    admin_stream_paused[sid] = paused
    return jsonify({"ok": True, "paused": paused})


@app.route("/api/stream/smart", methods=["POST"])
def api_stream_smart():
    """Toggle admin smart mode (5fps limit)."""
    user, error = require_user()
    if error:
        return error
    if user.get("role") != "admin":
        return jsonify({"ok": False, "error": "admin_required"}), 403

    data = request.get_json(silent=True) or {}
    smart = data.get("smart", False)
    sid = data.get("sid", str(id(request)))

    admin_stream_smart[sid] = smart
    return jsonify({"ok": True, "smart": smart})


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
    init_car()
    if os.environ.get("AUTO_CONNECT_CAR", "").lower() in {"1", "true", "yes"}:
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
