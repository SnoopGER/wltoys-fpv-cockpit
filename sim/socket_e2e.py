"""E2E test of the Socket.IO control plane + latency telemetry against car_sim.

Starts webapp.py on PORT from .env.local (expects 5560), redeems a guest
code over HTTP, drives the socket as an authenticated session, and asserts:
  1. control:command over socket returns ok ack (car connected via sim)
  2. control:ack carries server_ts + echo_ts (latency loop fields)
  3. control:rtt round-trip works (clock sync fields)
  4. /api/status exposes control_latency_* after activity
  5. unauthorized second session is rejected on the control path
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

import requests
import socketio

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:5560"

results = []
def check(name, ok, extra=""):
    results.append(ok)
    print(("PASS" if ok else "FAIL"), name, extra)

def wait_ready(timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(BASE + "/", timeout=2)
            return True
        except urllib.error.HTTPError:
            return True  # serving (e.g. 503 on some routes) == up
        except Exception:
            time.sleep(0.5)
    return False

def main():
    sim = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "sim", "car_sim.py"),
         "--dest", "127.0.0.1", "--always-on"],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    web = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "webapp.py")],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_ready(), "webapp did not come up"

        # --- session 1: guest code -> driver
        s1 = requests.Session()
        code = s1.post(BASE + "/api/guest/debug-generate", json={}).json()["code"]
        r = s1.post(BASE + "/api/redeem-code", json={"code": code})
        check("session1 redeems guest code", r.status_code == 200)
        check("session1 joins queue (becomes active)",
              s1.post(BASE + "/api/queue/join", json={"car": "car1"}).status_code == 200)
        check("session1 connects car",
              s1.post(BASE + "/api/connect", json={"car": "car1"}).json().get("state") == "connected")
        time.sleep(1.5)

        # --- socket authed with session1 cookies
        cookie_hdr = "; ".join(f"{c.name}={c.value}" for c in s1.cookies)
        acks = {"list": []}
        rtts = {}
        ev = threading.Event()
        ev2 = threading.Event()
        sio = socketio.Client(reconnection=False)

        @sio.on("control:ack")
        def on_ack(data):
            acks.setdefault("list", []).append(data)
            ev.set()

        @sio.on("control:rtt:ack")
        def on_rtt(data):
            rtts.update(data)
            ev2.set()

        # requests session cookies -> socketio headers
        sio.connect(BASE, headers={"Cookie": cookie_hdr},
                    transports=["websocket"], wait_timeout=10)
        check("socket connected", sio.connected)

        # clock sync ping
        t0 = time.time()
        sio.emit("control:rtt", {"t0": t0})
        check("control:rtt ack", ev2.wait(5), f"offset~{(rtts.get('server_ts', 0) - t0)*1000:.0f}ms")

        # drive commands over socket
        ok_count = 0
        for i in range(10):
            acks.clear() if False else None
            ev.clear()
            sio.emit("control:command",
                     {"command": "forward", "speed": 60, "client_ts": time.time()})
            ev.wait(3)
            a = (acks.get("list") or [{}])[-1]
            if a.get("ok"):
                ok_count += 1
        check("socket control commands accepted", ok_count >= 8, f"{ok_count}/10 ok")
        last = (acks.get("list") or [{}])[-1]
        check("ack carries latency loop fields",
              "server_ts" in last and "echo_ts" in last, str({k: last.get(k) for k in ("server_ts", "echo_ts")}))

        sio.emit("control:command", {"command": "stop", "client_ts": time.time()})
        time.sleep(0.3)

        # --- status telemetry
        st = s1.get(BASE + "/api/status").json()
        lat_ok = (st.get("control_latency_samples", 0) > 0
                  and st.get("control_latency_avg_ms") is not None)
        check("status exposes latency stats", lat_ok,
              f"avg={st.get('control_latency_avg_ms')}ms p95={st.get('control_latency_p95_ms')}ms n={st.get('control_latency_samples')}")

        # --- session 2 (another guest) must NOT control the car
        s2 = requests.Session()
        code2 = s2.post(BASE + "/api/guest/debug-generate", json={}).json()["code"]
        s2.post(BASE + "/api/redeem-code", json={"code": code2})
        r2 = s2.post(BASE + "/api/command", json={"command": "forward", "speed": 50})
        check("second guest rejected on control path",
              r2.status_code == 403 and r2.json().get("error") == "unauthorized_driver")

        # --- admin override lock (nobody drives except admins)
        # session3 redeems a persistent ADMIN code (PERSISTENT_CODES env)
        s3 = requests.Session()
        r3 = s3.post(BASE + "/api/redeem-code", json={"code": "E2E-ADMIN-TEST"})
        check("persistent admin code redeems", r3.status_code == 200)
        r3 = s3.post(BASE + "/api/admin/admin_override", json={"value": True})
        check("admin engages override lock", r3.status_code == 200,
              "" if r3.status_code == 200 else r3.text[:80])
        r3 = s3.post(BASE + "/api/command", json={"command": "forward", "speed": 50})
        check("override engaged: admin still drives", r3.status_code == 200 and r3.json().get("ok"),
              "" if r3.status_code == 200 else r3.text[:80])
        r1 = s1.post(BASE + "/api/command", json={"command": "forward", "speed": 50})
        check("override engaged: active driver blocked",
              r1.status_code == 403 and r1.json().get("error") == "admin_override")
        r2 = s2.post(BASE + "/api/command", json={"command": "forward", "speed": 50})
        check("override engaged: other guest blocked",
              r2.status_code == 403 and r2.json().get("error") in ("admin_override", "unauthorized_driver"))
        r3 = s3.post(BASE + "/api/admin/admin_override", json={"value": False})
        check("admin releases override lock", r3.status_code == 200)
        r1 = s1.post(BASE + "/api/command", json={"command": "forward", "speed": 50})
        check("after release active driver drives again", r1.status_code == 200)

        # --- WebCodecs video relay (Phase 3): /ws/video/<car>
        import websocket  # websocket-client
        vid = s1.get(BASE + "/api/video-token?car=car1").json()
        check("video-token issued", vid.get("ok") and bool(vid.get("token")))
        wsurl = "ws://127.0.0.1:5560/ws/video/car1?token=" + vid["token"]
        wsc = websocket.create_connection(wsurl, timeout=10)
        first = wsc.recv()  # meta frame (text)
        check("relay meta frame", isinstance(first, (str, bytes)) and
              b"fpv-meta" in (first if isinstance(first, bytes) else first.encode()))
        # collect binary video frames (sim streams continuously)
        got_video = False
        deadline = time.time() + 8
        while time.time() < deadline:
            msg = wsc.recv()
            if isinstance(msg, (bytes, bytearray)) and len(msg) > 10:
                got_video = True
                break
        check("relay delivers H.264 frames", got_video)
        wsc.close()
        # forged token rejected
        try:
            wsb = websocket.create_connection(
                "ws://127.0.0.1:5560/ws/video/car1?token=garbage", timeout=5)
            resp = wsb.recv()
            wsb.close()
        except Exception as exc:
            resp = str(exc).encode()
        check("relay rejects forged token",
              b"fpv-auth-error" in (resp if isinstance(resp, bytes) else str(resp).encode()))

        sio.disconnect()
    finally:
        for p in (web, sim):
            p.terminate()
        time.sleep(1)
        for p in (web, sim):
            p.kill()

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1

if __name__ == "__main__":
    sys.exit(main())
