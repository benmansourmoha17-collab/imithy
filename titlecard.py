"""Animated reputation titles: the text a member picks, drawn as a small looping GIF with an effect.

Discord can't animate text, so a title is an image: a dark name plate with the text in it (rainbow, flow, neon, glitch, gold
shine or fire). It is drawn ONCE when the member sets or changes the title (in a worker thread, about 0.2 seconds) and saved
as a file named after the text + effect + color. Showing it later only sends that file, no drawing, so it costs no CPU.

Needs Pillow (in requirements.txt) and the two fonts in fonts/. Without either, available() is False and the bot shows the
title as plain styled text instead. Everything here works without Discord.
"""
import colorsys
import hashlib
import math
import os
import random
import unicodedata

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
except Exception:  # Pillow isn't installed: the bot falls back to plain text titles
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_MAIN = os.path.join(HERE, "fonts", "Poppins-Bold.ttf")      # looks best, Latin letters only
FONT_WIDE = os.path.join(HERE, "fonts", "DejaVuSans-Bold.ttf")   # Cyrillic, Greek and lots of symbols
W, H = 480, 96
FRAMES = 30
FRAME_MS = 45
MAX_CHARS = 20
RENDER_VERSION = "3"  # bump it to redraw every cached title after the look changes

EFFECTS = {
    "rainbow": "Rainbow",
    "flow": "Flow",
    "neon": "Neon",
    "glitch": "Glitch",
    "gold": "Gold shine",
    "fire": "Fire",
}
DEFAULT_COLOR = {"flow": 0xA78BFA, "neon": 0x00E5FF}
USES_COLOR = ("flow", "neon")  # the other effects have their own colors

_BAD_WORDS = ("@everyone", "@here", "<@", "http", "www.", ".gg", "discord.", ".com", ".net", ".org")


def available():
    return Image is not None and os.path.isfile(FONT_MAIN) and os.path.isfile(FONT_WIDE)


# ---- the text -----------------------------------------------------------------------------------------
def _allowed_range(code):
    """Latin / Greek / Cyrillic, extended Latin, and the punctuation + symbol blocks. No Arabic, Hebrew, Indic, CJK or emoji:
    those need text shaping or other fonts, so they would draw wrong."""
    return code < 0x0590 or 0x1E00 <= code <= 0x1FFF or 0x2010 <= code <= 0x2BFF


_notdef_cache = {}


def _glyph(font, ch):
    """(the character drawn on a small picture as bytes, its bounding box or None when nothing was drawn)."""
    im = Image.new("L", (90, 90), 0)
    ImageDraw.Draw(im).text((12, 12), ch, font=font, fill=255)
    return im.tobytes(), im.getbbox()


def _supports(font_path, ch):
    """True when the font has a real picture for this character (not its 'missing character' box)."""
    key = (font_path, ch)
    if key in _notdef_cache:
        return _notdef_cache[key]
    font = ImageFont.truetype(font_path, 40)
    notdef, _box = _glyph(font, "\uffff")
    picture, box = _glyph(font, ch)
    ok = picture != notdef and box is not None
    _notdef_cache[key] = ok
    return ok


def clean_title(text):
    """(text, None) when the title can be drawn, else (None, what is wrong in one sentence)."""
    text = " ".join(str(text or "").split())
    if not text:
        return None, "Write the text of your title."
    if len(text) > MAX_CHARS:
        return None, f"A title can have at most {MAX_CHARS} characters."
    low = text.lower()
    if any(bad in low for bad in _BAD_WORDS):
        return None, "No mentions or links in a title."
    if not available():
        return text, None
    for ch in text:
        cat = unicodedata.category(ch)
        if ch == " ":
            continue
        if cat[0] in "CZM" or not _allowed_range(ord(ch)) or not (_supports(FONT_MAIN, ch) or _supports(FONT_WIDE, ch)):
            return None, f"`{ch}` can't be drawn. Use letters, numbers and simple symbols (no emoji, Arabic or Chinese)."
    return text, None


def parse_color(text, default=None):
    """'#ff00aa', 'ff00aa', '#f0a' or a color word -> 0xRRGGBB, or `default` when it isn't one."""
    names = {"red": 0xFF3B4E, "orange": 0xFF8A1F, "yellow": 0xFFD60A, "green": 0x2EE66B, "cyan": 0x00E5FF, "blue": 0x3B82F6,
             "purple": 0xA78BFA, "violet": 0x8B5CF6, "pink": 0xFF4FA3, "white": 0xF2F2F2, "gold": 0xFFC83D, "teal": 0x14D8C4}
    t = str(text or "").strip().lower().lstrip("#")
    if t in names:
        return names[t]
    if len(t) == 3 and all(c in "0123456789abcdef" for c in t):
        t = "".join(c * 2 for c in t)
    if len(t) == 6 and all(c in "0123456789abcdef" for c in t):
        return int(t, 16)
    return default


def title_key(text, effect, color):
    """The file name (without .gif) of a title: the same text + effect + color always gives the same file."""
    raw = f"{RENDER_VERSION}|{text}|{effect}|{color if effect in USES_COLOR else 0}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def plain_title(text, effect=None):
    """The title as plain text for places that can't show a picture (and when Pillow is missing)."""
    return f"『 {text} 』"


# ---- drawing ------------------------------------------------------------------------------------------
def _pick_font(text):
    for path in (FONT_MAIN, FONT_WIDE):
        if all(ch == " " or _supports(path, ch) for ch in text):
            return path
    return FONT_WIDE


def _text_mask(text):
    """The text as a white-on-black anti-aliased picture (L), centered, as big as fits the plate."""
    path = _pick_font(text)
    scale = 3
    size = 60
    while size > 14:
        font = ImageFont.truetype(path, size * scale)
        box = font.getbbox(text)
        if (box[2] - box[0]) <= (W - 56) * scale and (box[3] - box[1]) <= (H - 30) * scale:
            break
        size -= 2
    font = ImageFont.truetype(path, size * scale)
    big = Image.new("L", (W * scale, H * scale), 0)
    ImageDraw.Draw(big).text((W * scale // 2, H * scale // 2), text, font=font, fill=255, anchor="mm")
    return big.resize((W, H), Image.LANCZOS)


def _plate():
    """The dark name plate: a soft vertical gradient, a thin lighter border."""
    plate = Image.new("RGB", (W, H))
    px = plate.load()
    for y in range(H):
        t = y / (H - 1)
        base = (int(22 + 8 * t), int(23 + 8 * t), int(28 + 10 * t))
        for x in range(W):
            px[x, y] = base
    d = ImageDraw.Draw(plate)
    d.rectangle((0, 0, W - 1, H - 1), outline=(52, 55, 63))
    return plate


def _hsv(h, s=0.78, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def _rgb(value):
    return (value >> 16) & 255, (value >> 8) & 255, value & 255


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _strip(color_at):
    """A color strip 2 x W wide whose colors repeat every W pixels, so a window slid along it loops without a jump."""
    row = Image.new("RGB", (W * 2, 1))
    px = row.load()
    for x in range(W * 2):
        px[x, 0] = color_at((x % W) / W)
    return row


def _window(strip, offset):
    return strip.crop((offset, 0, offset + W, 1)).resize((W, H), Image.NEAREST)


def _paste(img, color, mask):
    """Put `color` (a color tuple or a picture) on `img` wherever `mask` is white."""
    if isinstance(color, tuple):
        color = Image.new("RGB", img.size, color)
    img.paste(color, (0, 0), mask)


def _twinkles(img, t, count, seed, color, spread=(24, W - 24, 10, H - 10)):
    """Small four-point stars that grow and fade, each at its own rhythm. Loops cleanly: every star blinks a whole number of times."""
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    for _ in range(count):
        x, y = rnd.randint(spread[0], spread[1]), rnd.randint(spread[2], spread[3])
        blinks, phase, size = rnd.choice((1, 1, 2)), rnd.random(), rnd.randint(3, 7)
        k = max(0.0, math.sin(math.pi * ((t * blinks + phase) % 1.0))) ** 3
        r = size * k
        if r < 0.8:
            continue
        c = _mix((40, 40, 46), color, k)
        d.line((x - r, y, x + r, y), fill=c, width=1)
        d.line((x, y - r, x, y + r), fill=c, width=1)
        d.point((x, y), fill=(255, 255, 255))


def _embers(img, t, count, seed):
    """Little sparks drifting up (fire). Each one makes a whole trip per loop, so the loop is seamless."""
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    for _ in range(count):
        x0 = rnd.randint(30, W - 30)
        sway, phase, size = rnd.uniform(3, 9), rnd.random(), rnd.choice((1, 1, 2))
        p = (t + rnd.random()) % 1.0
        y = H - 6 - p * (H - 8)
        x = x0 + math.sin((p + phase) * math.tau) * sway
        fade = 1 - p
        c = _mix((30, 20, 20), (255, int(150 + 80 * fade), int(40 * fade)), fade)
        d.ellipse((x - size, y - size, x + size, y + size), fill=c)


def _frames(text, effect, color):
    mask = _text_mask(text)
    plate = _plate()
    seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
    rnd = random.Random(seed)
    glow_big = mask.filter(ImageFilter.GaussianBlur(9))
    glow_small = mask.filter(ImageFilter.GaussianBlur(3.5))
    frames = []
    rgb = _rgb(color if color is not None else DEFAULT_COLOR.get(effect, 0xFFFFFF))

    if effect == "rainbow":
        strip = _strip(lambda u: _hsv(u))
        for i in range(FRAMES):
            t = i / FRAMES
            layer = _window(strip, int(t * W))
            img = Image.composite(layer, plate, glow_big.point(lambda v: int(v * 0.30)))
            _paste(img, layer, mask)
            frames.append(img)

    elif effect == "flow":
        c1 = rgb
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in c1))
        c2 = _hsv(h + 0.16, min(1.0, s + 0.05), 1.0)
        c3 = _mix(_hsv(h - 0.10, 0.45, 1.0), (255, 255, 255), 0.35)
        stops = (c1, c2, c3, c2)

        def flow_at(u):
            p = u * len(stops)
            i = int(p) % len(stops)
            return _mix(stops[i], stops[(i + 1) % len(stops)], (math.sin((p - int(p)) * math.pi - math.pi / 2) + 1) / 2)
        strip = _strip(flow_at)
        for i in range(FRAMES):
            t = i / FRAMES
            layer = _window(strip, int(t * W))
            img = Image.composite(layer, plate, glow_big.point(lambda v: int(v * 0.22)))
            _paste(img, layer, mask)
            frames.append(img)

    elif effect == "neon":
        core = _mix(rgb, (255, 255, 255), 0.72)
        flicker = {rnd.randint(2, FRAMES - 3) for _ in range(2)}
        for i in range(FRAMES):
            t = i / FRAMES
            pulse = 0.62 + 0.38 * math.sin(t * math.tau)
            if i in flicker:
                pulse *= 0.45
            img = Image.composite(Image.new("RGB", (W, H), rgb), plate, glow_big.point(lambda v, p=pulse: int(v * 0.85 * p)))
            img = Image.composite(Image.new("RGB", (W, H), rgb), img, glow_small.point(lambda v, p=pulse: int(v * 0.9 * p)))
            _paste(img, _mix(rgb, core, 0.55 + 0.45 * pulse), mask)
            _twinkles(img, t, 5, seed, rgb)
            frames.append(img)

    elif effect == "glitch":
        red, cyan = (255, 30, 80), (0, 255, 240)
        jitter = {i: (rnd.choice((-1, 1)) * rnd.randint(3, 8), rnd.randint(0, H - 30), rnd.randint(8, 24)) for i in rnd.sample(range(2, FRAMES - 2), 6)}
        for i in range(FRAMES):
            img = plate.copy()
            dx, band_y, band_h = jitter.get(i, (0, 0, 0))
            m = mask
            if dx:
                band = mask.crop((0, band_y, W, band_y + band_h))
                m = mask.copy()
                m.paste(0, (0, band_y, W, band_y + band_h))
                m.paste(ImageChops.offset(band, dx * 2, 0), (0, band_y))
            split = dx if dx else (1 if i % 7 == 0 else 0)
            r_mask = ImageChops.offset(m, -split - (1 if dx else 0), 0)
            c_mask = ImageChops.offset(m, split + (1 if dx else 0), 0)
            red_layer, cyan_layer = Image.new("RGB", (W, H), (0, 0, 0)), Image.new("RGB", (W, H), (0, 0, 0))
            _paste(red_layer, red, r_mask)
            _paste(cyan_layer, cyan, c_mask)
            both = ImageChops.add(red_layer, cyan_layer)  # where the two copies overlap the sum is white
            img.paste(both, (0, 0), ImageChops.lighter(r_mask, c_mask))
            frames.append(img)

    elif effect == "gold":
        top, mid, bottom = (255, 238, 150), (255, 196, 61), (176, 110, 20)
        base = Image.new("RGB", (W, H))
        bp = base.load()
        for y in range(H):
            u = y / (H - 1)
            c = _mix(top, mid, u / 0.5) if u < 0.5 else _mix(mid, bottom, (u - 0.5) / 0.5)
            for x in range(W):
                bp[x, y] = c
        bump = Image.new("L", (W * 3, 1))
        bpx = bump.load()
        for x in range(W * 3):
            bpx[x, 0] = int(255 * math.exp(-((x - W * 1.5) ** 2) / (2 * 26 ** 2)))
        for i in range(FRAMES):
            t = i / FRAMES
            img = Image.composite(Image.new("RGB", (W, H), (255, 196, 61)), plate, glow_big.point(lambda v: int(v * 0.16)))
            _paste(img, base, mask)
            p = -70 + t * (W + 140)
            band = bump.crop((int(W * 1.5 - p), 0, int(W * 1.5 - p) + W, 1)).resize((W, H), Image.NEAREST)
            _paste(img, (255, 255, 255), ImageChops.multiply(band, mask))
            _twinkles(img, t, 6, seed, (255, 226, 130))
            frames.append(img)

    else:  # fire
        base = Image.new("RGB", (W, H))
        bp = base.load()
        for y in range(H):
            u = y / (H - 1)
            c = _mix((255, 236, 120), (255, 120, 20), u / 0.55) if u < 0.55 else _mix((255, 120, 20), (210, 30, 20), (u - 0.55) / 0.45)
            for x in range(W):
                bp[x, y] = c
        for i in range(FRAMES):
            t = i / FRAMES
            flick = 0.7 + 0.3 * math.sin(t * math.tau * 3) * math.sin(t * math.tau + 1)
            img = Image.composite(Image.new("RGB", (W, H), (255, 90, 10)), plate, glow_big.point(lambda v, f=flick: int(v * 0.55 * f)))
            img = Image.composite(Image.new("RGB", (W, H), (255, 150, 30)), img, glow_small.point(lambda v, f=flick: int(v * 0.35 * f)))
            _embers(img, t, 14, seed)
            shaken = ImageChops.offset(mask, 0, int(round(math.sin(t * math.tau * 2) * 1)))
            _paste(img, base, shaken)
            frames.append(img)
    return frames


def _to_gif(frames, path):
    """Save the frames as one looping GIF with ONE shared palette (smaller file, no colors shimmering between frames)."""
    sample = frames[:: max(1, len(frames) // 8)]
    sheet = Image.new("RGB", (W, H * len(sample)))
    for i, f in enumerate(sample):
        sheet.paste(f, (0, i * H))
    palette = sheet.quantize(colors=200, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    tmp = path + ".tmp"
    out[0].save(tmp, format="GIF", save_all=True, append_images=out[1:], duration=FRAME_MS, loop=0, optimize=False, disposal=1)
    os.replace(tmp, path)


def render_title(text, effect, color, path):
    """Draw the title and save it as a GIF at `path`. Blocking (about a fifth of a second): call it from a worker thread.
    Raises when the text can't be drawn or Pillow / the fonts are missing; the caller then shows the plain text."""
    if not available():
        raise RuntimeError("Pillow or the fonts are missing")
    if effect not in EFFECTS:
        effect = "rainbow"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    _to_gif(_frames(text, effect, color), path)
