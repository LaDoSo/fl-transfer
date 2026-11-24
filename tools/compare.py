"""Dev tool: compare what the tool wrote with what FL Studio re-saved from it."""
import os, sys, struct
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer.flp import Project, NOTE_SIZE, NOTE_STRUCT, PAT_NOTES
from fltransfer.transfer import Layout
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def view(path):
    p = Project.load(path)
    lay = Layout.of(p)
    chans = [(c["iid"], c["type"], c["name"], c["sample"], c["internal"]) for c in p.channels()]
    blocks = {}
    for iid, a, b in lay.channels:
        blocks[iid] = [(e.id, e.data) for e in p.events[a:b]]
    pats = {}
    for num, d in p.patterns().items():
        notes = []
        if num in lay.notes:
            a, b = lay.notes[num]
            for e in p.events[a:b]:
                if e.id == PAT_NOTES:
                    notes += [NOTE_STRUCT.unpack_from(e.data, o) for o in range(0, len(e.data), NOTE_SIZE)]
        pats[num] = (d["name"], sorted(notes))
    return p, chans, blocks, pats


def main(before, after):
    pb, cb, bb, patb = view(before)
    pa, ca, ba, pata = view(after)
    print(f"written by tool : FL {pb.version}  {len(cb)} channels  {len(patb)} patterns  {len(pb.events)} events")
    print(f"re-saved by FL  : FL {pa.version}  {len(ca)} channels  {len(pata)} patterns  {len(pa.events)} events")
    ok = True
    if cb != ca:
        ok = False
        print("CHANNEL LIST DIFFERS"); print("  tool:", cb); print("  FL  :", ca)
    else:
        print("channel list    : same ->", [(c[0], c[2]) for c in ca])
    for iid in sorted(set(bb) & set(ba)):
        if bb[iid] != ba[iid]:
            x, y = dict(bb[iid]), dict(ba[iid])
            ids_x, ids_y = [i for i, _ in bb[iid]], [i for i, _ in ba[iid]]
            changed = sorted(i for i in set(x) & set(y) if x[i] != y[i])
            print(f"channel #{iid} block: ids {'same' if ids_x == ids_y else 'DIFFER'}; values changed for ids {changed}")
            for i in changed[:6]:
                print(f"    {i}: {x[i].hex()[:48]} -> {y[i].hex()[:48]}")
    for num in sorted(set(patb) | set(pata)):
        b, a = patb.get(num), pata.get(num)
        if b == a:
            print(f"pattern #{num}     : same -> {a[0]!r} {len(a[1])} notes, channels {sorted({n[2] for n in a[1]})}")
        else:
            ok = False
            print(f"PATTERN #{num} DIFFERS"); print("  tool:", b and (b[0], len(b[1]), b[1][:3])); print("  FL  :", a and (a[0], len(a[1]), a[1][:3]))
    print("byte-identical  :", open(before, "rb").read() == open(after, "rb").read())
    print("VERDICT:", "FL kept every channel, pattern and note" if ok else "MISMATCH")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
