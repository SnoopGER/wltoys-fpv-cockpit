import os
import unittest
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("ADMIN_DISCORD_IDS", "admin")
os.environ.setdefault("ALLOWED_DISCORD_IDS", "driver,next")
os.environ.setdefault("DISCORD_CLIENT_ID", "client-id")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "client-secret")
os.environ.setdefault(
    "DISCORD_REDIRECT_URIS",
    "https://race.zen-rc.net/auth/discord/callback,http://192.168.178.187:5555/auth/discord/callback,http://localhost:5555/auth/discord/callback,http://127.0.0.1:5555/auth/discord/callback",
)
os.environ.setdefault("DISCORD_REDIRECT_URI", "https://race.zen-rc.net/auth/discord/callback")

import webapp


class DiscordRedirectSelectionTests(unittest.TestCase):
    def setUp(self):
        webapp.app.config.update(TESTING=True, SERVER_NAME=None)
        webapp.app.secret_key = "test-secret"
        self.client = webapp.app.test_client()

    def login_params_for_host(self, host):
        response = self.client.get("/login", headers={"Host": host})
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        parsed = urlparse(location)
        self.assertEqual(parsed.netloc, "discord.com")
        return parse_qs(parsed.query)

    def test_lan_login_uses_lan_redirect_uri_and_state(self):
        params = self.login_params_for_host("192.168.178.187:5555")

        self.assertEqual(params["redirect_uri"], ["http://192.168.178.187:5555/auth/discord/callback"])
        self.assertIn("state", params)
        with self.client.session_transaction(base_url="http://192.168.178.187:5555") as sess:
            self.assertEqual(sess["discord_oauth_state"], params["state"][0])
            self.assertEqual(sess["discord_oauth_redirect_uri"], "http://192.168.178.187:5555/auth/discord/callback")

    def test_public_login_uses_public_redirect_uri_and_state(self):
        params = self.login_params_for_host("race.zen-rc.net")

        self.assertEqual(params["redirect_uri"], ["https://race.zen-rc.net/auth/discord/callback"])
        self.assertIn("state", params)

    def test_unknown_host_falls_back_to_public_redirect_uri(self):
        params = self.login_params_for_host("unexpected.example")

        self.assertEqual(params["redirect_uri"], ["https://race.zen-rc.net/auth/discord/callback"])

    def test_callback_rejects_bad_state_before_token_exchange(self):
        self.login_params_for_host("192.168.178.142:5555")

        response = self.client.get(
            "/auth/discord/callback?code=abc&state=wrong",
            headers={"Host": "192.168.178.187:5555"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Invalid Discord OAuth state", response.data)


class SessionCookieConfigTests(unittest.TestCase):
    def test_public_host_uses_secure_host_cookie(self):
        with webapp.app.test_request_context("/", headers={"Host": "race.zen-rc.net"}, base_url="https://race.zen-rc.net"):
            self.assertTrue(webapp.app.session_interface.get_cookie_secure(webapp.app))
            self.assertEqual(webapp.app.session_interface.get_cookie_domain(webapp.app), "race.zen-rc.net")
            self.assertEqual(webapp.app.session_interface.get_cookie_samesite(webapp.app), "Lax")

    def test_lan_host_uses_http_local_cookie(self):
        with webapp.app.test_request_context("/", headers={"Host": "192.168.178.187:5555"}, base_url="http://192.168.178.187:5555"):
            self.assertFalse(webapp.app.session_interface.get_cookie_secure(webapp.app))
            self.assertIsNone(webapp.app.session_interface.get_cookie_domain(webapp.app))
            self.assertEqual(webapp.app.session_interface.get_cookie_samesite(webapp.app), "Lax")


if __name__ == "__main__":
    unittest.main()
