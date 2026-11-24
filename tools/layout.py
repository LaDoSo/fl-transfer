"""Dev tool: print how a project's event stream is laid out (structure only)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer.flp import Project

def rle(ids):
    out, prev = [], None
    for i in ids:
        if out and out[-1][0] == i: out[-1][1] += 1
        else: out.append([i, 1])
    return " ".join(str(i) if n == 1 else f"{i}*{n}" for i, n in out)

def layout(path, verbose):
    p = Project.load(path); ev = p.events
    print(f"== {os.path.basename(path)}  v={p.version} hdr_channels={p.n_channels} events={len(ev)} problems={p.sanity()}")
    first64 = next((i for i, e in enumerate(ev) if e.id == 64), len(ev))
    arr = next((i for i, e in enumerate(ev) if e.id == 99), len(ev))
    print("  head  :", rle([e.id for e in ev[:first64]])[:700])
    segs, cur = [], None
    for i in range(first64, arr):
        e = ev[i]
        if e.id == 64: cur = ["ch", e.value, i, []]; segs.append(cur)
        elif e.id == 65: cur = ["pat", e.value, i, []]; segs.append(cur)
        cur[3].append(e.id)
    order = " ".join(f"{k[0]}{v}" for k, v, _, _ in segs)
    print("  order :", order[:900])
    shapes = {}
    for k, v, i, ids in segs:
        shapes.setdefault((k, rle(ids)), []).append(v)
    for (k, shape), vs in shapes.items():
        print(f"  shape {k} x{len(vs)} {vs[:12]}: {shape[:600]}")
    if verbose:
        for k, v, i, ids in segs:
            if k != "ch": continue
            d = {e.id: e for e in ev[i:i + len(ids)]}
            vals = {n: d[n].data.hex() for n in (21, 41, 104, 139, 144, 145, 155, 170, 51, 32, 20, 143, 131, 138, 97, 89, 48, 50) if n in d}
            print(f"    ch{v}: {vals}")
        for k, v, i, ids in segs:
            if k != "pat": continue
            vals = {e.id: (e.data.hex() if e.id < 192 else len(e.data)) for e in ev[i:i + len(ids)]}
            print(f"    pat{v}: {vals}")
    tail = [e.id for e in ev[arr:]]
    print("  tail  :", rle(tail)[:300], "...")
    for e in ev[:first64]:
        if e.id in (67, 146, 216, 226, 231): print(f"    head {e.id}: {e.data.hex()[:80]}")

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    verbose = "-v" in sys.argv
    for f in sys.argv[1:]:
        if f != "-v": layout(f, verbose)
