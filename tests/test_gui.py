import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fltransfer import gui
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
        self.assertIn("没有找到匹配的采样", labels)
        app.clear_sample_search()
        self.assertEqual(list(app.boxes), ["p1", "c0", "c1", "c2"])
        self.assertEqual(app.sample_search_count.get(), "3 / 3")
        self.assertEqual(app.selected, ["p1"])


if __name__ == "__main__":
    unittest.main()
