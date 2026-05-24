"""
WLtoys FPV Car - H.264/H.265 -> JPEG Video Decoder.

Uses PyAV for in-process decode and PIL for JPEG encode. Every frame is fed
to the codec in order because inter frames depend on previous frames.
"""

import os
import threading
import io
from typing import Optional
from queue import Queue, Empty

import av
from PIL import Image

JPEG_START = b'\xff\xd8'


class VideoDecoder:
    """H.264/H.265 -> JPEG decoder using PyAV."""

    def __init__(self, width: int = 640, height: int = 360, quality: int = 75):
        self.width = width
        self.height = height
        self.quality = int(os.environ.get("JPEG_QUALITY", quality))
        self._lock = threading.Lock()
        self._latest_jpeg: Optional[bytes] = None
        self._frame_count = 0
        self._running = False
        self._got_keyframe = False
        self._thread: Optional[threading.Thread] = None
        self._feed_queue: Queue = Queue(maxsize=int(os.environ.get("VIDEO_DECODE_QUEUE", "8")))
        self._codec_name = os.environ.get("VIDEO_CODEC", "auto").strip().lower()
        self._detected_codec: Optional[str] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def feed_frame(self, video_data: bytes):
        """Feed a complete Annex B H.264 or H.265 frame."""
        if not self._running:
            return

        codec = self._detect_codec(video_data)
        if codec is None:
            return

        if not self._got_keyframe:
            if self._is_keyframe(video_data, codec):
                self._got_keyframe = True
            else:
                return

        if self._feed_queue.full():
            try:
                self._feed_queue.get_nowait()
            except Empty:
                pass

        try:
            self._feed_queue.put_nowait((codec, video_data))
        except Exception:
            pass

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def _decode_loop(self):
        codec_ctx = None
        active_codec = None

        while self._running:
            try:
                codec, video_data = self._feed_queue.get(timeout=0.5)
            except Empty:
                continue

            try:
                if codec_ctx is None or active_codec != codec:
                    codec_ctx = av.CodecContext.create(codec, 'r')
                    active_codec = codec

                frames = codec_ctx.decode(av.Packet(video_data))

                for frame in frames:
                    arr = frame.to_ndarray(format='bgr24')

                    if frame.width != self.width or frame.height != self.height:
                        img = Image.fromarray(arr[:, :, ::-1])
                        img = img.resize((self.width, self.height), Image.NEAREST)
                    else:
                        img = Image.fromarray(arr[:, :, ::-1])

                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=self.quality)
                    jpeg_data = buf.getvalue()

                    if len(jpeg_data) > 100 and jpeg_data[:2] == JPEG_START:
                        self._frame_count += 1
                        with self._lock:
                            self._latest_jpeg = jpeg_data

            except av.InvalidDataError:
                continue
            except av.EOFError:
                codec_ctx = None
                active_codec = None
                self._got_keyframe = False
                continue
            except Exception:
                codec_ctx = None
                active_codec = None
                self._got_keyframe = False
                continue

    def _detect_codec(self, data: bytes) -> Optional[str]:
        if self._codec_name in {"h264", "hevc"}:
            self._detected_codec = self._codec_name
            return self._codec_name

        h264_types = self._h264_nal_types(data)
        if any(t in {1, 5, 7, 8} for t in h264_types):
            self._detected_codec = "h264"
            return "h264"

        h265_types = self._h265_nal_types(data)
        if any(t in {1, 19, 20, 21, 32, 33, 34} for t in h265_types):
            self._detected_codec = "hevc"
            return "hevc"

        return self._detected_codec

    def _is_keyframe(self, data: bytes, codec: str) -> bool:
        if codec == "h264":
            return any(t in {5, 7} for t in self._h264_nal_types(data))
        if codec == "hevc":
            return any(t in {19, 20, 21, 32, 33} for t in self._h265_nal_types(data))
        return False

    def _nal_start_offsets(self, data: bytes):
        i = 0
        while i < len(data) - 4:
            if data[i:i + 4] == b'\x00\x00\x00\x01':
                yield i + 4
                i += 4
            elif data[i:i + 3] == b'\x00\x00\x01':
                yield i + 3
                i += 3
            else:
                i += 1

    def _h264_nal_types(self, data: bytes) -> list[int]:
        return [data[offset] & 0x1f for offset in self._nal_start_offsets(data) if offset < len(data)]

    def _h265_nal_types(self, data: bytes) -> list[int]:
        return [(data[offset] >> 1) & 0x3f for offset in self._nal_start_offsets(data) if offset < len(data)]

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def codec_name(self) -> str:
        return self._detected_codec or self._codec_name
