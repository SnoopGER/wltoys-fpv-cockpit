"""Virtual Race engine — pseudo-3D Turbo OutRun-style mini-game server.

ROADMAP decisions (2026-09-06/07, Snoop + Bunny + Riko):
- D16: custom clone, NOT a ROM emulator (netplay + item injection into a
  black box is worse; ROM redistribution is also a legal non-starter).
- D17: road model forked from jakesgordon/javascript-racer (MIT) — client
  side. THIS module is the authority: physics, items, collisions, race state.
- D18: pseudo-3D free-steer — per-car state is (track_pos, lane). No full 3D.
- D19: sim speed is FREE (not tied to the real-car governor, which lives in
  race_engine.py and bounds only physical cars). Fairness = identical car
  model for everyone + item cooldowns.
- D20: item semantics mirror race_engine.ITEMS safety rules: soft effects,
  red shell = time-boxed ONE-SIDED steering bias + brake pulse, NEVER a
  control inversion; MAX_DURATION ceiling.

The engine is pure state: no sockets, no Flask, no wall clock assumptions.
webapp.py feeds it inputs and broadcasts snapshots. Clock + rng injectable.
Coordinates: track_pos is an INCREASING distance in meters (never wrapped),
so traps/shells placed absolutely can never double-hit after a lap.
Snapshots expose wrapped positions for display.
"""
import random
import time

IDLE = "idle"
COUNTDOWN = "countdown"
GREEN = "green"
FINISHED = "finished"

#: hard safety ceiling for any timed effect (mirrors race_engine.MAX_DURATION)
MAX_DURATION = 10.0

CAR_LEN = 1.6          # meters
LANE_HIT_WIDTH = 0.34  # lane-unit overlap for collisions (car is ~0.30 wide)

ITEM_IDS = ("boost", "redshell", "banana", "star")


class VirtualRace:
    def __init__(self, track_length=2000.0, laps=3, top_speed=60.0,
                 accel=15.0, brake_dec=60.0, steer_rate=1.4,
                 npc_count=8, boost_seconds=5.0, boost_cooldown=15.0,
                 boost_factor=1.75, shell_flight_speed=200.0,
                 shell_cooldown=8.0, banana_cooldown=3.0,
                 star_cooldown=15.0, silence_timeout=5.0,
                 clock=time.monotonic, rng=None):
        self.clock = clock
        self.rng = rng or random.Random(7)
        self.track_length = float(track_length)
        self.laps = int(laps)
        self.top_speed = float(top_speed)
        self.accel = float(accel)
        self.brake_dec = float(brake_dec)
        self.steer_rate = float(steer_rate)
        self.boost_seconds = min(float(boost_seconds), MAX_DURATION)
        self.boost_cooldown = max(0.0, float(boost_cooldown))
        self.boost_factor = max(1.0, float(boost_factor))
        self.shell_flight_speed = float(shell_flight_speed)
        self.item_cooldowns = {"redshell": float(shell_cooldown),
                               "banana": float(banana_cooldown),
                               "star": float(star_cooldown)}
        self.silence_timeout = float(silence_timeout)

        self.state = IDLE
        self.state_since = self.clock()
        self.countdown_until = None
        self.last_tick = self.clock()

        self.cars = {}        # uid -> car dict
        self.npcs = []        # NPC dicts (wrapped coords)
        self.shells = []      # in-flight red shells
        self.traps = []       # dropped bananas (absolute coords)
        self.results = []     # [{user, place, finish_ts, dnf}]
        self._events = []
        self._pair_slow_until = {}   # (a,b) -> ts, spam guard for car-car
        self._npc_slow_until = {}    # (uid,npc) -> ts

        for i in range(npc_count):
            self.npcs.append({
                "id": "npc%d" % i,
                "track_pos": (i + 0.5) * (self.track_length / max(1, npc_count)),
                "lane": [-0.66, 0.0, 0.66][i % 3],
                "target_lane": [-0.66, 0.0, 0.66][i % 3],
                "speed": 24.0 + self.rng.random() * 14.0,
                "next_change_at": self.clock() + 4.0 + self.rng.random() * 6.0,
            })

    # ------------------------------------------------------------- roster

    def add_player(self, uid, name, color=None):
        """Register (or re-attach) a player. Idempotent; reconnect keeps the
        car: D21 rejoin semantics = same uid gets the same car back."""
        car = self.cars.get(uid)
        if car is None:
            self.cars[uid] = {
                "id": uid, "name": name,
                "color": color or "#00e5ff",
                "track_pos": 0.0, "lane": 0.0, "speed": 0.0,
                "throttle": 0.0, "steer": 0.0, "brake": False,
                "laps_done": 0, "place": None, "finish_ts": None,
                "dnf": False, "connected": True,
                "last_input_at": self.clock(),
                "effects": {},   # item -> {"until": t, "params": {}}
                "cooldowns": {}, # item -> ready_at
            }
        else:
            car["connected"] = True
            car["last_input_at"] = self.clock()
        return self.cars[uid]

    def mark_disconnected(self, uid):
        car = self.cars.get(uid)
        if car:
            car["connected"] = False

    # -------------------------------------------------------------- states

    @property
    def active(self):
        return self.state in (COUNTDOWN, GREEN)

    def start(self):
        if self.state not in (IDLE, FINISHED):
            return False
        now = self.clock()
        self.results = []
        self.shells = []
        self.traps = []
        for car in self.cars.values():
            car.update(track_pos=0.0, lane=0.0, speed=0.0, throttle=0.0,
                       steer=0.0, brake=False, laps_done=0, place=None,
                       finish_ts=None, dnf=False, effects={}, cooldowns={},
                       last_input_at=now)
        for i, npc in enumerate(self.npcs):
            npc["track_pos"] = (i + 0.5) * (self.track_length / max(1, len(self.npcs)))
            npc["speed"] = 24.0 + self.rng.random() * 14.0
        self.state = COUNTDOWN
        self.state_since = now
        self.countdown_until = now + 3.0
        return True

    def green_now(self):
        if self.state != COUNTDOWN:
            return False
        self.state = GREEN
        self.state_since = self.clock()
        self.countdown_until = None
        return True

    def finish(self):
        """green/countdown -> finished; unfinished racers are DNF."""
        if self.state not in (COUNTDOWN, GREEN):
            return False
        self.state = FINISHED
        self.state_since = self.clock()
        self.countdown_until = None
        for car in self.cars.values():
            if car["place"] is None:
                car["dnf"] = True
                self._record_place(car)
        return True

    def reset(self):
        self.state = IDLE
        self.state_since = self.clock()
        self.countdown_until = None
        self.results = []
        self.shells = []
        self.traps = []
        for car in self.cars.values():
            car.update(track_pos=0.0, lane=0.0, speed=0.0, effects={},
                       cooldowns={}, laps_done=0, place=None, finish_ts=None,
                       dnf=True)

    def _tick_state(self):
        if self.state == COUNTDOWN and self.countdown_until is not None \
                and self.clock() >= self.countdown_until:
            self.state = GREEN
            self.state_since = self.clock()
            self.countdown_until = None
            self._emit({"type": "green"})
            return True
        return False

    # --------------------------------------------------------------- input

    def apply_input(self, uid, data):
        """Validate + queue one input frame (steer -1..1, throttle 0..1).
        Returns (ok, error). Guards mirror the SRV-6 payload rules."""
        if not isinstance(data, dict):
            return False, "invalid_payload"
        car = self.cars.get(uid)
        if car is None:
            return False, "no_car"
        try:
            steer = float(data.get("steer", 0.0))
            throttle = float(data.get("throttle", 0.0))
        except (TypeError, ValueError):
            return False, "invalid_payload"
        car["steer"] = max(-1.0, min(1.0, steer))
        car["throttle"] = max(0.0, min(1.0, throttle))
        car["brake"] = bool(data.get("brake"))
        car["last_input_at"] = self.clock()
        item = data.get("item")
        if item is not None:
            ok, err = self.use_item(uid, item)
            if not ok:
                return False, err
        return True, None

    def use_item(self, uid, item):
        car = self.cars.get(uid)
        if car is None or item not in ITEM_IDS:
            return False, "invalid_item"
        if self.state != GREEN:
            return False, "race_not_green"
        now = self.clock()
        ready = car["cooldowns"].get(item, 0.0)
        if now < ready:
            return False, "cooldown"
        if item == "boost":
            self._apply(car, "boost", self.boost_seconds,
                        {"throttle_factor": self.boost_factor})
            car["cooldowns"]["boost"] = now + self.boost_seconds + self.boost_cooldown
            self._emit({"type": "item", "item": "boost", "by": uid})
            return True, None
        if item == "star":
            self._apply(car, "star", 5.0,
                        {"immune": True, "throttle_factor": 1.1})
            car["cooldowns"]["star"] = now + self.item_cooldowns["star"]
            self._emit({"type": "item", "item": "star", "by": uid})
            return True, None
        if item == "banana":
            self.traps.append({"pos": car["track_pos"], "lane": car["lane"],
                               "owner": uid})
            car["cooldowns"]["banana"] = now + self.item_cooldowns["banana"]
            self._emit({"type": "item", "item": "banana", "by": uid})
            return True, None
        if item == "redshell":
            target = self._closest_ahead(uid)
            if target is None:
                return False, "no_target"
            dz = target["track_pos"] - car["track_pos"]
            flight = min(3.0, max(0.8, dz / self.shell_flight_speed)) + 0.6
            self.shells.append({"shooter": uid, "target": target["id"],
                                "impact_at": now + flight})
            car["cooldowns"]["redshell"] = now + self.item_cooldowns["redshell"]
            self._emit({"type": "shell_launched", "by": uid,
                        "target": target["id"]})
            return True, None
        return False, "invalid_item"

    def _closest_ahead(self, uid):
        shooter = self.cars.get(uid)
        if shooter is None:
            return None
        best, best_dz = None, None
        for other in self.cars.values():
            if other["id"] == uid or other["place"] is not None:
                continue
            dz = other["track_pos"] - shooter["track_pos"]
            if dz > 0 and (best_dz is None or dz < best_dz):
                best, best_dz = other, dz
        return best

    # ------------------------------------------------------------- effects

    def _apply(self, car, item, duration, params):
        duration = min(float(duration), MAX_DURATION)
        car["effects"][item] = {"until": self.clock() + duration,
                                "params": dict(params)}

    def _prune(self, car):
        now = self.clock()
        for k in [k for k, v in car["effects"].items() if now >= v["until"]]:
            del car["effects"][k]

    def _immune(self, car):
        self._prune(car)
        return "immune" in car["effects"].get("star", {}).get("params", {})

    def _effect_factors(self, car):
        """Mirror race_engine.modify combination rules: beneficial factors
        take max, harmful take min; steer_limit clamps; steer_lock pins."""
        self._prune(car)
        throttle_factor = 1.0
        steer_limit = None
        steer_lock = None
        for item, data in car["effects"].items():
            p = data["params"]
            if "throttle_factor" in p:
                if item in ("boost", "star"):
                    throttle_factor = max(throttle_factor, p["throttle_factor"])
                else:
                    throttle_factor = min(throttle_factor, p["throttle_factor"])
            if "steer_limit" in p:
                steer_limit = min(steer_limit if steer_limit is not None else 1.0,
                                  p["steer_limit"])
            if "steer_lock" in p:
                steer_lock = p["steer_lock"]
        return throttle_factor, steer_limit, steer_lock

    # ---------------------------------------------------------------- tick

    def tick(self):
        """Advance the world by the clock delta since the previous tick."""
        now = self.clock()
        dt = max(0.0, min(0.25, now - self.last_tick))
        self.last_tick = now
        self._tick_state()
        if self.state == COUNTDOWN:
            for car in self.cars.values():
                car["speed"] = 0.0
            return
        if self.state != GREEN:
            return

        for car in self.cars.values():
            self._step_car(car, dt, now)
        self._step_npcs(dt, now)
        self._step_shells(now)
        self._step_traps(now)
        self._step_collisions(now)
        self._check_race_end()

    def _step_car(self, car, dt, now):
        # silence watchdog (D21): no input for N seconds -> neutral coast.
        silent = (now - car["last_input_at"]) > self.silence_timeout
        throttle = 0.0 if silent else car["throttle"]
        steer = 0.0 if silent else car["steer"]
        brake = False if silent else car["brake"]

        t_factor, s_limit, s_lock = self._effect_factors(car)
        if s_lock is not None:
            # ONE-SIDED bias (never through center, never inverted): pin a
            # bounded right turn for the effect window.
            steer = max(steer, 0.45) if steer >= 0 else 0.45
        if s_limit is not None:
            steer = max(-s_limit, min(s_limit, steer))

        target = throttle * self.top_speed * t_factor
        if brake:
            car["speed"] = max(0.0, car["speed"] - self.brake_dec * dt)
        elif car["speed"] < target:
            car["speed"] = min(target, car["speed"] + self.accel * dt)
        elif car["speed"] > target:
            car["speed"] = max(target, car["speed"] - self.accel * dt)
        car["speed"] = max(0.0, car["speed"])
        car["lane"] = max(-1.0, min(1.0, car["lane"] + steer * self.steer_rate * dt))

        prev = car["track_pos"]
        car["track_pos"] = prev + car["speed"] * dt
        while car["track_pos"] >= (car["laps_done"] + 1) * self.track_length:
            car["laps_done"] += 1
            if car["laps_done"] >= self.laps and car["place"] is None:
                self._record_place(car)
                self._emit({"type": "finish", "by": car["id"],
                            "place": car["place"]})
            else:
                self._emit({"type": "lap", "by": car["id"],
                            "lap": car["laps_done"]})

    def _record_place(self, car):
        car["place"] = len(self.results) + 1
        car["finish_ts"] = self.clock()
        self.results.append({"user": car["id"], "place": car["place"],
                             "finish_ts": car["finish_ts"],
                             "dnf": car.get("dnf", False)})

    def _step_npcs(self, dt, now):
        for npc in self.npcs:
            if now >= npc["next_change_at"]:
                lanes = [-0.66, 0.0, 0.66]
                npc["target_lane"] = lanes[self.rng.randrange(3)]
                npc["next_change_at"] = now + 4.0 + self.rng.random() * 6.0
            d = npc["target_lane"] - npc["lane"]
            step = max(-0.5 * dt, min(0.5 * dt, d))
            npc["lane"] += step
            npc["track_pos"] = (npc["track_pos"] + npc["speed"] * dt) \
                % self.track_length

    def _step_shells(self, now):
        for shell in list(self.shells):
            if now < shell["impact_at"]:
                continue
            self.shells.remove(shell)
            victim = self.cars.get(shell["target"])
            if victim is None or victim["place"] is not None:
                self._emit({"type": "shell_missed", "target": shell["target"]})
                continue
            if self._immune(victim):
                self._emit({"type": "shell_missed", "target": shell["target"],
                            "immune": True})
                continue
            self._apply(victim, "redshell", 1.0,
                        {"steer_lock": 30, "throttle_factor": 0.3})
            self._emit({"type": "shell_impact", "by": shell["shooter"],
                        "target": victim["id"]})

    def _step_traps(self, now):
        for trap in list(self.traps):
            for car in self.cars.values():
                if car["place"] is not None:
                    continue
                if trap["owner"] == car["id"] \
                        and car["track_pos"] < trap["pos"] + self.track_length:
                    continue  # owner can only be caught after a full lap
                if abs(car["track_pos"] - trap["pos"]) < 1.3 \
                        and abs(car["lane"] - trap["lane"]) < LANE_HIT_WIDTH:
                    self.traps.remove(trap)
                    if self._immune(car):
                        break
                    self._apply(car, "banana", 3.0,
                                {"steer_limit": 0.35, "throttle_factor": 0.6})
                    self._emit({"type": "banana_hit", "by": car["id"]})
                    break

    def _step_collisions(self, now):
        # car vs NPC (NPC coords are wrapped; compare wrapped car pos)
        for car in self.cars.values():
            if car["place"] is not None:
                continue
            wrapped = car["track_pos"] % self.track_length
            for npc in self.npcs:
                key = (car["id"], npc["id"])
                if now < self._npc_slow_until.get(key, 0.0):
                    continue
                dz = self._wrapped_dz(wrapped, npc["track_pos"])
                if abs(dz) < CAR_LEN and abs(car["lane"] - npc["lane"]) < LANE_HIT_WIDTH:
                    self._npc_slow_until[key] = now + 0.7
                    if not self._immune(car):
                        car["speed"] = min(car["speed"], npc["speed"]) * 0.75
                    self._emit({"type": "hit_npc", "by": car["id"],
                                "npc": npc["id"],
                                "immune": self._immune(car)})
        # car vs car (players)
        cars = [c for c in self.cars.values() if c["place"] is None]
        cars.sort(key=lambda c: c["track_pos"])
        for i, a in enumerate(cars):
            for b in cars[i + 1:]:
                if b["track_pos"] - a["track_pos"] > CAR_LEN:
                    break
                if abs(a["lane"] - b["lane"]) >= LANE_HIT_WIDTH - 0.04:
                    continue
                key = tuple(sorted((a["id"], b["id"])))
                if now < self._pair_slow_until.get(key, 0.0):
                    continue
                self._pair_slow_until[key] = now + 1.0
                slow = min(a["speed"], b["speed"]) * 0.85
                if not self._immune(a):
                    a["speed"] = min(a["speed"], slow)
                if not self._immune(b):
                    b["speed"] = min(b["speed"], slow)
                self._emit({"type": "hit_car", "a": a["id"], "b": b["id"]})

    def _wrapped_dz(self, a, b):
        d = (a - b) % self.track_length
        return d - self.track_length if d > self.track_length / 2 else d

    def _check_race_end(self):
        racers = [c for c in self.cars.values()]
        if racers and all(c["place"] is not None for c in racers):
            self.state = FINISHED
            self.state_since = self.clock()
            self._emit({"type": "race_over"})

    # ------------------------------------------------------------ snapshots

    def positions(self):
        def key(c):
            if c["place"] is not None:
                return (2, -c["place"])
            return (1, c["track_pos"])
        return {c["id"]: rank
                for rank, c in enumerate(sorted(self.cars.values(), key=key,
                                                reverse=True), 1)}

    def snapshot(self, drain=True):
        """Full world snapshot ~10 Hz. Events drain once per snapshot call —
        the broadcast loop is the consumer; join-time snapshots pass
        drain=False so they cannot swallow broadcast events."""
        now = self.clock()
        self._tick_state()
        ranks = self.positions()
        incoming = {}
        for shell in self.shells:
            eta = round(max(0.0, shell["impact_at"] - now), 1)
            prev = incoming.get(shell["target"])
            incoming[shell["target"]] = eta if prev is None else min(prev, eta)
        cars = []
        for c in self.cars.values():
            self._prune(c)
            cars.append({
                "id": c["id"], "name": c["name"], "color": c["color"],
                "pos": round(c["track_pos"] % self.track_length, 2),
                "total_pos": round(c["track_pos"], 2),
                "lane": round(c["lane"], 3),
                "speed": round(c["speed"], 2),
                "kmh": round(c["speed"] * 3.6, 1),
                "lap": min(c["laps_done"] + 1, self.laps),
                "place": c["place"], "rank": ranks.get(c["id"]),
                "dnf": c["dnf"], "connected": c["connected"],
                "items": sorted(c["effects"].keys()),
                "cooldowns": {item: round(max(0.0, ready - now), 1)
                              for item, ready in c["cooldowns"].items()
                              if ready > now},
                "incoming_shell_in": incoming.get(c["id"]),
            })
        return {
            "t": round(now, 3),
            "state": self.state,
            "countdown_remaining": round(max(
                0.0, self.countdown_until - now), 1)
                if self.state == COUNTDOWN and self.countdown_until else None,
            "track_length": self.track_length,
            "laps": self.laps,
            "cars": cars,
            "npcs": [{"id": n["id"], "pos": round(n["track_pos"], 2),
                      "lane": round(n["lane"], 3),
                      "speed": round(n["speed"], 2)} for n in self.npcs],
            "traps": [{"pos": round(t["pos"] % self.track_length, 2),
                       "lane": round(t["lane"], 3)} for t in self.traps],
            "results": list(self.results),
            "events": self._drain_events() if drain else list(self._events),
        }

    def _emit(self, event):
        event["t"] = round(self.clock(), 3)
        self._events.append(event)

    def _drain_events(self):
        out, self._events = self._events, []
        return out
