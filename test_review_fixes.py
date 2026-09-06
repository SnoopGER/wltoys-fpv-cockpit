"""Regression tests for the 2026-09-06 pre-track review fixes.

Covers: SRV-1 (tunnel collapses local_request + rate-limit keys),
SRV-6 (malformed control payloads must degrade, not raise).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

import webapp  # noqa: E402


class ClientIpTests(unittest.TestCase):
    """SRV-1: cloudflared reaches the origin over loopback, so raw
    remote_addr is 127.0.0.1 for EVERY tunnel visitor."""

    def test_direct_loopback_is_local(self):
        with webapp.app.test_request_context(
                "/", environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertTrue(webapp.local_request())
            self.assertEqual(webapp.client_ip(), "127.0.0.1")

    def test_tunnel_visitor_is_not_local(self):
        with webapp.app.test_request_context(
                "/", headers={"CF-Connecting-IP": "203.0.113.9"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertFalse(webapp.local_request())

    def test_tunnel_visitor_real_ip_for_rate_keys(self):
        with webapp.app.test_request_context(
                "/", headers={"CF-Connecting-IP": "203.0.113.9"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertEqual(webapp.client_ip(), "203.0.113.9")

    def test_forged_cf_header_from_non_loopback_ignored(self):
        # Only our own tunnel (loopback peer) may assert CF-Connecting-IP
        with webapp.app.test_request_context(
                "/", headers={"CF-Connecting-IP": "8.8.8.8"},
                environ_base={"REMOTE_ADDR": "192.168.1.5"}):
            self.assertEqual(webapp.client_ip(), "192.168.1.5")
            self.assertFalse(webapp.local_request())

    def test_xff_also_disqualifies_local(self):
        with webapp.app.test_request_context(
                "/", headers={"X-Forwarded-For": "198.51.100.7"},
                environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertFalse(webapp.local_request())


class MalformedControlTests(unittest.TestCase):
    """SRV-6: junk WS payloads must return an error dict, never raise."""

    def setUp(self):
        self.user = {"id": "driver", "role": "driver", "username": "d"}
        patcher = patch.object(webapp, "lobby", {
            "users": {"driver": self.user}, "queue": [], "paused": False,
            "emergency_stop": False, "admin_override": False,
            "active_driver": "driver", "max_speed_percent": 70,
            "banned_ids": set(), "sid_to_user": {}, "user_sids": {},
            "driver_started_at": 0, "session_duration": 120,
            "paused_remaining": None,
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        slot = MagicMock()
        slot["car"].send_command.return_value = True
        slots_p = patch.object(webapp, "car_slots", {"car1": slot})
        slots_p.start()
        self.addCleanup(slots_p.stop)
        init_p = patch.object(webapp, "init_car", return_value=slot)
        init_p.start()
        self.addCleanup(init_p.stop)

    def test_non_dict_payload(self):
        for junk in ("x", 5, ["a"], None):
            r = webapp.handle_control_command(self.user, junk)
            self.assertFalse(r["ok"])

    def test_non_string_car_id(self):
        r = webapp.handle_control_command(
            self.user, {"car": 5, "command": "forward", "speed": 50})
        self.assertTrue(r["ok"])  # coerces to default car

    def test_non_string_command(self):
        r = webapp.handle_control_command(
            self.user, {"car": "car1", "command": {"x": 1}})
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
