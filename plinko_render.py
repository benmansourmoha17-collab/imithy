"""Draws the plinko board for the ,plinko panel: a dark card with a triangle of pegs, a yellow ball and a row of slot boxes.

No Discord in here (only Pillow), so it can be tested on its own:  python plinko_render.py  writes plinko_test.gif / plinko_test.png.

  * render_gif(table, path)  -> bytes   the ball falling peg by peg into its slot (the animated image)
  * render_png(table, slot)  -> bytes   the board after the drop: the slot the ball landed in has a yellow border

`table` is one row of gambling.PLINKO_TABLES (9 multipliers), `path` is a tuple of 0 (left) / 1 (right), one per row.
Slots paying 1x or more are green, slots paying less are red, like in the reference video.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(HERE, "fonts", "DejaVuSans-Bold.ttf")

WIDTH, HEIGHT = 760, 440
MARGIN = 28
ROWS = 8                      # rows of pegs = the number of left / right choices (gambling.PLINKO_ROWS)
SLOTS = ROWS + 1
SPACING = (WIDTH - 2 * MARGIN) / SLOTS   # distance between two pegs / two slots
ROW_GAP = 33
TOP_Y = 66                    # y of the single top peg
PEG_R = 4.2
BALL_R = 9.5
SLOT_TOP = TOP_Y + (ROWS - 1) * ROW_GAP + 52
SLOT_H = 62
SLOT_W = SPACING - 8

BG = (31, 35, 34)
PEG = (228, 230, 229)
BALL = (254, 205, 82)
GOOD_FILL, GOOD_LINE = (21, 52, 47), (30, 74, 66)
BAD_FILL, BAD_LINE = (60, 38, 40), (84, 54, 56)
HIT_FILL, HIT_LINE = (51, 54, 40), BALL
TEXT, HIT_TEXT = (240, 242, 241), BALL

FRAME_MS = 42                 # time of one GIF frame
HOLD_MS = 900                 # the last frame stays this long


def _font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def peg_xy(row, index):
    """Centre of peg `index` (0..row) in peg row `row` (0 = the single peg at the top)."""
    return WIDTH / 2 + (index - row / 2) * SPACING, TOP_Y + row * ROW_GAP


def slot_cx(slot):
    return MARGIN + SPACING / 2 + slot * SPACING


def label(multiplier):
    return f"{multiplier:g}X"


def _base(table, hit=None):
    """The board without the ball: background, pegs and slot boxes (`hit` = the slot to outline in yellow)."""
    ss = 3  # drawn 3x bigger and shrunk afterwards, so the circles and the text have smooth edges
    img = Image.new("RGB", (WIDTH * ss, HEIGHT * ss), BG)
    d = ImageDraw.Draw(img)
    for row in range(ROWS):
        for i in range(row + 1):
            x, y = peg_xy(row, i)
            d.ellipse([(x - PEG_R) * ss, (y - PEG_R) * ss, (x + PEG_R) * ss, (y + PEG_R) * ss], fill=PEG)
    font = _font(19 * ss)
    for k, mult in enumerate(table):
        cx = slot_cx(k)
        box = [(cx - SLOT_W / 2) * ss, SLOT_TOP * ss, (cx + SLOT_W / 2) * ss, (SLOT_TOP + SLOT_H) * ss]
        good = mult >= 1
        fill, line = (GOOD_FILL, GOOD_LINE) if good else (BAD_FILL, BAD_LINE)
        text = TEXT
        width = 1
        if k == hit:
            fill, line, text, width = HIT_FILL, HIT_LINE, HIT_TEXT, 2
        d.rounded_rectangle(box, radius=6 * ss, fill=fill, outline=line, width=width * ss)
        d.text((cx * ss, (SLOT_TOP + SLOT_H - 17) * ss), label(mult), font=font, fill=text, anchor="mm")
    return img.resize((WIDTH, HEIGHT), Image.LANCZOS)


def _ball(img, cx, cy):
    """Draw the ball with a smooth edge at a fractional position."""
    ss = 6
    r = BALL_R
    left, top = int(cx - r - 2), int(cy - r - 2)
    size = int(2 * r + 6)
    mask = Image.new("L", (size * ss, size * ss), 0)
    ox, oy = (cx - left) * ss, (cy - top) * ss
    ImageDraw.Draw(mask).ellipse([ox - r * ss, oy - r * ss, ox + r * ss, oy + r * ss], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    img.paste(Image.new("RGB", (size, size), BALL), (left, top), mask)


def _rest(row, index):
    """Where the ball sits when it rests on top of a peg."""
    x, y = peg_xy(row, index)
    return x, y - PEG_R - BALL_R + 1


def _ball_positions(path):
    """Every position of the ball: it drops onto the top peg, hops from peg to peg following the path and ends in its slot."""
    pts = []
    x0, y0 = _rest(0, 0)
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):       # the drop onto the first peg
        e = t * t
        pts.append((x0, y0 - 34 * (1 - e)))
    index = 0
    for row in range(ROWS):
        index += path[row]                       # after a bounce on row `row` the ball is at this peg of the next row
        ax, ay = _rest(row, index - path[row])
        if row + 1 < ROWS:
            bx, by = _rest(row + 1, index)
        else:
            bx, by = slot_cx(index), SLOT_TOP + 20
        steps = 5
        for s in range(1, steps + 1):
            t = s / steps
            hop = 11 * 4 * t * (1 - t) if row + 1 < ROWS else 8 * 4 * t * (1 - t)
            pts.append((ax + (bx - ax) * t, ay + (by - ay) * (t * t * 0.6 + t * 0.4) - hop))
    return pts, index


def render_png(table, slot):
    """The board with the ball sitting in `slot` (outlined in yellow)."""
    img = _base(table, hit=slot)
    _ball(img, slot_cx(slot), SLOT_TOP + 20)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def render_gif(table, path):
    """The animated drop. Returns (bytes, seconds the animation takes)."""
    pts, slot = _ball_positions(path)
    plain = _base(table)
    hit = _base(table, hit=slot)
    frames = []
    for n, (x, y) in enumerate(pts):
        last = n >= len(pts) - 3  # the last frames show the slot outlined, like the picture that follows
        img = (hit if last else plain).copy()
        _ball(img, x, y)
        frames.append(img)
    palette = frames[-1].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    durations = [FRAME_MS] * len(frames)
    durations[-1] = HOLD_MS
    out = io.BytesIO()
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=False, disposal=1)
    return out.getvalue(), (sum(durations)) / 1000


if __name__ == "__main__":
    import sys
    sys.path.insert(0, HERE)
    import gambling
    table = gambling.PLINKO_TABLES["high"]
    path = (0, 1, 1, 0, 1, 0, 1, 0)
    data, secs = render_gif(table, path)
    with open("plinko_test.gif", "wb") as f:
        f.write(data)
    with open("plinko_test.png", "wb") as f:
        f.write(render_png(table, gambling.plinko_slot(path)))
    print(f"gif {len(data) / 1024:.0f} KB, {secs:.1f} s, slot {gambling.plinko_slot(path) + 1}/9")
