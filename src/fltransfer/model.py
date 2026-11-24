"""What the window shows for one source project; no GUI code in here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import export, flp
from .flp import Project
from .transfer import AUDIO_CLIP, PLUGIN, SAMPLER, Transfer, TransferError, default_output

KIND_LABEL = {SAMPLER: "Sample", AUDIO_CLIP: "Audio clip", PLUGIN: "Plugin", 3: "Layer", 5: "Automation"}


def notes_text(n: int) -> str:
    return f"{n} note" + ("" if n == 1 else "s")
PRESET, SAMPLE = "preset", "sample"


@dataclass(frozen=True)
class Row:
    key: str            # "c3", "p2", "p2c3"
    parent: str         # "" for top level rows
    name: str
    detail: str
    draggable: bool
    why_not: str = ""


@dataclass(frozen=True)
class Clip:
    """One card in the window, drawn like a clip in FL's playlist."""
    key: str                    # "c3" or "p2"
    kind: str                   # "pattern", "audio", "plugin" or "other"
    name: str
    detail: str
    color: tuple | None         # (r, g, b) as stored in the project, None for FL's default
    enabled: bool
    why_not: str = ""
    notes: tuple = ()           # pattern: (position, length, key, channel iid) in ticks
    length: int = 0             # pattern: ticks, rounded up to a whole bar
    channels: tuple = ()        # pattern: (iid, name) of every channel it plays
    sample: str = ""            # audio: file found on disk, "" when missing
    sample_name: str = ""       # audio: file name stored in the project


class Source:
    def __init__(self, path):
        self.path = str(path)
        self.project: Project | None = None
        self.mtime = 0.0
        self.load()

    def load(self) -> None:
        self.mtime = os.path.getmtime(self.path)
        export._nearby.clear()
        project = Project.load(self.path)
        problems = project.sanity()
        if problems:
            raise TransferError(f"The project failed the structure check: {problems[0]}")
        self.project = project
        self.channels = {c["iid"]: c for c in project.channels()}
        self.patterns = project.patterns()

    def changed_on_disk(self) -> bool:
        try:
            return os.path.getmtime(self.path) != self.mtime
        except OSError:
            return False

    def summary(self) -> str:
        p = self.project
        return f"FL {p.version} · {len(self.channels)} channels · {len(self.patterns)} Patterns"

    def rows(self) -> list[Row]:
        out: list[Row] = []
        for iid, c in self.channels.items():
            kind = c["type"]
            label = KIND_LABEL.get(kind, str(kind))
            if kind in (SAMPLER, AUDIO_CLIP):
                detail = f"{label} · {os.path.basename(c['sample'].replace(chr(92), '/')) or '(empty)'}"
            elif kind == PLUGIN:
                detail = f"{label} · {c['internal']}"
            else:
                detail = label
            ok = kind in (SAMPLER, AUDIO_CLIP, PLUGIN)
            out.append(Row(f"c{iid}", "", c["name"] or f"Channel {iid}", detail, ok,
                           "" if ok else f"{label} channels are not supported yet"))
        for num, d in sorted(self.patterns.items()):
            name = d["name"] or f"Pattern {num}"
            used = sorted(d["channels"])
            if d["legacy"] or not d["notes"]:
                out.append(Row(f"p{num}", "", name, "no notes", False, "This Pattern has no notes"))
                continue
            single = len(used) == 1
            detail = f"{notes_text(d['notes'])} · " + (self._cname(used[0]) if single else f"{len(used)} channels")
            out.append(Row(f"p{num}", "", name, detail, single,
                           "" if single else "Multi-channel Pattern: expand it and drag one channel at a time, or use Merge below"))
            if not single:
                counts = self._note_counts(num)
                for iid in used:
                    out.append(Row(f"p{num}c{iid}", f"p{num}", self._cname(iid), notes_text(counts.get(iid, 0)), True))
        return out

    def clips(self) -> list[Clip]:
        """Patterns first, then sample channels, then instruments: what the window shows."""
        rows = {r.key: r for r in self.rows()}
        out: list[Clip] = []
        bar = max(1, self.project.ppq * 4)
        for num, d in sorted(self.patterns.items()):
            r = rows[f"p{num}"]
            notes = tuple(self._notes(num)) if d["notes"] and not d["legacy"] else ()
            end = max((n[0] + max(n[1], 1) for n in notes), default=0)
            used = tuple((i, self._cname(i)) for i in sorted(d["channels"]))
            ok = bool(notes)
            names = ", ".join(n for _, n in used[:3]) + (" …" if len(used) > 3 else "")
            out.append(Clip(r.key, "pattern", r.name, f"{notes_text(len(notes))} · {names}" if ok else "no notes",
                            d.get("color"), ok, "" if ok else "This Pattern has no notes", notes,
                            max(bar, -(-end // bar) * bar), used))
        for iid, c in self.channels.items():
            r = rows[f"c{iid}"]
            kind = c["type"]
            if kind in (SAMPLER, AUDIO_CLIP) and c["sample"]:
                found = export.sample_file(self.project, self.path, iid)
                stored = c["sample"].replace(chr(92), "/").rsplit("/", 1)[-1]
                out.append(Clip(r.key, "audio", r.name, KIND_LABEL[kind], c.get("color"), True, "",
                                sample=str(found) if found else "", sample_name=stored))
            elif kind in (SAMPLER, PLUGIN):
                detail = c["internal"] or "Plugin" if kind == PLUGIN else "Empty sampler"
                out.append(Clip(r.key, "plugin", r.name, detail, c.get("color"), True))
            else:
                out.append(Clip(r.key, "other", r.name, r.detail, c.get("color"), False, r.why_not))
        order = {"pattern": 0, "audio": 1, "plugin": 2, "other": 3}
        return sorted(out, key=lambda c: order[c.kind])

    def _notes(self, num: int):
        from .transfer import Layout
        a, b = Layout.of(self.project).notes[num]
        for e in self.project.events[a:b]:
            if e.id == flp.PAT_NOTES and len(e.data) % flp.NOTE_SIZE == 0:
                for off in range(0, len(e.data), flp.NOTE_SIZE):
                    n = flp.NOTE_STRUCT.unpack_from(e.data, off)
                    yield (n[0], n[3], min(n[4], 131), n[2])

    def files_for(self, key: str, channel_mode: str = PRESET) -> list[Path]:
        """Files handed to FL when a channel card is dragged out."""
        pat, chan = self.parse_key(key)
        if pat is not None:
            raise TransferError("A Pattern has no file of its own: it is sent together with its instruments. Use “Send to FL” or drag it straight into FL")
        if channel_mode == SAMPLE:
            found = export.sample_file(self.project, self.path, chan)
            if found is not None:
                return [found]
        return [self.drag_file(key, PRESET)]

    def _cname(self, iid: int) -> str:
        c = self.channels.get(iid)
        return (c["name"] if c and c["name"] else f"Channel {iid}")

    def _note_counts(self, num: int) -> dict[int, int]:
        import struct
        from .transfer import Layout
        counts: dict[int, int] = {}
        a, b = Layout.of(self.project).notes[num]
        for e in self.project.events[a:b]:
            if e.id == flp.PAT_NOTES:
                for off in range(0, len(e.data), flp.NOTE_SIZE):
                    ch = struct.unpack_from("<H", e.data, off + 6)[0]
                    counts[ch] = counts.get(ch, 0) + 1
        return counts

    @staticmethod
    def parse_key(key: str) -> tuple[int | None, int | None]:
        """'c3' -> (None, 3); 'p2' -> (2, None); 'p2c3' -> (2, 3)"""
        pat = chan = None
        if key.startswith("p"):
            head, _, tail = key[1:].partition("c")
            pat = int(head)
            chan = int(tail) if tail else None
        else:
            chan = int(key[1:])
        return pat, chan

    def drag_file(self, key: str, channel_mode: str = PRESET) -> Path:
        """Create (or locate) the file to hand to FL for one row."""
        pat, chan = self.parse_key(key)
        if pat is None:
            if channel_mode == SAMPLE:
                found = export.sample_file(self.project, self.path, chan)
                if found is None:
                    raise TransferError("This channel has no sample file, or the file is no longer where it was")
                return found
            return export.write_preset(self.project, self.path, chan)
        if chan is None:
            used = sorted(self.patterns[pat]["channels"])
            if len(used) != 1:
                raise TransferError("Multi-channel Pattern: expand it and drag one channel at a time")
            chan = used[0]
        return export.write_score(self.project, self.path, pat, chan)

    def send_plan(self, keys: list[str], target=None) -> list[tuple[Path, int, int]]:
        """Files to send to the running FL for the chosen cards: (file, channels added, patterns added).

        All chosen patterns travel in one preset file together with every channel they play, so
        an instrument shared by several patterns is loaded once.  That file numbers its notes by
        the channel positions of the receiving project, hence target (live.Target).  A chosen
        channel that such a pattern already brings is not sent a second time.  Channels on their
        own go as a plain preset (FL ignores a bare wav passed this way).
        """
        pats = [n for n in dict.fromkeys(self.parse_key(k)[0] for k in keys if k.startswith("p"))]
        plan: list[tuple[Path, int, int]] = []
        brought: set[int] = set()
        if pats:
            if target is None:
                raise TransferError("The channel count of the project in FL is unknown, so the Pattern cannot be sent with its instruments")
            for num in pats:
                brought |= set(self.patterns[num]["channels"])
            path, added = export.write_pattern_preset(self.project, self.path, pats, target.next_channel,
                                                      target.next_pattern, target.ppq)
            plan.append((path, added, len(pats)))
        for key in keys:
            if key.startswith("c") and self.parse_key(key)[1] not in brought:
                plan.append((self.drag_file(key, PRESET), 1, 0))
        return plan

    def merge_into(self, target_path: str, keys: list[str], keep_routing: bool = False):
        """Route A: write a new copy of the target with the selected rows added."""
        dst = Project.load(target_path)
        t = Transfer(self.project, dst, keep_routing=keep_routing, src_path=self.path)
        pats = sorted({self.parse_key(k)[0] for k in keys if k.startswith("p")})
        chans = sorted({self.parse_key(k)[1] for k in keys if k.startswith("c")})
        for iid in chans:
            t.add_channel(iid)
        for num in pats:
            t.add_pattern(num)
        if not (t.report.channels or t.report.patterns):
            raise TransferError("Nothing that can be merged is selected")
        out = t.save_new(default_output(target_path))
        return out, t.report


def recent_projects(limit: int = 25) -> list[str]:
    """FL Studio's recently opened projects, newest first (Windows registry)."""
    try:
        import winreg
    except ImportError:
        return []
    found: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Image-Line") as root:
            names = []
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(root, i))
                except OSError:
                    break
                i += 1
        for name in sorted((n for n in names if n.startswith("FL Studio ")), reverse=True):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Image-Line\{name}\MRU") as k:
                    j = 0
                    while len(found) < limit:
                        try:
                            value = winreg.QueryValueEx(k, str(j))[0]
                        except OSError:
                            break
                        if value and value not in found and os.path.isfile(value):
                            found.append(value)
                        j += 1
            except OSError:
                continue
    except OSError:
        pass
    return found
