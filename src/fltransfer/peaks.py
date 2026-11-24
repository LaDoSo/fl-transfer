"""Waveform outline of an audio file for the clip preview. Pure standard library, WAV only."""
from __future__ import annotations

import struct
import sys
from array import array

PCM, FLOAT, EXTENSIBLE = 1, 3, 0xFFFE
MAX_BYTES = 400 * 1024 * 1024


class Wave:
    def __init__(self, columns: list[tuple[float, float]], seconds: float, channels: int, rate: int, bits: int):
        self.columns = columns      # (low, high) per column, each within -1..1
        self.seconds = seconds
        self.channels = channels
        self.rate = rate
        self.bits = bits


def _chunks(fh):
    head = fh.read(12)
    if len(head) < 12 or head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
        return
    while True:
        h = fh.read(8)
        if len(h) < 8:
            return
        size = struct.unpack("<I", h[4:])[0]
        yield h[:4], size, fh.tell()
        fh.seek(size + (size & 1), 1)


def read(path, columns: int = 400) -> Wave | None:
    """Return the outline of a WAV file, or None when the file is not a WAV this module can read."""
    try:
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
            code = {(PCM, 8): "B", (PCM, 16): "h", (PCM, 24): None, (PCM, 32): "i",
                    (FLOAT, 32): "f", (FLOAT, 64): "d"}.get((kind, bits), "?")
            if code == "?" or nch < 1 or block < 1 or rate < 1:
                return None
            pos, size = data
            fh.seek(0, 2)
            size = min(size, fh.tell() - pos, MAX_BYTES)
            frames = size // block
            if frames < 1:
                return Wave([], 0.0, nch, rate, bits)
            fh.seek(pos)
            raw = fh.read(frames * block)
    except OSError:
        return None
    frames = len(raw) // block
    if code is None:                        # 24 bit: the top byte of every sample is enough for a preview
        samples = array("b", raw[2::3])
        full = 128.0
    else:
        samples = array(code)
        samples.frombytes(raw[:frames * block])
        if sys.byteorder == "big":
            samples.byteswap()
        full = {"B": 128.0, "h": 32768.0, "i": 2147483648.0}.get(code, 1.0)
    offset = 128 if code == "B" else 0
    columns = max(1, min(columns, frames))
    out = []
    for i in range(columns):
        a = (i * frames // columns) * nch
        b = max(a + nch, ((i + 1) * frames // columns) * nch)
        part = samples[a:b]
        lo, hi = (min(part) - offset) / full, (max(part) - offset) / full
        out.append((max(-1.0, lo), min(1.0, hi)))
    return Wave(out, frames / rate, nch, rate, bits)
