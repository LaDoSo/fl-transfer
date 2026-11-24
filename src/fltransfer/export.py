"""Write single-channel preset (.fst) and score (.fsc) files from a project.

FL Studio accepts both file types by drag and drop, which is what the live
window uses: nothing in the running project is touched by this tool, FL
itself adds the channel or the notes exactly as if the file came from its
own browser.

Both files use the project container with a different header format word:
16 for a score, 32 for a channel preset.  Their event lists, as written by
FL (checked against the factory library, see docs/FORMAT.md):

  score   199 159 [169] 28 [172 192] 37  65 <0>  224 <notes, channel field 0>
  preset  199 159 [169] 28 [172 192] 37  <one channel block starting at 64>

A preset may also carry patterns for the channels it brings (pattern_preset below).  FL then
adds the channels and the patterns in one go, which is how a pattern reaches a running FL
together with its instrument.  The note channel numbers in such a file are the positions the
new channels will get in the receiving project, so that project's channel count must be known.
"""
from __future__ import annotations

import os
import re
import struct
import tempfile
from pathlib import Path

from . import flp
from .flp import Event, Project
from .transfer import AUDIO_CLIP, Layout, PLUGIN, SAMPLER, TransferError

FORMAT_SCORE = 16
FORMAT_PRESET = 32
_PREAMBLE = (flp.FL_VERSION, 159, 169, 28, flp.QUIRK_172, flp.CH_NAME_OLD, 37)


def _preamble(p: Project) -> list[Event]:
    """The leading version/build events of the project, as FL repeats them in preset files."""
    out = []
    for e in p.events:
        if e.id not in _PREAMBLE:
            break
        out.append(Event(e.id, e.data))
    if not out or out[0].id != flp.FL_VERSION:
        raise TransferError("project does not start with a version event")
    return out


def score(p: Project, pattern: int, channel: int) -> bytes:
    """The notes one channel plays in one pattern, as an .fsc file."""
    lay = Layout.of(p)
    if pattern not in lay.notes:
        raise TransferError(f"pattern #{pattern} has no notes")
    a, b = lay.notes[pattern]
    picked = bytearray()
    for e in p.events[a:b]:
        if e.id != flp.PAT_NOTES:
            continue
        for off in range(0, len(e.data), flp.NOTE_SIZE):
            note = bytearray(e.data[off:off + flp.NOTE_SIZE])
            if struct.unpack_from("<H", note, 6)[0] == channel:
                struct.pack_into("<H", note, 6, 0)
                picked += note
    if not picked:
        raise TransferError(f"pattern #{pattern} has no notes for channel #{channel}")
    events = _preamble(p) + [Event(flp.PAT_NEW, struct.pack("<H", 0)), Event(flp.PAT_NOTES, bytes(picked))]
    return Project(FORMAT_SCORE, 1, p.ppq, events).dump()


def channel_preset(p: Project, iid: int, sample_path=None) -> bytes:
    """One channel with all its settings, as an .fst file.

    sample_path replaces the stored sample location; used when the project came from another
    computer and the file was found next to the project instead.
    """
    lay = Layout.of(p)
    hit = [c for c in lay.channels if c[0] == iid]
    if not hit:
        raise TransferError(f"no channel #{iid}")
    _, a, b = hit[0]
    kind = next((e.value for e in p.events[a:b] if e.id == flp.CH_TYPE), None)
    if kind not in (SAMPLER, AUDIO_CLIP, PLUGIN):
        raise TransferError(f"channel #{iid} is a {flp.CH_TYPES.get(kind, kind)} channel, which cannot be exported")
    block = [Event(e.id, e.data) for e in p.events[a:b]]
    if sample_path:                      # the stored path is dead, the file was found somewhere else
        for e in block:
            if e.id == flp.SAMPLE_PATH:
                e.data = (str(sample_path) + "\0").encode("utf-16-le" if p.unicode else "latin-1", "replace")
    return Project(FORMAT_PRESET, 1, p.ppq, _preamble(p) + block).dump()


def pattern_preset(p: Project, patterns: list[int], first_channel: int, first_pattern: int,
                   ppq: int | None = None, samples: dict | None = None) -> tuple[bytes, list[int]]:
    """Patterns together with every channel they play, as one .fst file.

    first_channel is the number of channels the receiving project has now (the first new channel
    gets that position), first_pattern the first free pattern number there, ppq its resolution.
    samples maps a channel to the place its sample was found when the stored path is dead.
    Returns the file and the source channels it carries, in the order FL will add them.
    """
    lay = Layout.of(p)
    info = p.patterns()
    for num in patterns:
        if num not in lay.notes or not info[num]["notes"] or info[num]["legacy"]:
            raise TransferError(f"pattern #{num} has no notes")
    used = sorted({c for num in patterns for c in info[num]["channels"]})
    blocks = {c[0]: (c[1], c[2]) for c in lay.channels}
    events = _preamble(p)
    for iid in used:
        if iid not in blocks:
            raise TransferError(f"pattern refers to channel #{iid}, which does not exist")
        a, b = blocks[iid]
        kind = next((e.value for e in p.events[a:b] if e.id == flp.CH_TYPE), None)
        if kind not in (SAMPLER, AUDIO_CLIP, PLUGIN):
            raise TransferError(f"channel #{iid} is a {flp.CH_TYPES.get(kind, kind)} channel, which cannot be exported")
        block = [Event(e.id, e.data) for e in p.events[a:b]]
        if samples and samples.get(iid):
            for e in block:
                if e.id == flp.SAMPLE_PATH:
                    e.data = (str(samples[iid]) + "\0").encode("utf-16-le" if p.unicode else "latin-1", "replace")
        events += block
    place = {iid: first_channel + k for k, iid in enumerate(used)}
    scale = (ppq or p.ppq) / p.ppq
    for k, num in enumerate(patterns):
        number = struct.pack("<H", first_pattern + k)
        a, b = lay.notes[num]
        raw = bytearray()
        for e in p.events[a:b]:
            if e.id != flp.PAT_NOTES:
                continue
            for off in range(0, len(e.data), flp.NOTE_SIZE):
                note = bytearray(e.data[off:off + flp.NOTE_SIZE])
                pos, _flags, chan, length = struct.unpack_from("<IHHI", note, 0)
                struct.pack_into("<H", note, 6, place[chan])
                if scale != 1:
                    struct.pack_into("<I", note, 0, round(pos * scale))
                    struct.pack_into("<I", note, 8, round(length * scale))
                raw += note
        events += [Event(flp.PAT_NEW, number), Event(flp.PAT_NOTES, bytes(raw))]
        if num in lay.meta:                                  # name, colour
            a, b = lay.meta[num]
            events += [Event(e.id, number if e.id == flp.PAT_NEW else e.data) for e in p.events[a:b]]
    return Project(FORMAT_PRESET, 1, ppq or p.ppq, events).dump(), used


def write_pattern_preset(p: Project, project_path, patterns: list[int], first_channel: int,
                         first_pattern: int, ppq: int | None = None) -> tuple[Path, int]:
    """Write pattern_preset() to the scratch folder. Returns the file and how many channels it adds."""
    chans = {c["iid"]: c for c in p.channels()}
    used = sorted({c for num in patterns for c in p.patterns()[num]["channels"]})
    samples = {}
    for iid in used:
        stored = expand_fl_path(chans[iid]["sample"]) if iid in chans else ""
        if stored and not os.path.isfile(stored):
            samples[iid] = sample_file(p, project_path, iid)
    raw, used = pattern_preset(p, patterns, first_channel, first_pattern, ppq, samples)
    # FL names the first new channel after the file, so the file carries that channel's name
    folder = drag_dir(project_path) / ("pattern " + "+".join(str(n) for n in patterns))[:60]
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{safe_name(chans[used[0]]['name'], f'Channel {used[0]}')}.fst"
    out.write_bytes(raw)
    return out, len(used)


def safe_name(name: str, fallback: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:80] or fallback


def drag_dir(project_path) -> Path:
    """Scratch folder for files handed to FL by drag and drop."""
    stem = safe_name(Path(project_path).stem, "project")
    d = Path(tempfile.gettempdir()) / "FLTransfer" / stem
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_score(p: Project, project_path, pattern: int, channel: int) -> Path:
    pats, chans = p.patterns(), {c["iid"]: c for c in p.channels()}
    pname = safe_name(pats[pattern]["name"], f"Pattern {pattern}")
    cname = safe_name(chans[channel]["name"], f"Channel {channel}")
    out = drag_dir(project_path) / f"{pname} - {cname}.fsc"
    out.write_bytes(score(p, pattern, channel))
    return out


def write_preset(p: Project, project_path, iid: int) -> Path:
    chans = {c["iid"]: c for c in p.channels()}
    out = drag_dir(project_path) / f"{safe_name(chans[iid]['name'], f'Channel {iid}')}.fst"
    stored = expand_fl_path(chans[iid]["sample"])
    found = sample_file(p, project_path, iid) if stored and not os.path.isfile(stored) else None
    out.write_bytes(channel_preset(p, iid, found))
    return out


def sample_file(p: Project, project_path, iid: int) -> Path | None:
    """The audio file a sampler or audio clip channel plays, if it can be found on disk.

    Looks at the stored path first, then for a file of the same name in the project's own folder
    and its subfolders (projects moved from another computer usually keep their samples there).
    """
    chans = {c["iid"]: c for c in p.channels()}
    raw = chans[iid]["sample"]
    if not raw:
        return None
    path = expand_fl_path(raw)
    home = os.path.dirname(os.path.abspath(project_path))
    if not os.path.isabs(path) and not re.match(r"^[A-Za-z]:", path):
        path = os.path.join(home, path)
    if os.path.isfile(path):
        return Path(path)
    return _find_nearby(home, re.split(r"[\\/]", raw)[-1])


_nearby: dict[str, dict[str, str]] = {}


def _find_nearby(folder: str, name: str, limit: int = 20000) -> Path | None:
    index = _nearby.get(folder)
    if index is None:
        index = {}
        seen = 0
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d.lower() != "backup"]
            for f in files:
                index.setdefault(f.lower(), os.path.join(root, f))
            seen += len(files)
            if seen > limit:
                break
        _nearby[folder] = index
    hit = index.get(name.lower())
    return Path(hit) if hit and os.path.isfile(hit) else None


def expand_fl_path(path: str, factory: str | None = None, user: str | None = None) -> str:
    """Replace FL's %FLStudioFactoryData% / %FLStudioUserData% prefixes with real folders."""
    if not path.startswith("%"):
        return path
    if factory is None or user is None:
        f, u = _fl_dirs()
        factory, user = factory or f, user or u
    for var, real in (("%FLStudioFactoryData%", factory), ("%FLStudioUserData%", user)):
        if real and path.lower().startswith(var.lower()):
            return real.rstrip("\\/") + path[len(var):]
    return path


def fl_registry_paths(*names: str):
    """Yield Image-Line path values from per-user and machine-wide installations."""
    try:
        import winreg
    except ImportError:
        return
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Image-Line\Shared\Paths", 0, winreg.KEY_READ | view) as key:
                    for name in names:
                        try:
                            value, _ = winreg.QueryValueEx(key, name)
                        except OSError:
                            continue
                        if isinstance(value, str):
                            path = os.path.expandvars(value.strip().strip('"'))
                            if os.path.isabs(path):
                                yield name, os.path.normpath(path)
            except OSError:
                continue


def _fl_dirs() -> tuple[str | None, str | None]:
    install = user = None
    for name, path in fl_registry_paths("Install path", "FL Studio", "Shared data"):
        if name == "Shared data":
            user = user or os.path.join(path, "FL Studio")
        elif not install:
            if os.path.isdir(path):
                install = path
            elif name == "FL Studio" and os.path.isfile(path):
                install = os.path.dirname(path)
        if install and user:
            break
    return install, user
