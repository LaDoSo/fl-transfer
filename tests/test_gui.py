import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fltransfer import gui, live
from fltransfer.model import Clip


class GuiSearchTests(unittest.TestCase):
    def test_matches_channel_and_filenames_without_matching_directories(self):
        clip = Clip("c0", "audio", "底鼓", "采样", None, True,
                    sample=str(Path("Drums") / "Relocated.wav"), sample_name="KICK_01.wav")
        for query in ("底", "  kick_01  ", "RELOCATED", "  "):
            with self.subTest(query=query):
                self.assertTrue(gui.sample_matches(clip, query))
        self.assertFalse(gui.sample_matches(clip, "Drums"))
        self.assertTrue(gui.sample_matches(replace(clip, sample=""), "01.wav"))

    def test_filter_selection_drag_refresh_and_clear(self):
        try:
            root = gui.tk.Tk()
        except gui.tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(gui, "HAVE_DND", False):
            app = gui.App(root)
        first = Clip("c0", "audio", "底鼓", "采样", None, True, sample_name="Kick 1.wav")
        snare = replace(first, key="c1", name="军鼓", sample_name="Snare.wav")
        last = replace(first, key="c2", name="KICK 2", sample_name="Drum.wav")
        pattern = Clip("p1", "pattern", "Beat", "", None, True)
        clips = [pattern, first, snare, last]
        app.source = SimpleNamespace(path="source.flp", clips=lambda: clips, summary=lambda: "",
                                     project=SimpleNamespace(ppq=96))
        app.fill()
        app.select(["p1", "c0", "c1"])
        app.sample_search.insert(0, "kick")
        self.assertEqual(app.sample_search_count.get(), "2 / 3")
        self.assertEqual(list(app.boxes), ["p1", "c0", "c2"])
        self.assertEqual(app.chosen(), ["p1", "c0"])
        self.assertEqual(app.dragged_keys("c1"), [])

        app.select(["c0"])
        x, y = app.boxes["c2"][:2]
        app.click(SimpleNamespace(x=x + 25, y=y + 5 - app.canvas.canvasy(0),
                                  x_root=0, y_root=0, state=1))
        self.assertEqual(app.chosen(), ["c0", "c2"])
        self.assertEqual(app.dragged_keys("c0"), ["c0", "c2"])
        app.select_all()
        self.assertEqual(app.chosen(), ["p1", "c0", "c2"])

        clips[-1] = replace(last, name="Ride")
        app.fill()
        self.assertEqual(app.sample_search_count.get(), "1 / 3")
        self.assertEqual(app.chosen(), ["p1", "c0"])
        app.sample_search_var.set("不存在")
        self.assertEqual(list(app.boxes), ["p1"])
        self.assertEqual(app.sample_search_count.get(), "0 / 3")
        labels = [app.canvas.itemcget(i, "text") for i in app.canvas.find_all() if app.canvas.type(i) == "text"]
        self.assertIn("No matching samples", labels)
        app.clear_sample_search()
        self.assertEqual(list(app.boxes), ["p1", "c0", "c1", "c2"])
        self.assertEqual(app.sample_search_count.get(), "3 / 3")
        self.assertEqual(app.selected, ["p1"])


class GuiInstallTests(unittest.TestCase):
    def test_manual_choice_is_validated_and_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            exe = base / "Custom Install" / "FL.exe"
            exe.parent.mkdir()
            exe.touch()
            wrong = base / "wrong.exe"
            wrong.touch()
            app = gui.App.__new__(gui.App)
            app.root = None
            with patch.object(live, "_settings_path", return_value=base / "settings.json"), \
                    patch.object(live, "_memo_path", return_value=base / "memo.json"), \
                    patch.object(live, "_process_image", return_value=None), \
                    patch.object(live, "fl_registry_paths", return_value=[]), \
                    patch.dict(gui.os.environ, {"ProgramFiles": "", "ProgramW6432": "", "ProgramFiles(x86)": ""}), \
                    patch.object(gui.filedialog, "askopenfilename", side_effect=[str(wrong), str(exe)]) as choose:
                with self.assertRaises(live.LiveError):
                    app.fl_executable(42)
                self.assertIsNone(live.setting("fl_exe"))
                self.assertEqual(app.fl_executable(42), str(exe))
                self.assertEqual(app.fl_executable(42), str(exe))
                self.assertEqual(choose.call_count, 2)

    def test_cancelling_exe_picker_precedes_saving_or_sending_pattern(self):
        app = Mock()
        app.chosen.return_value = ["p1"]
        app.find_target.return_value.pid = 42
        app.fl_executable.side_effect = lambda pid: gui.App.fl_executable(app, pid)
        with patch.object(live, "fl_running", return_value=1), patch.object(live, "find_fl", return_value=None), \
                patch.object(gui.filedialog, "askopenfilename", return_value=""), patch.object(live, "send") as send:
            gui.App.send(app)
        app.find_target.assert_called_once_with()
        app.may_save.assert_not_called()
        send.assert_not_called()
        self.assertIn("cancelled", app.say.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
