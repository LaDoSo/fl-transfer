import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fltransfer import peaks  # noqa: E402


def wav(samples: bytes, kind=1, channels=1, rate=1000, bits=16, extensible=False) -> bytes:
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", 0xFFFE if extensible else kind, channels, rate, rate * block, block, bits)
    if extensible:
        fmt += struct.pack("<HHIH", 22, bits, 0, kind) + b"\x00" * 14
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"LIST" + struct.pack("<I", 3) + b"abc\x00"                # odd-sized chunk before the data
    body += b"data" + struct.pack("<I", len(samples)) + samples
    return b"RIFF" + struct.pack("<I", len(body)) + body


class PeaksTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def load(self, data: bytes, columns=10):
        path = Path(self.dir.name) / "a.wav"
        path.write_bytes(data)
        return peaks.read(path, columns)

    def test_16_bit_ramp(self):
        vals = [int(32767 * i / 999) for i in range(1000)]
        w = self.load(wav(struct.pack("<1000h", *vals)))
        self.assertEqual(len(w.columns), 10)
        self.assertAlmostEqual(w.seconds, 1.0)
        self.assertAlmostEqual(w.columns[0][0], 0.0, places=3)
        self.assertAlmostEqual(w.columns[-1][1], 1.0, places=3)
        self.assertTrue(all(a[1] <= b[1] for a, b in zip(w.columns, w.columns[1:])))

    def test_formats_agree(self):
        sine = [math.sin(i / 7.0) * 0.8 for i in range(700)]
        cases = {
            "pcm8": wav(bytes(int(128 + s * 127) for s in sine), bits=8),
            "pcm16": wav(struct.pack("<700h", *[int(s * 32767) for s in sine])),
            "pcm24": wav(b"".join(int(s * 8388607).to_bytes(3, "little", signed=True) for s in sine), bits=24),
            "pcm32": wav(struct.pack("<700i", *[int(s * 2147483647) for s in sine]), bits=32),
            "float32": wav(struct.pack("<700f", *sine), kind=3, bits=32),
            "float64": wav(struct.pack("<700d", *sine), kind=3, bits=64),
            "extensible": wav(struct.pack("<700h", *[int(s * 32767) for s in sine]), extensible=True),
        }
        for name, data in cases.items():
            w = self.load(data, 20)
            self.assertIsNotNone(w, name)
            self.assertAlmostEqual(max(c[1] for c in w.columns), 0.8, delta=0.02, msg=name)
            self.assertAlmostEqual(min(c[0] for c in w.columns), -0.8, delta=0.02, msg=name)

    def test_stereo_takes_both_channels(self):
        frames = b"".join(struct.pack("<hh", 0, 16384) for _ in range(100))
        w = self.load(wav(frames, channels=2), 4)
        self.assertEqual(w.channels, 2)
        self.assertAlmostEqual(w.seconds, 0.1)
        self.assertTrue(all(abs(hi - 0.5) < 0.001 and lo == 0 for lo, hi in w.columns))

    def test_not_a_wav(self):
        self.assertIsNone(self.load(b"ID3\x03 this is an mp3"))
        self.assertIsNone(self.load(b""))
        self.assertIsNone(peaks.read(Path(self.dir.name) / "missing.wav"))
        self.assertIsNone(self.load(wav(b"\x00" * 64, kind=85)))          # mp3 inside a wav container

    def test_short_and_truncated(self):
        self.assertEqual(len(self.load(wav(struct.pack("<3h", 1, 2, 3)), 50).columns), 3)
        data = wav(struct.pack("<100h", *range(100)))
        self.assertEqual(len(self.load(data[:-50], 10).columns), 10)       # data chunk longer than the file


if __name__ == "__main__":
    unittest.main()
