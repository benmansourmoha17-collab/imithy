"""The rules of the role / title edit panels that need no Discord: color styles, hex codes, emoji pages.

Why it is a separate file: it can be tested on its own, and the panels in bot.py only call it.
A role "look" is a small dict saved in the member's shop record:
    {"style": "solid" | "gradient" | "holo", "c1": 0xRRGGBB, "c2": 0xRRGGBB or None, "emoji": custom emoji id or None,
     "uemoji": a normal (unicode) emoji as text or None}   (at most one of the two icons is set)
"""

STYLES = {"solid": "Solid", "gradient": "Gradient", "holo": "Holographic"}
STYLE_HINT = {
    "solid": "One color",
    "gradient": "Two colors that blend into each other",
    "holo": "Discord's shiny holographic colors (no hex needed)",
}
NEEDS_ENHANCED = ("gradient", "holo")  # these two need the server feature ENHANCED_ROLE_COLORS

# Discord's fixed holographic colors (primary, secondary, tertiary).
HOLO_COLORS = (0xA9C9FF, 0xFFBBEC, 0xFFC3A0)

DEFAULT_C1 = 0x99AAB5
DEFAULT_C2 = 0xFF4FA3
PAGE_SIZE = 25  # a select menu holds at most 25 options


import re
import unicodedata

_CUSTOM_EMOJI = re.compile(r"^<(a?):([A-Za-z0-9_~]{2,32}):(\d{15,25})>$")
_REMOVE_WORDS = ("none", "remove", "off", "no", "clear", "-")


def looks_like_emoji(text):
    """True for a normal emoji (one or a few symbols, flags, keycaps, joined families). Discord has the final say."""
    text = (text or "").strip()
    if not text or len(text) > 16:
        return False
    if any(ch.isascii() and ch.isalpha() for ch in text):
        return False
    return any(unicodedata.category(ch) in ("So", "Sk") or ch == "\u20e3" for ch in text)


def parse_icon(text, find_by_name=None):
    """What a member typed for a role icon. Returns (kind, value):
      ("keep", None)    nothing typed: leave the icon as it is
      ("remove", None)  none / remove / off
      ("custom", id)    <:name:id>, <a:name:id>, a bare emoji id, or :name: / name (found with find_by_name(name) -> id or None)
      ("unicode", text) a normal emoji
      ("bad", None)     something that is not an emoji"""
    text = (text or "").strip()
    if not text:
        return "keep", None
    if text.lower() in _REMOVE_WORDS:
        return "remove", None
    m = _CUSTOM_EMOJI.match(text)
    if m:
        return "custom", int(m.group(3))
    if text.isdigit() and 15 <= len(text) <= 25:
        return "custom", int(text)
    if looks_like_emoji(text):
        return "unicode", text
    name = text.strip(":").strip()
    if find_by_name is not None and re.fullmatch(r"[A-Za-z0-9_~]{2,32}", name):
        found = find_by_name(name)
        if found:
            return "custom", int(found)
    return "bad", None


def _color_ok(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 0xFFFFFF


def clean_look(raw):
    """Whatever was saved (a restored backup can hold anything) -> a safe look dict. Missing or odd values get defaults."""
    raw = raw if isinstance(raw, dict) else {}
    style = raw.get("style") if raw.get("style") in STYLES else "solid"
    c1 = raw.get("c1") if _color_ok(raw.get("c1")) else DEFAULT_C1
    c2 = raw.get("c2") if _color_ok(raw.get("c2")) else None
    emoji = raw.get("emoji")
    if isinstance(emoji, bool) or not isinstance(emoji, int) or emoji <= 0:
        emoji = None
    uemoji = raw.get("uemoji")
    if not isinstance(uemoji, str) or not looks_like_emoji(uemoji) or emoji is not None:
        uemoji = None
    if style == "gradient" and c2 is None:
        c2 = DEFAULT_C2
    return {"style": style, "c1": c1, "c2": c2, "emoji": emoji, "uemoji": uemoji}


def colors_for(look):
    """The (primary, secondary, tertiary) colors Discord should get for a look. Unused ones are None."""
    look = clean_look(look)
    if look["style"] == "holo":
        return HOLO_COLORS
    if look["style"] == "gradient":
        return look["c1"], look["c2"], None
    return look["c1"], None, None


def look_problem(look, features):
    """None when the look can be applied in a server with these guild features, else one sentence."""
    look = clean_look(look)
    if look["style"] in NEEDS_ENHANCED and "ENHANCED_ROLE_COLORS" not in set(features or ()):
        return f"{STYLES[look['style']]} colors need this server to unlock enhanced role colors (a Boost perk). Pick Solid, or ask the owner."
    if look["style"] == "gradient" and look["c1"] == look["c2"]:
        return "A gradient needs two different colors."
    return None


def hex_text(value):
    return f"#{value:06X}" if _color_ok(value) else "-"


def describe(look):
    """'Gradient #FF00AA -> #00AAFF', 'Holographic', 'Solid #99AAB5'."""
    look = clean_look(look)
    if look["style"] == "holo":
        return STYLES["holo"]
    if look["style"] == "gradient":
        return f"{STYLES['gradient']} {hex_text(look['c1'])} \u2192 {hex_text(look['c2'])}"
    return f"{STYLES['solid']} {hex_text(look['c1'])}"


def preview_color(look):
    """The one color the panel's embed bar can show."""
    return colors_for(look)[0]


def pages(count, size=PAGE_SIZE):
    return max(1, -(-int(count) // size))


def page_slice(items, page, size=PAGE_SIZE):
    """(the items of that page, the page number kept inside the real range, how many pages there are)."""
    items = list(items)
    total = pages(len(items), size)
    page = min(max(0, int(page)), total - 1)
    return items[page * size:(page + 1) * size], page, total
