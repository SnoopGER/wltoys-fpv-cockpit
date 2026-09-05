"""Raw H.264/HEVC frame relay: UDP frames -> browser WebCodecs (Phase 3).

Taps the car's Annex-B frames *before* the PyAV->JPEG decode path and fans
them out to per-client bounded queues. The browser decodes with WebCodecs;
MJPEG stays as the universal fallback. Keeping the relay separate lets the
JPEG path run untouched while WebCodecs viewers are active.

Semantics:
- feed() starts accepting only at a keyframe (a decoder joining mid-GOP is
  broken until the next IDR — same rule as video_decoder.VideoDecoder).
- backlog keeps a short GOP window so a client that connects (or falls
  behind) can be resynced from the oldest retained keyframe.
- slow consumers are marked need_sync; on next poll they receive the
  backlog again instead of a torn P-frame stream.
"""
import threading
from collections import deque
from queue import Empty, Full, Queue


def detect_codec(data: bytes):
    """'h264' | 'hevc' | None from Annex-B NAL types."""
    for offset in _nal_offsets(data):
        if offset >= len(data):
            break
        b = data[offset]
        if (b & 0x1F) in (1, 5, 7, 8):
            return "h264"
        if ((b >> 1) & 0x3F) in (1, 19, 20, 21, 32, 33, 34):
            return "hevc"
    return None


def is_keyframe(data: bytes, codec: str) -> bool:
    for offset in _nal_offsets(data):
        if offset >= len(data):
            break
        b = data[offset]
        if codec == "h264" and (b & 0x1F) in (5, 7):
            return True
        if codec == "hevc" and ((b >> 1) & 0x3F) in (19, 20, 21, 32, 33):
            return True
    return False


def _nal_offsets(data: bytes):
    i = 0
    while i < len(data) - 4:
        if data[i:i + 4] == b"\x00\x00\x00\x01":
            yield i + 4
            i += 4
        elif data[i:i + 3] == b"\x00\x00\x01":
            yield i + 3
            i += 3
        else:
            i += 1


class RawFrameRelay:
    MAX_BACKLOG_BYTES = 1_500_000  # ~1-2 s of 1 Mbps video

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._subs = []  # list of {'q': Queue, 'need_sync': bool}
        self._backlog = deque()
        self._backlog_bytes = 0
        self._accepting = False
        self.codec = None
        self.frames_relayed = 0

    # ---- producer (car.on_frame fan-out) ----
    def feed(self, data: bytes):
        if not data:
            return
        codec = detect_codec(data)
        if codec is None:
            return  # unknown/junk frame — never poison the GOP
        if not self._accepting:
            if not is_keyframe(data, codec):
                return
            with self._cond:
                self._accepting = True
                self.codec = codec
                self._backlog.clear()
                self._backlog_bytes = 0
        if not self._accepting:
            return
        if codec != self.codec:
            # codec flipped mid-stream (rare) — restart GOP discipline
            with self._cond:
                self._accepting = False
            return
        with self._cond:
            self._backlog.append(data)
            self._backlog_bytes += len(data)
            while self._backlog_bytes > self.MAX_BACKLOG_BYTES and len(self._backlog) > 1:
                self._backlog_bytes -= len(self._backlog.popleft())
            self.frames_relayed += 1
            for sub in self._subs:
                try:
                    sub["q"].put_nowait(data)
                except Full:
                    sub["need_sync"] = True
            self._cond.notify_all()

    # ---- consumer (WebSocket writer) ----
    def subscribe(self, maxsize=90):
        sub = {"q": Queue(maxsize=maxsize), "need_sync": False}
        with self._cond:
            # seed current GOP so a fresh joiner can decode immediately
            frames = self._backlog_from_keyframe_locked()
            for d in frames:
                try:
                    sub["q"].put_nowait(d)
                except Full:
                    break
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub):
        with self._cond:
            if sub in self._subs:
                self._subs.remove(sub)

    def backlog_from_keyframe(self):
        with self._lock:
            return self._backlog_from_keyframe_locked()

    def _backlog_from_keyframe_locked(self):
        frames = list(self._backlog)
        kf = "hevc" if self.codec == "hevc" else "h264"
        for idx, d in enumerate(frames):
            if is_keyframe(d, kf):
                return frames[idx:]
        return []

    def poll(self, sub, timeout=0.5):
        """Yield frames: backlog when out of sync, then live frames.

        Returns the frame bytes; raises queue.Empty on poll timeout so the
        caller can re-check liveness/auth.
        """
        if sub["need_sync"]:
            sub["need_sync"] = False
            while True:  # drop stale frames queued before the desync
                try:
                    sub["q"].get_nowait()
                except Empty:
                    break
            for d in self.backlog_from_keyframe():
                try:
                    sub["q"].put_nowait(d)
                except Full:
                    break
        return sub["q"].get(timeout=timeout)
