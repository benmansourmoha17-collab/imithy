"""Friend matchmaking for the bot (romantic matches are 18+ only), works across every server, with staff safety tools.

Load it from bot.py with:   await self.load_extension("matchmaking")   (inside setup_hook)

How it works
- One global profile database (matchmaking.json), keyed by Discord user ID, so a profile made in
  one server works in every server the bot is in.
- Profile = three steps, like the reference bot: About You (nickname, date of birth, gender, timezone),
  Your Vibes (about, likes, dislikes, avatar), Your Match (preferable gender / timezone / match type,
  "older only"). `.match profile` shows the buttons.
- `.match find [quick|deep]` joins the queue (limited lookups per day). Quick = fast match,
  Deep = needs shared interests. When two compatible people are waiting, the bot DMs both.
- The chat is RELAYED by the bot (nobody sees the other person's Discord account). Buttons in the
  DM: Like Profile / Report (leave with /match end, block with /match block). Leaving asks for confirmation
  first and is locked for the first MATCH_UNMATCH_LOCK_MINUTES minutes.
- Safety: 13+ (Discord's minimum age), under-18s are only matched with under-18s and adults with
  adults, age gap limit, account-age check, no links or attachments, rate limits, block + report,
  auto-suspend after repeated reports, global ban list, per-server off switch.
- Staff tools (`.match staff`): every chat message and event is written to an audit log
  (matchmaking_log.jsonl, kept MATCH_LOG_DAYS days). Staff can list live chats, read any chat, read the
  event log, open any profile, force-end a chat, and the bot owner can mirror everything live into a
  private staff channel. Every staff look is itself logged. Users are told about the logging.
- The bot owner sets who is matchmaking staff: `.match staff add @user` / `.match staff remove @user`.

Optional env vars (all have defaults):
  MATCH_MAX_AGE_GAP=2   MATCH_MIN_ACCOUNT_DAYS=30   MATCH_REMATCH_DAYS=7   MATCH_DAILY_LOOKUPS=3 (0 = unlimited)
  MATCH_QUEUE_MINUTES=30   MATCH_AUTO_SUSPEND_REPORTS=3   MATCH_REPORT_CHANNEL_ID=<channel id>
  MATCH_LOG_DAYS=30   MATCH_UNMATCH_LOCK_MINUTES=10 (no unmatching right after a match)
  MATCH_REFUND_MINUTES=90 (leaving inside this window returns the other person's lookup)
"""
import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import os
import random
import re
import tempfile
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:  # banner + ribbon images (optional: without the file the bot just skips them)
    from mm_assets import ASSETS
except Exception:
    ASSETS = {}

log = logging.getLogger("wordfilter.matchmaking")


def _int_env(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
MATCH_FILE = os.path.join(DATA_DIR, "matchmaking.json")
LOG_FILE = os.path.join(DATA_DIR, "matchmaking_log.jsonl")

MIN_AGE = 13           # Discord's own minimum age
ADULT_AGE = 18         # under-18s are only matched with under-18s, adults with adults
MAX_AGE = 99
MAX_AGE_GAP = _int_env("MATCH_MAX_AGE_GAP", 2)
MIN_ACCOUNT_AGE_DAYS = _int_env("MATCH_MIN_ACCOUNT_DAYS", 30)
REMATCH_DAYS = _int_env("MATCH_REMATCH_DAYS", 7)
DAILY_LOOKUPS = _int_env("MATCH_DAILY_LOOKUPS", 3)   # 0 = unlimited
QUEUE_TIMEOUT = _int_env("MATCH_QUEUE_MINUTES", 30) * 60
UNMATCH_LOCK = _int_env("MATCH_UNMATCH_LOCK_MINUTES", 10) * 60   # no unmatching for this long after a match (0 = off)
REFUND_WINDOW = _int_env("MATCH_REFUND_MINUTES", 90) * 60        # leaving inside this window returns their lookup
REVEAL_MIN_AGE = _int_env("MATCH_REVEAL_MIN_AGE", 18)             # both people must be at least this old to swap usernames
REVEAL_SECONDS = 300                                              # how long a username swap request waits for an answer
ICEBREAKER_COOLDOWN = 45                                          # seconds between two icebreakers in the same chat
# what the invite link asks for (the wordfilter webhooks, family roles / nicknames and the embeds need these)
INVITE_PERMISSIONS = discord.Permissions(
    view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
    add_reactions=True, use_external_emojis=True, manage_messages=True, manage_webhooks=True,
    manage_roles=True, manage_nicknames=True)

ICEBREAKERS = [
    "What's a song you've had on repeat lately?",
    "What's the last show or anime you binged?",
    "Pizza or tacos for the rest of your life?",
    "What's a small thing that always makes your day better?",
    "If you could instantly master one skill, what would it be?",
    "What's the most useless talent you have?",
    "Which game have you sunk the most hours into?",
    "Early bird or night owl?",
    "What's a movie you could rewatch forever?",
    "Which fictional character would you have dinner with?",
    "What's your comfort food?",
    "Beach holiday or mountain trip?",
    "What's the best compliment you've ever received?",
    "What hobby would you try if time and money didn't matter?",
    "What's a harmless opinion you'd defend to the end? (pineapple on pizza counts)",
    "Cats or dogs, and why are you right?",
    "What's the last thing that made you laugh out loud?",
    "Which superpower would be the most annoying to actually have?",
    "What's something you're weirdly good at?",
    "Rainy day or sunny day?",
    "What's a book, manga or anime you think everyone should try?",
    "If your life had a theme song, what would it be?",
    "What's a food you'll never get tired of?",
    "What's the best thing you've ever bought for under $20?",
    "What's one thing on your bucket list?",
    "Would you rather talk to animals or speak every language?",
    "What's the weirdest dream you remember?",
    "How do you unwind after a long day?",
    "What game would you play with a new friend right now?",
    "What's your go-to karaoke song?",
]
AUTO_SUSPEND_REPORTS = _int_env("MATCH_AUTO_SUSPEND_REPORTS", 3)
REPORT_CHANNEL_ID = _int_env("MATCH_REPORT_CHANNEL_ID", 0)
LOG_DAYS = _int_env("MATCH_LOG_DAYS", 30)
PANEL_DELAY = _int_env("MATCH_PANEL_DELAY", 5)  # seconds of quiet before the sticky panel moves to the bottom
TZ_CLOSE_HOURS = 3     # "close to mine" timezone preference
FEED_BATCH_SECONDS = 2
PREFIX = os.getenv("PREFIX", ".")
GREY = 0x2B2D31
ERR = 0xD9534F
OK = 0x3BA55D
WARN = 0xFAA61A      # amber, same as the main bot
WAIT = 0x5865F2      # blurple "working on it" bar

LINK_RE = re.compile(r"(https?://|www\.|discord\.gg/|discord(app)?\.com/invite)", re.I)
MAX_RELAY_LEN = 1500

# friend type -> (label on the ribbon, ribbon image in mm_assets.py)
TYPES = {
    "romantic": ("romantic", "ribbon_romantic.png"),
    "any": ("friends", "ribbon_any.png"),
    "chat": ("just chatting", "ribbon_chat.png"),
    "music": ("music and anime", "ribbon_music.png"),
}
ADULT_ONLY_TYPES = {"romantic"}  # match types that only people aged ADULT_AGE+ can pick or be matched on
GENDERS = {"male": "Male", "female": "Female", "nonbinary": "Non-binary", "private": "Prefer not to say"}
# gender -> icon key of the ribbon emoji shown in front of the name on the profile card
GENDER_RIBBONS = {"female": "ribbon_female", "male": "ribbon_male", "nonbinary": "ribbon_nonbinary"}
GENDER_PREFS = {"any": "Anyone", "male": "Male", "female": "Female", "nonbinary": "Non-binary"}
TZ_PREFS = {"global": "Can be global", "close": f"Close to me (within {TZ_CLOSE_HOURS} hours)"}
# avatar picker (like the reference's "Choose your avatar"): name -> emoji shown in front of the profile name
AVATARS = {"smirk": "\U0001F63C", "happy": "\U0001F63A", "grin": "\U0001F638", "lol": "\U0001F639",
           "cute": "\U0001F63B", "sweet": "\U0001F63D", "shocked": "\U0001F640", "sad": "\U0001F63F",
           "mad": "\U0001F63E"}
DEFAULT_AVATAR = "happy"
MODE_NAMES = {"quick": "Quick Match", "deep": "Deep Match"}
STOP_WORDS = {"the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had", "her", "was", "one",
              "our", "out", "has", "have", "his", "how", "its", "who", "with", "that", "this", "like", "love",
              "lot", "lots", "really", "very", "just", "also", "some", "from", "they", "them", "what", "when",
              "much", "more", "most", "into", "about", "things", "stuff", "playing", "watching", "listening"}


# --------------------------------------------------------------------------
# Button icons. The bot looks for a custom emoji named bl_<key> first, then for the names
# below (case-insensitive, exact). Any icon that isn't found is simply left off the button.
# --------------------------------------------------------------------------
MM_EMOJI = {
    "user": ["user"],
    "sparkle": ["a_sparkle", "sparkle"],
    "heart": ["004_heart", "heart"],
    "search": ["search", "search_white", "magnifier", "magnify", "find"],
    "card": ["card", "id_card", "profile_card", "profile", "info_white"],
    "bulb": ["bulb", "lightbulb", "light_bulb", "idea", "elife_white_chat"],
    "exit": ["exit", "leave", "logout", "door", "left"],
    "flag": ["flag", "report", "warning"],
    "block": ["block", "ban", "blocked", "deny"],
    "rules": ["elife_white_rules"],
    "shield": ["WhiteStaff", "white_staff", "shield"],
    "announce": ["announce", "announcement", "announcements", "megaphone"],
    # status pack (same names as in the main bot)
    "ok": ["approve", "small_check_whi", "z_check2"],
    "err": ["deny", "x_white", "CB_redtick"],
    "warn": ["warning", "08excla"],
    "add": ["add"],
    "remove": ["remove"],
    "wait": ["cooldown"],
    "dm": ["telegram"],
    "left": ["left"],
    "right": ["right"],
    "lastfm": ["lastfm"],
    "spotify": ["spotify"],
    "telegram": ["telegram"],
    "eth": ["eth"],
    "dove": ["dove"],
    # profile card footer line: blue sparkle in front of the friend id, blue thumbs up in front of the likes
    "id": ["sparkle_blue", "id_sparkle"],
    "like": ["like_blue", "thumb_blue"],
    # gender ribbons in front of the name on the profile card (upload ribbon_female.png etc. as emojis)
    "ribbon_female": ["ribbon_female"],
    "ribbon_male": ["ribbon_male"],
    "ribbon_nonbinary": ["ribbon_nonbinary"],
}
# match type -> icon keys shown in front of the type name (heart = romantic, dove = friends, telegram = just chatting, spotify + lastfm = music)
TYPE_ICONS = {"romantic": ("heart",), "any": ("dove",), "chat": ("telegram",), "music": ("spotify", "lastfm")}
# message kind -> (icon key, bar color). note(..., kind="add") picks the icon and the bar from here.
KINDS = {
    "ok": ("ok", OK), "add": ("ok", OK), "remove": ("ok", OK), "dm": ("dm", OK),  # add / remove worked, so they get the same green tick as ok (no red minus on a green bar)
    "warn": ("warn", WARN), "err": ("err", ERR), "cooldown": ("wait", WARN), "wait": ("wait", WAIT),
}
BUTTON_ICONS = {
    "About You": "user", "Your Vibes": "sparkle", "Your Match": "heart", "Like Profile": "heart",
    "Find a Friend": "search", "Quick Find": "search", "Deep Find": "search", "My Profile": "card",
    "Suggestion": "bulb", "End chat": "exit", "Report": "flag", "Block": "block",
    "Approve": "ok", "Decline": "err", "Cancel": "err", "I understand - continue": "ok",
}


# matchmaking icon key -> the name in the bot's clean icon pack (ui_emojis.py), created as application emoji bl_<name>
MM_TO_PACK = {"ok": "check", "err": "cross", "warn": "warn", "add": "add", "remove": "remove", "wait": "wait",
              "left": "left", "right": "right", "search": "search"}


def mm_emoji(bot, key):
    """The custom emoji for an icon key, or None if the server doesn't have it. Order: an emoji named bl_<key> on any server, then the
    bot's own clean icon pack (application emoji bl_<pack name>), then the older server names in MM_EMOJI, then application emojis by those names."""
    mine = getattr(bot, "mm_app_emojis", None) or {}
    found = discord.utils.get(bot.emojis, name=f"bl_{key}") or mine.get(f"bl_{MM_TO_PACK.get(key, key)}")
    if found is None:
        for w in MM_EMOJI.get(key, []):
            found = next((e for e in bot.emojis if e.name.lower() == w.lower()), None)
            if found:
                break
    if found is None:  # emojis the bot created on its own account (application emojis), see make_app_emojis
        for w in MM_EMOJI.get(key, []):
            found = mine.get(w.lower())
            if found:
                break
    return found


def icon_text(bot, key):
    """The icon followed by a space for embed titles, or an empty string when it isn't available."""
    found = mm_emoji(bot, key)
    return f"{found} " if found else ""


def add_icons(bot, view):
    """Put the custom icons on a view's buttons (by button label). Missing icons are skipped. On a green / red / blurple button the white
    version of the icon (bl_<name>_w in the icon pack, e.g. check_w) is used when it exists, because a coloured icon would vanish there."""
    mine = getattr(bot, "mm_app_emojis", None) or {}
    for item in view.children:
        if isinstance(item, discord.ui.Button) and item.emoji is None:
            key = BUTTON_ICONS.get(item.label)
            icon = None
            if key and item.style != discord.ButtonStyle.secondary:
                icon = mine.get(f"bl_{MM_TO_PACK.get(key, key)}_w")
            if icon is None and key:
                icon = mm_emoji(bot, key)
            if icon:
                item.emoji = icon


# Avatar faces: every custom emoji named meep_* (in any server the bot is in) is an avatar choice.
# The saved value is the emoji's name. Old profiles that saved a cat name still work: they map to a
# meep emoji below, else to the plain cat emoji from AVATARS.
AVATAR_PREFIX = "meep_"
OLD_AVATAR_EMOJI = {
    "smirk": "meep_annoy", "happy": "meep_blush", "grin": "meep_bleh", "lol": "meep_silly",
    "cute": "meep_love", "sweet": "meep_sweet", "shocked": "meep_shocked", "sad": "meep_sad",
    "mad": "meep_mad",
}


def avatar_choices(bot):
    """All meep_* emojis the bot can see, in name order (a select menu holds 25 at most)."""
    found = {}
    for e in bot.emojis:
        if e.name.lower().startswith(AVATAR_PREFIX) and e.name.lower() not in found:
            found[e.name.lower()] = e
    return [found[k] for k in sorted(found)][:25]


def avatar_emoji(bot, name):
    """The custom emoji object for a saved avatar value, or None (then the plain cat emoji is used)."""
    name = (name or DEFAULT_AVATAR).lower()
    for wanted in (name, OLD_AVATAR_EMOJI.get(name)):
        if wanted:
            found = next((e for e in bot.emojis if e.name.lower() == wanted), None)
            if found:
                return found
    return None


def avatar_text(bot, name):
    """Avatar as text for titles and messages: custom emoji if available, else the cat emoji."""
    found = avatar_emoji(bot, name)
    if found:
        return str(found)
    return AVATARS.get(name, AVATARS[DEFAULT_AVATAR])


_BOT = None  # set when the cog loads, so note() can find the custom emojis without being handed the bot


def status_icon(kind):
    """The status emoji for a kind followed by a space, or nothing when the emoji isn't on the server."""
    key = KINDS[kind][0]
    found = icon_text(_BOT, key) if _BOT is not None else ""
    return found or STATUS_FALLBACK.get(key, "")


def type_icon(mtype):
    """Icon in front of a match type name (dove = friends, telegram = just chatting, spotify = music, heart = romantic)."""
    if _BOT is None:
        return ""
    return "".join(icon_text(_BOT, key) for key in TYPE_ICONS.get(mtype, ("dove",)))


def note(text, color=GREY, kind=None):
    """A clean one-block embed like Bleed's replies: status emoji + text, with a matching colored bar.

    The kind comes from the color (green = ok, red = err, amber = warn) unless you pass one:
    ok / add / remove / dm (green), warn / cooldown (amber), err (red), wait (blurple). Plain grey stays plain."""
    if kind is None:
        kind = {OK: "ok", ERR: "err", WARN: "warn"}.get(color)
    if kind is None:
        return discord.Embed(description=text, color=color)
    return discord.Embed(description=f"{status_icon(kind)}{text}", color=KINDS[kind][1])


def asset_file(name):
    """A fresh discord.File for an image in mm_assets.py (a File can only be sent once), or None."""
    raw = ASSETS.get(name)
    if not raw:
        return None
    try:
        return discord.File(io.BytesIO(base64.b64decode(raw)), filename=name)
    except Exception:
        log.exception("Bad image %s in mm_assets", name)
        return None


def file_kwargs(files):
    return {"files": files} if files else {}


def words(text):
    return {w for w in re.findall(r"[a-z0-9']{3,}", (text or "").lower()) if w not in STOP_WORDS}


def age_gap_text():
    """'up to 2 years older or younger than you' (so nobody reads the number as a waiting time)."""
    years = f"{MAX_AGE_GAP} year{'' if MAX_AGE_GAP == 1 else 's'}"
    return f"up to {years} older or younger than you"


def mode_text(mode):
    if mode == "deep":
        return "Deep Find (shared interests & lifestyle)"
    return "Quick Find (fastest match)"


def has_link(*texts):
    return any(LINK_RE.search(t or "") for t in texts)


def lookups_word(n):
    return f"{n} lookup{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------
# Container layout (the reference bot's cards). Needs discord.py 2.6+ (LayoutView and friends).
# On an older version the bot keeps the embed versions of everything that uses it.
# --------------------------------------------------------------------------
HAS_V2 = all(hasattr(discord.ui, n) for n in
             ("LayoutView", "Container", "Section", "TextDisplay", "Thumbnail", "Separator", "ActionRow"))
WARN_FALLBACK = "\u26A0\uFE0F "
STATUS_FALLBACK = {"ok": "\u2705 ", "err": "\u274C ", "warn": "\u26A0\uFE0F "}  # when the tick / cross / warning emoji can't be found


def wait_text(seconds) -> str:
    """Time left as '9m' (rounded up) or '45s'."""
    seconds = max(1, math.ceil(seconds))
    return f"{math.ceil(seconds / 60)}m" if seconds >= 60 else f"{seconds}s"


def box_view(text, kind=None, fallback_icon=""):
    """One plain container (no coloured bar) with a line of text: the reference bot's small notices."""
    icon = (status_icon(kind) if kind else "") or fallback_icon
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(f"{icon}{text}")))
    return view


def box_kwargs(text, kind=None, fallback_icon=""):
    """send() / reply() keyword arguments for a small notice: a container when possible, else the old embed."""
    if HAS_V2:
        try:
            return {"view": box_view(text, kind, fallback_icon)}
        except Exception:
            log.warning("Container notice failed, using an embed instead", exc_info=True)
    return {"embed": note(text, kind=kind)}


# ---- date of birth + timezone ----------------------------------------------
DOB_FORMATS = ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d",
               "%b %d %Y", "%B %d %Y")


def parse_dob(text) -> Optional[date]:
    """'2 Mar 2009', '02/03/2009' (day first), '2009-03-02' ... -> date, or None."""
    t = re.sub(r"\s+", " ", (text or "").replace(",", " ")).strip()
    for fmt in DOB_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def age_from(dob: date, today: Optional[date] = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


TZ_RE = re.compile(r"^(?:gmt|utc)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.I)


def tz_offset(text) -> Optional[float]:
    """Hours from UTC for 'GMT+1', 'UTC-5:30', 'Europe/Paris', 'Etc/GMT-1' ... or None when unknown."""
    t = (text or "").strip()
    if not t:
        return None
    if t.lower() in ("gmt", "utc", "z"):
        return 0.0
    m = TZ_RE.match(t)
    if m:
        hours, minutes = int(m.group(2)), int(m.group(3) or 0)
        if hours > 14 or minutes > 59:
            return None
        off = hours + minutes / 60
        return -off if m.group(1) == "-" else off
    if ZoneInfo is not None and re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+\-]+){1,2}", t):
        try:
            off = datetime.now(ZoneInfo(t)).utcoffset()
            return off.total_seconds() / 3600 if off is not None else None
        except Exception:
            return None
    return None


def gpref_list(value) -> list:
    """Preferable gender as a list of keys ([] = anyone). Old profiles stored one key (or 'any')."""
    if isinstance(value, (list, tuple, set)):
        return [g for g in ("male", "female", "nonbinary") if g in value]
    if value in GENDER_PREFS and value != "any":
        return [value]
    return []


def gpref_text(value) -> str:
    keys = gpref_list(value)
    return "Anyone" if not keys else ", ".join(GENDER_PREFS[k] for k in keys)


def bracket(age) -> str:
    return "adult" if age >= ADULT_AGE else "minor"


def types_for(age):
    """The match types a person of this age may choose (romantic is adults only)."""
    adult = age is not None and age >= ADULT_AGE
    return {k: v for k, v in TYPES.items() if adult or k not in ADULT_ONLY_TYPES}


def pair_str(a, b) -> str:
    return f"{min(a, b)}:{max(a, b)}"


def rules_embed():
    return note(
        f"{icon_text(_BOT, 'rules') if _BOT is not None else ''}**Before you start**\n"
        "- Matchmaking is for **making friends**. The **romantic** match type is only for adults (**18+**).\n"
        f"- You must be **{MIN_AGE} or older** (Discord's minimum age). Under-18s are only matched with other "
        "under-18s, and adults only with adults.\n"
        "- Your chat is passed through the bot so your Discord account stays hidden.\n"
        f"- For everyone's safety, **chats and activity are logged and can be read by the matchmaking staff**. "
        f"Logs are kept for {LOG_DAYS} days.\n"
        "- If you report someone, the last messages of the chat go to the moderators.\n"
        "- Never share your address, school, phone number or other personal info.\n"
        f"- If you say you're under {MIN_AGE} you can't use matchmaking.")


def panel_embed():
    e = discord.Embed(title="Customize your profile", color=GREY)
    e.description = (
        "Once you are ready, click on the buttons below to create your profile.\n\n"
        "**About You** - name, date of birth, gender and timezone\n"
        "**Your Vibes** - about you, likes, dislikes and avatar\n"
        "**Your Match** - the kind of friend you want\n\n"
        "Then press **Find a Friend**. Chats are passed through the bot so your Discord account stays hidden.")
    e.set_footer(text="Chats are logged for safety - romantic matches are 18+ only")
    return e


def panel_kwargs():
    """The panel embed, with the animated banner as its image when mm_assets.py is next to this file."""
    e = panel_embed()
    f = asset_file("banner.gif")
    if f:
        e.set_image(url="attachment://banner.gif")
        return {"embed": e, "file": f}
    return {"embed": e}


def match_prefs_embed(p):
    label = type_icon(p.get("mtype", "any")) + TYPES.get(p.get("mtype", "any"), TYPES["any"])[0]
    older = "on" if p.get("older_only") else "off"
    return note(
        "**Your Match**\n"
        f"Preferable gender: **{gpref_text(p.get('gpref'))}**\n"
        f"Preferable timezone: **{TZ_PREFS.get(p.get('tzpref', 'global'), TZ_PREFS['global'])}**\n"
        f"Preferable match type: **{label}**\n"
        f"Only need older match: **{older}**\n\n"
        f"You're only ever matched with people {age_gap_text()}.")


# --------------------------------------------------------------------------
# Storage (one JSON file, written atomically)
# --------------------------------------------------------------------------
class Store:
    def __init__(self, path):
        self.path = path
        self.data = {
            "profiles": {},     # user id -> profile
            "banned": {},       # user id -> reason (global, all servers)
            "underage": [],     # user ids that said they are under the minimum age (permanently refused)
            "blocks": {},       # user id -> [blocked user ids]
            "matches": {},      # user id -> {"partner": id, "since": ts} (both directions)
            "recent": {},       # user id -> {partner id: ts} to avoid instant rematches
            "reports": [],
            "guild_off": [],    # server ids where matchmaking is switched off
            "panels": {},       # channel id -> {"message_id", "guild_id"} for sticky sign-up panels
            "staff": [],        # user ids of matchmaking staff (set by the bot owner)
            "feed_channel": 0,  # private channel that mirrors everything live (set by the bot owner)
            "lookups": {},      # user id -> {"day": "YYYY-MM-DD", "used": n}
            "next_report": 1,
        }
        try:
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.exception("Could not read %s, starting empty", path)
        self.data.pop("minors", None)  # old 18+ gate list: not used any more

    def save(self):
        folder = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Could not save %s", self.path)
            try:
                os.remove(tmp)
            except OSError:
                pass


class AuditLog:
    """Append-only safety log (one JSON object per line). Every relayed chat message and every
    matchmaking event goes in here, so staff can see what happened. Old lines are pruned."""

    def __init__(self, path, days):
        self.path = path
        self.days = days

    def add(self, kind, **fields):
        entry = {"ts": int(time.time()), "kind": kind}
        entry.update({k: v for k, v in fields.items() if v is not None})
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            log.exception("Could not write the matchmaking log")
        return entry

    def _all(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except FileNotFoundError:
            return
        except OSError:
            log.exception("Could not read the matchmaking log")

    def read(self, kinds=None, involving=None, pair=None, since=0, limit=50):
        """The last `limit` matching entries, oldest first."""
        out = deque(maxlen=max(1, limit))
        for e in self._all():
            if e.get("ts", 0) < since:
                continue
            if kinds and e.get("kind") not in kinds:
                continue
            if pair and e.get("pair") != pair:
                continue
            if involving is not None and involving not in (e.get("uid"), e.get("partner")):
                continue
            out.append(e)
        return list(out)

    def count(self, kind, pair, since=0):
        return sum(1 for e in self._all()
                   if e.get("kind") == kind and e.get("pair") == pair and e.get("ts", 0) >= since)

    def prune(self):
        """Drop entries older than the retention period. Returns how many were removed."""
        cutoff = time.time() - self.days * 86400
        if not os.path.exists(self.path):
            return 0
        keep, dropped = [], 0
        for e in self._all():
            if e.get("ts", 0) >= cutoff:
                keep.append(e)
            else:
                dropped += 1
        if not dropped:
            return 0
        folder = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for e in keep:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Could not prune the matchmaking log")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return 0
        return dropped


# --------------------------------------------------------------------------
# Forms and buttons
# --------------------------------------------------------------------------
class AboutModal(discord.ui.Modal):
    """Step 1, About You: nickname, date of birth (first use only, locked once saved), timezone."""

    def __init__(self, cog, existing=None):
        super().__init__(title="About You", timeout=600)
        self.cog = cog
        self.editing = existing is not None
        ex = existing or {}
        self.nickname = discord.ui.TextInput(
            label="Enter your name", max_length=24, default=ex.get("name"),
            placeholder="What should your match call you?")
        self.add_item(self.nickname)
        self.dob = None
        if not self.editing:
            self.dob = discord.ui.TextInput(
                label="Your date of birth (locked once saved)", max_length=20, placeholder="2 Mar 2009")
            self.add_item(self.dob)
        self.tz = discord.ui.TextInput(
            label="Timezone (optional)", max_length=40, required=False, default=ex.get("tz") or None,
            placeholder="e.g. GMT+1 or Europe/Paris")
        self.add_item(self.tz)

    async def on_submit(self, interaction: discord.Interaction):
        cog = self.cog
        reason = cog.blocked_reason(interaction.user)
        if reason:
            return await interaction.response.send_message(embed=note(reason, ERR), ephemeral=True)
        name = self.nickname.value.strip()
        tz = (self.tz.value or "").strip()
        if not name:
            return await interaction.response.send_message(embed=note("Enter a name.", ERR), ephemeral=True)
        if has_link(name, tz):
            return await interaction.response.send_message(
                embed=note("Links aren't allowed in profiles.", ERR), ephemeral=True)
        if tz and tz_offset(tz) is None:
            return await interaction.response.send_message(
                embed=note("I don't know that timezone. Use something like `GMT+1`, `UTC-5` or `Europe/Paris`, "
                           "or leave it empty.", ERR), ephemeral=True)
        uid = str(interaction.user.id)
        profiles = cog.d["profiles"]
        if self.editing:
            profile = profiles.get(uid)
            if not profile:
                return await interaction.response.send_message(
                    embed=note("Profile not found. Press **About You** to start again.", ERR), ephemeral=True)
            action = "edited"
        else:
            dob = parse_dob(self.dob.value)
            if dob is None:
                return await interaction.response.send_message(
                    embed=note("I couldn't read that date. Write it like `2 Mar 2009` (day, month, year).", ERR),
                    ephemeral=True)
            today = datetime.now(timezone.utc).date()
            if dob > today:
                return await interaction.response.send_message(
                    embed=note("That date is in the future.", ERR), ephemeral=True)
            age = age_from(dob)
            if age < MIN_AGE:
                await cog.mark_underage(interaction.user.id)
                return await interaction.response.send_message(
                    embed=note(f"You need to be at least {MIN_AGE} to use matchmaking, so I can't make a profile "
                               "for you.", ERR), ephemeral=True)
            if age > MAX_AGE:
                return await interaction.response.send_message(
                    embed=note("Enter your real date of birth.", ERR), ephemeral=True)
            now = int(time.time())
            profile = {"dob": dob.isoformat(), "age": age, "agreed_at": now, "created": now, "pid": cog.new_pid(),
                       "mtype": "any", "older_only": False, "likers": [], "gender": None, "gpref": "any",
                       "tzpref": "global", "avatar": DEFAULT_AVATAR, "about": "", "likes": "", "dislikes": ""}
            profiles[uid] = profile
            action = "created"
        profile["name"] = name
        profile["tz"] = tz
        cog.store.save()
        cog.audit.add("profile", uid=interaction.user.id, action=action, step="about")
        cog.feed(f"\U0001F464 profile {action}: {cog.who(interaction.user.id)} (age {profile['age']})")
        nxt = "" if cog.ready(profile) else " Next, press **Your Vibes**."
        await interaction.response.send_message(
            embed=note("**About You** saved. Pick your gender below." + nxt, OK),
            view=GenderView(cog, interaction.user.id), ephemeral=True)


class VibesModal(discord.ui.Modal):
    """Step 2, Your Vibes: about me, likes (required) and dislikes. Then the avatar picker."""

    def __init__(self, cog, existing):
        super().__init__(title="Your Vibes", timeout=600)
        self.cog = cog
        ex = existing or {}
        self.about = discord.ui.TextInput(
            label="Introduce yourself (optional)", style=discord.TextStyle.paragraph, max_length=300,
            required=False, default=ex.get("about") or None, placeholder="A few lines about you")
        self.likes = discord.ui.TextInput(
            label="What do you like?", style=discord.TextStyle.paragraph, max_length=200,
            default=ex.get("likes") or None, placeholder="Music, anime, movies, hobbies...")
        self.dislikes = discord.ui.TextInput(
            label="What do you dislike? (optional)", style=discord.TextStyle.paragraph, max_length=200,
            required=False, default=ex.get("dislikes") or None)
        for item in (self.about, self.likes, self.dislikes):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        cog = self.cog
        reason = cog.blocked_reason(interaction.user)
        if reason:
            return await interaction.response.send_message(embed=note(reason, ERR), ephemeral=True)
        profile = cog.profile_of(interaction.user.id)
        if not profile:
            return await interaction.response.send_message(
                embed=note("Start with **About You** first.", ERR), ephemeral=True)
        about = (self.about.value or "").strip()
        likes = self.likes.value.strip()
        dislikes = (self.dislikes.value or "").strip()
        if not likes:
            return await interaction.response.send_message(
                embed=note("Tell me at least one thing you like.", ERR), ephemeral=True)
        if has_link(about, likes, dislikes):
            return await interaction.response.send_message(
                embed=note("Links aren't allowed in profiles.", ERR), ephemeral=True)
        profile["about"] = about
        profile["likes"] = likes
        profile["dislikes"] = dislikes
        cog.store.save()
        cog.audit.add("profile", uid=interaction.user.id, action="edited", step="vibes",
                      about=about, likes=likes, dislikes=dislikes)
        await interaction.response.send_message(
            embed=note("**Your Vibes** saved. Choose your avatar below, then set **Your Match** and press "
                       "**Find a Friend**.", OK),
            view=AvatarView(cog, interaction.user.id), ephemeral=True)


# --------------------------------------------------------------------------
# All-in-one forms: the dropdowns and the checkbox sit INSIDE the popup, like the reference bot.
# Needs discord.py 2.6+ (discord.ui.Label). On an older version the bot keeps the old flow
# (popup with text boxes, then the dropdowns in a small message).
# --------------------------------------------------------------------------
MODAL_SELECTS = hasattr(discord.ui, "Label")


def _label(text, component, description=None):
    return discord.ui.Label(text=text[:45], component=component, description=description)


def _select(placeholder, options, *, required=True, min_values=1, max_values=1):
    return discord.ui.Select(placeholder=placeholder, options=options, required=required,
                             min_values=min_values, max_values=max_values)


def _older_label(bot_name, default):
    """'Only need older match' as a real checkbox when this discord.py has one, else a Yes/No dropdown.
    Returns (label, read) where read() gives True/False after the form is submitted."""
    text = "Only need older match"
    desc = f"{bot_name} will only match with someone older than you"[:100]
    box_cls = getattr(discord.ui, "Checkbox", None)
    if box_cls is not None:
        try:
            box = box_cls(default=bool(default))
            return _label(text, box, desc), (lambda: bool(box.value))
        except Exception:
            log.warning("Checkbox in modals isn't available here, using a Yes/No dropdown instead", exc_info=True)
    sel = _select("Only need older match?", [
        discord.SelectOption(label="No", value="no", default=not default),
        discord.SelectOption(label="Yes - only someone older", value="yes", default=bool(default))])
    return _label(text, sel, desc), (lambda: bool(sel.values) and sel.values[0] == "yes")


def _bot_name(interaction):
    user = getattr(interaction.client, "user", None)
    return (user.display_name if user else None) or "I"


class FormModal(discord.ui.Modal):
    """Base for the three profile forms: a failure always tells the person instead of 'interaction failed'."""

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Matchmaking form failed", exc_info=error)
        msg = note("Something went wrong while saving that. Please try again.", ERR)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=msg, ephemeral=True)
            else:
                await interaction.response.send_message(embed=msg, ephemeral=True)
        except discord.HTTPException:
            pass


class AboutForm(FormModal):
    """Step 1, About You: name, date of birth (first time only, locked), gender dropdown, timezone."""

    def __init__(self, cog, existing=None):
        super().__init__(title="About You", timeout=600)
        self.cog = cog
        self.editing = existing is not None
        ex = existing or {}
        self.nickname = discord.ui.TextInput(max_length=24, default=ex.get("name"),
                                             placeholder="What should your match call you?")
        self.add_item(_label("Enter your name", self.nickname))
        self.dob = None
        if not self.editing:
            self.dob = discord.ui.TextInput(max_length=20, placeholder="2 Mar 2009")
            self.add_item(_label("Enter your date of birth", self.dob, "Locked once saved"))
        self.gender = _select("Select your gender", [
            discord.SelectOption(label=label, value=key, default=key == ex.get("gender"))
            for key, label in GENDERS.items()])
        self.add_item(_label("Select your gender", self.gender))
        self.tz = discord.ui.TextInput(max_length=40, required=False, default=ex.get("tz") or None,
                                       placeholder="e.g. GMT+1 or Europe/Paris")
        self.add_item(_label("Timezone (optional)", self.tz, "Used when you ask for a match close to you"))

    async def on_submit(self, interaction: discord.Interaction):
        cog = self.cog
        reason = cog.blocked_reason(interaction.user)
        if reason:
            return await interaction.response.send_message(embed=note(reason, ERR), ephemeral=True)
        name = self.nickname.value.strip()
        tz = (self.tz.value or "").strip()
        gender = self.gender.values[0] if self.gender.values else None
        if not name:
            return await interaction.response.send_message(embed=note("Enter a name.", ERR), ephemeral=True)
        if gender not in GENDERS:
            return await interaction.response.send_message(embed=note("Select your gender.", ERR), ephemeral=True)
        if has_link(name, tz):
            return await interaction.response.send_message(
                embed=note("Links aren't allowed in profiles.", ERR), ephemeral=True)
        if tz and tz_offset(tz) is None:
            return await interaction.response.send_message(
                embed=note("I don't know that timezone. Use something like `GMT+1`, `UTC-5` or `Europe/Paris`, "
                           "or leave it empty.", ERR), ephemeral=True)
        uid = str(interaction.user.id)
        profiles = cog.d["profiles"]
        if self.editing:
            profile = profiles.get(uid)
            if not profile:
                return await interaction.response.send_message(
                    embed=note("Profile not found. Press **About You** to start again.", ERR), ephemeral=True)
            action = "edited"
        else:
            dob = parse_dob(self.dob.value)
            if dob is None:
                return await interaction.response.send_message(
                    embed=note("I couldn't read that date. Write it like `2 Mar 2009` (day, month, year).", ERR),
                    ephemeral=True)
            if dob > datetime.now(timezone.utc).date():
                return await interaction.response.send_message(
                    embed=note("That date is in the future.", ERR), ephemeral=True)
            age = age_from(dob)
            if age < MIN_AGE:
                await cog.mark_underage(interaction.user.id)
                return await interaction.response.send_message(
                    embed=note(f"You need to be at least {MIN_AGE} to use matchmaking, so I can't make a profile "
                               "for you.", ERR), ephemeral=True)
            if age > MAX_AGE:
                return await interaction.response.send_message(
                    embed=note("Enter your real date of birth.", ERR), ephemeral=True)
            now = int(time.time())
            profile = {"dob": dob.isoformat(), "age": age, "agreed_at": now, "created": now, "pid": cog.new_pid(),
                       "mtype": "any", "older_only": False, "likers": [], "gender": None, "gpref": "any",
                       "tzpref": "global", "avatar": DEFAULT_AVATAR, "about": "", "likes": "", "dislikes": ""}
            profiles[uid] = profile
            action = "created"
        profile["name"] = name
        profile["tz"] = tz
        profile["gender"] = gender
        cog.store.save()
        cog.audit.add("profile", uid=interaction.user.id, action=action, step="about", gender=gender)
        cog.feed(f"\U0001F464 profile {action}: {cog.who(interaction.user.id)} (age {profile['age']})")
        nxt = "" if cog.ready(profile) else " Next, press **Your Vibes**."
        await interaction.response.send_message(
            embed=note(f"**{_bot_name(interaction)}** has updated your profile. Check `/match me` to see your "
                       f"more details.{nxt}", OK), ephemeral=True)


class VibesForm(FormModal):
    """Step 2, Your Vibes: about me, likes (required), dislikes, and the avatar dropdown."""

    def __init__(self, cog, existing):
        super().__init__(title="Your Vibes", timeout=600)
        self.cog = cog
        ex = existing or {}
        self.about = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=300, required=False,
                                          default=ex.get("about") or None, placeholder="A few lines about you")
        self.likes = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=200,
                                          default=ex.get("likes") or None, placeholder="Music, anime, movies, hobbies...")
        self.dislikes = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=200, required=False,
                                             default=ex.get("dislikes") or None)
        self.add_item(_label("Introduce yourself (optional)", self.about))
        self.add_item(_label("What do you like?", self.likes))
        self.add_item(_label("What do you dislike? (optional)", self.dislikes))
        choices = avatar_choices(cog.bot)
        if choices:  # emoji only: the option label is an invisible character, the name is never shown
            current = avatar_emoji(cog.bot, ex.get("avatar"))
            options = [discord.SelectOption(label="\u200b", value=e.name, emoji=e,
                                            default=bool(current and current.name == e.name)) for e in choices]
            placeholder = "Pick your face"
        else:  # no meep_* emojis on any server: the plain cat faces
            options = [discord.SelectOption(label=name, value=name, emoji=emoji,
                                            default=name == ex.get("avatar", DEFAULT_AVATAR))
                       for name, emoji in AVATARS.items()]
            placeholder = "Pick a cat face"
        self.avatar = _select(placeholder, options)
        self.add_item(_label("Choose your avatar", self.avatar))

    async def on_submit(self, interaction: discord.Interaction):
        cog = self.cog
        reason = cog.blocked_reason(interaction.user)
        if reason:
            return await interaction.response.send_message(embed=note(reason, ERR), ephemeral=True)
        profile = cog.profile_of(interaction.user.id)
        if not profile:
            return await interaction.response.send_message(
                embed=note("Start with **About You** first.", ERR), ephemeral=True)
        about = (self.about.value or "").strip()
        likes = self.likes.value.strip()
        dislikes = (self.dislikes.value or "").strip()
        if not likes:
            return await interaction.response.send_message(
                embed=note("Tell me at least one thing you like.", ERR), ephemeral=True)
        if has_link(about, likes, dislikes):
            return await interaction.response.send_message(
                embed=note("Links aren't allowed in profiles.", ERR), ephemeral=True)
        profile["about"] = about
        profile["likes"] = likes
        profile["dislikes"] = dislikes
        if self.avatar.values:
            profile["avatar"] = self.avatar.values[0]
        cog.store.save()
        cog.audit.add("profile", uid=interaction.user.id, action="edited", step="vibes",
                      about=about, likes=likes, dislikes=dislikes)
        await interaction.response.send_message(
            embed=note(f"**{_bot_name(interaction)}** has updated your profile. Check `/match me` to see your "
                       "more details. Next, set **Your Match** and press **Find a Friend**.", OK), ephemeral=True)


class MatchForm(FormModal):
    """Step 3, Your Match: preferable gender (pick any), timezone, match type, and the older-match checkbox."""

    def __init__(self, cog, profile, bot_name="I"):
        super().__init__(title="Your Match", timeout=600)
        self.cog = cog
        p = profile
        want = gpref_list(p.get("gpref"))
        self.gpref = _select("Who are you looking for?", [
            discord.SelectOption(label=label, value=key, default=key in want)
            for key, label in GENDER_PREFS.items() if key != "any"],
            required=False, min_values=0, max_values=3)
        self.add_item(_label("Preferable gender", self.gpref, "Pick one or more. Pick none for anyone"))
        self.tzpref = _select("Where should they be?", [
            discord.SelectOption(label=label, value=key, default=key == p.get("tzpref", "global"))
            for key, label in TZ_PREFS.items()])
        self.add_item(_label("Preferable timezone", self.tzpref))
        allowed = types_for(p.get("age"))
        self.mtype = _select("Choose a match type", [
            discord.SelectOption(label=label, value=key, default=key == p.get("mtype", "any"))
            for key, (label, _f) in allowed.items()])
        self.add_item(_label("Preferable match type", self.mtype))
        label, self._older = _older_label(bot_name, p.get("older_only"))
        self.add_item(label)

    async def on_submit(self, interaction: discord.Interaction):
        cog = self.cog
        reason = cog.blocked_reason(interaction.user)
        if reason:
            return await interaction.response.send_message(embed=note(reason, ERR), ephemeral=True)
        p = cog.profile_of(interaction.user.id)
        if not p:
            return await interaction.response.send_message(
                embed=note("Start with **About You** first.", ERR), ephemeral=True)
        mtype = self.mtype.values[0] if self.mtype.values else p.get("mtype", "any")
        if mtype not in types_for(p.get("age")):
            return await interaction.response.send_message(
                embed=note(f"The **{TYPES[mtype][0]}** match type is only for people aged {ADULT_AGE} and older.",
                           ERR), ephemeral=True)
        genders = gpref_list(list(self.gpref.values))
        p["gpref"] = genders or "any"
        p["tzpref"] = self.tzpref.values[0] if self.tzpref.values else "global"
        p["mtype"] = mtype
        p["older_only"] = self._older()
        cog.store.save()
        cog.audit.add("profile", uid=interaction.user.id, action="edited", step="match",
                      gpref=p["gpref"], tzpref=p["tzpref"], mtype=mtype, older_only=p["older_only"])
        await interaction.response.send_message(
            embeds=[note(f"**{_bot_name(interaction)}** has updated your profile. Check `/match me` to see your "
                         "more details.", OK), match_prefs_embed(p)], ephemeral=True)



class ReportModal(discord.ui.Modal, title="Report your match"):
    reason = discord.ui.TextInput(
        label="What happened?", style=discord.TextStyle.paragraph, max_length=500,
        placeholder="The last messages of this chat are sent to the moderators with your report.")

    def __init__(self, cog):
        super().__init__(timeout=600)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        rid = await self.cog.file_report(interaction.user.id, self.reason.value)
        if rid is None:
            return await interaction.response.send_message(
                embed=note("You're not in a chat right now.", ERR), ephemeral=True)
        await interaction.response.send_message(
            embed=note(f"Report #{rid} sent and the chat has ended. Thank you.", OK), ephemeral=True)


class SuggestionModal(discord.ui.Modal, title="Send a suggestion"):
    text = discord.ui.TextInput(
        label="Your suggestion", style=discord.TextStyle.paragraph, max_length=500,
        placeholder="Tell the staff what could be better.")

    def __init__(self, cog):
        super().__init__(timeout=600)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        msg, color = await self.cog.file_suggestion(interaction.user.id, self.text.value)
        await interaction.response.send_message(embed=note(msg, color), ephemeral=True)


class RulesGate(discord.ui.View):
    """Shown before the first profile: the rules (including the logging notice) and a continue button."""

    def __init__(self, cog, user_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        add_icons(cog.bot, self)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="I understand - continue", style=discord.ButtonStyle.success)
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AboutForm(self.cog) if MODAL_SELECTS else AboutModal(self.cog))
        self.stop()
        try:  # the rules message has done its job: remove it instead of leaving it on screen
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(embed=note("Okay, no profile was made.", kind="remove"), view=None)


class OwnProfileView(discord.ui.View):
    """Base for the small ephemeral select menus: only the person who opened it can use it."""

    def __init__(self, cog, user_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your menu.", ephemeral=True)
            return False
        return True

    async def need_profile(self, interaction):
        p = self.cog.profile_of(self.user_id)
        if not p:
            await interaction.response.send_message(
                embed=note("Start with **About You** first.", ERR), ephemeral=True)
        return p


class GenderView(OwnProfileView):
    """'Select your gender' (part of About You)."""

    def __init__(self, cog, user_id):
        super().__init__(cog, user_id)
        p = cog.profile_of(user_id) or {}
        for opt in self.pick.options:
            opt.default = opt.value == p.get("gender")

    @discord.ui.select(placeholder="Select your gender", min_values=1, max_values=1,
                       options=[discord.SelectOption(label=label, value=key) for key, label in GENDERS.items()])
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select):
        p = await self.need_profile(interaction)
        if not p:
            return
        p["gender"] = select.values[0]
        self.cog.store.save()
        self.cog.audit.add("profile", uid=self.user_id, action="edited", step="gender", gender=p["gender"])
        self.stop()
        await interaction.response.edit_message(embed=note(f"Gender saved: **{GENDERS[p['gender']]}**.", OK), view=None)


class AvatarView(OwnProfileView):
    """'Choose your avatar' (part of Your Vibes)."""

    def __init__(self, cog, user_id):
        super().__init__(cog, user_id)
        p = cog.profile_of(user_id) or {}
        choices = avatar_choices(cog.bot)
        if choices:  # emoji only: the option label is an invisible character, the name is never shown
            current = avatar_emoji(cog.bot, p.get("avatar"))
            self.pick.options = [
                discord.SelectOption(label="\u200b", value=e.name, emoji=e,
                                     default=bool(current and current.name == e.name))
                for e in choices]
            self.pick.placeholder = "Pick your face"
        else:  # no meep emojis on any server: keep the plain cat faces
            for opt in self.pick.options:
                opt.default = opt.value == p.get("avatar", DEFAULT_AVATAR)

    @discord.ui.select(placeholder="Pick a cat face", min_values=1, max_values=1,
                       options=[discord.SelectOption(label=name, value=name, emoji=emoji)
                                for name, emoji in AVATARS.items()])
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select):
        p = await self.need_profile(interaction)
        if not p:
            return
        p["avatar"] = select.values[0]
        self.cog.store.save()
        self.stop()
        await interaction.response.edit_message(
            embed=note(f"Avatar saved: {avatar_text(self.cog.bot, p['avatar'])}", OK), view=None)


class MatchPrefsView(OwnProfileView):
    """Step 3, Your Match: preferable gender / timezone / match type + 'only older match'. Saves on every click."""

    def __init__(self, cog, user_id):
        super().__init__(cog, user_id)
        p = cog.profile_of(user_id) or {}
        allowed = types_for(p.get("age"))
        self.pick.options = [discord.SelectOption(label=label, value=key) for key, (label, _f) in allowed.items()]
        self._sync(p)

    def _sync(self, p):
        keys = gpref_list(p.get("gpref"))
        current = {"gpref": keys[0] if len(keys) == 1 else "any", "tzpref": p.get("tzpref", "global"),
                   "mtype": p.get("mtype", "any")}
        for select, key in ((self.gpref, "gpref"), (self.tzpref, "tzpref"), (self.pick, "mtype")):
            for opt in select.options:
                opt.default = opt.value == current[key]
        on = bool(p.get("older_only"))
        self.older.label = "Only need older match: ON" if on else "Only need older match: OFF"
        self.older.style = discord.ButtonStyle.success if on else discord.ButtonStyle.secondary
        pill = (getattr(self.cog.bot, "mm_app_emojis", None) or {}).get("bl_on" if on else "bl_off")  # the clean on / off pill, when the icon pack exists
        if pill is not None:
            self.older.emoji = pill
            self.older.style = discord.ButtonStyle.secondary  # the pill shows on / off; a green button would hide the green pill

    async def _save(self, interaction, key, value):
        p = await self.need_profile(interaction)
        if not p:
            return
        p[key] = value
        self.cog.store.save()
        self._sync(p)
        await interaction.response.edit_message(embed=match_prefs_embed(p), view=self)

    @discord.ui.select(placeholder="Preferable gender", min_values=1, max_values=1, row=0,
                       options=[discord.SelectOption(label=label, value=key) for key, label in GENDER_PREFS.items()])
    async def gpref(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self._save(interaction, "gpref", select.values[0])

    @discord.ui.select(placeholder="Preferable timezone", min_values=1, max_values=1, row=1,
                       options=[discord.SelectOption(label=label, value=key) for key, label in TZ_PREFS.items()])
    async def tzpref(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self._save(interaction, "tzpref", select.values[0])

    @discord.ui.select(placeholder="Preferable match type", min_values=1, max_values=1, row=2,
                       options=[discord.SelectOption(label=label, value=key) for key, (label, _f) in TYPES.items()])
    async def pick(self, interaction: discord.Interaction, select: discord.ui.Select):
        p = self.cog.profile_of(self.user_id)
        if p and select.values[0] not in types_for(p.get("age")):
            return await interaction.response.send_message(
                embed=note(f"The **{TYPES[select.values[0]][0]}** match type is only for people aged "
                           f"{ADULT_AGE} and older.", ERR), ephemeral=True)
        await self._save(interaction, "mtype", select.values[0])

    @discord.ui.button(label="Only need older match: OFF", style=discord.ButtonStyle.secondary, row=3)
    async def older(self, interaction: discord.Interaction, button: discord.ui.Button):
        p = self.cog.profile_of(self.user_id)
        if not p:
            return await interaction.response.send_message(
                embed=note("Start with **About You** first.", ERR), ephemeral=True)
        await self._save(interaction, "older_only", not p.get("older_only"))


class ModeView(discord.ui.View):
    """Quick Find / Deep Find choice for the panel's Find a Friend button."""

    def __init__(self, cog, user, guild):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.guild = guild
        add_icons(cog.bot, self)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This isn't your menu.", ephemeral=True)
            return False
        return True

    async def _go(self, interaction, mode):
        await interaction.response.defer()
        text, color = await self.cog.start_search(self.user, self.guild, mode)
        self.stop()
        await interaction.edit_original_response(embed=note(text, color), view=None)

    @discord.ui.button(label="Quick Find", style=discord.ButtonStyle.primary)
    async def quick(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, "quick")

    @discord.ui.button(label="Deep Find", style=discord.ButtonStyle.secondary)
    async def deep(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, "deep")


class LeaveConfirm(discord.ui.View):
    """'Leave this match?' with Approve / Decline."""

    def __init__(self, cog, user_id):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        add_icons(cog.bot, self)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your menu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        text = await self.cog.do_leave(self.user_id)
        self.stop()
        await interaction.response.edit_message(embed=note(text), view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(embed=note("Okay, you're still matched.", kind="ok"), view=None)


class RevealView(discord.ui.View):
    """Accept / Decline under a username swap request. Only the person who was asked can press them."""

    def __init__(self, cog, asker, target):
        super().__init__(timeout=REVEAL_SECONDS)
        self.cog, self.asker, self.target = cog, asker, target
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.target:
            await interaction.response.send_message(embed=note("This request isn't for you.", kind="warn"), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        live = self.cog.reveal_wait.pop((self.asker, self.target), 0) > time.time()
        if not live:
            text, kind = "This request has expired or was already answered.", "warn"
        elif await self.cog.do_reveal(self.asker, self.target):
            text, kind = "You swapped usernames. Check your DMs.", "ok"
        else:
            text, kind = "That chat is over, so there's nothing to swap.", "warn"
        await interaction.response.edit_message(embed=note(text, kind=kind), view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        self.cog.reveal_wait.pop((self.asker, self.target), None)
        self.cog.reveal_cool[(self.asker, self.target)] = time.time() + 300
        await interaction.response.edit_message(embed=note("Declined. They won't see your username.", kind="ok"), view=None)
        await self.cog.dm(self.asker, embed=note("Your match didn't want to swap usernames. The chat goes on.", kind="warn"))

    async def on_timeout(self):
        self.cog.reveal_wait.pop((self.asker, self.target), None)
        if self.message is not None:
            try:
                await self.message.edit(embed=note("This request expired.", kind="warn"), view=None)
            except discord.HTTPException:
                pass


if HAS_V2:
    class LeaveLayout(discord.ui.LayoutView):
        """'Leave this match?' as a container, with green Approve / red Decline buttons under it."""

        def __init__(self, cog, user_id, body, fine_print=None):
            super().__init__(timeout=120)
            self.cog = cog
            self.user_id = user_id
            parts = [discord.ui.TextDisplay(body)]
            if fine_print:
                parts += [discord.ui.Separator(), discord.ui.TextDisplay(fine_print)]
            self.add_item(discord.ui.Container(*parts))
            approve = discord.ui.Button(label="Approve", style=discord.ButtonStyle.success)
            decline = discord.ui.Button(label="Decline", style=discord.ButtonStyle.danger)
            approve.callback = self.on_approve
            decline.callback = self.on_decline
            self.add_item(discord.ui.ActionRow(approve, decline))

        async def interaction_check(self, interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This isn't your menu.", ephemeral=True)
                return False
            return True

        async def on_approve(self, interaction: discord.Interaction):
            text = await self.cog.do_leave(self.user_id)
            self.stop()
            await interaction.response.edit_message(view=box_view(text))

        async def on_decline(self, interaction: discord.Interaction):
            self.stop()
            await interaction.response.edit_message(view=box_view("Okay, you're still matched.", "ok"))


def card_buttons(cog):
    """Like Profile / Report under the match card. Their custom ids are fixed, so they keep working after a restart."""
    like = discord.ui.Button(label="Like Profile", style=discord.ButtonStyle.secondary, custom_id="mm2:like")
    report = discord.ui.Button(label="Report", style=discord.ButtonStyle.secondary, custom_id="mm2:report")

    async def on_like(interaction: discord.Interaction):
        await cog.like_partner(interaction)

    async def on_report(interaction: discord.Interaction):
        await cog.report_partner(interaction)

    like.callback = on_like
    report.callback = on_report
    return discord.ui.ActionRow(like, report)


def card_buttons_view(cog):
    """Only used to register the persistent buttons when the bot starts."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(card_buttons(cog))
    return view


class MatchActions(discord.ui.View):
    """Buttons under the match DM. Persistent: they keep working after a restart."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        add_icons(cog.bot, self)

    @discord.ui.button(label="Like Profile", style=discord.ButtonStyle.secondary, custom_id="mm:like")
    async def like(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.like_partner(interaction)

    @discord.ui.button(label="End chat", style=discord.ButtonStyle.secondary, custom_id="mm:end")
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button):
        prompt = self.cog.leave_prompt(interaction.user.id)
        if not prompt:
            return await interaction.response.send_message(embed=note("You're not in a chat.", kind="warn"), ephemeral=True)
        await interaction.response.send_message(**prompt, ephemeral=True)

    @discord.ui.button(label="Report", style=discord.ButtonStyle.danger, custom_id="mm:report")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.report_partner(interaction)

    @discord.ui.button(label="Block", style=discord.ButtonStyle.secondary, custom_id="mm:block")
    async def block(self, interaction: discord.Interaction, button: discord.ui.Button):
        partner = await self.cog.block_partner(interaction.user.id)
        text = "Blocked. You won't be matched with them again." if partner else "You're not in a chat."
        await interaction.response.send_message(embed=note(text), ephemeral=True)


class PanelView(discord.ui.View):
    """The sign-up panel's buttons. Persistent: they keep working after a restart."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        add_icons(cog.bot, self)

    @discord.ui.button(label="About You", style=discord.ButtonStyle.secondary, custom_id="mm:panel:about")
    async def about(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_about(interaction)

    @discord.ui.button(label="Your Vibes", style=discord.ButtonStyle.secondary, custom_id="mm:panel:vibes")
    async def vibes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_vibes(interaction)

    @discord.ui.button(label="Your Match", style=discord.ButtonStyle.secondary, custom_id="mm:panel:match")
    async def match_prefs(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_match(interaction)

    @discord.ui.button(label="Find a Friend", style=discord.ButtonStyle.success, custom_id="mm:panel:find", row=1)
    async def find(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=note("**How should I look?**\n**Quick Find**: the fastest match.\n"
                       "**Deep Find**: matches you on shared interests & lifestyle, so it can take a bit longer.\n\n"
                       f"Either way, I only match you with people {age_gap_text()}."),
            view=ModeView(self.cog, interaction.user, interaction.guild), ephemeral=True)

    @discord.ui.button(label="My Profile", style=discord.ButtonStyle.secondary, custom_id="mm:panel:me", row=1)
    async def me(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if not self.cog.profile_of(uid):
            return await interaction.response.send_message(
                embed=note("You don't have a profile yet. Press **About You**."), ephemeral=True)
        await self.cog.show_card(interaction, uid, f"Status: {self.cog.state_of(uid)}")

    @discord.ui.button(label="Suggestion", style=discord.ButtonStyle.secondary, custom_id="mm:panel:suggest", row=1)
    async def suggest(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SuggestionModal(self.cog))


# --------------------------------------------------------------------------
# The cog
# --------------------------------------------------------------------------
class Matchmaking(commands.Cog):
    def __init__(self, bot):
        global _BOT
        _BOT = bot
        self.bot = bot
        self.store = Store(MATCH_FILE)
        self.audit = AuditLog(LOG_FILE, LOG_DAYS)
        self.queue = {}        # user id -> time they joined (memory only)
        self.modes = {}        # user id -> "quick" | "deep" for everyone in the queue
        self.pair_lock = asyncio.Lock()
        self.reveal_wait = {}  # (who asked, who was asked) -> when the username swap request expires
        self.reveal_cool = {}  # (who asked, who was asked) -> no new request before this time (after a decline)
        self.ice_last = {}     # (smaller id, bigger id) -> [time of the last icebreaker, its index]
        self.rate = {}         # user id -> recent message times
        self.last_find = {}    # user id -> last time they pressed find (cooldown)
        self.last_suggest = {}  # user id -> last suggestion time (cooldown)
        self.panel_pending = set()  # channel ids with a sticky refresh already scheduled
        self._cmd_ids = {}     # slash command name -> id (for clickable </match find:id> mentions)
        self._cmd_ids_at = 0
        self.feed_q = asyncio.Queue(maxsize=1000)
        self._feed_task = None
        self._tasks = set()

    async def cog_load(self):
        self.bot.add_view(MatchActions(self))
        self.bot.add_view(PanelView(self))
        if HAS_V2:
            try:
                self.bot.add_view(card_buttons_view(self))
            except Exception:
                log.warning("Couldn't register the container card buttons", exc_info=True)
        self.expire_queue.start()
        self.prune_logs.start()
        self._feed_task = asyncio.create_task(self._feed_worker())
        self._emoji_task = asyncio.create_task(self.make_app_emojis())

    async def cog_unload(self):
        self.expire_queue.cancel()
        self.prune_logs.cancel()
        if self._feed_task:
            self._feed_task.cancel()

    # ---- helpers --------------------------------------------------------
    @property
    def d(self):
        return self.store.data

    @staticmethod
    def ready(p) -> bool:
        """A profile can be matched once About You and Your Vibes are both filled in."""
        return bool(p and p.get("name") and p.get("likes"))

    def new_pid(self) -> int:
        used = {p.get("pid") for p in self.d["profiles"].values()}
        for _ in range(100):
            pid = random.randint(100000, 999999)
            if pid not in used:
                return pid
        return random.randint(10 ** 7, 10 ** 8)

    def profile_of(self, uid):
        """The stored profile (filling in fields that older profiles don't have yet), or None."""
        p = self.d["profiles"].get(str(uid))
        if p is None:
            return None
        changed = False
        for key, value in (("pid", None), ("mtype", "any"), ("older_only", False), ("likers", None),
                           ("tz", ""), ("about", ""), ("likes", ""), ("dislikes", ""), ("gender", None),
                           ("gpref", "any"), ("tzpref", "global"), ("avatar", DEFAULT_AVATAR), ("dob", None)):
            if key not in p:
                p[key] = self.new_pid() if key == "pid" else ([] if key == "likers" else value)
                changed = True
        if p.get("mtype") not in TYPES:  # e.g. the removed gaming type
            p["mtype"] = "any"
            changed = True
        if p.get("dob"):
            try:
                age = age_from(date.fromisoformat(p["dob"]))
            except ValueError:
                age = p.get("age")
            if age != p.get("age"):
                p["age"] = age
                changed = True
        if p.get("mtype") in ADULT_ONLY_TYPES and p.get("mtype") not in types_for(p.get("age")):
            p["mtype"] = "any"
            changed = True
        if changed:
            self.store.save()
        return p

    def tag(self, uid) -> str:
        p = self.profile_of(uid)
        return f"{p['name']}#{p['pid']}" if p else "your match"

    def who(self, uid) -> str:
        """Name#friendId (`discord id`) for staff views, or just the id when the profile is gone."""
        p = self.d["profiles"].get(str(uid))
        if p and p.get("name"):
            return f"{p['name']}#{p.get('pid')} (`{uid}`)"
        return f"`{uid}`"

    def state_of(self, uid) -> str:
        if str(uid) in self.d["matches"]:
            return "in a chat"
        return "searching" if uid in self.queue else "idle"

    def dequeue(self, uid) -> bool:
        self.modes.pop(uid, None)
        return self.queue.pop(uid, None) is not None

    def blocked_reason(self, user) -> Optional[str]:
        uid = str(user.id)
        if uid in self.d["banned"]:
            return "You can't use matchmaking."
        if uid in self.d["underage"]:
            return f"You need to be at least {MIN_AGE} to use matchmaking."
        age_days = (discord.utils.utcnow() - user.created_at).days
        if age_days < MIN_ACCOUNT_AGE_DAYS:
            return f"Your Discord account must be at least {MIN_ACCOUNT_AGE_DAYS} days old to use matchmaking."
        return None

    async def dm(self, uid, **kwargs) -> bool:
        try:
            user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            await user.send(allowed_mentions=discord.AllowedMentions.none(), **kwargs)
            return True
        except discord.HTTPException:
            return False

    def card_parts(self, uid, label=None):
        """(embed, files) for a profile card: ribbon for the friend type, name (age), about/likes/dislikes."""
        p = self.profile_of(uid)
        created = datetime.fromtimestamp(p.get("created") or time.time(), timezone.utc)
        avatar = avatar_text(self.bot, p.get("avatar"))
        e = discord.Embed(title=f"{avatar} {p['name']} ({p['age']})", color=GREY, timestamp=created)
        extra = []
        if p.get("gender") and p["gender"] != "private":
            extra.append(GENDERS.get(p["gender"], p["gender"]))
        if p.get("tz"):
            extra.append(p["tz"])
        head = (" \u2022 ".join(extra) + "\n\n") if extra else ""
        e.description = (f"{head}**About me**\n{p.get('about') or 'Nothing written yet.'}\n\n"
                         f"**My likes..**\n{p.get('likes') or 'Nothing yet.'}\n\n"
                         f"**My dislikes..**\n{p.get('dislikes') or 'Nothing in particular.'}")
        if label:
            e.set_author(name=label)
        n = len(p["likers"])
        likes = f"{n} like{'' if n == 1 else 's'}"
        id_icon, like_icon = mm_emoji(self.bot, "id"), mm_emoji(self.bot, "like")
        if id_icon and like_icon:  # footers can't show custom emojis, so the line lives in the card text
            e.description += f"\n\n{id_icon} friend id {p['pid']} \u2022 {like_icon} {likes}"
        else:
            e.set_footer(text=f"friend id {p['pid']} \u2022 {likes}")
        files = []
        _label, ribbon = TYPES.get(p.get("mtype", "any"), TYPES["any"])
        f = asset_file(ribbon)
        if f:
            e.set_image(url=f"attachment://{ribbon}")
            files.append(f)
        return e, files

    async def make_app_emojis(self):
        """Create the bot's own emojis (card emojis, gambling pictures and the clean UI icon pack) on the bot's own account when no
        server has them, so nothing has to be uploaded by hand. Needs discord.py 2.5+; otherwise upload the PNGs yourself.
        Runs on start-up and again with the emojisync command. Returns {"made", "replaced", "failed", "total"}, {"unsupported": True}
        on an old discord.py, or {"error": True}. Two runs at the same time wait for each other."""
        lock = getattr(self, "_emoji_lock", None)
        if lock is None:
            lock = self._emoji_lock = asyncio.Lock()
        async with lock:
            return await self._make_app_emojis()

    async def _make_app_emojis(self):
        try:
            await self.bot.wait_until_ready()
            if not hasattr(self.bot, "create_application_emoji"):
                log.info("This discord.py can't create application emojis; upload the card emoji PNGs by hand")
                return {"unsupported": True}
            mine = {e.name.lower(): e for e in await self.bot.fetch_application_emojis()}
            old = mine.pop("aura", None)  # the old purple person emoji is gone: remove it from the bot's account
            if old is not None:
                try:
                    await old.delete()
                    log.info("Removed the old aura emoji")
                except Exception:
                    log.warning("Couldn't remove the old aura emoji", exc_info=True)
            self.bot.mm_app_emojis = mine
            hashes = load_emoji_hashes()
            made = failed = replaced = 0
            for name, data in EMOJI_PACK.items():
                raw = base64.b64decode("".join(data))
                digest = hashlib.sha1(raw).hexdigest()
                live = mine.get(name)
                if live is not None:
                    # An emoji that already exists is only replaced when its picture was changed in this pack: either the
                    # remembered hash differs, or nothing is remembered yet and the name is in EMOJI_REDRAWN.
                    known = hashes.get(name)
                    if (known == digest) or (known is None and name not in EMOJI_REDRAWN):
                        hashes.setdefault(name, digest)
                        continue
                    try:
                        await live.delete()
                        mine.pop(name, None)
                        replaced += 1
                    except Exception:
                        log.warning("Couldn't remove the old %s emoji (it is tried again on the next start)", name, exc_info=True)
                        continue
                elif any(e.name.lower() == name for e in self.bot.emojis):
                    continue
                try:
                    mine[name] = await self.bot.create_application_emoji(name=name, image=raw)
                    hashes[name] = digest
                    made += 1
                    log.info("Created the %s emoji", name)
                except discord.HTTPException:
                    failed += 1
                    log.warning("Couldn't create the %s emoji (it is tried again on the next start)", name, exc_info=True)
            save_emoji_hashes(hashes)
            if made or failed:
                log.info("Emoji pack: %s created (%s of them replaced a changed picture), %s failed", made, replaced, failed)
            return {"made": made, "replaced": replaced, "failed": failed, "total": len(mine)}
        except Exception:
            log.warning("Couldn't set up the card emojis", exc_info=True)
            return {"error": True}

    # ---- stats, username swap, icebreakers, invite ------------------------------------
    def bump_match_stats(self, uid):
        """Count a new match for this person: total matches and a streak of days in a row with a match."""
        p = self.d["profiles"].get(str(uid))
        if p is None:
            return
        today = datetime.now(timezone.utc).date()
        p["matches"] = p.get("matches", 0) + 1
        last = p.get("streak_day")
        if last == today.isoformat():
            pass
        elif last == (today - timedelta(days=1)).isoformat():
            p["streak"] = p.get("streak", 0) + 1
        else:
            p["streak"] = 1
        p["streak_day"] = today.isoformat()
        p["best_streak"] = max(p.get("best_streak", 0), p.get("streak", 0))

    def stats_text(self, uid):
        """The person's own numbers, or None when they have no profile."""
        p = self.profile_of(uid)
        if p is None:
            return None
        today = datetime.now(timezone.utc).date()
        alive = p.get("streak_day") in (today.isoformat(), (today - timedelta(days=1)).isoformat())
        streak = p.get("streak", 0) if alive else 0
        best = max(p.get("best_streak", 0), streak)
        return (f"**Your matchmaking stats**\n"
                f"Matches: **{p.get('matches', 0)}**\n"
                f"Profile likes: **{len(p['likers'])}**\n"
                f"Streak: **{streak}** day{'' if streak == 1 else 's'} (best **{best}**)")

    def bot_stats_text(self) -> str:
        guilds = self.bot.guilds
        members = sum(g.member_count or 0 for g in guilds)
        made = self.d.get("totals", {}).get("matches", 0)
        return (f"**Bot stats**\n"
                f"Servers: **{len(guilds):,}**\n"
                f"Members: **{members:,}** (across all servers)\n"
                f"Profiles: **{len(self.d['profiles']):,}**\n"
                f"Matches made: **{made:,}**\n"
                f"Chatting now: **{len(self.d['matches']) // 2}** \u2022 Searching now: **{len(self.queue)}**")

    def invite_url(self) -> str:
        return discord.utils.oauth_url(self.bot.user.id, permissions=INVITE_PERMISSIONS,
                                       scopes=("bot", "applications.commands"))

    async def send_icebreaker(self, uid):
        """Send a conversation starter to both people in this chat. Returns (kind, text) for the reply."""
        m = self.d["matches"].get(str(uid))
        if not m:
            return "warn", "You're not in a chat."
        partner = m["partner"]
        key = tuple(sorted((uid, partner)))
        last = self.ice_last.get(key)
        now = time.time()
        if last and now - last[0] < ICEBREAKER_COOLDOWN:
            return "cooldown", f"Slow down, try again in **{math.ceil(ICEBREAKER_COOLDOWN - (now - last[0]))}s**."
        choices = [i for i in range(len(ICEBREAKERS)) if not last or i != last[1]]
        index = random.choice(choices)
        self.ice_last[key] = [now, index]
        spark = icon_text(self.bot, "sparkle")
        embed = note(f"{spark}**Icebreaker:** {ICEBREAKERS[index]}")
        sent = [await self.dm(x, embed=embed) for x in (uid, partner)]
        self.audit.add("icebreaker", uid=uid, partner=partner)
        if not all(sent):
            return "warn", "I couldn't reach both of you, so the icebreaker may not have arrived."
        return "ok", "Sent an icebreaker to your chat."

    async def request_reveal(self, uid):
        """Ask the match to swap Discord usernames. Returns (kind, text) for the reply."""
        m = self.d["matches"].get(str(uid))
        if not m:
            return "warn", "You're not in a chat."
        partner = m["partner"]
        ages = [(self.profile_of(x) or {}).get("age") or 0 for x in (uid, partner)]
        if min(ages) < REVEAL_MIN_AGE:
            return "warn", f"Swapping usernames is only for matches where you're both **{REVEAL_MIN_AGE}** or older."
        now = time.time()
        if self.reveal_wait.get((partner, uid), 0) > now:  # they already asked you: that's a yes from both
            self.reveal_wait.pop((partner, uid), None)
            if await self.do_reveal(partner, uid):
                return "ok", "They had already asked, so you swapped usernames. Check your DMs."
            return "warn", "That didn't work, try again."
        if self.reveal_wait.get((uid, partner), 0) > now:
            return "warn", "You already asked. Wait for their answer."
        if self.reveal_cool.get((uid, partner), 0) > now:
            return "warn", "They said no. Give it some time before asking again."
        user = self.bot.get_user(partner)
        try:
            user = user or await self.bot.fetch_user(partner)
            view = RevealView(self, uid, partner)
            view.message = await user.send(embed=note(
                "**Your match wants to swap Discord usernames.**\nIf you accept, you'll both see each other's username. "
                "Only accept if you're comfortable with that."), view=view)
        except discord.HTTPException:
            return "warn", "I couldn't reach them right now."
        self.reveal_wait[(uid, partner)] = now + REVEAL_SECONDS
        self.audit.add("reveal", uid=uid, partner=partner, action="asked")
        self.feed(f"\U0001F517 reveal asked: {self.who(uid)} \u2192 {self.who(partner)}")
        return "ok", "Request sent. If they accept, you'll both get each other's username."

    async def do_reveal(self, a, b) -> bool:
        """Both agreed: DM each person the other's Discord username."""
        ma, mb = self.d["matches"].get(str(a)), self.d["matches"].get(str(b))
        if not ma or not mb or ma["partner"] != b or mb["partner"] != a:
            return False
        users = {}
        for x in (a, b):
            try:
                users[x] = self.bot.get_user(x) or await self.bot.fetch_user(x)
            except discord.HTTPException:
                return False
        for me, other in ((a, b), (b, a)):
            await self.dm(me, embed=note(
                f"**Swapped!** Your match is **{users[other].name}** ({users[other].mention}). "
                "Add them as a friend if you want to keep talking.", kind="ok"))
        self.audit.add("reveal", uid=a, partner=b, action="swapped")
        self.feed(f"\U0001F517 usernames swapped: {self.who(a)} \u2194 {self.who(b)}")
        return True

    # ---- container cards (cupi layout) --------------------------------------
    def profile_items(self, uid, label=None, extra=None):
        """The profile card's parts: ribbon + name (+ avatar picture on the right), About me, likes / dislikes,
        a divider and the friend id / likes line."""
        p = self.profile_of(uid)
        text = discord.ui.TextDisplay
        name = discord.utils.escape_markdown(p["name"])
        gender = p.get("gender")
        ribbon = mm_emoji(self.bot, GENDER_RIBBONS[gender]) if gender in GENDER_RIBBONS else None
        lines = []
        if label:
            lines.append(f"-# {label}")
        lines.append(f"# {ribbon} {name} ({p['age']})" if ribbon else f"# {name} ({p['age']})")
        if gender in GENDER_RIBBONS and not ribbon:  # ribbon emoji not uploaded yet: say it in words
            lines.append(f"-# {GENDERS[gender]}")
        mtype = p.get("mtype", "any")
        if mtype != "any" and mtype in TYPES:  # the default "friends" shows nothing, like the reference card
            lines.append(f"-# {type_icon(mtype)}{TYPES[mtype][0]}")
        head = text("\n".join(lines))
        about = text(f"**About me**\n{p.get('about') or 'Nothing written yet.'}")
        emoji = avatar_emoji(self.bot, p.get("avatar"))
        picture = getattr(emoji, "url", None) if emoji else None
        if picture:
            items = [discord.ui.Section(head, about, accessory=discord.ui.Thumbnail(picture))]
        else:
            items = [head, about]
        items.append(text(f"**My likes..**\n{p.get('likes') or 'Nothing yet.'}\n\n"
                          f"**My dislikes..**\n{p.get('dislikes') or 'Nothing in particular.'}"))
        n = len(p["likers"])
        id_icon, like_icon = mm_emoji(self.bot, "id"), mm_emoji(self.bot, "like")
        left = f"{id_icon} friend id {p['pid']}" if id_icon else f"friend id {p['pid']}"
        likes = f"{n} like{'' if n == 1 else 's'}"
        right = f"{like_icon} {likes}" if like_icon else likes
        items.append(discord.ui.Separator())
        items.append(text(f"{left} \u2022 {right}"))
        if extra:
            items.append(text(f"-# {extra}"))
        return items

    def profile_view(self, uid, label=None, extra=None):
        view = discord.ui.LayoutView()
        view.add_item(discord.ui.Container(*self.profile_items(uid, label, extra)))
        return view

    def match_view(self, uid):
        """What someone receives when they're matched with `uid`: the card, the 'drop a hello' box and the buttons."""
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(*self.profile_items(uid)))
        heart = icon_text(self.bot, "heart") or "\U0001F496 "
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(
            f"{heart}drop a **hello in the chat here** while you wait for their reply.\n"
            "-# Chats are logged for safety. No links or files. `/match end` leaves, `/match reveal` swaps usernames (18+).")))
        view.add_item(card_buttons(self))
        return view

    async def send_match_card(self, me, other, mode="quick") -> bool:
        """DM `me` the card of `other`. True when it arrived."""
        if HAS_V2:
            try:
                view = self.match_view(other)
                user = self.bot.get_user(me) or await self.bot.fetch_user(me)
                await user.send(view=view, allowed_mentions=discord.AllowedMentions.none())
                return True
            except discord.Forbidden:  # their DMs are closed
                return False
            except Exception:
                log.warning("Container card failed for %s, sending the embed card instead", me, exc_info=True)
        intro = note(
            f"**Found someone for you!** ({MODE_NAMES.get(mode, 'Quick Match')})\n"
            f"Matched with **{self.tag(other)}**. Say hi - everything you type here is passed to them "
            "anonymously by the bot (no links or files). Chats are logged and can be reviewed by the "
            "matchmaking staff for safety. Never share personal info. "
            "Use the buttons below to like their profile, end the chat, report, or block.", OK)
        card, files = self.card_parts(other, "Your match")
        return await self.dm(me, embeds=[intro, card], view=MatchActions(self), **file_kwargs(files))

    async def show_card(self, interaction, uid, label=None):
        """Private reply with someone's profile card: container layout when possible, else the embed card."""
        if HAS_V2:
            try:
                return await interaction.response.send_message(view=self.profile_view(uid, label), ephemeral=True)
            except Exception:
                log.warning("Container profile failed, using the embed card", exc_info=True)
        embed, files = self.card_parts(uid, label)
        await interaction.response.send_message(embed=embed, ephemeral=True, **file_kwargs(files))

    async def like_partner(self, interaction):
        uid = interaction.user.id
        m = self.d["matches"].get(str(uid))
        if not m:
            return await interaction.response.send_message(embed=note("You're not in a chat.", kind="warn"), ephemeral=True)
        p = self.profile_of(m["partner"])
        if not p:
            return await interaction.response.send_message(embed=note("That profile is gone.", kind="warn"), ephemeral=True)
        if str(uid) in p["likers"]:
            return await interaction.response.send_message(
                embed=note(f"You already liked **{p['name']}**'s profile.", kind="warn"), ephemeral=True)
        p["likers"].append(str(uid))
        self.store.save()
        await interaction.response.send_message(embed=note(f"You liked **{p['name']}**'s profile.", kind="add"), ephemeral=True)

    async def report_partner(self, interaction):
        if str(interaction.user.id) not in self.d["matches"]:
            return await interaction.response.send_message(embed=note("You're not in a chat.", kind="warn"), ephemeral=True)
        await interaction.response.send_modal(ReportModal(self))

    async def cmd_mention(self, path="match find") -> str:
        """A clickable slash command (</match find:id>) when its id is known, else plain `/match find`."""
        now = time.time()
        if now - self._cmd_ids_at > 600:
            self._cmd_ids_at = now
            try:
                self._cmd_ids = {c.name: c.id for c in await self.bot.tree.fetch_commands()}
            except Exception:
                log.info("Couldn't fetch the slash command ids", exc_info=True)
        cid = self._cmd_ids.get(path.split()[0])
        return f"</{path}:{cid}>" if cid else f"`/{path}`"

    def shared_interests(self, pa, pb) -> bool:
        if pa.get("mtype", "any") == pb.get("mtype", "any") != "any":
            return True
        mine = words(pa.get("likes", "") + " " + pa.get("about", ""))
        theirs = words(pb.get("likes", "") + " " + pb.get("about", ""))
        return bool(mine & theirs)

    def compatible(self, a, b, mode_a="quick", mode_b="quick") -> bool:
        pa, pb = self.profile_of(a), self.profile_of(b)
        if not pa or not pb or a == b:
            return False
        if not (self.ready(pa) and self.ready(pb)):
            return False
        # under-18s only meet under-18s, adults only meet adults
        if bracket(pa["age"]) != bracket(pb["age"]):
            return False
        if abs(pa["age"] - pb["age"]) > MAX_AGE_GAP:
            return False
        ta, tb = pa.get("mtype", "any"), pb.get("mtype", "any")
        if ta in ADULT_ONLY_TYPES or tb in ADULT_ONLY_TYPES:  # romantic: both want it, both adults
            if ta != tb or min(pa["age"], pb["age"]) < ADULT_AGE:
                return False
        if pa.get("older_only") and pb["age"] < pa["age"]:
            return False
        if pb.get("older_only") and pa["age"] < pb["age"]:
            return False
        for me, other in ((pa, pb), (pb, pa)):
            want = gpref_list(me.get("gpref"))
            if want and other.get("gender") not in want:
                return False
            if me.get("tzpref") == "close":
                x, y = tz_offset(me.get("tz")), tz_offset(other.get("tz"))
                if x is not None and y is not None and abs(x - y) > TZ_CLOSE_HOURS:
                    return False
        if str(a) in self.d["banned"] or str(b) in self.d["banned"]:
            return False
        if b in self.d["blocks"].get(str(a), []) or a in self.d["blocks"].get(str(b), []):
            return False
        if str(a) in self.d["matches"] or str(b) in self.d["matches"]:
            return False
        last = self.d["recent"].get(str(a), {}).get(str(b))
        if last and time.time() - last < REMATCH_DAYS * 86400:
            return False
        if "deep" in (mode_a, mode_b) and not self.shared_interests(pa, pb):
            return False
        return True

    # ---- daily lookups ----------------------------------------------------
    @staticmethod
    def today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def lookups_left(self, uid) -> int:
        if not DAILY_LOOKUPS:
            return 999
        rec = self.d["lookups"].get(str(uid))
        used = rec["used"] if rec and rec.get("day") == self.today() else 0
        return max(0, DAILY_LOOKUPS - used)

    def use_lookup(self, uid):
        if not DAILY_LOOKUPS:
            return
        rec = self.d["lookups"].get(str(uid))
        if not rec or rec.get("day") != self.today():
            rec = {"day": self.today(), "used": 0}
        rec["used"] += 1
        self.d["lookups"][str(uid)] = rec
        self.store.save()

    def lookups_line(self, uid) -> str:
        return "" if not DAILY_LOOKUPS else f"\n\nYou have {lookups_word(self.lookups_left(uid))} left for today."

    # ---- staff + live feed -------------------------------------------------
    async def is_owner(self, uid) -> bool:
        return await self.bot.is_owner(discord.Object(id=uid))

    @staticmethod
    def owns_server(ctx) -> bool:
        """True when the person is the owner of the server the command is used in."""
        return ctx.guild is not None and ctx.author.id == ctx.guild.owner_id

    async def is_staff(self, uid) -> bool:
        return str(uid) in self.d["staff"] or await self.is_owner(uid)

    def feed(self, text):
        """Queue a line for the live staff feed channel (if the owner set one)."""
        if self.d.get("feed_channel"):
            try:
                self.feed_q.put_nowait(text)
            except asyncio.QueueFull:
                pass

    async def _feed_worker(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                line = await self.feed_q.get()
                await asyncio.sleep(FEED_BATCH_SECONDS)  # batch bursts into one message
                lines = [line]
                while not self.feed_q.empty():
                    lines.append(self.feed_q.get_nowait())
                cid = self.d.get("feed_channel")
                channel = self.bot.get_channel(cid) if cid else None
                if channel is None:
                    continue
                chunk = ""
                for ln in lines:
                    ln = ln[:600]
                    if len(chunk) + len(ln) + 1 > 1900:
                        await self._feed_send(channel, chunk)
                        chunk = ""
                    chunk += ln + "\n"
                if chunk:
                    await self._feed_send(channel, chunk)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Matchmaking feed worker error")

    @staticmethod
    async def _feed_send(channel, text):
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("Could not post to the matchmaking feed channel %s", getattr(channel, "id", "?"))

    async def tell_staff(self, embed):
        """Reports and suggestions: report channel, else the live feed channel, else DM every staff member."""
        cid = REPORT_CHANNEL_ID or self.d.get("feed_channel")
        channel = self.bot.get_channel(cid) if cid else None
        if channel:
            try:
                await channel.send(embed=embed)
                return
            except discord.HTTPException:
                pass
        ids = {int(s) for s in self.d["staff"]}
        ids |= set(self.bot.owner_ids or [])
        if not ids:
            try:
                app = await self.bot.application_info()
                ids = {m.id for m in app.team.members} if app.team else {app.owner.id}
            except discord.HTTPException:
                ids = set()
        for staff_id in ids:
            await self.dm(staff_id, embed=embed)

    # ---- queue / matches ------------------------------------------------
    async def try_pair(self, uid, mode="quick"):
        """Pair with the longest-waiting compatible person (returns (partner, partner's mode)) or join the queue."""
        async with self.pair_lock:
            for other, _ts in sorted(self.queue.items(), key=lambda kv: kv[1]):
                other_mode = self.modes.get(other, "quick")
                if other != uid and self.compatible(uid, other, mode, other_mode):
                    self.dequeue(other)
                    self.dequeue(uid)
                    now = time.time()
                    self.d["matches"][str(uid)] = {"partner": other, "since": now}
                    self.d["matches"][str(other)] = {"partner": uid, "since": now}
                    self.d["recent"].setdefault(str(uid), {})[str(other)] = now
                    self.d["recent"].setdefault(str(other), {})[str(uid)] = now
                    self.bump_match_stats(uid)
                    self.bump_match_stats(other)
                    totals = self.d.setdefault("totals", {})
                    totals["matches"] = totals.get("matches", 0) + 1
                    self.store.save()
                    self.audit.add("match", uid=uid, partner=other, pair=pair_str(uid, other),
                                   mode=mode, partner_mode=other_mode)
                    self.feed(f"\U0001F91D matched: {self.who(uid)} \u2194 {self.who(other)} "
                              f"({MODE_NAMES.get(mode)} / {MODE_NAMES.get(other_mode)})")
                    return other, other_mode
            self.queue[uid] = time.time()
            self.modes[uid] = mode
            return None, None

    async def announce_match(self, a, b, mode_a="quick", mode_b="quick"):
        sent = {}
        for me, other, mode in ((a, b, mode_a), (b, a, mode_b)):
            sent[me] = await self.send_match_card(me, other, mode)
        if not all(sent.values()):
            for uid, ok in sent.items():
                if ok:
                    await self.dm(uid, embed=note(
                        "I couldn't reach your match (their DMs are closed), so the chat was cancelled. "
                        "Run `/match find` to try again."))
            await self.end_match(a, notice=None, reason="dm closed")

    async def end_match(self, uid, notice="Your match didn't want to carry on, so the chat has ended.",
                        reason="left") -> Optional[int]:
        m = self.d["matches"].pop(str(uid), None)
        if not m:
            return None
        partner = m["partner"]
        self.d["matches"].pop(str(partner), None)
        self.store.save()
        self.audit.add("end", uid=uid, partner=partner, pair=pair_str(uid, partner), reason=reason)
        self.feed(f"\U0001F6D1 chat ended ({reason}): {self.who(uid)} \u2194 {self.who(partner)}")
        if notice:
            await self.dm(partner, embed=note(f"{notice} Run `/match find` to look for someone new."))
        return partner

    def lock_left(self, uid) -> float:
        """Seconds until this person may unmatch (0 = now). Nobody can unmatch right after a match starts."""
        m = self.d["matches"].get(str(uid))
        if not m or not UNMATCH_LOCK:
            return 0
        return max(0, UNMATCH_LOCK - (time.time() - m.get("since", 0)))

    @staticmethod
    def lock_text(left) -> str:
        return f"You can't unmatch for another **{wait_text(left)}**, give them a chance \u2014 say hi and see where it goes."

    def leave_prompt(self, uid):
        """Keyword arguments for sending the 'Leave this match?' prompt, or None when they're not in a chat.
        Inside the unmatch lock this is just the lock notice (no buttons)."""
        m = self.d["matches"].get(str(uid))
        if not m:
            return None
        left = self.lock_left(uid)
        if left > 0:
            return box_kwargs(self.lock_text(left), "warn", WARN_FALLBACK)
        tag = discord.utils.escape_markdown(self.tag(m["partner"]))
        body = (f"You're about to unmatch from **{tag}**. They'll be told you didn't want to carry on, "
                "and you'll both go back to looking.")
        fine = None
        if DAILY_LOOKUPS:
            end = int(m.get("since", 0) + REFUND_WINDOW)
            mins = REFUND_WINDOW // 60
            if time.time() < end:
                fine = (f"-# Leaving now **uses up your lookup**, while **{tag}** will have their lookup **returned** "
                        f"(until <t:{end}:t>). After {mins} minutes, both lookups are used.")
            else:
                fine = f"-# It's been more than {mins} minutes, so both lookups stay used."
        if HAS_V2:
            try:
                return {"view": LeaveLayout(self, uid, f"## Leave this match?\n{body}", fine)}
            except Exception:
                log.warning("Container leave prompt failed, using the embed one", exc_info=True)
        e = discord.Embed(title="Leave this match?", color=GREY)
        e.description = body + (("\n\n" + fine.replace("-# ", "")) if fine else "")
        return {"embed": e, "view": LeaveConfirm(self, uid)}

    def refund_lookup(self, uid) -> bool:
        """Give today's lookup back to someone who was left early. True when one was returned."""
        if not DAILY_LOOKUPS:
            return False
        rec = self.d["lookups"].get(str(uid))
        if not rec or rec.get("day") != self.today() or rec.get("used", 0) <= 0:
            return False
        rec["used"] -= 1
        self.store.save()
        self.audit.add("lookup", uid=uid, action="returned (match was left early)")
        return True

    async def do_leave(self, uid) -> str:
        m = self.d["matches"].get(str(uid))
        if not m:
            return "You're not in a chat."
        left = self.lock_left(uid)
        if left > 0:
            return self.lock_text(left)
        partner = m["partner"]
        tag = discord.utils.escape_markdown(self.tag(partner))
        early = time.time() - m.get("since", 0) < REFUND_WINDOW
        refunded = early and self.refund_lookup(partner)
        notice = "Your match didn't want to carry on, so the chat has ended."
        if refunded:
            notice += " Your lookup has been returned."
        await self.end_match(uid, notice=notice, reason="left")
        find = await self.cmd_mention("match find")
        return f"**Unpaired** from **{tag}**. Run {find} when you want me to look again."

    async def block_partner(self, uid) -> Optional[int]:
        m = self.d["matches"].get(str(uid))
        if not m:
            return None
        partner = m["partner"]
        self.d["blocks"].setdefault(str(uid), [])
        if partner not in self.d["blocks"][str(uid)]:
            self.d["blocks"][str(uid)].append(partner)
        self.audit.add("block", uid=uid, partner=partner, pair=pair_str(uid, partner))
        await self.end_match(uid, reason="blocked")
        return partner

    async def mark_underage(self, uid):
        """Permanent: anyone whose date of birth puts them under the minimum age is refused for good."""
        if str(uid) not in self.d["underage"]:
            self.d["underage"].append(str(uid))
        self.d["profiles"].pop(str(uid), None)
        self.dequeue(uid)
        await self.end_match(uid, reason="underage")
        self.store.save()
        self.audit.add("underage", uid=uid)
        self.feed(f"\u26D4 refused (under {MIN_AGE}): `{uid}`")

    # ---- reports + suggestions ---------------------------------------------
    async def file_report(self, reporter, reason) -> Optional[int]:
        m = self.d["matches"].get(str(reporter))
        if not m:
            return None
        target = m["partner"]
        msgs = self.audit.read(kinds={"msg"}, pair=pair_str(reporter, target),
                               since=int(m.get("since", 0)), limit=25)
        snapshot = [{"from": e["uid"], "ts": e["ts"], "text": e.get("text", "")} for e in msgs]
        rid = self.d["next_report"]
        self.d["next_report"] = rid + 1
        report = {"id": rid, "reporter": reporter, "target": target, "reason": reason[:500],
                  "ts": int(time.time()), "status": "open", "chat": snapshot}
        self.d["reports"].append(report)
        self.audit.add("report", uid=reporter, partner=target, pair=pair_str(reporter, target),
                       id=rid, reason=reason[:500])
        distinct = {r["reporter"] for r in self.d["reports"] if r["target"] == target and r["status"] != "dismissed"}
        if len(distinct) >= AUTO_SUSPEND_REPORTS and str(target) not in self.d["banned"]:
            self.d["banned"][str(target)] = f"auto-suspended after {len(distinct)} reports (pending review)"
            self.dequeue(target)
            self.audit.add("ban", uid=target, by="auto", reason=self.d["banned"][str(target)])
        self.store.save()
        await self.end_match(reporter, reason="report")
        await self.notify_staff(report)
        return rid

    async def notify_staff(self, report):
        e = discord.Embed(title=f"{icon_text(self.bot, 'announce')}Matchmaking report #{report['id']}", color=ERR)
        e.description = (f"Reported user: {self.who(report['target'])}\nReporter: {self.who(report['reporter'])}\n"
                         f"Reason: {report['reason']}\n\nSee it with `/match reports {report['id']}` or "
                         f"`/match staff chat {report['target']}`.")
        await self.tell_staff(e)

    async def file_suggestion(self, uid, text):
        text = (text or "").strip()
        if not text:
            return "Write something first.", ERR
        now = time.time()
        if now - self.last_suggest.get(uid, 0) < 60:
            return "Wait a minute before sending another suggestion.", GREY
        self.last_suggest[uid] = now
        self.audit.add("suggestion", uid=uid, text=text[:500])
        e = discord.Embed(title=f"{icon_text(self.bot, 'shield')}Matchmaking suggestion", color=GREY, description=text[:1500])
        e.set_footer(text=f"from {self.who(uid)}")
        await self.tell_staff(e)
        return "Thanks, your suggestion was sent to the staff.", OK

    # ---- relay ----------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is not None:
            return
        uid = message.author.id
        m = self.d["matches"].get(str(uid))
        if not m:
            return
        text = (message.content or "").strip()
        try:
            prefixes = await self.bot.get_prefix(message)
        except Exception:
            prefixes = []
        if isinstance(prefixes, str):
            prefixes = [prefixes]
        if text and prefixes and text.startswith(tuple(prefixes)):
            return  # a command, not chat
        partner = m["partner"]
        pair = pair_str(uid, partner)
        if message.attachments or message.stickers:
            self.audit.add("blocked", uid=uid, partner=partner, pair=pair, why="file or sticker",
                           count=len(message.attachments) + len(message.stickers))
            return await message.channel.send(embed=note("Text only: files and stickers aren't passed on."))
        if not text:
            return
        if LINK_RE.search(text):
            self.audit.add("blocked", uid=uid, partner=partner, pair=pair, why="link", text=text[:300])
            self.feed(f"\U0001F517 link blocked from {self.who(uid)}: {discord.utils.escape_markdown(text[:200])}")
            return await message.channel.send(embed=note("Links aren't allowed in matches, so that wasn't sent."))
        if len(text) > MAX_RELAY_LEN:
            return await message.channel.send(embed=note(f"Too long (max {MAX_RELAY_LEN} characters)."))
        now = time.time()
        recent = self.rate.setdefault(uid, deque(maxlen=6))
        if len(recent) == recent.maxlen and now - recent[0] < 10:
            return await message.channel.send(embed=note("Slow down a little.", kind="cooldown"))
        recent.append(now)
        nick = self.d["profiles"].get(str(uid), {}).get("name", "Your match")
        if not await self.dm(partner, content=f"**{nick}:** {text}"):
            await self.end_match(uid, notice=None, reason="undeliverable")
            return await message.channel.send(embed=note("I couldn't deliver that, so the chat has ended."))
        self.audit.add("msg", uid=uid, partner=partner, pair=pair, text=text)
        clean = discord.utils.escape_markdown(text.replace("\n", " "))
        self.feed(f"\U0001F4AC **{self.tag(uid)}** \u2192 **{self.tag(partner)}**: {clean}")

    @tasks.loop(minutes=1)
    async def expire_queue(self):
        cutoff = time.time() - QUEUE_TIMEOUT
        for uid, joined in list(self.queue.items()):
            if joined < cutoff:
                self.dequeue(uid)
                self.audit.add("queue", uid=uid, action="timed out")
                await self.dm(uid, embed=note("Nobody matched in time. Run `/match find` to search again.", kind="warn"))

    @expire_queue.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=6)
    async def prune_logs(self):
        dropped = self.audit.prune()
        if dropped:
            log.info("Pruned %s old matchmaking log entries", dropped)

    # ---- shared by commands and the panel buttons -------------------------
    async def start_search(self, user, guild, mode="quick"):
        """Returns (text, color). Used by /match find and the panel's Find a Friend button."""
        mode = "deep" if mode == "deep" else "quick"
        reason = self.blocked_reason(user)
        if reason:
            return reason, ERR
        if guild and str(guild.id) in self.d["guild_off"]:
            return "Matchmaking is switched off in this server.", GREY
        uid = user.id
        p = self.profile_of(uid)
        if not p:
            return "Make a profile first: press **About You** or use `/match profile`.", GREY
        if not self.ready(p):
            return "Finish your profile first: press **Your Vibes** and tell me what you like.", GREY
        if str(uid) in self.d["matches"]:
            return "You're already in a chat. Check your DMs.", GREY
        if uid in self.queue:
            return "You're already searching.", GREY
        if DAILY_LOOKUPS and self.lookups_left(uid) <= 0:
            tomorrow = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return (f"You've used all {lookups_word(DAILY_LOOKUPS)} for today. "
                    f"They reset <t:{int(tomorrow.timestamp())}:R>."), GREY
        now = time.time()
        if now - self.last_find.get(uid, 0) < 20:
            return "Wait a few seconds before searching again.", GREY
        self.last_find[uid] = now
        if not await self.dm(uid, embed=note(f"Searching for a friend ({MODE_NAMES[mode]})... "
                                             "I'll message you here as soon as I find one.", kind="wait")):
            return "I can't DM you. Turn on DMs from server members and try again.", ERR
        self.use_lookup(uid)
        self.audit.add("search", uid=uid, mode=mode)
        partner, partner_mode = await self.try_pair(uid, mode)
        if partner:
            await self.announce_match(uid, partner, mode, partner_mode)
            return (f"**Found someone for you!** ({MODE_NAMES[mode]})\n"
                    f"Matched with **{self.tag(partner)}**. Say hello in my DMs."
                    + self.lookups_line(uid)), OK
        return (f"**Searching...** ({MODE_NAMES[mode]}) I'll DM you as soon as someone matches. "
                "Use `/match leave` to stop." + self.lookups_line(uid)), OK

    async def open_about(self, interaction):
        reason = self.blocked_reason(interaction.user)
        if reason:
            return await interaction.response.send_message(embed=note(reason, ERR), ephemeral=True)
        existing = self.profile_of(interaction.user.id)
        if existing:
            return await interaction.response.send_modal(
                AboutForm(self, existing) if MODAL_SELECTS else AboutModal(self, existing))
        await interaction.response.send_message(
            embed=rules_embed(), view=RulesGate(self, interaction.user.id), ephemeral=True)

    async def open_vibes(self, interaction):
        reason = self.blocked_reason(interaction.user)
        if reason:
            return await interaction.response.send_message(embed=note(reason, ERR), ephemeral=True)
        p = self.profile_of(interaction.user.id)
        if not p:
            return await interaction.response.send_message(
                embed=note("Start with **About You** first.", kind="warn"), ephemeral=True)
        await interaction.response.send_modal(VibesForm(self, p) if MODAL_SELECTS else VibesModal(self, p))

    async def open_match(self, interaction):
        reason = self.blocked_reason(interaction.user)
        if reason:
            return await interaction.response.send_message(embed=note(reason, ERR), ephemeral=True)
        p = self.profile_of(interaction.user.id)
        if not p:
            return await interaction.response.send_message(
                embed=note("Start with **About You** first.", kind="warn"), ephemeral=True)
        if MODAL_SELECTS:
            return await interaction.response.send_modal(MatchForm(self, p, _bot_name(interaction)))
        await interaction.response.send_message(
            embed=match_prefs_embed(p), view=MatchPrefsView(self, interaction.user.id), ephemeral=True)

    # ---- sticky panel -----------------------------------------------------
    async def refresh_panel(self, channel):
        """Post a fresh panel at the bottom of the channel, then delete the old one."""
        info = self.d["panels"].setdefault(str(channel.id), {"message_id": None, "guild_id": channel.guild.id})
        old = info.get("message_id")
        msg = await channel.send(view=PanelView(self), **panel_kwargs())
        info["message_id"] = msg.id
        self.store.save()
        if old:
            try:
                await channel.get_partial_message(old).delete()
            except discord.HTTPException:
                pass  # already gone

    @commands.Cog.listener("on_message")
    async def sticky_panel(self, message):
        if message.guild is None or message.author.id == self.bot.user.id:
            return
        cid = message.channel.id
        if str(cid) not in self.d["panels"] or cid in self.panel_pending:
            return
        self.panel_pending.add(cid)
        task = asyncio.create_task(self._sticky_later(cid))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _sticky_later(self, cid):
        try:
            await asyncio.sleep(PANEL_DELAY)
            channel = self.bot.get_channel(cid)
            if channel is None:
                self.d["panels"].pop(str(cid), None)
                self.store.save()
                return
            await self.refresh_panel(channel)
        except discord.HTTPException:
            log.warning("Could not move the sticky panel in channel %s", cid)
        finally:
            self.panel_pending.discard(cid)

    # ---- permission + output helpers for staff tools -----------------------
    async def need_staff(self, ctx) -> bool:
        if await self.is_staff(ctx.author.id) or self.owns_server(ctx):
            return True
        await ctx.reply(embed=note("Only matchmaking staff can use this.", ERR), mention_author=False, ephemeral=True)
        return False

    async def need_owner(self, ctx) -> bool:
        if self.owns_server(ctx) or await self.is_owner(ctx.author.id):  # server owner first: no lookup needed
            return True
        await ctx.reply(embed=note("Only the bot owner or the server owner can use this.", ERR), mention_author=False, ephemeral=True)
        return False

    async def need_admin(self, ctx) -> bool:
        """The owner of this server, or the bot owner (falls through to need_owner for the refusal)."""
        if ctx.guild is not None and ctx.author.id == ctx.guild.owner_id:
            return True
        return await self.need_owner(ctx)

    async def private(self, ctx, **kwargs):
        """Staff output holds private chats: slash commands answer ephemerally, prefix commands go to DMs."""
        if ctx.interaction is not None:
            return await ctx.reply(mention_author=False, ephemeral=True, **kwargs)
        try:
            await ctx.author.send(**kwargs)
        except discord.HTTPException:
            return await ctx.reply(embed=note(
                "I couldn't DM you. Open your DMs so I can send private staff info.", ERR), mention_author=False)
        if ctx.guild is not None:
            await ctx.reply(embed=note("Sent to your DMs.", kind="dm"), mention_author=False)

    async def deliver(self, ctx, title, lines, color=GREY, filename="matchmaking.txt"):
        """One private embed, or a text file when it's too long for Discord."""
        text = "\n".join(lines) or "Nothing to show."
        if len(text) <= 3800:
            return await self.private(ctx, embed=discord.Embed(
                title=f"{icon_text(self.bot, 'shield')}{title}", description=text, color=color))

        def stamp(m):
            return datetime.fromtimestamp(int(m.group(1)), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        plain = re.sub(r"<t:(\d+):[a-zA-Z]>", stamp, text).replace("**", "").replace("`", "")
        f = discord.File(io.BytesIO(plain.encode("utf-8")), filename=filename)
        e = discord.Embed(title=f"{icon_text(self.bot, 'shield')}{title}", color=color,
                          description=f"{len(lines)} lines is too long for one message, so it's in the attached file.")
        await self.private(ctx, embed=e, file=f)

    def resolve_uid(self, text) -> Optional[int]:
        """A Discord ID / mention, or a 6-digit friend id, -> Discord user id."""
        t = re.sub(r"[<@!>]", "", (text or "").strip())
        if not t.isdigit():
            return None
        if len(t) <= 9:  # friend id
            for uid, p in self.d["profiles"].items():
                if p.get("pid") == int(t):
                    return int(uid)
            return None
        return int(t)

    def staff_look(self, ctx, what, target=None):
        """Every staff look at private data is itself logged (and shown in the live feed)."""
        self.audit.add("staff_view", uid=ctx.author.id, partner=target, what=what)
        self.feed(f"\U0001F441 {self.who(ctx.author.id)} looked at: {what}")

    def fmt_event(self, e) -> str:
        ts = f"<t:{e['ts']}:d> <t:{e['ts']}:T>"
        k, u, pt = e.get("kind"), e.get("uid"), e.get("partner")
        if k == "msg":
            return f"{ts} {self.who(u)} \u2192 {self.who(pt)}: {e.get('text', '')}"
        if k == "match":
            return f"{ts} **matched** {self.who(u)} \u2194 {self.who(pt)} ({e.get('mode')}/{e.get('partner_mode')})"
        if k == "end":
            return f"{ts} **chat ended** by {self.who(u)} with {self.who(pt)} ({e.get('reason')})"
        if k == "search":
            return f"{ts} **search** {self.who(u)} ({e.get('mode')})"
        if k == "queue":
            return f"{ts} **queue** {self.who(u)} {e.get('action')}"
        if k == "profile":
            return f"{ts} **profile {e.get('action')}** {self.who(u)} ({e.get('step', 'about')})"
        if k == "block":
            return f"{ts} **block** {self.who(u)} blocked {self.who(pt)}"
        if k == "report":
            return f"{ts} **report #{e.get('id')}** {self.who(u)} reported {self.who(pt)}: {e.get('reason', '')}"
        if k == "ban":
            return f"{ts} **ban** {self.who(u)} by {e.get('by', 'staff')}: {e.get('reason', '')}"
        if k == "unban":
            return f"{ts} **unban** {self.who(u)} by {self.who(e.get('by'))}"
        if k == "blocked":
            extra = f": {e['text']}" if e.get("text") else ""
            return f"{ts} **not sent ({e.get('why')})** {self.who(u)} \u2192 {self.who(pt)}{extra}"
        if k == "suggestion":
            return f"{ts} **suggestion** {self.who(u)}: {e.get('text', '')}"
        if k == "reveal":
            return f"{ts} **reveal {e.get('action')}** {self.who(u)} \u2194 {self.who(pt)}"
        if k == "icebreaker":
            return f"{ts} **icebreaker** in the chat of {self.who(u)} and {self.who(pt)}"
        if k == "lookup":
            return f"{ts} **lookup {e.get('action')}** {self.who(u)}"
        if k == "underage":
            return f"{ts} **refused (under {MIN_AGE})** `{u}`"
        if k and k.startswith("staff"):
            target = f" \u2192 {self.who(pt)}" if pt else ""
            return f"{ts} **{k.replace('_', ' ')}** {self.who(u)}{target} {e.get('what', '')}"
        return f"{ts} **{k}** {self.who(u)}"

    def chat_line(self, e) -> str:
        p = self.d["profiles"].get(str(e["uid"]))
        name = f"{p['name']}#{p['pid']}" if p else str(e["uid"])
        return f"<t:{e['ts']}:d> <t:{e['ts']}:T> **{name}**: {e.get('text', '')}"

    # ---- commands -------------------------------------------------------
    @commands.hybrid_group(name="match", aliases=["mm"], invoke_without_command=True,
                           description="Friend matching (romantic matches are 18+ only)")
    async def match(self, ctx):
        p = ctx.clean_prefix
        text = (
            f"**Friend matching (romantic matches are 18+ only)**\n"
            f"`{p}match profile` create or edit your profile (About You, Your Vibes, Your Match)\n"
            f"`{p}match find [quick|deep]` look for a friend (I'll DM you)\n"
            f"`{p}match leave` stop searching\n"
            f"`{p}match end` leave your current match (asks first, not in the first {UNMATCH_LOCK // 60} minutes)\n"
            f"`{p}match block` end the chat and never match with them again\n"
            f"`{p}match me` see your profile\n"
            f"`{p}match stats` your matches, likes and streak\n"
            f"`{p}match icebreaker` send a conversation starter to your chat\n"
            f"`{p}match reveal` swap Discord usernames if you both agree (both 18+)\n"
            f"`{p}match delete` delete your profile\n"
            f"`{p}match unblockall` clear your block list\n"
            f"`{p}match panel #channel` post the sticky sign-up panel (Manage Server)\n\n"
            f"Chats are logged for safety and can be read by the matchmaking staff.")
        if await self.is_staff(ctx.author.id) or self.owns_server(ctx):
            text += f"\n\n**Staff:** `{p}match staff` shows the safety tools."
        await ctx.reply(embed=note(text), mention_author=False)

    @match.command(name="profile", aliases=["create", "edit"], description="Create or edit your friend profile")
    async def profile(self, ctx):
        reason = self.blocked_reason(ctx.author)
        if reason:
            return await ctx.reply(embed=note(reason, ERR), mention_author=False)
        await ctx.reply(view=PanelView(self), mention_author=False, **panel_kwargs())

    @match.command(name="me", description="See your profile")
    async def me(self, ctx):
        if not self.profile_of(ctx.author.id):
            return await ctx.reply(embed=note(f"You don't have a profile yet. Run `{ctx.clean_prefix}match profile`."),
                                   mention_author=False)
        if HAS_V2:
            try:
                note_line = f"{lookups_word(self.lookups_left(ctx.author.id))} left for today" if DAILY_LOOKUPS else None
                view = self.profile_view(ctx.author.id, f"Status: {self.state_of(ctx.author.id)}", note_line)
                return await ctx.reply(view=view, mention_author=False)
            except Exception:
                log.warning("Container profile failed, using the embed card", exc_info=True)
        embed, files = self.card_parts(ctx.author.id, f"Status: {self.state_of(ctx.author.id)}")
        extra = f"\n{lookups_word(self.lookups_left(ctx.author.id))} left for today" if DAILY_LOOKUPS else ""
        foot = ((embed.footer.text or "") + extra.replace("\n", " \u2022 ")).strip(" \u2022")
        if foot:
            embed.set_footer(text=foot)
        await ctx.reply(embed=embed, mention_author=False, **file_kwargs(files))

    @match.command(name="find", aliases=["partner", "start"], description="Look for a friend to chat with")
    @app_commands.describe(mode="Choose matching speed & depth")
    @app_commands.choices(mode=[
        app_commands.Choice(name=mode_text("quick"), value="quick"),
        app_commands.Choice(name=mode_text("deep"), value="deep"),
    ])
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def find(self, ctx, mode: str = "quick"):
        await ctx.defer(ephemeral=True)  # slash: only the person who ran it sees the answer
        mode = "deep" if str(mode).lower().startswith("deep") else "quick"
        text, color = await self.start_search(ctx.author, ctx.guild, mode)
        await ctx.reply(embed=note(text, color), mention_author=False, ephemeral=True)

    @match.command(name="leave", description="Stop searching")
    async def leave(self, ctx):
        gone = self.dequeue(ctx.author.id)
        if gone:
            self.audit.add("queue", uid=ctx.author.id, action="left the queue")
        await ctx.reply(embed=note("Stopped searching." if gone else "You're not searching.", kind="remove" if gone else "warn"), mention_author=False)

    @match.command(name="end", aliases=["cancel"], description="Leave your current match")
    async def end(self, ctx):
        prompt = self.leave_prompt(ctx.author.id)
        if not prompt:
            return await ctx.reply(embed=note("You're not in a chat.", kind="warn"), mention_author=False)
        await ctx.reply(**prompt, mention_author=False)

    @match.command(name="block", description="End the chat and never match with them again")
    async def block(self, ctx):
        partner = await self.block_partner(ctx.author.id)
        await ctx.reply(embed=note("Blocked. You won't be matched again." if partner else "You're not in a chat.", kind="remove" if partner else "warn"),
                        mention_author=False)

    @match.command(name="reveal", description="Swap Discord usernames with your match (both must agree, 18+ only)")
    async def reveal(self, ctx):
        kind, text = await self.request_reveal(ctx.author.id)
        await ctx.reply(embed=note(text, kind=kind), mention_author=False)

    @match.command(name="icebreaker", description="Send a conversation starter to your chat")
    async def icebreaker(self, ctx):
        kind, text = await self.send_icebreaker(ctx.author.id)
        await ctx.reply(embed=note(text, kind=kind), mention_author=False)

    @match.command(name="stats", description="Your matches, profile likes and streak")
    async def match_stats(self, ctx):
        text = self.stats_text(ctx.author.id)
        if text is None:
            return await ctx.reply(embed=note(f"You don't have a profile yet. Run `{ctx.clean_prefix}match profile`.", kind="warn"),
                                   mention_author=False)
        await ctx.reply(mention_author=False, **box_kwargs(text))

    @commands.hybrid_command(name="stats", description="How many servers, members and matches the bot has")
    async def server_stats(self, ctx):  # not bot_stats: discord.py rejects command names starting with bot_ or cog_
        await ctx.reply(mention_author=False, **box_kwargs(self.bot_stats_text()))

    @match.command(name="unblockall", description="Clear your block list")
    async def unblockall(self, ctx):
        self.d["blocks"].pop(str(ctx.author.id), None)
        self.store.save()
        await ctx.reply(embed=note("Block list cleared.", kind="remove"), mention_author=False)

    @match.command(name="delete", description="Delete your profile")
    async def delete(self, ctx):
        uid = ctx.author.id
        self.dequeue(uid)
        await self.end_match(uid, reason="profile deleted")
        removed = self.d["profiles"].pop(str(uid), None)
        self.store.save()
        if removed:
            self.audit.add("profile", uid=uid, action="deleted", step="all")
        await ctx.reply(embed=note("Profile deleted." if removed else "You had no profile.", kind="remove" if removed else "warn"), mention_author=False)

    @match.command(name="panel", description="Post the sticky sign-up panel in a channel (Manage Server)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def panel(self, ctx, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != ctx.guild.id:
            return await ctx.reply(embed=note("Pick a text channel in this server.", ERR), mention_author=False)
        perms = channel.permissions_for(ctx.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.attach_files):
            return await ctx.reply(embed=note(
                f"I need View Channel, Send Messages, Embed Links and Attach Files in {channel.mention}.", ERR),
                mention_author=False)
        try:
            await self.refresh_panel(channel)
        except discord.HTTPException:
            return await ctx.reply(embed=note("Discord refused to let me post there.", ERR), mention_author=False)
        await ctx.reply(embed=note(f"Panel posted in {channel.mention}. It moves to the bottom when people chat. "
                                   f"Remove it with `/match unpanel`.", kind="add"), mention_author=False)

    @match.command(name="unpanel", description="Remove the sticky sign-up panel from a channel (Manage Server)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def unpanel(self, ctx, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        info = self.d["panels"].pop(str(channel.id), None)
        self.store.save()
        if info and info.get("message_id"):
            try:
                await channel.get_partial_message(info["message_id"]).delete()
            except discord.HTTPException:
                pass
        await ctx.reply(embed=note("Panel removed." if info else "There's no panel in that channel.", kind="remove"),
                        mention_author=False)

    @match.command(name="server", description="Turn matchmaking on or off in this server")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def server(self, ctx, state: str = ""):
        gid = str(ctx.guild.id)
        state = state.lower()
        if state == "off" and gid not in self.d["guild_off"]:
            self.d["guild_off"].append(gid)
        elif state == "on" and gid in self.d["guild_off"]:
            self.d["guild_off"].remove(gid)
        elif state not in ("on", "off"):
            now = "off" if gid in self.d["guild_off"] else "on"
            return await ctx.reply(embed=note(f"Matchmaking is **{now}** here. Use `{ctx.clean_prefix}match server on|off`."),
                                   mention_author=False)
        self.store.save()
        await ctx.reply(embed=note(f"Matchmaking is now **{state}** in this server.", kind="ok" if state == "on" else "remove"), mention_author=False)

    # ---- moderation (staff + bot owner, global) ---------------------------
    @match.command(name="ban", description="Staff: ban a user from matchmaking everywhere")
    async def ban(self, ctx, user_id: str, *, reason: str = "No reason given"):
        if not await self.need_staff(ctx):
            return
        uid = self.resolve_uid(user_id)
        if uid is None:
            return await ctx.reply(embed=note("Give the numeric user ID (or friend id).", ERR), mention_author=False)
        self.d["banned"][str(uid)] = reason
        self.dequeue(uid)
        await self.end_match(uid, notice=None, reason="banned")
        self.store.save()
        self.audit.add("ban", uid=uid, by=str(ctx.author.id), reason=reason[:300])
        self.feed(f"\U0001F528 {self.who(ctx.author.id)} banned {self.who(uid)}: {reason[:200]}")
        await ctx.reply(embed=note(f"`{uid}` is banned from matchmaking in every server.", kind="remove"), mention_author=False)

    @match.command(name="unban", description="Staff: unban a user")
    async def unban(self, ctx, user_id: str):
        if not await self.need_staff(ctx):
            return
        uid = self.resolve_uid(user_id)
        if uid is None:
            return await ctx.reply(embed=note("Give the numeric user ID (or friend id).", ERR), mention_author=False)
        removed = self.d["banned"].pop(str(uid), None)
        self.store.save()
        if removed:
            self.audit.add("unban", uid=uid, by=ctx.author.id)
            self.feed(f"{status_icon('ok')}{self.who(ctx.author.id)} unbanned {self.who(uid)}")
        await ctx.reply(embed=note("Unbanned." if removed else "They weren't banned.", kind="add" if removed else "warn"), mention_author=False)

    @match.command(name="reports", description="Staff: view reports")
    async def reports(self, ctx, report_id: Optional[int] = None):
        if not await self.need_staff(ctx):
            return
        if report_id is None:
            open_ones = [r for r in self.d["reports"] if r["status"] == "open"][-10:]
            if not open_ones:
                return await self.private(ctx, embed=note("No open reports.", kind="ok"))
            lines = [f"**#{r['id']}** {self.who(r['target'])} reported by {self.who(r['reporter'])}: {r['reason'][:80]}"
                     for r in open_ones]
            return await self.private(ctx, embed=note("\n".join(lines)))
        report = next((r for r in self.d["reports"] if r["id"] == report_id), None)
        if not report:
            return await ctx.reply(embed=note("No report with that ID.", kind="warn"), mention_author=False)
        self.staff_look(ctx, f"report #{report_id}", report["target"])
        chat = "\n".join(f"<t:{c['ts']}:t> {self.who(c['from'])}: {c['text']}" for c in report["chat"]) \
            or "(no messages saved)"
        body = (f"Reported: {self.who(report['target'])}  Reporter: {self.who(report['reporter'])}  "
                f"Status: {report['status']}\nReason: {report['reason']}\n\n{chat}")
        await self.private(ctx, embed=note(body[:3900]))

    @match.command(name="resolve", description="Staff: mark a report reviewed or dismissed")
    async def resolve(self, ctx, report_id: int, outcome: str = "reviewed"):
        if not await self.need_staff(ctx):
            return
        report = next((r for r in self.d["reports"] if r["id"] == report_id), None)
        if not report:
            return await ctx.reply(embed=note("No report with that ID.", kind="warn"), mention_author=False)
        report["status"] = "dismissed" if outcome.lower() == "dismissed" else "reviewed"
        self.store.save()
        await ctx.reply(embed=note(f"Report #{report_id} marked {report['status']}.", kind="ok"), mention_author=False)

    # ---- staff safety tools: see everything that happens in matchmaking ------
    @match.group(name="staff", invoke_without_command=True, description="Matchmaking staff safety tools")
    async def staff(self, ctx):
        if not await self.need_staff(ctx):
            return
        p = ctx.clean_prefix
        text = (
            f"Private: slash commands answer only to you, prefix commands go to your DMs.\n\n"
            f"`{p}match staff chats` every chat running right now + who is searching\n"
            f"`{p}match staff chat <user> [partner] [count]` read a user's chat (live, or their latest)\n"
            f"`{p}match staff log [user|all] [count]` everything that happened: matches, messages, reports, bans...\n"
            f"`{p}match staff profile <user>` open a full profile (Discord id, age, preferences, reports)\n"
            f"`{p}match staff stop <user> [reason]` end a chat right now\n"
            f"`{p}match staff list` who is matchmaking staff\n"
            f"`{p}match reports [id]`, `{p}match resolve <id>`, `{p}match ban <user> [reason]`, `{p}match unban <user>`\n\n"
            f"`<user>` = Discord ID, mention, or the 6-digit friend id shown on profiles.\n"
            f"Every look you take is logged. Chats are kept {LOG_DAYS} days.")
        if await self.is_owner(ctx.author.id) or self.owns_server(ctx):
            text += (f"\n\n**Owner only:** `{p}match staff add @user`, `{p}match staff remove @user`, "
                     f"`{p}match staff feed #channel|off` (live mirror of everything into a private channel)")
        await self.private(ctx, embed=discord.Embed(
            title=f"{icon_text(self.bot, 'shield')}Matchmaking staff tools", description=text, color=GREY))

    @staff.command(name="chats", description="Staff: list every chat running right now")
    async def staff_chats(self, ctx):
        if not await self.need_staff(ctx):
            return
        await ctx.defer(ephemeral=True)
        seen, lines = set(), []
        for uid_s, m in self.d["matches"].items():
            a, b = int(uid_s), m["partner"]
            key = pair_str(a, b)
            if key in seen:
                continue
            seen.add(key)
            since = int(m.get("since", 0))
            n = self.audit.count("msg", key, since)
            lines.append(f"{self.who(a)} \u2194 {self.who(b)} - since <t:{since}:R> - {n} message{'' if n == 1 else 's'}")
        searching = [self.who(u) for u in self.queue]
        head = [f"**{len(seen)} active chat{'' if len(seen) == 1 else 's'}**, {len(self.queue)} searching"]
        if searching:
            head.append("Searching: " + ", ".join(searching[:20]))
        self.staff_look(ctx, "the list of active chats")
        await self.deliver(ctx, "Active chats", head + [""] + lines)

    @staff.command(name="chat", description="Staff: read a user's chat (live, or their latest)")
    @app_commands.describe(user="Discord ID, mention or friend id", partner="Optional: the other person",
                           count="How many messages (default 60)")
    async def staff_chat(self, ctx, user: str, partner: str = "", count: int = 60):
        if not await self.need_staff(ctx):
            return
        await ctx.defer(ephemeral=True)
        uid = self.resolve_uid(user)
        if uid is None:
            return await ctx.reply(embed=note("Give a Discord ID, a mention or a 6-digit friend id.", ERR),
                                   mention_author=False, ephemeral=True)
        count = max(1, min(count, 500))
        other, since, live = None, 0, False
        if partner:
            other = self.resolve_uid(partner)
            if other is None:
                return await ctx.reply(embed=note("I don't know the second user.", ERR),
                                       mention_author=False, ephemeral=True)
        else:
            m = self.d["matches"].get(str(uid))
            if m:
                other, since, live = m["partner"], int(m.get("since", 0)), True
            else:
                last = self.audit.read(kinds={"match"}, involving=uid, limit=1)
                if not last:
                    return await ctx.reply(embed=note("I have no chat on record for that user.", ERR),
                                           mention_author=False, ephemeral=True)
                ev = last[0]
                other = ev["partner"] if ev["uid"] == uid else ev["uid"]
                since = ev["ts"]
        pair = pair_str(uid, other)
        msgs = self.audit.read(kinds={"msg"}, pair=pair, since=since, limit=count)
        ends = self.audit.read(kinds={"end"}, pair=pair, since=since, limit=1)
        status = "live" if live else (f"ended ({ends[0].get('reason')})" if ends else "not live")
        head = [f"{self.who(uid)} \u2194 {self.who(other)} - {status} - showing {len(msgs)} message"
                f"{'' if len(msgs) == 1 else 's'}", ""]
        self.staff_look(ctx, f"the chat {self.tag(uid)} \u2194 {self.tag(other)}", uid)
        await self.deliver(ctx, "Chat transcript", head + [self.chat_line(e) for e in msgs] or head,
                           filename=f"chat_{uid}_{other}.txt")

    @staff.command(name="log", description="Staff: everything that happened in matchmaking")
    @app_commands.describe(user="Discord ID, mention or friend id (or 'all')", count="How many events (default 30)")
    async def staff_log(self, ctx, user: str = "all", count: int = 30):
        if not await self.need_staff(ctx):
            return
        await ctx.defer(ephemeral=True)
        uid = None
        if user and user.lower() not in ("all", "everyone", "*"):
            uid = self.resolve_uid(user)
            if uid is None:
                return await ctx.reply(embed=note("Give a Discord ID, a mention, a friend id or `all`.", ERR),
                                       mention_author=False, ephemeral=True)
        count = max(1, min(count, 200))
        events = self.audit.read(involving=uid, limit=count)
        self.staff_look(ctx, "the event log" + (f" of {self.tag(uid)}" if uid else ""), uid)
        lines = [self.fmt_event(e) for e in events] or ["Nothing logged yet."]
        await self.deliver(ctx, "Matchmaking log" + (f" - {self.tag(uid)}" if uid else ""), lines,
                           filename="matchmaking_log.txt")

    @staff.command(name="profile", description="Staff: open a full profile")
    @app_commands.describe(user="Discord ID, mention or friend id")
    async def staff_profile(self, ctx, user: str):
        if not await self.need_staff(ctx):
            return
        await ctx.defer(ephemeral=True)
        uid = self.resolve_uid(user)
        p = self.profile_of(uid) if uid is not None else None
        if not p:
            return await ctx.reply(embed=note("No profile for that user.", ERR), mention_author=False, ephemeral=True)
        against = sum(1 for r in self.d["reports"] if r["target"] == uid)
        filed = sum(1 for r in self.d["reports"] if r["reporter"] == uid)
        blocked = len(self.d["blocks"].get(str(uid), []))
        blocked_by = sum(1 for lst in self.d["blocks"].values() if uid in lst)
        tz = p.get("tz") or "not set"
        lines = [
            f"**{p['name']}#{p['pid']}** - Discord `{uid}` (<@{uid}>)",
            f"Age **{p['age']}** ({bracket(p['age'])}), gender {GENDERS.get(p.get('gender'), 'not set')}, timezone {tz}",
            f"Match type: {TYPES.get(p.get('mtype'), TYPES['any'])[0]}, preferable gender: "
            f"{gpref_text(p.get('gpref'))}, timezone: {TZ_PREFS.get(p.get('tzpref'), TZ_PREFS['global'])}, "
            f"older only: {'yes' if p.get('older_only') else 'no'}",
            f"Status: {self.state_of(uid)}   Banned: {self.d['banned'].get(str(uid), 'no')}",
            f"Reports: {against} against, {filed} filed   Blocks: {blocked} blocked, blocked by {blocked_by}",
            f"Created <t:{p.get('created') or 0}:f>",
            "", f"**About me**\n{p.get('about') or '-'}", f"**Likes**\n{p.get('likes') or '-'}",
            f"**Dislikes**\n{p.get('dislikes') or '-'}"]
        self.staff_look(ctx, f"the profile of {self.tag(uid)}", uid)
        await self.deliver(ctx, "Profile (staff view)", lines, filename=f"profile_{uid}.txt")

    @staff.command(name="stop", description="Staff: end a user's chat right now")
    @app_commands.describe(user="Discord ID, mention or friend id", reason="Why (kept in the log)")
    async def staff_stop(self, ctx, user: str, *, reason: str = "No reason given"):
        if not await self.need_staff(ctx):
            return
        uid = self.resolve_uid(user)
        m = self.d["matches"].get(str(uid)) if uid is not None else None
        if not m:
            return await ctx.reply(embed=note("That user isn't in a chat.", ERR), mention_author=False, ephemeral=True)
        partner = await self.end_match(uid, notice="A moderator ended this chat for safety.",
                                       reason=f"staff: {reason[:100]}")
        await self.dm(uid, embed=note("A moderator ended your chat for safety. Run `/match find` to look again.", kind="warn"))
        self.audit.add("staff_stop", uid=ctx.author.id, partner=uid, what=f"ended the chat with {partner}: {reason[:200]}")
        await ctx.reply(embed=note(f"Ended the chat between {self.who(uid)} and {self.who(partner)}.", kind="remove"),
                        mention_author=False, ephemeral=True)

    @staff.command(name="list", description="Staff: who is matchmaking staff")
    async def staff_list(self, ctx):
        if not await self.need_staff(ctx):
            return
        lines = [f"<@{s}> (`{s}`)" for s in self.d["staff"]] or ["Nobody was added yet. The bot owner is always staff."]
        await self.private(ctx, embed=note("**Matchmaking staff**\n" + "\n".join(lines)))

    # ---- bot owner: choose the staff -----------------------------------------
    @staff.command(name="add", description="Owner: make someone matchmaking staff")
    @app_commands.describe(user="The person to add")
    async def staff_add(self, ctx, user: discord.User):
        if not await self.need_owner(ctx):
            return
        if user.bot:
            return await ctx.reply(embed=note("Bots can't be staff.", ERR), mention_author=False)
        if str(user.id) in self.d["staff"]:
            return await ctx.reply(embed=note(f"{user.mention} is already matchmaking staff.", kind="warn"), mention_author=False)
        self.d["staff"].append(str(user.id))
        self.store.save()
        self.audit.add("staff_add", uid=ctx.author.id, partner=user.id, what="made matchmaking staff")
        self.feed(f"\U0001F6E1 {self.who(ctx.author.id)} made {self.who(user.id)} matchmaking staff")
        await ctx.reply(embed=note(f"{user.mention} is now matchmaking staff. They can use `{ctx.clean_prefix}match staff`.",
                                   kind="add"), mention_author=False)

    @staff.command(name="remove", description="Owner: remove someone from matchmaking staff")
    @app_commands.describe(user="The person to remove")
    async def staff_remove(self, ctx, user: discord.User):
        if not await self.need_owner(ctx):
            return
        if str(user.id) not in self.d["staff"]:
            return await ctx.reply(embed=note(f"{user.mention} isn't matchmaking staff.", kind="warn"), mention_author=False)
        self.d["staff"].remove(str(user.id))
        self.store.save()
        self.audit.add("staff_remove", uid=ctx.author.id, partner=user.id, what="removed from matchmaking staff")
        self.feed(f"\U0001F6E1 {self.who(ctx.author.id)} removed {self.who(user.id)} from matchmaking staff")
        await ctx.reply(embed=note(f"{user.mention} is no longer matchmaking staff.", kind="remove"), mention_author=False)

    @staff.command(name="feed", description="Owner: mirror all matchmaking activity into a private channel")
    @app_commands.describe(target="A channel (mention or ID) or 'off'")
    async def staff_feed(self, ctx, target: str = ""):
        if not await self.need_owner(ctx):
            return
        t = target.strip().lower()
        if not t:
            cid = self.d.get("feed_channel")
            text = (f"The live feed is **on** in <#{cid}>." if cid else
                    f"The live feed is **off**. Use `{ctx.clean_prefix}match staff feed #private-staff-channel`.")
            return await ctx.reply(embed=note(text), mention_author=False)
        if t in ("off", "none", "disable"):
            self.d["feed_channel"] = 0
            self.store.save()
            return await ctx.reply(embed=note("Live feed turned off.", kind="remove"), mention_author=False)
        m = re.search(r"\d{15,}", target)
        channel = self.bot.get_channel(int(m.group())) if m else None
        if not isinstance(channel, discord.TextChannel):
            return await ctx.reply(embed=note("Give me a text channel (mention or ID) the bot can see, or `off`.", ERR),
                                   mention_author=False)
        if channel.permissions_for(channel.guild.default_role).view_channel:
            return await ctx.reply(embed=note(
                f"{channel.mention} can be seen by everyone. The feed contains private chats, so pick a channel "
                "that only matchmaking staff can see.", ERR), mention_author=False)
        if not channel.permissions_for(channel.guild.me).send_messages:
            return await ctx.reply(embed=note(f"I can't send messages in {channel.mention}.", ERR), mention_author=False)
        self.d["feed_channel"] = channel.id
        self.store.save()
        self.audit.add("staff_feed", uid=ctx.author.id, what=f"turned the live feed on in {channel.id}")
        self.feed("\U0001F4E1 Matchmaking live feed started. Every chat message and event shows up here.")
        await ctx.reply(embed=note(f"Live feed is on in {channel.mention}. Chat messages, matches, reports and bans "
                                   "are mirrored there. Keep that channel private.", OK), mention_author=False)


# --------------------------------------------------------------------------
# Images for the card emojis. The bot creates them by itself on start-up (Matchmaking.make_app_emojis).
# --------------------------------------------------------------------------
# Which picture each app emoji was created from (name -> sha1 of the PNG/GIF in the pack), kept in emoji_hashes.json.
# The bot never touches an app emoji that already exists, so when a picture is redrawn the old one would stay for ever.
# With this file the bot notices that the pack changed, deletes that one emoji and creates it again from the new picture.
EMOJI_HASH_FILE = os.path.join(DATA_DIR, "emoji_hashes.json")
# Pictures that were redrawn BEFORE emoji_hashes.json existed (the first start with this version replaces them once):
#   Step 47: bl_hit, bl_stand, bl_double (colored buttons) and rep_star (gold sparkle).
#   v37: bl_wratio (the first flat badge was replaced by the shaded gold W).
#   v49e: bl_check, bl_cross, bl_warn (new flat tick / cross / warning triangle for every status line).
EMOJI_REDRAWN = ("bl_hit", "bl_stand", "bl_double", "rep_star", "bl_wratio", "bl_check", "bl_cross", "bl_warn")


def load_emoji_hashes():
    try:
        with open(EMOJI_HASH_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_emoji_hashes(hashes):
    try:
        tmp = EMOJI_HASH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(hashes, f)
        os.replace(tmp, EMOJI_HASH_FILE)
    except Exception:
        log.warning("Couldn't save emoji_hashes.json", exc_info=True)


EMOJI_PACK = {
    "ribbon_female": (
        "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"),
    "ribbon_male": (
        "iVBORw0KGgoAAAANSUhEUgAAAG0AAAAkCAYAAACDr7TyAAAKeklEQVR42u2ae4xd1XXGf2vv87r3emZsA7ZxTHGLDXGggYBA"
        "pAINU9HUaQggpWMh0RSEKmiTqiooaXFCNFy1gahVVUWpBUqqEiWFtp5INBJNH8TF04pAQlI5DbYoEIeEAjE2jj2P+zp779U/"
        "9r3zsMfEiJnqCs2WRjNz7rlnn7O+vb7vW+tsGbl/+n9skm8Nrq0ghpXRp0NVTIIGf9yI6gNiE1HQlcD0M2QEk+ai6JeNb7sv"
        "u3bjkLWZRXUFuD4dYqzx7WYzqNtlJuprjqH697ZIQfAr4enLPHNpUYiq/6eJT61+0QAkafrXrt0JIHYlQH0IGZgQPEaSXQBm"
        "dHS3ffyT+bOhdBM2r6KqK9nWX5AFm1ZM2Wztv6pV/IeqSvL6e0YFQEQeEGFkJUh9N4JNjAkdfbBel7AXTQRUAIbHyE0+87xN"
        "83OCa4cV+98XllHFphKCO9pslVufrg/9DAUDosNj2Im6tAzykM0SgPCOd2MC1sz99OdN4pMiQ1Qffrq++ujw2F6LiBqAa7og"
        "JWIecq1WC7EW3rn2X4DSw7GGcqyhHG9onz6tWNduB6v6xYjTNaF3/wCM7lY7vkP8yH1Tj6aVVTe61owDSd6JGVY62LBauGxz"
        "NMs+wH8+72h2wEjfMKNPK1VbNht7nvjUqmtHR3fb8fEdHuAEUFSCtHZpCDdq/9z/koPWcsrW9ZY/+lA2e3zfLs90S7FJP3GM"
        "EFT+KmbVKIzHo7NsPr5DPAq0ir2u3TyQpIWB5Su2zTw9EYkpb084NnvuCfqz2GI68ZxTXWs+PfoQf2baiwO12DX/f1ayBpsW"
        "tmzMHDzcqX4DVRnfMeczFmTa8L3Yibq4X/3czJdMav/Sl6qLh+jtj6mmErqBqmSCEZhsKBKLSfJUKJL490wbXPdkAWq5YMzC"
        "QDdLKF285okgVVIhS5idb74RgZONiHQ/b3ag4xeimVqhmsW5lzEhg82sCSV/c6AunWE0mUDcoqBN3IunDpmvPtxpNOpikgEN"
        "bpEwvH2K+uB7EypZvOy+n3iOTiujV6RsPtNwZEr5t2cdP3w9YEW4covh/VsslUx44aeBf/lvR8tBOs8ubV1v2LJe2LTWsKYq"
        "WAszLeWlI8q3D3pe/VlgVS6ndW8+QLMNF24yXPaLhrOHDNbA4UnlmR95vv+TQJFy0sJZKjUTSaxrtqaNlYfm47IoaIho15Ac"
        "vua+qa/lefW2suncydr3dm4pruw7t+cMVuKxx/Y53n22Ycv6uSX/kcsT7nykza9ssdx6dbrgGr92UcJdj7Rm6e2MVcKuWwqq"
        "2eJzTjaVP/9Gh2/udwxW5E1dZQgxg+7+cMaH35ecRIe3Dac8ts/xF//cIYRIl0uMm0+KPGk3px/de/fAq6O71Y6LLJCpkyuU"
        "rtjZ1DwYnGe5iuyppuJD1JbrLknYst7w8hsBVXAhUuDnby649eqU4w3l9ckYmtLDRZsM11+aMN1SRCCxUKTwv0eVJ1/w/OsP"
        "HBPPxexVhcGKcM8NOe9aY+i4U+uSEWh2lE/8RsYNlyaIREr91guefz/gaJVx0V13ScIf/npGo6NLzEEAYoIPiNgHT3XGSRk0"
        "Pi6eMTV7PsF3R+6feSIpqiOu3fCyxM3knhExCtMt5e7dbZ456Llze8ZNV6aUHioZ/ODlwM7dMas+/9GCresNQeGyzZaHv1Ui"
        "QKuEux5p8e0fBiabUWyCwjlnCF/93QpnDQh5Au/fYhn/ToldBDURaHSUSzdbPnRxgg/xGnf/Q5vH9jmCwsg2xxd+uyC1cP2l"
        "Cf/4PccLhyJVLgVNKuqTvGp9qzHxxM7aUzTVjO8Q/3NBAxgD6iLKfVMNEUGQZdNcEdj7nOepFz15Ijzzo8BNVzJrSB5+quTw"
        "VDQY//WS5/wNBgEGCyExQmLhjSnljSllZJtl20bDmpqQ2ghmanoBgbNXnzotjEDHweW/ZGf/n2wqq2uREqPjVGZaylA1cuL7"
        "zjXsf8VTzQS/FBFSEARFG4jo2JhK/XQybWxMTb0uYeRzUxcak37QtRq6lJq22Hj5jUCWQGIgdC2etfEhDh1XKpnQKhdSkUjM"
        "1nYJm84QPvubOeetW5zJfVd7Uvum8UIE1g3ESYLCmprwmRvyU35n4xohhKVcwGJdu6E2ybaP3D99cX2nfL+Hx5uCduDCrsdX"
        "+ztJnpuysfydkaBzFlrnmwLAd0FUXbzeanvl96+NgDkP023l0e86Dk1GzbzlqpR3rTkN19id2+scgFMt5blXA9aChnhSLA8E"
        "VeXHR3SBg10iq+ZtniW+0bkd+PgsHqcGTWV8h/jtYzrYpnGza5cgYvpy94hEw7K6Kpy/IepcYuFvnyz5wuMdBitCULjlqgR5"
        "C27h5aMhRqlbp931SJtGR6mkglfF+Ui7QxUhS6Gay4L6bwmGda2OCvamD4zpZ8Z3yFFUBZmTqAV8MjyGBehUZkazSvWs4Noe"
        "pT974N2yv5elPYp790bDuWca1taEndflbFprKP3pZXuRwpPPe0ofrf+6QeHT12ecc4ahmke6fM9Gwx0jKV+8rWDdgFD6pa9i"
        "NZQ+rVbWlkXj5l7T45SZNgGBMTXqp+8IPrAMfnZpl6SB441Y8F53SYILcO2FCb+8yZInsLomTDaVVcXPfw5VKFLhpSOBB/Z0"
        "+IMPxKJv+3sTrr7AcnhKKVLhrAGZ7aAElqtfJBJcANXbx8Z0V/2E4trM7/JTR4crjStsXlzuO60gfb5nRDW2wB7Y0+E7Bz1J"
        "92nWDwlpAvd8rc2Lh8Jp9wt9gIFC+LunHX/69TY/PhL1v5YLm880bBiKgB2ZUh7fHxvMy9MVwbpOMyR55aKJamuk1/SYr78L"
        "Xs1c89nJr2S1gY+65vIakLNXR8sO8b3W8WbsOxYprBucY+TXjgecj/S1tiYMdLOm45SfHo9BK33kxot/wbBxjWG6pex/JfDa"
        "scB56wypjd+ZailHZ+I8lQzOGjCztPrasYALMSCma0IGCmHL+ghWngqNtvL6lPLK0cCR6Zh5y9dAVpcUtaRsTH/9iU8P3NjD"
        "Zw60rtAN/9n0BuvN82LMKvWOt6Tgb3F03JxTTOa9PQ7KAg3K7BxLu27bqldH9Sy8dK1foxMby0ZiwzlPomnoGQVrmM3G+fMI"
        "kCYLqc6aOF+7jNdU7XX9Yw2YWpbagJzMI2IQkZLAtj07Kwd79j+Z7e6DM47fSmuVgbIx45Dltfl5Ohek+R1zI1BJ54zFfOpJ"
        "bQTxxM96v2t5XGe9z3yAbB4Y87/zZvP0qFKAarZw7WrX+IRld9QiaHBJpZZ1Go3bgHv2RjkL8U2WBhm+Fyv5zLNJVlwQytbK"
        "xp7+UO1gbGa867yapLXzH/8kDQAzujtYRJTazLVpUb3Al80VwPqnGDXetX1WrW10buYjvU1YZvYVtuNj3T5jWAlWf3URgldQ"
        "/TjETVgCMPInzfNMJgc0hLS3D3Jl9NUIJkmFwBXf/OP0e7GTZt2tSSXPIKxsCe9TbbNZKsGXvweictnYK9WhfOiASYtzg1sx"
        "IH3aRVCxqWhwk8awzQwWQzfZvHquXwGsj2VNJPjS27w66AO3G0E+phpWhKzvccOEsqOi4Y7/AwT01+0lq4yPAAAAAElFTkSu"
        "QmCC"),
    "ribbon_nonbinary": (
        "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"),
    "sparkle_blue": (
        "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"
        "RU5ErkJggg=="),
    "rep_star": (  # gold four-point sparkle in front of reputation amounts (was white, before that the purple "aura" person)
        "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"),
    "duel": (
        "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"
        "4WXuBgAAAABJRU5ErkJggg=="),
    "like_blue": (
        "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"
        "D+dKsc6W/PZhAAAAAElFTkSuQmCC"),
}

# The gambling emojis (cards, slot tiles, chip, coins, icons) are drawn pictures in gamble_emojis.py. They are created
# in the same start-up step as the card emojis above, and are looked up as bl_<name> (for example emo("c_as")).
try:
    from gamble_emojis import GAMBLE_PACK

    for _name, _data in GAMBLE_PACK.items():
        EMOJI_PACK.setdefault(_name, _data)
except ImportError:
    log.info("gamble_emojis.py is missing: the gambling embeds use plain text instead of the drawn emojis")

# The clean UI icons (pager arrows, page jump, white tick / cross / plus / minus for coloured buttons, status icons, on / off pills) are
# drawn pictures in ui_emojis.py, created in the same start-up step and looked up as bl_<name> (for example emo("check")).
try:
    from ui_emojis import UI_PACK

    for _name, _data in UI_PACK.items():
        EMOJI_PACK.setdefault(_name, _data)
except ImportError:
    log.info("ui_emojis.py is missing: the bot keeps using the server's own icon emojis")


async def setup(bot):
    await bot.add_cog(Matchmaking(bot))
