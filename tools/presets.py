"""Dev tool: version histogram of .fst/.fsc files and the event layout of the newest ones."""
import os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer.flp import Project, _version_tuple
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
best = {}; hist = collections.Counter()
for root in sys.argv[1:]:
    walker = [(os.path.dirname(root), [], [os.path.basename(root)])] if os.path.isfile(root) else os.walk(root)
    for d, _, fs in walker:
        for f in fs:
            ext = f[-4:].lower()
            if ext not in (".fst", ".fsc"): continue
            path = os.path.join(d, f)
            try:
                raw = open(path, "rb").read()
                if raw[:4] != b"FLhd": hist[ext + " not-FLhd"] += 1; continue
                p = Project.parse(raw)
            except Exception as exc:
                hist[ext + " err " + type(exc).__name__] += 1; continue
            v = _version_tuple(p.version.encode())
            types = [e.value for e in p.events if e.id == 21]
            key = (ext, types[0] if types else None)
            hist["%s v%s" % (ext, v[0] if v else "?")] += 1
            if key not in best or v > best[key][0]: best[key] = (v, path, p)
for k, n in sorted(hist.items()): print(n, k)
for (ext, t), (v, path, p) in sorted(best.items(), key=lambda kv: str(kv[0])):
    ids = [e.id for e in p.events]
    print(f"\n{ext} type={t} newest v={'.'.join(map(str, v))} fmt={p.format} nch={p.n_channels} ppq={p.ppq} :: {os.path.basename(path)}")
    print("  ids:", " ".join(map(str, ids))[:420])
    for e in p.events:
        if e.id in (64, 65, 67, 21, 104, 145, 22, 156, 159, 172): print(f"    {e.id}={e.data.hex()}", end="")
        if e.id == 224: print(f"    224:{len(e.data)}B first={e.data[:24].hex()}", end="")
    print()
