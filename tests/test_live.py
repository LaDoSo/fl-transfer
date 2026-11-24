import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_transfer import source  # noqa: E402
from fltransfer import live  # noqa: E402
from fltransfer.flp import FLPError, Project  # noqa: E402
from fltransfer.model import Source  # noqa: E402


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_refuses_other_file_types(self):
        for name in ("a.flp", "a.wav", "a"):
            f = self.root / name
            f.write_bytes(b"x")
            with self.assertRaises(live.LiveError):
                live.send(f, exe="FL64.exe", require_running=False)

    def test_refuses_missing_file_and_missing_fl(self):
        with self.assertRaises(live.LiveError):
            live.send(self.root / "none.fst", exe="FL64.exe", require_running=False)
        f = self.root / "a.fst"
        f.write_bytes(b"x")
        with mock.patch.object(live, "find_fl", return_value=None), self.assertRaises(live.LiveError):
            live.send(f, require_running=False)
        with mock.patch.object(live, "fl_running", return_value=0), self.assertRaises(live.LiveError):
            live.send(f, exe="FL64.exe")

    def test_send_starts_fl_with_the_file(self):
        f = self.root / "a.fsc"
        f.write_bytes(b"x")
        with mock.patch.object(live.subprocess, "Popen") as popen, \
                mock.patch.object(live, "fl_running", return_value=1):
            live.send(f, exe="X:/FL64.exe")
        self.assertEqual(popen.call_args[0][0], ["X:/FL64.exe", str(f)])

    def test_pattern_travels_with_its_channels(self):
        import struct
        from fltransfer import flp
        path = self.root / "A.flp"
        path.write_bytes(source().dump())
        with mock.patch("fltransfer.export.drag_dir", return_value=self.root):
            s = Source(path)
            self.assertEqual([(f.suffix, c, p) for f, c, p in s.send_plan(["c0"])], [(".fst", 1, 0)])
            with self.assertRaises(FLPError):          # no target: a pattern cannot be numbered
                s.send_plan(["p1"])
            target = live.Target("B.flp", "B.flp", 1, 0.0, s.project.ppq * 2, channels=7, last_pattern=4)
            used = sorted(s.patterns[1]["channels"])                   # pattern 1 uses two channels
            plan = s.send_plan(["p1", f"c{used[0]}"], target)
        self.assertEqual([(c, p) for _, c, p in plan], [(len(used), 1)])   # the channel card is not sent twice
        made = Project.load(plan[0][0])
        self.assertEqual(made.format, 32)
        self.assertEqual(made.ppq, target.ppq)
        self.assertEqual(plan[0][0].stem, s.channels[used[0]]["name"])   # FL names the first channel after the file
        ids = [e.id for e in made.events]
        self.assertEqual(ids.count(flp.CH_NEW), len(used))
        self.assertLess(max(i for i, x in enumerate(ids) if x == flp.CH_NEW), ids.index(flp.PAT_NEW))
        self.assertEqual({e.value for e in made.events if e.id == flp.PAT_NEW}, {5})
        notes = next(e.data for e in made.events if e.id == flp.PAT_NOTES)
        fields = {struct.unpack_from("<H", notes, off + 6)[0] for off in range(0, len(notes), flp.NOTE_SIZE)}
        self.assertEqual(fields, {7, 8})                               # positions the new channels will get
        before = next(e.data for e in s.project.events if e.id == flp.PAT_NOTES)
        self.assertEqual(struct.unpack_from("<I", notes, 8)[0], 2 * struct.unpack_from("<I", before, 8)[0])

    def test_several_patterns_share_one_file(self):
        from fltransfer import flp
        path = self.root / "A.flp"
        path.write_bytes(source().dump())
        with mock.patch("fltransfer.export.drag_dir", return_value=self.root):
            s = Source(path)
            target = live.Target("B.flp", "B.flp", 1, 0.0, s.project.ppq, channels=3, last_pattern=0)
            plan = s.send_plan(["p1", "p2"], target)
        self.assertEqual(len(plan), 1)
        made = Project.load(plan[0][0])
        union = set(s.patterns[1]["channels"]) | set(s.patterns[2]["channels"])
        self.assertEqual((plan[0][1], plan[0][2]), (len(union), 2))
        self.assertEqual(sum(1 for e in made.events if e.id == flp.CH_NEW), len(union))
        self.assertEqual({e.value for e in made.events if e.id == flp.PAT_NEW}, {1, 2})

    def test_target_is_read_from_the_saved_file_and_counts_what_was_sent(self):
        path = self.root / "Song.flp"
        path.write_bytes(source().dump())
        memo = self.root / "targets.json"
        saved = Project.load(path)
        with mock.patch.object(live, "_memo_path", return_value=memo), \
                mock.patch.object(live, "fl_windows", return_value=[(1, 77, "Song.flp - FL Studio 2025")]), \
                mock.patch("fltransfer.model.recent_projects", return_value=[]):
            with self.assertRaises(live.TargetUnknown) as caught:
                live.find_target()
            self.assertEqual(caught.exception.name, "Song.flp")
            t = live.find_target(known=[str(path)])
            self.assertEqual((t.next_channel, t.pid), (len(saved.channels()), 77))
            self.assertEqual(t.next_pattern, max(saved.patterns()) + 1)
            t.sent(2, 1)
            again = live.find_target(known=[str(path)])
            self.assertEqual((again.next_channel, again.next_pattern), (t.channels + 2, t.last_pattern + 2))
            live.remember_path(path)
            self.assertEqual(live.find_target().path, str(path.resolve()))
            with mock.patch.object(live, "fl_windows", return_value=[(1, 78, "Song.flp - FL Studio 2025")]):
                self.assertEqual(live.find_target().added_channels, 0)      # FL was restarted: start over
            with mock.patch.object(live, "fl_windows", return_value=[(1, 77, "FL Studio 2025")]):
                with self.assertRaises(live.TargetUnknown) as caught:
                    live.find_target()
                self.assertIsNone(caught.exception.name)                    # never saved: nothing to read

    def test_save_first_waits_for_the_new_file(self):
        path = self.root / "Song.flp"
        path.write_bytes(source().dump())
        import os
        os.utime(path, (1000, 1000))

        def fl_saves(hwnd):
            path.write_bytes(source().dump())           # mtime moves to now

        with mock.patch.object(live, "press_save", side_effect=fl_saves) as pressed:
            live.save_in_fl(5, str(path), timeout=5)
        pressed.assert_called_once_with(5)
        os.utime(path, (1000, 1000))
        with mock.patch.object(live, "press_save"), self.assertRaises(live.LiveError):
            live.save_in_fl(5, str(path), timeout=1)    # FL did not save: nothing may be sent

    def test_save_waits_while_fl_shows_a_message_box(self):
        path = self.root / "Song.flp"
        path.write_bytes(source().dump())
        import os
        os.utime(path, (1000, 1000))
        said = []
        state = {"blocked": True}
        with mock.patch.object(live, "fl_blocked", side_effect=lambda hwnd: state["blocked"]), \
                mock.patch.object(live, "press_save") as pressed, self.assertRaises(live.LiveError):
            live.save_in_fl(5, str(path))               # a dialog is already up: do not even try
        pressed.assert_not_called()

        calls = {"n": 0}

        def blocked(hwnd):                              # box appears after the key, user clicks it away later
            calls["n"] += 1
            if calls["n"] == 1:
                return False
            if calls["n"] == 9:
                path.write_bytes(source().dump())       # FL finishes the save once the box is gone
            return calls["n"] < 9

        with mock.patch.object(live, "fl_blocked", side_effect=blocked), mock.patch.object(live, "press_save"):
            live.save_in_fl(5, str(path), timeout=2.5, notice=said.append, patience=20)
        self.assertEqual(len(said), 1)
        self.assertIn("提示框", said[0])

        os.utime(path, (1000, 1000))
        calls["n"] = 0

        def stuck(hwnd):                                # the box is never dismissed
            calls["n"] += 1
            return calls["n"] > 1

        with mock.patch.object(live, "fl_blocked", side_effect=stuck), \
                mock.patch.object(live, "press_save"), self.assertRaises(live.LiveError) as caught:
            live.save_in_fl(5, str(path), timeout=2, patience=3)
        self.assertIn("一直没有处理", str(caught.exception))

    def test_settings_are_kept_outside_the_scratch_folder(self):
        memo = self.root / "targets.json"
        memo.write_text('{"settings": {"save_first": true}}', encoding="utf-8")       # written by an older version
        with mock.patch.object(live, "_memo_path", return_value=memo), \
                mock.patch.object(live, "_settings_path", return_value=self.root / "keep" / "settings.json"):
            self.assertIs(live.setting("save_first"), True)          # still honoured
            self.assertIsNone(live.setting("other"))
            live.set_setting("save_first", False)
            self.assertIs(live.setting("save_first"), False)
            memo.unlink()                                            # scratch folder emptied
            self.assertIs(live.setting("save_first"), False)

    def test_show_pattern_presses_keypad_keys(self):
        keys = []
        with mock.patch.object(live.sys, "platform", "win32"), \
                mock.patch.object(live, "fl_windows", return_value=[(9, 77, "Song.flp - FL Studio 2025")]), \
                mock.patch.object(live, "fl_blocked", return_value=False), \
                mock.patch.object(live, "post_key", side_effect=lambda hwnd, key, ctrl=False: keys.append(key)):
            self.assertTrue(live.show_pattern(7))
            self.assertEqual(keys, [0x67])                       # keypad 7 picks pattern 7
            keys.clear()
            self.assertTrue(live.show_pattern(11))
            self.assertEqual(keys, [0x69, 0x6B, 0x6B])           # keypad 9, then "next pattern" twice
            with mock.patch.object(live, "fl_blocked", return_value=True):
                keys.clear()
                self.assertFalse(live.show_pattern(3))           # a dialog is up in FL: press nothing
                self.assertEqual(keys, [])

    def test_uncounted_arrivals_make_the_count_unsure_until_a_save(self):
        path = self.root / "Song.flp"
        path.write_bytes(source().dump())
        memo = self.root / "targets.json"
        with mock.patch.object(live, "_memo_path", return_value=memo), \
                mock.patch.object(live, "fl_windows", return_value=[(1, 77, "Song.flp - FL Studio 2025")]), \
                mock.patch("fltransfer.model.recent_projects", return_value=[]):
            self.assertFalse(live.find_target(known=[str(path)]).unsure)
            live.mark_unsure()                                   # e.g. a sample card was dropped into FL
            self.assertTrue(live.find_target().unsure)
            with mock.patch.object(live, "save_in_fl"):          # FL saved: the file is the truth again
                self.assertFalse(live.find_target(save=True).unsure)
            self.assertFalse(live.find_target().unsure)
            live.mark_unsure()
            import os
            os.utime(path, (2000, 2000))                         # the user saved in FL
            self.assertFalse(live.find_target().unsure)

    def test_title(self):
        self.assertEqual(live.title_project("我的 歌.flp - FL Studio 2025"), "我的 歌.flp")
        self.assertIsNone(live.title_project("FL Studio 2025"))


if __name__ == "__main__":
    unittest.main()
