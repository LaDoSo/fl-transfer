"""Command line entry: python -m fltransfer inspect|check|copy|gui ..."""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter

from .flp import CH_TYPES, Project


def inspect(path: str, show_events: bool) -> int:
    with open(path, "rb") as fh:
        raw = fh.read()
    p = Project.parse(raw)
    same = p.dump() == raw
    print(f"file      : {path}")
    print(f"version   : {p.version}  ppq={p.ppq}  events={len(p.events)}  size={len(raw)}")
    print(f"roundtrip : {'identical' if same else 'DIFFERENT'}")
    for problem in p.sanity():
        print(f"WARNING   : {problem}")
    chans = p.channels()
    print(f"channels  : {len(chans)}")
    for c in chans:
        kind = CH_TYPES.get(c["type"], str(c["type"]))
        extra = c["sample"] or c["internal"]
        print(f"  iid={c['iid']:<3} {kind:<11} insert={c['insert']!s:<4} "
              f"events={c['end'] - c['start']:<4} name={c['name']!r} {extra!r}")
    pats = p.patterns()
    print(f"patterns  : {len(pats)}")
    for num, d in sorted(pats.items()):
        note = " LEGACY NOTE LAYOUT" if d["legacy"] else ""
        print(f"  #{num:<3} notes={d['notes']:<5} ticks={d['length']:<7} "
              f"channels={sorted(d['channels'])} ctrl={d['ctrl_bytes']} name={d['name']!r}{note}")
    print(f"playlist  : {p.playlist_blocks()} bytes")
    if show_events:
        counts = Counter(e.id for e in p.events)
        print("event ids :", " ".join(f"{k}x{v}" for k, v in sorted(counts.items())))
        run, prev = [], None
        for e in p.events:
            if e.id == prev:
                run[-1][1] += 1
            else:
                run.append([e.id, 1])
            prev = e.id
        seq = [f"{i}" if n == 1 else f"{i}*{n}" for i, n in run]
        more = f" ... (+{len(seq) - 400} more)" if len(seq) > 400 else ""
        print("sequence  :", " ".join(seq[:400]) + more)
    return 0 if same else 1


def check(roots: list[str]) -> int:
    """Round-trip every project under the given folders; print totals only."""
    import os
    from .flp import FLPError
    seen = Counter()
    bad = 0
    for root in roots:
        walker = [(os.path.dirname(root), [], [os.path.basename(root)])] if os.path.isfile(root) else os.walk(root)
        for folder, _, names in walker:
            for name in names:
                if not name.lower().endswith((".flp", ".fst", ".fsc")):
                    continue
                path = os.path.join(folder, name)
                try:
                    with open(path, "rb") as fh:
                        raw = fh.read()
                    p = Project.parse(raw)
                except (OSError, FLPError) as exc:
                    seen["unreadable: " + type(exc).__name__] += 1
                    continue
                kind = name[-3:].lower() + " " + ".".join(p.version.split(".")[:2])
                ok = p.dump() == raw and not (p.sanity() if kind.startswith("flp") else [])
                seen[kind + (" ok" if ok else " PROBLEM")] += 1
                bad += not ok
    for key, n in sorted(seen.items()):
        print(f"{n:6}  {key}")
    print(f"total {sum(seen.values())}, problems {bad}")
    return 1 if bad else 0


def copy(args) -> int:
    from .flp import FLPError
    from .transfer import Transfer, default_output, find_channel, find_pattern
    try:
        src, dst = Project.load(args.src), Project.load(args.dst)
        t = Transfer(src, dst, force=args.force, keep_routing=args.keep_routing, src_path=args.src)
        for pair in args.map:
            a, _, b = pair.partition("=")
            t.reuse_channel(find_channel(src, a), find_channel(dst, b))
        for key in args.channel:
            t.add_channel(find_channel(src, key))
        for key in args.pattern:
            t.add_pattern(find_pattern(src, key))
        if not (t.report.channels or t.report.patterns):
            print("nothing selected: use --channel and/or --pattern")
            return 2
        t.verify()
        for line in t.report.lines():
            print(line)
        if args.dry_run:
            print("dry run: nothing written")
            return 0
        out = t.save_new(args.out or default_output(args.dst))
        print(f"written: {out}")
        return 0
    except (FLPError, OSError) as exc:
        print(f"error: {exc}")
        return 1


def send(args) -> int:
    from . import live
    from .flp import FLPError
    from .model import Source
    from .transfer import find_channel, find_pattern
    try:
        src = Source(args.src)
        keys = [f"c{find_channel(src.project, k)}" for k in args.channel]
        keys += [f"p{find_pattern(src.project, k)}" for k in args.pattern]
        if not keys:
            print("nothing selected: use --channel and/or --pattern")
            return 2
        target = None
        try:
            target = live.find_target(args.target, known=[args.src], save=args.save_first and bool(args.pattern))
        except live.LiveError:
            if args.pattern:                 # channels alone do not need the target's numbers
                raise
        if target and args.pattern:
            print(f"target: {target.path}  channels={target.next_channel}  next pattern=#{target.next_pattern}")
        first = target.next_pattern if target else 0
        for path, channels, patterns in src.send_plan(keys, target):
            if args.dry_run:
                print(f"would send: {path}")
                continue
            live.send(path)
            if target:
                target.sent(channels, patterns)
            print(f"sent: {path}")
            time.sleep(args.gap)
        if args.pattern and not args.dry_run:
            if live.wait_delivered() and live.show_pattern(first):
                print(f"FL now shows pattern #{first}")
            else:
                print(f"select pattern #{first} in FL to see the notes")
        return 0
    except (FLPError, OSError, live.LiveError) as exc:
        print(f"error: {exc}")
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fltransfer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("inspect", help="list channels and patterns of an .flp/.fst/.fsc")
    a.add_argument("files", nargs="+")
    a.add_argument("--events", action="store_true", help="also print the event id sequence")
    cp = sub.add_parser("copy", help="copy channels and patterns from one project into a new copy of another")
    cp.add_argument("--from", dest="src", required=True, metavar="A.flp")
    cp.add_argument("--to", dest="dst", required=True, metavar="B.flp")
    cp.add_argument("--out", help="new file to write; default 'B (transfer).flp' next to B")
    cp.add_argument("--channel", action="append", default=[], metavar="NAME|#N")
    cp.add_argument("--pattern", action="append", default=[], metavar="NAME|#N")
    cp.add_argument("--map", action="append", default=[], metavar="SRC=DST",
                    help="play notes of source channel SRC on existing target channel DST instead of copying it")
    cp.add_argument("--keep-routing", action="store_true", help="keep the mixer insert number of copied channels")
    cp.add_argument("--dry-run", action="store_true", help="show what would be added, write nothing")
    cp.add_argument("--force", action="store_true", help="skip the FL version checks")
    sd = sub.add_parser("send", help="add channels/patterns of a saved project to the project open in the running FL")
    sd.add_argument("--from", dest="src", required=True, metavar="A.flp")
    sd.add_argument("--channel", action="append", default=[], metavar="NAME|#N")
    sd.add_argument("--pattern", action="append", default=[], metavar="NAME|#N")
    sd.add_argument("--target", metavar="B.flp",
                    help="saved file of the project open in FL (found from FL's title bar when omitted)")
    sd.add_argument("--save-first", action="store_true",
                    help="press Ctrl+S in FL before sending a pattern, so the channel count read from the file is current")
    sd.add_argument("--gap", type=float, default=1.0, help="seconds between two files")
    sd.add_argument("--dry-run", action="store_true", help="only create the files, do not contact FL")
    g = sub.add_parser("gui", help="open the drag-and-drop window")
    g.add_argument("project", nargs="?")
    c = sub.add_parser("check", help="round-trip every project under some folders, totals only")
    c.add_argument("roots", nargs="+")
    args = ap.parse_args(argv)
    if sys.stdout is not None:               # pythonw has no console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.cmd == "check":
        return check(args.roots)
    if args.cmd == "copy":
        return copy(args)
    if args.cmd == "send":
        return send(args)
    if args.cmd == "gui":
        from .gui import main as gui_main
        return gui_main(args.project)
    rc = 0
    for f in args.files:
        rc |= inspect(f, args.events)
        print()
    return rc


if __name__ == "__main__":
    sys.exit(main())
