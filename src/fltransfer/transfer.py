"""Copy channels and patterns from one project into another.

Nothing here touches a file on disk except save_new(), which refuses to
overwrite.  The destination Project is modified in memory only by inserting
events; none of its existing events are changed, and verify() proves that.

Layout facts this relies on (FL Studio 24.x and 25.x, see docs/FORMAT.md):

  head      version, tempo, ... then one (65, 224) pair per pattern that has
            notes, then three 226 events
  channels  one block per channel starting with 64 <iid>; iids are 0..n-1 in
            file order; the header's channel count equals n
  meta      pattern name/colour blocks starting with 65 sit after the first
            channel block
  tail      99 (arrangement), playlist, tracks, mixer
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import flp
from .flp import Event, FLPError, Project

CH_MIXER_TRACK = 104      # word: mixer insert the channel is routed to, 0 = master
CH_GROUP = 145            # dword: index into the channel filter groups (231)
CH_LAYER_CHILD = 94       # word: child iid of a layer channel
HEAD_END = (226, 227)     # first events after the pattern note blocks

SAMPLER, PLUGIN, LAYER, AUDIO_CLIP, AUTOMATION = 0, 2, 3, 4, 5
COPYABLE = {SAMPLER: "sampler", AUDIO_CLIP: "audio clip", PLUGIN: "plugin"}
MIN_TARGET = (24,)
MIN_SOURCE = (20,)


class TransferError(FLPError):
    pass


def _ver(p: Project) -> tuple[int, ...]:
    return flp._version_tuple(p.version.encode())


def _text(s: str) -> bytes:
    return s.encode("utf-16-le") + b"\0\0"


@dataclass
class Layout:
    first_ch: int                      # index of the first 64, or of the tail
    tail: int                          # index of event 99 (end of channel area)
    channels: list[tuple[int, int, int]] = field(default_factory=list)   # iid, start, end
    meta: dict[int, tuple[int, int]] = field(default_factory=dict)       # pattern -> start, end
    notes: dict[int, tuple[int, int]] = field(default_factory=dict)      # pattern -> start, end
    head_end: int = 0                  # where new (65, 224) groups go
    groups: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, p: Project) -> "Layout":
        ev = p.events
        ids = [e.id for e in ev]
        if flp.ARR_NEW not in ids:
            raise TransferError("no arrangement event (99): not a full project of a supported version")
        tail = ids.index(flp.ARR_NEW)
        first = ids.index(flp.CH_NEW) if flp.CH_NEW in ids[:tail] else tail
        lay = cls(first, tail)
        uni = p.unicode
        lay.groups = [e.text(uni) for e in ev[:first] if e.id == flp.GROUP_NAME]
        # head: pattern note groups
        lay.head_end = next((i for i in range(first) if ids[i] in HEAD_END), first)
        i = 0
        while i < lay.head_end:
            if ids[i] == flp.PAT_NEW:
                j = i + 1
                while j < lay.head_end and ids[j] in (flp.PAT_NOTES, flp.PAT_CTRL):
                    j += 1
                lay.notes[ev[i].value] = (i, j)
                i = j
            else:
                i += 1
        # channel area: channel blocks and pattern meta blocks
        i = first
        while i < tail:
            j = i + 1
            while j < tail and ids[j] not in (flp.CH_NEW, flp.PAT_NEW):
                j += 1
            if ids[i] == flp.CH_NEW:
                lay.channels.append((ev[i].value, i, j))
            elif ids[i] == flp.PAT_NEW:
                lay.meta[ev[i].value] = (i, j)
            else:
                raise TransferError(f"unexpected event {ids[i]} at the start of the channel area")
            i = j
        iids = [c[0] for c in lay.channels]
        if iids != list(range(len(iids))):
            raise TransferError(f"channel ids are not 0..n-1 in file order: {iids[:20]}")
        if p.n_channels != len(iids):
            raise TransferError(f"header says {p.n_channels} channels, file has {len(iids)}")
        return lay

    def meta_insert(self) -> int:
        """Pattern meta blocks follow the first channel block."""
        if self.meta:
            return max(end for _, end in self.meta.values())
        if self.channels:
            return self.channels[0][2]
        return self.tail


@dataclass
class Report:
    channels: list[tuple[int, int, str]] = field(default_factory=list)   # src iid, new iid, name
    patterns: list[tuple[int, int, str, int]] = field(default_factory=list)  # src, new, name, notes
    reused: list[tuple[int, int, str]] = field(default_factory=list)     # src iid, dst iid, name
    warnings: list[str] = field(default_factory=list)
    missing_samples: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"new channel #{d}: {name} (source #{s})" for s, d, name in self.channels]
        out += [f"reused existing channel #{d}: {name} (source #{s})" for s, d, name in self.reused]
        out += [f"new Pattern #{d}: {name or '(unnamed)'}, {n} notes (source #{s})" for s, d, name, n in self.patterns]
        out += [f"sample file not found: {m}" for m in self.missing_samples]
        out += [f"note: {w}" for w in self.warnings]
        return out


class Transfer:
    """One copy operation from src into dst.  Use add_channels / add_patterns, then verify/save."""

    def __init__(self, src: Project, dst: Project, *, force: bool = False,
                 keep_routing: bool = False, src_path: str | None = None):
        self.src, self.dst = src, dst
        self.keep_routing = keep_routing
        self.src_dir = os.path.dirname(os.path.abspath(src_path)) if src_path else None
        self.report = Report()
        self._original = [(e, e.id, e.data) for e in dst.events]
        self._original_count = dst.n_channels
        self._inserted: set[int] = set()        # id() of events this transfer added
        self._chan_map: dict[int, int] = {}     # src iid -> dst iid
        for label, p in (("source", src), ("target", dst)):
            problems = p.sanity()
            if problems:
                raise TransferError(f"{label} project fails the structure check: {problems[0]}")
        sv, dv = _ver(src), _ver(dst)
        if not force:
            if dv < MIN_TARGET:
                raise TransferError(f"target was saved by FL {dst.version}; only FL 24 and later are supported")
            if sv < MIN_SOURCE:
                raise TransferError(f"source was saved by FL {src.version}; only FL 20 and later are supported")
            if sv[:2] > dv[:2]:
                raise TransferError(
                    f"source (FL {src.version}) is newer than target (FL {dst.version}); "
                    "re-save the target in the newer FL first")
        if sv[:2] != dv[:2]:
            self.report.warnings.append(f"the source was saved by FL {src.version}, the target by FL {dst.version}")
        if not (src.unicode and dst.unicode):
            raise TransferError("projects older than FL 11.5 are not supported")
        self.src_lay = Layout.of(src)
        self.src_channels = {c["iid"]: c for c in src.channels()}
        self.src_patterns = src.patterns()

    # ---- helpers ---------------------------------------------------------
    def _insert(self, index: int, events: list[Event]) -> None:
        self.dst.events[index:index] = events
        self._inserted.update(id(e) for e in events)

    def _dst_group_for(self, src_block: list[Event]) -> int:
        """Pick the target filter group: same name if it exists, else Unsorted, else 0."""
        dst_groups = Layout.of(self.dst).groups
        src_groups = self.src_lay.groups
        idx = next((e.value for e in src_block if e.id == CH_GROUP), 0)
        name = src_groups[idx] if idx < len(src_groups) else None
        for want in (name, "Unsorted"):
            if want in dst_groups:
                return dst_groups.index(want)
        return 0

    def _resolve_sample(self, path: str) -> str | None:
        """Absolute file path of a sample reference, or None if it cannot be resolved here."""
        if not path:
            return None
        p = path
        if p.startswith("%"):
            return None  # FL variable such as %FLStudioFactoryData%: FL resolves it itself
        if not os.path.isabs(p) and self.src_dir:
            p = os.path.join(self.src_dir, p)
        return p

    # ---- channels --------------------------------------------------------
    def add_channel(self, src_iid: int) -> int:
        if src_iid in self._chan_map:
            return self._chan_map[src_iid]
        info = self.src_channels.get(src_iid)
        if info is None:
            raise TransferError(f"source has no channel #{src_iid}")
        if info["type"] not in COPYABLE:
            kind = flp.CH_TYPES.get(info["type"], str(info["type"]))
            raise TransferError(f"channel #{src_iid} {info['name']!r} is a {kind} channel; "
                                "only sampler, audio clip and plugin channels can be copied")
        _, start, end = next(c for c in self.src_lay.channels if c[0] == src_iid)
        lay = Layout.of(self.dst)
        new_iid = len(lay.channels)
        block = [Event(e.id, e.data) for e in self.src.events[start:end]]
        group = self._dst_group_for(block)
        for e in block:
            if e.id == flp.CH_NEW:
                e.data = struct.pack("<H", new_iid)
            elif e.id == CH_MIXER_TRACK and not self.keep_routing:
                e.data = struct.pack("<H", 0)
            elif e.id == CH_GROUP:
                e.data = struct.pack("<I", group)
            elif e.id == flp.SAMPLE_PATH:
                path = e.text(True)
                real = self._resolve_sample(path)
                if real and real != path:
                    e.data = _text(real)
                if real and not os.path.isfile(real):
                    self.report.missing_samples.append(real)
        self._insert(lay.tail, block)
        self.dst.n_channels += 1
        self._chan_map[src_iid] = new_iid
        self.report.channels.append((src_iid, new_iid, info["name"]))
        if info["type"] == PLUGIN:
            self.report.warnings.append(
                f"channel {info['name']} is a plugin ({info['internal']}); the target computer needs that plugin installed")
        return new_iid

    def reuse_channel(self, src_iid: int, dst_iid: int) -> None:
        """Make notes of src channel play an existing target channel instead of copying it."""
        dst = {c["iid"]: c for c in self.dst.channels()}
        if dst_iid not in dst:
            raise TransferError(f"target has no channel #{dst_iid}")
        if src_iid not in self.src_channels:
            raise TransferError(f"source has no channel #{src_iid}")
        self._chan_map[src_iid] = dst_iid
        self.report.reused.append((src_iid, dst_iid, dst[dst_iid]["name"]))

    # ---- patterns --------------------------------------------------------
    def add_pattern(self, src_num: int) -> int:
        info = self.src_patterns.get(src_num)
        if info is None:
            raise TransferError(f"source has no pattern #{src_num}")
        if info["legacy"]:
            raise TransferError(f"pattern #{src_num} uses a pre-FL4 note layout")
        for iid in sorted(info["channels"]):
            if iid not in self._chan_map:
                self.add_channel(iid)
        lay = Layout.of(self.dst)
        used = set(lay.notes) | set(lay.meta) | {e.value for e in self.dst.events if e.id == flp.PAT_NEW}
        new_num = max(used, default=0) + 1
        scale = self.dst.ppq / self.src.ppq
        head: list[Event] = []
        if src_num in self.src_lay.notes:
            a, b = self.src_lay.notes[src_num]
            for e in self.src.events[a:b]:
                if e.id == flp.PAT_NEW:
                    head.append(Event(e.id, struct.pack("<H", new_num)))
                elif e.id == flp.PAT_NOTES:
                    head.append(Event(e.id, self._remap_notes(e.data, scale)))
                elif e.id == flp.PAT_CTRL and e.data:
                    self.report.warnings.append(
                        f"the event automation in Pattern #{src_num} ({len(e.data) // 12} points) was not copied")
        meta: list[Event] = []
        taken = {d["name"] for d in self.dst.patterns().values() if d["name"]}
        name = info["name"]
        if src_num in self.src_lay.meta:
            a, b = self.src_lay.meta[src_num]
            for e in self.src.events[a:b]:
                if e.id == flp.PAT_NEW:
                    meta.append(Event(e.id, struct.pack("<H", new_num)))
                elif e.id == flp.PAT_NAME:
                    name = _unique(e.text(True), taken)
                    meta.append(Event(e.id, _text(name)))
                else:
                    meta.append(Event(e.id, e.data))
        # insert the later position first so the earlier index stays valid
        if meta:
            self._insert(lay.meta_insert(), meta)
        if head:
            self._insert(lay.head_end, head)
        self.report.patterns.append((src_num, new_num, name, info["notes"]))
        return new_num

    def _remap_notes(self, data: bytes, scale: float) -> bytes:
        out = bytearray(data)
        for off in range(0, len(data), flp.NOTE_SIZE):
            pos, _flags, chan, length = struct.unpack_from("<IHHI", data, off)
            if chan not in self._chan_map:
                raise TransferError(f"note refers to channel #{chan}, which has no mapping")
            struct.pack_into("<H", out, off + 6, self._chan_map[chan])
            if scale != 1:
                struct.pack_into("<I", out, off, round(pos * scale))
                struct.pack_into("<I", out, off + 8, round(length * scale))
        return bytes(out)

    # ---- finishing ---------------------------------------------------------
    def verify(self) -> None:
        """Raise unless the result is the original target plus inserted events, and parses cleanly."""
        kept = [e for e in self.dst.events if id(e) not in self._inserted]
        if len(kept) != len(self._original) or any(
                e is not o or e.id != oid or e.data is not odata
                for e, (o, oid, odata) in zip(kept, self._original)):
            raise TransferError("internal error: original target events were disturbed")
        again = Project.parse(self.dst.dump())
        problems = again.sanity()
        if problems:
            raise TransferError(f"result fails the structure check: {problems[0]}")
        lay = Layout.of(again)
        if len(lay.channels) != self._original_count + len(self.report.channels):
            raise TransferError("internal error: channel count mismatch")
        n = len(lay.channels)
        for num, d in again.patterns().items():
            bad = [c for c in d["channels"] if c >= n]
            if bad:
                raise TransferError(f"pattern #{num} has notes for missing channels {bad}")

    def save_new(self, path) -> Path:
        path = Path(path)
        if path.exists():
            raise TransferError(f"refusing to overwrite {path}")
        self.verify()
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(self.dst.dump())
        os.replace(tmp, path)
        return path


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


def default_output(target_path) -> Path:
    """'Song.flp' -> 'Song (transfer).flp', then '(transfer 2)' and so on."""
    target = Path(target_path)
    out = target.with_name(f"{target.stem} (transfer){target.suffix}")
    n = 2
    while out.exists():
        out = target.with_name(f"{target.stem} (transfer {n}){target.suffix}")
        n += 1
    return out


def find_channel(p: Project, key: str) -> int:
    """'#3' selects by number, anything else by exact then case-insensitive name."""
    chans = p.channels()
    if key.startswith("#") and key[1:].isdigit():
        iid = int(key[1:])
        if any(c["iid"] == iid for c in chans):
            return iid
        raise TransferError(f"no channel {key}")
    for test in (lambda c: c["name"] == key, lambda c: c["name"].lower() == key.lower()):
        hits = [c["iid"] for c in chans if test(c)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise TransferError(f"{len(hits)} channels are named {key!r}; use #number: {hits}")
    raise TransferError(f"no channel named {key!r}")


def find_pattern(p: Project, key: str) -> int:
    pats = p.patterns()
    if key.startswith("#") and key[1:].isdigit():
        num = int(key[1:])
        if num in pats:
            return num
        raise TransferError(f"no pattern {key}")
    for test in (lambda d: d["name"] == key, lambda d: d["name"].lower() == key.lower()):
        hits = [n for n, d in pats.items() if test(d)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise TransferError(f"{len(hits)} patterns are named {key!r}; use #number: {hits}")
    raise TransferError(f"no pattern named {key!r}")
