#!/usr/bin/env python3
"""
No-auth FPV stream bridge for VLC/OBS.

Receives the WLtoys UDP video stream, decodes it, and exposes a simple MJPEG
HTTP stream. This intentionally has no dashboard, queue, Discord, or auth.
"""

import argparse
import os
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify

from car_protocol import CarProtocol
from video_decoder import VideoDecoder

load_dotenv(Path(__file__).parent / ".env.local")

app = Flask(__name__)
car = None
decoder = None
started_at = time.time()


def build_parser():
    parser = argparse.ArgumentParser(description="WLtoys FPV MJPEG stream bridge for VLC/OBS")
    parser.add_argument("--host", default=os.environ.get("STREAM_BRIDGE_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("STREAM_BRIDGE_PORT", "8080")))
    parser.add_argument("--car-ip", default=os.environ.get("STREAM_BRIDGE_CAR_IP", os.environ.get("FPV_CAR_IP", "172.16.11.1")))
    parser.add_argument("--bind-ip", default=os.environ.get("STREAM_BRIDGE_BIND_IP", ""))
    parser.add_argument("--listen-port", type=int, default=int(os.environ.get("STREAM_BRIDGE_LISTEN_PORT", os.environ.get("FPV_LISTEN_PORT", "1234"))))
    parser.add_argument("--width", type=int, default=int(os.environ.get("STREAM_BRIDGE_WIDTH", "640")))
    parser.add_argument("--height", type=int, default=int(os.environ.get("STREAM_BRIDGE_HEIGHT", "360")))
    parser.add_argument("--quality", type=int, default=int(os.environ.get("STREAM_BRIDGE_JPEG_QUALITY", os.environ.get("JPEG_QUALITY", "65"))))
    parser.add_argument("--no-connect", action="store_true", help="Start HTTP server without sending car handshake")
    return parser


def init_stream(args):
    global car, decoder
    decoder = VideoDecoder(width=args.width, height=args.height, quality=args.quality)
    car = CarProtocol(
        car_ip=args.car_ip,
        listen_port=args.listen_port,
        bind_ip=args.bind_ip,
        name="stream-bridge",
    )
    car.on_frame = decoder.feed_frame
    decoder.start()
    if not args.no_connect:
        car.connect()


@app.route("/")
def index():
    return (
        "WLtoys FPV Stream Bridge\n\n"
        "MJPEG stream:\n"
        "  /stream.mjpg\n\n"
        "Status:\n"
        "  /status\n",
        200,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


@app.route("/stream.mjpg")
@app.route("/stream")
def stream():
    def generate():
        last_count = -1
        while True:
            jpeg = decoder.get_latest_jpeg() if decoder else None
            if jpeg:
                frame_count = decoder.frame_count
                if frame_count != last_count:
                    last_count = frame_count
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                    )
            time.sleep(0.005)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.route("/status")
def status():
    car_status = car.get_status() if car else {}
    car_status["decoder_frames"] = decoder.frame_count if decoder else 0
    car_status["decoder_codec"] = decoder.codec_name if decoder else None
    car_status["bridge_uptime"] = round(time.time() - started_at, 1)
    car_status["stream_url"] = "/stream.mjpg"
    return jsonify(car_status)


def shutdown(*_args):
    if car:
        try:
            car.disconnect()
        except Exception:
            pass
    if decoder:
        try:
            decoder.stop()
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    args = build_parser().parse_args()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    init_stream(args)
    print("=" * 60)
    print("WLtoys FPV Stream Bridge")
    print(f"  Stream: http://localhost:{args.port}/stream.mjpg")
    print(f"  Status: http://localhost:{args.port}/status")
    print(f"  Car:    {args.car_ip} UDP video {args.listen_port}")
    print("=" * 60)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
