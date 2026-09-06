"""Server-authoritative race engine — ROADMAP Phase 4 core.

Design rules (locked in ROADMAP decision log):
- D6: all race state + item effects live HERE, never in the browser.
- D4/D15: during a race the governor caps throttle (default 70%); 🍄 boost
  unlocks 100% car power for RACE_BOOST_SECONDS (5s), then a 15s cooldown.
  The lobby/free-drive cap does NOT apply during a race — the engine owns it.
- D5/D9: SOFT safety only. Banana = steer-angle limit + mild slowdown.
  Red shell = time-boxed one-sided steering lock (max ~1s) + brake pulse.
  NO full control inversion, ever.
- D8: race director (admin) triggers items manually; no telemetry needed.

The engine is pure state: it never touches sockets, Flask, or the car.
webapp.py calls `modify()` per control command and applies the result.
Clock is injectable for deterministic tests.
"""
import time

IDLE = "idle"
COUNTDOWN = "countdown"
GREEN = "green"
FINISHED = "finished"

#: item -> (duration seconds, params). Durations are safety-bounded below.
ITEMS = {
    # boost params are parameterized per-engine (Snoop 2026-09-06):
    # default 5s unlock to 100% power, 15s cooldown before re-use
    "boost":  (5.0, {"max_throttle": 100}),
    # banana: steer angle limited + throttle reduced (soft, never reversed)
    "banana": (3.0, {"steer_limit": 35, "throttle_factor": 0.6}),
    # red shell impact: time-boxed one-sided steering lock + brake pulse
    "redshell": (1.0, {"steer_lock": 30, "throttle_factor": 0.3}),
    # star: immunity + slight throttle edge (later phase, defined here)
    "star":   (5.0, {"immune": True, "throttle_factor": 1.1}),
}

MAX_DURATION = 10.0  # hard safety ceiling for any effect


class RaceEngine:
    def __init__(self, governor_percent=70, boost_seconds=5.0,
                 boost_cooldown=15.0, boost_max=100, clock=time.monotonic):
        self.clock = clock
        self.governor_percent = max(5, min(100, int(governor_percent)))
        # D15 (2026-09-06, Snoop): during a race the ceiling lives HERE —
        # governor limits everyone; boost releases to boost_max (100% power).
        self.boost_seconds = min(float(boost_seconds), MAX_DURATION)
        self.boost_cooldown = max(0.0, float(boost_cooldown))
        self.boost_max = max(5, min(100, int(boost_max)))
        self.state = IDLE
        self.state_since = self.clock()
        self.countdown_until = None
        self.results = []            # [{"user": id, "finish_ts": t, "place": n}]
        self._effects = {}           # user_id -> {item: {"until": t, "params": ...}}
        self._cooldowns = {}         # (user_id, "boost") -> ready_at
        self._immune_until = {}      # deprecated placeholder (star handled via effects)

    # ------------------------------------------------------------------ state

    @property
    def active(self):
        return self.state in (COUNTDOWN, GREEN)

    def start(self):
        """idle/finished -> countdown (3s) -> green via tick()."""
        if self.state not in (IDLE, FINISHED):
            return False
        self.results = []
        self._effects.clear()
        self._cooldowns.clear()
        self.state = COUNTDOWN
        self.state_since = self.clock()
        self.countdown_until = self.clock() + 3.0
        return True

    def green_now(self):
        if self.state != COUNTDOWN:
            return False
        self.state = GREEN
        self.state_since = self.clock()
        self.countdown_until = None
        return True

    def finish(self):
        """green/countdown -> finished. Clears every effect immediately."""
        if self.state not in (COUNTDOWN, GREEN):
            return False
        self.state = FINISHED
        self.state_since = self.clock()
        self.countdown_until = None
        self._effects.clear()
        return True

    def reset(self):
        self.state = IDLE
        self.state_since = self.clock()
        self.countdown_until = None
        self.results = []
        self._effects.clear()
        self._cooldowns.clear()

    def tick(self):
        """Advance countdown -> green. Returns True if state changed."""
        if self.state == COUNTDOWN and self.countdown_until is not None \
                and self.clock() >= self.countdown_until:
            self.state = GREEN
            self.state_since = self.clock()
            self.countdown_until = None
            return True
        return False

    def register_finish(self, user_id):
        """RD taps the checkered for one driver. Returns place or None."""
        if self.state not in (GREEN, COUNTDOWN):
            return None
        if any(r["user"] == user_id for r in self.results):
            return None
        place = len(self.results) + 1
        self.results.append({"user": user_id, "finish_ts": self.clock(),
                             "place": place})
        return place

    # ----------------------------------------------------------------- items

    def grant_item(self, user_id, item):
        if item not in ITEMS:
            return False
        if item == "boost":
            if self.clock() < self._cooldowns.get((user_id, "boost"), 0.0):
                return False  # boost cooldown active
            duration = self.boost_seconds
            params = {"max_throttle": self.boost_max}
            # cooldown spans the boost itself + the recovery window
            self._cooldowns[(user_id, "boost")] = (
                self.clock() + duration + self.boost_cooldown)
        else:
            duration, params = ITEMS[item]
            duration = min(duration, MAX_DURATION)
            if item == "redshell" and self._has_effect(user_id, "star"):
                return False  # star immunity
        self._effects.setdefault(user_id, {})[item] = {
            "until": self.clock() + duration, "params": dict(params)}
        return True

    def clear_effects(self, user_id):
        self._effects.pop(user_id, None)

    def _has_effect(self, user_id, item):
        self._prune(user_id)
        return item in self._effects.get(user_id, {})

    def _prune(self, user_id):
        now = self.clock()
        eff = self._effects.get(user_id)
        if not eff:
            return
        for k in [k for k, v in eff.items() if now >= v["until"]]:
            del eff[k]
        if not eff:
            self._effects.pop(user_id, None)

    # ------------------------------------------------------------- hot path

    def modify(self, user_id, command, speed, steer_range):
        """Apply governor + active effects to one outgoing command.

        Returns (command, speed, steer_range, meta). Pure w.r.t. the car;
        call AFTER the lobby/admin speed cap, BEFORE car.send_command().
        `speed`/`steer_range` stay in 0..100 and steering direction is
        NEVER inverted (D5): the worst case is a short one-sided turn.
        """
        self._prune(user_id)
        eff = self._effects.get(user_id, {})
        meta = {"governor": None, "effects": sorted(eff.keys())}

        if not self.active:
            return command, speed, steer_range, meta

        # countdown: nobody moves (even admins — the car must be neutral)
        if self.state == COUNTDOWN:
            return "stop", 0, 0, meta

        # governor (D4): baseline cap unless boost releases it
        cap = self.governor_percent
        throttle_factor = 1.0
        steer_limit = None
        steer_lock = None
        for item, data in eff.items():
            p = data["params"]
            if item == "boost":
                cap = max(cap, p["max_throttle"])
            if "throttle_factor" in p:
                if item == "boost" or item == "star":
                    throttle_factor = max(throttle_factor, p["throttle_factor"])
                else:
                    throttle_factor = min(throttle_factor, p["throttle_factor"])
            if "steer_limit" in p:
                steer_limit = min(steer_limit if steer_limit is not None else 101,
                                  p["steer_limit"])
            if "steer_lock" in p:
                steer_lock = p["steer_lock"]

        speed = int(round(speed * throttle_factor))
        speed = min(speed, cap)
        meta["governor"] = cap

        if steer_limit is not None:
            # steer_range is a deflection MAGNITUDE (direction is in
            # `command`), so limiting = clamping how far off center it goes
            steer_range = min(steer_range, steer_limit)
        if steer_lock is not None:
            # spin := time-boxed ONE-SIDED turn (never a flip through
            # center): pin command to a bounded right turn for the effect
            # duration, soft-throttled (D9)
            command = "forward_right"
            steer_range = steer_lock

        speed = max(0, min(100, speed))
        steer_range = max(0, min(100, steer_range))
        return command, speed, steer_range, meta

    # ---------------------------------------------------------------- status

    def snapshot(self):
        self.tick()
        self._prune_all()
        return {
            "state": self.state,
            "governor_percent": self.governor_percent,
            "countdown_remaining": round(max(
                0.0, self.countdown_until - self.clock()), 1)
                if self.state == COUNTDOWN and self.countdown_until else None,
            "elapsed": round(self.clock() - self.state_since, 1),
            "results": list(self.results),
            "active_effects": {
                uid: sorted(self._effects[uid].keys())
                for uid in list(self._effects) if self._effects.get(uid)},
            "boost_cooldown_remaining": {
                uid: round(max(0.0, ready - self.clock()), 1)
                for (uid, kind), ready in self._cooldowns.items()
                if kind == "boost" and ready > self.clock()},
        }

    def _prune_all(self):
        for uid in list(self._effects):
            self._prune(uid)
