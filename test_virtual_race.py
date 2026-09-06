"""Virtual Race engine regression suite (D16-D21).

Hard rules under test:
  * pure state: injectable clock + rng, no sockets, no wall clock
  * countdown locks everyone at 0 speed
  * identical car model for everyone (fairness comes from items, D19)
  * effects NEVER invert steering: red shell worst case is a one-sided
    right bias (same D9 safety rule as race_engine)
  * traps can never double-hit (absolute increasing coords)
  * silence -> neutral coast; reconnect re-attaches the same car (D21)
"""
import random
import unittest

from virtual_race import (
    VirtualRace, IDLE, COUNTDOWN, GREEN, FINISHED, CAR_LEN,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make_race(**kw):
    clock = FakeClock()
    rng = random.Random(1)
    vr = VirtualRace(clock=clock, rng=rng, npc_count=kw.pop("npc_count", 3), **kw)
    return vr, clock


def run(vr, clock, seconds, dt=0.05):
    for _ in range(int(seconds / dt)):
        clock.advance(dt)
        vr.tick()


def drive(vr, clock, seconds, inputs, dt=0.05):
    """Re-apply inputs every tick (proper 20 Hz client cadence, keeps the
    silence watchdog fed). inputs: {uid: {throttle, steer, ...}}."""
    for _ in range(int(seconds / dt)):
        for uid, payload in inputs.items():
            vr.apply_input(uid, dict(payload))
        clock.advance(dt)
        vr.tick()


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.vr, self.clock = make_race()
        self.vr.add_player("a", "A")

    def test_starts_idle(self):
        self.assertEqual(self.vr.state, IDLE)

    def test_start_runs_countdown_then_green(self):
        self.assertTrue(self.vr.start())
        self.assertEqual(self.vr.state, COUNTDOWN)
        run(self.vr, self.clock, 2.0)
        self.assertEqual(self.vr.state, COUNTDOWN)
        run(self.vr, self.clock, 1.5)
        self.assertEqual(self.vr.state, GREEN)

    def test_cannot_start_while_active(self):
        self.vr.start()
        self.assertFalse(self.vr.start())

    def test_countdown_locks_speed(self):
        self.vr.start()
        self.vr.apply_input("a", {"throttle": 1.0, "steer": 0})
        run(self.vr, self.clock, 2.0)
        car = self.vr.cars["a"]
        self.assertEqual(car["speed"], 0.0)
        self.assertEqual(car["track_pos"], 0.0)
        run(self.vr, self.clock, 3.0)   # go green, keep throttle
        self.assertGreater(car["speed"], 0.0)

    def test_admin_finish_assigns_dnfs(self):
        self.vr.add_player("b", "B")
        self.vr.start()
        self.vr.green_now()
        self.assertTrue(self.vr.finish())
        self.assertEqual(self.vr.state, FINISHED)
        dnfs = [r for r in self.vr.results if r["dnf"]]
        self.assertEqual({r["user"] for r in dnfs}, {"a", "b"})


class MovementTests(unittest.TestCase):
    def setUp(self):
        self.vr, self.clock = make_race(npc_count=0)
        self.vr.add_player("a", "A")
        self.vr.add_player("b", "B")
        self.vr.cars["b"]["lane"] = 0.66   # keep lanes apart: no test bumps
        self.vr.start()
        self.vr.green_now()
        self.vr.cars["b"]["lane"] = 0.66

    def test_full_throttle_reaches_top_speed(self):
        drive(self.vr, self.clock, 10, {"a": {"throttle": 1.0}})
        self.assertAlmostEqual(self.vr.cars["a"]["speed"],
                               self.vr.top_speed, delta=0.5)

    def test_identical_models_stay_even(self):
        drive(self.vr, self.clock, 8,
              {"a": {"throttle": 1.0}, "b": {"throttle": 1.0}})
        self.assertAlmostEqual(self.vr.cars["a"]["track_pos"],
                               self.vr.cars["b"]["track_pos"], places=6)

    def test_lane_clamped(self):
        drive(self.vr, self.clock, 5, {"a": {"throttle": 1.0, "steer": -1.0}})
        self.assertGreaterEqual(self.vr.cars["a"]["lane"], -1.0)
        drive(self.vr, self.clock, 5, {"a": {"steer": 999}})  # clamp, not error
        self.assertLessEqual(self.vr.cars["a"]["lane"], 1.0)

    def test_silence_coasts_to_neutral(self):
        self.vr.apply_input("a", {"throttle": 1.0})
        run(self.vr, self.clock, 5)
        self.assertGreater(self.vr.cars["a"]["speed"], 40)
        run(self.vr, self.clock, self.vr.silence_timeout + 4)
        self.assertAlmostEqual(self.vr.cars["a"]["speed"], 0.0, places=3)

    def test_input_guards(self):
        ok, err = self.vr.apply_input("ghost", {"throttle": 1})
        self.assertFalse(ok)
        self.assertEqual(err, "no_car")
        ok, err = self.vr.apply_input("a", "not-a-dict")
        self.assertFalse(ok)
        ok, err = self.vr.apply_input("a", {"throttle": "abc"})
        self.assertFalse(ok)
        ok, err = self.vr.apply_input("a", {"throttle": 5, "steer": -50})
        self.assertTrue(ok)
        self.assertEqual(self.vr.cars["a"]["throttle"], 1.0)
        self.assertEqual(self.vr.cars["a"]["steer"], -1.0)


class ItemTests(unittest.TestCase):
    def setUp(self):
        self.vr, self.clock = make_race(npc_count=0)
        for uid in ("a", "b", "c"):
            self.vr.add_player(uid, uid.upper())
        self.vr.start()
        self.vr.green_now()
        self.vr.cars["b"]["lane"] = 0.66
        self.vr.cars["c"]["lane"] = -0.66

    def test_boost_multiplier_and_duration(self):
        drive(self.vr, self.clock, 10, {"a": {"throttle": 1.0}})
        base = self.vr.cars["a"]["speed"]
        ok, _ = self.vr.use_item("a", "boost")
        self.assertTrue(ok)
        drive(self.vr, self.clock, 4, {"a": {"throttle": 1.0}})
        self.assertGreater(self.vr.cars["a"]["speed"], base * 1.3)
        drive(self.vr, self.clock, 4, {"a": {"throttle": 1.0}})  # past 5s boost
        self.assertAlmostEqual(self.vr.cars["a"]["speed"], base, delta=1.0)

    def test_boost_cooldown_blocks_reuse(self):
        ok, _ = self.vr.use_item("a", "boost")
        self.assertTrue(ok)
        ok, err = self.vr.use_item("a", "boost")
        self.assertFalse(ok)
        self.assertEqual(err, "cooldown")
        run(self.vr, self.clock, self.vr.boost_seconds
            + self.vr.boost_cooldown + 0.1, dt=0.1)
        ok, _ = self.vr.use_item("a", "boost")
        self.assertTrue(ok)

    def test_redshell_targets_closest_ahead(self):
        self.vr.cars["b"]["track_pos"] = 100.0
        self.vr.cars["c"]["track_pos"] = 40.0
        ok, _ = self.vr.use_item("a", "redshell")
        self.assertTrue(ok)
        self.assertEqual(self.vr.shells[0]["target"], "c")

    def test_redshell_leader_has_no_target(self):
        self.vr.cars["b"]["track_pos"] = -100.0
        self.vr.cars["c"]["track_pos"] = -40.0
        ok, err = self.vr.use_item("a", "redshell")
        self.assertFalse(ok)
        self.assertEqual(err, "no_target")

    def test_shell_impact_one_sided_never_inverts(self):
        victim = self.vr.cars["b"]
        victim["track_pos"] = 50.0
        self.vr.use_item("a", "redshell")
        inputs = {"b": {"throttle": 1.0, "steer": -1.0}}
        drive(self.vr, self.clock, 2.0, inputs)   # impact lands at ~1.4s
        self.assertIn("redshell", victim["effects"])
        start_lane = victim["lane"]
        drive(self.vr, self.clock, 0.3, inputs)
        self.assertGreater(victim["lane"], start_lane)  # steering left != going left
        drive(self.vr, self.clock, 1.0, inputs)
        self.assertNotIn("redshell", victim["effects"])  # bounded ~1s

    def test_star_blocks_shell(self):
        victim = self.vr.cars["b"]
        victim["track_pos"] = 50.0
        self.vr.use_item("b", "star")
        self.vr.use_item("a", "redshell")
        run(self.vr, self.clock, 4)
        self.assertNotIn("redshell", victim["effects"])
        missed = [e for e in self.vr.snapshot()["events"]
                  if e["type"] == "shell_missed"]
        self.assertTrue(missed)

    def test_banana_hits_victim_once(self):
        self.vr.cars["a"].update(track_pos=100.0, lane=0.0)
        self.vr.use_item("a", "banana")
        trap = self.vr.traps[0]
        self.assertAlmostEqual(trap["pos"], 100.0)
        victim = self.vr.cars["b"]
        victim.update(track_pos=90.0, lane=0.05)
        self.vr.apply_input("b", {"throttle": 1.0})
        run(self.vr, self.clock, 2)
        self.assertIn("banana", victim["effects"])
        self.assertEqual(len(self.vr.traps), 0)

    def test_banana_owner_cannot_self_hit_same_lap(self):
        self.vr.cars["a"].update(track_pos=100.0, lane=0.0)
        self.vr.use_item("a", "banana")
        self.vr.apply_input("a", {"throttle": 1.0})
        run(self.vr, self.clock, 20)
        self.assertNotIn("banana", self.vr.cars["a"]["effects"])

    def test_invalid_item_and_pre_race_lock(self):
        ok, err = self.vr.use_item("a", "banana_split")
        self.assertFalse(ok)
        self.assertEqual(err, "invalid_item")
        vr2, clock2 = make_race(npc_count=0)
        vr2.add_player("a", "A")
        vr2.start()   # COUNTDOWN
        ok, err = vr2.use_item("a", "boost")
        self.assertFalse(ok)
        self.assertEqual(err, "race_not_green")


class NpcTests(unittest.TestCase):
    def setUp(self):
        self.vr, self.clock = make_race(npc_count=4)
        self.vr.add_player("a", "A")
        self.vr.start()
        self.vr.green_now()

    def test_npcs_wrap_and_stay_on_lane(self):
        run(self.vr, self.clock, 300)
        for npc in self.vr.npcs:
            self.assertGreaterEqual(npc["track_pos"], 0.0)
            self.assertLess(npc["track_pos"], self.vr.track_length)
            self.assertGreaterEqual(npc["lane"], -1.0)
            self.assertLessEqual(npc["lane"], 1.0)

    def test_car_npc_collision_slows_with_event(self):
        car = self.vr.cars["a"]
        npc = self.vr.npcs[0]
        car.update(track_pos=npc["track_pos"] - 5.0, lane=npc["lane"],
                   speed=self.vr.top_speed)
        self.vr.apply_input("a", {"throttle": 1.0})
        run(self.vr, self.clock, 1.0)
        events = [e for e in self.vr._events if e["type"] == "hit_npc"]
        self.assertTrue(events)
        self.assertLess(car["speed"], self.vr.top_speed)

    def test_star_passes_through_npc(self):
        car = self.vr.cars["a"]
        npc = self.vr.npcs[0]
        car.update(track_pos=npc["track_pos"] - 5.0, lane=npc["lane"],
                   speed=self.vr.top_speed)
        self.vr.use_item("a", "star")
        self.vr.apply_input("a", {"throttle": 1.0})
        run(self.vr, self.clock, 1.0)
        self.assertGreater(car["speed"], npc["speed"])


class RaceFlowTests(unittest.TestCase):
    def setUp(self):
        self.vr, self.clock = make_race(npc_count=0, laps=2,
                                        track_length=100.0, top_speed=50.0)
        self.vr.add_player("a", "A")
        self.vr.add_player("b", "B")
        self.vr.start()
        self.vr.green_now()

    def _drive(self, uid, lead=0.0):
        self.vr.apply_input(uid, {"throttle": 1.0})
        if lead:
            self.vr.cars[uid]["track_pos"] = lead

    def test_finish_order_and_race_over(self):
        # put A nearly done, B a lap behind
        self.vr.cars["a"].update(track_pos=195.0, speed=50.0, laps_done=1)
        self.vr.cars["b"].update(track_pos=120.0, speed=45.0, laps_done=1)
        self._drive("a")
        self._drive("b")
        run(self.vr, self.clock, 2.0)
        results = {r["user"]: r for r in self.vr.results}
        self.assertEqual(results["a"]["place"], 1)
        self.assertEqual(results["b"]["place"], 2)
        self.assertEqual(self.vr.state, FINISHED)

    def test_lap_events(self):
        self._drive("a")
        run(self.vr, self.clock, 5.0)
        laps = [e for e in self.vr.snapshot()["events"] if e["type"] == "lap"]
        self.assertTrue(laps)

    def test_reconnect_keeps_same_car(self):
        self.vr.cars["a"].update(track_pos=80.0, speed=40.0)
        self.vr.mark_disconnected("a")
        run(self.vr, self.clock, 7)   # coast to neutral during silence
        car = self.vr.add_player("a", "A")   # re-attach
        self.assertIs(car, self.vr.cars["a"])
        self.assertTrue(car["connected"])
        self.assertGreaterEqual(car["track_pos"], 80.0)  # world kept racing it
        self.assertAlmostEqual(car["speed"], 0.0, places=3)  # coasted neutral

    def test_snapshot_shape(self):
        self.vr.use_item("a", "banana")
        self.vr.apply_input("a", {"throttle": 1.0})
        self._drive("b")
        run(self.vr, self.clock, 1)
        snap = self.vr.snapshot()
        for key in ("t", "state", "cars", "npcs", "traps", "results",
                    "events", "countdown_remaining", "laps", "track_length"):
            self.assertIn(key, snap)
        car = snap["cars"][0]
        for key in ("id", "name", "color", "pos", "lane", "kmh", "lap",
                    "rank", "items", "cooldowns", "incoming_shell_in"):
            self.assertIn(key, car)
        self.assertEqual({c["rank"] for c in snap["cars"]}, {1, 2})


if __name__ == "__main__":
    unittest.main()
