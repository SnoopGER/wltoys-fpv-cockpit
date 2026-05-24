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


class PersistentCodeTests(unittest.TestCase):
    def test_zenadmin_redeems_as_admin(self):
        user, error = webapp.redeem_guest_code("ZENADMIN")

        self.assertIsNone(error)
        self.assertEqual(user["role"], "admin")
        self.assertTrue(user["can_connect"])
        self.assertTrue(webapp.is_admin(user))

    def test_zengarden_redeems_as_driver(self):
        user, error = webapp.redeem_guest_code("ZENGARDEN")

        self.assertIsNone(error)
        self.assertEqual(user["role"], "driver")
        self.assertTrue(user["can_connect"])
        self.assertFalse(webapp.is_admin(user))


if __name__ == "__main__":
    unittest.main()
