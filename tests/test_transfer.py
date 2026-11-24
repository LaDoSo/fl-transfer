"""Transfer tests on synthetic projects laid out like FL 25.2.5 saves them."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fltransfer.flp import Event, NOTE_STRUCT, Project  # noqa: E402
from fltransfer.transfer import (Layout, Transfer, TransferError, default_output,  # noqa: E402
                                 find_channel, find_pattern)


def T(s):
    return s.encode("utf-16-le") + b"\0\0"


def W(n):
    return struct.pack("<H", n)


def D(n):
    return struct.pack("<I", n)


def note(pos, chan, length=24, key=60):
    return NOTE_STRUCT.pack(pos, 0x4000, chan, length, key, 0, 120, 0, 64, 0, 64, 100, 128, 128)


def channel(iid, name, kind=0, sample="", insert=0, group=0):
    ev = [Event(64, W(iid)), Event(21, bytes([kind])), Event(203, T(name)), Event(0, b"\1"),
          Event(104, W(insert)), Event(145, D(group)), Event(143, D(3)), Event(20, b"\0")]
    if sample:
        ev.append(Event(196, T(sample)))
    return ev + [Event(170, b"\xff" * 4), Event(51, b"\0")]


def project(channels, patterns, groups=("Unsorted",), version=b"25.2.5.5319\0", ppq=96):
    """patterns: {num: (name or None, [notes])}"""
    ev = [Event(199, version)]
    if version.startswith(b"25.2.5"):
        ev += [Event(172, b"\1\1\0"), Event(192, T("FL Studio 25.2.5.5319.5319"))]
    ev += [Event(156, D(140000)), Event(67, W(1))]
    ev += [Event(231, T(g)) for g in groups]
    for num, (_, notes) in patterns.items():
        if notes:
            ev += [Event(65, W(num)), Event(224, b"".join(notes))]
    ev += [Event(226, bytes(20))] * 3
    for i, ch in enumerate(channels):
        ev += ch
        if i == 0:
            for num, (name, _) in patterns.items():
                if name is not None:
                    ev += [Event(65, W(num)), Event(193, T(name)), Event(150, D(0x565148)), Event(157, b"\xff" * 4)]
    ev += [Event(99, W(0)), Event(241, T("Arrangement")), Event(233, b""), Event(238, bytes(70))]
    return Project.parse(Project(0, len(channels), ppq, ev).dump())


def source():
    return project(
        [channel(0, "Kick", sample="C:\\smp\\kick.wav", insert=3, group=1),
         channel(1, "Lead", kind=2),
         channel(2, "Hat", sample="%FLStudioFactoryData%\\Data\\hat.wav"),
         channel(3, "Layer", kind=3)],
        {1: ("Drums", [note(0, 0), note(48, 2), note(96, 0)]),
         2: (None, [note(0, 1, key=72)]),
         3: ("Empty", [])},
        groups=("Audio", "Drums"))


def target():
    return project(
        [channel(0, "Sampler"), channel(1, "Bass", kind=2, insert=1)],
        {1: ("Drums", [note(0, 1)])})


class LayoutTest(unittest.TestCase):
    def test_layout(self):
        lay = Layout.of(source())
        self.assertEqual([c[0] for c in lay.channels], [0, 1, 2, 3])
        self.assertEqual(sorted(lay.notes), [1, 2])
        self.assertEqual(sorted(lay.meta), [1, 3])
        self.assertEqual(lay.groups, ["Audio", "Drums"])

    def test_rejects_gap_in_channel_ids(self):
        p = project([channel(0, "A"), channel(2, "B")], {})
        with self.assertRaises(TransferError):
            Layout.of(p)


class TransferTest(unittest.TestCase):
    def test_copy_sampler_channel(self):
        src, dst = source(), target()
        before = dst.dump()
        t = Transfer(src, dst)
        self.assertEqual(t.add_channel(0), 2)
        t.verify()
        out = Project.parse(dst.dump())
        self.assertEqual(out.n_channels, 3)
        ch = out.channels()
        self.assertEqual([c["name"] for c in ch], ["Sampler", "Bass", "Kick"])
        self.assertEqual(ch[2]["sample"], "C:\\smp\\kick.wav")
        _, a, b = Layout.of(out).channels[2]
        block = {e.id: e for e in out.events[a:b]}
        self.assertEqual(block[104].value, 0)       # routed to master, not the source's insert 3
        self.assertEqual(block[145].value, 0)       # source group "Drums" is unknown -> Unsorted
        self.assertEqual(block[143].value, 3)       # everything else untouched
        # the original target is still there, byte for byte, once the new block is removed
        del out.events[a:b]
        out.n_channels -= 1
        self.assertEqual(out.dump(), before)

    def test_keep_routing_and_same_group_name(self):
        src = source()
        dst = project([channel(0, "X")], {}, groups=("Unsorted", "Drums"))
        t = Transfer(src, dst, keep_routing=True)
        t.add_channel(0)
        _, a, b = Layout.of(dst).channels[1]
        block = {e.id: e for e in dst.events[a:b]}
        self.assertEqual((block[104].value, block[145].value), (3, 1))

    def test_copy_pattern_brings_channels_and_remaps_notes(self):
        src, dst = source(), target()
        t = Transfer(src, dst)
        self.assertEqual(t.add_pattern(1), 2)
        t.verify()
        out = Project.parse(dst.dump())
        self.assertEqual([c["name"] for c in out.channels()], ["Sampler", "Bass", "Kick", "Hat"])
        pats = out.patterns()
        self.assertEqual(pats[1]["channels"], {1})                 # existing pattern untouched
        self.assertEqual(pats[2]["channels"], {2, 3})
        self.assertEqual(pats[2]["notes"], 3)
        self.assertEqual(pats[2]["name"], "Drums (2)")             # name clash gets a suffix
        lay = Layout.of(out)
        a, b = lay.notes[2]
        data = out.events[a + 1].data
        got = [NOTE_STRUCT.unpack_from(data, o)[:3:2] for o in range(0, len(data), 24)]
        self.assertEqual(got, [(0, 2), (48, 3), (96, 2)])
        self.assertEqual(sorted(lay.meta), [1, 2])
        self.assertLess(lay.meta[2][0], lay.channels[1][1])        # meta block sits after channel 0
        self.assertEqual(t.report.patterns, [(1, 2, "Drums (2)", 3)])

    def test_unnamed_pattern_and_reuse_existing_channel(self):
        src, dst = source(), target()
        t = Transfer(src, dst)
        t.reuse_channel(1, 1)                                      # Lead -> existing Bass
        num = t.add_pattern(2)
        t.verify()
        out = Project.parse(dst.dump())
        self.assertEqual(out.n_channels, 2)
        self.assertEqual(out.patterns()[num]["channels"], {1})
        self.assertNotIn(num, Layout.of(out).meta)

    def test_ppq_is_rescaled(self):
        src = project([channel(0, "A", sample="a.wav")], {1: ("P", [note(96, 0, length=48)])}, ppq=96)
        dst = project([channel(0, "B")], {}, ppq=384)
        t = Transfer(src, dst)
        t.add_pattern(1)
        lay = Layout.of(dst)
        data = dst.events[lay.notes[1][0] + 1].data
        self.assertEqual(NOTE_STRUCT.unpack_from(data)[:4], (384, 0x4000, 1, 192))

    def test_refusals(self):
        src, dst = source(), target()
        with self.assertRaises(TransferError):
            Transfer(src, dst).add_channel(3)                      # layer channel
        with self.assertRaises(TransferError):
            Transfer(src, dst).add_pattern(9)
        old = project([channel(0, "A")], {}, version=b"20.7.2.1863\0")
        with self.assertRaises(TransferError):
            Transfer(src, old)                                     # target too old / source newer
        Transfer(src, old, force=True)

    def test_save_never_overwrites(self):
        src, dst = source(), target()
        t = Transfer(src, dst)
        t.add_channel(2)
        with tempfile.TemporaryDirectory() as d:
            first = Path(d) / "Song.flp"
            first.write_bytes(b"keep me")
            with self.assertRaises(TransferError):
                t.save_new(first)
            self.assertEqual(first.read_bytes(), b"keep me")
            out = default_output(first)
            self.assertEqual(out.name, "Song (transfer).flp")
            t.save_new(out)
            self.assertEqual(default_output(first).name, "Song (transfer 2).flp")
            self.assertEqual(Project.load(out).n_channels, 3)

    def test_missing_sample_is_reported_not_fatal(self):
        src, dst = source(), target()
        t = Transfer(src, dst)
        t.add_channel(0)
        t.add_channel(2)
        self.assertEqual(t.report.missing_samples, ["C:\\smp\\kick.wav"])   # %variables% are left to FL

    def test_finders(self):
        src = source()
        self.assertEqual(find_channel(src, "kick"), 0)
        self.assertEqual(find_channel(src, "#2"), 2)
        self.assertEqual(find_pattern(src, "Drums"), 1)
        with self.assertRaises(TransferError):
            find_channel(src, "Nope")


if __name__ == "__main__":
    unittest.main()
