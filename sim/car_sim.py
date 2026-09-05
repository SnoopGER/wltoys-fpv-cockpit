#!/usr/bin/env python3
"""
WLtoys FPV Car Simulator — dev harness for the cockpit (NO real car needed).

Emulates one WLtoys 6405 FPV car on localhost:
  * listens for handshake WAKE/TRIGGER packets on UDP :23459
  * listens for motor/heartbeat packets on UDP :23458 (ca47d5... frame)
  * once triggered, streams synthetic H.264 (ffmpeg testsrc + wall clock)
    to the cockpit's video port (UDP :1234) using the real 32-byte
    fragment protocol (magic 0x5aa56cc6, max 1440B payload per packet)
  * stops streaming if control/heartbeat traffic stops for >1s
    (mirrors real car behavior: car returns to neutral / stream stalls)

Usage:
  python sim/car_sim.py [--dest 127.0.0.1] [--video-port 1234]
                        [--ctl-port 23458] [--hs-port 23459]
                        [--width 640 --height 360 --fps 20]
                        [--crf 28] [--always-on]

Requires: ffmpeg on PATH.
"""

import argparse
import socket
import struct
import subprocess
import sys
import threading
import time

HEADER_MAGIC = 0x5AA56CC6
MAX_PAYLOAD = 1440

MOTOR_MAGIC = bytes.fromhex("ca47d5")
HANDSHAKE_WAKE_PREFIX = bytes.fromhex("a88a21")
HANDSHAKE_TRIGGER_PREFIX = bytes.fromhex("a88a20")

DEADMAN_SEC = 1.0


def build_header(seq, frame_size, total, idx, offset, datalen, ts):
    """Exact 32-byte header per protocol doc:
    u32 magic | u32 frame_size | u32 seq | u32 ts | u32 pad(0)
    u16 total_frags | u16 frag_idx | u32 data_offset | u32 data_len
    """
    return struct.pack("<IIIIIHHII",
                       HEADER_MAGIC, frame_size, seq, ts & 0xFFFFFFFF, 0,
                       total, idx, offset, datalen)


def nal_type(byte):
    return byte & 0x1F


class CarSim:
    def __init__(self, args):
        self.args = args
        self.awake = False
        self.streaming_wanted = False
        self.last_ctl = 0.0
        self.last_shown = (0x80, 0x80)
        self.frames_sent = 0
        self.packets_sent = 0
        self.seq = 0
        self.stop = threading.Event()
        self.video_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._ffmpeg_proc = None

    # ---------- control receivers ----------
    def _bind(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.settimeout(0.5)
        return s

    def handshake_loop(self):
        s = self._bind(self.args.hs_port)
        while not self.stop.is_set():
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                continue
            if data.startswith(HANDSHAKE_WAKE_PREFIX):
                if not self.awake:
                    print(f"[hs ] WAKE from {addr[0]}:{addr[1]}")
                self.awake = True
            elif data.startswith(HANDSHAKE_TRIGGER_PREFIX):
                print(f"[hs ] TRIGGER -> video ON (from {addr[0]}:{addr[1]})")
                self.awake = True
                self.streaming_wanted = True
                self.last_ctl = time.time()

    def control_loop(self):
        s = self._bind(self.args.ctl_port)
        cmd_count, win_start = 0, time.time()
        while not self.stop.is_set():
            try:
                data, addr = s.recvfrom(2048)
            except socket.timeout:
                if (time.time() - self.last_ctl > DEADMAN_SEC
                        and self.streaming_wanted and not self.args.always_on):
                    print("[ctl] heartbeat gap > 1s -> neutral, stream OFF")
                    self.streaming_wanted = False
                continue
            if not data.startswith(MOTOR_MAGIC):
                continue
            self.last_ctl = time.time()
            if len(data) >= 15:
                str_b, thr_b = data[9], data[10]
                chk = data[14]
                ok = "OK " if chk == (str_b ^ thr_b ^ 0x80) else "BAD"
                if (str_b, thr_b) != self.last_shown:
                    print(f"[ctl] STR=0x{str_b:02x} THR=0x{thr_b:02x} chk={ok}"
                          f" ({self._interpret(str_b, thr_b)})")
                    self.last_shown = (str_b, thr_b)
                cmd_count += 1
                if not self.streaming_wanted and self.awake:
                    # real car starts streaming after wake+traffic
                    self.streaming_wanted = True
            now = time.time()
            if now - win_start >= 5.0:
                print(f"[ctl] {cmd_count / (now - win_start):.1f} pkt/s sustained")
                cmd_count, win_start = 0, now

    @staticmethod
    def _interpret(str_b, thr_b):
        parts = []
        t = thr_b - 0x80
        if t > 5: parts.append(f"fwd {int(t / 127 * 100)}%")
        elif t < -5: parts.append(f"rev {int(-t / 127 * 100)}%")
        s = str_b - 0x80
        if s > 5: parts.append(f"right {int(s / 127 * 100)}%")
        elif s < -5: parts.append(f"left {int(-s / 127 * 100)}%")
        return ", ".join(parts) if parts else "neutral"

    # ---------- video pipeline ----------
    def _ffmpeg(self):
        a = self.args
        return subprocess.Popen(
            ["ffmpeg", "-loglevel", "error",
             "-re", "-f", "lavfi",
             "-i", f"testsrc=size={a.width}x{a.height}:rate={a.fps}",
             "-vf", "drawtext=text='%{localtime\\:%T}':x=10:y=10:fontsize=28:fontcolor=yellow:box=1,format=yuv420p",
             "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
             "-profile:v", "baseline", "-level", "3.1",
             "-g", str(a.fps * 2), "-bf", "0", "-sc_threshold", "0",
             "-x264-params", f"crf={a.crf}",
             "-f", "h264", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def video_loop(self):
        """Read Annex-B stream from ffmpeg, cut into frames, fragment, send."""
        buf = b""
        while not self.stop.is_set():
            if not self.streaming_wanted:
                time.sleep(0.1)
                if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
                    pass  # keep proc, just not reading -> ffmpeg blocks on pipe (ok)
                continue
            chunk = self._ffmpeg_proc.stdout.read(4096)
            if not chunk:
                print("[vid] ffmpeg stream ended, restarting")
                time.sleep(1)
                self._ffmpeg_proc = self._ffmpeg()
                buf = b""
                continue
            buf += chunk
            frames, buf = self._split_frames(buf)
            for f in frames:
                self._send_frame(f)

    def _split_frames(self, buf):
        """Cut Annex-B buffer into access units. A frame = optional
        (SPS|PPS|SEI|AUD)* prefix + first VCL NAL (types 1-5) up to next
        VCL start code."""
        units, frames = [], []
        # find all start code positions
        positions = []
        i = 0
        while True:
            j = buf.find(b"\x00\x00\x01", i)
            if j < 0:
                break
            # normalize 4-byte start codes
            start = j - 1 if j > 0 and buf[j - 1] == 0 else j
            positions.append((start, j + 3))
            i = j + 3
        if not positions:
            return [], buf
        complete = []
        for k, (s, payload_off) in enumerate(positions):
            end = positions[k + 1][0] if k + 1 < len(positions) else len(buf)
            if k + 1 >= len(positions):
                break  # last unit may be incomplete — keep in buf
            complete.append((payload_off, buf[payload_off:end]))
        tail = buf[complete[-1][0] - 1:] if complete else buf

        cur = b""
        for payload_off, unit in complete:
            nt = nal_type(unit[0]) if unit else 0
            body = b"\x00\x00\x00\x01" + unit
            if nt in (1, 5):  # VCL slice -> closes the frame
                frames.append(cur + body)
                cur = b""
            else:  # SPS/PPS/SEI/AUD/etc -> prefix of next frame
                cur += body
        return frames, (cur + tail) if (cur or tail) else b""

    def _send_frame(self, frame):
        self.seq += 1
        ts = int(time.time() * 1000) & 0xFFFFFFFF
        total = (len(frame) + MAX_PAYLOAD - 1) // MAX_PAYLOAD
        for idx in range(total):
            payload = frame[idx * MAX_PAYLOAD:(idx + 1) * MAX_PAYLOAD]
            hdr = build_header(self.seq, len(frame), total, idx,
                               idx * MAX_PAYLOAD, len(payload), ts)
            self.video_sock.sendto(hdr + payload, (self.args.dest, self.args.video_port))
            self.packets_sent += 1
            time.sleep(0.0005)  # gentle pacing, avoid receive-buffer bursts
        self.frames_sent += 1
        if self.frames_sent % (self.args.fps * 5) == 0:
            print(f"[vid] {self.frames_sent} frames, {self.packets_sent} pkts "
                  f"-> {self.args.dest}:{self.args.video_port}")

    # ---------- lifecycle ----------
    def run(self):
        self._ffmpeg_proc = self._ffmpeg()
        threads = [
            threading.Thread(target=self.handshake_loop, daemon=True),
            threading.Thread(target=self.control_loop, daemon=True),
            threading.Thread(target=self.video_loop, daemon=True),
        ]
        for t in threads:
            t.start()
        print(f"[sim] car simulator up: ctl :{self.args.ctl_port} "
              f"hs :{self.args.hs_port} -> video to {self.args.dest}:{self.args.video_port}")
        if self.args.always_on:
            self.awake = self.streaming_wanted = True
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[sim] stopping")
            self.stop.set()
            self._ffmpeg_proc.terminate()


def main():
    p = argparse.ArgumentParser(description="WLtoys FPV car simulator")
    p.add_argument("--dest", default="127.0.0.1", help="cockpit IP (video destination)")
    p.add_argument("--video-port", type=int, default=1234)
    p.add_argument("--ctl-port", type=int, default=23458)
    p.add_argument("--hs-port", type=int, default=23459)
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=360)
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--crf", type=int, default=28)
    p.add_argument("--always-on", action="store_true",
                   help="stream immediately without handshake")
    CarSim(p.parse_args()).run()


if __name__ == "__main__":
    sys.exit(main())
