import importlib
import os
import unittest
from unittest.mock import patch


os.environ.setdefault("ADMIN_DISCORD_IDS", "admin")
os.environ.setdefault("ALLOWED_DISCORD_IDS", "driver,next")

import webapp


def reset_lobby():
    webapp.lobby.update({
        "paused": False,
        "emergency_stop": False,
        "max_speed_percent": webapp.MAX_REMOTE_SPEED_PERCENT,
        "session_duration": 120,
        "active_driver": None,
        "driver_started_at": None,
        "queue": [],
        "users": {},
        "sid_to_user": {},
        "user_sids": {},
        "driver_sessions": {},
    })
    if "banned_ids" in webapp.lobby:
        webapp.lobby["banned_ids"] = set()


class LobbyTimerTests(unittest.TestCase):
    def setUp(self):
        reset_lobby()
        webapp.lobby["users"] = {
            "driver": {"id": "driver", "username": "driver", "display_name": "Driver", "role": "driver"},
            "next": {"id": "next", "username": "next", "display_name": "Next", "role": "driver"},
        }

    def test_expired_active_driver_stays_active_when_queue_empty(self):
        webapp.start_driver("driver")
        webapp.lobby["driver_started_at"] -= 121

        with patch.object(webapp, "send_neutral") as send_neutral:
            changed = webapp.advance_driver_if_due_locked("timer")

        self.assertFalse(changed)
        self.assertEqual(webapp.lobby["active_driver"], "driver")
        send_neutral.assert_not_called()

    def test_expired_active_driver_switches_when_next_driver_queued(self):
        webapp.start_driver("driver")
        webapp.lobby["queue"] = ["next"]
        webapp.lobby["driver_started_at"] -= 121

        with patch.object(webapp, "send_neutral") as send_neutral:
            changed = webapp.advance_driver_if_due_locked("timer")

        self.assertTrue(changed)
        self.assertEqual(webapp.lobby["active_driver"], "next")
        self.assertEqual(webapp.lobby["queue"], [])
        send_neutral.assert_called_once()


class AdminModerationTests(unittest.TestCase):
    def setUp(self):
        reset_lobby()
        webapp.lobby["users"] = {
            "driver": {"id": "driver", "username": "driver", "display_name": "Driver", "role": "driver"},
            "next": {"id": "next", "username": "next", "display_name": "Next", "role": "driver"},
        }
        webapp.lobby["user_sids"] = {"driver": {"sid-driver"}, "next": {"sid-next"}}
        webapp.lobby["sid_to_user"] = {"sid-driver": "driver", "sid-next": "next"}
        webapp.lobby["queue"] = ["next"]
        webapp.lobby["active_driver"] = "driver"
        webapp.lobby["driver_started_at"] = webapp.time.time()

    def test_ban_user_removes_user_from_queue_and_active_driver(self):
        with patch.object(webapp, "send_neutral"), patch.object(webapp, "disconnect_user_sockets") as disconnect_user_sockets, patch.object(webapp, "save_banned_ids"):
            changed = webapp.ban_user("driver", "admin ban")

        self.assertTrue(changed)
        self.assertIn("driver", webapp.lobby["banned_ids"])
        self.assertEqual(webapp.lobby["active_driver"], "next")
        self.assertNotIn("driver", webapp.lobby["users"])
        disconnect_user_sockets.assert_called_once_with("driver")

    def test_unban_user_allows_id_again(self):
        webapp.lobby["banned_ids"] = {"driver"}
        with patch.object(webapp, "save_banned_ids"):
            changed = webapp.unban_user("driver")

        self.assertTrue(changed)
        self.assertNotIn("driver", webapp.lobby["banned_ids"])


class SessionPhysicsTests(unittest.TestCase):
    def setUp(self):
        reset_lobby()

    def test_start_driver_initializes_cold_tire_session(self):
        webapp.start_driver("driver")

        state = webapp.lobby["driver_sessions"]["driver"]
        self.assertEqual(state["tire_temp"], webapp.TIRE_TEMP_MIN)
        self.assertEqual(state["engine_temp"], webapp.ENGINE_TEMP_MIN)
        self.assertEqual(webapp.session_snapshot(state)["tire_state"], "cold")

    def test_active_steering_warms_tires(self):
        now = 1000.0
        state = webapp.new_driver_session(now)

        telemetry = webapp.update_tire_warmup(state, "left", 100, now + 1.0)

        self.assertGreater(telemetry["tire_temp"], webapp.TIRE_TEMP_MIN)
        self.assertGreater(telemetry["steering_multiplier"], 0.68)

    def test_idle_after_delay_cools_tires(self):
        now = 1000.0
        state = webapp.new_driver_session(now)
        state["tire_temp"] = 0.8
        state["last_steering_time"] = now - 20
        state["last_update"] = now

        telemetry = webapp.update_tire_warmup(state, "stop", 100, now + 1.0)

        self.assertLess(telemetry["tire_temp"], 0.8)

    def test_active_throttle_warms_engine(self):
        now = 1000.0
        state = webapp.new_driver_session(now)

        telemetry = webapp.update_engine_temperature(state, "forward", 100, now + 1.0)

        self.assertGreater(telemetry["engine_temp"], webapp.ENGINE_TEMP_MIN)
        self.assertGreater(telemetry["max_throttle_pct"], 60)

    def test_idle_after_delay_cools_engine(self):
        now = 1000.0
        state = webapp.new_driver_session(now)
        state["engine_temp"] = 90
        state["last_throttle_time"] = now - 20
        state["last_engine_update"] = now

        telemetry = webapp.update_engine_temperature(state, "stop", 0, now + 1.0)

        self.assertLess(telemetry["engine_temp"], 90)

    def test_session_modifiers_reduce_cold_steering_throttle_and_corner_speed(self):
        now = 1000.0
        webapp.lobby["driver_sessions"]["driver"] = webapp.new_driver_session(now)

        speed, steer_range, telemetry = webapp.apply_session_modifiers(
            "driver",
            "forward_left",
            100,
            100,
            now + 0.1,
        )

        self.assertEqual(steer_range, 68)
        self.assertEqual(speed, 60)
        self.assertEqual(telemetry["corner_speed_cap"], 68)
        self.assertEqual(telemetry["max_throttle_pct"], 60)


if __name__ == "__main__":
    unittest.main()
