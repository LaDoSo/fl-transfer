"""Dev experiment: preset files built from parts.

  python tools/variant.py <src.flp> <out.fst> <part>...
    b<iid>            the block of source channel <iid>
    p<num>:<name>     select pattern <num> and name it
    n<num>:<pat>:<src iid>:<field>   notes of source pattern <pat>/<src iid> into pattern <num>, channel field <field>
"""
import os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer import export, flp
from fltransfer.flp import Event, Project
from fltransfer.transfer import Layout

src, out = sys.argv[1], sys.argv[2]
p = Project.load(src)
lay = Layout.of(p)
ev = export._preamble(p)
for part in sys.argv[3:]:
    if part[0] == "b":
        _, a, b = next(c for c in lay.channels if c[0] == int(part[1:]))
        ev += [Event(e.id, e.data) for e in p.events[a:b]]
    elif part[0] == "p":
        num, name = part[1:].split(":")
        ev += [Event(flp.PAT_NEW, struct.pack("<H", int(num))), Event(flp.PAT_NAME, (name + "\0").encode("utf-16-le")),
               Event(flp.PAT_COLOR, bytes((0x5d, 0x85, 0x51, 0)))]
    elif part[0] == "n":
        num, pat, chan, field = (int(v) for v in part[1:].split(":"))
        a, b = lay.notes[pat]
        raw = bytearray()
        for e in p.events[a:b]:
            if e.id == flp.PAT_NOTES:
                for off in range(0, len(e.data), flp.NOTE_SIZE):
                    if struct.unpack_from("<H", e.data, off + 6)[0] == chan:
                        note = bytearray(e.data[off:off + flp.NOTE_SIZE])
                        struct.pack_into("<H", note, 6, field)
                        raw += note
        ev += [Event(flp.PAT_NEW, struct.pack("<H", num)), Event(flp.PAT_NOTES, bytes(raw))]
open(out, "wb").write(Project(export.FORMAT_PRESET, 1, p.ppq, ev).dump())
print("wrote", out, [e.id for e in ev if e.id in (64, 65, 193, 224)])
