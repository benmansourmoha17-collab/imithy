"""make_bottle_icons.py - draws the four icons of the ,bottle command: bl_bottle (green glass bottle), bl_kiss (glossy lips),
bl_slap (open hand with impact marks) and bl_ring (gold ring with a diamond). Lit and shaded like the W ratio badge
(bevels, reflections, dark outline, soft shadow), 128x128 PNG, drawn at 4x and scaled down.
Needs Pillow, numpy and scipy (only to draw them: the bot itself only reads ui_emojis.py).
Usage: python3 make_bottle_icons.py   (writes ui_emojis/bl_*.png and updates ui_emojis.py)
Run it AFTER make_ui_emojis.py (that script rewrites ui_emojis.py), like make_wratio_icon.py.
The bot creates them on start-up and finds them as emo("bottle"), emo("kiss"), emo("slap") and emo("ring").
"""
import base64, os, re
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage as ndi

S, OUT = 4, 128
N = OUT * S
NAMES = ("bl_bottle", "bl_kiss", "bl_slap", "bl_ring")


def hexrgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def new_mask():
    im = Image.new("L", (N, N), 0)
    return im, ImageDraw.Draw(im)


def to_mask(im):
    return np.array(im) > 127


def bez(p0, p1, p2, p3, steps=40):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0]
        y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1]
        pts.append((x * S, y * S))
    return pts


def path(segments):
    pts = []
    for seg in segments:
        pts += bez(*seg)
    return pts


def capsule(d, a, b, width):
    r = width * S / 2
    d.line([(a[0] * S, a[1] * S), (b[0] * S, b[1] * S)], fill=255, width=int(width * S))
    for x, y in (a, b):
        d.ellipse((x * S - r, y * S - r, x * S + r, y * S + r), fill=255)


def shade(mask, top, mid, bot, spec_pow=60, spec_amt=170, bevel=0.7):
    """RGB (N, N, 3) for a part: vertical colour gradient, rounded bevel lighting from the top left, a specular highlight."""
    dist = ndi.distance_transform_edt(mask)
    half = max(dist.max(), 1)
    t = np.clip(dist / (half * bevel), 0, 1)
    h = (np.sqrt(1 - (1 - t) ** 2) * 0.8 + np.clip(dist / half, 0, 1) * 0.2)
    h = ndi.gaussian_filter(h, 1.2 * S) * (half * 0.55)
    gy, gx = np.gradient(h)
    nx, ny, nz = -gx, -gy, np.ones_like(h)
    nn = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    nx, ny, nz = nx / nn, ny / nn, nz / nn
    ys, xs = np.where(mask)
    y0, y1 = (ys.min(), ys.max()) if len(ys) else (0, 1)
    v = np.clip((np.arange(N)[:, None] - y0) / max(y1 - y0, 1), 0, 1) * np.ones((1, N))
    c_top, c_mid, c_bot = hexrgb(top), hexrgb(mid), hexrgb(bot)
    lo = np.clip(v * 2, 0, 1)[..., None]
    hi = np.clip(v * 2 - 1, 0, 1)[..., None]
    face = (c_top * (1 - lo) + c_mid * lo) * (1 - hi) + c_bot * hi
    light = np.array([-0.45, -0.65, 0.62])
    light /= np.linalg.norm(light)
    diffuse = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0, 1)
    rgb = face * (0.62 + 0.5 * diffuse[..., None])
    hv = light + np.array([0, 0, 1.0])
    hv /= np.linalg.norm(hv)
    spec = np.clip(nx * hv[0] + ny * hv[1] + nz * hv[2], 0, 1) ** spec_pow
    return np.clip(rgb + spec[..., None] * spec_amt, 0, 255)


def compose(parts, outline="#2A1208", shadow_shift=(3.5, 0.6)):
    """parts: list of (mask, rgb). Each gets a dark outline; the union gets a soft drop shadow."""
    union = np.zeros((N, N), bool)
    for m, _ in parts:
        union |= m
    grow_all = ndi.distance_transform_edt(~union) <= 2.8 * S
    sh = ndi.gaussian_filter(ndi.shift(grow_all.astype(float), (shadow_shift[0] * S, shadow_shift[1] * S), order=1), 2.6 * S) * 0.5
    canvas = np.zeros((N, N, 4), float)
    canvas[..., :3] = 20
    canvas[..., 3] = sh
    oc = hexrgb(outline)
    for m, rgb in parts:
        ring = (ndi.distance_transform_edt(~m) <= 2.8 * S) & ~m
        a = ring.astype(float)
        canvas[..., :3] = canvas[..., :3] * (1 - a[..., None]) + oc * a[..., None]
        canvas[..., 3] = np.maximum(canvas[..., 3], a)
        f = m.astype(float)
        canvas[..., :3] = canvas[..., :3] * (1 - f[..., None]) + rgb * f[..., None]
        canvas[..., 3] = np.maximum(canvas[..., 3], f)
    arr = canvas.copy()
    arr[..., 3] *= 255
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def star(cx, cy, r):
    return [(cx, cy - r), (cx + r * 0.22, cy - r * 0.22), (cx + r, cy), (cx + r * 0.22, cy + r * 0.22),
            (cx, cy + r), (cx - r * 0.22, cy + r * 0.22), (cx - r, cy), (cx - r * 0.22, cy - r * 0.22)]


def sparkle(img, spots):
    glow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for cx, cy, r in spots:
        gd.polygon([(x * S, y * S) for x, y in star(cx, cy, r)], fill=(255, 252, 235, 255))
    halo = glow.filter(ImageFilter.GaussianBlur(2.2 * S))
    return Image.alpha_composite(Image.alpha_composite(img, halo), glow)


def gloss(img, box, alpha=150, blur=1.6, angle=0):
    layer = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse([c * S for c in box], fill=(255, 255, 255, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(blur * S))
    return Image.alpha_composite(img, layer)


def clip_to(img, mask):
    arr = np.array(img)
    arr[..., 3] = np.where(mask, arr[..., 3], 0)
    return Image.fromarray(arr, "RGBA")


# ------------------------------------------------------------------ lips
def draw_kiss():
    up_im, ud = new_mask()
    ud.polygon(path([((16, 68), (26, 52), (34, 38), (48, 40)), ((48, 40), (56, 41), (60, 48), (64, 51)),
                     ((64, 51), (68, 48), (72, 41), (80, 40)), ((80, 40), (94, 38), (102, 52), (112, 68)),
                     ((112, 68), (96, 74), (80, 70), (64, 72)), ((64, 72), (48, 70), (32, 74), (16, 68))]), fill=255)
    lo_im, ld = new_mask()
    ld.polygon(path([((16, 68), (32, 74), (48, 70), (64, 72)), ((64, 72), (80, 70), (96, 74), (112, 68)),
                     ((112, 68), (106, 92), (86, 108), (64, 108)), ((64, 108), (42, 108), (22, 92), (16, 68))]), fill=255)
    up, lo = to_mask(up_im), to_mask(lo_im)
    parts = [(lo, shade(lo, "#F0435D", "#D01535", "#8E0B24", spec_pow=45, spec_amt=150)),
             (up, shade(up, "#E0314E", "#B80F2E", "#7B0720", spec_pow=45, spec_amt=130))]
    img = compose(parts, outline="#4A0612")
    img = gloss(clip_to(img, lo | up), (42, 82, 82, 95), alpha=95, blur=1.5)
    img = gloss(img, (34, 44, 52, 52), alpha=70, blur=1.4)
    return img.resize((OUT, OUT), Image.LANCZOS)


# ------------------------------------------------------------------ ring
def draw_ring():
    band_im, bd = new_mask()
    cx, cy, ro, ri = 64, 86, 34, 22
    bd.ellipse(((cx - ro) * S, (cy - ro) * S, (cx + ro) * S, (cy + ro) * S), fill=255)
    bd.ellipse(((cx - ri) * S, (cy - ri) * S, (cx + ri) * S, (cy + ri) * S), fill=0)
    band = to_mask(band_im)
    band_rgb = shade(band, "#FFE680", "#F3B722", "#B8720A", spec_pow=40, spec_amt=190, bevel=0.9)
    # the diamond: flat facets
    facets = [
        ([(46, 18), (82, 18), (90, 32), (38, 32)], "#F4FCFF"),        # table + crown top
        ([(38, 32), (51, 32), (64, 58)], "#9AD2F6"),                    # left pavilion
        ([(51, 32), (77, 32), (64, 58)], "#D6F0FF"),                    # middle pavilion
        ([(77, 32), (90, 32), (64, 58)], "#6DB6EA"),                    # right pavilion
        ([(46, 18), (38, 32), (51, 32)], "#BFE5FF"),
        ([(82, 18), (90, 32), (77, 32)], "#8CCBF2"),
    ]
    all_im, ad = new_mask()
    for pts, _ in facets:
        ad.polygon([(x * S, y * S) for x, y in pts], fill=255)
    gem = to_mask(all_im)
    gem_rgb = np.zeros((N, N, 3), float)
    for pts, col in facets:
        fi, fd = new_mask()
        fd.polygon([(x * S, y * S) for x, y in pts], fill=255)
        gem_rgb[to_mask(fi)] = hexrgb(col)
    # facet edges
    edge = Image.new("L", (N, N), 0)
    ed = ImageDraw.Draw(edge)
    for pts, _ in facets:
        ed.line([(x * S, y * S) for x, y in pts + [pts[0]]], fill=255, width=int(1.3 * S))
    em = np.array(edge) > 100
    gem_rgb[em & gem] = hexrgb("#4C96CF")
    img = compose([(band, band_rgb), (gem, gem_rgb)], outline="#3B2A05")
    img = sparkle(img, [(100, 20, 11), (24, 52, 6)])
    return img.resize((OUT, OUT), Image.LANCZOS)


# ------------------------------------------------------------------ hand
def draw_slap():
    fingers = [((47, 66), (44, 28), 15), ((61, 64), (60, 18), 15.5), ((75, 66), (77, 24), 15), ((88, 72), (95, 40), 13)]
    parts = []
    for a, b, w in fingers:
        im, d = new_mask()
        capsule(d, a, b, w)
        m = to_mask(im)
        parts.append((m, shade(m, "#FFD9B8", "#F1B28A", "#D58A5E", spec_pow=35, spec_amt=60, bevel=0.9)))
    th_im, td = new_mask()
    capsule(td, (46, 92), (22, 68), 17)
    th = to_mask(th_im)
    pm_im, pd = new_mask()
    pd.rounded_rectangle((38 * S, 58 * S, 98 * S, 112 * S), radius=22 * S, fill=255)
    palm = to_mask(pm_im)
    parts.append((th, shade(th, "#FFD9B8", "#F1B28A", "#D58A5E", spec_pow=35, spec_amt=60, bevel=0.9)))
    parts.append((palm, shade(palm, "#FFDDBD", "#F3B58C", "#D18359", spec_pow=30, spec_amt=70, bevel=0.8)))
    img = compose(parts, outline="#6B3118")
    # impact marks on the left (flat red-orange, round ends)
    layer = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for a, b in (((8, 20), (20, 30)), ((4, 44), (16, 46)), ((14, 6), (24, 18))):
        ld.line([(a[0] * S, a[1] * S), (b[0] * S, b[1] * S)], fill=(255, 77, 60, 255), width=int(6 * S))
        for x, y in (a, b):
            ld.ellipse((x * S - 3 * S, y * S - 3 * S, x * S + 3 * S, y * S + 3 * S), fill=(255, 77, 60, 255))
    img = Image.alpha_composite(layer, img)
    return img.resize((OUT, OUT), Image.LANCZOS)


# ------------------------------------------------------------------ bottle
def draw_bottle():
    glass_im, gd = new_mask()
    gd.rounded_rectangle((57 * S, 8 * S, 71 * S, 54 * S), radius=5 * S, fill=255)           # neck
    gd.rounded_rectangle((42 * S, 44 * S, 86 * S, 122 * S), radius=18 * S, fill=255)        # body
    gd.polygon([(57 * S, 40 * S), (71 * S, 40 * S), (86 * S, 70 * S), (42 * S, 70 * S)], fill=255)  # shoulder
    glass = to_mask(glass_im)
    # glass: dark green with a lit rim, shading across the width like a cylinder
    rgb = shade(glass, "#2FA865", "#157A44", "#0A4A2A", spec_pow=70, spec_amt=120, bevel=0.55)
    cap_im, cd = new_mask()
    cd.rounded_rectangle((54 * S, 2 * S, 74 * S, 16 * S), radius=4 * S, fill=255)
    cap = to_mask(cap_im)
    cap_rgb = shade(cap, "#FFE680", "#E9B21E", "#A9690A", spec_pow=40, spec_amt=150)
    lab_im, ld = new_mask()
    ld.rounded_rectangle((46 * S, 84 * S, 82 * S, 108 * S), radius=5 * S, fill=255)
    label = to_mask(lab_im) & glass
    lab_rgb = shade(label, "#FFF8E6", "#F1E3BC", "#D8C28C", spec_pow=30, spec_amt=30, bevel=0.5)
    img = compose([(glass, rgb), (label, lab_rgb), (cap, cap_rgb)], outline="#06281A", shadow_shift=(0, 0))
    # long vertical gloss streaks on the glass
    layer = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((50 * S, 52 * S, 54.5 * S, 112 * S), radius=2 * S, fill=(255, 255, 255, 120))
    ImageDraw.Draw(layer).rounded_rectangle((60 * S, 14 * S, 62.5 * S, 44 * S), radius=1 * S, fill=(255, 255, 255, 110))
    layer = layer.filter(ImageFilter.GaussianBlur(0.9 * S))
    arr = np.array(layer)
    arr[..., 3] = np.where(glass & ~label, arr[..., 3], 0)
    img = Image.alpha_composite(img, Image.fromarray(arr, "RGBA"))
    # lay it down: turned 45 degrees, then the drop shadow is redone for the turned shape
    img = img.rotate(-45, resample=Image.BICUBIC, center=(64 * S, 62 * S))
    a = np.array(img)[..., 3] / 255.0
    sh = ndi.gaussian_filter(ndi.shift(a, (3.5 * S, 0.6 * S), order=1), 2.6 * S) * 0.45
    base = np.zeros((N, N, 4), np.uint8)
    base[..., :3] = 20
    base[..., 3] = np.clip(sh * 255, 0, 255).astype(np.uint8)
    img = Image.alpha_composite(Image.fromarray(base, "RGBA"), img)
    return img.resize((OUT, OUT), Image.LANCZOS)


DRAWERS = {"bl_bottle": draw_bottle, "bl_kiss": draw_kiss, "bl_slap": draw_slap, "bl_ring": draw_ring}


def add_to_pack(py, name, png_path):
    b64 = base64.b64encode(open(png_path, "rb").read()).decode()
    s = open(py, encoding="utf-8").read()
    s = re.sub(r'    "%s": \(\n(?:        ".*"\)?,?\n)+' % re.escape(name), "", s)  # replace an earlier version
    chunks = [b64[i:i + 100] for i in range(0, len(b64), 100)]
    add = f'    "{name}": (\n' + "\n".join(
        f'        "{c}"' + ("),") if i == len(chunks) - 1 else f'        "{c}"' for i, c in enumerate(chunks)) + "\n"
    s = s.rstrip()
    assert s.endswith("}")
    open(py, "w", encoding="utf-8").write(s[:-1] + add + "}\n")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "ui_emojis"), exist_ok=True)
    for name in NAMES:
        path = os.path.join(here, "ui_emojis", name + ".png")
        DRAWERS[name]().save(path, optimize=True)
        add_to_pack(os.path.join(here, "ui_emojis.py"), name, path)


if __name__ == "__main__":
    main()
