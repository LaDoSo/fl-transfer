"""Dev check: is a channel in one project the same as a channel in another, event by event?

  python tools/samechannel.py <a.flp> <iid> <b.flp> <iid>

For a VST the plugin state sits in event 213 as a list of (id, size, data) records; record 53 is
what the plugin itself handed to FL.  A plugin may write its state differently each time it is
asked (Serum does), so that record is also compared after unpacking where it is zlib data.
"""
import os, struct, sys, zlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from fltransfer.flp import Project
from fltransfer.transfer import Layout


def block(path, iid):
    p = Project.load(path)
    _, a, b = next(c for c in Layout.of(p).channels if c[0] == iid)
    return p.events[a + 1:b]                      # without the leading 64 <iid>


def records(data):
    out, i = {}, 4
    while i + 12 <= len(data):
        rid, size = struct.unpack_from("<IQ", data, i)
        if size > len(data) - i - 12:
            return None
        out[rid] = data[i + 12:i + 12 + size]
        i += 12 + size
    return out


def differing(x, y):
    n = min(len(x), len(y))
    return sum(1 for i in range(n) if x[i] != y[i]) + abs(len(x) - len(y))


def unpacked(raw):
    """Every zlib stream found inside raw, unpacked and joined (empty when there is none)."""
    out, i = b"", 0
    while True:
        i = raw.find(b"\x78", i)
        if i < 0:
            return out
        try:
            d = zlib.decompressobj()
            piece = d.decompress(raw[i:])
            if len(piece) > 64:
                out += piece
                i = len(raw) - len(d.unused_data)
                continue
        except zlib.error:
            pass
        i += 1


a, b = block(sys.argv[1], int(sys.argv[2])), block(sys.argv[3], int(sys.argv[4]))
print("events:", len(a), "vs", len(b), "| same event ids in the same order:", [e.id for e in a] == [e.id for e in b])
for x, y in zip(a, b):
    if x.id != y.id or x.data == y.data:
        continue
    if x.id == 213 and records(x.data) and records(y.data):
        ra, rb = records(x.data), records(y.data)
        print("  event 213 (plugin wrapper), records:", sorted(ra), "vs", sorted(rb))
        for rid in sorted(set(ra) | set(rb)):
            p, q = ra.get(rid, b""), rb.get(rid, b"")
            if p == q:
                continue
            line = "    record %d: %d vs %d bytes, %d bytes differ" % (rid, len(p), len(q), differing(p, q))
            up, uq = unpacked(p), unpacked(q)
            if up or uq:
                line += " | unpacked: %d vs %d bytes, %d differ" % (len(up), len(uq), differing(up, uq))
            print(line, "| head", p[:16].hex(), "->", q[:16].hex())
            if (up or uq) and len(up) == len(uq):
                for i in range(len(up)):
                    if up[i] != uq[i]:
                        print("      offset %d of the unpacked state: %s -> %s | around: %r" % (
                            i, up[max(0, i - 8):i + 8].hex(), uq[max(0, i - 8):i + 8].hex(), up[max(0, i - 40):i + 24]))
    else:
        print("  event %d: %d vs %d bytes, %d differ | %s -> %s" % (x.id, len(x.data), len(y.data), differing(x.data, y.data),
                                                                 x.data[:20].hex(), y.data[:20].hex()))
