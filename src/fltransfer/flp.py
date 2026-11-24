"""Lossless event-level reader/writer for FL Studio project files.

An FLP is a "FLhd" header chunk followed by one "FLdt" chunk holding a flat
stream of events.  The event ID alone decides the payload size:

    0..63     one byte
    64..127   two bytes   (little endian)
    128..191  four bytes  (little endian)
    192..255  varint length + that many bytes

Everything is kept as raw bytes, so parse() -> dump() is byte-identical for
any file, including events this module knows nothing about.  .fst (channel
preset) and .fsc (score) files use the same container.

One exception to the size rule was found in files saved by FL Studio 25.2.3
and 25.2.5: event 172 sits in the four-byte range but carries three bytes
(01 01 00) and is followed by a text event 192.  Reading it as four bytes
does not fail loudly: the stream drifts, usually re-synchronises a few
hundred bytes later and still dumps byte-identically, while the header and
the first pattern are silently misread.  sanity() exists to catch that kind
of drift.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


class FLPError(ValueError):
    pass


# Event IDs used by this project. Names follow the community FLP notes.
CH_ENABLED = 0
CH_TYPE = 21
CH_ROUTED_TO = 22        # mixer insert, FL 20
CH_INSERT = 104           # mixer insert, FL 21 and later (word)
CH_NEW = 64
PAT_NEW = 65
CUR_PAT = 67
ARR_NEW = 99
CH_COLOR = 128
PAT_COLOR = 150
TEMPO = 156
CH_NAME_OLD = 192
PAT_NAME = 193
TITLE = 194
SAMPLE_PATH = 196
FL_VERSION = 199
PLUGIN_INTERNAL = 201
PLUGIN_NAME = 203
INSERT_NAME = 204
PLUGIN_PARAMS = 213
PAT_CTRL = 223
PAT_NOTES = 224
GROUP_NAME = 231
PLAYLIST = 233
INSERT_FLAGS = 236
TRACK_DATA = 238
ARR_NAME = 241

QUIRK_172 = 172        # see module docstring
QUIRK_172_SINCE = (25, 2)

CH_TYPES = {0: "sampler", 2: "plugin", 3: "layer", 4: "audio clip", 5: "automation"}
NOTE_SIZE = 24
NOTE_STRUCT = struct.Struct("<IHHIHHBBBBBBBB")
PATTERN_BASE = 20480


def _version_tuple(raw: bytes) -> tuple[int, ...]:
    out = []
    for part in raw.rstrip(b"\0").decode("ascii", "replace").split("."):
        if not part.isdigit():
            break
        out.append(int(part))
    return tuple(out)


@dataclass
class Event:
    id: int
    data: bytes

    @property
    def value(self) -> int:
        """Integer value of a byte/word/dword event."""
        if self.id >= 192:
            raise FLPError(f"event {self.id} holds data, not an integer")
        return int.from_bytes(self.data, "little")

    def text(self, unicode: bool = True) -> str:
        if unicode:
            return self.data.decode("utf-16-le", "replace").rstrip("\0")
        return self.data.decode("latin-1").rstrip("\0")


@dataclass
class Project:
    format: int
    n_channels: int
    ppq: int
    events: list[Event] = field(default_factory=list)

    # ---- container -------------------------------------------------------
    @classmethod
    def parse(cls, raw: bytes) -> "Project":
        if raw[:4] != b"FLhd":
            raise FLPError("not an FL Studio file: missing FLhd")
        (hlen,) = struct.unpack_from("<I", raw, 4)
        if hlen != 6:
            raise FLPError(f"unexpected header length {hlen}")
        fmt, nch, ppq = struct.unpack_from("<hHH", raw, 8)
        pos = 8 + hlen
        if raw[pos:pos + 4] != b"FLdt":
            raise FLPError("missing FLdt chunk")
        (dlen,) = struct.unpack_from("<I", raw, pos + 4)
        pos += 8
        end = pos + dlen
        if end != len(raw):
            raise FLPError(f"FLdt length {dlen} does not match file size")
        events: list[Event] = []
        version: tuple[int, ...] = ()
        while pos < end:
            eid = raw[pos]
            pos += 1
            if (eid == QUIRK_172 and version >= QUIRK_172_SINCE
                    and pos + 3 < end and raw[pos + 3] == CH_NAME_OLD):
                size = 3
            elif eid < 64:
                size = 1
            elif eid < 128:
                size = 2
            elif eid < 192:
                size = 4
            else:
                size = shift = 0
                while True:
                    b = raw[pos]
                    pos += 1
                    size |= (b & 0x7F) << shift
                    shift += 7
                    if not b & 0x80:
                        break
            if pos + size > end:
                raise FLPError(f"event {eid} runs past the end of the file")
            events.append(Event(eid, raw[pos:pos + size]))
            if eid == FL_VERSION:
                version = _version_tuple(events[-1].data)
            pos += size
        return cls(fmt, nch, ppq, events)

    @classmethod
    def load(cls, path) -> "Project":
        return cls.parse(Path(path).read_bytes())

    def dump(self) -> bytes:
        out = bytearray()
        for e in self.events:
            out.append(e.id)
            if e.id >= 192:  # shorter ids write their payload as is, quirks included
                n = len(e.data)
                while True:
                    b = n & 0x7F
                    n >>= 7
                    out.append(b | (0x80 if n else 0))
                    if not n:
                        break
            out += e.data
        head = b"FLhd" + struct.pack("<IhHH", 6, self.format, self.n_channels, self.ppq)
        return head + b"FLdt" + struct.pack("<I", len(out)) + bytes(out)

    # ---- read-only views ---------------------------------------------------
    @property
    def version(self) -> str:
        for e in self.events:
            if e.id == FL_VERSION:
                return e.data.decode("ascii", "replace").rstrip("\0")
        return ""

    @property
    def unicode(self) -> bool:
        """FL 11.5 and later store text as UTF-16-LE."""
        v = _version_tuple(self.version.encode())
        return not v or v >= (11, 5)

    def sanity(self) -> list[str]:
        """Cheap structural checks; an empty list means nothing looks wrong."""
        problems = []
        if not self.events or self.events[0].id != FL_VERSION:
            problems.append("first event is not the FL version")
        fixed = {QUIRK_172: (3, 4)}
        for i, e in enumerate(self.events):
            if e.id < 192:
                want = fixed.get(e.id, (1 if e.id < 64 else 2 if e.id < 128 else 4,))
                if len(e.data) not in want:
                    problems.append(f"event #{i} id {e.id} has {len(e.data)} payload bytes")
            elif e.id == PAT_NOTES and len(e.data) % NOTE_SIZE and self.version[:2] not in ("1.", "2.", "3."):
                problems.append(f"event #{i}: note block of {len(e.data)} bytes")
        heads = [e.id for e in self.events[:12]]
        if _version_tuple(self.version.encode()) >= (25, 2, 3) and QUIRK_172 not in heads:
            problems.append("FL 25.2.3+ file without event 172 in the header area")
        if sum(e.id == CH_ENABLED for e in self.events[:40]) > 3:
            problems.append("many channel events in the header area: stream drift?")
        return problems

    def channels(self) -> list[dict]:
        """One dict per channel rack entry, in file order."""
        out: list[dict] = []
        cur = None
        uni = self.unicode
        for i, e in enumerate(self.events):
            if e.id == CH_NEW:
                cur = {"iid": e.value, "start": i, "end": i + 1, "type": None,
                       "name": "", "internal": "", "sample": "", "insert": None, "color": None}
                out.append(cur)
                continue
            if cur is None:
                continue
            if e.id in (ARR_NEW, INSERT_FLAGS, PAT_NEW, CUR_PAT):
                cur = None
                continue
            cur["end"] = i + 1
            if e.id == CH_TYPE:
                cur["type"] = e.value
            elif e.id == CH_COLOR:
                cur["color"] = tuple(e.data[:3])        # red, green, blue
            elif e.id in (CH_ROUTED_TO, CH_INSERT):
                cur["insert"] = int.from_bytes(e.data, "little", signed=True)
            elif e.id in (PLUGIN_NAME, CH_NAME_OLD) and not cur["name"]:
                cur["name"] = e.text(uni)
            elif e.id == PLUGIN_INTERNAL:
                cur["internal"] = e.text(uni)
            elif e.id == SAMPLE_PATH:
                cur["sample"] = e.text(uni)
        return out

    def patterns(self) -> dict[int, dict]:
        """Pattern number -> name, note count and the channel iids it plays."""
        out: dict[int, dict] = {}
        cur = None
        uni = self.unicode
        for e in self.events:
            if e.id == PAT_NEW:
                cur = out.setdefault(e.value, {"name": "", "notes": 0, "channels": set(),
                                               "length": 0, "ctrl_bytes": 0, "legacy": False,
                                               "color": None})
            elif e.id in (CH_NEW, ARR_NEW, INSERT_FLAGS):
                cur = None
            elif cur is None:
                continue
            elif e.id == PAT_NAME:
                cur["name"] = e.text(uni)
            elif e.id == PAT_COLOR:
                cur["color"] = tuple(e.data[:3])
            elif e.id == PAT_NOTES:
                if len(e.data) % NOTE_SIZE:
                    cur["legacy"] = True  # pre-FL4 score layout, not handled
                    continue
                for off in range(0, len(e.data), NOTE_SIZE):
                    n = NOTE_STRUCT.unpack_from(e.data, off)
                    cur["notes"] += 1
                    cur["channels"].add(n[2])
                    cur["length"] = max(cur["length"], n[0] + n[3])
            elif e.id == PAT_CTRL:
                cur["ctrl_bytes"] += len(e.data)
        return out

    def playlist_blocks(self) -> list[int]:
        return [len(e.data) for e in self.events if e.id == PLAYLIST]
