"""Play a sample through Windows' default sound output, so a card can be heard before it is sent.

Standard library only.  WAV files (PCM 8/16/24/32 bit, float 32/64 bit) are decoded here and played
with waveOut; any other file (mp3 and whatever Windows itself can decode) is handed to MCI.

    player = Player()
    player.start(path)          # returns at once; the file is read on a worker thread
    player.start(path, 0.5)     # the same, from the middle of the sample
    player.poll()               # call regularly from the UI thread: "idle" "loading" "playing" "error"
    player.fraction             # 0..1, how far into the sample the sound is
    player.stop()
"""
from __future__ import annotations

import os
import queue
import struct
import sys
import threading
import time
from array import array
from dataclasses import dataclass

from .peaks import EXTENSIBLE, FLOAT, PCM, _chunks

LIMIT_SECONDS = 120.0           # a long recording is auditioned from its start, not loaded whole
_UNSIGN = bytes(b ^ 0x80 for b in range(256))      # 8 bit WAV is unsigned


class AuditionError(Exception):
    """The sample cannot be played; the text is meant for the status line."""


@dataclass
class Pcm:
    """Sound ready for the output device: 16 bit signed little-endian, interleaved, 1 or 2 channels."""
    rate: int
    channels: int
    data: bytes
    seconds: float              # length of the whole file
    played: float               # length of data; shorter than seconds when only a part of the file is in it
    start: float = 0.0          # where in the file data begins, in seconds

    @property
    def cut(self) -> bool:
        """The file goes on after the end of data."""
        return self.start + self.played < self.seconds - 0.01

    @property
    def whole(self) -> bool:
        return self.start == 0.0 and not self.cut

    def tail(self, at: float) -> "Pcm":
        """The same sound from the fraction at (0..1) of the file on.  Only for a Pcm holding the whole file."""
        frame = self.channels * 2
        frames = len(self.data) // frame
        first = min(max(int(at * frames), 0), max(frames - 1, 0))
        if not first:
            return self
        return Pcm(self.rate, self.channels, self.data[first * frame:], self.seconds,
                   (frames - first) / self.rate, first / self.rate)


def _clip16(values) -> list[int]:
    try:
        return [32767 if v >= 1.0 else -32768 if v <= -1.0 else int(v * 32767.0) for v in values]
    except (ValueError, OverflowError):                 # a NaN somewhere in the file
        return [0 if v != v else 32767 if v >= 1.0 else -32768 if v <= -1.0 else int(v * 32767.0) for v in values]


def decode(path, limit: float = LIMIT_SECONDS, at: float = 0.0) -> Pcm | None:
    """A WAV file as 16 bit sound, or None when it is not a WAV this module decodes.

    at is where to begin, as a fraction 0..1 of the file; at most limit seconds from there are read.
    A file no longer than limit comes back whole whatever at is (Pcm.tail then gives the part wanted).
    More than two channels: the first two are kept.  OSError when the file cannot be read.
    """
    with open(path, "rb") as fh:
        fmt = data = None
        for tag, size, pos in _chunks(fh):
            if tag == b"fmt ":
                here = fh.tell()
                fmt = fh.read(min(size, 40))
                fh.seek(here)
            elif tag == b"data":
                data = (pos, size)
                break
        if not fmt or not data or len(fmt) < 16:
            return None
        kind, nch, rate, _, block, bits = struct.unpack("<HHIIHH", fmt[:16])
        if kind == EXTENSIBLE and len(fmt) >= 26:
            kind = struct.unpack("<H", fmt[24:26])[0]
        if (kind, bits) not in ((PCM, 8), (PCM, 16), (PCM, 24), (PCM, 32), (FLOAT, 32), (FLOAT, 64)):
            return None
        width = bits // 8
        if nch < 1 or rate < 1 or block != nch * width:
            return None
        pos, size = data
        fh.seek(0, 2)
        total = min(size, fh.tell() - pos) // block
        if total < 1:
            return Pcm(rate, min(nch, 2), b"", 0.0, 0.0)
        most = max(1, int(limit * rate))
        first = min(max(int(at * total), 0), total - 1) if total > most else 0
        frames = min(total - first, most)
        fh.seek(pos + first * block)
        raw = fh.read(frames * block)
    frames = len(raw) // block
    raw = raw[:frames * block]
    och = min(nch, 2)
    out = bytearray(frames * och * 2)
    if kind == PCM and width == 1:
        for c in range(och):
            out[c * 2 + 1::och * 2] = raw[c::block].translate(_UNSIGN)
    elif kind == PCM:                                   # keep the two most significant bytes of every sample
        for c in range(och):
            for b in (0, 1):
                out[c * 2 + b::och * 2] = raw[c * width + width - 2 + b::block]
    else:
        samples = array("f" if width == 4 else "d")
        samples.frombytes(raw)
        if sys.byteorder == "big":
            samples.byteswap()
        mixed = array("h", bytes(len(out)))
        for c in range(och):
            mixed[c::och] = array("h", _clip16(samples[c::nch]))
        if sys.byteorder == "big":
            mixed.byteswap()
        out = mixed.tobytes()
    return Pcm(rate, och, bytes(out), total / rate, frames / rate, first / rate)


# ---- Windows outputs --------------------------------------------------------------------------
def _winmm():
    import ctypes
    return ctypes, ctypes.WinDLL("winmm")


class _WaveOut:
    """One buffer of 16 bit sound on the default output device."""

    def __init__(self, pcm: Pcm):
        ctypes, mm = _winmm()
        from ctypes import wintypes

        class WAVEFORMATEX(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                        ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                        ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]

        class WAVEHDR(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                        ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                        ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                        ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]

        class MMTIME(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("wType", wintypes.UINT), ("value", wintypes.DWORD), ("more", wintypes.DWORD)]

        self._ctypes, self._mm, self._MMTIME = ctypes, mm, MMTIME
        mm.waveOutOpen.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.c_size_t, wintypes.DWORD]
        for name in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader", "waveOutGetPosition"):
            getattr(mm, name).argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        mm.waveOutReset.argtypes = mm.waveOutClose.argtypes = [ctypes.c_void_p]
        mm.waveOutGetErrorTextW.argtypes = [wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]

        self.pcm = pcm
        self.started = time.monotonic()
        self.handle = ctypes.c_void_p()
        self.header = None
        frame = pcm.channels * 2
        fmt = WAVEFORMATEX(1, pcm.channels, pcm.rate, pcm.rate * frame, frame, 16, 0)
        err = mm.waveOutOpen(ctypes.byref(self.handle), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0)   # WAVE_MAPPER
        if err:
            self.handle = None
            raise AuditionError("Cannot open the sound output device: " + self._text(err))
        self.buffer = ctypes.create_string_buffer(pcm.data, len(pcm.data))
        self.header = WAVEHDR(ctypes.addressof(self.buffer), len(pcm.data), 0, 0, 0, 0, None, 0)
        size = ctypes.sizeof(self.header)
        err = mm.waveOutPrepareHeader(self.handle, ctypes.byref(self.header), size) or \
            mm.waveOutWrite(self.handle, ctypes.byref(self.header), size)
        if err:
            self.close()
            raise AuditionError("The sound did not play: " + self._text(err))

    def _text(self, err: int) -> str:
        buf = self._ctypes.create_unicode_buffer(256)
        self._mm.waveOutGetErrorTextW(err, buf, 256)
        return buf.value.strip() or f"error {err}"

    @property
    def seconds(self) -> float:
        return self.pcm.seconds

    def position(self) -> float:
        """Where in the file the sound is, in seconds."""
        mm_time = self._MMTIME(4, 0, 0)                                    # TIME_BYTES
        if self.handle and not self._mm.waveOutGetPosition(self.handle, self._ctypes.byref(mm_time),
                                                           self._ctypes.sizeof(mm_time)) and mm_time.wType == 4:
            return self.pcm.start + mm_time.value / (self.pcm.rate * self.pcm.channels * 2)
        return self.pcm.start + min(time.monotonic() - self.started, self.pcm.played)

    def done(self) -> bool:
        return self.header is None or bool(self.header.dwFlags & 1)       # WHDR_DONE

    def close(self) -> None:
        if self.handle:
            self._mm.waveOutReset(self.handle)
            if self.header is not None:
                self._mm.waveOutUnprepareHeader(self.handle, self._ctypes.byref(self.header),
                                                self._ctypes.sizeof(self.header))
            self._mm.waveOutClose(self.handle)
        self.handle = self.header = None


class _Mci:
    """A file Windows decodes by itself (mp3 and friends).  To be used from one thread only."""
    _count = 0

    def __init__(self, path, at: float = 0.0, volume: int | None = None):
        self._ctypes, self._mm = _winmm()
        _Mci._count += 1
        self.alias = f"fltransfer{_Mci._count}"
        self.open = False
        self._send(f'open "{path}" type mpegvideo alias {self.alias}')
        self.open = True
        try:
            self._send(f"set {self.alias} time format milliseconds")
            length = int(self._send(f"status {self.alias} length") or 0)
            self.seconds = length / 1000
            if volume is not None:
                self._send(f"setaudio {self.alias} volume to {volume}")
            first = min(max(int(at * length), 0), max(length - 1, 0))
            self._send(f"play {self.alias} from {first}" if first else f"play {self.alias}")
        except AuditionError:
            self.close()
            raise

    def _send(self, command: str) -> str:
        buf = self._ctypes.create_unicode_buffer(256)
        err = self._mm.mciSendStringW(command, buf, 255, None)
        if err:
            text = self._ctypes.create_unicode_buffer(256)
            self._mm.mciGetErrorStringW(err, text, 255)
            raise AuditionError(text.value.strip() or f"MCI error {err}")
        return buf.value

    def position(self) -> float:
        try:
            return int(self._send(f"status {self.alias} position") or 0) / 1000
        except (AuditionError, ValueError):
            return 0.0

    def done(self) -> bool:
        try:
            return self._send(f"status {self.alias} mode") != "playing"
        except AuditionError:
            return True

    def close(self) -> None:
        if self.open:
            self.open = False
            try:
                self._send(f"close {self.alias}")
            except AuditionError:
                pass


def _open_wave(pcm: Pcm):
    if sys.platform != "win32":
        raise AuditionError("Auditioning is only available on Windows")
    return _WaveOut(pcm)


def _open_file(path, at: float = 0.0):
    if sys.platform != "win32":
        raise AuditionError("Auditioning is only available on Windows")
    try:
        return _Mci(path, at)
    except AuditionError as exc:
        kind = str(path).rsplit(".", 1)[-1].lower() if "." in str(path) else "these"
        raise AuditionError(f"{kind} files cannot be auditioned: the player built into Windows does not support them ({exc})") from None


# ---- what the window uses -----------------------------------------------------------------------
class Player:
    """One sample at a time.  start() and stop() at will; poll() from the UI thread keeps it going."""

    def __init__(self, open_wave=_open_wave, open_file=_open_file, read=decode):
        self._open_wave, self._open_file, self._read = open_wave, open_file, read
        self.state = "idle"             # idle | loading | playing | error
        self.path: str | None = None
        self.error = ""
        self.cut = False                # the sample goes on after what is played
        self._out = None
        self._at = 0.0
        self._token = 0
        self._ready: queue.Queue = queue.Queue()
        self._kept = None               # (path, file stamp, Pcm of the whole file): jumping around needs no reading

    @staticmethod
    def _stamp(path: str):
        try:
            info = os.stat(path)
            return info.st_mtime_ns, info.st_size
        except OSError:
            return None

    def start(self, path, at: float = 0.0) -> None:
        """Play the file from the fraction at (0..1) of its length."""
        self.stop()
        self._token += 1
        path = str(path)
        self.path, self._at, self.state = path, min(max(at, 0.0), 1.0), "loading"
        stamp = self._stamp(path)
        if self._kept and self._kept[:2] == (path, stamp) and stamp is not None:
            self._begin(self._kept[2].tail(self._at))
            return
        self._kept = None
        threading.Thread(target=self._load, args=(self._token, path, self._at, stamp), daemon=True).start()

    def _load(self, token: int, path: str, at: float, stamp) -> None:
        try:
            result = self._read(path, at=at) if at else self._read(path)
        except Exception as exc:  # noqa: BLE001 - a broken file ends up as a message, not a crash
            result = exc
        self._ready.put((token, result, stamp))

    def poll(self) -> str:
        if self.state == "loading":
            try:
                while True:
                    token, result, stamp = self._ready.get_nowait()
                    if token == self._token:
                        if isinstance(result, Pcm) and result.whole and result.data:
                            if stamp is not None:
                                self._kept = (self.path, stamp, result)
                            result = result.tail(self._at)
                        self._begin(result)
                        break
            except queue.Empty:
                pass
        elif self.state == "playing" and self._out.done():
            self.stop()
        return self.state

    def _begin(self, result) -> None:
        try:
            if isinstance(result, Exception):
                raise AuditionError(f"Cannot read this file: {result}")
            if result is None:
                self._out = self._open_file(self.path, self._at) if self._at else self._open_file(self.path)
                self.cut = False
            elif not result.data:
                raise AuditionError("This sample is empty")
            else:
                self._out, self.cut = self._open_wave(result), result.cut
            self.state = "playing"
        except AuditionError as exc:
            self._out, self.state, self.error = None, "error", str(exc)

    @property
    def fraction(self) -> float:
        """How far into the sample the sound is, 0..1."""
        if self.state == "loading":
            return self._at
        if self.state != "playing" or not self._out.seconds:
            return 0.0
        return max(0.0, min(1.0, self._out.position() / self._out.seconds))

    def stop(self) -> None:
        self._token += 1                # whatever is still loading is no longer wanted
        if self._out is not None:
            self._out.close()
        self._out, self.state, self.path, self.cut = None, "idle", None, False
