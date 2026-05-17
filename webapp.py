#!/usr/bin/env python3
"""
WLtoys FPV Car - Debug Cockpit Web Server
Flask app providing REST API + MJPEG video stream + real-time logs.
"""

import json
import time
import os
import sys
import signal
import threading
from flask import Flask, render_template, Response, jsonify, request, send_from_directory

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from car_protocol import CarProtocol, ConnectionState, SPS_PPS
from video_decoder import VideoDecoder

app = Flask(__name__, template_folder='templates', static_folder='static')

# ── Global State ───────────────────────────────────────────────────────

car = None
decoder = None

def init_car():
    global car, decoder
    if car is None:
        car = CarProtocol(
            car_ip=os.environ.get("FPV_CAR_IP", "172.16.11.1"),
            listen_port=int(os.environ.get("FPV_LISTEN_PORT", "1234")),
            on_log=lambda level, msg: None,
        )
    if decoder is None:
        decoder = VideoDecoder(width=640, height=360, quality=15)
    
    # Wire up frame delivery: car → decoder
    car.on_frame = lambda data: decoder.feed_frame(data)

# ── HTML Routes ────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

# ── API Routes ────────────────────────────────────────────────────────

@app.route('/api/connect', methods=['POST'])
def api_connect():
    init_car()
    success = car.connect()
    if success:
        decoder.start()
    return jsonify({"ok": success, "state": car.state.value})

@app.route('/api/disconnect', methods=['POST'])
def api_disconnect():
    global car, decoder
    if decoder:
        decoder.stop()
    if car:
        car.disconnect()
    return jsonify({"ok": True, "state": "disconnected"})

@app.route('/api/status')
def api_status():
    init_car()
    status = car.get_status()
    status['decoder_frames'] = decoder.frame_count if decoder else 0
    return jsonify(status)

@app.route('/api/logs')
def api_logs():
    init_car()
    logs = car.get_logs()
    return jsonify({"logs": logs})

@app.route('/api/command', methods=['POST'])
def api_command():
    """Send a command to the car. Body: {"command": "...", "speed": 100, "steer_range": 100}"""
    init_car()
    data = request.get_json(silent=True) or {}
    cmd = data.get('command', 'stop')
    speed = data.get('speed', 100)
    steer_range = data.get('steer_range', 100)
    success = car.send_command(cmd, speed=speed, steer_range=steer_range)
    return jsonify({"ok": success, "command": cmd, "speed": speed, "steer_range": steer_range})

@app.route('/api/send_raw', methods=['POST'])
def api_send_raw():
    """Send a raw hex packet to the car. Body: {"hex": "...", "port": 23458}"""
    init_car()
    data = request.get_json(silent=True) or {}
    hex_data = data.get('hex', '')
    port = data.get('port', 23458)
    
    try:
        import socket
        raw = bytes.fromhex(hex_data.replace(' ', '').replace(':', ''))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(raw, (car.car_ip, port))
        sock.close()
        car.log("TX", f"Raw {len(raw)}B → {car.car_ip}:{port} [{hex_data[:40]}...]")
        return jsonify({"ok": True, "sent": len(raw)})
    except Exception as e:
        car.log("ERROR", f"Raw send failed: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/protocol')
def api_protocol():
    """Return protocol documentation."""
    return jsonify({
        "header": {
            "size": 32,
            "fields": [
                {"offset": 0, "size": 4, "name": "magic", "type": "uint32_le", "value": "0x5aa56cc6"},
                {"offset": 4, "size": 4, "name": "frame_size", "type": "uint32_le", "desc": "Total H.264 frame size"},
                {"offset": 8, "size": 4, "name": "seq_num", "type": "uint32_le", "desc": "Frame sequence number"},
                {"offset": 12, "size": 4, "name": "timestamp", "type": "uint32_le", "desc": "Timestamp/counter"},
                {"offset": 16, "size": 4, "name": "padding", "type": "uint32_le", "value": "0x00000000"},
                {"offset": 20, "size": 2, "name": "total_frags", "type": "uint16_le", "desc": "Fragment count"},
                {"offset": 22, "size": 2, "name": "frag_idx", "type": "uint16_le", "desc": "Fragment index (0-based)"},
                {"offset": 24, "size": 4, "name": "data_offset", "type": "uint32_le", "desc": "Byte offset in frame"},
                {"offset": 28, "size": 4, "name": "data_len", "type": "uint32_le", "desc": "Payload length"},
            ]
        },
        "ports": {
            "video": 1234,
            "handshake": 23459,
            "control": 23458,
        },
        "codec": {
            "type": "H.264",
            "profile": "Constrained Baseline",
            "level": "3.1",
            "resolution": "640x360",
            "fps": 20,
        },
        "handshake": {
            "wake": HANDSHAKE_WAKE.hex(),
            "trigger": HANDSHAKE_TRIGGER.hex(),
        },
        "heartbeat": HEARTBEAT.hex(),
        "sps_pps": SPS_PPS.hex(),
    })

# ── Video Stream ───────────────────────────────────────────────────────

@app.route('/api/stream')
def api_stream():
    """MJPEG video stream endpoint — rate-limited to ~10fps to avoid CPU overload."""
    init_car()

    def generate():
        while True:
            jpeg = decoder.get_latest_jpeg() if decoder else None
            if jpeg:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n'
                       + jpeg + b'\r\n')
            # Rate limit: ~10fps max to avoid CPU overload
            time.sleep(0.1)

    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        }
    )

# ── Main ───────────────────────────────────────────────────────────────

# Import hex strings from car_protocol for the /api/protocol endpoint
from car_protocol import HANDSHAKE_WAKE, HANDSHAKE_TRIGGER, HEARTBEAT

if __name__ == '__main__':
    print("=" * 60)
    print("  WLtoys FPV Car - Debug Cockpit")
    print("  http://localhost:5555")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5555, threaded=True, debug=False)
