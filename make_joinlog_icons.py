"""make_joinlog_icons.py - draws the three join log status icons (flat round badges (no gloss) with a white glyph, 128x128 PNG):
  bl_jl_alert  red badge, exclamation mark   (alt detected)
  bl_jl_warn   amber badge, exclamation mark (suspicious)
  bl_jl_safe   green badge, check mark       (safe account)
Drawn at 4x and scaled down so the edges are smooth. Needs Pillow only.
Usage: python3 make_joinlog_icons.py   (writes ui_emojis/<n>.png and updates ui_emojis.py)
"""
import base64, math, os, re
from PIL import Image, ImageDraw

S, OUT = 4, 128
W = OUT * S
WHITE = (255, 255, 255, 255)
BADGES = {
    "bl_jl_alert": ((0xE8, 0x41, 0x4B), (0xC9, 0x2F, 0x3A), "bang"),
    "bl_jl_warn": ((0xF5, 0xA5, 0x24), (0xD9, 0x8A, 0x0C), "bang"),
    "bl_jl_safe": ((0x2E, 0xB8, 0x6B), (0x1F, 0x9A, 0x55), "tick"),
}


def line(d, pts, width, fill):
    """A polyline with round ends and round joints."""
    for a, b in zip(pts, pts[1:]):
        d.line([a, b], fill=fill, width=width)
    r = width / 2
    for x, y in pts:
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def badge(main, edge, glyph):
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = 6 * S
    d.ellipse((m, m, W - m, W - m), fill=edge + (255,))            # darker rim
    d.ellipse((m + 3 * S, m + 3 * S, W - m - 3 * S, W - m - 3 * S), fill=main + (255,))
    d = ImageDraw.Draw(img)
    c = W / 2
    if glyph == "bang":
        bw = 15 * S
        d.rounded_rectangle((c - bw / 2, 30 * S, c + bw / 2, 76 * S), radius=bw / 2, fill=WHITE)
        r = 9 * S
        d.ellipse((c - r, 88 * S - r, c + r, 88 * S + r), fill=WHITE)
    else:
        line(d, [(36 * S, 66 * S), (55 * S, 86 * S), (92 * S, 42 * S)], 16 * S, WHITE)
    return img.resize((OUT, OUT), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "ui_emojis"), exist_ok=True)
    entries = {}
    for name, (main_c, edge, glyph) in BADGES.items():
        path = os.path.join(here, "ui_emojis", name + ".png")
        badge(main_c, edge, glyph).save(path)
        entries[name] = base64.b64encode(open(path, "rb").read()).decode()
    py = os.path.join(here, "ui_emojis.py")
    s = open(py, encoding="utf-8").read()
    for name in list(entries) + ["bl_alert"]:  # replace earlier versions
        s = re.sub(r'    "%s": \(\n(?:        ".*"\)?,?\n)+' % re.escape(name), "", s)
    add = ""
    for name, b64 in entries.items():
        chunks = [b64[i:i + 100] for i in range(0, len(b64), 100)]
        add += f'    "{name}": (\n' + "\n".join(f'        "{c}"' + ("),") if i == len(chunks) - 1 else f'        "{c}"' for i, c in enumerate(chunks)) + "\n"
    s = s.rstrip()
    assert s.endswith("}")
    open(py, "w", encoding="utf-8").write(s[:-1] + add + "}\n")


if __name__ == "__main__":
    main()
