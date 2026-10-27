"""Tiny image I/O + frame diff, stdlib only (zlib/struct), so nothing has to be installed on the workstation.

Frames are kept as raw rows (bytes) with bytes-per-pixel `bpp`. Two frames from the same capture backend are
compared row by row; unchanged rows cost one C-level bytes compare, so a static screen is cheap to check.
"""
import struct
import zlib


class Frame(object):
    __slots__ = ("w", "h", "bpp", "rows")

    def __init__(self, w, h, bpp, rows):
        self.w, self.h, self.bpp, self.rows = w, h, bpp, rows


# ---------------------------------------------------------------- BMP (screencapture+sips, ffmpeg, ImageMagick)
def read_bmp(data):
    if data[:2] != b"BM":
        raise ValueError("not a BMP")
    off = struct.unpack_from("<I", data, 10)[0]
    hsize = struct.unpack_from("<I", data, 14)[0]
    if hsize < 40:
        raise ValueError("unsupported BMP header")
    w, h, _planes, bits, comp = struct.unpack_from("<iiHHI", data, 18)
    if bits not in (24, 32) or comp not in (0, 3):
        raise ValueError("unsupported BMP format (bits=%d, compression=%d)" % (bits, comp))
    bpp = bits // 8
    stride = (w * bpp + 3) & ~3
    top_down = h < 0
    h = abs(h)
    rows = []
    for y in range(h):
        src = y if top_down else h - 1 - y
        s = off + src * stride
        rows.append(_swap_rb(data[s:s + w * bpp], bpp))
    return Frame(w, h, bpp, rows)


def _swap_rb(row, bpp):
    """BGR(A) <-> RGB(A) with slice assignment (C speed)."""
    out = bytearray(row)
    out[0::bpp] = row[2::bpp]
    out[2::bpp] = row[0::bpp]
    return bytes(out)


def write_bmp(frame):
    """24/32-bit bottom-up BMP from an RGB(A) frame (used by tests)."""
    bpp = frame.bpp
    stride = (frame.w * bpp + 3) & ~3
    pad = b"\0" * (stride - frame.w * bpp)
    body = b"".join(_swap_rb(frame.rows[y], bpp) + pad for y in range(frame.h - 1, -1, -1))
    hdr = struct.pack("<2sIHHI", b"BM", 54 + len(body), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, frame.w, frame.h, 1, bpp * 8, 0, len(body), 2835, 2835, 0, 0)
    return hdr + info + body


# ---------------------------------------------------------------- PNG
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(data):
    """8-bit gray / gray+alpha / RGB / RGBA, non-interlaced. Pure Python: fine for tests and offline replay."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w = 8, [], None
    while pos < len(data):
        ln, typ = struct.unpack_from(">I4s", data, pos)
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype, _c, _f, inter = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or inter != 0 or ctype not in (0, 2, 4, 6):
                raise ValueError("unsupported PNG (depth=%d type=%d interlace=%d)" % (depth, ctype, inter))
            bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
        elif typ == b"IDAT":
            idat.append(chunk)
        elif typ == b"IEND":
            break
        pos += 12 + ln
    if w is None:
        raise ValueError("PNG without IHDR")
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    p = 0
    for _ in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                ul = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], ul)) & 255
        elif ft != 0:
            raise ValueError("bad PNG filter %d" % ft)
        rows.append(bytes(line))
        prev = line
    return Frame(w, h, bpp, rows)


def write_png(frame):
    """Filter 0 + zlib: fast enough for evidence frames (frames are RGB(A); read_bmp converts from BGR(A))."""
    ctype = {1: 0, 2: 4, 3: 2, 4: 6}[frame.bpp]

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"".join(b"\0" + r for r in frame.rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", frame.w, frame.h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b""))


def read_image(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"BM":
        return read_bmp(data)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return read_png(data)
    raise ValueError("%s: not BMP/PNG" % path)


# ---------------------------------------------------------------- diff
def parse_rect(s):
    """'x,y,w,h' as fractions of the screen (0-1), e.g. menu-bar clock '0.9,0,0.1,0.03'."""
    v = [float(x) for x in s.split(",")]
    if len(v) != 4 or any(x < 0 or x > 1 for x in v):
        raise ValueError("rect must be x,y,w,h fractions in 0..1: %r" % s)
    return tuple(v)


def _px_rect(rect, w, h):
    x, y, rw, rh = rect
    return (int(x * w), int(y * h), int(min(w, round((x + rw) * w))), int(min(h, round((y + rh) * h))))


def _segments(w, bpp, y, masks_px):
    """Unmasked byte ranges of row y."""
    cut = sorted((x0, x1) for (x0, y0, x1, y1) in masks_px if y0 <= y < y1)
    segs, x = [], 0
    for x0, x1 in cut:
        if x0 > x:
            segs.append((x * bpp, x0 * bpp))
        x = max(x, x1)
    if x < w:
        segs.append((x * bpp, w * bpp))
    return segs


def _first_diff(a, b, s, e):
    lo, hi = s, e          # invariant: a[s:lo] == b[s:lo]; a[s:hi] != b[s:hi]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if a[s:mid] == b[s:mid]:
            lo = mid
        else:
            hi = mid
    return hi - 1


def _last_diff(a, b, s, e):
    lo, hi = s, e          # invariant: a[hi:e] == b[hi:e]; a[lo:e] != b[lo:e]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if a[mid:e] == b[mid:e]:
            hi = mid
        else:
            lo = mid
    return lo


def diff(prev, cur, masks=(), tol=0):
    """Compare two frames. masks = fractional rects to ignore; tol = per-byte tolerance (0 = exact).
    Returns {changed, rows, bbox:(x0,y0,x1,y1) px or None, area: bbox fraction of the screen}."""
    if prev is None:
        return {"changed": False, "rows": 0, "bbox": None, "area": 0.0, "first": True}
    if (prev.w, prev.h, prev.bpp) != (cur.w, cur.h, cur.bpp):
        return {"changed": True, "rows": cur.h, "bbox": (0, 0, cur.w, cur.h), "area": 1.0, "resized": True}
    masks_px = [_px_rect(m, cur.w, cur.h) for m in masks]
    bpp = cur.bpp
    rows, x0, y0, x1, y1 = 0, None, None, None, None
    seg_cache = {}
    for y in range(cur.h):
        a, b = prev.rows[y], cur.rows[y]
        if a == b:
            continue
        key = tuple(m for m in masks_px if m[1] <= y < m[3])
        segs = seg_cache.get(key)
        if segs is None:
            segs = seg_cache[key] = _segments(cur.w, bpp, y, masks_px)
        hit = False
        for s, e in segs:
            if a[s:e] == b[s:e]:
                continue
            fs, ls = _first_diff(a, b, s, e), _last_diff(a, b, s, e)
            if tol:
                if not any(abs(a[i] - b[i]) > tol for i in range(fs, ls + 1)):
                    continue
            hit = True
            px0, px1 = fs // bpp, ls // bpp + 1
            x0 = px0 if x0 is None else min(x0, px0)
            x1 = px1 if x1 is None else max(x1, px1)
        if hit:
            rows += 1
            y0 = y if y0 is None else y0
            y1 = y + 1
    if not rows:
        return {"changed": False, "rows": 0, "bbox": None, "area": 0.0}
    area = (x1 - x0) * (y1 - y0) / float(cur.w * cur.h)
    return {"changed": True, "rows": rows, "bbox": (x0, y0, x1, y1), "area": round(area, 6)}


def bbox_inside(bbox, rect, w, h):
    """True if the pixel bbox lies fully inside the fractional rect."""
    rx0, ry0, rx1, ry1 = _px_rect(rect, w, h)
    x0, y0, x1, y1 = bbox
    return rx0 <= x0 and ry0 <= y0 and x1 <= rx1 and y1 <= ry1
