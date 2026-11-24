import struct
import sys
import tempfile
import time
import unittest
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fltransfer import audition  # noqa: E402
from fltransfer.audition import AuditionError, Pcm, Player, decode  # noqa: E402


def riff(fmt: bytes, data: bytes, extra: bytes = b"") -> bytes:
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def fmt_chunk(kind, channels, rate, bits, extensible=False):
    block = channels * bits // 8
    head = struct.pack("<HHIIHH", 0xFFFE if extensible else kind, channels, rate, rate * block, block, bits)
    if extensible:
        head += struct.pack("<HHIH", 22, bits, 3, kind) + bytes(14)
    return head


def shorts(pcm: Pcm) -> list[int]:
    out = array("h")
    out.frombytes(pcm.data)
    if sys.byteorder == "big":
        out.byteswap()
    return list(out)


class DecodeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def write(self, name: str, raw: bytes) -> Path:
        path = self.root / name
        path.write_bytes(raw)
        return path

    def test_16_bit_passes_through(self):
        path = self.root / "a.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
            w.writeframes(struct.pack("<6h", 1, -1, 300, -300, 32767, -32768))
        pcm = decode(path)
        self.assertEqual((pcm.rate, pcm.channels), (44100, 2))
        self.assertEqual(shorts(pcm), [1, -1, 300, -300, 32767, -32768])
        self.assertAlmostEqual(pcm.seconds, 3 / 44100)
        self.assertFalse(pcm.cut)

    def test_8_bit_is_unsigned(self):
        pcm = decode(self.write("b.wav", riff(fmt_chunk(1, 1, 8000, 8), bytes([0, 128, 255, 129]))))
        self.assertEqual(shorts(pcm), [-32768, 0, 32512, 256])

    def test_24_and_32_bit_keep_the_top_two_bytes(self):
        frames24 = b"".join(int(v).to_bytes(3, "little", signed=True) for v in (0x123456, -0x123456, 0x7FFFFF))
        pcm = decode(self.write("c.wav", riff(fmt_chunk(1, 1, 48000, 24), frames24)))
        self.assertEqual(shorts(pcm), [0x1234, -0x1235, 0x7FFF])
        frames32 = struct.pack("<4i", 0x12345678, -0x12345678, 1, -2147483648)
        pcm = decode(self.write("d.wav", riff(fmt_chunk(1, 2, 48000, 32, extensible=True), frames32)))
        self.assertEqual((pcm.channels, shorts(pcm)), (2, [0x1234, -0x1235, 0, -32768]))

    def test_float_is_scaled_and_clipped(self):
        values = [0.0, 0.5, -0.5, 1.0, -1.0, 3.0, -3.0, float("nan")]
        pcm = decode(self.write("e.wav", riff(fmt_chunk(3, 1, 44100, 32), struct.pack("<8f", *values))))
        self.assertEqual(shorts(pcm), [0, 16383, -16383, 32767, -32768, 32767, -32768, 0])
        pcm = decode(self.write("f.wav", riff(fmt_chunk(3, 2, 44100, 64), struct.pack("<4d", 0.25, -0.25, 1.5, 0.0))))
        self.assertEqual(shorts(pcm), [8191, -8191, 32767, 0])

    def test_more_than_two_channels_keeps_the_first_two(self):
        frames = struct.pack("<8h", 1, 2, 3, 4, 5, 6, 7, 8)                  # 4 channels, 2 frames
        pcm = decode(self.write("g.wav", riff(fmt_chunk(1, 4, 44100, 16), frames)))
        self.assertEqual((pcm.channels, shorts(pcm)), (2, [1, 2, 5, 6]))
        floats = struct.pack("<6f", 0.5, 0.0, -0.5, 1.0, 0.0, -1.0)          # 3 channels, 2 frames
        pcm = decode(self.write("h.wav", riff(fmt_chunk(3, 3, 44100, 32), floats)))
        self.assertEqual(shorts(pcm), [16383, 0, 32767, 0])

    def test_long_file_is_cut_at_the_limit(self):
        path = self.write("i.wav", riff(fmt_chunk(1, 1, 1000, 16), bytes(2 * 5000)))      # 5 seconds
        pcm = decode(path, limit=2.0)
        self.assertEqual(len(pcm.data), 2 * 2000)
        self.assertAlmostEqual(pcm.seconds, 5.0)
        self.assertAlmostEqual(pcm.played, 2.0)
        self.assertTrue(pcm.cut)

    def test_long_file_from_the_middle(self):
        frames = b"".join(struct.pack("<h", i) for i in range(5000))
        path = self.write("o.wav", riff(fmt_chunk(1, 1, 1000, 16), frames))               # 5 seconds
        pcm = decode(path, limit=2.0, at=0.5)
        self.assertEqual((pcm.start, pcm.played, pcm.seconds), (2.5, 2.0, 5.0))
        self.assertEqual(shorts(pcm)[:3], [2500, 2501, 2502])
        self.assertTrue(pcm.cut)
        pcm = decode(path, limit=2.0, at=0.9)                                             # runs into the end
        self.assertEqual((pcm.start, pcm.played, shorts(pcm)[-1]), (4.5, 0.5, 4999))
        self.assertFalse(pcm.cut)
        self.assertFalse(pcm.whole)
        pcm = decode(path, limit=2.0, at=7.0)                                             # past the end: last frame
        self.assertEqual(shorts(pcm), [4999])

    def test_short_file_comes_whole_and_is_cut_by_tail(self):
        frames = b"".join(struct.pack("<2h", i, -i) for i in range(1000))
        path = self.write("p.wav", riff(fmt_chunk(1, 2, 1000, 16), frames))               # 1 second, stereo
        pcm = decode(path, at=0.5)
        self.assertTrue(pcm.whole)
        self.assertEqual(len(pcm.data), 4000)
        part = pcm.tail(0.25)
        self.assertEqual((part.start, part.played, part.seconds), (0.25, 0.75, 1.0))
        self.assertEqual(shorts(part)[:4], [250, -250, 251, -251])
        self.assertFalse(part.cut)
        self.assertIs(pcm.tail(0.0), pcm)
        self.assertEqual(shorts(pcm.tail(1.0)), [999, -999])

    def test_chunks_before_data_and_a_lying_data_size(self):
        junk = b"LIST" + struct.pack("<I", 5) + b"abcde\0"                    # odd size: one pad byte
        raw = bytearray(riff(fmt_chunk(1, 1, 8000, 16), struct.pack("<2h", 7, -7), extra=junk))
        raw[-8:-4] = struct.pack("<I", 0xFFFFFFF0)                           # data chunk claims far more than there is
        pcm = decode(self.write("j.wav", bytes(raw)))
        self.assertEqual(shorts(pcm), [7, -7])

    def test_what_is_not_decoded_here(self):
        self.assertIsNone(decode(self.write("k.mp3", b"ID3\x03" + bytes(64))))
        self.assertIsNone(decode(self.write("l.wav", riff(fmt_chunk(2, 1, 8000, 4), bytes(8)))))     # ADPCM
        self.assertIsNone(decode(self.write("m.wav", riff(struct.pack("<HHIIHH", 1, 2, 8000, 0, 3, 16), bytes(8)))))
        empty = decode(self.write("n.wav", riff(fmt_chunk(1, 2, 44100, 16), b"")))
        self.assertEqual((empty.data, empty.seconds), (b"", 0.0))
        with self.assertRaises(OSError):
            decode(self.root / "missing.wav")


class FakeOutput:
    def __init__(self, seconds=2.0, at=0.0):
        self.seconds, self.at, self.finished, self.closed = seconds, at, False, False

    def position(self):
        return self.at

    def done(self):
        return self.finished

    def close(self):
        self.closed = True


class PlayerTest(unittest.TestCase):
    def player(self, read, **kw):
        self.outputs = []

        def open_wave(pcm):
            self.outputs.append(FakeOutput(pcm.seconds, pcm.start))
            self.outputs[-1].pcm = pcm
            return self.outputs[-1]

        def open_file(path, at=0.0):
            self.outputs.append(FakeOutput(4.0, at * 4.0))
            return self.outputs[-1]

        return Player(kw.get("open_wave", open_wave), kw.get("open_file", open_file), read)

    def wait(self, player, state):
        end = time.time() + 5
        while player.poll() != state and time.time() < end:
            time.sleep(0.005)
        self.assertEqual(player.state, state)

    def test_plays_reports_position_and_ends(self):
        p = self.player(lambda path: Pcm(1000, 1, bytes(4000), 2.0, 2.0))
        self.assertEqual((p.poll(), p.fraction), ("idle", 0.0))
        p.start("a.wav")
        self.assertEqual((p.state, p.path), ("loading", "a.wav"))
        self.wait(p, "playing")
        out = self.outputs[0]
        out.at = 0.5
        self.assertAlmostEqual(p.fraction, 0.25)
        out.at = 9.0
        self.assertEqual(p.fraction, 1.0)
        out.finished = True
        self.assertEqual(p.poll(), "idle")
        self.assertTrue(out.closed)
        self.assertIsNone(p.path)

    def test_starting_another_stops_the_first(self):
        p = self.player(lambda path: Pcm(1000, 1, bytes(4000), 2.0, 2.0))
        p.start("a.wav")
        self.wait(p, "playing")
        p.start("b.wav")
        self.assertTrue(self.outputs[0].closed)
        self.wait(p, "playing")
        self.assertEqual((len(self.outputs), p.path), (2, "b.wav"))
        p.stop()
        self.assertTrue(self.outputs[1].closed)
        self.assertEqual(p.state, "idle")

    def test_stop_while_loading_plays_nothing(self):
        gate = []

        def slow(path):
            while not gate:
                time.sleep(0.005)
            return Pcm(1000, 1, bytes(4000), 2.0, 2.0)

        p = self.player(slow)
        p.start("a.wav")
        p.stop()
        gate.append(1)
        time.sleep(0.05)
        self.assertEqual(p.poll(), "idle")
        self.assertEqual(self.outputs, [])

    def test_other_formats_go_to_the_system_player(self):
        p = self.player(lambda path: None)
        p.start("a.mp3")
        self.wait(p, "playing")
        self.assertEqual(self.outputs[0].seconds, 4.0)

    def test_failures_become_messages(self):
        def broken(path):
            raise OSError("拒绝访问")

        p = self.player(broken)
        p.start("a.wav")
        self.wait(p, "error")
        self.assertIn("拒绝访问", p.error)
        p.stop()
        self.assertEqual(p.state, "idle")

        def no_device(pcm):
            raise AuditionError("Cannot open the sound output device: x")

        p = self.player(lambda path: Pcm(1000, 1, bytes(4000), 2.0, 2.0), open_wave=no_device)
        p.start("a.wav")
        self.wait(p, "error")
        self.assertIn("Cannot open the sound output device", p.error)

        p = self.player(lambda path: Pcm(1000, 1, b"", 0.0, 0.0))
        p.start("a.wav")
        self.wait(p, "error")
        self.assertIn("empty", p.error)

    def test_from_a_point_and_jumping_reads_the_file_once(self):
        path = Path(tempfile.mkdtemp()) / "a.wav"
        path.write_bytes(riff(fmt_chunk(1, 1, 1000, 16), b"".join(struct.pack("<h", i) for i in range(2000))))
        reads = []

        def read(p, **kw):
            reads.append(kw)
            return decode(p, **kw)

        p = self.player(read)
        p.start(path, 0.5)
        self.assertEqual(p.fraction, 0.5)                       # the play head is there while the file is read
        self.wait(p, "playing")
        first = self.outputs[0]
        self.assertEqual((first.pcm.start, shorts(first.pcm)[0], len(first.pcm.data)), (1.0, 1000, 2000))
        self.assertAlmostEqual(p.fraction, 0.5)
        p.start(path, 0.25)                                     # a jump: no thread, no reading, playing at once
        self.assertEqual(p.state, "playing")
        self.assertTrue(first.closed)
        self.assertEqual((self.outputs[1].pcm.start, shorts(self.outputs[1].pcm)[0]), (0.5, 500))
        p.start(path)
        self.assertEqual((p.state, self.outputs[2].pcm.start, len(self.outputs[2].pcm.data)), ("playing", 0.0, 4000))
        self.assertEqual(len(reads), 1)
        path.write_bytes(riff(fmt_chunk(1, 1, 1000, 16), bytes(2 * 500)))      # the file changed: read again
        p.start(path, 0.5)
        self.wait(p, "playing")
        self.assertEqual((len(reads), self.outputs[3].pcm.seconds, self.outputs[3].pcm.start), (2, 0.5, 0.25))
        p.stop()

    def test_long_file_from_a_point_reads_from_there(self):
        asked = []

        def read(p, at=0.0):
            asked.append(at)
            return Pcm(1000, 1, bytes(4000), 10.0, 2.0, at * 10.0)

        p = self.player(read)
        p.start("long.wav", 0.6)
        self.wait(p, "playing")
        self.assertEqual((asked, self.outputs[0].pcm.start), ([0.6], 6.0))
        self.assertTrue(p.cut)
        self.assertAlmostEqual(p.fraction, 0.6)
        p.start("long.wav", 0.8)                                 # not kept: it is not the whole file
        self.wait(p, "playing")
        self.assertEqual(asked, [0.6, 0.8])
        self.assertFalse(p.cut)

    def test_system_player_from_a_point(self):
        p = self.player(lambda path, **kw: None)
        p.start("a.mp3", 0.5)
        self.wait(p, "playing")
        self.assertAlmostEqual(p.fraction, 0.5)

    def test_cut_flag_follows_the_sample(self):
        p = self.player(lambda path: Pcm(1000, 1, bytes(4000), 9.0, 2.0))
        p.start("a.wav")
        self.wait(p, "playing")
        self.assertTrue(p.cut)
        self.outputs[0].at = 1.0
        self.assertAlmostEqual(p.fraction, 1 / 9)

    @unittest.skipIf(sys.platform == "win32", "only where there is no Windows sound output")
    def test_elsewhere_it_says_so(self):
        with self.assertRaises(AuditionError):
            audition._open_wave(Pcm(1000, 1, bytes(4), 0.002, 0.002))


if __name__ == "__main__":
    unittest.main()
