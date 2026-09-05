"""Race engine regression suite — ROADMAP Phase 4 (D4/D5/D6/D9).

Covers the state machine, governor, item effects, and the wiring inside
handle_control_command. The hard rules under test:
  * race state never trusts the browser (server-authoritative)
  * countdown = nobody moves, including admins
  * effects NEVER invert steering; worst case is a bounded one-sided turn
  * free-drive behavior is byte-identical when no race is active
"""
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ADMIN_DISCORD_IDS", "admin")

import webapp
from race_engine import RaceEngine, IDLE, COUNTDOWN, GREEN, FINISHED

ADMIN = {"id": "admin", "username": "admin", "role": "admin"}
DRIVER = {"id": "driver", "username": "driver", "role": "driver"}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class EngineStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.e = RaceEngine(80, clock=self.clock)

    def test_starts_idle(self):
        self.assertEqual(self.e.state, IDLE)
        self.assertFalse(self.e.active)

    def test_countdown_to_green_via_tick(self):
        self.assertTrue(self.e.start())
        self.assertEqual(self.e.state, COUNTDOWN)
        self.clock.advance(2.9)
        self.assertFalse(self.e.tick())
        self.assertEqual(self.e.state, COUNTDOWN)
        self.clock.advance(0.2)
        self.assertTrue(self.e.tick())
        self.assertEqual(self.e.state, GREEN)

    def test_cannot_start_while_running(self):
        self.e.start()
        self.assertFalse(self.e.start())
        self.e.tick()
        self.assertFalse(self.e.start())

    def test_finish_from_green(self):
        self.e.start()
        self.e.tick()
        self.assertTrue(self.e.finish())
        self.assertEqual(self.e.state, FINISHED)
        # finished is startable again (next race)
        self.assertTrue(self.e.start())

    def test_restart_clears_results_and_effects(self):
        self.e.start()
        self.e.tick()
        self.e.grant_item("driver", "banana")
        self.e.register_finish("driver")
        self.e.finish()
        self.e.start()
        self.assertEqual(self.e.snapshot()["results"], [])
        self.assertEqual(self.e.snapshot()["active_effects"], {})

    def test_finish_ordering(self):
        self.e.start()
        self.e.tick()
        self.assertEqual(self.e.register_finish("a"), 1)
        self.assertEqual(self.e.register_finish("b"), 2)
        self.assertIsNone(self.e.register_finish("a"))  # no duplicates


class EffectTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.e = RaceEngine(80, clock=self.clock)
        self.e.start()
        self.clock.advance(3.1)
        self.e.tick()  # -> GREEN

    def mod(self, user="driver", cmd="forward", speed=100, steer=100):
        return self.e.modify(user, cmd, speed, steer)

    def test_idle_passthrough(self):
        e = RaceEngine(80, clock=self.clock)
        self.assertEqual(e.modify("driver", "forward", 100, 100),
                         ("forward", 100, 100, {"governor": None, "effects": []}))

    def test_countdown_forces_stop_for_everyone(self):
        e = RaceEngine(80, clock=self.clock)
        e.start()
        for user in ("driver", "admin"):
            cmd, s, st, _ = e.modify(user, "forward", 100, 100)
            self.assertEqual((cmd, s, st), ("stop", 0, 0))

    def test_governor_caps_throttle_in_race(self):
        _, s, _, meta = self.mod(speed=100)
        self.assertEqual(s, 80)
        self.assertEqual(meta["governor"], 80)

    def test_boost_releases_governor(self):
        self.e.grant_item("driver", "boost")
        _, s, _, meta = self.mod(speed=100)
        self.assertEqual(s, 100)
        self.assertIn("boost", meta["effects"])
        self.clock.advance(3.1)
        _, s, _, _ = self.mod(speed=100)
        self.assertEqual(s, 80)  # expired

    def test_banana_limits_steer_and_throttle(self):
        self.e.grant_item("driver", "banana")
        cmd, s, st, _ = self.mod(cmd="right", speed=100, steer=100)
        self.assertEqual(cmd, "right")   # direction untouched
        self.assertEqual(st, 35)         # deflection magnitude limited
        self.assertEqual(s, 60)          # 100*0.6=60, governor 80 not binding
        self.clock.advance(3.1)
        _, _, st, _ = self.mod(cmd="right", speed=100, steer=100)
        self.assertEqual(st, 100)

    def test_redshell_one_sided_never_inverted(self):
        self.e.grant_item("driver", "redshell")
        cmd, s, st, _ = self.mod(cmd="forward_left", speed=100, steer=100)
        self.assertEqual(cmd, "forward_right")  # one-sided lock
        self.assertLessEqual(st, 100)
        self.assertGreaterEqual(s, 0)
        self.clock.advance(1.1)
        cmd, _, _, _ = self.mod(cmd="forward_left", speed=100, steer=100)
        self.assertEqual(cmd, "forward_left")   # expired, driver regains

    def test_star_blocks_redshell(self):
        self.e.grant_item("driver", "star")
        self.assertFalse(self.e.grant_item("driver", "redshell"))
        cmd, _, _, meta = self.mod(cmd="forward_left")
        self.assertEqual(cmd, "forward_left")
        self.assertIn("star", meta["effects"])

    def test_unknown_item_rejected(self):
        self.assertFalse(self.e.grant_item("driver", "lightning"))

    def test_clear_effects(self):
        self.e.grant_item("driver", "banana")
        self.e.clear_effects("driver")
        _, s, st, _ = self.mod(speed=100, steer=100)
        self.assertEqual((s, st), (80, 100))

    def test_output_bounds(self):
        self.e.grant_item("driver", "banana")
        self.e.grant_item("driver", "redshell")
        _, s, st, _ = self.mod(speed=999, steer=-50)
        self.assertTrue(0 <= s <= 100)
        self.assertTrue(0 <= st <= 100)


class HotPathWiringTests(unittest.TestCase):
    """handle_control_command must apply race.modify for real commands."""

    def setUp(self):
        webapp.lobby.update({
            "paused": False, "emergency_stop": False, "admin_override": False,
            "max_speed_percent": 70, "active_driver": "driver",
        })
        self.clock = FakeClock()
        webapp.race = RaceEngine(80, clock=self.clock)
        self.car = MagicMock()
        self.car.send_command.return_value = True
        self.car.state = webapp.ConnectionState.CONNECTED
        slot = {"car": self.car, "decoder": MagicMock(), "relay": MagicMock(),
                "config": {}, "last_control_at": None}
        p1 = patch.object(webapp, "init_car", return_value=slot)
        p1.start()
        self.addCleanup(p1.stop)

    def cmd(self, user, **kw):
        data = {"car": "car1", "command": "forward"}
        data.update(kw)
        return webapp.handle_control_command(user, data)

    def test_no_race_unchanged(self):
        r = self.cmd(DRIVER, speed=100)
        self.assertEqual(r["speed"], 70)  # lobby cap, no race meta
        self.assertNotIn("race", r)

    def test_race_governor_applied_server_side(self):
        webapp.race.start()
        self.clock.advance(3.1)
        webapp.race.tick()
        r = self.cmd(DRIVER, speed=100)
        # lobby 70 cap still upstream: min(100,70)=70 <= governor 80
        self.assertEqual(r["speed"], 70)
        self.assertEqual(r["race"]["governor"], 80)

    def test_race_countdown_neutralizes_client(self):
        webapp.race.start()
        r = self.cmd(DRIVER, speed=100)
        self.assertEqual(r["command"], "stop")
        self.assertEqual(r["speed"], 0)
        self.car.send_command.assert_called_with("stop", speed=0, steer_range=0)

    def test_banana_reaches_wire(self):
        webapp.race.start()
        self.clock.advance(3.1)
        webapp.race.tick()
        webapp.race.grant_item("driver", "banana")
        r = webapp.handle_control_command(
            DRIVER, {"car": "car1", "command": "right", "speed": 100,
                     "steer_range": 100})
        self.assertEqual(r["steer_range"], 35)
        self.car.send_command.assert_called_once_with(
            "right", speed=r["speed"], steer_range=35)

    def test_estop_outranks_race(self):
        webapp.race.start()
        self.clock.advance(3.1)
        webapp.race.tick()
        webapp.lobby["emergency_stop"] = True
        self.assertEqual(self.cmd(DRIVER)["error"], "emergency_stop")


if __name__ == "__main__":
    unittest.main()
