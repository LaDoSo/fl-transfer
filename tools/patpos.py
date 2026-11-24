"""Dev tool: where do pattern meta blocks sit relative to channels? (structure only)"""
import os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer.flp import Project
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
seen = collections.Counter(); shapes = collections.Counter(); n = 0
for root in sys.argv[1:]:
    for d, _, fs in os.walk(root):
        for f in fs:
            if not f.lower().endswith(".flp"): continue
            try:
                path = os.path.join(d, f)
                if os.path.getsize(path) > 30_000_000: continue
                p = Project.load(path)
            except Exception: continue
            if not p.version.startswith(("25.", "24.")): continue
            n += 1
            ev = p.events
            a = next((i for i, e in enumerate(ev) if e.id == 64), len(ev)); b = next((i for i, e in enumerate(ev) if e.id == 99), len(ev))
            head_pats = [e.value for e in ev[:a] if e.id == 65]
            order = []; cur = None
            for e in ev[a:b]:
                if e.id == 64: order.append("c"); cur = None
                elif e.id == 65: cur = [e.id]; order.append("p%d" % e.value)
                elif cur is not None: cur.append(e.id)
                if cur is not None and e.id != 65: pass
            # shapes of pattern blocks
            cur = None
            for e in ev[a:b]:
                if e.id == 65:
                    if cur: shapes[" ".join(map(str, cur))] += 1
                    cur = [65]
                elif e.id == 64:
                    if cur: shapes[" ".join(map(str, cur))] += 1
                    cur = None
                elif cur is not None: cur.append(e.id)
            if cur: shapes[" ".join(map(str, cur))] += 1
            if not any(o != "c" for o in order): seen["no pattern meta blocks"] += 1; continue
            # compress runs of c
            out = []; run = 0
            for o in order:
                if o == "c": run += 1
                else:
                    if run: out.append("c*%d" % run); run = 0
                    out.append(o)
            if run: out.append("c*%d" % run)
            seen[p.version[:6] + " head=" + str(head_pats)[:60] + " :: " + " ".join(out)[:160]] += 1
            tailpat = [e.value for e in ev[b:] if e.id == 65]
            if tailpat: seen["PATTERN EVENTS AFTER 99"] += 1
print("files", n)
for k, v in seen.most_common(30): print(v, k)
print("pattern block shapes:")
for k, v in shapes.most_common(12): print(v, k)
