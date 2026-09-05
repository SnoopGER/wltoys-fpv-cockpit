"""RawFrameRelay GOP discipline + resync tests (Phase 3 video relay)."""
import unittest
from queue import Empty

from video_relay import RawFrameRelay, detect_codec, is_keyframe

# minimal Annex-B shaped payloads (NAL headers only — relay never decodes)
H264_SPS = b"\x00\x00\x00\x01\x67" + b"\xAA" * 20   # type 7
H264_IDR = b"\x00\x00\x00\x01\x65" + b"\xBB" * 40   # type 5
H264_P = b"\x00\x00\x00\x01\x41" + b"\xCC" * 30     # type 1
HEVC_IDR = b"\x00\x00\x00\x01\x26\x01" + b"\xDD" * 30  # type 19<<1


class CodecDetectTests(unittest.TestCase):
    def test_h264(self):
        self.assertEqual(detect_codec(H264_IDR), "h264")

    def test_hevc(self):
        self.assertEqual(detect_codec(HEVC_IDR), "hevc")

    def test_junk(self):
        self.assertIsNone(detect_codec(b"hello world, not video at all"))

    def test_keyframe_flags(self):
        self.assertTrue(is_keyframe(H264_IDR, "h264"))
        self.assertTrue(is_keyframe(H264_SPS, "h264"))
        self.assertFalse(is_keyframe(H264_P, "h264"))


class RelayTests(unittest.TestCase):
    def test_gop_gate(self):
        r = RawFrameRelay()
        sub = r.subscribe()
        r.feed(H264_P)                    # mid-GOP: must NOT be relayed
        with self.assertRaises(Empty):
            sub["q"].get_nowait()
        r.feed(H264_IDR)                  # IDR opens the GOP
        self.assertEqual(sub["q"].get_nowait(), H264_IDR)
        r.feed(H264_P)
        self.assertEqual(sub["q"].get_nowait(), H264_P)
        self.assertEqual(r.codec, "h264")

    def test_slow_consumer_resyncs_from_keyframe(self):
        r = RawFrameRelay()
        sub = r.subscribe(maxsize=2)
        r.feed(H264_IDR)
        r.feed(H264_P)
        r.feed(H264_P)
        r.feed(H264_P)                    # queue(2) overflow -> need_sync
        self.assertTrue(sub["need_sync"])
        frame = r.poll(sub, timeout=0.1)  # resync delivers backlog...
        self.assertEqual(frame, H264_IDR)
        frame2 = r.poll(sub, timeout=0.1)  # ...then queued continuation
        self.assertEqual(frame2, H264_P)

    def test_backlog_starts_at_keyframe(self):
        r = RawFrameRelay()
        r.feed(H264_P)                    # pre-GOP junk ignored
        r.feed(H264_IDR)
        r.feed(H264_P)
        backlog = r.backlog_from_keyframe()
        self.assertEqual(backlog[0], H264_IDR)
        self.assertEqual(len(backlog), 2)

    def test_codec_flip_restarts_gop(self):
        r = RawFrameRelay()
        sub = r.subscribe()
        r.feed(H264_IDR)
        r.feed(HEVC_IDR)                  # codec flip: drop, re-arm GOP gate
        sub["q"].get_nowait()
        with self.assertRaises(Empty):
            sub["q"].get_nowait()
        r.feed(H264_IDR)                  # h264 again accepted fresh
        self.assertEqual(sub["q"].get_nowait(), H264_IDR)

    def test_unsubscribe(self):
        r = RawFrameRelay()
        sub = r.subscribe()
        r.unsubscribe(sub)
        r.feed(H264_IDR)
        with self.assertRaises(Empty):
            sub["q"].get_nowait()


if __name__ == "__main__":
    unittest.main()
