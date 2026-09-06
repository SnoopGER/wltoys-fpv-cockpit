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
from collections import deque
from pathlib import Path
from urllib.parse import urlencode, urlparse

from dotenv import load_dotenv
import requests
from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, send_from_directory, session, url_for
from flask.sessions import SecureCookieSessionInterface
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sock import Sock

from video_relay import RawFrameRelay

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
sock = Sock(app)  # raw H.264 -> WebCodecs relay (Phase 3)

# Global State

DEFAULT_CAR_ID = os.environ.get("DEFAULT_CAR_ID", "car1")
CAR_CONFIGS = {}
car_slots = {}
state_lock = threading.RLock()
timer_started = False

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


# Persistent codes are NOT hardcoded anymore (AUDIT §6.4: ZENGARDEN/ZENADMIN
# were a source-visible backdoor incl. admin). Opt in via env instead:
#   PERSISTENT_CODES=DRIVE-ABCD-EFGH:driver,MY-CODE:admin
for _entry in os.environ.get("PERSISTENT_CODES", "").split(","):
    _code, _, _role = _entry.strip().partition(":")
    if not _code:
        continue
    guest_codes[_code.upper()] = {
        "created": time.time(),
        "expires_at": time.time() + (365 * 24 * 3600),  # 1 year
        "duration": 365 * 24 * 3600,
        "redeemed_by": None,
        "active": True,
        "persistent": True,
        "role": _role or "driver",
    }

# Load saved codes from disk
_load_guest_codes()

# Active guest sessions: {guest_user_id: {"code": str, "expires_at": time}}
active_guest_sessions = {}
# (redeem debug log removed — it wrote plaintext codes + IP + UA to disk, AUDIT §6.3)

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


CAR_CONFIGS = {
    "car1": {
        "id": "car1",
        "label": os.environ.get("FPV_CAR1_LABEL", "Car 1 / 99613492"),
        "ssid": os.environ.get("FPV_CAR1_SSID", "WL_FPV_CAR_99613492"),
        "ip": os.environ.get("FPV_CAR1_IP", os.environ.get("FPV_CAR_IP", "172.16.11.1")),
        "bind_ip": os.environ.get("FPV_CAR1_BIND_IP", ""),
        "listen_port": env_int("FPV_CAR1_LISTEN_PORT", int(os.environ.get("FPV_LISTEN_PORT", "1234"))),
    },
    "car2": {
        "id": "car2",
        "label": os.environ.get("FPV_CAR2_LABEL", "Car 2 / 64886271"),
        "ssid": os.environ.get("FPV_CAR2_SSID", "WL_FPV_CAR_64886271"),
        "ip": os.environ.get("FPV_CAR2_IP", "172.16.11.1"),
        "bind_ip": os.environ.get("FPV_CAR2_BIND_IP", ""),
        "listen_port": env_int("FPV_CAR2_LISTEN_PORT", 1234),
    },
    "car3": {
        "id": "car3",
        "label": os.environ.get("FPV_CAR3_LABEL", "Car 3 / 10335160"),
        "ssid": os.environ.get("FPV_CAR3_SSID", "WL FPV CAR 10335160"),
        "ip": os.environ.get("FPV_CAR3_IP", "172.16.11.1"),
        "bind_ip": os.environ.get("FPV_CAR3_BIND_IP", ""),
        "listen_port": env_int("FPV_CAR3_LISTEN_PORT", 1234),
    },
}


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
    "admin_override": False,  # race-director hold: only admins may drive
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

# Phase 4 race engine (server-authoritative, ROADMAP D4-D9, D15). Idle unless an
# admin starts a race — free-drive behavior is untouched while state == idle.
# D15: during a race the governor IS the ceiling (default 70%); boost unlocks
# RACE_BOOST_MAX_PERCENT (100 = full car power) for RACE_BOOST_SECONDS, then a
# RACE_BOOST_COOLDOWN_SECONDS recovery window blocks re-use.
from race_engine import RaceEngine  # noqa: E402


def _env_float(name, default):
    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


race = RaceEngine(
    env_int("RACE_GOVERNOR_PERCENT", 70, 5, 100),
    boost_seconds=_env_float("RACE_BOOST_SECONDS", 5.0),
    boost_cooldown=_env_float("RACE_BOOST_COOLDOWN_SECONDS", 15.0),
    boost_max=env_int("RACE_BOOST_MAX_PERCENT", 100, 5, 100),
)


def normalize_car_id(car_id=None):
    # str() guard (REVIEW SRV-6): client-supplied car must never reach .strip() raw
    car_id = (str(car_id) if car_id else None) or request.args.get("car") or DEFAULT_CAR_ID
    car_id = car_id.strip().lower()
    return car_id if car_id in CAR_CONFIGS else DEFAULT_CAR_ID


_init_car_lock = threading.RLock()


def init_car(car_id=None):
    car_id = normalize_car_id(car_id)
    slot = car_slots.get(car_id)
    if slot is None:
        with _init_car_lock:  # AUDIT §6.7: two racing first-requests must not
            slot = car_slots.get(car_id)  # build two CarProtocols / double-bind UDP
            if slot is None:
                config = CAR_CONFIGS[car_id]
                decoder = VideoDecoder(width=640, height=360, quality=75)
                relay = RawFrameRelay()
                car = CarProtocol(
                    car_ip=config["ip"],
                    listen_port=config["listen_port"],
                    bind_ip=config.get("bind_ip", ""),
                    name=config["label"],
                    on_log=lambda level, msg: None,
                )
                car.on_frame = lambda data, d=decoder, r=relay: (d.feed_frame(data), r.feed(data))
                slot = {"car": car, "decoder": decoder, "config": config,
                        "relay": relay, "last_control_at": None}
                car_slots[car_id] = slot
    return slot


def all_car_snapshots():
    result = []
    for car_id, config in CAR_CONFIGS.items():
        slot = init_car(car_id)
        status = slot["car"].get_status()
        status["id"] = car_id
        status["label"] = config["label"]
        status["ssid"] = config["ssid"]
        status["decoder_frames"] = slot["decoder"].frame_count
        status["decoder_codec"] = slot["decoder"].codec_name
        result.append(status)
    return result


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
    """Generate a random drive code like DRIVE-AX7K-M9P2.

    Codes are bearer credentials -> CSPRNG (AUDIT §6.5), not random.
    """
    import secrets
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No ambiguous chars
    while True:
        part1 = ''.join(secrets.choice(chars) for _ in range(4))
        part2 = ''.join(secrets.choice(chars) for _ in range(4))
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
        role = entry.get("role", "driver")
        if role not in {"admin", "driver", "spectator"}:
            role = "driver"

        # Generate guest user
        guest_id = f"guest-{code}"
        remaining = int(entry["expires_at"] - time.time())
        role_label = "Admin" if role == "admin" else "Driver"
        user = {
            "id": guest_id,
            "username": f"Guest-{code[-4:]}",
            "display_name": f"Guest {role_label} ({code[-4:]})",
            "avatar": None,
            "role": role,
            "is_guest": True,
            "guest_expires_at": entry["expires_at"],
            "guest_code": code,
            "can_connect": role in {"admin", "driver"},
        }
        # Track active guest session
        active_guest_sessions[guest_id] = {
            "code": code,
            "expires_at": entry["expires_at"],
        }
        return user, None


def expire_guest_sessions():
    """Check and expire guest sessions. Called from timer_loop.

    kick_user mutates lobby — must hold state_lock (AUDIT §6.7);
    state_lock is an RLock, no deadlock with deeper lock usage.
    """
    now = time.time()
    expired = []
    with guest_codes_lock:
        for guest_id, info in list(active_guest_sessions.items()):
            if now > info["expires_at"]:
                expired.append(guest_id)
                del active_guest_sessions[guest_id]
    for guest_id in expired:
        with state_lock:
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
        "admin_override": lobby.get("admin_override", False),
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


def car_online(car_id=None):
    slot = car_slots.get(normalize_car_id(car_id))
    if not slot:
        return False
    car = slot["car"]
    return car.state in {ConnectionState.CONNECTED, ConnectionState.STREAMING}


def lobby_snapshot():
    init_car(DEFAULT_CAR_ID)
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
            "admin_override": lobby.get("admin_override", False),
            "active_driver": public_user(lobby["users"].get(active_id)) if active_id else None,
            "remaining_drive_time": seconds_remaining(),
            "queue": [public_user(lobby["users"].get(uid, {"id": uid, "username": uid})) for uid in lobby["queue"]],
            "connected_spectators": [u for u in users if u and u["role"] == "spectator"],
            "connected_users": users,
            "banned_users": banned,
            "car_online": car_online(DEFAULT_CAR_ID),
            "cars": all_car_snapshots(),
            "max_speed_percent": lobby["max_speed_percent"],
            "session_duration": lobby["session_duration"],
            "race": race.snapshot(),
        }


def broadcast_lobby():
    socketio.emit("lobby:update", lobby_snapshot(), room="lobby")


def send_neutral(reason, car_id=None):
    car_ids = [normalize_car_id(car_id)] if car_id else list(CAR_CONFIGS)
    for cid in car_ids:
        slot = init_car(cid)
        car = slot["car"]
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
    # Fresh watchdog window for the new driver
    for slot in car_slots.values():
        slot["last_control_at"] = time.time()


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


CONTROL_SILENCE_SECONDS = int(os.environ.get("CONTROL_SILENCE_SECONDS", "3"))


def watchdog_control_silence():
    """If the active driver's client went silent, force neutral.

    The heartbeat thread reinforces _last_motor_cmd forever; a dead browser
    (no socket disconnect processed — e.g. mobile) would otherwise keep the
    car driving at the last throttle. AUDIT §6.2.
    """
    if not lobby.get("active_driver") or lobby.get("paused"):
        return
    now = time.time()
    for cid, slot in list(car_slots.items()):
        last = slot.get("last_control_at")
        if last is None:
            slot["last_control_at"] = now
            continue
        if now - last > CONTROL_SILENCE_SECONDS:
            slot["last_control_at"] = now  # avoid re-firing every second
            if slot["car"].state in (ConnectionState.CONNECTED, ConnectionState.STREAMING):
                send_neutral(f"control silence > {CONTROL_SILENCE_SECONDS}s", cid)


def timer_loop():
    while True:
        time.sleep(1)
        try:
            timer_tick()
        except Exception:
            # REVIEW SRV-2 (2026-09-06): one exception must never kill this
            # thread — the control-silence watchdog, guest budgets and driver
            # rotation all live here, and ensure_timer never respawns.
            try:
                app.logger.exception("timer tick failed")
            except Exception:
                pass


def timer_tick():
    # Expire guest sessions
    expire_guest_sessions()
    watchdog_control_silence()
    with state_lock:
        # REVIEW SRV-3: tick() runs under state_lock like every other race
        # call site, so it cannot wedge the engine GREEN after a reset nor
        # race race.snapshot()'s countdown_until read.
        race_changed = race.tick()  # countdown -> green
        changed = False
        should_broadcast = False
        if lobby["active_driver"] and not lobby["paused"]:
            changed = advance_driver_if_due_locked("driver timer expired")
            # Broadcast every second while a session is active so clients see the countdown.
            should_broadcast = True
    if changed or should_broadcast or race_changed:
        broadcast_lobby()


def validate_control_user(user):
    """Active-driver or admin may act on the car. Guests are drivers too
    (role_for_user() knows only Discord ids — do not gate guests through it)."""
    active = lobby["active_driver"]
    if is_admin(user):
        return True
    return bool(active and user["id"] == active)


def clamp_int(value, default, min_value, max_value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


_latency_lock = threading.Lock()
_latency_samples = deque(maxlen=100)
_tx_samples = deque(maxlen=100)


def note_control_rx(data, rx_ts):
    """Record one-way input->server latency from client_ts (needs rough
    clock sync; client derives offset via control:rtt)."""
    try:
        client_ts = float((data or {}).get("client_ts"))
    except (TypeError, ValueError):
        return
    if client_ts <= 0:
        return
    one_way = rx_ts - client_ts
    if 0 <= one_way < 5.0:  # ignore clock-skew outliers / negative time travel
        with _latency_lock:
            _latency_samples.append(one_way * 1000.0)


def note_command_tx(rx_ts, tx_ts):
    """Server-side pipeline: command received -> UDP packet handed to car."""
    dt = (tx_ts - rx_ts) * 1000.0
    if 0 <= dt < 5000:
        with _latency_lock:
            _tx_samples.append(dt)


def latency_stats():
    with _latency_lock:
        vals = sorted(_latency_samples)
        txvals = sorted(_tx_samples)
    if not vals:
        return {"control_latency_ms": None, "control_latency_avg_ms": None,
                "control_latency_p95_ms": None, "control_latency_samples": 0}
    avg = sum(vals) / len(vals)
    p95 = vals[min(len(vals) - 1, int(len(vals) * 0.95))]
    stats = {"control_latency_ms": round(vals[-1], 1),
             "control_latency_avg_ms": round(avg, 1),
             "control_latency_p95_ms": round(p95, 1),
             "control_latency_samples": len(vals)}
    if txvals:
        stats["control_tx_avg_ms"] = round(sum(txvals) / len(txvals), 2)
        stats["control_tx_p95_ms"] = round(txvals[min(len(txvals) - 1, int(len(txvals) * 0.95))], 2)
    return stats


def handle_control_command(user, data, rx_ts=None):
    if not isinstance(data, dict):  # REVIEW SRV-6: malformed WS frames
        return {"ok": False, "error": "invalid_payload"}
    car_id = normalize_car_id(data.get("car"))
    slot = init_car(car_id)
    if rx_ts is None:
        rx_ts = time.time()
    with state_lock:
        cmd = data.get("command", "stop")
        if not isinstance(cmd, str) or cmd not in COMMANDS:
            return {"ok": False, "error": "invalid_command"}

        # Server-side authorization (AUDIT §6.1): browser gating is not a boundary.
        if lobby.get("emergency_stop") and not is_admin(user):
            return {"ok": False, "error": "emergency_stop"}
        # Input priority (ROADMAP Phase 2): admin override outranks the driver.
        if lobby.get("admin_override") and not is_admin(user):
            return {"ok": False, "error": "admin_override"}
        if not validate_control_user(user):
            return {"ok": False, "error": "unauthorized_driver"}

        max_speed = clamp_int(lobby.get("max_speed_percent", 100), MAX_REMOTE_SPEED_PERCENT, 5, 100)
        speed = clamp_int(data.get("speed", 100), 100, 0, 100)
        steer_range = clamp_int(data.get("steer_range", 100), 100, 0, 100)

        # Speed ceilings (D15, Snoop 2026-09-06):
        #  free-drive -> lobby cap (MAX_REMOTE_SPEED_PERCENT bounds the slider)
        #  race       -> the ceiling lives in the race engine: governor limits
        #               everyone (default 70), 🍄 boost releases to 100% power.
        #               The lobby slider must NOT clamp first, or boost == baseline.
        race_meta = None
        if race.active:
            cmd, speed, steer_range, race_meta = race.modify(
                user["id"], cmd, speed, steer_range)
        else:
            speed = min(speed, max_speed)

    success = slot["car"].send_command(cmd, speed=speed, steer_range=steer_range)
    if success:
        # Client-silence watchdog bookkeeping (AUDIT §6.2)
        now = time.time()
        slot["last_control_at"] = now
        note_command_tx(rx_ts, now)
    out = {"ok": success, "car": car_id, "command": cmd,
           "speed": speed, "steer_range": steer_range}
    if race_meta:
        out["race"] = race_meta
    return out


def local_request():
    # REVIEW SRV-1 (2026-09-06): cloudflared connects to the origin over
    # loopback, so remote_addr alone is NOT a locality signal — every tunnel
    # visitor would appear local. Proxy headers present => remote by definition.
    if request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For"):
        return False
    return request.remote_addr in {"127.0.0.1", "::1", "localhost"}


def client_ip():
    """Real client IP for rate-limit keys (REVIEW SRV-1). The tunnel process
    itself connects via loopback, so raw remote_addr collapses to 127.0.0.1
    for every visitor. Trust CF-Connecting-IP only when the direct peer IS
    loopback (our own cloudflared) — never from an arbitrary peer."""
    if request.remote_addr in {"127.0.0.1", "::1", "localhost"}:
        cf = request.headers.get("CF-Connecting-IP", "").strip()
        if cf:
            return cf
    return request.remote_addr or "?"


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
@app.route("/car/<car_id>")
def index(car_id=None):
    user = current_user()
    car_id = normalize_car_id(car_id)
    car_config = CAR_CONFIGS[car_id]
    
    # Mobile detection - use mobile template for mobile browsers
    if is_mobile():
        resp = make_response(render_template(
            "mobile.html",
            user=public_user(user),
            discord_configured=bool(os.environ.get("DISCORD_CLIENT_ID") and os.environ.get("DISCORD_CLIENT_SECRET")),
            is_local=local_request(),
            car_id=car_id,
            car_config=car_config,
            cars=CAR_CONFIGS,
        ))
    else:
        resp = make_response(render_template(
            "index.html",
            user=public_user(user),
            discord_configured=bool(os.environ.get("DISCORD_CLIENT_ID") and os.environ.get("DISCORD_CLIENT_SECRET")),
            is_local=local_request(),
            car_id=car_id,
            car_config=car_config,
            cars=CAR_CONFIGS,
        ))
    
    # No-cache headers to prevent stale templates
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/admin/cars")
def admin_cars():
    user = current_user()
    if not (is_admin(user) or local_request()):
        return redirect(url_for("index"))
    return render_template(
        "admin_cars.html",
        user=public_user(user),
        cars=CAR_CONFIGS,
        now=int(time.time()),
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
    if rate_limited(f"oauth:{client_ip()}", limit=10, window=60):
        return "Too many login attempts. Wait a minute.", 429
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
    # AUDIT §6.3: anonymous snapshot leaked usernames + banned IDs
    user, error = require_user()
    if error:
        return error
    return jsonify(lobby_snapshot())


@app.route("/api/cars")
def api_cars():
    user, error = require_user()
    if error:
        return error
    return jsonify({"ok": True, "default_car": DEFAULT_CAR_ID, "cars": all_car_snapshots()})


@app.route("/api/handshake", methods=["POST"])
def api_handshake():
    user, error = require_can_connect()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    requested = data.get("car") or request.args.get("car") or DEFAULT_CAR_ID
    car_ids = list(CAR_CONFIGS) if requested == "all" else [normalize_car_id(requested)]
    results = {}
    for car_id in car_ids:
        slot = init_car(car_id)
        results[car_id] = slot["car"].send_handshake(count=2)
    return jsonify({"ok": all(results.values()), "results": results})


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
        elif action == "admin_override":
            lobby["admin_override"] = bool(data.get("value", True))
            if lobby["admin_override"]:
                send_neutral("admin override engaged")
        elif action == "set_max_speed":
            lobby["max_speed_percent"] = clamp_int(data.get("value"), MAX_REMOTE_SPEED_PERCENT, 5, 100)
        elif action == "set_session_duration":
            lobby["session_duration"] = clamp_int(data.get("value"), DEFAULT_DRIVE_SECONDS, 15, 3600)
            if lobby["active_driver"]:
                lobby["driver_started_at"] = time.time()
        # ---- Phase 4: race-director actions (ROADMAP D8: manual RD control)
        elif action == "race_start":
            if not race.start():
                return jsonify({"ok": False, "error": "race_not_startable"}), 409
            send_neutral("race countdown — cars held neutral")
        elif action == "race_green":
            if not race.green_now():
                return jsonify({"ok": False, "error": "race_not_in_countdown"}), 409
        elif action == "race_finish":
            if not race.finish():
                return jsonify({"ok": False, "error": "race_not_running"}), 409
            send_neutral("race finished")
        elif action == "race_reset":
            race.reset()
        elif action == "race_finish_car":
            target_id = str(data.get("user_id", "")).strip()
            place = race.register_finish(target_id) if target_id else None
            if place is None:
                return jsonify({"ok": False, "error": "finish_not_recorded"}), 400
        elif action == "item_grant":
            target_id = str(data.get("user_id", "")).strip()
            item = str(data.get("item", "")).strip()
            if not target_id or not race.grant_item(target_id, item):
                return jsonify({"ok": False, "error": "item_not_granted"}), 400
        elif action == "item_clear":
            race.clear_effects(str(data.get("user_id", "")).strip())
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
            # expires_at is None until first redemption (dormant) — guard it (AUDIT §6.6)
            to_remove = [c for c, e in guest_codes.items()
                         if not e.get("persistent")
                         and (not e["active"]
                              or (e["expires_at"] is not None and time.time() > e["expires_at"]))]
            for c in to_remove:
                del guest_codes[c]
                removed += 1
    _save_guest_codes()
    return jsonify({"ok": True, "removed": removed})


_rate_lock = threading.Lock()
_rate_events = {}


def rate_limited(key, limit=10, window=60.0):
    """Sliding-window limiter (per key). AUDIT §6.5: redeem/login had none."""
    now = time.time()
    with _rate_lock:
        hits = [t for t in _rate_events.get(key, ()) if now - t < window]
        if len(hits) >= limit:
            _rate_events[key] = hits
            return True
        hits.append(now)
        _rate_events[key] = hits
        if len(_rate_events) > 4096:  # keep the table bounded
            for k in [k for k, v in _rate_events.items()
                      if not v or now - v[-1] >= window]:
                del _rate_events[k]
    return False


@app.route("/api/redeem-code", methods=["POST"])
def api_redeem_code():
    """Guest: redeem a drive code to get driver access."""
    if rate_limited(f"redeem:{client_ip()}", limit=10, window=60):
        return jsonify({"ok": False, "error": "too_many_attempts"}), 429
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"ok": False, "error": "no_code_provided"}), 400
    user, error = redeem_guest_code(code)
    if error:
        return jsonify({"ok": False, "error": error}), 403
    session["user"] = user
    session["is_guest"] = True
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
    """Temporary: generate a code. Admin session OR true-local (REVIEW SRV-1:
    was local-only, but the tunnel made everyone 'local')."""
    user, error = require_admin()
    if error and not local_request():
        return error
    code = generate_guest_code(30)
    return jsonify({"ok": True, "code": code})


@app.route("/api/guest/test", methods=["GET"])
def api_guest_test():
    """Debug helper — disabled unless ENABLE_TEST_CODES=1 (AUDIT §6.3:
    this endpoint used to hand out the admin code to anyone)."""
    if os.environ.get("ENABLE_TEST_CODES") != "1":
        return jsonify({"ok": False, "error": "disabled"}), 404
    return jsonify({
        "ok": True,
        "known_codes_count": len(guest_codes),
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
    car_id = normalize_car_id(request.args.get("car"))
    slot = init_car(car_id)
    car = slot["car"]
    decoder = slot["decoder"]
    success = car.connect()
    if success:
        decoder.start()
    broadcast_lobby()
    return jsonify({"ok": success, "car": car_id, "state": car.state.value})


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    user, error = require_can_connect()
    if error:
        return error
    car_id = normalize_car_id(request.args.get("car"))
    slot = init_car(car_id)
    car = slot["car"]
    decoder = slot["decoder"]
    if decoder:
        decoder.stop()
    if car:
        car.disconnect()
    broadcast_lobby()
    return jsonify({"ok": True, "car": car_id, "state": "disconnected"})


@app.route("/api/status")
def api_status():
    # AUDIT §6.3: anonymous status leaked car IP, SSID, traffic stats
    user, error = require_user()
    if error:
        return error
    car_id = normalize_car_id(request.args.get("car"))
    slot = init_car(car_id)
    car = slot["car"]
    decoder = slot["decoder"]
    status = car.get_status()
    status["decoder_frames"] = decoder.frame_count if decoder else 0
    status["decoder_codec"] = decoder.codec_name if decoder else None
    status["car"] = car_id
    status["label"] = CAR_CONFIGS[car_id]["label"]
    status["ssid"] = CAR_CONFIGS[car_id]["ssid"]
    status.update(latency_stats())
    return jsonify(status)


@app.route("/api/logs")
def api_logs():
    user, error = require_user()
    if error:
        return error
    car_id = normalize_car_id(request.args.get("car"))
    slot = init_car(car_id)
    logs = slot["car"].get_logs()
    return jsonify({"logs": logs})


@app.route("/api/command", methods=["POST"])
def api_command():
    user, error = require_user()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    data.setdefault("car", request.args.get("car"))
    rx_ts = time.time()
    result = handle_control_command(user, data, rx_ts=rx_ts)
    note_control_rx(data, rx_ts)
    if result.get("ok"):
        status = 200
    elif result.get("error") in ("unauthorized_driver", "emergency_stop", "admin_override"):
        status = 403
    elif result.get("error") == "invalid_command":
        status = 400
    else:
        status = 503  # transport/car-side failure, not an auth failure
    return jsonify(result), status


@app.route("/api/lights", methods=["POST"])
def api_lights():
    user, error = require_user()
    if error:
        return error
    with state_lock:
        if not validate_control_user(user):
            return jsonify({"ok": False, "error": "not_active_driver"}), 403
    car_id = normalize_car_id(request.args.get("car"))
    slot = init_car(car_id)
    data = request.get_json(silent=True) or {}
    on = bool(data.get("on", True))
    success = slot["car"].toggle_lights(on=on)
    return jsonify({"ok": success, "car": car_id, "lights": on})


@app.route("/api/send_raw", methods=["POST"])
def api_send_raw():
    user, error = require_admin()
    if error:
        return error
    if not local_request():
        return jsonify({"ok": False, "error": "raw_sender_local_only"}), 403
    data = request.get_json(silent=True) or {}
    car_id = normalize_car_id(data.get("car") or request.args.get("car"))
    slot = init_car(car_id)
    car = slot["car"]
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


def _video_token_signer():
    return SecureCookieSessionInterface().get_signing_serializer(app)


def video_token_for(user_id, car_id):
    return _video_token_signer().dumps(
        {"vid_uid": user_id, "vid_car": car_id, "vid_iat": time.time()})


def video_token_verify(token):
    try:
        data = _video_token_signer().loads(token)
    except Exception:
        return None
    if not data or "vid_uid" not in data:
        return None
    if time.time() - float(data.get("vid_iat", 0)) > 3600:
        return None
    return data


@app.route("/api/video-token")
def api_video_token():
    """Short-lived signed token for the WebCodecs relay websocket.

    The browser cannot attach cookies reliably to WS on every platform and
    a fresh per-connection token avoids long-lived credentials in URLs.
    """
    user, error = require_user()
    if error:
        return error
    car_id = normalize_car_id(request.args.get("car"))
    slot = init_car(car_id)
    return jsonify({
        "ok": True,
        "token": video_token_for(user["id"], car_id),
        "codec": slot["relay"].codec,          # None until first frame seen
        "relay_frames": slot["relay"].frames_relayed,
    })


@sock.route("/ws/video/<car_id>")
def ws_video(ws, car_id):
    """Raw Annex-B H.264 -> browser WebCodecs. Auth = signed video token."""
    car_id = normalize_car_id(car_id)
    data = video_token_verify(request.args.get("token", ""))
    if not data:
        ws.send("fpv-auth-error")
        ws.close()
        return
    uid = data["vid_uid"]
    if data.get("vid_car") != car_id or is_banned_user(uid):
        ws.send("fpv-auth-error")
        ws.close()
        return
    guest_info = active_guest_sessions.get(uid)
    if uid.startswith("guest-"):
        # REVIEW SRV-5 (2026-09-06): a guest stream needs a LIVE session —
        # kicked/expired guests lose their active_guest_sessions entry, and a
        # stale signed token must not outlive it.
        if guest_info is None or time.time() > guest_info["expires_at"]:
            ws.send("fpv-auth-error")
            ws.close()
            return
    elif not is_allowed_user(uid):
        ws.send("fpv-auth-error")
        ws.close()
        return
    slot = init_car(car_id)
    relay = slot["relay"]
    sub = relay.subscribe()
    last_auth_check = time.time()
    try:
        ws.send(b"fpv-meta:" + json.dumps(
            {"codec": relay.codec or "h264", "car": car_id}).encode())
        while True:
            try:
                frame = relay.poll(sub, timeout=0.5)
            except Exception:
                frame = None  # poll timeout
            if frame is not None:
                ws.send(frame)  # bytes -> binary frame (flask-sock auto-detects)
            if time.time() - last_auth_check > 5:
                last_auth_check = time.time()
                # REVIEW SRV-5: re-check LIVE state, not the connect-time
                # snapshot — kick/expire purge active_guest_sessions.
                revoked = is_banned_user(uid) or (
                    uid.startswith("guest-") and uid not in active_guest_sessions)
                if revoked:
                    ws.send("fpv-auth-revoked")
                    break
    except Exception:
        pass  # client disconnect
    finally:
        relay.unsubscribe(sub)


@app.route("/api/stream")
def api_stream():
    user, error = require_user()
    if error:
        return error
    car_id = normalize_car_id(request.args.get("car"))
    slot = init_car(car_id)
    decoder = slot["decoder"]

    is_admin = user.get("role") == "admin"
    sid = f"{car_id}:{request.args.get('sid', str(id(request)))}"

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
    sid = f"{normalize_car_id(data.get('car') or request.args.get('car'))}:{data.get('sid', str(id(request)))}"

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
    sid = f"{normalize_car_id(data.get('car') or request.args.get('car'))}:{data.get('sid', str(id(request)))}"

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
    rx_ts = time.time()
    user = current_user()
    if not user:
        emit("control:ack", {"ok": False, "error": "login_required"})
        return
    result = handle_control_command(user, data or {}, rx_ts=rx_ts)
    note_control_rx(data, rx_ts)
    emit("control:ack", dict(result, echo_ts=(data or {}).get("client_ts"),
                             server_ts=rx_ts))


@socketio.on("control:rtt")
def ws_control_rtt(data):
    """Clock-sync/RTT ping: client measures rtt = now - t0 and derives
    server<->client clock offset from (server_ts - t0 - rtt/2)."""
    raw = (data or {}).get("t0")
    t0 = None
    if raw is not None:
        try:
            t0 = float(raw)
        except (TypeError, ValueError):
            t0 = None
    emit("control:rtt:ack", {"t0": t0, "server_ts": time.time()})


if __name__ == "__main__":
    ensure_timer()
    for configured_car_id in CAR_CONFIGS:
        init_car(configured_car_id)
    if os.environ.get("AUTO_CONNECT_CAR", "").lower() in {"1", "true", "yes"}:
        for configured_car_id, slot in car_slots.items():
            try:
                car = slot["car"]
                decoder = slot["decoder"]
                success = car.connect()
                if success:
                    decoder.start()
                    print(f"  {configured_car_id} connected: {car.state.value}")
                else:
                    print(f"  {configured_car_id} connection failed: {car.state.value}")
            except Exception as e:
                print(f"  {configured_car_id} connect error: {e}")
    print("=" * 60)
    print("  WLtoys FPV Car - Race Lobby Cockpit")
    print("  http://localhost:5555")
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=env_int("PORT", 5555, 1, 65535), debug=False, allow_unsafe_werkzeug=True)
