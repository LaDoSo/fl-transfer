"""Dev tool: find FL 25.2 projects with several samplers and patterns (counts only)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer.flp import Project
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
rows = []
for root in sys.argv[1:]:
    for d, _, fs in os.walk(root):
        for f in fs:
            if not f.lower().endswith(".flp"): continue
            path = os.path.join(d, f)
            try:
                if os.path.getsize(path) > 30_000_000: continue
                p = Project.load(path)
            except Exception: continue
            if not p.version.startswith("25.2"): continue
            ch = p.channels(); pats = p.patterns()
            kinds = [c["type"] for c in ch]
            rows.append((kinds.count(0), kinds.count(4), kinds.count(2), len([1 for x in pats.values() if x["notes"]]), len(ch), path))
rows.sort(reverse=True)
for r in rows[:8]: print("samplers=%d audio=%d plugins=%d patterns_with_notes=%d channels=%d | %s" % r)
print("candidates:", len(rows))
