"""Dev check for the sample audition.  Nothing here makes a sound: what goes to the device is silence.

  python tools/auditioncheck.py                    open the default output, play 0.6 s of silence, watch the position
  python tools/auditioncheck.py <project.flp>      also decode every sample of the project (decoded, not played) and
                                                   run the hidden window: click a play button, Space, stop
  python tools/auditioncheck.py --mci <file>       hand the file to Windows' own player at volume 0 for a moment
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer import audition
from fltransfer.audition import Pcm


def silence(seconds, rate=44100, channels=2, whole=None):
    n = int(seconds * rate)
    return Pcm(rate, channels, bytes(n * channels * 2), whole or n / rate, n / rate)


def device():
    for rate, channels in ((44100, 2), (48000, 1), (96000, 2), (22050, 1)):
        t0 = time.monotonic()
        out = audition._open_wave(silence(0.6, rate, channels))
        opened = time.monotonic() - t0
        seen = []
        while not out.done() and time.monotonic() - t0 < 3:
            seen.append(out.position())
            time.sleep(0.05)
        took = time.monotonic() - t0
        out.close()
        rising = all(b >= a for a, b in zip(seen, seen[1:]))
        print(f"waveOut {rate} Hz {channels} ch: opened in {opened * 1000:.0f} ms, done after {took:.2f} s, "
              f"positions {seen[0]:.2f} .. {max(seen):.2f} ({len(seen)} readings, rising {rising})")
    out = audition._open_wave(silence(5.0))
    time.sleep(0.2)
    t0 = time.monotonic()
    out.close()
    print(f"stop in the middle: close took {(time.monotonic() - t0) * 1000:.0f} ms")


def mci(path):
    out = audition._Mci(path, volume=0)
    seen = []
    for _ in range(3):
        time.sleep(0.1)
        seen.append(out.position())
    out.close()
    print(f"MCI {os.path.basename(path)} from the start: positions {seen}")
    out = audition._Mci(path, at=0.5, volume=0)
    seen = []
    for _ in range(8):
        time.sleep(0.1)
        seen.append(out.position())
    print(f"MCI {os.path.basename(path)} from the middle: length {out.seconds:.2f} s, positions {seen}, done {out.done()}")
    out.close()


def project(path):
    from fltransfer.model import Source
    clips = [c for c in Source(path).clips() if c.kind == "audio"]
    kinds = {}
    for c in clips:
        if not c.sample:
            kinds["file missing"] = kinds.get("file missing", 0) + 1
            continue
        t0 = time.monotonic()
        try:
            pcm = audition.decode(c.sample)
        except Exception as exc:  # noqa: BLE001
            pcm, what = None, f"ERROR {exc}"
        else:
            what = "decoded here" if pcm else "left to Windows (" + os.path.splitext(c.sample)[1].lower() + ")"
        kinds[what] = kinds.get(what, 0) + 1
        took = time.monotonic() - t0
        if pcm:
            loud = max((abs(int.from_bytes(pcm.data[i:i + 2], "little", signed=True))
                        for i in range(0, min(len(pcm.data), 400000), 2)), default=0)
            print(f"  {c.key:5} {pcm.rate} Hz {pcm.channels} ch {pcm.seconds:7.2f} s  peak {loud:5}  "
                  f"{took * 1000:4.0f} ms  {os.path.basename(c.sample)}")
        else:
            print(f"  {c.key:5} {what}  {os.path.basename(c.sample)}")
    print("samples:", len(clips), kinds)


def window(path):
    from fltransfer import gui, live
    live.set_setting = lambda name, value: None
    read = audition.decode

    def quiet(p, **kw):                             # the real file's length and format, with silence in it
        pcm = read(p, **kw)
        return pcm and Pcm(pcm.rate, pcm.channels, bytes(len(pcm.data)), pcm.seconds, pcm.played, pcm.start)

    root = gui.TkinterDnD.Tk() if gui.HAVE_DND else gui.tk.Tk()
    root.withdraw()
    app = gui.App(root, path)
    app.player = audition.Player(read=quiet, open_file=lambda p, at=0.0: (_ for _ in ()).throw(
        audition.AuditionError("not played in this check")))

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            root.update()
            time.sleep(0.01)

    pump(1.0)
    app.redraw()
    keys = [k for k, c in app.clips.items() if app.can_play(c) and audition.decode(c.sample)]
    print("window: cards", len(app.boxes), "| samples that can be heard", len(keys))
    if not keys:
        root.destroy()
        return
    key = keys[0]
    x0, y0 = app.boxes[key][:2]
    top = app.canvas.canvasy(0)
    slow = []
    t0 = time.monotonic()
    press = type("Press", (), {"x": int(x0 + 8), "y": int(y0 + 8 - top), "x_root": 0, "y_root": 0, "state": 0})
    app.click(press)                                # a withdrawn window gets no generated mouse events
    app.carry_drop(press)
    while app.player.state == "loading" and time.monotonic() - t0 < 5:
        t = time.monotonic()
        root.update()
        slow.append(time.monotonic() - t)
        time.sleep(0.005)
    started = time.monotonic() - t0
    a = app.player.fraction
    pump(0.4)
    b = app.player.fraction
    print(f"click on {key} ({app.clips[key].name}): state {app.player.state} after {started * 1000:.0f} ms, "
          f"longest stall of the window {max(slow, default=0) * 1000:.0f} ms, play head {a:.3f} -> {b:.3f}, "
          f"selected {app.selected}")
    print("status:", app.log_text.replace("\n", " / "))
    app.click(press)
    app.carry_drop(press)
    pump(0.1)
    print("click again:", app.player.state, app.playing)
    app.select([key])
    app.space()
    pump(0.3)
    print("space:", app.player.state, app.playing)
    app.space()
    pump(0.1)
    print("space again:", app.player.state, app.playing)
    app.stop_play()
    app.select([])
    x0, y0, x1, y1 = app.boxes[key]
    for part in (0.5, 0.2, 0.8):
        press = type("Press", (), {"x": int(x0 + (x1 - x0) * part), "y": int(y0 + 50 - top), "x_root": 0, "y_root": 0,
                                   "state": 0})
        t0 = time.monotonic()
        app.click(press)
        app.carry_drop(press)
        while app.player.state == "loading" and time.monotonic() - t0 < 5:
            root.update()
            time.sleep(0.005)
        took = time.monotonic() - t0
        a = app.player.fraction
        pump(0.3)
        print(f"click on the waveform at {part:.0%}: {app.player.state} after {took * 1000:.0f} ms, play head {a:.3f} -> "
              f"{app.player.fraction:.3f}, selected {app.selected}")
    app.double_click(press)
    app.carry_drop(press)
    pump(0.1)
    print("double click on the waveform:", app.player.state, f"{app.player.fraction:.3f}")
    app.stop_play()
    root.destroy()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--mci"]:
        mci(args[1])
    else:
        device()
        if args:
            project(args[0])
            window(args[0])
