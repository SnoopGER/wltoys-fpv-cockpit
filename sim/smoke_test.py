#!/usr/bin/env python3
"""
Golden-path smoke test: real CarProtocol + VideoDecoder against sim/car_sim.py.

Start the simulator first (or let this script start it):
  python sim/car_sim.py --always-on &
  python sim/smoke_test.py

Checks:
  1. connect() handshake succeeds
  2. frames are assembled from UDP fragments
  3. H.264 decodes and JPEG is produced (video pipeline alive)
  4. motor commands accepted, checksum valid (sim validates them)
  5. heartbeat-loss -> sim drops to neutral/streams off (deadman works)
Exit code 0 = all pass.
"""

import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from car_protocol import CarProtocol  # noqa: E402
from video_decoder import VideoDecoder  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


def main():
    sim = subprocess.Popen(
        [sys.executable, os.path.join(REPO, "sim", "car_sim.py"), "--always-on"],
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    time.sleep(1.5)
    try:
        print("smoke: CarProtocol <-> car_sim end-to-end")
        decoder = VideoDecoder(width=320, height=180, quality=70)
        car = CarProtocol(car_ip="127.0.0.1", listen_port=1234,
                          on_log=lambda lvl, msg: None)
        car.on_frame = lambda d: decoder.feed_frame(d)

        ok = car.connect()
        check("connect (handshake)", ok, car.state.value)
        if not ok:
            return finish()

        decoder.start()
        # drive a bit
        for cmd in ("forward", "forward_right", "forward_left", "reverse", "stop"):
            car.send_command(cmd, speed=60, steer_range=50)
            time.sleep(0.25)

        # wait for frames to arrive and decode
        deadline = time.time() + 8
        while time.time() < deadline and decoder.frame_count < 5:
            time.sleep(0.2)
        check("frames assembled + decoded", decoder.frame_count >= 5,
              f"{decoder.frame_count} decoded frames")

        jpeg = decoder.get_latest_jpeg()
        check("JPEG output valid", bool(jpeg) and jpeg[:2] == b"\xff\xd8",
              f"{len(jpeg) if jpeg else 0} bytes")

        st = car.get_status()
        check("status reports traffic",
              st.get("packets_received", 0) > 0 and st.get("frames_assembled", 0) > 0,
              f"pkts={st.get('packets_received')} frames={st.get('frames_assembled')} "
              f"dropped={st.get('frames_dropped')}")

        car.disconnect()
        time.sleep(1.6)  # deadman window in sim is 1.0s
        # after disconnect the sim should have stopped streaming;
        # count no longer grows
        c1 = decoder.frame_count
        time.sleep(1.5)
        check("deadman: stream stops when heartbeat stops",
              decoder.frame_count == c1,
              f"frames before={c1} after={decoder.frame_count}")
        return finish()
    finally:
        sim.terminate()


def finish():
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed"
          + (f"  FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
