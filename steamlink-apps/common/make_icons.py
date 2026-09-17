#!/usr/bin/env python3
"""Generate the 116x116 app icons the Steam Link menu wants.

Kept as a generator rather than a pair of committed base64 blobs so the icons
can actually be reviewed and adjusted. Standard library only: zlib and struct
are all a PNG needs, and the build host should not need Pillow for two icons.

  ./make_icons.py <outdir>      writes fix.png and status.png
"""
import math
import os
import struct
import sys
import zlib

SIZE = 116          # what the Steam Link menu expects
BG = (0x15, 0x18, 0x1E)
ACCENT_FIX = (0x8F, 0xD4, 0x60)     # green, matches "Fix it"
ACCENT_STAT = (0x66, 0xC0, 0xF4)    # blue, matches the accent in tvui
DIM = (0x92, 0x9A, 0xA8)


def blank():
    return [[BG for _ in range(SIZE)] for _ in range(SIZE)]


def blend(dst, src, a):
    """a in 0..1. Cheap alpha so edges are not jagged on a big TV."""
    if a <= 0:
        return dst
    if a >= 1:
        return src
    return tuple(int(round(d + (s - d) * a)) for d, s in zip(dst, src))


def rounded_panel(px, radius=22, inset=4):
    """A rounded square backdrop, so the icon reads as an app tile."""
    lo, hi = inset, SIZE - 1 - inset
    panel = (0x1E, 0x24, 0x2E)
    for y in range(SIZE):
        for x in range(SIZE):
            # distance outside the rounded rect, for a soft edge
            dx = max(lo + radius - x, 0, x - (hi - radius))
            dy = max(lo + radius - y, 0, y - (hi - radius))
            if x < lo or x > hi or y < lo or y > hi:
                continue
            d = math.hypot(dx, dy)
            cov = 1.0 if d <= radius - 0.5 else max(0.0, radius + 0.5 - d)
            px[y][x] = blend(px[y][x], panel, cov)


def ring(px, colour, cx, cy, r_outer, r_inner, start_deg, end_deg):
    """Arc of an annulus, anti-aliased on both radii."""
    for y in range(SIZE):
        for x in range(SIZE):
            dx, dy = x - cx, y - cy
            d = math.hypot(dx, dy)
            if d > r_outer + 1 or d < r_inner - 1:
                continue
            ang = math.degrees(math.atan2(-dy, dx)) % 360
            a0, a1 = start_deg % 360, end_deg % 360
            inside = (a0 <= ang <= a1) if a0 <= a1 else (ang >= a0 or ang <= a1)
            if not inside:
                continue
            cov = min(1.0, r_outer + 0.5 - d, d - (r_inner - 0.5))
            px[y][x] = blend(px[y][x], colour, max(0.0, cov))


def triangle(px, colour, pts):
    """Filled triangle, for the arrowhead on the refresh glyph."""
    (x0, y0), (x1, y1), (x2, y2) = pts

    def side(ax, ay, bx, by, x, y):
        return (bx - ax) * (y - ay) - (by - ay) * (x - ax)

    for y in range(SIZE):
        for x in range(SIZE):
            d0 = side(x0, y0, x1, y1, x + 0.5, y + 0.5)
            d1 = side(x1, y1, x2, y2, x + 0.5, y + 0.5)
            d2 = side(x2, y2, x0, y0, x + 0.5, y + 0.5)
            if (d0 >= 0 and d1 >= 0 and d2 >= 0) or (d0 <= 0 and d1 <= 0 and d2 <= 0):
                px[y][x] = colour


def bar(px, colour, x, y, w, h):
    for yy in range(y, min(y + h, SIZE)):
        for xx in range(x, min(x + w, SIZE)):
            px[yy][xx] = colour


def icon_fix():
    """A refresh arrow: the app restores a session rather than launching one."""
    px = blank()
    rounded_panel(px)
    cx = cy = SIZE / 2.0
    # Open ring with a gap at the top right, then an arrowhead closing it.
    ring(px, ACCENT_FIX, cx, cy, 38, 28, 300, 215)
    triangle(px, ACCENT_FIX, [(cx + 20, cy - 40), (cx + 46, cy - 29), (cx + 20, cy - 16)])
    return px


def icon_status():
    """Three rising bars: a read-only health screen."""
    px = blank()
    rounded_panel(px)
    base = 84
    for i, (h, colour) in enumerate([(22, DIM), (38, ACCENT_STAT), (54, ACCENT_STAT)]):
        bar(px, colour, 26 + i * 22, base - h, 14, h)
    bar(px, DIM, 22, base + 4, 72, 3)
    return px


def write_png(path, px):
    raw = bytearray()
    for row in px:
        raw.append(0)  # filter type 0
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)  # 8-bit truecolour
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)
    for name, maker in (("fix.png", icon_fix), ("status.png", icon_status)):
        path = os.path.join(outdir, name)
        n = write_png(path, maker())
        print(f"{path}  {n} bytes")


if __name__ == "__main__":
    main()
