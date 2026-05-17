"""
WLtoys FPV Car - H.264 → JPEG Video Decoder (Fixed v4)
Uses ffmpeg file output instead of pipe (pipe buffering kills continuous mjpeg).
Writes latest frame to temp file, reads it back as JPEG.
"""

import subprocess
import threading
import time
import os
import tempfile
from typing import Optional

JPEG_START = b'\xff\xd8'
JPEG_END = b'\xff\xd9'

FRAME_DIR = os.path.join(tempfile.gettempdir(), 'fpv_frames')


class VideoDecoder:
    """H.264 → JPEG decoder. Buffers until keyframe, then decodes via ffmpeg file output."""

    def __init__(self, width: int = 640, height: int = 360, quality: int = 5):
        self.width = width
        self.height = height
        self.quality = quality
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        self._frame_count = 0
        self._running = False
        self._got_keyframe = False
        self._input_file = os.path.join(FRAME_DIR, 'input.h264')
        self._output_file = os.path.join(FRAME_DIR, 'frame.jpg')
        self._frame_buffer = bytearray()
        self._decode_thread: Optional[threading.Thread] = None
        self._pending_frames: int = 0

        os.makedirs(FRAME_DIR, exist_ok=True)

    def start(self):
        if self._running:
            return
        self._running = True
        self._decode_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._decode_thread.start()

    def stop(self):
        self._running = False

    def feed_frame(self, h264_data: bytes):
        """Feed a H.264 frame. Buffers until keyframe, then accumulates for decode."""
        if not self._running:
            return

        # Check if keyframe (SPS NAL type 7)
        is_keyframe = (len(h264_data) > 4
                       and h264_data[0:4] == b'\x00\x00\x00\x01'
                       and (h264_data[4] & 0x1f) == 7)

        if not self._got_keyframe:
            if is_keyframe:
                self._got_keyframe = True
                with self._lock:
                    self._frame_buffer = bytearray(h264_data)
                    self._pending_frames = 1
            return

        with self._lock:
            self._frame_buffer.extend(h264_data)
            self._pending_frames += 1

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def _decode_loop(self):
        """Background thread: periodically decode accumulated H.264 frames to JPEG."""
        while self._running:
            with self._lock:
                pending = self._pending_frames

            if pending < 3:
                time.sleep(0.1)
                continue

            # Grab accumulated frames
            with self._lock:
                h264_data = bytes(self._frame_buffer)
                self._frame_buffer = bytearray()
                self._pending_frames = 0

            # Write to file
            try:
                with open(self._input_file, 'wb') as f:
                    f.write(h264_data)
            except Exception:
                continue

            # Decode last frame with ffmpeg
            try:
                subprocess.run(
                    [
                        'ffmpeg', '-hide_banner', '-loglevel', 'error',
                        '-y',
                        '-probesize', '32',
                        '-analyzeduration', '0',
                        '-f', 'h264',
                        '-i', self._input_file,
                        '-frames:v', '1',
                        '-update', '1',
                        '-q:v', str(self.quality),
                        '-vf', f'scale={self.width}:{self.height}',
                        self._output_file,
                    ],
                    capture_output=True,
                    timeout=3,
                )
            except Exception:
                continue

            # Read the decoded JPEG
            try:
                if os.path.exists(self._output_file):
                    with open(self._output_file, 'rb') as f:
                        jpeg_data = f.read()
                    if len(jpeg_data) > 100 and jpeg_data[:2] == JPEG_START:
                        self._frame_count += 1
                        with self._lock:
                            self._latest_jpeg = jpeg_data
            except Exception:
                pass

    @property
    def frame_count(self) -> int:
        return self._frame_count
