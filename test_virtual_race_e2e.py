"""Virtual Race E2E over a REAL webapp + Socket.IO (ROADMAP P3).

Boots webapp.py as a subprocess on a scratch port with two persistent
driver codes + one admin code, then:
  1. two drivers redeem + vr:join and see each other in snapshots
  2. driver A fires a red shell -> B receives shell_impact/shell_launched
  3. A hard-disconnects mid-race -> car coasts; A reconnects with the same
     session cookie and re-attaches WITHOUT a page reload
Skips cleanly if python-socketio[client] is unavailable.

Run: python3 -m pytest test_virtual_race_e2e.py -q
"""
import os
import socket
import subprocess
import sys
import threading
import time
import unittest

import requests

try:
    import socketio as sio
    HAS_SIO = True
except ImportError:  # pragma: no cover
    HAS_SIO = False

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER_A = "DRIVE-AAA-1111"
DRIVER_B = "DRIVE-BBB-2222"
ADMIN = "DRIVE-ADM-9999"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Recorder:
    """Collects vr:snapshot payloads per client."""

    def __init__(self):
        self.last = None
        self.events = []
        self.lock = threading.Lock()
        self.item_acks = []

    def on_snapshot(self, snap):
        with self.lock:
            self.last = snap
            self.events.extend(snap.get("events") or [])

    def on_item_ack(self, ack):
        with self.lock:
            self.item_acks.append(ack)

    def car(self, uid):
        with self.lock:
            if not self.last:
                return None
            for c in self.last.get("cars", []):
                if c["id"] == uid:
                    return c
        return None


@unittest.skipUnless(HAS_SIO, "python-socketio[client] not installed")
class VirtualRaceE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ)
        env.update({
            "PORT": str(cls.port),
            "PERSISTENT_CODES": f"{DRIVER_A}:driver,{DRIVER_B}:driver,{ADMIN}:admin",
            "SESSION_SECRET": "e2e-test-secret",
            "VR_TRACK_LENGTH": "400",
            "VR_LAPS": "2",
            "VR_NPCS": "2",
        })
        env.pop("DISCORD_CLIENT_ID", None)
        env.pop("DISCORD_CLIENT_SECRET", None)
        env.pop("AUTO_CONNECT_CAR", None)
        env.pop("ENABLE_TEST_CODES", None)
        cls.proc = subprocess.Popen(
            [sys.executable, "webapp.py"], cwd=HERE, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{cls.port}"
        cls.base = base
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                requests.get(base + "/", timeout=1)
                return
            except requests.RequestException:
                time.sleep(0.3)
        cls.tearDownClass()
        raise RuntimeError("webapp did not come up")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    # ------------------------------------------------------------------ utils

    def redeem(self, code):
        sess = requests.Session()
        r = sess.post(self.base + "/api/redeem-code", json={"code": code},
                      timeout=5)
        self.assertTrue(r.ok, r.text)
        self.assertTrue(r.json().get("ok"), r.text)
        return sess

    def connect(self, sess, rec):
        client = sio.Client(reconnection=False)
        client.on("vr:snapshot", rec.on_snapshot)
        client.on("vr:item:ack", rec.on_item_ack)
        cookie = sess.cookies.get("session")
        client.connect(self.base, transports=["websocket"],
                       headers={"Cookie": f"session={cookie}"})
        return client

    def drive(self, client, throttle=1.0, steer=0.0, interval=0.05):
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                try:
                    client.emit("vr:input", {"throttle": throttle,
                                             "steer": steer})
                except Exception:
                    return
                time.sleep(interval)
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return stop

    def wait_for(self, predicate, timeout=8.0, msg=""):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.1)
        self.fail(msg or "condition not met within %.1fs" % timeout)

    # ------------------------------------------------------------------- test

    def test_full_race_flow(self):
        sess_a, sess_b = self.redeem(DRIVER_A), self.redeem(DRIVER_B)
        sess_adm = self.redeem(ADMIN)
        rec_a, rec_b = Recorder(), Recorder()

        cli_a = self.connect(sess_a, rec_a)
        cli_b = self.connect(sess_b, rec_b)
        cli_a.emit("vr:join")
        cli_b.emit("vr:join")
        time.sleep(0.5)

        # non-admin cannot start the race
        r = sess_a.post(self.base + "/api/virtual/start", timeout=5)
        self.assertEqual(r.status_code, 403)
        r = sess_adm.post(self.base + "/api/virtual/start", timeout=5)
        self.assertTrue(r.json().get("ok"), r.text)

        # countdown: wait for green snapshots
        self.wait_for(lambda: rec_a.last and rec_a.last["state"] == "green",
                      msg="race never went GREEN")

        # B drives away; A holds for 1.5s so B is legitimately ahead
        stop_b = self.drive(cli_b)
        time.sleep(1.5)
        stop_a = self.drive(cli_a)

        # 1. each driver sees the other's car
        def two_players(seen):
            if not seen.last:
                return False
            ids = [c["id"] for c in seen.last["cars"]]
            return (any(DRIVER_A in i for i in ids)
                    and any(DRIVER_B in i for i in ids))
        self.wait_for(lambda: two_players(rec_a), msg="A never saw both cars")
        self.wait_for(lambda: two_players(rec_b), msg="B never saw both cars")

        # 2. A fires a red shell at B (closest ahead)
        cli_a.emit("vr:input", {"throttle": 1.0, "item": "redshell"})
        self.wait_for(lambda: any(e["type"] == "shell_launched"
                                  for e in rec_a.events + rec_b.events),
                      msg="no shell_launched event")

        def shell_hit():
            if any(e["type"] == "shell_impact" for e in rec_b.events):
                return True
            if rec_b.last:
                for c in rec_b.last["cars"]:
                    if DRIVER_B in c["id"] and "redshell" in c["items"]:
                        return True
            return False
        self.wait_for(shell_hit, timeout=5.0, msg="shell never impacted B")

        # 3. A hard-disconnects; car must coast to neutral; rejoin re-attaches
        stop_a.set()
        time.sleep(0.3)
        cli_a.disconnect()
        time.sleep(self._silence_timeout() + 1.5)
        def a_neutral():
            if not rec_b.last:
                return False
            for c in rec_b.last["cars"]:
                if DRIVER_A in c["id"]:
                    return c["speed"] < 1.0 and c["connected"] is False
            return False
        self.wait_for(a_neutral, timeout=6,
                      msg="A's abandoned car never coasted neutral")

        cli_a2 = self.connect(sess_a, rec_a)   # same cookie, no reload
        cli_a2.emit("vr:join")
        def a_back():
            c = None
            if rec_b.last:
                for cc in rec_b.last["cars"]:
                    if DRIVER_A in cc["id"]:
                        c = cc
            return c is not None and c["connected"] is True
        self.wait_for(a_back, timeout=6,
                      msg="reconnected A never re-attached to its car")
        stop_a2 = self.drive(cli_a2)
        def a_moving():
            if not rec_b.last:
                return False
            for cc in rec_b.last["cars"]:
                if DRIVER_A in cc["id"]:
                    return cc["speed"] > 5.0
            return False
        self.wait_for(a_moving, timeout=6, msg="A's car never resumed driving")
        stop_a2.set()

        for cli in (cli_b, cli_a2):
            try:
                cli.disconnect()
            except Exception:
                pass
        stop_b.set()

    @staticmethod
    def _silence_timeout():
        return 5.0  # virtual_race default silence_timeout


if __name__ == "__main__":
    unittest.main()
