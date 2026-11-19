"""make_wratio_icon.py - draws the emoji in front of "W ratio" in the ,ratio embed (bl_wratio): a shiny 3D gold first-place medal
on a red ribbon (rounded rim, recessed face with a raised star, metal reflections, highlight, dark outline, soft drop shadow and two
sparkles), 128x128 PNG. (It used to be a gold letter W.)
It is meant to sit next to the shaded skull of the L ratio, so it is lit and shaded instead of flat.
Drawn at 4x and scaled down so the edges are smooth. Needs Pillow, numpy and scipy (only to draw it: the bot itself only reads ui_emojis.py).
Usage: python3 make_wratio_icon.py   (writes ui_emojis/bl_wratio.png and updates ui_emojis.py)
The bot creates it by itself on start-up (a changed picture replaces the old emoji) and finds it as emo("wratio"); without it the old trophy is shown.
Run it AFTER make_ui_emojis.py (that script rewrites ui_emojis.py), together with make_joinlog_icons.py.
"""
import base64, math, os, re

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage as ndi

S, OUT = 4, 128
N = OUT * S
NAME = "bl_wratio"

# medal (128 px coordinates)
CX, CY, R = 64, 83, 36
# the two ribbon straps, each a polygon; they meet behind the top of the medal
STRAP_L = [(22, 4), (48, 4), (80, 72), (54, 72)]
STRAP_R = [(106, 4), (80, 4), (48, 72), (74, 72)]


def hexrgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def ramp(values, stops):
    """Colour for each value in [0, 1] from a list of (position, "#hex") stops."""
    pos = [p for p, _c in stops]
    cols = np.array([hexrgb(c) for _p, c in stops])
    return np.stack([np.interp(values, pos, cols[:, i]) for i in range(3)], axis=-1)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def bevel_of(mask, width_frac=0.62):
    """0..1 rounded-bevel profile of a mask: 0 at its edge, 1 in the flat middle."""
    dist = ndi.distance_transform_edt(mask)
    half = max(dist.max(), 1)
    t = np.clip(dist / (half * width_frac), 0, 1)
    return np.sqrt(1 - (1 - t) ** 2), dist / half


def poly_mask(points):
    img = Image.new("L", (N, N), 0)
    ImageDraw.Draw(img).polygon([(x * S, y * S) for x, y in points], fill=255)
    return np.asarray(img) > 127


def star5(cx, cy, ro, ri):
    pts = []
    for i in range(10):
        a = math.pi / 5 * i - math.pi / 2
        rr = ro if i % 2 == 0 else ri
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def sparkle(cx, cy, r, squash=0.22):
    """A four-point sparkle as polygon points."""
    pts = []
    for i in range(8):
        a = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * squash
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def shade(h, face, metal, spec_power=55, spec_gain=200, dull=1.0):
    """Light a height map h (in 4x pixels): a smooth face colour, with a metal reflection ramp on the steep parts."""
    gy, gx = np.gradient(h)
    nx, ny, nz = -gx, -gy, np.ones_like(h)
    norm = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    nx, ny, nz = nx / norm, ny / norm, nz / norm
    up = -ny                                    # 1 = the surface looks up, -1 = down
    steep = np.clip(np.sqrt(nx ** 2 + ny ** 2) * 1.7, 0, 1)[..., None]
    metal_rgb = ramp((up + 1) / 2, metal)
    rgb = face * (1 - steep) + metal_rgb * steep
    light = np.array([-0.45, -0.65, 0.62])
    light /= np.linalg.norm(light)
    diffuse = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0, 1)
    rgb = rgb * (0.78 + 0.32 * diffuse[..., None])
    half_v = light + np.array([0, 0, 1.0])
    half_v /= np.linalg.norm(half_v)
    spec = np.clip(nx * half_v[0] + ny * half_v[1] + nz * half_v[2], 0, 1) ** spec_power
    rgb = rgb + spec[..., None] * spec_gain * dull
    return np.clip(rgb, 0, 255)


def layer(mask, rgb, outline_hex, outline_px):
    """A finished RGBA layer: the lit fill with a dark outline around it (float array N x N x 4, alpha 0..1)."""
    grow = ndi.distance_transform_edt(~mask)
    outline = (grow > 0) & (grow <= outline_px * S)
    out = np.zeros((N, N, 4), float)
    out[outline, :3] = hexrgb(outline_hex)
    out[outline, 3] = 1
    out[mask, :3] = rgb[mask]
    out[mask, 3] = 1
    return out


def to_image(arr):
    a = arr.copy()
    a[..., 3] *= 255
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")


def draw():
    yy, xx = np.mgrid[0:N, 0:N].astype(float)

    # ---------------- ribbon: two matte red straps ----------------
    red_metal = [(0, "#5E0D14"), (0.3, "#A3202A"), (0.55, "#D8323B"), (0.78, "#F0626A"), (1, "#FF9A9E")]
    straps = []
    for pts, tone in ((STRAP_L, 0.82), (STRAP_R, 1.0)):
        m = poly_mask(pts)
        b, rel = bevel_of(m, 0.55)
        h = ndi.gaussian_filter(b * 0.85 + np.clip(rel, 0, 1) * 0.15, 1.3 * S) * 9 * S
        face = ramp(np.clip(yy / N / 0.6, 0, 1), [(0, "#E8444C"), (1, "#B6242D")]) * tone
        rgb = shade(h, face, red_metal, spec_power=30, spec_gain=200, dull=0.18) * tone
        straps.append((m, np.clip(rgb, 0, 255)))

    # ---------------- medal: rim, recessed face, raised star ----------------
    r = np.hypot(xx - CX * S, yy - CY * S) / S           # distance from the centre in 128 px units
    disc = r <= R
    rim = smoothstep(R, R - 4.5, r)                      # 0 at the edge, 1 on the ridge
    step = 1 - 0.5 * smoothstep(R - 6.0, R - 9.0, r)     # the face is recessed inside the rim
    h = rim * step + 0.10 * (1 - np.clip(r / R, 0, 1))
    star_m = poly_mask(star5(CX, CY + 1.5, 19.5, 8.4))
    sb, _rel = bevel_of(star_m, 0.7)
    h = h + 0.55 * ndi.gaussian_filter(sb, 0.8 * S) * (r < R - 6.5)
    h = ndi.gaussian_filter(h, 1.0 * S) * 24 * S / 4     # height in 4x pixels
    top, bottom = (CY - R) * S, (CY + R) * S
    face = ramp(np.clip((yy - top) / (bottom - top), 0, 1), [(0, "#FFE680"), (0.5, "#F7C531"), (1, "#E39A12")])
    gold_metal = [(0, "#8A4E05"), (0.22, "#C27A0C"), (0.45, "#F3B722"), (0.62, "#FFE27A"), (0.8, "#FFF6C2"), (1, "#FFFFFF")]
    medal_rgb = shade(h, face, gold_metal)

    # ---------------- stack: shadow, straps, medal ----------------
    union = disc | straps[0][0] | straps[1][0]
    grow = ndi.distance_transform_edt(~union)
    shadow_src = ndi.shift((grow <= 2.6 * S).astype(float), (3.5 * S, 0.6 * S), order=1)
    shadow = ndi.gaussian_filter(shadow_src, 2.6 * S) * 0.5
    base = np.zeros((N, N, 4), float)
    base[..., :3] = 20
    base[..., 3] = shadow
    img = to_image(base)
    for m, rgb in straps:
        img = Image.alpha_composite(img, to_image(layer(m, rgb, "#4A0A10", 2.4)))
    img = Image.alpha_composite(img, to_image(layer(disc, medal_rgb, "#5B3304", 3.0)))

    # ---------------- sparkles ----------------
    glow = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for cx, cy, rr in ((112, 62, 9), (14, 108, 6)):
        gd.polygon(sparkle(cx * S, cy * S, rr * S), fill=(255, 250, 210, 255))
    halo = glow.filter(ImageFilter.GaussianBlur(2.2 * S))
    img = Image.alpha_composite(img, halo)
    img = Image.alpha_composite(img, glow)
    return img.resize((OUT, OUT), Image.LANCZOS)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "ui_emojis"), exist_ok=True)
    path = os.path.join(here, "ui_emojis", NAME + ".png")
    draw().save(path, optimize=True)
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    py = os.path.join(here, "ui_emojis.py")
    s = open(py, encoding="utf-8").read()
    s = re.sub(r'    "%s": \(\n(?:        ".*"\)?,?\n)+' % re.escape(NAME), "", s)  # replace an earlier version
    chunks = [b64[i:i + 100] for i in range(0, len(b64), 100)]
    add = f'    "{NAME}": (\n' + "\n".join(
        f'        "{c}"' + (")," if i == len(chunks) - 1 else "") for i, c in enumerate(chunks)) + "\n"
    s = s.rstrip()
    assert s.endswith("}")
    open(py, "w", encoding="utf-8").write(s[:-1] + add + "}\n")


if __name__ == "__main__":
    main()
