"""Hand a generated file to the FL Studio that is already running.

Starting FL64.exe with a file path while FL is open does not start a second copy: the running
instance takes the file.  Checked with FL 25.2.5:
  .fst  -> a new channel appears in the open project, named after the file
  .fst  -> a preset that also holds patterns adds those patterns too; their notes name the
           channel by its position in the open project, so the tool has to know how many
           channels that project has (see Target)
  .fsc  -> the notes go into the current pattern on a new channel called "ScoreTrack"
  .flp  -> replaces the open project (never sent from here)
  .wav  -> nothing happens (so samples are sent as a channel preset instead)
No mouse, keyboard or window focus is involved.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .export import fl_registry_paths

SENDABLE = (".fst", ".fsc")
EXE_NAMES = ("FL64.exe", "FL.exe")
FL_NOT_RUNNING = "FL Studio is not running. Open the target project in FL first, then send."


class LiveError(Exception):
    pass


def _process_image(pid: int) -> str | None:
    """Read the executable path for a window's process, including paths on other drives."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32")
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, wintypes.LPDWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        path = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(path))
        if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
            return path.value
        return None
    finally:
        kernel.CloseHandle(handle)


def is_fl_exe(path) -> bool:
    return (isinstance(path, str) and os.path.isabs(path)
            and os.path.basename(path).casefold() in ("fl64.exe", "fl.exe") and os.path.isfile(path))


def _exe_in(folder: str) -> str | None:
    for name in EXE_NAMES:
        exe = os.path.join(folder, name)
        if os.path.isfile(exe):
            return exe
    return None


def find_fl(pid: int | None = None) -> str | None:
    """Prefer the FL instance with the target project over any other installed version."""
    if not pid:
        windows = fl_windows()
        pid = windows[0][1] if windows else None
    if pid:
        running = _process_image(pid)
        if is_fl_exe(running):
            return running
    remembered = setting("fl_exe")
    if is_fl_exe(remembered):
        return remembered
    for _, path in fl_registry_paths("FL Studio", "Install path"):
        if is_fl_exe(path):
            return path
        exe = _exe_in(path)
        if exe:
            return exe
    for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if not base or not os.path.isabs(base):
            continue
        folder = os.path.join(base, "Image-Line")
        try:
            names = sorted(os.listdir(folder), reverse=True)
        except OSError:
            continue
        for name in names:
            exe = _exe_in(os.path.join(folder, name))
            if exe:
                return exe
    return None


_helpers: list = []         # the short-lived FL64 processes that carried a file to the running FL


def reap() -> None:
    """Let go of helper processes that have finished, so they do not linger as dead FL64 entries."""
    _helpers[:] = [p for p in _helpers if p.poll() is None]


def fl_running() -> int:
    """How many FL Studio processes are running (0 when unknown or not on Windows)."""
    if sys.platform != "win32":
        return 0
    reap()
    try:
        shown = len({w[1] for w in fl_windows()})        # a helper or a finished process has no main window
        if shown:
            return shown
    except OSError:
        pass
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             errors="replace", creationflags=0x08000000).stdout
    except OSError:
        return 0
    names = {n.lower() for n in EXE_NAMES}
    return sum(1 for line in out.splitlines() if line.split(",")[0].strip('"').lower() in names)


def send(path, exe: str | None = None, require_running: bool = True) -> str:
    """Pass one .fst / .fsc file to the running FL Studio. Returns the executable used."""
    path = Path(path)
    if path.suffix.lower() not in SENDABLE:
        raise LiveError(f"Only .fst or .fsc files can be sent, not {path.suffix or path.name}")
    if not path.is_file():
        raise LiveError(f"File not found: {path}")
    if require_running and fl_running() == 0:
        raise LiveError(FL_NOT_RUNNING)
    exe = exe or find_fl()
    if not exe:
        raise LiveError("Could not find the FL Studio program. Click “Send to FL” in the FL Transfer window and pick FL64.exe or FL.exe when asked.")
    reap()
    _helpers.append(subprocess.Popen([exe, str(path)], close_fds=True))
    return exe


# ---- the project open in FL -------------------------------------------------------------------
MAIN_CLASS = "TFruityLoopsMainForm"


def fl_windows() -> list[tuple[int, int, str]]:
    """(window, process id, title) of every FL Studio main window."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found: list[tuple[int, int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value == MAIN_CLASS and u.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, title, 512)
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((hwnd, pid.value, title.value))
        return True

    u.EnumWindows(each, 0)
    return found


def pointer_over_fl() -> bool:
    """True when the mouse pointer is over a window of FL Studio."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    u.WindowFromPoint.argtypes = [wintypes.POINT]
    u.WindowFromPoint.restype = wintypes.HWND
    hwnd = u.WindowFromPoint(pt)
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value in {w[1] for w in fl_windows()}


def title_project(title: str) -> str | None:
    """'song.flp - FL Studio 2025' -> 'song.flp'; None for a project that was never saved."""
    m = re.match(r"^(.+\.flp)\s+-\s+FL Studio", title, re.I)
    return m.group(1) if m else None


def _memo_path() -> Path:
    return Path(tempfile.gettempdir()) / "FLTransfer" / "targets.json"


def _memo() -> dict:
    try:
        data = json.loads(_memo_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _memo_save(data: dict) -> None:
    try:
        _memo_path().parent.mkdir(parents=True, exist_ok=True)
        _memo_path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _settings_path() -> Path:
    """Choices the user made (kept apart from the scratch folder, which cleaners may empty)."""
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path(tempfile.gettempdir())) / "FLTransfer" / "settings.json"


def _settings() -> dict:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def setting(name: str, default=None):
    data = _settings()
    if name in data:
        return data[name]
    return _memo().get("settings", {}).get(name, default)      # where versions before 1.0 kept them


def set_setting(name: str, value) -> None:
    data = _settings()
    data[name] = value
    try:
        _settings_path().parent.mkdir(parents=True, exist_ok=True)
        _settings_path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def post_key(hwnd: int, key: int, ctrl: bool = False) -> None:
    """Post one key press to a window. Works while that window is in the background or minimised."""
    import ctypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    tid, me = u.GetWindowThreadProcessId(hwnd, None), k.GetCurrentThreadId()
    u.AttachThreadInput(me, tid, True)       # the target reads the Ctrl state from its own input queue
    try:
        state = (ctypes.c_ubyte * 256)()
        u.GetKeyboardState(state)
        saved = bytes(state)
        if ctrl:
            state[0x11] |= 0x80
        else:
            for mod in (0x10, 0x11, 0x12):   # a modifier the user happens to hold must not join in
                state[mod] &= 0x7F
        u.SetKeyboardState(state)
        scan = u.MapVirtualKeyW(key, 0)
        u.PostMessageW(hwnd, 0x0100, key, 1 | (scan << 16))
        time.sleep(0.15)
        u.PostMessageW(hwnd, 0x0101, key, 1 | (scan << 16) | (3 << 30))
        time.sleep(0.15)
        u.SetKeyboardState((ctypes.c_ubyte * 256)(*saved))
    finally:
        u.AttachThreadInput(me, tid, False)


def press_save(hwnd: int) -> None:
    """Post Ctrl+S to FL's main window."""
    post_key(hwnd, ord("S"), ctrl=True)


def pending() -> bool:
    """True while FL is still taking a file that was sent (see wait_delivered)."""
    return any(proc.poll() is None for proc in _helpers)


def wait_delivered(timeout: float = 90.0) -> bool:
    """Wait until FL has taken every file sent so far.

    The helper process that carries a file stays alive until the running FL has finished
    loading it (plugins included), so its exit marks the moment the new channels exist.
    """
    limit = time.time() + timeout
    for proc in list(_helpers):
        try:
            proc.wait(max(0.1, limit - time.time()))
        except subprocess.TimeoutExpired:
            return False
    reap()
    return True


def show_pattern(number: int) -> bool:
    """Make the running FL display a pattern, with the keys a user would press on the numeric keypad.

    1 to 9 pick that pattern directly; above that, 9 followed by "next pattern" presses.
    """
    wins = fl_windows()
    if sys.platform != "win32" or not wins or number < 1 or number > 60:
        return False
    hwnd = wins[0][0]
    if fl_blocked(hwnd):
        return False
    post_key(hwnd, 0x60 + min(number, 9))
    for _ in range(number - 9):
        post_key(hwnd, 0x6B)
    return True


def fl_blocked(hwnd: int) -> bool:
    """True while FL's main window is disabled, which is how it looks with a message box or dialog up."""
    if sys.platform != "win32":
        return False
    import ctypes
    u = ctypes.windll.user32
    return bool(u.IsWindow(hwnd)) and not u.IsWindowEnabled(hwnd)


def save_in_fl(hwnd: int, path: str, timeout: float = 45.0, notice=None, patience: float = 180.0) -> None:
    """Have FL save the open project and wait until the file on disk is the new one.

    If FL answers the save with a message box (it does when the project holds notes that point
    at channels which do not exist), notice(text) is called once and the wait goes on for up to
    patience seconds, so the save can finish after the user has dismissed the box.
    """
    from .flp import FLPError, Project
    try:
        before = os.path.getmtime(path)
    except OSError as exc:
        raise LiveError(f"Cannot read the target project {path}: {exc}")
    name = os.path.basename(path)
    if fl_blocked(hwnd):
        raise LiveError("FL has a dialog open, so it cannot be asked to save right now. Nothing was sent.\nDeal with the dialog in FL first, then send again.")
    press_save(hwnd)
    start = time.time()
    limit = start + timeout
    told = answered = False
    while time.time() < limit:
        time.sleep(0.3)
        try:
            if os.path.getmtime(path) == before:
                blocked = fl_blocked(hwnd)
                if blocked and not told and time.time() - start > 1.5:
                    told = True
                    limit = max(limit, start + patience)
                    if notice:
                        notice(f"FL showed a dialog while saving {name}; waiting for you to answer it in FL…\n"
                               "If it is “Invalid data” (invalid notes, deleted on save), those are notes that missed "
                               "their channels earlier: just click Yes.")
                elif told and not blocked and not answered:
                    answered = True                      # box gone: the save follows at once, or not at all
                    limit = min(limit, time.time() + 15)
                continue
            size = os.path.getsize(path)
            time.sleep(0.4)                              # FL may still be writing
            if os.path.getsize(path) != size:
                continue
            if not Project.load(path).sanity():
                return
        except (OSError, FLPError):
            continue
    if answered:
        raise LiveError(f"FL's dialog was closed, but {name} was not saved (did you click No?). Nothing was sent.")
    if told:
        raise LiveError(f"The dialog FL showed was never answered, so {name} was not saved. Nothing was sent.\nDeal with the dialog in FL, then send again.")
    raise LiveError(f"FL did not save {name} (it may have a dialog open, or be busy). Nothing was sent.\n"
                    "Press Ctrl+S in FL yourself, then send again.")


def remember_path(path) -> None:
    """Note where a project file lives, so its name in FL's title bar can be resolved later."""
    data = _memo()
    data.setdefault("paths", {})[os.path.basename(str(path)).lower()] = os.path.abspath(str(path))
    _memo_save(data)


class TargetUnknown(LiveError):
    """The project open in FL could not be read from disk; .name is its file name, if it has one."""

    def __init__(self, message: str, name: str | None = None):
        super().__init__(message)
        self.name = name


@dataclass
class Target:
    """The project open in FL, as far as it can be known from outside.

    FL cannot be asked how many channels the open project has.  The count is taken from the
    project file as last saved, plus whatever this tool has added since that save.
    """
    name: str
    path: str
    pid: int
    mtime: float
    ppq: int
    channels: int               # in the saved file
    last_pattern: int           # highest pattern number in the saved file
    added_channels: int = 0     # sent by this tool since that save
    added_patterns: int = 0
    unsure: bool = False        # something reached FL that this tool could not count (see mark_unsure)

    @property
    def next_channel(self) -> int:
        return self.channels + self.added_channels

    @property
    def next_pattern(self) -> int:
        return self.last_pattern + self.added_patterns + 1

    def sent(self, channels: int, patterns: int = 0) -> None:
        """Record what was just handed to FL, so the next file is numbered after it."""
        self.added_channels += channels
        self.added_patterns += patterns
        data = _memo()
        data.setdefault("sent", {})[self.path] = {"mtime": self.mtime, "pid": self.pid,
                                                  "channels": self.added_channels, "patterns": self.added_patterns}
        _memo_save(data)


def mark_unsure() -> None:
    """Note that the channel count kept for the running FL can no longer be trusted.

    Called when a card was dropped into FL as a file (FL decides whether that makes a new
    channel) or a channel was sent while the open project could not be identified.  The mark
    goes away when the project is saved, by the user or through find_target(save=True).
    """
    wins = fl_windows()
    if not wins:
        return
    mtime = None
    name = title_project(wins[0][2])
    path = _memo().get("paths", {}).get(name.lower()) if name else None
    if path:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            pass
    data = _memo()
    data["unsure"] = {"pid": wins[0][1], "mtime": mtime}
    _memo_save(data)


def find_target(path=None, known=(), save: bool = False, notice=None) -> Target:
    """Work out which project the running FL has open and read its saved file.

    path names the file directly.  Otherwise the name in FL's title bar is looked up among
    files remembered earlier, the given known paths and FL's recent projects.
    With save, FL is first told to save the project, so the file read here is exactly what FL
    has open and the channel count cannot be stale.
    """
    from . import flp
    from .flp import FLPError, Project
    wins = fl_windows()
    pid = wins[0][1] if wins else 0
    name = None
    if path is None:
        if not wins:
            raise LiveError(FL_NOT_RUNNING)
        name = title_project(wins[0][2])
        if name is None:
            raise TargetUnknown("The project open in FL has never been saved, so its channel count cannot be read.\n"
                                "Sending a Pattern with its instruments needs that number: press Ctrl+S in FL once, then send.")
        from .model import recent_projects
        remembered = _memo().get("paths", {}).get(name.lower())
        for cand in ([remembered] if remembered else []) + [str(k) for k in known] + recent_projects(60):
            if os.path.basename(cand).lower() == name.lower() and os.path.isfile(cand):
                path = cand
                break
        if path is None:
            raise TargetUnknown(f"FL has {name} open, but it is not known where that file is.", name)
    path = os.path.abspath(str(path))
    if save:
        if not wins or title_project(wins[0][2]) is None:
            raise LiveError("FL has no saved project open, so it cannot be asked to save.")
        save_in_fl(wins[0][0], path, notice=notice)
        data = _memo()
        if data.pop("unsure", None) is not None:
            _memo_save(data)
    try:
        project = Project.load(path)
        mtime = os.path.getmtime(path)
    except (OSError, FLPError) as exc:
        raise LiveError(f"Cannot read the target project {path}: {exc}")
    numbers = [e.value for e in project.events if e.id == flp.PAT_NEW]
    target = Target(name or os.path.basename(path), path, pid, mtime, project.ppq,
                    len(project.channels()), max(numbers, default=0))
    remember_path(path)
    mark = _memo().get("unsure")
    target.unsure = bool(mark and mark.get("pid") == pid and mark.get("mtime") in (None, mtime))
    old = _memo().get("sent", {}).get(path)
    if old and old.get("mtime") == mtime and old.get("pid") == pid:     # same save, same FL session
        target.added_channels, target.added_patterns = int(old.get("channels", 0)), int(old.get("patterns", 0))
    return target
