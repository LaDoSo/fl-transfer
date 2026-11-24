"""Dev check: build the window without showing it and list what it would draw.

  python tools/guismoke.py <project.flp>             list only
  python tools/guismoke.py <project.flp> --send c1 p4+p5   also send those cards to the running FL (a+b = together)
  python tools/guismoke.py <project.flp> --pose 12         show the window for 12 s with one card selected, one
                                                           held down, one just sent and the drag chip up (for a screenshot)
  add --save-first to have FL save the target project before a pattern is sent
  python tools/guismoke.py <project.flp> --carry p4        drag that card with synthetic events, let go over FL
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer import gui, live

# the one-time "may FL save first?" question is answered here instead of in a dialog
live.setting = lambda name, default=None: ("--save-first" in sys.argv) if name == "save_first" else default
from tkinter import messagebox                                   # no switch: the question is answered "cancel"
messagebox.askyesnocancel = lambda *a, **k: print("ASKED:", a[0] if a else k.get("title")) or None
live.set_setting = lambda name, value: None
root = gui.TkinterDnD.Tk() if gui.HAVE_DND else gui.tk.Tk()
root.withdraw()
app = gui.App(root, sys.argv[1])
deadline = time.time() + 8
while time.time() < deadline and any(v == "wait" for v in app.waves.values()):
    root.update(); time.sleep(0.05)
root.update()
print("HAVE_DND", gui.HAVE_DND, "| tk", root.tk.call("info", "patchlevel"), "| font", app.f_ui.actual("family"),
      "| fl", live.find_fl(), "| running", live.fl_running())
print("status:", app.status.get())
print("recent:", len(app.combo.cget("values")), "projects | cards drawn:", len(app.boxes))
for key, c in app.clips.items():
    wave = app.waves.get(c.sample)
    extra = ""
    if c.kind == "audio":
        extra = "wave %d cols %.2fs" % (len(wave.columns), wave.seconds) if isinstance(wave, gui.peaks.Wave) else \
            ("NO FILE " + c.sample_name if not c.sample else "no preview " + os.path.basename(c.sample))
    elif c.kind == "pattern":
        extra = "%d notes, %d ticks, %d channels" % (len(c.notes), c.length, len(c.channels))
    print(" ", key, c.kind, repr(c.name), c.color, "|", extra, "" if c.enabled else "| disabled")
if "--send" in sys.argv:
    for key in [k for k in sys.argv[sys.argv.index("--send") + 1:] if not k.startswith("--")]:
        app.select(key.split("+")); app.send(); root.update()
        print("send", key, "->", app.log_text)
if "--pose" in sys.argv:
    seconds = float(sys.argv[sys.argv.index("--pose") + 1])
    root.deiconify(); root.geometry("+60+60"); root.update(); time.sleep(0.4); root.update()
    keys = [k for k, c in app.clips.items() if c.enabled]
    app.select(keys[:1])
    app.pressed = keys[1] if len(keys) > 1 else None
    if len(keys) > 2:
        app.flash_cards(keys[2:3], gui.GOOD, "已发送 ✓", 60000)
    if len(keys) > 3:
        app.set_hover(keys[3])
    app.draw_marks()
    app.ghost_show(keys[:1])
    app.ghost.geometry("+%d+%d" % (root.winfo_rootx() + 420, root.winfo_rooty() + 330))
    app.ghost_tone(True)
    app.say("已发送到 FL：1 个 Pattern + 1 个音源\n音符在新的 Pattern #5 里（在 FL 顶部的 Pattern 选择器里切过去看），音源从第 30 个通道开始。", "ok")
    root.update()
    print("ghost", app.ghost.winfo_id(), "root", root.winfo_id(), flush=True)
    end = time.time() + seconds
    while time.time() < end:
        root.update(); time.sleep(0.05)
if "--carry" in sys.argv:                      # the pattern drag, with FL "under the pointer" or not
    key = sys.argv[sys.argv.index("--carry") + 1]
    root.deiconify(); root.update(); time.sleep(0.5); root.update()     # events only reach a mapped window
    x0, y0, x1, y1 = app.boxes[key]
    cx, cy = int((x0 + x1) / 2), int((y0 + y1) / 2 - app.canvas.canvasy(0))
    seen, real_init = [], app.drag_init
    if gui.HAVE_DND:
        app.canvas.dnd_bind("<<DragInitCmd>>", lambda e: (seen.append(real_init(e)), seen[-1])[1])
    for over in ((False,) if "--elsewhere" in sys.argv else (False, True)):     # --elsewhere: never sends
        live.pointer_over_fl = lambda over=over: over
        seen.clear()
        time.sleep(0.8)
        ev = lambda name, dx: (app.canvas.event_generate(name, x=cx + dx, y=cy, rootx=cx + dx, rooty=cy,
                                                         time=int(time.time() * 1000) & 0x7fffffff), root.update())
        ev("<ButtonPress-1>", 0)
        for dx in (4, 12, 30, 60):
            ev("<B1-Motion>", dx)
        print("carrying", app.carry, "| cursor", app.canvas.cget("cursor"), "| DragInitCmd answered", seen,
              "| pressed", app.pressed, "| chip", app.ghost is not None and app.ghost.winfo_geometry(),
              "| chip says", app.ghost is not None and app.ghost.hint_label.cget("text"))
        ev("<ButtonRelease-1>", 60)
        print("let go over FL" if over else "let go elsewhere", "->", app.log_text)
        print("after: pressed", app.pressed, "| chip", app.ghost, "| cursor", repr(app.canvas.cget("cursor")))
root.destroy()
