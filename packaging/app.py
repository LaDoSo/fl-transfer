"""Entry point of the packaged FL Transfer (see packaging/build.py).

  "FL Transfer.exe" [project.flp]
  "FL Transfer.exe" --selftest <report.json> [project.flp]     check the package, write what was found, exit
"""
import json
import os
import sys
import traceback


def selftest(report: str, project: str | None) -> int:
    from fltransfer import __version__, gui, live
    info = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0],
            "drag_and_drop": gui.HAVE_DND, "icon": gui.resource("icon.ico"), "fl": live.find_fl(),
            "fl_windows": [w[2] for w in live.fl_windows()]}
    try:                                            # the sound output, with a moment of silence: nothing is heard
        import time
        from fltransfer import audition
        out = audition._open_wave(audition.Pcm(44100, 2, bytes(4 * 4410), 0.1, 0.1))
        end = time.monotonic() + 2
        while not out.done() and time.monotonic() < end:
            time.sleep(0.02)
        info["audition"] = "ok" if out.done() else "the output never finished"
        out.close()
    except Exception as exc:  # noqa: BLE001 - a machine without a sound card still gets the rest checked
        info["audition"] = f"failed: {exc}"
    try:
        root = gui.TkinterDnD.Tk() if gui.HAVE_DND else gui.tk.Tk()
        info["tk"] = root.tk.call("info", "patchlevel")
        if gui.HAVE_DND:
            info["tkdnd"] = root.tk.call("package", "require", "tkdnd")
        app = gui.App(root, project)
        root.update()
        info["project"] = project
        info["cards"] = len(app.clips)
        info["patterns"] = sum(1 for k in app.clips if k.startswith("p"))
        info["status"] = app.log_text
        root.destroy()
        info["ok"] = True
    except Exception:
        info["ok"] = False
        info["error"] = traceback.format_exc()
    with open(report, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return 0 if info["ok"] else 1


def main() -> int:
    args = sys.argv[1:]
    project = next((a for a in args if a.lower().endswith(".flp") and os.path.isfile(a)), None)
    if args[:1] == ["--selftest"] and len(args) > 1:
        return selftest(args[1], project)
    from fltransfer import gui
    try:
        return gui.main(project)
    except Exception:
        path = gui.log_error(traceback.format_exc())
        try:
            from tkinter import messagebox
            messagebox.showerror("FL Transfer", f"程序出错，已退出。\n详细记录在：\n{path}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
