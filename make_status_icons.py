"""make_status_icons.py - draws the three status emojis in front of every status message: bl_check (green tick), bl_cross (red cross),
bl_warn (yellow warning triangle). Flat, chunky, rounded look (tick with a darker lower edge, salmon-red cross, yellow triangle with an
orange rim and a dark blue-grey exclamation mark), 128x128 PNG with a transparent background.
Drawn at 8x and scaled down so the edges are smooth. Needs Pillow (only to draw them: the bot itself only reads ui_emojis.py).
Usage: python3 make_status_icons.py   (writes ui_emojis/bl_check.png, bl_cross.png, bl_warn.png and updates ui_emojis.py)
Run it AFTER make_ui_emojis.py (that script rewrites ui_emojis.py), together with make_wratio_icon.py / make_joinlog_icons.py.
The bot replaces the old emojis on its own at the next start (a changed picture replaces the old one).
"""
import base64, math, os, re

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT, S = 128, 8
N = OUT * S

GREEN, GREEN_SHADE = (147, 213, 115), (118, 190, 88)
RED = (254, 100, 100)
YELLOW, ORANGE, SLATE = (255, 210, 64), (244, 158, 40), (70, 87, 115)


def canvas():
    return Image.new("L", (N, N), 0)


def rounded(mask, r):
    """Round every corner of a mask: blur it, then cut it again at 50%."""
    blurred = mask.filter(ImageFilter.GaussianBlur(r * S))
    return blurred.point(lambda v: 255 if v >= 128 else 0)


def fill(mask, rgb):
    layer = Image.new("RGBA", (N, N), rgb + (255,))
    layer.putalpha(mask)
    return layer


def shift(mask, dx, dy):
    return ImageChops.offset(mask, int(dx * S), int(dy * S))


def check():
    m = canvas()
    d = ImageDraw.Draw(m)
    w = 24 * S
    a, b, c = (22 * S, 63 * S), (50 * S, 91 * S), (108 * S, 33 * S)
    d.line([a, b], fill=255, width=w)
    d.line([b, c], fill=255, width=w)
    r = w // 2
    d.ellipse([b[0] - r, b[1] - r, b[0] + r, b[1] + r], fill=255)  # rounded bottom
    body = rounded(m, 1.6)
    light = ImageChops.multiply(shift(body, -4, -5), body)         # the darker strip stays on the lower-right edge
    img = fill(body, GREEN_SHADE)
    return Image.alpha_composite(img, fill(light, GREEN))


def cross():
    m = canvas()
    d = ImageDraw.Draw(m)
    w = 36 * S
    lo, hi = 24 * S, 104 * S
    d.line([(lo, lo), (hi, hi)], fill=255, width=w)
    d.line([(hi, lo), (lo, hi)], fill=255, width=w)
    return fill(rounded(m, 3.2), RED)


def warn():
    def tri(scale):
        m = canvas()
        d = ImageDraw.Draw(m)
        cx, cy = 64 * S, 75.8 * S  # centre of the circle inside the triangle, so the orange rim is the same width all round
        pts = [(64 * S, 12 * S), (120 * S, 108 * S), (8 * S, 108 * S)]
        d.polygon([(cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in pts], fill=255)
        return rounded(m, 4.5)
    outer = tri(1.0)
    inner = tri(0.82)
    img = fill(outer, ORANGE)
    img = Image.alpha_composite(img, fill(inner, YELLOW))
    m = canvas()
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([58 * S, 44 * S, 70 * S, 82 * S], radius=6 * S, fill=255)   # the bar
    d.ellipse([57 * S, 87 * S, 71 * S, 101 * S], fill=255)                          # the dot
    return Image.alpha_composite(img, fill(m, SLATE))


ICONS = {"bl_check": check, "bl_cross": cross, "bl_warn": warn}


def put(py_path, name, png_path):
    b64 = base64.b64encode(open(png_path, "rb").read()).decode()
    s = open(py_path, encoding="utf-8").read()
    s = re.sub(r'    "%s": \(\n(?:        ".*"\)?,?\n)+' % re.escape(name), "", s)  # replace an earlier version
    chunks = [b64[i:i + 100] for i in range(0, len(b64), 100)]
    add = f'    "{name}": (\n' + "\n".join(
        f'        "{c}"' + ("),"  if i == len(chunks) - 1 else "") for i, c in enumerate(chunks)) + "\n"
    s = s.rstrip()
    assert s.endswith("}")
    open(py_path, "w", encoding="utf-8").write(s[:-1] + add + "}\n")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "ui_emojis"), exist_ok=True)
    for name, draw in ICONS.items():
        path = os.path.join(here, "ui_emojis", name + ".png")
        draw().resize((OUT, OUT), Image.LANCZOS).save(path, optimize=True)
        put(os.path.join(here, "ui_emojis.py"), name, path)


if __name__ == "__main__":
    main()
