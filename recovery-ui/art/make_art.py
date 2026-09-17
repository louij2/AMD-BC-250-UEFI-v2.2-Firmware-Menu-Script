#!/usr/bin/env python3
"""Generate Steam library artwork for the BC-250 Recovery shortcut.

Writes portrait, wide, hero, logo and icon PNGs at Steam's expected sizes.
Rendered at 2x and downsampled for clean edges. Needs Pillow.
Usage: make_art.py OUTPUT_DIR [FONT_PATH]
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 2  # supersampling factor

BG_TOP = (9, 11, 15)
BG_BOTTOM = (26, 7, 9)
RED = (237, 28, 36)
RED_DIM = (120, 16, 20)
CHIP = (21, 24, 30)
PIN = (150, 156, 166)
WHITE = (242, 244, 247)
GREY = (150, 156, 166)


def font(path, size, index):
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.truetype(path, size)


def gradient(w, h):
    img = Image.new("RGB", (w, h), BG_TOP)
    px = img.load()
    for y in range(h):
        for x in range(0, w):
            t = min(1.0, max(0.0, (y / h) * 0.75 + (x / w) * 0.25))
            px[x, y] = tuple(int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3))
    return img


def traces(img, spacing, alpha=18):
    """Faint circuit-board grid for texture."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = (255, 255, 255, alpha)
    lw = max(1, spacing // 40)
    for x in range(0, w, spacing):
        d.line([(x, 0), (x, h)], fill=col, width=lw)
    for y in range(0, h, spacing):
        d.line([(0, y), (w, y)], fill=col, width=lw)
    for x in range(0, w, spacing):
        for y in range(0, h, spacing):
            if (x // spacing + y // spacing) % 3 == 0:
                r = lw * 3
                d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, alpha * 2))
    img.paste(layer, (0, 0), layer)


def glow(img, cx, cy, radius, color=RED, strength=140):
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=color + (strength,))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.55))
    img.paste(layer, (0, 0), layer)


def chip(img, cx, cy, size, pins=6):
    """A processor package with a restart arrow inside."""
    d = ImageDraw.Draw(img)
    half = size / 2
    pin_len = size * 0.11
    pin_w = size * 0.045
    gap = size * 0.8 / pins
    start = -size * 0.4 + gap / 2
    for i in range(pins):
        o = start + i * gap
        # top / bottom
        d.rounded_rectangle([cx + o - pin_w / 2, cy - half - pin_len, cx + o + pin_w / 2, cy - half + 2],
                            radius=pin_w / 3, fill=PIN)
        d.rounded_rectangle([cx + o - pin_w / 2, cy + half - 2, cx + o + pin_w / 2, cy + half + pin_len],
                            radius=pin_w / 3, fill=PIN)
        # left / right
        d.rounded_rectangle([cx - half - pin_len, cy + o - pin_w / 2, cx - half + 2, cy + o + pin_w / 2],
                            radius=pin_w / 3, fill=PIN)
        d.rounded_rectangle([cx + half - 2, cy + o - pin_w / 2, cx + half + pin_len, cy + o + pin_w / 2],
                            radius=pin_w / 3, fill=PIN)

    d.rounded_rectangle([cx - half, cy - half, cx + half, cy + half],
                        radius=size * 0.09, fill=CHIP, outline=RED, width=max(2, int(size * 0.028)))
    inner = size * 0.78
    d.rounded_rectangle([cx - inner / 2, cy - inner / 2, cx + inner / 2, cy + inner / 2],
                        radius=size * 0.06, outline=RED_DIM, width=max(1, int(size * 0.01)))

    # restart arrow: arc from 50deg to 330deg, arrowhead at the 50deg end
    r = size * 0.24
    stroke = max(3, int(size * 0.06))
    d.arc([cx - r, cy - r, cx + r, cy + r], start=50, end=330, fill=WHITE, width=stroke)
    ang = math.radians(50)
    ex, ey = cx + r * math.cos(ang), cy + r * math.sin(ang)
    head = size * 0.11
    # tangent direction at the arc start, pointing against the arc's sweep
    tx, ty = math.sin(ang), -math.cos(ang)
    nx, ny = math.cos(ang), math.sin(ang)
    tip = (ex + tx * head, ey + ty * head)
    left = (ex + nx * head * 0.62, ey + ny * head * 0.62)
    right = (ex - nx * head * 0.62, ey - ny * head * 0.62)
    d.polygon([tip, left, right], fill=WHITE)
    # power dot in the centre
    dot = size * 0.045
    d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=RED)


def text_center(d, x, y, s, f, fill, spacing=0):
    if spacing:
        widths = [d.textlength(ch, font=f) for ch in s]
        total = sum(widths) + spacing * (len(s) - 1)
        cx = x - total / 2
        for ch, cw in zip(s, widths):
            d.text((cx, y), ch, font=f, fill=fill, anchor="lm")
            cx += cw + spacing
    else:
        d.text((x, y), s, font=f, fill=fill, anchor="mm")


def text_left(d, x, y, s, f, fill, spacing=0):
    cx = x
    for ch in s:
        d.text((cx, y), ch, font=f, fill=fill, anchor="lm")
        cx += d.textlength(ch, font=f) + spacing


def finish(img, size, path):
    img.convert("RGB").resize(size, Image.LANCZOS).save(path, optimize=True)


def portrait(out, fp):
    w, h = 600 * SS, 900 * SS
    img = gradient(w, h)
    traces(img, 60 * SS)
    glow(img, w / 2, h * 0.38, 240 * SS)
    chip(img, w / 2, h * 0.38, 300 * SS)
    d = ImageDraw.Draw(img)
    text_center(d, w / 2, h * 0.73, "BC-250", font(fp, 118 * SS, 8), WHITE)
    text_center(d, w / 2, h * 0.83, "RECOVERY", font(fp, 44 * SS, 2), RED, spacing=14 * SS)
    d.line([(w * 0.3, h * 0.905), (w * 0.7, h * 0.905)], fill=RED_DIM, width=3 * SS)
    text_center(d, w / 2, h * 0.94, "RESET  ·  REBOOT  ·  FIRMWARE", font(fp, 20 * SS, 5), GREY)
    finish(img, (600, 900), out / "portrait.png")


def wide(out, fp):
    w, h = 920 * SS, 430 * SS
    img = gradient(w, h)
    traces(img, 46 * SS)
    glow(img, w * 0.24, h / 2, 190 * SS)
    chip(img, w * 0.24, h / 2, 210 * SS)
    d = ImageDraw.Draw(img)
    text_left(d, w * 0.46, h * 0.42, "BC-250", font(fp, 112 * SS, 8), WHITE)
    text_left(d, w * 0.465, h * 0.64, "RECOVERY", font(fp, 42 * SS, 2), RED, spacing=12 * SS)
    finish(img, (920, 430), out / "wide.png")


def hero(out, fp):
    w, h = 1920 * SS, 620 * SS
    img = gradient(w, h)
    traces(img, 64 * SS, alpha=14)
    glow(img, w * 0.78, h * 0.5, 330 * SS, strength=110)
    chip(img, w * 0.78, h * 0.5, 380 * SS, pins=7)
    # darken the left so Steam's logo overlay stays readable
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for x in range(int(w * 0.6)):
        a = int(150 * (1 - x / (w * 0.6)))
        sd.line([(x, 0), (x, h)], fill=(0, 0, 0, a))
    img.paste(shade, (0, 0), shade)
    finish(img, (1920, 620), out / "hero.png")


def logo(out, fp):
    w, h = 1200 * SS, 400 * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    big = font(fp, 190 * SS, 8)
    small = font(fp, 64 * SS, 2)
    d.text((w / 2, h * 0.40), "BC-250", font=big, fill=WHITE + (255,), anchor="mm")
    text_center(d, w / 2, h * 0.82, "RECOVERY", small, RED + (255,), spacing=22 * SS)
    bbox = img.getbbox()
    img = img.crop(bbox)
    img = img.resize((img.width // SS, img.height // SS), Image.LANCZOS)
    img.save(out / "logo.png", optimize=True)


def icon(out, fp):
    w = h = 256 * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bg = gradient(w, h).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=56 * SS, fill=255)
    img.paste(bg, (0, 0), mask)
    chip(img, w / 2, h / 2, 150 * SS)
    img = img.resize((256, 256), Image.LANCZOS)
    img.save(out / "icon.png", optimize=True)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    fp = sys.argv[2] if len(sys.argv) > 2 else "/System/Library/Fonts/Avenir Next.ttc"
    out.mkdir(parents=True, exist_ok=True)
    for fn in (portrait, wide, hero, logo, icon):
        fn(out, fp)
        print("wrote", fn.__name__)


if __name__ == "__main__":
    main()
