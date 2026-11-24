import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_transfer import source  # noqa: E402
from fltransfer import export  # noqa: E402
from fltransfer.flp import NOTE_STRUCT, Project  # noqa: E402
from fltransfer.transfer import TransferError  # noqa: E402


class ExportTest(unittest.TestCase):
    def test_score_holds_one_channels_notes(self):
        raw = export.score(source(), 1, 0)
        p = Project.parse(raw)
        self.assertEqual((p.format, p.n_channels, p.ppq), (16, 1, 96))
        self.assertEqual([e.id for e in p.events], [199, 172, 192, 65, 224])
        self.assertEqual(p.events[3].value, 0)
        data = p.events[4].data
        notes = [NOTE_STRUCT.unpack_from(data, o) for o in range(0, len(data), 24)]
        self.assertEqual([(n[0], n[2]) for n in notes], [(0, 0), (96, 0)])   # Kick notes only, channel field 0
        hat = Project.parse(export.score(source(), 1, 2)).events[4].data
        self.assertEqual(NOTE_STRUCT.unpack_from(hat)[:3:2], (48, 0))

    def test_score_refuses_empty(self):
        with self.assertRaises(TransferError):
            export.score(source(), 1, 1)
        with self.assertRaises(TransferError):
            export.score(source(), 3, 0)

    def test_preset_is_the_channel_block(self):
        src = source()
        p = Project.parse(export.channel_preset(src, 0))
        self.assertEqual(p.format, 32)
        self.assertEqual([e.id for e in p.events[:4]], [199, 172, 192, 64])
        (ch,) = p.channels()
        self.assertEqual((ch["name"], ch["sample"]), ("Kick", "C:\\smp\\kick.wav"))
        self.assertEqual(p.events[-1].id, 51)
        with self.assertRaises(TransferError):
            export.channel_preset(src, 3)          # layer

    def test_names_and_paths(self):
        self.assertEqual(export.safe_name('a/b:c*?"', "x"), "a_b_c___")
        self.assertEqual(export.safe_name("  ", "x"), "x")
        got = export.expand_fl_path("%FLStudioFactoryData%\\Data\\hat.wav", "D:\\FL", "C:\\U")
        self.assertEqual(got, "D:\\FL\\Data\\hat.wav")
        self.assertEqual(export.expand_fl_path("C:\\a.wav", "D:\\FL", "C:\\U"), "C:\\a.wav")

    def test_write_files(self):
        src = source()
        f = export.write_score(src, "/tmp/My Song.flp", 1, 0)
        self.assertEqual(f.name, "Drums - Kick.fsc")
        self.assertEqual(Project.load(f).format, 16)
        g = export.write_preset(src, "/tmp/My Song.flp", 0)
        self.assertEqual(g.name, "Kick.fst")
        self.assertIsNone(export.sample_file(src, "/tmp/My Song.flp", 0))


if __name__ == "__main__":
    unittest.main()
