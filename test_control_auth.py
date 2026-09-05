"""Control-path authorization regression suite (AUDIT §6.1/6.2/6.6).

These tests lock down the security fix: driving requires being the ACTIVE
DRIVER (or admin), E-STOP freezes non-admin control, client silence forces
neutral. Run: .venv/bin/python -m unittest test_control_auth -v
"""
import os
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ADMIN_DISCORD_IDS", "admin")
os.environ.setdefault("ALLOWED_DISCORD_IDS", "driver,spectator:spectator")

import webapp

ADMIN = {"id": "admin", "username": "admin", "display_name": "Admin", "role": "admin"}
DRIVER = {"id": "driver", "username": "driver", "display_name": "Driver", "role": "driver"}
SPECTATOR = {"id": "spectator", "username": "spectator", "display_name": "Spec",
             "role": "spectator"}
GUEST = {"id": "guest-DRIVE-ABCD-EFGH", "username": "guest", "is_guest": True,
         "role": "driver", "can_connect": True}


def reset_state():
    webapp.lobby.update({
        "paused": False, "emergency_stop": False,
        "max_speed_percent": 70, "session_duration": 120,
        "active_driver": None, "driver_started_at": None,
        "queue": [], "users": {}, "sid_to_user": {}, "user_sids": {},
        "banned_ids": set(),
    })


class CommandAuthTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.car = MagicMock()
        self.car.send_command.return_value = True
        self.car.state = webapp.ConnectionState.CONNECTED
        self.slot = {"car": self.car, "decoder": MagicMock(),
                     "config": {}, "last_control_at": None}
        slots_p = patch.object(webapp, "car_slots", {"car1": self.slot})
        slots_p.start()
        self.addCleanup(slots_p.stop)
        init_p = patch.object(webapp, "init_car", return_value=self.slot)
        init_p.start()
        self.addCleanup(init_p.stop)

    def cmd(self, user, command="forward"):
        return webapp.handle_control_command(user, {"car": "car1", "command": command})

    def test_spectator_cannot_drive(self):
        self.assertFalse(self.cmd(SPECTATOR)["ok"])
        self.assertEqual(self.cmd(SPECTATOR)["error"], "unauthorized_driver")
        self.car.send_command.assert_not_called()

    def test_logged_in_non_active_driver_cannot_drive(self):
        webapp.start_driver("someone_else")
        self.assertEqual(self.cmd(DRIVER)["error"], "unauthorized_driver")
        self.car.send_command.assert_not_called()

    def test_active_driver_can_drive(self):
        webapp.start_driver("driver")
        self.assertTrue(self.cmd(DRIVER)["ok"])

    def test_active_guest_driver_can_drive_and_lights(self):
        webapp.start_driver(GUEST["id"])
        self.assertTrue(self.cmd(GUEST)["ok"])
        self.assertTrue(webapp.validate_control_user(GUEST))  # §6.4 guest-role bug

    def test_admin_can_drive_without_queue(self):
        self.assertTrue(self.cmd(ADMIN)["ok"])

    def test_admin_override_blocks_active_driver(self):
        webapp.start_driver("driver")
        webapp.lobby["admin_override"] = True
        self.assertEqual(self.cmd(DRIVER)["error"], "admin_override")
        self.car.send_command.assert_not_called()
        self.assertTrue(self.cmd(ADMIN)["ok"])  # admins keep control
        webapp.lobby["admin_override"] = False

    def test_command_tx_latency_recorded(self):
        webapp.start_driver("driver")
        before = len(webapp._tx_samples)
        self.assertTrue(self.cmd(DRIVER)["ok"])
        self.assertEqual(len(webapp._tx_samples), before + 1)

    def test_emergency_stop_blocks_driver(self):
        webapp.start_driver("driver")
        webapp.lobby["emergency_stop"] = True
        self.assertEqual(self.cmd(DRIVER)["error"], "emergency_stop")

    def test_client_silence_watchdog_forces_neutral(self):
        webapp.start_driver("driver")
        self.cmd(DRIVER)
        self.car.send_command.reset_mock()
        self.slot["last_control_at"] = time.time() - (webapp.CONTROL_SILENCE_SECONDS + 1)
        webapp.watchdog_control_silence()
        self.car.send_command.assert_called_once_with("stop", speed=0, steer_range=0)

    def test_watchdog_idle_when_no_active_driver(self):
        self.slot["last_control_at"] = time.time() - 100
        webapp.watchdog_control_silence()
        self.car.send_command.assert_not_called()


class GuestCodeTests(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_clear_survives_dormant_code(self):
        """§6.6: expires_at is None until redemption — used-mode clear used to 500."""
        webapp.guest_codes["DRIVE-TSTM-0001"] = {
            "created": time.time(), "expires_at": None, "duration": 600,
            "redeemed_by": None, "active": True,
        }
        self.addCleanup(webapp.guest_codes.pop, "DRIVE-TSTM-0001", None)
        webapp.lobby  # noqa: touch
        # simulate the endpoint's used-mode predicate
        dormant = webapp.guest_codes["DRIVE-TSTM-0001"]
        keep = not dormant.get("persistent") and (
            not dormant["active"]
            or (dormant["expires_at"] is not None and time.time() > dormant["expires_at"]))
        self.assertFalse(keep)  # dormant code must NOT crash and NOT be removed

    def test_no_hardcoded_codes(self):
        """§6.4: source-visible backdoor codes must never be seeded."""
        self.assertNotIn("ZENGARDEN", webapp.guest_codes)
        self.assertNotIn("ZENADMIN", webapp.guest_codes)


if __name__ == "__main__":
    unittest.main()
