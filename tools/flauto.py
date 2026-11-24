"""Dev tool: drive a local FL Studio for verification (Windows only).

  launch <file.flp>            start FL with a project, print the pid
  render <file.flp> <outdir>   FL command-line render to wav (blocks until FL exits)
  windows                      list visible top-level windows of FL processes
  shot <out.jpg> [scale] [x y w h]   screenshot (whole screen or a region)
  focus <hwnd>
  key <combo> [...]            e.g. ctrl+s  enter  alt+f4  f6
  type <text>
  click <x> <y> [right|double]
  drag <x1> <y1> <x2> <y2> [hold_ms]
  close <hwnd>                 post WM_CLOSE
  kill <pid>                   terminate one FL process started by this tool
  state                        foreground window and seconds since the user's last input
  pshot <hwnd> <out.jpg> [scale] [x y w h]   capture one window even when it is covered (PrintWindow)
  pkey <hwnd> <combo> [...]    post keys to a window without giving it focus
  children <hwnd>              visible child windows with class, rect and whether they accept dropped files
  pdrop <hwnd> <x> <y> <file>...   post a file drop (WM_DROPFILES) at client coordinates, no mouse
  cliptext save|load <file>    keep / put back the text on the clipboard
  clipput <format name> <file> put a file's bytes on the clipboard under a registered format
  clip [dumpdir]               list clipboard formats (and dump the non-standard ones)
  pclick <hwnd> <x> <y>        post a left click at client coordinates (no real mouse)
  savedlg <pid> <file>         fill the file name of an open Save dialog and confirm it (messages only)
  wait <text> [seconds]        wait until an FL window title contains text; prints the window
  verify <file.flp> <shot.jpg> [settle_s] [keys...]   open in FL, screenshot, Ctrl+S, close; all in the background

Commands that inject keyboard or mouse input refuse to run while another
program (for example a game) is in the foreground and the user was active in
the last few seconds.  Pass --even-if-busy as the first argument to override.
"""
import ctypes, os, subprocess, sys, time
from ctypes import wintypes

FL = r"D:\Fl studio\FL64.exe"
u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
u32.SetProcessDPIAware()

VK = {"enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "ctrl": 0x11, "shift": 0x10, "alt": 0x12,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "del": 0x2E, "home": 0x24, "end": 0x23,
      "backspace": 0x08, "win": 0x5B}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
VK.update({"add": 0x6B, "subtract": 0x6D, **{f"num{i}": 0x60 + i for i in range(10)}})


class KI(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class MI(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class U(ctypes.Union):
    _fields_ = [("ki", KI), ("mi", MI)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", U)]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    u32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _key(vk, up=False, scan=0, flags=0):
    return INPUT(1, U(ki=KI(vk, scan, flags | (2 if up else 0), 0, None)))


def press(combo):
    parts = combo.lower().split("+")
    codes = [VK[p] if p in VK else ord(p.upper()) for p in parts]
    for c in codes:
        _send(_key(c)); time.sleep(0.03)
    for c in reversed(codes):
        _send(_key(c, up=True)); time.sleep(0.03)


def type_text(text):
    for ch in text:
        _send(_key(0, scan=ord(ch), flags=4), _key(0, up=True, scan=ord(ch), flags=4))
        time.sleep(0.01)


def _mouse(flags):
    _send(INPUT(0, U(mi=MI(0, 0, 0, flags, 0, None))))


def move(x, y):
    u32.SetCursorPos(int(x), int(y)); time.sleep(0.03)


def click(x, y, kind="left"):
    move(x, y)
    down, up = (0x0008, 0x0010) if kind == "right" else (0x0002, 0x0004)
    for _ in range(2 if kind == "double" else 1):
        _mouse(down); time.sleep(0.04); _mouse(up); time.sleep(0.08)


def drag(x1, y1, x2, y2, hold_ms=400):
    move(x1, y1); _mouse(0x0002); time.sleep(0.25)
    steps = 30
    for i in range(1, steps + 1):
        move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
        _mouse(0x0001)  # relative zero move so the app sees mouse motion
        time.sleep(0.02)
    time.sleep(hold_ms / 1000)
    _mouse(0x0004)


def proc_name(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(520); n = wintypes.DWORD(520)
    ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)); k32.CloseHandle(h)
    return os.path.basename(buf.value) if ok else ""


def windows(all_procs=False):
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD(); u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = proc_name(pid.value)
        if not all_procs and not name.lower().startswith(("fl64", "fl.exe", "ilbridge")):
            return True
        t = ctypes.create_unicode_buffer(300); u32.GetWindowTextW(hwnd, t, 300)
        c = ctypes.create_unicode_buffer(120); u32.GetClassNameW(hwnd, c, 120)
        r = wintypes.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append((hwnd, pid.value, name, c.value, t.value, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    u32.EnumWindows(cb, 0)
    return out


def children(parent):
    """Visible child windows: (hwnd, class, title, rect, accepts WM_DROPFILES)."""
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if u32.IsWindowVisible(hwnd):
            t = ctypes.create_unicode_buffer(200); u32.GetWindowTextW(hwnd, t, 200)
            c = ctypes.create_unicode_buffer(120); u32.GetClassNameW(hwnd, c, 120)
            r = wintypes.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
            ex = u32.GetWindowLongW(hwnd, -20)
            out.append((hwnd, c.value, t.value, (r.left, r.top, r.right - r.left, r.bottom - r.top), bool(ex & 0x10)))
        return True

    u32.EnumChildWindows(parent, cb, 0)
    return out


def pdrop(hwnd, x, y, files):
    """Post WM_DROPFILES to a window of another process (no mouse involved).

    The DROPFILES block has to live in the target process, so it is written
    there with VirtualAllocEx/WriteProcessMemory.  x, y are client coordinates.
    """
    pid = wintypes.DWORD(); u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    names = "".join(os.path.abspath(f) + "\0" for f in files) + "\0"
    data = struct_pack_dropfiles(x, y) + names.encode("utf-16-le")
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.VirtualAllocEx.restype = ctypes.c_void_p
    k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_void_p]
    h = k32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid.value)
    addr = k32.VirtualAllocEx(h, None, len(data), 0x3000, 0x04)
    ok = k32.WriteProcessMemory(h, addr, data, len(data), None)
    k32.CloseHandle(h)
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
    posted = u32.PostMessageW(hwnd, 0x0233, addr, 0)
    print("pdrop", "written" if ok else "WRITE FAILED", "posted" if posted else "POST FAILED", hex(addr or 0))


def savedlg(pid, path):
    """Answer a standard Save dialog owned by the given process without touching focus."""
    path = os.path.abspath(path)
    dlg = None
    for _ in range(30):
        hits = [w for w in windows() if w[1] == pid and w[3] == "#32770"]
        if hits:
            dlg = hits[0]; break
        time.sleep(0.3)
    if dlg is None:
        print("savedlg: no dialog; windows:", [(w[3], w[4]) for w in windows() if w[1] == pid]); return
    edits = [c for c in children_all(dlg[0]) if c[1] == "Edit"]
    if not edits:
        print("savedlg: dialog has no edit box", dlg); return
    u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_void_p, ctypes.c_wchar_p]
    u32.SendMessageW(edits[0][0], 0x000C, 0, path)                # WM_SETTEXT
    time.sleep(0.3)
    u32.PostMessageW(dlg[0], 0x0111, 1, 0)                        # WM_COMMAND IDOK
    for _ in range(40):
        time.sleep(0.25)
        if os.path.isfile(path):
            print("savedlg: saved", path, "dialog title", repr(dlg[4])); return
    print("savedlg: file did not appear; windows:", [(w[3], w[4]) for w in windows() if w[1] == pid])


def children_all(parent):
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        c = ctypes.create_unicode_buffer(120); u32.GetClassNameW(hwnd, c, 120)
        out.append((hwnd, c.value))
        return True

    u32.EnumChildWindows(parent, cb, 0)
    return out


def struct_pack_dropfiles(x, y):
    import struct
    return struct.pack("<IiiII", 20, int(x), int(y), 0, 1)


def focus(hwnd):
    u32.ShowWindow(hwnd, 9)
    _send(_key(0x12), _key(0x12, up=True))   # a tap on Alt lets SetForegroundWindow succeed
    u32.SetForegroundWindow(hwnd); time.sleep(0.3)


def shot(path, scale=0.5, box=None):
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=(box[0], box[1], box[0] + box[2], box[1] + box[3]) if box else None)
    if scale != 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.convert("RGB").save(path, quality=80)
    print("saved", path, img.size)


def pshot(hwnd, path, scale=0.5, box=None):
    """Capture a window through PrintWindow, so it works while another program covers it."""
    from PIL import Image
    gdi = ctypes.windll.gdi32
    r = wintypes.RECT(); u32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u32.GetWindowDC(hwnd); mem = gdi.CreateCompatibleDC(hdc); bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mem, bmp)
    ok = u32.PrintWindow(hwnd, mem, 2)      # PW_RENDERFULLCONTENT

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("a", wintypes.LONG), ("b", wintypes.LONG),
                    ("c", wintypes.DWORD), ("d", wintypes.DWORD)]
    bmi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi.DeleteObject(bmp); gdi.DeleteDC(mem); u32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    if box:
        img = img.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    if scale != 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path, quality=82)
    print("saved", path, img.size, "printwindow_ok", ok)


def pkey(hwnd, combo):
    """Post a key combination to a window that is not in the foreground.

    Modifier state is read by the target through GetKeyState, so the modifier
    bits are set in the target thread's keyboard state for the duration.
    """
    parts = combo.lower().split("+")
    mods = [VK[p] for p in parts[:-1]]
    key = VK[parts[-1]] if parts[-1] in VK else ord(parts[-1].upper())
    tid = u32.GetWindowThreadProcessId(hwnd, None); me = k32.GetCurrentThreadId()
    u32.AttachThreadInput(me, tid, True)
    state = (ctypes.c_ubyte * 256)(); u32.GetKeyboardState(state)
    saved = bytes(state)
    for m in mods:
        state[m] |= 0x80
    u32.SetKeyboardState(state)
    scan = u32.MapVirtualKeyW(key, 0)
    alt = 0x12 in mods
    down, up = (0x0104, 0x0105) if alt else (0x0100, 0x0101)
    u32.PostMessageW(hwnd, down, key, 1 | (scan << 16) | ((1 << 29) if alt else 0))
    time.sleep(0.15)
    u32.PostMessageW(hwnd, up, key, 1 | (scan << 16) | (3 << 30))
    time.sleep(0.15)
    u32.SetKeyboardState((ctypes.c_ubyte * 256)(*saved))
    u32.AttachThreadInput(me, tid, False)


def verify(path, shot_path, settle=6.0, keys=()):
    """Let FL open a project, capture it, re-save it in place and quit, without taking focus.

    Prints RESULT lines.  The re-saved file is FL's own reading of the project,
    which the caller compares with what it expected.
    """
    path = os.path.abspath(path)
    name = os.path.basename(path)
    before = os.path.getmtime(path)
    si = subprocess.STARTUPINFO()
    proc = subprocess.Popen([FL, path], creationflags=0x00000008 | 0x00000200, close_fds=True, startupinfo=si)
    main = None
    limit = time.time() + 90
    while time.time() < limit and main is None:
        for w in windows():
            if w[1] == proc.pid and w[3] == "TFruityLoopsMainForm" and name.lower() in w[4].lower():
                main = w
        time.sleep(0.5)
    mine = [w for w in windows() if w[1] == proc.pid]
    if main is None:
        print("RESULT load FAILED: no main window titled", name)
        for w in mine:
            print("  window", w[3], repr(w[4]), w[5])
        if mine:
            pshot(mine[0][0], shot_path, 0.5)
        subprocess.call(["taskkill", "/PID", str(proc.pid), "/F"], stdout=subprocess.DEVNULL)
        return
    time.sleep(settle)
    extra = [w for w in windows() if w[1] == proc.pid and w[3] not in ("TFruityLoopsMainForm", "TApplication")]
    for w in extra:
        print("RESULT extra window", w[3], repr(w[4]), w[5])
    for combo in keys:                      # e.g. f6 to show the channel rack
        pkey(main[0], combo); time.sleep(0.8)
    pshot(main[0], shot_path, 0.5)
    pkey(main[0], "ctrl+s")
    saved = False
    for _ in range(20):
        time.sleep(0.5)
        if os.path.getmtime(path) != before:
            saved = True
            break
    print("RESULT resaved", saved)
    time.sleep(1.0)
    u32.PostMessageW(main[0], 0x0010, 0, 0)
    try:
        proc.wait(timeout=25)
        print("RESULT closed cleanly")
    except subprocess.TimeoutExpired:
        left = [w for w in windows() if w[1] == proc.pid]
        print("RESULT still running after close; windows:", [(w[3], w[4]) for w in left])
        if left:
            pshot(left[0][0], shot_path.replace(".jpg", "_close.jpg"), 0.5)
        subprocess.call(["taskkill", "/PID", str(proc.pid), "/F"], stdout=subprocess.DEVNULL)


class LASTINPUT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def state():
    li = LASTINPUT(ctypes.sizeof(LASTINPUT), 0); u32.GetLastInputInfo(ctypes.byref(li))
    idle = (k32.GetTickCount() - li.dwTime) / 1000
    hwnd = u32.GetForegroundWindow()
    pid = wintypes.DWORD(); u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    t = ctypes.create_unicode_buffer(300); u32.GetWindowTextW(hwnd, t, 300)
    return {"idle_s": round(idle, 1), "fg_hwnd": hwnd, "fg_proc": proc_name(pid.value), "fg_title": t.value}


OURS = ("fl64", "python", "pythonw", "explorer", "code", "shuncode")
INJECTING = {"key", "type", "click", "drag", "focus"}
OVERRIDE = False


def guard():
    """Do not fight the user for the mouse and keyboard."""
    if OVERRIDE:
        return
    st = state()
    if not st["fg_proc"].lower().startswith(OURS) and st["idle_s"] < 20:
        sys.exit(f"BUSY: user is active in {st['fg_proc']} ({st['fg_title']!r}); not injecting input")


def main(a):
    cmd = a[0]
    if cmd in INJECTING:
        guard()
    if cmd == "state":
        print(state()); return
    if cmd == "launch":
        args = [FL] + [x if x.startswith("--") else os.path.abspath(x) for x in a[1:]]
        si = subprocess.STARTUPINFO()
        if "--background" in a:
            args = [x for x in args if x != "--background"]
            si.dwFlags |= 1; si.wShowWindow = 7     # SW_SHOWMINNOACTIVE: do not steal focus
        p = subprocess.Popen(args, creationflags=0x00000008 | 0x00000200, close_fds=True, startupinfo=si)
        print("pid", p.pid)
    elif cmd == "render":
        out = os.path.abspath(a[2]); os.makedirs(out, exist_ok=True)
        t0 = time.time()
        si = subprocess.STARTUPINFO(); si.dwFlags |= 1; si.wShowWindow = 7
        rc = subprocess.call([FL, "/R", "/Ewav", "/O" + out, os.path.abspath(a[1])], timeout=300, startupinfo=si)
        print("render rc", rc, "seconds", round(time.time() - t0, 1), os.listdir(out))
    elif cmd == "windows":
        for w in windows("all" in a[1:]):
            print(w, "acceptfiles" if u32.GetWindowLongW(w[0], -20) & 0x10 else "")
    elif cmd == "children":
        for c in children(int(a[1])):
            print(c)
    elif cmd == "cliptext":
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.GetClipboardData.restype = ctypes.c_void_p
        k.GlobalLock.restype = ctypes.c_void_p; k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k.GlobalAlloc.restype = ctypes.c_void_p; k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        if not u.OpenClipboard(0):
            print("cliptext: cannot open"); return
        try:
            if a[1] == "save":
                h = u.GetClipboardData(13)
                text = ""
                if h:
                    ptr = k.GlobalLock(h); text = ctypes.wstring_at(ptr); k.GlobalUnlock(h)
                open(a[2], "w", encoding="utf-8").write(text)
                print("cliptext saved", len(text), "chars")
            else:
                text = open(a[2], encoding="utf-8").read()
                raw = (text + "\0").encode("utf-16-le")
                u.EmptyClipboard()
                h = k.GlobalAlloc(0x0002, len(raw)); ptr = k.GlobalLock(h)
                ctypes.memmove(ptr, raw, len(raw)); k.GlobalUnlock(h)
                u.SetClipboardData(13, h)
                print("cliptext restored", len(text), "chars")
        finally:
            u.CloseClipboard()
    elif cmd == "clipput":
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        k.GlobalLock.restype = ctypes.c_void_p; k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k.GlobalAlloc.restype = ctypes.c_void_p; k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        fmt = u.RegisterClipboardFormatW(a[1])
        raw = open(a[2], "rb").read()
        if not u.OpenClipboard(0):
            print("clipput: cannot open"); return
        try:
            u.EmptyClipboard()
            h = k.GlobalAlloc(0x0002, len(raw)); ptr = k.GlobalLock(h)
            ctypes.memmove(ptr, raw, len(raw)); k.GlobalUnlock(h)
            u.SetClipboardData(fmt, h)
            print("clipput", a[1], fmt, len(raw), "bytes")
        finally:
            u.CloseClipboard()
    elif cmd == "clip":
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        u.GetClipboardData.restype = ctypes.c_void_p
        k.GlobalLock.restype = ctypes.c_void_p; k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalSize.restype = ctypes.c_size_t; k.GlobalSize.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        if not u.OpenClipboard(0):
            print("clip: cannot open"); return
        try:
            fmt = 0
            while True:
                fmt = u.EnumClipboardFormats(fmt)
                if not fmt:
                    break
                nm = ctypes.create_unicode_buffer(200); u.GetClipboardFormatNameW(fmt, nm, 200)
                size = -1; head = b""
                if fmt >= 0xC000 or fmt in (1, 13):
                    h = u.GetClipboardData(fmt)
                    if h:
                        size = k.GlobalSize(h); ptr = k.GlobalLock(h)
                        if ptr:
                            data = ctypes.string_at(ptr, size); k.GlobalUnlock(h); head = data[:48]
                            if len(a) > 1 and fmt >= 0xC000:
                                open(os.path.join(a[1], "clip_%d.bin" % fmt), "wb").write(data)
                print("format", fmt, repr(nm.value), "size", size, head.hex())
        finally:
            u.CloseClipboard()
    elif cmd == "pclick":
        lp = (int(a[3]) << 16) | (int(a[2]) & 0xFFFF)
        for msg, wp in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
            ctypes.windll.user32.PostMessageW(int(a[1]), msg, wp, lp); time.sleep(0.12)
    elif cmd == "savedlg":
        savedlg(int(a[1]), a[2])
    elif cmd == "pdrop":
        pdrop(int(a[1]), int(a[2]), int(a[3]), a[4:])
    elif cmd == "shot":
        scale = float(a[2]) if len(a) > 2 else 0.5
        box = tuple(int(v) for v in a[3:7]) if len(a) >= 7 else None
        shot(a[1], scale, box)
    elif cmd == "focus":
        focus(int(a[1]))
    elif cmd == "key":
        for combo in a[1:]:
            press(combo); time.sleep(0.15)
    elif cmd == "type":
        type_text(a[1])
    elif cmd == "click":
        click(int(a[1]), int(a[2]), a[3] if len(a) > 3 else "left")
    elif cmd == "drag":
        drag(*[int(v) for v in a[1:5]], hold_ms=int(a[5]) if len(a) > 5 else 400)
    elif cmd == "close":
        u32.PostMessageW(int(a[1]), 0x0010, 0, 0)
    elif cmd == "kill":
        if not proc_name(int(a[1])).lower().startswith("fl64"):
            sys.exit("refusing: that pid is not FL64.exe")
        subprocess.call(["taskkill", "/PID", a[1], "/F"])
    elif cmd == "pshot":
        scale = float(a[3]) if len(a) > 3 else 0.5
        box = tuple(int(v) for v in a[4:8]) if len(a) >= 8 else None
        pshot(int(a[1]), a[2], scale, box)
    elif cmd == "pkey":
        for combo in a[2:]:
            pkey(int(a[1]), combo); time.sleep(0.2)
    elif cmd == "wait":
        limit = time.time() + (float(a[2]) if len(a) > 2 else 60)
        while time.time() < limit:
            hit = [w for w in windows() if a[1].lower() in w[4].lower()]
            if hit:
                print("found", hit[0]); break
            time.sleep(0.5)
        else:
            print("TIMEOUT waiting for", a[1]); [print(" ", w) for w in windows()]
    elif cmd == "verify":
        verify(a[1], a[2], float(a[3]) if len(a) > 3 else 6, a[4:])
    elif cmd == "sleep":
        time.sleep(float(a[1]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # several commands can be chained with "--"
    args, cur = [], []
    argv = sys.argv[1:]
    if argv and argv[0] == "--even-if-busy":
        OVERRIDE = True; argv = argv[1:]
    for tok in argv:
        if tok == "--":
            args.append(cur); cur = []
        else:
            cur.append(tok)
    args.append(cur)
    for part in args:
        if part:
            main(part)
