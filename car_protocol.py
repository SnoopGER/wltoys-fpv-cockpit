"""
WLtoys FPV Car Protocol Handler
Full protocol implementation: UDP fragments → H.264 Annex B frames.

Header structure (32 bytes):
  Bytes 0-3:   Magic (0x5aa56cc6)
  Bytes 4-7:   Total frame size (32-bit LE)
  Bytes 8-11:  Frame sequence number (32-bit LE)
  Bytes 12-15: Timestamp/counter (32-bit LE)
  Bytes 16-19: Padding (usually 0)
  Bytes 20-21: Total fragment count (16-bit LE)
  Bytes 22-23: Fragment index (16-bit LE, 0-based)
  Bytes 24-27: Data offset within frame (32-bit LE)
  Bytes 28-31: Data length of this fragment (32-bit LE)
  Bytes 32+:   Raw H.264 Annex B data
"""

import platform
import shutil
import socket
import struct
import threading
import time
import logging
from collections import defaultdict
from queue import Queue, Empty
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

HEADER_SIZE = 32
MAGIC = 0x5aa56cc6

# Known SPS+PPS from the car (H.264 Baseline Profile, Level 3.1, 640x360)
SPS_PPS = bytes.fromhex(
    "000000016742001f963540a00b74dc04040408"
    "0000000168ce31b2"
)

# Handshake packets (port 23459)
HANDSHAKE_WAKE = bytes.fromhex("a88a210006000000010000000000")
HANDSHAKE_TRIGGER = bytes.fromhex("a88a200008000000010002000000d204")

# Heartbeat packet (port 23458)
HEARTBEAT = bytes.fromhex("ca47d500000000006680808000008099")

# Motor command template (port 23458)
# Based on heartbeat, with motor bytes modified
MOTOR_CMD_BASE = bytes.fromhex("ca47d50000000000")

# Direction constants for motor control
# The heartbeat bytes 8-13 control motors: [throttle, steering, ...]
# Byte 8 = throttle (0x80 = neutral, 0x00 = full reverse, 0xFF = full forward)
# Byte 9 = steering (0x80 = center, 0x00 = full left, 0xFF = full right)


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STREAMING = "streaming"
    ERROR = "error"


@dataclass
class PacketInfo:
    seq_num: int
    frag_idx: int
    total_frags: int
    data_offset: int
    data_len: int
    frame_size: int
    timestamp: int
    data: bytes


@dataclass
class ConnectionStats:
    packets_received: int = 0
    frames_assembled: int = 0
    frames_dropped: int = 0
    i_frames: int = 0
    p_frames: int = 0
    bytes_received: int = 0
    current_fps: float = 0.0
    current_bitrate: float = 0.0
    last_frame_size: int = 0
    last_seq_num: int = 0
    uptime: float = 0.0
    heartbeat_count: int = 0


class FrameAssembler:
    """Assembles fragmented UDP packets into complete H.264 frames."""
    
    def __init__(self, max_pending: int = 20):
        self.frames: dict[int, dict[int, bytes]] = {}
        self.frame_meta: dict[int, dict] = {}
        self.max_pending = max_pending
    
    def add_fragment(self, pkt: PacketInfo) -> Optional[bytes]:
        """Add a fragment. Returns complete frame data if ready, else None."""
        seq = pkt.seq_num
        
        if seq not in self.frames:
            self.frames[seq] = {}
            self.frame_meta[seq] = {
                'total_frags': pkt.total_frags,
                'frame_size': pkt.frame_size,
                'timestamp': pkt.timestamp,
            }
        
        self.frames[seq][pkt.frag_idx] = pkt.data
        
        # Check if frame is complete
        meta = self.frame_meta[seq]
        if len(self.frames[seq]) >= meta['total_frags']:
            # Reassemble in order
            frame_data = bytearray()
            for i in range(meta['total_frags']):
                if i in self.frames[seq]:
                    frame_data.extend(self.frames[seq][i])
                else:
                    # Missing fragment — discard
                    self._cleanup(seq)
                    return None
            
            self._cleanup(seq)
            return bytes(frame_data)
        
        return None
    
    def _cleanup(self, seq: int):
        """Remove a frame and any stale pending frames."""
        self.frames.pop(seq, None)
        self.frame_meta.pop(seq, None)
        
        # Remove frames older than max_pending
        if len(self.frames) > self.max_pending:
            oldest = min(self.frames.keys())
            self.frames.pop(oldest, None)
            self.frame_meta.pop(oldest, None)


class CarProtocol:
    """Full WLtoys FPV Car protocol handler."""
    
    def __init__(self, car_ip: str = "172.16.11.1", listen_port: int = 1234,
                 on_log: Optional[Callable] = None, on_frame: Optional[Callable] = None):
        self.car_ip = car_ip
        self.listen_port = listen_port
        self.on_log = on_log or (lambda level, msg: None)
        self.on_frame = on_frame or (lambda data: None)
        
        self.state = ConnectionState.DISCONNECTED
        self.stats = ConnectionStats()
        
        self.assembler = FrameAssembler(max_pending=20)
        self.frame_queue: Queue = Queue(maxsize=30)
        self.log_queue: Queue = Queue(maxsize=1000)
        
        self._recv_sock: Optional[socket.socket] = None
        self._hb_sock: Optional[socket.socket] = None
        self._cmd_sock: Optional[socket.socket] = None
        self._hb_thread: Optional[threading.Thread] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._start_time: float = 0
        self._stats_lock = threading.Lock()
        self._last_motor_cmd: bytes = HEARTBEAT  # Heartbeat carries current motor state
    
    def log(self, level: str, msg: str):
        """Log a message to both callback and queue."""
        ts = time.strftime("%H:%M:%S")
        entry = {"ts": ts, "level": level, "msg": msg}
        try:
            self.log_queue.put_nowait(entry)
        except Exception:
            pass
        self.on_log(level, msg)
    
    def get_logs(self) -> list:
        """Drain all pending log entries."""
        logs = []
        while True:
            try:
                logs.append(self.log_queue.get_nowait())
            except Empty:
                break
        return logs
    
    def connect(self) -> bool:
        """Connect to the car: send handshake, start heartbeat & receiver."""
        if self.state in (ConnectionState.CONNECTED, ConnectionState.STREAMING):
            self.log("WARN", "Already connected")
            return True
        
        self.state = ConnectionState.CONNECTING
        self._stop_event.clear()
        self.log("INFO", f"Connecting to car at {self.car_ip}...")
        
        # Check reachability
        import subprocess
        if shutil.which("ping"):
            try:
                ping_cmd = (
                    ["ping", "-n", "1", "-w", "2000", self.car_ip]
                    if platform.system().lower() == "windows"
                    else ["ping", "-c", "1", "-W", "2", self.car_ip]
                )
                result = subprocess.run(ping_cmd, capture_output=True, timeout=5)
                if result.returncode != 0:
                    self.log("WARN", f"Ping did not reach {self.car_ip}, trying UDP handshake anyway")
                else:
                    self.log("OK", f"Car reachable at {self.car_ip}")
            except Exception as e:
                self.log("WARN", f"Ping check failed, trying UDP handshake anyway: {e}")
        else:
            self.log("WARN", "Ping command not available, trying UDP handshake anyway")
        
        # Send handshake
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for i in range(3):
                sock.sendto(HANDSHAKE_WAKE, (self.car_ip, 23459))
                self.log("TX", f"Handshake wake #{i+1} → {self.car_ip}:23459")
                time.sleep(0.2)
                sock.sendto(HANDSHAKE_TRIGGER, (self.car_ip, 23459))
                self.log("TX", f"Handshake trigger #{i+1} → {self.car_ip}:23459")
                time.sleep(0.3)
            sock.close()
        except Exception as e:
            self.log("ERROR", f"Handshake failed: {e}")
            self.state = ConnectionState.ERROR
            return False
        
        self.log("OK", "Handshake sent (3x wake + trigger)")
        
        # Start heartbeat thread
        self._hb_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()
        self.log("OK", "Heartbeat thread started (port 23458)")
        
        # Start receiver thread
        try:
            self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
            self._recv_sock.bind(('', self.listen_port))
            self._recv_sock.settimeout(0.5)
        except Exception as e:
            self.log("ERROR", f"Failed to bind port {self.listen_port}: {e}")
            self.state = ConnectionState.ERROR
            return False
        
        self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._recv_thread.start()
        self._start_time = time.time()
        
        self.state = ConnectionState.CONNECTED
        self.log("OK", f"Listening for video on port {self.listen_port}")
        return True
    
    def disconnect(self):
        """Disconnect from the car."""
        self.log("INFO", "Disconnecting...")
        self._stop_event.set()
        
        if self._recv_sock:
            try:
                self._recv_sock.close()
            except Exception:
                pass
            self._recv_sock = None
        
        if self._hb_sock:
            try:
                self._hb_sock.close()
            except Exception:
                pass
            self._hb_sock = None

        if self._cmd_sock:
            try:
                self._cmd_sock.close()
            except Exception:
                pass
            self._cmd_sock = None
        
        self.state = ConnectionState.DISCONNECTED
        self.log("OK", "Disconnected")
    
    def send_command(self, command: str, speed: int = 100, steer_range: int = 100) -> bool:
        """Send a motor/control command to the car.

        Decoded from PCAP capture of actual Android app traffic:
        Packet: ca 47 d5 00 00 00 00 00 66 [STR] [THR] 80 00 00 [CHK] 99

        Byte 9  = STEERING: 0x00=full left, 0x80=center, 0xFF=full right
        Byte 10 = THROTTLE: 0x00=full forward, 0x80=neutral, 0xFF=full reverse
        Byte 14 = Checksum (copy of byte 9)

        Args:
            command: "forward"|"reverse"|"left"|"right"|"stop"
            speed: 0-100, throttle power percentage (default 100 = full)
            steer_range: 0-100, steering angle percentage (default 100 = max)

        Key insight: Car expects CONTINUOUS commands at 20Hz (every 50ms).
        A single packet makes it move briefly then return to neutral.
        The app sends smooth ramps, not instant jumps.
        """
        if self.state not in (ConnectionState.CONNECTED, ConnectionState.STREAMING):
            self.log("WARN", "Not connected — cannot send command")
            return False

        # Clamp percentages
        speed = max(0, min(100, speed))
        steer_range = max(0, min(100, steer_range))

        # Center = 0x80 (128), max deflection = 127
        # CONFIRMED from working PCAP captures:
        #   HIGH values (0x80→0xFF) = forward / right
        #   LOW values  (0x00→0x80) = reverse / left
        # Old working values: forward=0xC0, reverse=0x50, left=0x50, right=0xB0
        throttle_deflect = int(127 * speed / 100)
        steer_deflect = int(127 * steer_range / 100)

        str_val = 0x80  # center
        thr_val = 0x80  # neutral

        if command == "forward":
            str_val = 0x80
            thr_val = 0x80 + throttle_deflect
        elif command == "reverse":
            str_val = 0x80
            thr_val = 0x80 - throttle_deflect
        elif command == "left":
            str_val = 0x80 - steer_deflect
            thr_val = 0x80
        elif command == "right":
            str_val = 0x80 + steer_deflect
            thr_val = 0x80
        elif command == "forward_left":
            str_val = 0x80 - steer_deflect
            thr_val = 0x80 + throttle_deflect
        elif command == "forward_right":
            str_val = 0x80 + steer_deflect
            thr_val = 0x80 + throttle_deflect
        elif command == "reverse_left":
            str_val = 0x80 - steer_deflect
            thr_val = 0x80 - throttle_deflect
        elif command == "reverse_right":
            str_val = 0x80 + steer_deflect
            thr_val = 0x80 - throttle_deflect
        elif command == "stop":
            str_val = 0x80
            thr_val = 0x80
        else:
            self.log("WARN", f"Unknown command: {command}")
            return False

        # Clamp to byte range
        str_val = max(0, min(255, str_val))
        thr_val = max(0, min(255, thr_val))
        # Checksum formula from PCAP analysis: B14 = B9 XOR B10 XOR 0x80
        # Verified against 402 captured packets including 33 dual-axis commands
        chk = str_val ^ thr_val ^ 0x80

        try:
            hdr = bytes.fromhex("ca47d5000000000066")
            cmd = hdr + bytes([str_val, thr_val, 0x80, 0x00, 0x00, chk, 0x99])
            if self._cmd_sock is None:
                self._cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._cmd_sock.sendto(cmd, (self.car_ip, 23458))
            self._last_motor_cmd = cmd  # Store for heartbeat reinforcement
            return True
        except Exception as e:
            self.log("ERROR", f"Command failed: {e}")
            return False

    def toggle_lights(self, on: bool = True) -> bool:
        """Toggle car headlights/LEDs.

        NOTE: The exact light command is NOT documented in the protocol.
        This sends a common WLtoys light toggle pattern (byte 8 = 0x67).
        If lights don't respond, capture the light button press from the
        Android app (com.lg.wltechfpvcar) and update the hex below.
        """
        if self.state not in (ConnectionState.CONNECTED, ConnectionState.STREAMING):
            self.log("WARN", "Not connected — cannot toggle lights")
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Attempt: command type 0x67, byte 9 = 0x01 (on) or 0x00 (off)
            light_byte = 0x01 if on else 0x00
            cmd = bytes.fromhex("ca47d50000000000") + bytes([0x67, light_byte, 0x80, 0x80, 0x00, 0x00, 0x00, 0x99])
            sock.sendto(cmd, (self.car_ip, 23458))
            sock.close()
            self.log("TX", f"Lights {'ON' if on else 'OFF'} [{cmd.hex()}]")
            return True
        except Exception as e:
            self.log("ERROR", f"Lights command failed: {e}")
            return False
    
    def get_frame(self, timeout: float = 1.0) -> Optional[bytes]:
        """Get the next complete H.264 frame (blocking)."""
        try:
            return self.frame_queue.get(timeout=timeout)
        except Empty:
            return None
    
    def get_status(self) -> dict:
        """Get current connection status and stats."""
        with self._stats_lock:
            uptime = time.time() - self._start_time if self._start_time else 0
            return {
                "state": self.state.value,
                "car_ip": self.car_ip,
                "listen_port": self.listen_port,
                "packets_received": self.stats.packets_received,
                "frames_assembled": self.stats.frames_assembled,
                "frames_dropped": self.stats.frames_dropped,
                "i_frames": self.stats.i_frames,
                "p_frames": self.stats.p_frames,
                "bytes_received": self.stats.bytes_received,
                "current_fps": self.stats.current_fps,
                "current_bitrate": self.stats.current_bitrate,
                "last_frame_size": self.stats.last_frame_size,
                "last_seq_num": self.stats.last_seq_num,
                "uptime": round(uptime, 1),
                "heartbeat_count": self.stats.heartbeat_count,
                "pending_frames": len(self.assembler.frames),
            }
    
    def _heartbeat_loop(self):
        """Background thread: send heartbeats every 0.5s."""
        count = 0
        while not self._stop_event.is_set():
            try:
                if self._hb_sock:
                    self._hb_sock.sendto(self._last_motor_cmd, (self.car_ip, 23458))
                    count += 1
                    with self._stats_lock:
                        self.stats.heartbeat_count = count
            except Exception:
                pass
            self._stop_event.wait(0.5)
    
    def _receive_loop(self):
        """Background thread: receive UDP packets and assemble frames."""
        fps_counter = 0
        fps_start = time.time()
        bytes_counter = 0
        
        while not self._stop_event.is_set():
            try:
                if not self._recv_sock:
                    break
                data, addr = self._recv_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            
            if addr[0] != self.car_ip:
                continue
            
            with self._stats_lock:
                self.stats.packets_received += 1
                self.stats.bytes_received += len(data)
            
            # Parse packet
            pkt = self._parse_packet(data)
            if pkt is None:
                continue
            
            # Assemble frame
            complete_frame = self.assembler.add_fragment(pkt)
            
            if complete_frame is not None:
                with self._stats_lock:
                    self.stats.frames_assembled += 1
                    self.stats.last_frame_size = len(complete_frame)
                    self.stats.last_seq_num = pkt.seq_num
                    
                    # Count NAL types
                    nals = self._count_nals(complete_frame)
                    self.stats.i_frames += nals.get(5, 0)
                    self.stats.p_frames += nals.get(1, 0)
                    
                    # FPS calculation
                    fps_counter += 1
                    bytes_counter += len(complete_frame)
                    elapsed = time.time() - fps_start
                    if elapsed >= 1.0:
                        self.stats.current_fps = fps_counter / elapsed
                        self.stats.current_bitrate = (bytes_counter * 8) / (elapsed * 1000)  # kbps
                        fps_counter = 0
                        bytes_counter = 0
                        fps_start = time.time()
                
                if self.state == ConnectionState.CONNECTED:
                    self.state = ConnectionState.STREAMING
                    self.log("OK", "Video stream detected!")
                
                # Deliver frame
                try:
                    self.frame_queue.put_nowait(complete_frame)
                except Exception:
                    try:
                        self.frame_queue.get_nowait()  # Drop oldest
                        self.frame_queue.put_nowait(complete_frame)
                    except Exception:
                        pass
                    with self._stats_lock:
                        self.stats.frames_dropped += 1
                
                self.on_frame(complete_frame)
    
    def _parse_packet(self, pkt: bytes) -> Optional[PacketInfo]:
        """Parse a UDP packet into a PacketInfo."""
        if len(pkt) < HEADER_SIZE:
            return None
        
        magic = struct.unpack('<I', pkt[0:4])[0]
        if magic != MAGIC:
            return None
        
        frame_size = struct.unpack('<I', pkt[4:8])[0]
        seq_num = struct.unpack('<I', pkt[8:12])[0]
        timestamp = struct.unpack('<I', pkt[12:16])[0]
        total_frags = struct.unpack('<H', pkt[20:22])[0]
        frag_idx = struct.unpack('<H', pkt[22:24])[0]
        data_offset = struct.unpack('<I', pkt[24:28])[0]
        data_len = struct.unpack('<I', pkt[28:32])[0]
        
        if data_len > 1500 or data_len == 0:
            return None
        
        fragment_data = pkt[32:32 + data_len]
        
        return PacketInfo(
            seq_num=seq_num,
            frag_idx=frag_idx,
            total_frags=total_frags,
            data_offset=data_offset,
            data_len=data_len,
            frame_size=frame_size,
            timestamp=timestamp,
            data=fragment_data,
        )
    
    def _count_nals(self, data: bytes) -> dict:
        """Count NAL unit types in H.264 data."""
        counts = {}
        i = 0
        while i < len(data) - 4:
            if data[i:i+4] == b'\x00\x00\x00\x01':
                if i + 4 < len(data):
                    ntype = data[i+4] & 0x1f
                    counts[ntype] = counts.get(ntype, 0) + 1
                i += 4
            elif data[i:i+3] == b'\x00\x00\x01':
                if i + 3 < len(data):
                    ntype = data[i+3] & 0x1f
                    counts[ntype] = counts.get(ntype, 0) + 1
                i += 3
            else:
                i += 1
        return counts
