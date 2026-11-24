"""Container tests that need no FL Studio files."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fltransfer.flp import Event, FLPError, NOTE_STRUCT, Project  # noqa: E402


def build():
    note = NOTE_STRUCT.pack(96, 0x4000, 3, 48, 60, 0, 120, 0, 64, 0, 64, 100, 128, 128)
    ev = [
        Event(199, b"25.2.5.5319\0"),
        Event(65, struct.pack("<H", 1)),
        Event(224, note * 2),
        Event(64, struct.pack("<H", 3)),
        Event(21, b"\0"),
        Event(203, "Kick".encode("utf-16-le") + b"\0\0"),
        Event(196, "C:\\s\\kick.wav".encode("utf-16-le") + b"\0\0"),
        Event(213, bytes(300)),  # needs a two byte varint
        Event(65, struct.pack("<H", 1)),
        Event(193, "Drums".encode("utf-16-le") + b"\0\0"),
    ]
    return Project(0, 1, 96, ev)


class ContainerTest(unittest.TestCase):
    def test_roundtrip(self):
        raw = build().dump()
        again = Project.parse(raw)
        self.assertEqual(again.dump(), raw)
        self.assertEqual(len(again.events), 10)
        self.assertEqual(len(again.events[7].data), 300)

    def test_views(self):
        p = Project.parse(build().dump())
        self.assertEqual(p.version, "25.2.5.5319")
        (ch,) = p.channels()
        self.assertEqual((ch["iid"], ch["type"], ch["name"]), (3, 0, "Kick"))
        self.assertEqual(ch["sample"], "C:\\s\\kick.wav")
        pat = p.patterns()[1]
        self.assertEqual((pat["name"], pat["notes"], pat["channels"]), ("Drums", 2, {3}))
        self.assertEqual(pat["length"], 144)

    def test_fl25_three_byte_event(self):
        """FL 25.2.3+ writes event 172 with three bytes, then a text event 192."""
        p = build()
        text = "FL Studio 25.2.5.5319.5319".encode("utf-16-le") + b"\0\0"
        p.events[1:1] = [Event(172, b"\x01\x01\x00"), Event(192, text)]
        raw = p.dump()
        self.assertIn(b"\xac\x01\x01\x00\xc0\x36F\x00L\x00", raw)
        again = Project.parse(raw)
        self.assertEqual([e.id for e in again.events[:4]], [199, 172, 192, 65])
        self.assertEqual(again.events[1].data, b"\x01\x01\x00")
        self.assertEqual(again.patterns()[1]["notes"], 2)
        self.assertEqual(again.dump(), raw)

    def test_older_versions_keep_four_bytes(self):
        p = build()
        p.events[0] = Event(199, b"24.2.99.4720\0")
        p.events.insert(1, Event(172, b"\x01\x01\x00\xc0"))
        again = Project.parse(p.dump())
        self.assertEqual(again.events[1].data, b"\x01\x01\x00\xc0")
        self.assertEqual(again.events[2].id, 65)

    def test_rejects_garbage(self):
        with self.assertRaises(FLPError):
            Project.parse(b"RIFF" + bytes(40))
        raw = build().dump()
        with self.assertRaises(FLPError):
            Project.parse(raw[:-5])


if __name__ == "__main__":
    unittest.main()
