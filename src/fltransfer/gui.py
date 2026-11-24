"""The window: one project's patterns and samples drawn like clips in FL's playlist.

Cards can be sent to the running FL Studio, dragged out, or merged into another project file.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import time
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import __version__, audition, live, peaks
from .flp import FLPError
from .model import PRESET, SAMPLE, Clip, Source, recent_projects

try:  # optional: real drag and drop out of the window
    _vendor = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "vendor")
    if os.path.isdir(_vendor) and _vendor not in sys.path:
        sys.path.insert(0, _vendor)
    from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
    HAVE_DND = True
except Exception:  # noqa: BLE001 - missing package or missing native library
    HAVE_DND = False

# ---- look -----------------------------------------------------------------------------------
BG = "#1b2227"            # playlist background
PANEL = "#242c32"         # top and bottom bars
FIELD = "#161c20"
LINE = "#313b42"
TEXT = "#d3dce1"
DIM = "#7f8c94"
ACCENT = "#f5a23c"        # FL's orange
BODY = "#0f181d"          # inside of a clip
GOOD = "#6fd28a"          # something arrived
BAD = "#ef6f6c"           # something did not
PATTERN_DEFAULT = (61, 71, 77)
AUDIO_DEFAULT = (93, 133, 81)
PLUGIN_DEFAULT = (89, 108, 143)

HEAD_H = 19
PLAY_W = 20                # the play button at the left end of a sample card's header
CARD_H = {"pattern": 84, "audio": 84, "plugin": 46, "other": 46}
CARD_MIN_W = 250
GAP = 10
PAD = 14
SECTIONS = (("pattern", "PATTERNS"), ("audio", "SAMPLES"), ("plugin", "INSTRUMENT CHANNELS"), ("other", "UNSUPPORTED CHANNELS"))
WAVE_COLUMNS = 420
MAX_NOTES_DRAWN = 1500


def rgb(color) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in color)


def hexrgb(text: str) -> tuple:
    return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t: float) -> tuple:
    """a moved towards b by t (0..1)."""
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def clip_color(clip: Clip) -> tuple:
    """The colour stored in the project, or FL's usual one when it is unset or too dark to read."""
    default = {"pattern": PATTERN_DEFAULT, "audio": AUDIO_DEFAULT}.get(clip.kind, PLUGIN_DEFAULT)
    c = clip.color
    if not c or len(c) < 3:
        return default
    if min(c) >= 250 or max(c) < 64:
        return default
    return tuple(c[:3])


def sample_matches(clip: Clip, query: str) -> bool:
    """Match a search against the sample file name or its channel label."""
    query = query.strip().casefold()
    if not query:
        return True
    names = (clip.name, clip.sample_name, os.path.basename(clip.sample))
    return any(query in name.casefold() for name in names if name)


def plural(n: int, word: str) -> str:
    """"1 instrument", "7 instruments"."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def seconds_text(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f} s"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


class FlatButton(tk.Label):
    """A flat, dark button; `primary` gives the orange one, `toggle` keeps an on/off state."""

    def __init__(self, parent, text, command, primary=False, toggle=None, font=None):
        super().__init__(parent, text=text, padx=14, pady=6, cursor="hand2", font=font, borderwidth=0)
        self.command, self.primary, self.toggle = command, primary, toggle
        self.hover = False
        self.down = False
        self.bind("<ButtonPress-1>", lambda e: self._set_down(True))
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        if toggle is not None:
            toggle.trace_add("write", lambda *a: self.paint())
        self.paint()

    def _set_hover(self, on: bool) -> None:
        self.hover = on
        self.paint()

    def _set_down(self, on: bool) -> None:
        self.down = on
        self.paint()

    def _click(self, event) -> None:
        self._set_down(False)
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            if self.toggle is not None:
                self.toggle.set(not self.toggle.get())
            self.command()

    def paint(self) -> None:
        if self.primary:
            bg, fg = (mix(hexrgb(ACCENT), (255, 255, 255), 0.18) if self.hover else hexrgb(ACCENT)), (27, 20, 8)
        elif self.toggle is not None and self.toggle.get():
            bg, fg = mix(hexrgb(PANEL), hexrgb(ACCENT), 0.22 if not self.hover else 0.3), hexrgb(ACCENT)
        else:
            bg, fg = (mix(hexrgb(LINE), (255, 255, 255), 0.08) if self.hover else hexrgb(LINE)), hexrgb(TEXT)
        if self.down:                                   # pressed: clearly darker until the button is let go
            bg = mix(bg, (0, 0, 0), 0.32)
        self.configure(background=rgb(bg), foreground=rgb(fg))


class App:
    def __init__(self, root: tk.Tk, initial: str | None = None):
        self.root = root
        self.source: Source | None = None
        self.project_clips: list[Clip] = []
        self.clips: dict[str, Clip] = {}             # cards included by the current search
        self.boxes: dict[str, tuple] = {}            # key -> x0, y0, x1, y1 on the canvas
        self.selected: list[str] = []
        self.press = None            # (root x, root y, card) of the last button press
        self.pressed: str | None = None   # card under a held mouse button
        self.flash: dict[str, tuple] = {}   # card -> (colour, label) shown briefly after sending
        self.ghost = None            # the chip that follows the pointer while a card is carried
        self.ghost_over = None
        self.carry: list[str] = []   # cards being dragged towards FL by this window itself
        self.hover: str | None = None
        self.waves: dict[str, object] = {}           # sample path -> peaks.Wave, None (unreadable) or "wait"
        self.wave_queue: queue.Queue = queue.Queue()
        self.redraw_job = None
        self.player = audition.Player()
        self.playing: str | None = None              # card whose sample is being auditioned
        self.play_hover: str | None = None           # card whose play button is under the pointer
        self.play_job = None
        self.play_told = False
        self.seek = None                             # (card, fraction) of a press on a waveform, played on release

        root.title(f"FL Transfer {__version__}")
        root.geometry("900x720")
        root.minsize(520, 440)
        root.configure(background=BG)
        self._fonts()
        self._style()

        # ---- top bar
        top = tk.Frame(root, background=PANEL)
        top.pack(fill="x")
        row = tk.Frame(top, background=PANEL)
        row.pack(fill="x", padx=PAD, pady=(12, 4))
        tk.Label(row, text="Source project", background=PANEL, foreground=DIM, font=self.f_ui, width=13,
                 anchor="w").pack(side="left")
        self.path_var = tk.StringVar()
        self.combo = ttk.Combobox(row, textvariable=self.path_var, values=recent_projects(), font=self.f_ui,
                                  style="Dark.TCombobox")
        self.combo.pack(side="left", fill="x", expand=True, padx=10, ipady=3)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.open(self.path_var.get()))
        self.combo.bind("<Return>", lambda e: self.open(self.path_var.get()))
        FlatButton(row, "Browse…", self.browse, font=self.f_ui).pack(side="left")
        FlatButton(row, "Refresh", self.reload, font=self.f_ui).pack(side="left", padx=(6, 0))
        search_row = tk.Frame(top, background=PANEL)
        search_row.pack(fill="x", padx=PAD, pady=(4, 6))
        tk.Label(search_row, text="Search samples", background=PANEL, foreground=DIM, font=self.f_ui, width=13,
                 anchor="w").pack(side="left")
        self.sample_search_var = tk.StringVar()
        self.sample_search_count = tk.StringVar(value="0 / 0")
        self.sample_search = ttk.Entry(search_row, textvariable=self.sample_search_var, font=self.f_ui, style="Dark.TEntry")
        self.sample_search.pack(side="left", fill="x", expand=True, padx=10, ipady=3)
        self.sample_search.bind("<Escape>", self.clear_sample_search)
        FlatButton(search_row, "Clear", self.clear_sample_search, font=self.f_ui).pack(side="left")
        tk.Label(search_row, textvariable=self.sample_search_count, background=PANEL, foreground=DIM,
                 font=self.f_small, width=10, anchor="e").pack(side="right", padx=(10, 0))
        self.sample_search_var.trace_add("write", self.search_changed)
        self.status = tk.StringVar(value="Pick a saved .flp project, or drop one onto this window")
        tk.Label(top, textvariable=self.status, background=PANEL, foreground=DIM, font=self.f_small,
                 anchor="w").pack(fill="x", padx=PAD, pady=(0, 10))
        tk.Frame(root, background="#11161a", height=1).pack(fill="x")

        # ---- bottom bar (packed before the canvas so the canvas takes what is left)
        bottom = tk.Frame(root, background=PANEL)
        bottom.pack(side="bottom", fill="x")
        tk.Frame(root, background="#11161a", height=1).pack(side="bottom", fill="x")
        bar = tk.Frame(bottom, background=PANEL)
        bar.pack(fill="x", padx=PAD, pady=(12, 6))
        FlatButton(bar, "Send to FL", self.send, primary=True, font=self.f_bold).pack(side="left")
        FlatButton(bar, "Merge into file…", self.merge, font=self.f_ui).pack(side="left", padx=(8, 0))
        FlatButton(bar, "Show file", self.reveal, font=self.f_ui).pack(side="left", padx=(8, 0))
        self.topmost = tk.BooleanVar(value=True)
        FlatButton(bar, "On top", self.apply_topmost, toggle=self.topmost, font=self.f_ui).pack(side="right")
        self.save_first = tk.BooleanVar(value=bool(live.setting("save_first", False)))
        FlatButton(bar, "Auto-save FL before Patterns", lambda: live.set_setting("save_first", bool(self.save_first.get())),
                   toggle=self.save_first, font=self.f_ui).pack(side="right", padx=(0, 8))
        self.raw_audio = tk.BooleanVar(value=False)
        FlatButton(bar, "Drag samples as audio", lambda: None, toggle=self.raw_audio,
                   font=self.f_ui).pack(side="right", padx=(0, 8))
        self.log = tk.Label(bottom, text="", background=PANEL, foreground=DIM, font=self.f_small, anchor="nw",
                            justify="left", height=4)
        self.log.pack(fill="x", padx=PAD, pady=(0, 10))
        self.log.bind("<Configure>", lambda e: self.log.configure(wraplength=max(200, e.width - 4)))

        # ---- clips
        mid = tk.Frame(root, background=BG)
        mid.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(mid, background=BG, highlightthickness=0, borderwidth=0)
        self.scroll = ttk.Scrollbar(mid, orient="vertical", command=self.canvas.yview, style="Dark.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw_soon())
        self.canvas.bind("<Button-1>", self.click)
        self.canvas.bind("<Double-Button-1>", self.double_click)
        self.canvas.bind("<Motion>", self.motion)
        self.canvas.bind("<B1-Motion>", self.carry_move, add="+")
        self.canvas.bind("<ButtonRelease-1>", self.carry_drop, add="+")
        self.canvas.bind("<Leave>", self.leave)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))
        root.bind("<Control-a>", self.select_all)
        root.bind("<Escape>", lambda e: self.select([]))
        root.bind("<space>", self.space)

        if HAVE_DND:
            self.canvas.drag_source_register(1, DND_FILES)
            self.canvas.dnd_bind("<<DragInitCmd>>", self.drag_init)
            self.canvas.dnd_bind("<<DragEndCmd>>", self.drag_end)
            root.drop_target_register(DND_FILES)
            root.dnd_bind("<<Drop>>", self.dropped)
        self.say("Select cards and click “Send to FL” to add them to the project open in FL; double-clicking a card sends "
                 "it too (for a sample, double-click its title bar). Ctrl / Shift-click selects several. Cards can also be "
                 "dragged straight into FL. A Pattern brings its instruments along.\n"
                 "Audition a sample: click its waveform to play from there; the play button at the card's top left, or Space, "
                 "plays from the start, and once more stops.")
        self.apply_topmost()
        if initial:
            self.open(initial)
        root.after(1000, self.watch)
        root.after(80, self.poll_waves)

    # ---- look ---------------------------------------------------------------------------
    def _fonts(self) -> None:
        families = set(tkfont.families(self.root))
        family = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC",
                                   "Segoe UI") if f in families), tkfont.nametofont("TkDefaultFont").actual("family"))
        self.f_ui = tkfont.Font(root=self.root, family=family, size=10)
        self.f_bold = tkfont.Font(root=self.root, family=family, size=10, weight="bold")
        self.f_small = tkfont.Font(root=self.root, family=family, size=9)
        self.f_clip = tkfont.Font(root=self.root, family=family, size=9)
        self.f_section = tkfont.Font(root=self.root, family=family, size=9, weight="bold")
        self.f_big = tkfont.Font(root=self.root, family=family, size=13)

    def _style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCombobox", fieldbackground=FIELD, background=LINE, foreground=TEXT, arrowcolor=TEXT,
                        bordercolor=LINE, lightcolor=FIELD, darkcolor=FIELD, insertcolor=TEXT, padding=4)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)], selectbackground=[("!focus", FIELD)],
                  selectforeground=[("!focus", TEXT)])
        style.configure("Dark.TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT,
                        bordercolor=LINE, lightcolor=FIELD, darkcolor=FIELD, padding=4)
        style.map("Dark.TEntry", fieldbackground=[("focus", FIELD)], foreground=[("disabled", DIM)],
                  bordercolor=[("focus", ACCENT)])
        style.configure("Dark.Vertical.TScrollbar", background=LINE, troughcolor=BG, bordercolor=BG, arrowcolor=DIM,
                        lightcolor=LINE, darkcolor=LINE, gripcount=0)
        style.map("Dark.Vertical.TScrollbar", background=[("active", "#46535c")])
        for key, value in (("background", FIELD), ("foreground", TEXT), ("selectBackground", ACCENT),
                           ("selectForeground", "#1b1408"), ("font", self.f_ui)):
            self.root.option_add(f"*TCombobox*Listbox.{key}", value)

    # ---- source -------------------------------------------------------------------------
    def say(self, text: str, tone: str = "") -> None:
        """Status text. tone "ok" / "bad" / "busy" colours it and lights the bar up for a moment."""
        self.log_text = text
        ink = {"ok": GOOD, "bad": BAD, "busy": ACCENT}.get(tone, DIM)
        self.log.configure(text=text, foreground=ink)
        if tone in ("ok", "bad"):
            glow = rgb(mix(hexrgb(PANEL), hexrgb(ink), 0.28))
            self.log.configure(background=glow)
            self.root.after(450, lambda: self.log.configure(background=PANEL))
            self.root.after(6000, lambda: self.log_text == text and self.log.configure(foreground=TEXT))

    def apply_topmost(self) -> None:
        self.root.attributes("-topmost", bool(self.topmost.get()))

    def browse(self) -> None:
        path = filedialog.askopenfilename(title="Choose the source project", filetypes=[("FL Studio project", "*.flp")])
        if path:
            self.open(path)

    def open(self, path: str) -> None:
        path = path.strip().strip('"')
        try:
            self.source = Source(path)
        except (FLPError, OSError) as exc:
            self.say(f"Cannot open {path}\n{exc}")
            return
        self.path_var.set(path)
        self.fill()

    def reload(self) -> None:
        if self.source:
            self.open(self.source.path)

    def watch(self) -> None:
        if self.source and self.source.changed_on_disk():
            self.reload()
            self.say("The source project was saved again; the cards have been refreshed.")
        live.reap()
        self.root.after(1000, self.watch)

    def fill(self) -> None:
        self.project_clips = self.source.clips()
        self.status.set(f"{os.path.basename(self.source.path)}  ·  {self.source.summary()}")
        for clip in self.project_clips:
            if clip.sample and clip.sample not in self.waves:
                self.waves[clip.sample] = "wait"
                threading.Thread(target=self._load_wave, args=(clip.sample,), daemon=True).start()
        self.canvas.yview_moveto(0)
        self.search_changed()

    @property
    def rows(self) -> dict:                 # kept for scripts that list what the window shows
        return self.clips

    def _load_wave(self, path: str) -> None:
        try:
            wave = peaks.read(path, WAVE_COLUMNS)
        except Exception:  # noqa: BLE001 - a broken file must not take the window down
            wave = None
        self.wave_queue.put((path, wave))

    def clear_sample_search(self, event=None):
        self.sample_search_var.set("")
        return "break"

    def search_changed(self, *_args) -> None:
        query = self.sample_search_var.get().strip()
        self.clips = {}
        total_samples = 0
        matched_samples = 0
        for clip in self.project_clips:
            if clip.kind == "audio":
                total_samples += 1
                if not sample_matches(clip, query):
                    continue
                matched_samples += 1
            self.clips[clip.key] = clip
        self.sample_search_count.set(f"{matched_samples} / {total_samples}")
        previous = self.selected
        self.selected = [key for key in previous if key in self.clips and self.clips[key].enabled]
        if self.selected != previous:
            if self.selected:
                self.describe()
            else:
                self.say("No card is selected.")
        if self.playing and (self.playing not in self.clips or self.clips[self.playing].sample != self.player.path):
            self.stop_play()
        self.redraw(scroll_to_samples=bool(query))

    def poll_waves(self) -> None:
        got = False
        try:
            while True:
                path, wave = self.wave_queue.get_nowait()
                self.waves[path] = wave
                got = True
        except queue.Empty:
            pass
        if got:
            self.redraw_soon()
        self.root.after(120, self.poll_waves)

    # ---- drawing ------------------------------------------------------------------------
    def redraw_soon(self) -> None:
        if self.redraw_job is None:
            self.redraw_job = self.root.after(30, self.redraw)

    def redraw(self, scroll_to_samples: bool = False) -> None:
        self.redraw_job = None
        cv = self.canvas
        cv.delete("all")
        self.boxes = {}
        width = max(cv.winfo_width(), 2 * PAD + CARD_MIN_W)
        if not self.project_clips:
            cv.create_text(width // 2, max(cv.winfo_height() // 2 - 12, 60), text="Drop a .flp project here",
                           fill=DIM, font=self.f_big)
            cv.create_text(width // 2, max(cv.winfo_height() // 2 + 16, 88),
                           text="or pick a recently opened project above", fill="#56626a", font=self.f_ui)
            cv.configure(scrollregion=(0, 0, width, cv.winfo_height()))
            return
        inner = width - 2 * PAD
        cols = max(1, (inner + GAP) // (CARD_MIN_W + GAP))
        card_w = (inner - GAP * (cols - 1)) / cols
        y = PAD
        sample_top = 0
        query = self.sample_search_var.get().strip()
        for kind, title in SECTIONS:
            group = [c for c in self.clips.values() if c.kind == kind]
            if not group and not (kind == "audio" and query):
                continue
            if kind == "audio":
                sample_top = y - PAD
            cv.create_text(PAD, y + 8, text=title, anchor="w", fill=DIM, font=self.f_section)
            tw = self.f_section.measure(title)
            count = str(len(group))
            cv.create_text(PAD + tw + 8, y + 8, text=count, anchor="w", fill="#56626a", font=self.f_small)
            cv.create_line(PAD + tw + self.f_small.measure(count) + 18, y + 8, width - PAD, y + 8, fill="#262f35")
            y += 24
            if not group:
                cv.create_text(PAD, y + 7, text="No matching samples", anchor="w", fill=DIM, font=self.f_ui)
                y += 32
                continue
            h = CARD_H[kind]
            for i, clip in enumerate(group):
                x0 = PAD + (i % cols) * (card_w + GAP)
                y0 = y + (i // cols) * (h + GAP)
                box = (int(x0), int(y0), int(x0 + card_w), int(y0 + h))
                self.boxes[clip.key] = box
                self.draw_clip(clip, *box)
            y += -(-len(group) // cols) * (h + GAP) + 8
        height = max(y + PAD - 8, cv.winfo_height())
        cv.configure(scrollregion=(0, 0, width, height))
        if scroll_to_samples:
            cv.yview_moveto(sample_top / height)
        self.draw_marks()

    def fit(self, text: str, font, width: float) -> str:
        if width <= 0:
            return ""
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…" if text else ""

    def draw_clip(self, clip: Clip, x0: int, y0: int, x1: int, y1: int, wash=None) -> None:
        """One card. wash = (colour, how much of it in the body, how much in the header) for a state look."""
        cv = self.canvas
        base = clip_color(clip)
        bg = hexrgb(BG)
        fade = 0.0 if clip.enabled else 0.62
        head = mix(base, bg, fade)
        body = mix(mix(base, hexrgb(BODY), 0.86), bg, fade)
        ink = mix(mix(base, (255, 255, 255), 0.62), bg, fade)          # notes and waveform
        name_ink = mix(mix(base, (255, 255, 255), 0.80), bg, fade)
        sub_ink = mix(mix(base, (255, 255, 255), 0.42), bg, fade)
        if wash:
            tone, in_body, in_head = wash
            body, head = mix(body, tone, in_body), mix(head, tone, in_head)
            ink = mix(ink, (255, 255, 255), 0.25)
            if in_head >= 0.6 and sum(tone) > 380:        # bright header: dark lettering
                name_ink, sub_ink = (22, 18, 10), mix((22, 18, 10), head, 0.35)
        cv.create_rectangle(x0, y0, x1, y1, fill=rgb(body), outline="")
        cv.create_rectangle(x0, y0, x1, y0 + HEAD_H, fill=rgb(head), outline="")
        if self.can_play(clip):                           # the icon of a sample that can be heard is a button
            cv.create_rectangle(x0 + 1, y0 + 1, x0 + PLAY_W - 1, y0 + HEAD_H - 1, outline="",
                                fill=rgb(mix(head, (0, 0, 0), 0.30)))
        self.draw_icon(clip.kind, x0 + 6, y0 + HEAD_H // 2, rgb(name_ink))
        detail = clip.detail
        wave = self.waves.get(clip.sample) if clip.sample else None
        if clip.kind == "audio" and isinstance(wave, peaks.Wave) and wave.seconds:
            detail = seconds_text(wave.seconds)
        room = x1 - x0 - 26
        shown = self.fit(clip.name, self.f_clip, room)
        cv.create_text(x0 + 20, y0 + HEAD_H // 2, text=shown, anchor="w", fill=rgb(name_ink), font=self.f_clip)
        left = room - self.f_clip.measure(shown) - 14
        if clip.kind in ("pattern", "audio") and left > 40:
            cv.create_text(x1 - 6, y0 + HEAD_H // 2, text=self.fit(detail, self.f_small, left), anchor="e",
                           fill=rgb(sub_ink), font=self.f_small)
        bx0, by0, bx1, by1 = x0, y0 + HEAD_H, x1, y1
        if clip.kind == "pattern":
            self.draw_notes(clip, bx0, by0, bx1, by1, rgb(ink), rgb(mix(body, (255, 255, 255), 0.05)))
        elif clip.kind == "audio":
            self.draw_wave(clip, wave, bx0, by0, bx1, by1, rgb(ink), rgb(sub_ink))
        else:
            cv.create_text(bx0 + 8, (by0 + by1) // 2, text=self.fit(clip.detail, self.f_small, x1 - x0 - 16),
                           anchor="w", fill=rgb(sub_ink), font=self.f_small)

    def draw_icon(self, kind: str, x: int, cy: int, color: str) -> None:
        cv = self.canvas
        if kind == "pattern":                      # three short rows, like FL's pattern clip mark
            for i, dy in enumerate((-3, 0, 3)):
                cv.create_line(x + (2 if i == 1 else 0), cy + dy, x + 8, cy + dy, fill=color)
        elif kind == "audio":                      # small play arrow
            cv.create_polygon(x + 1, cy - 4, x + 8, cy, x + 1, cy + 4, fill=color, outline="")
        else:                                      # plug
            cv.create_rectangle(x + 1, cy - 3, x + 7, cy + 3, outline=color)
            cv.create_line(x + 4, cy - 3, x + 4, cy + 4, fill=color)

    def draw_notes(self, clip: Clip, x0, y0, x1, y1, color: str, grid: str) -> None:
        cv = self.canvas
        w, h = x1 - x0, y1 - y0
        if not clip.notes or clip.length <= 0:
            return
        bar = self.source.project.ppq * 4 if self.source else clip.length
        bars = clip.length // max(1, bar)
        if 1 < bars <= 64:
            for i in range(1, bars):
                gx = x0 + w * i / bars
                cv.create_line(gx, y0, gx, y1, fill=grid)
        notes = clip.notes
        if len(notes) > MAX_NOTES_DRAWN:
            notes = notes[::len(notes) // MAX_NOTES_DRAWN + 1]
        keys = [n[2] for n in notes]
        low, high = min(keys), max(keys)
        span = high - low
        pad = 6
        room = h - 2 * pad
        thick = max(2.0, min(4.0, room / (span + 1)))
        for pos, length, key, _ in notes:
            nx0 = x0 + 1 + (w - 2) * pos / clip.length
            nx1 = x0 + 1 + (w - 2) * min(clip.length, pos + max(length, 1)) / clip.length
            nx1 = max(nx1 - 1, nx0 + 2)
            ny = (y0 + h / 2 - thick / 2) if span == 0 else y0 + pad + (room - thick) * (high - key) / span
            cv.create_rectangle(nx0, ny, nx1, ny + thick, fill=color, outline="")

    def draw_wave(self, clip: Clip, wave, x0, y0, x1, y1, color: str, quiet: str) -> None:
        cv = self.canvas
        mid = (y0 + y1) / 2
        w = x1 - x0
        if not isinstance(wave, peaks.Wave) or not wave.columns:
            cv.create_line(x0, mid, x1, mid, fill=quiet)
            if not clip.sample:
                note = "Sample file not found · " + clip.sample_name
            elif wave == "wait":
                note = "Reading waveform…"
            else:
                ext = os.path.splitext(clip.sample)[1].lstrip(".").lower() or "audio"
                note = f"{ext} file · no waveform preview"
            label = self.fit(note, self.f_small, w - 16)
            tw = self.f_small.measure(label)
            body = cv.itemcget(cv.find_closest(x0 + 2, y1 - 2)[0], "fill") or BODY
            cv.create_rectangle(x0 + 6, mid - 9, x0 + 12 + tw, mid + 9, fill=body, outline="")
            cv.create_text(x0 + 9, mid, text=label, anchor="w", fill=quiet, font=self.f_small)
            return
        cols = wave.columns
        loud = max(max(abs(lo), abs(hi)) for lo, hi in cols)
        scale = (y1 - y0 - 8) / 2 / max(loud, 0.25)
        n = int(max(2, min(len(cols), w)))
        top, bottom = [], []
        for i in range(n):
            a = i * len(cols) // n
            b = max(a + 1, (i + 1) * len(cols) // n)
            lo = min(c[0] for c in cols[a:b])
            hi = max(c[1] for c in cols[a:b])
            x = x0 + (w * i / (n - 1) if n > 1 else 0)
            top.append((x, mid - max(hi, 0.0) * scale - 0.5))
            bottom.append((x, mid - min(lo, 0.0) * scale + 0.5))
        points = [v for p in top + bottom[::-1] for v in p]
        cv.create_polygon(points, fill=color, outline="")
        cv.create_line(x0, mid, x1, mid, fill=color)

    def draw_marks(self) -> None:
        """Hover, pressed, selected and just-sent looks, drawn over the plain cards."""
        cv = self.canvas
        cv.delete("mark")
        last = max(cv.find_all() or (0,))

        def again(key, wash):                            # the same card once more, in its state colours
            cv_box = self.boxes[key]
            self.draw_clip(self.clips[key], *cv_box, wash=wash)
            return cv_box

        if self.hover in self.boxes and self.hover not in self.selected and self.hover != self.pressed:
            x0, y0, x1, y1 = again(self.hover, ((255, 255, 255), 0.07, 0.14))
            cv.create_rectangle(x0 - 1, y0 - 1, x1, y1, outline="#9aa8b1")
        for key in self.selected:
            if key in self.boxes and key not in self.flash and key != self.pressed:
                x0, y0, x1, y1 = again(key, (hexrgb(ACCENT), 0.16, 0.92))
                cv.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline=ACCENT, width=3)
                cv.create_oval(x1 - 20, y1 - 20, x1 - 5, y1 - 5, fill=ACCENT, outline="#1b1408")
                cv.create_line(x1 - 17, y1 - 12, x1 - 13, y1 - 9, x1 - 8, y1 - 16, fill="#1b1408", width=2)
        if self.pressed in self.boxes and self.pressed not in self.flash:
            x0, y0, x1, y1 = again(self.pressed, ((255, 255, 255), 0.20, 0.30))   # held down: lights up
            cv.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline="#ffffff", width=3)
        for key, (color, label) in self.flash.items():
            if key in self.boxes:
                x0, y0, x1, y1 = again(key, (hexrgb(color), 0.30, 0.92))
                cv.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, outline=color, width=4)
                if label:
                    w = self.f_bold.measure(label) + 22
                    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2 + HEAD_H // 2
                    cv.create_rectangle(cx - w // 2, cy - 13, cx + w // 2, cy + 13, fill=color, outline="")
                    cv.create_text(cx, cy, text=label, fill="#101a14", font=self.f_bold)
        for item in cv.find_all():
            if item > last:
                cv.addtag_withtag("mark", item)
        self.draw_play()

    # ---- audition -----------------------------------------------------------------------
    @staticmethod
    def can_play(clip: Clip) -> bool:
        return clip.kind == "audio" and bool(clip.sample)

    def play_at(self, event) -> str | None:
        """The card whose play button is under the pointer."""
        key = self.key_at(event)
        if key is None or not self.can_play(self.clips[key]):
            return None
        x0, y0 = self.boxes[key][:2]
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        return key if x <= x0 + PLAY_W and y <= y0 + HEAD_H else None

    def draw_play(self) -> None:
        """The play button under the pointer, and the stop button and play head of the card being heard."""
        cv = self.canvas
        cv.delete("play")
        for key, on in ((self.play_hover, False), (self.playing, True)):
            if key not in self.boxes or (not on and key == self.playing):
                continue
            x0, y0, x1, y1 = self.boxes[key]
            cy = y0 + HEAD_H // 2
            cv.create_rectangle(x0 + 1, y0 + 1, x0 + PLAY_W - 1, y0 + HEAD_H - 1, outline="",
                                fill="#14191c" if on else "#e6ecef", tags="play")
            if on:
                cv.create_rectangle(x0 + 6, cy - 4, x0 + 14, cy + 4, fill=ACCENT, outline="", tags="play")
                x = x0 + (x1 - x0) * self.player.fraction
                cv.create_line(x, y0 + HEAD_H, x, y1, fill="#ffffff", tags=("play", "head"))
            else:
                cv.create_polygon(x0 + 7, cy - 4, x0 + 14, cy, x0 + 7, cy + 4, fill="#1b2227", outline="",
                                  tags="play")

    def seek_at(self, event):
        """(card, fraction) when the pointer is on the waveform of a sample that can be heard."""
        key = self.key_at(event)
        if key is None or not self.can_play(self.clips[key]):
            return None
        x0, y0, x1, y1 = self.boxes[key]
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if y <= y0 + HEAD_H or x1 <= x0:
            return None
        return key, min(max((x - x0) / (x1 - x0), 0.0), 0.999)

    def toggle_play(self, key: str) -> None:
        """Start the card's sample, or stop it when it is the one being heard."""
        if key == self.playing:
            self.stop_play()
            self.say("Audition stopped.")
            return
        self.start_play(key)

    def start_play(self, key: str, at: float = 0.0) -> None:
        """Hear the card's sample from the fraction at (0..1) of its length."""
        clip = self.clips[key]
        if not self.can_play(clip):
            if clip.kind == "audio":
                self.say(f"{clip.name}: the sample file is missing, so it cannot be auditioned.", "bad")
            else:
                self.say("Only samples can be auditioned.")
            return
        told = self.play_told and key == self.playing       # a jump inside the same sample: say nothing new
        self.stop_play()
        self.playing, self.play_told = key, told
        self.player.start(clip.sample, at)
        self.draw_play()
        self.play_tick()

    def stop_play(self) -> None:
        self.player.stop()
        self.playing = None
        if self.play_job is not None:
            self.root.tk.call("after", "cancel", self.play_job)
            self.play_job = None
        self.draw_play()

    def play_tick(self) -> None:
        self.play_job = None
        if self.playing is None:
            return
        clip = self.clips.get(self.playing)
        state = self.player.poll()
        if clip is None or state == "idle":                # played to the end
            self.stop_play()
            return
        if state == "error":
            text = self.player.error
            self.stop_play()
            self.say(f"{clip.name}: {text}", "bad")
            return
        if state == "playing":
            if not self.play_told:
                self.play_told = True
                more = " This sample is long: at most two minutes play from where you clicked." if self.player.cut else ""
                self.say(f"Auditioning: {clip.name} ({os.path.basename(clip.sample)}). Click elsewhere on the waveform to jump there; "
                         f"the play button or Space stops.\nYou hear the original file, without the channel's pitch, trim, "
                         f"reverse and similar settings.{more}",
                         "busy")
            if self.playing in self.boxes and self.canvas.find_withtag("head"):
                x0, y0, x1, y1 = self.boxes[self.playing]
                x = x0 + (x1 - x0) * self.player.fraction
                self.canvas.coords("head", x, y0 + HEAD_H, x, y1)
        self.play_job = self.root.after(30, self.play_tick)

    def space(self, event=None):
        if isinstance(self.root.focus_get(), (ttk.Combobox, tk.Entry)):
            return None
        if self.playing:
            self.stop_play()
            self.say("Audition stopped.")
        elif len(self.selected) == 1:
            self.toggle_play(self.selected[0])
        elif self.hover in self.boxes:
            self.toggle_play(self.hover)
        else:
            self.say("Select a sample first, then press Space to audition it.")
        return "break"

    def flash_cards(self, keys: list[str], color: str, label: str, ms: int = 1400) -> None:
        """Light the given cards up for a moment: the visible answer to a send or a drop."""
        for key in keys:
            self.flash[key] = (color, label)
        self.draw_marks()

        def clear():
            for key in keys:
                if self.flash.get(key) == (color, label):
                    del self.flash[key]
            self.draw_marks()
        self.root.after(ms, clear)

    # ---- pointer ------------------------------------------------------------------------
    def key_at(self, event) -> str | None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for key, (x0, y0, x1, y1) in self.boxes.items():
            if x0 <= x <= x1 and y0 <= y <= y1:
                return key
        return None

    def set_hover(self, key: str | None) -> None:
        if key != self.hover:
            self.hover = key
            self.canvas.configure(cursor="hand2" if key else "")
            self.draw_marks()

    def leave(self, event=None) -> None:
        self.play_hover = None
        self.draw_play()
        self.set_hover(None)

    def motion(self, event) -> None:
        over = self.play_at(event)
        if over != self.play_hover:
            self.play_hover = over
            if self.key_at(event) == self.hover:
                self.draw_play()
        self.set_hover(self.key_at(event))

    def select(self, keys: list[str]) -> None:
        self.selected = [key for key in keys if key in self.boxes and self.clips[key].enabled]
        self.draw_marks()

    def select_all(self, event=None):
        if isinstance(self.root.focus_get(), (ttk.Combobox, tk.Entry)):
            return None
        self.select([k for k in self.boxes if self.clips[k].enabled])
        return "break"

    def click(self, event) -> None:
        self.canvas.focus_set()
        self.seek = None
        play = self.play_at(event)
        if play:                                         # the play button: hear it, nothing else
            self.press, self.carry, self.pressed = (event.x_root, event.y_root, None), [], None
            self.toggle_play(play)
            return
        self.press = (event.x_root, event.y_root, self.key_at(event))
        self.carry = []
        self.pressed = self.press[2] if self.press[2] and self.clips[self.press[2]].enabled else None
        self.draw_marks()
        key = self.key_at(event)
        if key is None:
            self.select([])
            return
        clip = self.clips[key]
        if not clip.enabled:
            self.say(f"{clip.name}: {clip.why_not}")
            return
        if event.state & 0x0001 and self.selected:                       # Shift: range
            keys = list(self.boxes)
            a, b = sorted((keys.index(self.selected[-1]), keys.index(key)))
            picked = [k for k in keys[a:b + 1] if self.clips[k].enabled]
            self.select([k for k in self.selected if k not in picked] + picked)
        elif event.state & 0x0004:                                       # Ctrl: toggle
            self.select([k for k in self.selected if k != key] + ([] if key in self.selected else [key]))
        elif key not in self.selected:
            self.select([key])
        self.describe()
        if not event.state & 0x0005:                     # a plain click on a waveform also plays from there,
            self.seek = self.seek_at(event)              # once the button is let go without a drag

    def describe(self) -> None:
        if len(self.selected) == 1:
            clip = self.clips[self.selected[0]]
            if clip.kind == "pattern":
                names = ", ".join(n for _, n in clip.channels)
                self.say(f"{clip.name}: {plural(len(clip.notes), 'note')}, channels used: {names}.\n"
                         "Sent to FL, it becomes a new Pattern together with every instrument it uses; "
                         "the notes stay on those new channels.")
            elif clip.kind == "audio":
                where = clip.sample or f"File not found (the project refers to {clip.sample_name})"
                self.say(f"{clip.name}: {clip.detail}\n{where}")
            else:
                self.say(f"{clip.name}: {clip.detail}")
        elif self.selected:
            self.say(f"{len(self.selected)} items selected")

    def double_click(self, event) -> None:
        if self.play_at(event) or (self.seek_at(event) and not event.state & 0x0005):
            self.click(event)                            # two quick clicks on the play button or on a waveform
            return                                       # are two clicks: listening must never send by accident
        key = self.key_at(event)
        if key and self.clips[key].enabled:
            self.select([key])
            self.send()

    # ---- out of the window --------------------------------------------------------------
    def chosen(self) -> list[str]:
        keys = [k for k in self.boxes if k in self.selected]             # in display order
        if not keys:
            self.say("Select the Patterns or samples to transfer first (Ctrl / Shift-click selects several).")
        return keys

    def mode(self) -> str:
        return SAMPLE if self.raw_audio.get() else PRESET

    def dragged_keys(self, key: str | None) -> list[str]:
        if key not in self.boxes or not self.clips[key].enabled:
            return []
        return [k for k in self.boxes if k in self.selected] if key in self.selected else [key]

    def drag_init(self, event):
        """A channel card leaves the window as a file. Anything with a pattern in it is carried instead."""
        x = self.canvas.winfo_pointerx() - self.canvas.winfo_rootx()
        y = self.canvas.winfo_pointery() - self.canvas.winfo_rooty()
        key = self.press[2] if self.press else self.key_at(type("Point", (), {"x": x, "y": y}))
        self.seek = None                                 # the card is being dragged out, not listened to
        keys = self.dragged_keys(key)
        if not keys:
            return ("refuse_drop",)
        if any(k.startswith("p") for k in keys):
            self.carry_start(keys)
            return ("refuse_drop",)
        try:
            files = [str(f) for k in keys for f in self.source.files_for(k, self.mode())]
        except (FLPError, OSError) as exc:
            self.say(str(exc))
            return ("refuse_drop",)
        self.say("Dragging out: " + ", ".join(os.path.basename(f) for f in files) + ". Release it inside FL.", "busy")
        self.root.update_idletasks()
        return (COPY, DND_FILES, tuple(files))

    # A pattern cannot leave as a file of its own: FL would put the notes on whatever channel the
    # file lands on, without the instrument.  So a dragged pattern is carried by this window and,
    # when the button is let go over FL, sent the same way as the Send to FL button.
    def carry_start(self, keys: list[str]) -> None:
        if self.carry:
            return
        self.carry = keys
        self.canvas.configure(cursor="fleur")
        names = ", ".join(self.clips[k].name for k in keys[:3]) + (" and more" if len(keys) > 3 else "")
        self.say(f"Dragging: {names}. Release over the FL window and the Pattern is added together with its instruments.", "busy")
        self.ghost_show(keys)

    def carry_move(self, event) -> None:
        if self.carry:
            self.ghost_move(event.x_root, event.y_root)
            return
        if not self.press:
            return
        x, y, key = self.press
        if abs(event.x_root - x) + abs(event.y_root - y) < 8:
            return
        keys = self.dragged_keys(key)
        if any(k.startswith("p") for k in keys):
            self.carry_start(keys)
            self.ghost_move(event.x_root, event.y_root)

    def carry_drop(self, event) -> None:
        seek, self.seek, press = self.seek, None, self.press
        keys, self.carry, self.press = self.carry, [], None
        self.pressed = None
        self.draw_marks()
        if not keys:
            if seek and press and seek[0] in self.boxes and \
                    abs(event.x_root - press[0]) + abs(event.y_root - press[1]) < 8:
                self.start_play(*seek)
            return
        self.canvas.configure(cursor="")
        self.ghost_hide()
        if not live.pointer_over_fl():
            self.say("Not released over an FL window: nothing was sent.", "bad")
            return
        self.select(keys)
        self.send()

    # The chip that follows the pointer while a card is carried.  It says what is being dragged
    # and turns orange as soon as letting go would send it.
    def ghost_show(self, keys: list[str]) -> None:
        self.ghost_hide()
        g = tk.Toplevel(self.root)
        g.overrideredirect(True)
        g.attributes("-topmost", True)
        try:
            g.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        frame = tk.Frame(g, background=LINE, padx=2, pady=2)
        frame.pack()
        inner = tk.Frame(frame, background=PANEL, padx=12, pady=7)
        inner.pack()
        first = self.clips[keys[0]]
        title = first.name + (f"  + {len(keys) - 1} more" if len(keys) > 1 else "")
        pats = [k for k in keys if k.startswith("p")]
        chans = {i for k in pats for i, _ in self.clips[k].channels}
        sub = (f"{plural(len(pats), 'Pattern')} + {plural(len(chans), 'instrument')}" if pats
               else plural(len(keys), "channel"))
        g.title_label = tk.Label(inner, text="♪  " + self.fit(title, self.f_bold, 260), background=PANEL,
                                 foreground=TEXT, font=self.f_bold, anchor="w")
        g.title_label.pack(fill="x")
        g.sub_label = tk.Label(inner, text=sub, background=PANEL, foreground=DIM, font=self.f_small, anchor="w")
        g.sub_label.pack(fill="x")
        g.hint_label = tk.Label(inner, text="", background=PANEL, foreground=DIM, font=self.f_small, anchor="w")
        g.hint_label.pack(fill="x", pady=(3, 0))
        g.frame, g.inner = frame, inner
        self.ghost, self.ghost_over = g, None
        self.ghost_tone(False)

    def ghost_tone(self, over: bool) -> None:
        g = self.ghost
        if g is None or over == self.ghost_over:
            return
        self.ghost_over = over
        edge = ACCENT if over else LINE
        fill = rgb(mix(hexrgb(PANEL), hexrgb(ACCENT), 0.30)) if over else PANEL
        g.frame.configure(background=edge)
        for w in (g.inner, g.title_label, g.sub_label, g.hint_label):
            w.configure(background=fill)
        g.title_label.configure(foreground="#ffffff" if over else TEXT)
        g.sub_label.configure(foreground=TEXT if over else DIM)
        g.hint_label.configure(text="Release: add to this FL project" if over else "Drag onto the FL window, then release",
                               foreground=ACCENT if over else DIM)

    def ghost_move(self, x: int, y: int) -> None:
        if self.ghost is None:
            return
        self.ghost.geometry(f"+{x + 18}+{y + 20}")
        self.ghost_tone(live.pointer_over_fl())

    def ghost_hide(self) -> None:
        if self.ghost is not None:
            self.ghost.destroy()
        self.ghost = None

    def drag_end(self, event) -> None:
        """A channel card was dragged out as a file. If it ended over FL, FL may have a new channel."""
        self.pressed = None
        self.draw_marks()
        if live.pointer_over_fl():
            live.mark_unsure()
            self.say("Dragged into FL.", "ok")

    def dropped(self, event):
        for path in self.root.tk.splitlist(event.data):
            if path.lower().endswith(".flp"):
                self.open(path)
                break

    def may_save(self, name: str) -> bool:
        """A pattern is only sent after FL has saved the target.  Ask, unless the switch says always."""
        if self.save_first.get():
            return True
        answer = messagebox.askyesnocancel(
            f"Let FL save {name} first?", parent=self.root, message=(
                "A Pattern's notes have to say which channel of the target project they belong to. FL does not tell "
                "other programs how many channels it has, so the only way is to let it save once and read the file. "
                "Without that the notes miss their instruments, which looks like “no MIDI”.\n\n"
                f"Yes: press Ctrl+S once in FL to save {name}, then send; do the same every time from now on, without asking.\n"
                "No: save and send just this once, and ask again next time.\n"
                "Cancel: neither save nor send.\n\n"
                "As always, each save in FL leaves an “overwritten” backup in the project's Backup folder."))
        if answer:
            self.save_first.set(True)
            live.set_setting("save_first", True)
        return answer is not None

    def find_target(self, save: bool = False):
        """The project open in FL, asking once for its file when the name alone does not locate it."""
        known = [self.source.path] + list(self.combo.cget("values") or ())

        def notice(text: str) -> None:               # FL is waiting for the user: say so while we wait
            self.say(text, "bad")
            self.root.update()
            self.root.bell()

        try:
            return live.find_target(known=known, save=save, notice=notice)
        except live.TargetUnknown as exc:
            if not exc.name:
                raise
            path = filedialog.askopenfilename(
                parent=self.root, title=f"Where is {exc.name}, the project open in FL? (Only its channel count is read; it is not changed)",
                initialfile=exc.name, filetypes=[("FL Studio project", "*.flp")])
            if not path:
                raise
            if os.path.basename(path).lower() != exc.name.lower():
                raise live.LiveError(f"The chosen file is not {exc.name}; nothing was sent.")
            live.remember_path(path)
            return live.find_target(known=known, save=save, notice=notice)

    def send(self, event=None) -> None:
        if not self.source:
            return
        keys = self.chosen()
        if not keys:
            return
        several = live.fl_running() > 1
        with_pattern = any(k.startswith("p") for k in keys)
        done: list[str] = []
        target = None
        self.flash_cards(keys, ACCENT, "Sending…", 60000)
        self.say("Sending…", "busy")
        self.root.update()
        first_channel = first_pattern = 0
        try:
            try:
                target = self.find_target()
                if with_pattern:
                    # The count in the saved file is only right if nothing was added or deleted in
                    # FL since.  That cannot be seen from here, so FL saves first, every time.
                    if not self.may_save(target.name):
                        self.flash.clear()
                        self.draw_marks()
                        self.say("Cancelled: nothing was saved or sent.", "bad")
                        return
                    self.say(f"Asking FL to save {target.name}…", "busy")
                    self.root.update()
                    target = self.find_target(save=True)
            except live.LiveError:
                if with_pattern:                  # channels alone do not need the target's numbers
                    raise
            if target:
                first_channel, first_pattern = target.next_channel, target.next_pattern
            for path, channels, patterns in self.source.send_plan(keys, target):
                live.send(path)
                if target:
                    target.sent(channels, patterns)
                done.append(f"{plural(patterns, 'Pattern')} + {plural(channels, 'instrument')}" if patterns else path.stem)
                self.root.update()
                self.root.after(700)              # FL takes the files one at a time
        except (FLPError, OSError, live.LiveError) as exc:
            self.flash.clear()
            self.flash_cards(keys, BAD, "Not sent")
            self.say("\n".join(([f"Sent: {', '.join(done)}"] if done else []) + [f"Not sent: {exc}"]), "bad")
            return
        shown = False
        if with_pattern:
            # FL stays on whatever pattern it was showing, where the new instrument's row is empty.
            # Once it has finished loading, switch it to the new pattern so the notes are in view.
            self.say("FL is loading the instruments…", "busy")
            shown = self.show_in_fl(first_pattern)
        self.flash.clear()
        self.flash_cards(keys, GOOD, "Sent ✓")
        text = "Sent to FL: " + ", ".join(done)
        if with_pattern:
            count = sum(1 for k in keys if k.startswith("p"))
            numbers = f"#{first_pattern}" if count == 1 else f"#{first_pattern}–#{first_pattern + count - 1}"
            where = f" The instruments are the new channels at the bottom of the Channel Rack (from channel {first_channel + 1})."
            if shown:
                text += (f"\nFL has switched to the new Pattern #{first_pattern}; the notes are in the new instruments' rows "
                         "in the Channel Rack." + where)
                if count > 1:
                    text += f" The Patterns sent this time are {numbers}."
            else:
                text += (f"\nThe notes are in the new Pattern {numbers}. FL did not switch to it: pick #{first_pattern} in the "
                         "Pattern selector at the top of FL, otherwise the new instruments' rows look empty." + where)
        if several:
            text += "\nSeveral FL windows are open: FL decides which one receives."
        self.say(text, "ok")

    def show_in_fl(self, number: int) -> bool:
        """After FL has loaded what was sent, make it display that pattern. False if it could not."""
        limit = time.time() + 120
        while live.pending() and time.time() < limit:        # plugins can take a while to load
            self.root.update()
            time.sleep(0.05)
        if live.pending():
            return False
        wins = live.fl_windows()
        told = False
        limit = time.time() + 60
        while wins and live.fl_blocked(wins[0][0]) and time.time() < limit:
            if not told:                                     # e.g. a plugin's own notice
                told = True
                self.say("FL is showing a dialog (most likely a plugin's own notice). Dismiss it in FL and the switch to the new Pattern continues.", "bad")
            self.root.update()
            time.sleep(0.1)
        time.sleep(0.3)
        return live.show_pattern(number)

    def reveal(self, event=None) -> None:
        if not self.source:
            return
        keys = self.chosen()
        if not keys:
            return
        try:
            path = self.source.files_for(keys[0], self.mode())[0]
        except (FLPError, OSError) as exc:
            self.say(str(exc))
            return
        self.say(f"File: {path}")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])

    def merge(self) -> None:
        if not self.source:
            return
        keys = self.chosen()
        if not keys:
            return
        target = filedialog.askopenfilename(title="Merge into which project? (A new copy is written; the original is untouched)",
                                            filetypes=[("FL Studio project", "*.flp")])
        if target:
            self.merge_into(target, keys)

    def merge_into(self, target: str, keys: list[str]) -> None:
        try:
            out, report = self.source.merge_into(target, keys)
        except (FLPError, OSError) as exc:
            self.say(f"No file was written.\n{exc}")
            return
        self.say(f"Created: {out}\n" + "; ".join(report.lines()))
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(out)])


def resource(name: str) -> str | None:
    """A file shipped with the program: next to the packaged app, or in packaging/ when run from source."""
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (getattr(sys, "_MEIPASS", None), os.path.join(here, "..", "..", "packaging")):
        if folder and os.path.isfile(os.path.join(folder, name)):
            return os.path.join(folder, name)
    return None


def error_log() -> str:
    folder = os.path.join(tempfile.gettempdir(), "FLTransfer")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "error.log")


def log_error(text: str) -> str:
    """Append to the error log (the packaged app has no console). Returns the log's path."""
    path = error_log()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f"  FL Transfer {__version__}\n{text}\n\n")
    except OSError:
        pass
    return path


def main(initial: str | None = None) -> int:
    root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
    icon = resource("icon.ico")
    if icon and sys.platform == "win32":
        try:
            root.iconbitmap(default=icon)
        except tk.TclError:
            pass
    app = App(root, initial)

    def report(kind, value, trace):                 # an error inside a button or mouse handler
        import traceback
        path = log_error("".join(traceback.format_exception(kind, value, trace)))
        try:
            app.flash.clear()
            app.draw_marks()
            app.say(f"Something went wrong: {value}\nDetails are logged in {path}", "bad")
        except Exception:
            pass

    root.report_callback_exception = report
    root.mainloop()
    app.player.stop()
    return 0
