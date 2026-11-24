import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_transfer import source, target  # noqa: E402
from fltransfer.flp import Project  # noqa: E402
from fltransfer.model import PRESET, SAMPLE, Source  # noqa: E402
from fltransfer.transfer import TransferError  # noqa: E402


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.src_path = Path(self.dir.name) / "A.flp"
        self.dst_path = Path(self.dir.name) / "B.flp"
        self.src_path.write_bytes(source().dump())
        self.dst_path.write_bytes(target().dump())
        self.s = Source(self.src_path)

    def tearDown(self):
        self.dir.cleanup()

    def test_rows(self):
        rows = {r.key: r for r in self.s.rows()}
        self.assertEqual(list(rows), ["c0", "c1", "c2", "c3", "p1", "p1c0", "p1c2", "p2", "p3"])
        self.assertTrue(rows["c0"].draggable and rows["c1"].draggable)
        self.assertFalse(rows["c3"].draggable)                 # layer
        self.assertFalse(rows["p1"].draggable)                 # two channels: drag the parts
        self.assertEqual((rows["p1c0"].parent, rows["p1c0"].detail), ("p1", "2 notes"))
        self.assertTrue(rows["p2"].draggable)                  # single channel
        self.assertFalse(rows["p3"].draggable)                 # no notes
        self.assertIn("kick.wav", rows["c0"].detail)

    def test_drag_files(self):
        f = self.s.drag_file("p1c2")
        self.assertEqual(f.name, "Drums - Hat.fsc")
        self.assertEqual(Project.load(f).format, 16)
        self.assertEqual(self.s.drag_file("p2").name, "Pattern 2 - Lead.fsc")
        self.assertEqual(self.s.drag_file("c0", PRESET).name, "Kick.fst")
        with self.assertRaises(TransferError):
            self.s.drag_file("c0", SAMPLE)                     # sample file is not on this machine
        with self.assertRaises(TransferError):
            self.s.drag_file("p1")

    def test_merge_into_writes_new_file_only(self):
        before = self.dst_path.read_bytes()
        out, report = self.s.merge_into(str(self.dst_path), ["p1", "c1"])
        self.assertEqual(out.name, "B (transfer).flp")
        self.assertEqual(self.dst_path.read_bytes(), before)
        p = Project.load(out)
        self.assertEqual([c["name"] for c in p.channels()], ["Sampler", "Bass", "Lead", "Kick", "Hat"])
        self.assertEqual(len(report.patterns), 1)

    def test_reload_on_change(self):
        self.assertFalse(self.s.changed_on_disk())
        import os
        os.utime(self.src_path, (1, 1))
        self.assertTrue(self.s.changed_on_disk())
        self.s.load()
        self.assertFalse(self.s.changed_on_disk())


class ClipTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "samples").mkdir()
        (self.root / "samples" / "kick.wav").write_bytes(b"RIFF")
        self.path = self.root / "A.flp"
        self.path.write_bytes(source().dump())
        self.s = Source(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_clips(self):
        clips = {c.key: c for c in self.s.clips()}
        self.assertEqual([c.key for c in self.s.clips()], ["p1", "p2", "p3", "c0", "c2", "c1", "c3"])
        self.assertEqual([clips[k].kind for k in ("p1", "c0", "c1", "c3")], ["pattern", "audio", "plugin", "other"])
        p1 = clips["p1"]
        self.assertEqual([n[0] for n in p1.notes], [0, 48, 96])
        self.assertEqual([n[3] for n in p1.notes], [0, 2, 0])
        self.assertEqual(p1.channels, ((0, "Kick"), (2, "Hat")))
        self.assertEqual(p1.length % (self.s.project.ppq * 4), 0)
        self.assertGreaterEqual(p1.length, 96)
        self.assertFalse(clips["p3"].enabled)
        self.assertFalse(clips["c3"].enabled)

    def test_sample_found_next_to_project(self):
        clips = {c.key: c for c in self.s.clips()}
        self.assertEqual(Path(clips["c0"].sample), self.root / "samples" / "kick.wav")   # C:\smp\kick.wav is gone
        self.assertEqual(clips["c0"].sample_name, "kick.wav")
        self.assertEqual(clips["c2"].sample, "")
        self.assertEqual(clips["c2"].sample_name, "hat.wav")

    def test_preset_points_at_the_found_sample(self):
        from unittest import mock
        with mock.patch("fltransfer.export.drag_dir", return_value=self.root):
            preset = Project.load(self.s.files_for("c0")[0])
            sample = self.s.files_for("c0", SAMPLE)
            with self.assertRaises(TransferError):                # a pattern never leaves as a bare file
                self.s.files_for("p1")
            fallback = self.s.files_for("c2", SAMPLE)             # no file on disk: falls back to the preset
        self.assertEqual(preset.channels()[0]["sample"], str(self.root / "samples" / "kick.wav"))
        self.assertEqual(preset.sanity(), [])
        self.assertEqual(sample, [self.root / "samples" / "kick.wav"])
        self.assertEqual([f.suffix for f in fallback], [".fst"])


if __name__ == "__main__":
    unittest.main()
