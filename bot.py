"""Discord Wordfilter Bot (discord.py 2.x)

- .wordfilter add/remove/list  (Manage Server only)
- Fun + reputation commands use ",": ,puh ,racist ,ratio ,gayest ,tightest ,goonlock ,reputation (alias ,rep) ,daily ,duel ,quests ,collect ,sop (everything else uses ".", help uses "+")
  (reputation is saved per server; ,fam war turns it into a daily contest between families)
  "." runs almost everything, "," the fun and reputation commands, "+" the help command.
- .prefix  (server owner and bot owner) shows or changes the prefix of ANY command, saved in settings.json.
  Example: .prefix puh !   then   !puh   (.prefix puh reset restores it)
- ,fam ...  families: create, invite, info, kidnap, top (no family roles, no forced [TAG] nicknames)
- +help  dropdown menu with every command, +help <command> for details.
  All replies share one embed style.
- .customize  (server administrators, server owner and bot owner) appearance / style /
  reset buttons: profile picture, banner, username, bio, embed color, name
  color and name font.
- When a message contains a trigger word, the bot deletes it and reposts it
  through ONE bot-owned webhook per channel, using the author's name + avatar.
- If that message was a reply, the repost carries a real embed
  ("Replying to <name>" + the replied text) and a "jump to message" link button.
  Nothing like "Replying to" is ever written inside the message text.
- Loop / duplicate protection:
    * ignores every bot and webhook message (so it can never read its own reposts)
    * on_message is defined once, commands are invoked once
    * each message ID is handled once
    * the original message is deleted BEFORE reposting, so if two copies of the
      bot ever run at once only the one whose delete succeeds reposts
    * one cached webhook per channel, created under a lock
    * extra webhooks made by this bot are deleted on startup
    * lock file stops a second copy on the same machine
    * circuit breaker if a channel gets flooded with reposts
"""
import asyncio
import base64
import datetime
import hashlib
import io
import json
import logging
import math
import os
import unicodedata
import random
import re
import shutil
import signal
import sys
import tempfile
import time
import types
import uuid
import zipfile
import zlib
from collections import OrderedDict, deque
from html import escape
from urllib.parse import urlparse, quote, unquote

# --------------------------------------------------------------------------
# Self-installing dependencies. Wispbyte's package installer fails here with
# "Permission denied: '.local'", so the bot installs what it needs into a
# folder it can write to (next to bot.py, or the temp dir as a fallback).
# --------------------------------------------------------------------------
def _ensure_dependencies(needed, required=True):
    import importlib
    import importlib.util
    import subprocess

    here = os.path.dirname(os.path.abspath(__file__))
    targets = [os.path.join(here, ".pylibs"), os.path.join(tempfile.gettempdir(), "wordfilter_pylibs")]
    for t in targets:
        if os.path.isdir(t) and t not in sys.path:
            sys.path.append(t)
    importlib.invalidate_caches()
    missing = [pkg for mod, pkg in needed if importlib.util.find_spec(mod) is None]
    if not missing:
        return
    print("Installing missing packages: " + ", ".join(missing), flush=True)
    for t in targets:
        try:
            os.makedirs(t, exist_ok=True)
        except OSError:
            continue
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
             "--target", t] + missing
        )
        if result.returncode == 0:
            if t not in sys.path:
                sys.path.append(t)
            importlib.invalidate_caches()
            print("Packages installed.", flush=True)
            return
    if not required:
        print("Could not install " + ", ".join(missing) + ". The bot runs without it.", flush=True)
        return
    print("Could not install the required packages (discord.py, aiohttp, python-dotenv).", flush=True)
    sys.exit(1)


_ensure_dependencies([("discord", "discord.py>=2.7"), ("aiohttp", "aiohttp"), ("dotenv", "python-dotenv")])
_ensure_dependencies([("PIL", "Pillow")], required=False)  # draws the animated ,title pictures; without it titles show as plain text

import discord
import aiohttp
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import cleanbox    # draws every embed as a clean container with no side line (Bleed style), see CHANGES.md
import gambling
import plinko_render  # draws the plinko board image / animation (Pillow only)
import roleplace   # moves the bot's roles under Staff / Soul (see CHANGES.md, Step 48)
import shopkit     # shop rules: names, streak freezes, bounties
import shopstyle   # role color styles (solid / gradient / holographic), hex codes, emoji pages
import titlecard   # animated ,title pictures
from gambling import Blackjack, RNG as GAMBLE_RNG, card_label, card_points, hand_value  # blackjack rules + the random generator

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", ".")
FUN_PREFIX = os.getenv("FUN_PREFIX") or os.getenv("PUH_PREFIX") or ","
MATCH_PREFIX = os.getenv("MATCH_PREFIX") or "."  # friend matchmaking commands (matchmaking.py)
HELP_PREFIX = os.getenv("HELP_PREFIX", "+")
PORT = int(os.getenv("PORT", "8080"))
# Optional: comma separated user IDs that count as bot owner (they can always use
# .customize, even without Administrator). Leave empty to use the owner of the bot
# application (or every member of its team).
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").replace(" ", "").split(",") if x.isdigit()}
DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
WORDS_FILE = os.path.join(DATA_DIR, "words.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

GREY = 0x2B2D31    # same as Discord's dark embed background, so the side bar is invisible
OK_COLOR = 0x3BA55D  # success bar
ERR_COLOR = 0xD9534F # error bar
WARN_COLOR = 0xFAA61A  # warning bar (amber, like Bleed's warnings)
WAIT_COLOR = 0x5865F2  # "working on it" bar (blurple, like the loading line of /imyy)

log = logging.getLogger("wordfilter")


# --------------------------------------------------------------------------
# Single instance lock (same machine only)
# --------------------------------------------------------------------------
_lock_handle = None


def acquire_single_instance_lock():
    global _lock_handle
    try:
        import fcntl
    except ImportError:  # Windows has no fcntl
        return
    handle = open(os.path.join(tempfile.gettempdir(), "wordfilter_bot.lock"), "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another copy of the bot is already running on this machine. Exiting.")
        sys.exit(1)
    _lock_handle = handle  # keep a reference so the lock stays held


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
# Trigger words every NEW server starts with (copied from the old server's list). Add or remove freely: this list is only used
# the first time a server is seen, or when someone runs `.wordfilter defaults`.
DEFAULT_WORDS = [
    "kys", "kms", "cock", "dick", "die", "clit", "pussy", "pedo", "retard", "retarded",
    "retards", "fag", "fags", "faggots", "groom", "faggot", "dicks", "whore", "nazi", "doxx",
]


class WordStore:
    """Trigger words per server, saved in words.json (lowercase, no duplicates)."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self._patterns = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {}
        except Exception:
            log.exception("Could not read %s, starting with an empty list", self.path)
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _seed(self, guild_id):
        """A server we have never seen starts with DEFAULT_WORDS (an emptied list stays empty)."""
        if str(guild_id) not in self.data:
            self.data[str(guild_id)] = list(DEFAULT_WORDS)
            self._patterns.pop(guild_id, None)
            self.save()

    def add_defaults(self, guild_id):
        """Add every default word that is missing. Returns how many were added."""
        self._seed(guild_id)
        words = self.data[str(guild_id)]
        missing = [w for w in DEFAULT_WORDS if w not in words]
        if missing:
            words.extend(missing)
            self._patterns.pop(guild_id, None)
            self.save()
        return len(missing)

    def words(self, guild_id):
        self._seed(guild_id)
        return list(self.data.get(str(guild_id), []))

    def add(self, guild_id, word):
        self._seed(guild_id)
        words = self.data.setdefault(str(guild_id), [])
        if word in words:
            return False
        words.append(word)
        self._patterns.pop(guild_id, None)
        self.save()
        return True

    def remove(self, guild_id, word):
        self._seed(guild_id)
        words = self.data.get(str(guild_id), [])
        if word not in words:
            return False
        words.remove(word)
        self._patterns.pop(guild_id, None)
        self.save()
        return True

    def match(self, guild_id, text):
        """True if the text contains a trigger word as a whole word."""
        if not text:
            return False
        pattern = self._patterns.get(guild_id)
        if pattern is None:
            words = self.words(guild_id)
            if not words:
                return False
            pattern = re.compile(
                "|".join(rf"(?<!\w){re.escape(w)}(?!\w)" for w in words), re.IGNORECASE
            )
            self._patterns[guild_id] = pattern
        return pattern.search(text) is not None


class RecentIds:
    """Remembers the last N message IDs so each one is handled only once."""

    def __init__(self, size=2000):
        self._ids = OrderedDict()
        self._size = size

    def claim(self, id_):
        if id_ in self._ids:
            return False
        self._ids[id_] = None
        if len(self._ids) > self._size:
            self._ids.popitem(last=False)
        return True


class Settings:
    """Look settings in settings.json: one embed color for everything, plus a name style per server."""

    GUILD_DEFAULTS = {"name_color": None, "secondary_color": None, "font": "default", "effect": "solid"}

    def __init__(self, path):
        self.path = path
        self.data = {"embed_color": None, "guilds": {}, "prefixes": {}}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data["embed_color"] = loaded.get("embed_color")
                self.data["guilds"] = loaded.get("guilds") or {}
                prefixes = loaded.get("prefixes")
                self.data["prefixes"] = prefixes if isinstance(prefixes, dict) else {}
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("Could not read %s, using the default look", self.path)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @property
    def embed_color(self):
        return self.data.get("embed_color")

    def set_embed_color(self, value):
        self.data["embed_color"] = value
        self.save()

    def guild(self, guild_id):
        merged = dict(self.GUILD_DEFAULTS)
        merged.update(self.data["guilds"].get(str(guild_id), {}))
        return merged

    def set_guild(self, guild_id, **fields):
        current = self.data["guilds"].setdefault(str(guild_id), {})
        current.update(fields)
        self.save()

    def clear_guild(self, guild_id):
        self.data["guilds"].pop(str(guild_id), None)
        self.save()

    # ---- per-command prefix overrides (set with +prefix) ----
    @property
    def prefixes(self):
        return dict(self.data.get("prefixes", {}))

    def set_prefix(self, command_name, prefix):
        self.data.setdefault("prefixes", {})[command_name] = prefix
        self.save()

    def reset_prefix(self, command_name):
        removed = self.data.setdefault("prefixes", {}).pop(command_name, None)
        self.save()
        return removed is not None


store = WordStore(WORDS_FILE)
handled = RecentIds()
settings = Settings(SETTINGS_FILE)


DEFAULT_THEME = GREY  # no visible bar on help, lists and fun replies


def theme():
    """The neutral embed color: the owner's pick from .customize, or the default grey."""
    return settings.embed_color or DEFAULT_THEME


def make_embed(text, color):
    return discord.Embed(description=text, color=color)


# --------------------------------------------------------------------------
# Custom emojis. Upload the bl_* pack (bl_check, bl_cross, bl_warn, bl_lock, bl_owner,
# bl_member, bl_family, bl_role, bl_base, bl_heart, bl_percent, plus the optional
# bl_*_anim GIFs) to ANY server the bot is in. The bot finds them by name, so there
# are no IDs to copy. Until they exist the plain fallback text is used instead.
# --------------------------------------------------------------------------
# The emojis already uploaded to the server, by their real names (no bl_ prefix needed).
# Matching ignores upper/lower case, and a name also matches when it only STARTS with the
# text below (Discord cuts long names in the list, e.g. small_check_whi... = small_check_white).
# "lock", "member", "heart" and "percent" have no match on purpose: they keep using bl_<n> or
# their plain fallback, so the fun commands look exactly like before.
EMOJI_ALIASES = {
    # the status pack (names exactly as added with ,emoji add). They come FIRST, the older names stay as a backup.
    "check": ["approve", "small_check_whi", "z_check2"],
    "cross": ["deny", "x_white", "CB_redtick"],
    "warn": ["warning", "08excla"],
    "add": ["add"],
    "remove": ["remove"],
    "wait": ["cooldown"],
    "left": ["left"],
    "right": ["right"],
    "lastfm": ["lastfm"],
    "spotify": ["spotify"],
    "telegram": ["telegram"],
    "eth": ["eth"],
    "dove": ["dove"],
    "owner": ["white_crown"],
    "base": ["white_mod"],
    "role": ["elife_white_star"],
    "family": ["elife_white_own"],
    "info": ["info_white"],
    "link": ["elife_white_link"],
    "rules": ["elife_white_rules"],
    "chat": ["elife_white_chat"],
    "announce": ["announce"],
    "gift": ["white_gift"],
    "arrow": ["red_arrow"],
    "reply": ["whitereply"],
    "replyextend": ["whitereplyextend"],
    "trophy": ["86269trophy", "trophy"],  # W ratio, and in front of the ,gayest / ,tightest winner
    "skull": ["Skull_2", "skull"],        # L ratio
    "mystats": ["stats", "mystats", "my_stats"],  # icon on the aura leaderboard's My Stats button (the server emoji :stats:)
    "aura": ["m_gem", "rep_star"],        # the diamond :m_gem: in front of reputation amounts (any server the bot shares with it); the bot's own gold sparkle is the fallback
    "duel": ["duel"],                     # on the duel challenge
    "smash": ["004_heart"],               # the Smash button and bar in ,sop (the white outline heart)
    "shop": ["dcshop"],                   # the store icon: shop title, Buy buttons, editor titles
    "update": ["update"],                 # Edit / Refresh / Reset buttons of the shop
    "crown": ["crown"],                   # in front of the reputation leaderboard title (the server emoji :crown:)
    "pride": ["rainbow_flag", "pride_flag"],  # in front of the ,gayest winner (falls back to the unicode rainbow flag)
}


def _rep_emoji_from_env():
    """REP_EMOJI in the env (for example <:m_gem:123456789012345678>, type \\:m_gem: in Discord to copy it) pins the reputation emoji
    even when the bot isn't in the server that has it. Empty or not in that form: the normal lookup below is used."""
    raw = os.getenv("REP_EMOJI", "").strip()
    if not raw.startswith("<"):
        return None
    try:
        found = discord.PartialEmoji.from_str(raw)
        return found if found.id else None
    except Exception:
        return None


REP_EMOJI = _rep_emoji_from_env()


def find_emoji(name):
    """The custom emoji object for an icon name, or None. Order:
      0. for the reputation icon only: REP_EMOJI from the env, when it is set,
      1. an emoji called bl_<n> on any server the bot is in (so a server can override an icon by uploading its own),
      2. the bot's own clean icon pack, created on the bot's account (application emoji bl_<n>, see make_app_emojis),
      3. the older server emoji names in EMOJI_ALIASES (exact names first, then names that only start with it),
      4. an application emoji with one of those alias names (aura, duel, ribbons...)."""
    if name == "aura" and REP_EMOJI is not None:
        return REP_EMOJI
    mine = getattr(bot, "mm_app_emojis", None) or {}
    found = discord.utils.get(bot.emojis, name=f"bl_{name}") or mine.get(f"bl_{name}".lower())
    if found is None:
        wanted = [w.lower() for w in EMOJI_ALIASES.get(name, [])]
        for w in wanted:  # exact names first ("whitereply" must not pick "whitereplyextend")
            found = next((e for e in bot.emojis if e.name.lower() == w), None)
            if found:
                break
        if found is None:
            for w in wanted:
                found = next((e for e in bot.emojis if e.name.lower().startswith(w)), None)
                if found:
                    break
        if found is None:
            for w in wanted:
                found = mine.get(w)
                if found:
                    break
    return found


def emo(name, fallback=""):
    """A custom emoji as text for embeds, or the fallback when the server doesn't have it."""
    found = find_emoji(name)
    return str(found) if found else fallback


def button_icon(key, style):
    """The emoji for icon `key` on a button of this style. A coloured icon would vanish on a coloured button (a green tick on a green
    button, a red cross on a red one), so on blurple / green / red buttons the white version bl_<key>_w is used when the icon pack has one
    (check_w, cross_w, add_w, remove_w). Grey buttons keep the coloured icon."""
    if style != discord.ButtonStyle.secondary:
        white = find_emoji(f"{key}_w")
        if white is not None:
            return white
    return find_emoji(key)


def style_buttons(view, icons, icon_only=()):
    """Put custom emojis on a view's buttons, matched by label. Buttons without a matching emoji stay as they are.
    A label in icon_only is dropped when its emoji is found, so the button shows just the icon."""
    for item in view.children:
        if isinstance(item, discord.ui.Button) and item.label in icons:
            found = button_icon(icons[item.label], item.style)
            if found is not None:
                if item.label in icon_only:
                    item.label = None
                item.emoji = found


def info_icon():
    """The info emoji followed by a space, or nothing if the emoji isn't available."""
    icon = emo("info")
    return icon + " " if icon else ""


# kind -> (plain fallback, bar color). The emoji for each kind is in STATUS_EMOJI.
#   ok / add / remove = green bar (approve, add, remove)   warn = amber (warning)   err = red (deny)
#   cooldown = amber with the clock   wait = blurple with the clock (something is still being worked on)
STATUS_STYLES = {
    "ok": ("\u2705", OK_COLOR),
    "add": ("\u2705", OK_COLOR),
    "remove": ("\u2705", OK_COLOR),  # a removal that worked is a success: same green tick, same green bar (no red minus on a green box)
    "warn": ("\u26a0\ufe0f", WARN_COLOR),
    "err": ("\u274c", ERR_COLOR),
    "cooldown": ("\u23f3", WARN_COLOR),
    "wait": ("", WAIT_COLOR),
}
STATUS_EMOJI = {"ok": "check", "add": "check", "remove": "check", "err": "cross", "warn": "warn", "cooldown": "wait", "wait": "wait"}
# ok / add / remove all use the green tick: a removal that worked is a success (no red minus on a green box).
# check / cross / warn are the bot's own flat green tick, red cross and yellow warning triangle (make_status_icons.py).


def status_icon(kind):
    return emo(STATUS_EMOJI[kind], STATUS_STYLES[kind][0])


def status_text(kind, text):
    icon = status_icon(kind)
    return f"{icon} {text}" if icon else text


def status_embed(ctx, kind, text):
    """One-line result such as  \u2713 @user: text  (mentions inside embeds never ping)."""
    return status_line(ctx.author, kind, text)


async def plain_reply(ctx, kind, text):
    """Plain-text reply (no embed) to the command message, like a normal chat bot: it shows the little reply arrow, and never pings.
    Used by the .wordfilter commands. In a slash command it is just a normal message."""
    return await ctx.reply(status_text(kind, text), allowed_mentions=discord.AllowedMentions.none())


def status_line(user, kind, text):
    """Same as status_embed, for anyone with a .mention (used by menus and buttons)."""
    return discord.Embed(description=status_text(kind, f"{user.mention}: {text}"), color=STATUS_STYLES[kind][1])


def avatar_of(member):
    avatar = getattr(member, "display_avatar", None)
    return avatar.url if avatar else None


def with_author(embed, user):
    """Small avatar + name line at the top of an embed, like Bleed's replies.
    Skipped when the text already @mentions that same person (,puh ,racist ,ratio ,rep on yourself...): the name would show twice and make the embed longer."""
    if user is not None:
        text = embed.description or ""
        uid = getattr(user, "id", None)
        if uid is not None and (f"<@{uid}>" in text or f"<@!{uid}>" in text):
            return embed
        embed.set_author(name=user.display_name, icon_url=avatar_of(user))
    return embed


FUN_COMMANDS = {"fam", "puh", "racist", "ratio", "gayest", "tightest", "goonlock", "reputation", "daily", "duel", "bottle", "coinflip", "slots", "plinko", "blackjack", "rob", "quests", "collect", "sop", "wordfilter", "shop", "inventory", "myrole", "title", "bounty", "badge", "wordstats", "assist", "assistbackfill"}  # the commands that use ","
GAME_SECONDS = 20  # how long ,gayest / ,tightest keep the Join button open
MATCH_COMMANDS = {"match", "stats"}  # matchmaking.py (its alias "mm" resolves to match)

# Aura (saved per server in aura.json). ,ratio / ,daily / ,duel change it, ,aura shows it, ,aura top ranks it.
AURA_FILE = os.path.join(DATA_DIR, "aura.json")
RATIO_COOLDOWN = int(os.getenv("RATIO_COOLDOWN", "300"))           # seconds between two ,ratio per person (5 minutes)
RATIO_WIN_CHANCE = float(os.getenv("RATIO_WIN_CHANCE", "0.6"))     # chance of a W ratio (an L ratio is the rest)
RATIO_WIN_MAX = int(os.getenv("RATIO_WIN_MAX", "500"))             # a W ratio gives 1 to this much reputation
RATIO_LOSS_MAX = int(os.getenv("RATIO_LOSS_MAX", "200"))           # an L ratio takes 1 to this much (it can take you below zero)
RATIO_DEBT_LOSS = float(os.getenv("RATIO_DEBT_LOSS", "0.3"))       # when you're already below zero, an L only takes this share (30%)
COLLECT_AMOUNT = int(os.getenv("COLLECT_AMOUNT", "100"))           # what ,collect gives staff and server boosters
COLLECT_COOLDOWN = int(os.getenv("COLLECT_COOLDOWN", "86400"))     # seconds between two ,collect per person (24 hours)
SOP_COOLDOWN_SECONDS = int(os.getenv("SOP_COOLDOWN_SECONDS", "60"))  # time one person has to wait between two ,sop
DAILY_AURA = int(os.getenv("DAILY_AURA", "100"))                   # what ,daily gives on day one
DAILY_STREAK_BONUS = int(os.getenv("DAILY_STREAK_BONUS", "10"))    # extra aura for every streak day after the first
DAILY_STREAK_MAX = 10                                              # streak days that still add to the bonus
DUEL_COOLDOWN = int(os.getenv("DUEL_COOLDOWN", "30"))              # seconds between two ,duel per person
BOTTLE_COOLDOWN = int(os.getenv("BOTTLE_COOLDOWN", "60"))  # seconds between two ,bottle per person
BOTTLE_ACTIVE_MINUTES = int(os.getenv("BOTTLE_ACTIVE_MINUTES", "30"))  # who counts as "active" (spoke in this channel in the last X minutes)
DUEL_SECONDS = 60                                                  # how long a challenge waits for an answer
WAR_BONUS = int(os.getenv("WAR_BONUS", "500"))                     # each member of the day's winning family gets this (the war is daily)

# Gambling with reputation (,coinflip ,slots and ,blackjack), always against the bot. The odds: coinflip 54% for the player,
# slots pay back about 95% (house edge), blackjack pays 2:1: see Step 44 in CHANGES.md for the numbers and how to dial
# them back. The limits are clamped so a typo in .env can't break anything: the smallest bet is at least 1, the biggest at least
# the smallest (and at most a billion), the cooldown can't be negative, the coin chance stays between 0 and 1.
GAMBLE_MIN_BET = max(1, int(os.getenv("GAMBLE_MIN_BET", "10")))                                        # smallest bet
GAMBLE_MAX_BET = min(max(GAMBLE_MIN_BET, int(os.getenv("GAMBLE_MAX_BET", "25000"))), 10 ** 9)         # biggest bet
GAMBLE_COOLDOWN = max(0.0, float(os.getenv("GAMBLE_COOLDOWN", "5")))                                   # seconds between two bets per person (all three games share it)
try:
    COINFLIP_WIN_CHANCE = float(os.getenv("COINFLIP_WIN_CHANCE", "0.54"))  # chance the player wins a flip. 0.5 = a fair coin, 0.54 = players win 54% of the time
except ValueError:
    COINFLIP_WIN_CHANCE = 0.54
if COINFLIP_WIN_CHANCE != COINFLIP_WIN_CHANCE:  # nan
    COINFLIP_WIN_CHANCE = 0.54
COINFLIP_WIN_CHANCE = min(1.0, max(0.0, COINFLIP_WIN_CHANCE))
gambling.BLACKJACK_PAYS = gambling.parse_ratio(os.getenv("BLACKJACK_PAYOUT", "2:1"), (2, 1))  # what a blackjack pays: "2:1", "3:2" (casino), "6:5"...
BJ_TIMEOUT = min(600.0, max(10.0, float(os.getenv("BLACKJACK_TIMEOUT", "60"))))  # seconds without a press before the game is dealt with (see BJ_TIMEOUT_ACTION)
BJ_TIMEOUT_ACTION = os.getenv("BLACKJACK_TIMEOUT_ACTION", "refund").strip().lower()  # what an idle game does: refund (cancelled, bet returned) | stand (dealer plays it out, win or lose) | lose (the bet is lost)
if BJ_TIMEOUT_ACTION not in ("refund", "stand", "lose"):
    BJ_TIMEOUT_ACTION = "refund"
SLOTS_SYMBOLS = [  # (symbol, weight = how common it is, what three of them pay: x the bet)
    ("\U0001F352", 22, 3),     # cherry
    ("\U0001F34B", 20, 4),     # lemon
    ("\U0001F347", 18, 5),     # grape
    ("\U0001F514", 16, 8),     # bell
    ("\U0001F48E", 8, 20),     # diamond
    ("7\ufe0f\u20e3", 4, 40),   # seven: the jackpot
]
try:
    SLOTS_PAIR_PAYOUT = float(os.getenv("SLOTS_PAIR_PAYOUT", "1.6"))  # any two matching symbols pay this x the bet (1.6 = the house wins about 4-5%, 1.75 = players win about 3%)
except ValueError:
    SLOTS_PAIR_PAYOUT = 1.6
if not 0.0 <= SLOTS_PAIR_PAYOUT <= 10.0:  # also catches nan
    SLOTS_PAIR_PAYOUT = 1.6
ROB_COOLDOWN = max(0, int(os.getenv("ROB_COOLDOWN", "1800")))  # seconds between two ,rob per person (30 minutes)
try:
    ROB_CHANCE = min(1.0, max(0.0, float(os.getenv("ROB_CHANCE", "0.45"))))  # chance a rob works (0.45 = 45%)
except ValueError:
    ROB_CHANCE = 0.45
ROB_MIN_BALANCE = max(1, int(os.getenv("ROB_MIN_BALANCE", "100")))  # the person being robbed needs at least this much reputation
ROB_DEBT_LIMIT = max(0, int(os.getenv("ROB_DEBT_LIMIT", "500")))  # a failed rob can put you in debt, but never lower than minus this
ROB_STEAL = (0.10, 0.25)    # a rob takes (or costs) this share of the VICTIM's reputation
ROB_MAX = max(1, int(os.getenv("ROB_MAX", "1500")))  # the most one rob can move
ROB_IMMUNITY = max(0, int(os.getenv("ROB_IMMUNITY", "3600")))  # seconds someone is safe after being robbed
SLOTS_SPIN_SECONDS = 0.8     # seconds between two frames of the spinning animation (3 reels stop one after the other)

# Reputation shop settings (the shop code is further down, next to the backup code). Every number can be changed in .env.
def _env_int(name, default, low=0, high=10 ** 9):
    try:
        return min(high, max(low, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


SHIELD_PRICE = _env_int("SHIELD_PRICE", 7500, 1)
SHIELD_HOURS = _env_int("SHIELD_HOURS", 24, 1, 720)                       # one Rob Shield protects this long
SHIELD_MAX_HOURS = max(SHIELD_HOURS, _env_int("SHIELD_MAX_HOURS", 72, 1, 720))  # buying more stacks up to this
FREEZE_PRICE = _env_int("FREEZE_PRICE", 6000, 1)
FREEZE_MAX = _env_int("FREEZE_MAX", 3, 1, 30)                             # Streak Freezes one person can hold
BOOST_PRICE = _env_int("BOOST_PRICE", 5000, 1)
BOOST_MAX = _env_int("BOOST_MAX", 3, 1, 30)                               # Quest Boosts one person can hold
BOOST_CAP = _env_int("BOOST_CAP", 2000, 1)                                # the most a Quest Boost adds to one claim
LUCK_PRICE = _env_int("LUCK_PRICE", 50000, 1)                             # one Luck Boost (LUCK_GAMES games of better luck)
LUCK_GAMES = _env_int("LUCK_GAMES", 5, 1, 100)                            # games one Luck Boost lasts
HALF_PRICE = _env_int("HALF_PRICE", 12000, 1)                             # one Half Loss (HALF_GAMES games where a lost bet only costs half)
HALF_GAMES = _env_int("HALF_GAMES", 5, 1, 100)                            # games one Half Loss lasts
BOOST_MAX_BET = _env_int("BOOST_MAX_BET", 25000, 1, 10 ** 9)               # Luck Boost / Half Loss only work on bets up to this (a bigger bet is played normally and uses no game). 25,000 = GAMBLE_MAX_BET, so they work on every gamble bet
BOOST_GAMES_MAX = _env_int("BOOST_GAMES_MAX", 100, 1, 10000)              # the most games of each of the two one person can hold at once
try:
    LUCK_WIN_CHANCE = min(1.0, max(0.0, float(os.getenv("LUCK_WIN_CHANCE", "0.70"))))  # win chance while a Luck Boost is active (never lowers a game's normal chance)
except ValueError:
    LUCK_WIN_CHANCE = 0.70
DUELINS_PRICE = _env_int("DUELINS_PRICE", 8000, 1)                         # one Duel Insurance (DUELINS_DUELS duels where a lost bet only moves half)
DUELINS_DUELS = _env_int("DUELINS_DUELS", 5, 1, 100)                      # duels one Duel Insurance lasts
DAILYBOOST_PRICE = _env_int("DAILYBOOST_PRICE", 800, 1)                   # one Daily Boost (your next ,daily pays double)
DAILYBOOST_MAX = _env_int("DAILYBOOST_MAX", 3, 1, 30)                     # Daily Boosts one person can hold
DAILYBOOST_CAP = _env_int("DAILYBOOST_CAP", 300, 1)                       # the most a Daily Boost adds to one daily
REROLL_PRICE = _env_int("REROLL_PRICE", 2500, 1)                          # one Quest Reroll (swap one unfinished quest for a new one)
REROLL_MAX = _env_int("REROLL_MAX", 3, 1, 30)                             # Quest Rerolls one person can hold
BADGE_PRICE = _env_int("BADGE_PRICE", 5000, 1)                            # a Leaderboard Badge (a short tag or emoji after your name on the leaderboards)
BADGE_CHANGE_PRICE = _env_int("BADGE_CHANGE_PRICE", 1500, 1)              # changing a badge you already own
BADGE_MAX_CHARS = _env_int("BADGE_MAX_CHARS", 6, 1, 12)                   # letters in a text badge (an emoji badge is always fine)
LOCKPICK_PRICE = _env_int("LOCKPICK_PRICE", 3000, 1)                      # one Lockpick (makes your next ,rob likelier to work)
LOCKPICK_MAX = _env_int("LOCKPICK_MAX", 3, 1, 30)                         # Lockpicks one person can hold
try:
    LOCKPICK_CHANCE = min(1.0, max(0.0, float(os.getenv("LOCKPICK_CHANCE", "0.65"))))  # chance a rob works with a Lockpick (never lowers ROB_CHANCE)
except ValueError:
    LOCKPICK_CHANCE = 0.65
VAULT_PRICE = _env_int("VAULT_PRICE", 50000, 1)                           # bought once, kept for good
VAULT_PERCENT = _env_int("VAULT_PERCENT", 50, 1, 100)                     # share of the owner's reputation robbers can't touch
ROLE_PRICE = _env_int("ROLE_PRICE", 150000, 1)
CUSTOM_ROLE_NAME_MAX = _env_int("CUSTOM_ROLE_NAME_MAX", 32, 4, 100)
ROLE_DEFAULT_COLOR = 0x99AAB5
TITLE_PRICE = _env_int("TITLE_PRICE", 40000, 1)
TITLE_CHANGE_PRICE = _env_int("TITLE_CHANGE_PRICE", 12000, 1)              # changing the text, effect or color of a title you already own
TITLE_COOLDOWN = _env_int("TITLE_COOLDOWN", 30, 0, 3600)
BOUNTY_MIN = _env_int("BOUNTY_MIN", 100, 1)
BOUNTY_FEE_PERCENT = _env_int("BOUNTY_FEE_PERCENT", 10, 0, 50)            # paid on top of a bounty and gone for good
BOUNTY_DAYS = _env_int("BOUNTY_DAYS", 3, 1, 30)                           # a bounty nobody collects goes back to its owner after this
BOUNTY_MAX_ENTRIES = 25                                                    # bounties that can stack on one person
TITLE_DIR = os.path.join(DATA_DIR, "titles")
ROLE_CHECK_SECONDS = _env_int("ROLE_CHECK_SECONDS", 900, 120, 86400)      # how often the Staff / Soul role positions are re-checked
ROLE_LOOKALIKE_WORDS = {"staff", "soul", "owner", "admin", "administrator", "moderator", "mod", "helper", "everyone", "here", "booster"}
SOUL_RESERVED = ("staff", "soul", "owner", "co owner", "admin", "administrator", "moderator", "mod", "helper",
                 "everyone", "here", "booster", "server booster", "nitro booster")




HELP_PREFIX_COMMANDS = {"help"}  # the only command that uses "+" (its alias "commands" resolves to the same command)


def default_prefix_for(root_name):
    """The built-in prefix: help uses '+', puh/racist/ratio/gayest/tightest use ',', everything else '.'."""
    if root_name in FUN_COMMANDS:
        return FUN_PREFIX
    if root_name in MATCH_COMMANDS:
        return MATCH_PREFIX
    if root_name in HELP_PREFIX_COMMANDS:
        return HELP_PREFIX
    return PREFIX


def prefix_for(root_name):
    """Which prefix a command answers to: the one set with +prefix, else its default."""
    return settings.prefixes.get(root_name) or default_prefix_for(root_name)


def all_prefixes():
    """Every prefix currently in use, longest first so '..' is tried before '.'."""
    found = {PREFIX, FUN_PREFIX, MATCH_PREFIX, HELP_PREFIX} | set(settings.prefixes.values())
    return sorted(found, key=lambda p: (-len(p), p))


async def get_prefix(bot_, message):
    """Command prefixes for discord.py: all the ones in use, plus @mention (always works for +prefix)."""
    return commands.when_mentioned(bot_, message) + all_prefixes()


def roll_puh():
    """Random split that always adds up to 100: (tight, loose)."""
    tight = random.randint(0, 100)
    return tight, 100 - tight


def human_time(seconds):
    """A time left as '45s', '12m 30s' or '5h 12m' (rounded up to the second)."""
    seconds = max(1, math.ceil(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m" if seconds % 60 == 0 else f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


def parse_amount(text, balance):
    """'500', '1,500', '2k', '1.5m', 'all' or 'half' -> a whole number of reputation, or None when it isn't one."""
    t = text.strip().lower().replace(",", "").replace("_", "")
    if t == "all":
        return max(0, balance)
    if t == "half":
        return max(0, balance // 2)
    mult = 1
    if t.endswith("k"):
        mult, t = 1000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1_000_000, t[:-1]
    try:
        value = float(t) * mult
    except ValueError:
        return None
    return int(value) if math.isfinite(value) else None


def week_id(now=None):
    """The ISO week of the weekly quests (the family war is daily now, see war_day_id), in UTC: '2026-W44'. A new week starts every Monday at 00:00 UTC."""
    year, week, _day = datetime.datetime.fromtimestamp(now or time.time(), datetime.timezone.utc).isocalendar()
    return f"{year}-W{week:02d}"


def week_end(now=None):
    """Unix time when the current quest week ends (next Monday 00:00 UTC)."""
    d = datetime.datetime.fromtimestamp(now or time.time(), datetime.timezone.utc)
    start = (d - datetime.timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((start + datetime.timedelta(days=7)).timestamp())


def war_day_id(now=None):
    """The day of the family war, in UTC: '2026-11-01'. A new war starts every day at 00:00 UTC.
    (Stored under the old 'week' keys of families.json, so old data and backups still load.)"""
    return datetime.datetime.fromtimestamp(now or time.time(), datetime.timezone.utc).date().isoformat()


def war_end(now=None):
    """Unix time when the current family war ends (next 00:00 UTC)."""
    d = datetime.datetime.fromtimestamp(now or time.time(), datetime.timezone.utc)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return int((start + datetime.timedelta(days=1)).timestamp())


class AuraStore:
    """Reputation per server, saved in aura.json: {server: {user: {aura, ratio_at, daily_day, streak}}}."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {}
        except Exception:
            log.exception("Could not read %s, starting without reputation", self.path)
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def user(self, gid, uid):
        """The record of one person (created when missing). Use balance() to only look."""
        u = self.data.setdefault(str(gid), {}).setdefault(str(uid), {})
        u.setdefault("aura", 0)
        u.setdefault("ratio_at", 0)
        u.setdefault("daily_day", "")
        u.setdefault("streak", 0)
        return u

    def balance(self, gid, uid):
        return self.data.get(str(gid), {}).get(str(uid), {}).get("aura", 0)

    @staticmethod
    def _today():
        return datetime.datetime.now(datetime.timezone.utc).date()

    def add(self, gid, uid, delta):
        """Change someone's reputation (can go below zero) and save. Also notes it in that day's gained / lost totals
        (kept for two weeks) for the 7 Days leaderboard. Returns the new total."""
        u = self.user(gid, uid)
        delta = int(delta)
        u["aura"] += delta
        self._tally(u, delta)
        self.save()
        self._goat(gid)
        return u["aura"]

    def _tally(self, u, delta):
        """Note a change in that day's gained / lost totals (kept for two weeks)."""
        days = u.setdefault("days", {})
        key = self._today().isoformat()
        gained, lost = days.get(key, [0, 0])
        days[key] = [gained + max(delta, 0), lost + max(-delta, 0)]
        cutoff = (self._today() - datetime.timedelta(days=14)).isoformat()
        for old in [d for d in days if d < cutoff]:
            del days[old]

    def _goat(self, gid):
        try:
            schedule_goat_role(int(gid))  # the goat role follows the all-time #1 (checked a few seconds later)
        except NameError:
            pass

    # Blackjack parks the stake outside the balance while a game runs ("bj_hold" in the person's record). Taking it,
    # paying it out and giving it back after a restart are each ONE change that is saved in one go, so the money can
    # neither be spent twice nor vanish.
    def hold(self, gid, uid, amount):
        """Take `amount` out of the balance and park it, in one step. Returns False (and changes nothing) when the amount
        isn't a whole number of at least 1 or the balance doesn't cover it."""
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
            return False
        u = self.user(gid, uid)
        if u["aura"] < amount:
            return False
        u["aura"] -= amount
        u["bj_hold"] = int(u.get("bj_hold", 0)) + amount
        self.save()
        self._goat(gid)
        return True

    def release(self, gid, uid, stake, payout):
        """Settle a parked stake: `payout` goes back into the balance (0 = the stake is lost), the day's gained / lost
        totals get payout - stake. Returns the new total."""
        u = self.user(gid, uid)
        stake, payout = int(stake), int(payout)
        u["aura"] += payout
        u["bj_hold"] = max(0, int(u.get("bj_hold", 0)) - stake)
        self._tally(u, payout - stake)
        self.save()
        self._goat(gid)
        return u["aura"]

    def spend(self, gid, uid, amount):
        """Pay for something (shop, bounty). Takes `amount` and returns True, or returns False and changes nothing when the
        balance doesn't cover it. Unlike add() it isn't noted as 'lost' on the 7 Days leaderboard: buying a thing is not losing."""
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
            return False
        u = self.user(gid, uid)
        if u["aura"] < amount:
            return False
        u["aura"] -= amount
        self.save()
        self._goat(gid)
        return True

    def credit(self, gid, uid, amount):
        """Give reputation back (a refund): not noted as 'gained' on the 7 Days leaderboard and not counted for the family war."""
        u = self.user(gid, uid)
        u["aura"] += int(amount)
        self.save()
        self._goat(gid)
        return u["aura"]

    def items(self, gid, uid):
        """What someone owns from the shop (shield, freezes, boosts, role, title): a dict inside their record, created when missing."""
        u = self.user(gid, uid)
        if not isinstance(u.get("items"), dict):
            u["items"] = {}
        return u["items"]

    def items_of(self, gid, uid):
        """The same, read only: {} for someone without a record (does not create one)."""
        held = self.data.get(str(gid), {}).get(str(uid), {}).get("items")
        return held if isinstance(held, dict) else {}

    def recover_holds(self):
        """After a restart no game is running, so every parked stake goes back to its owner. [(server, user, amount)]."""
        back = []
        for gid, users in self.data.items():
            for uid, u in users.items():
                held = int(u.get("bj_hold", 0) or 0)
                if held > 0:
                    u["aura"] += held
                    u["bj_hold"] = 0
                    back.append((int(gid), int(uid), held))
        if back:
            self.save()
        return back

    def window(self, gid, days=7):
        """Over the last `days` days (today included): {user id: (gained, lost)} for everyone who had any change."""
        first = (self._today() - datetime.timedelta(days=days - 1)).isoformat()
        out = {}
        for uid, u in self.data.get(str(gid), {}).items():
            recent = [v for d, v in u.get("days", {}).items() if d >= first]
            gained, lost = sum(v[0] for v in recent), sum(v[1] for v in recent)
            if gained or lost:
                out[int(uid)] = (gained, lost)
        return out

    def ranking_recent(self, gid, days=7):
        """[(user id, net change)] over the last `days` days, best first."""
        rows = [(uid, gained - lost) for uid, (gained, lost) in self.window(gid, days).items()]
        rows.sort(key=lambda r: (-r[1], r[0]))
        return rows

    def ranking(self, gid):
        """[(user id, reputation)], most reputation first (a tie goes to the lower id)."""
        rows = [(int(uid), u.get("aura", 0)) for uid, u in self.data.get(str(gid), {}).items()]
        rows.sort(key=lambda r: (-r[1], r[0]))
        return rows

    def rank_of(self, gid, uid):
        for place, (who, _total) in enumerate(self.ranking(gid), start=1):
            if who == int(uid):
                return place
        return None


auras = AuraStore(AURA_FILE)
for _gid, _uid, _amount in auras.recover_holds():  # a blackjack game was cut short by a restart: the bet goes back
    log.info("Gave %s reputation back to %s in server %s (blackjack game cut short by a restart)", _amount, _uid, _gid)

def roll_ratio():
    """(True for a W ratio / False for an L ratio, how much reputation). Wins are likelier and bigger than losses,
    so people end up ahead over time instead of bleeding reputation."""
    if random.random() < RATIO_WIN_CHANCE:
        return True, random.randint(1, RATIO_WIN_MAX)
    return False, random.randint(1, RATIO_LOSS_MAX)


METER_SEGMENTS = 10


def meter_bar(percent):
    """A 10 block bar in an inline code box, e.g.  `██████░░░░`  for 64%."""
    filled = max(0, min(METER_SEGMENTS, int(percent / 100 * METER_SEGMENTS + 0.5)))
    return "`" + "\u2588" * filled + "\u2591" * (METER_SEGMENTS - filled) + "`"


# --------------------------------------------------------------------------
# Bot + status page
# --------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # member events (joins, leaves): turn on "Server Members Intent" in the Developer Portal


async def start_status_server():
    async def index(request):
        name = escape(str(bot.user)) if bot.user else "starting"
        status = "online" if bot.is_ready() else "starting"
        page = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Discord Wordfilter Bot</title></head><body "
            "style='font-family:sans-serif;padding:24px'>"
            f"<h1>Discord Wordfilter Bot</h1><p>Status: {status}</p><p>{name}</p>"
            "</body></html>"
        )
        return web.Response(text=page, content_type="text/html")

    async def health(request):
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        log.info("Status page running on port %s", PORT)
    except OSError as e:
        log.error("Could not start status page on port %s: %s", PORT, e)


class WordfilterBot(commands.Bot):
    async def setup_hook(self):
        await start_status_server()
        self.war_task = asyncio.create_task(war_loop())  # closes each daily family war (defined with the family code)
        self.quest_task = asyncio.create_task(quest_save_loop())  # saves quest progress every 5 minutes
        self.role_task = asyncio.create_task(role_check_loop())  # re-checks the roles under Staff / Soul and hands back old bounties
        self.status_task = asyncio.create_task(status_loop())  # tells the status file the bot is alive (offline / back online notices)
        try:  # a host's Stop / Restart sends SIGTERM: close the normal way so the "going offline" notice can be sent first
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, lambda: setattr(self, "_term_task", asyncio.create_task(self.close())))
        except (NotImplementedError, RuntimeError, ValueError, AttributeError):
            pass  # Windows has no add_signal_handler
        try:
            await self.load_extension("matchmaking")
        except Exception:
            log.exception("Could not load matchmaking.py - the rest of the bot keeps running")
        try:  # who invited each new member (invitetracker.py)
            await self.load_extension("invitetracker")
        except Exception:
            log.exception("Could not load invitetracker.py - the rest of the bot keeps running")
        try:  # ,assist - the AI assistant (askai.py)
            await self.load_extension("askai")
        except Exception:
            log.exception("Could not load askai.py - the rest of the bot keeps running")

    async def close(self):
        try:
            await announce_shutdown()  # "going offline" notice (only the first time close() runs)
        except Exception:
            log.exception("Could not send the going-offline notice")
        try:
            quests.save()  # message progress is only written every 30 seconds, so write it once more on the way out
        except Exception:
            log.exception("Could not save the quests on shutdown")
        await super().close()


bot = WordfilterBot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    owner_ids=OWNER_IDS or None,
    activity=discord.Activity(type=discord.ActivityType.listening, name=f"{HELP_PREFIX}help"),
    allowed_mentions=discord.AllowedMentions.none(),
)

try:  # the Bleed look: embeds are sent as clean containers (no side line). CLEAN_EMBEDS=0 in .env turns it off; the bot runs either way
    cleanbox.install()
except Exception:
    log.exception("Could not turn on the clean embed look - the bot keeps using normal embeds")


# --------------------------------------------------------------------------
# Webhooks: ONE per channel, cached, created under a lock
# --------------------------------------------------------------------------
webhook_cache = {}
webhook_locks = {}


def base_channel(channel):
    return channel.parent if isinstance(channel, discord.Thread) else channel


def is_mine(webhook):
    return webhook.user is not None and bot.user is not None and webhook.user.id == bot.user.id


async def get_webhook(channel):
    base = base_channel(channel)
    lock = webhook_locks.setdefault(base.id, asyncio.Lock())
    async with lock:
        cached = webhook_cache.get(base.id)
        if cached is not None:
            return cached
        mine = [w for w in await base.webhooks() if is_mine(w)]
        if mine:
            hook = mine[0]
            for extra in mine[1:]:
                try:
                    await extra.delete(reason="Duplicate relay webhook")
                except discord.HTTPException:
                    pass
        else:
            hook = await base.create_webhook(name="Relay", reason="Wordfilter relay")
        webhook_cache[base.id] = hook
        return hook


def forget_webhook(channel):
    webhook_cache.pop(base_channel(channel).id, None)


async def cleanup_webhooks():
    """On startup: keep one webhook per channel made by this bot, delete the rest."""
    for guild in bot.guilds:
        try:
            hooks = await guild.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            log.warning("No permission to list webhooks in %s (needs Manage Webhooks)", guild.name)
            continue
        by_channel = {}
        for hook in hooks:
            if is_mine(hook) and hook.channel_id:
                by_channel.setdefault(hook.channel_id, []).append(hook)
        for hooks_in_channel in by_channel.values():
            for extra in hooks_in_channel[1:]:
                try:
                    await extra.delete(reason="Duplicate relay webhook")
                    log.info("Deleted duplicate webhook %s", extra.id)
                except discord.HTTPException:
                    pass


# --------------------------------------------------------------------------
# Reposting
# --------------------------------------------------------------------------
recent_reposts = {}


def breaker_tripped(channel_id):
    """Safety net: more than 15 reposts in 10 seconds in one channel -> stop."""
    now = time.monotonic()
    stamps = recent_reposts.setdefault(channel_id, deque())
    while stamps and now - stamps[0] > 10:
        stamps.popleft()
    if len(stamps) >= 15:
        return True
    stamps.append(now)
    return False


def transform_text(text):
    """What the repost says. Right now it is the same text.
    Change this if you want something else, e.g. censor the trigger words."""
    return text


def safe_username(name):
    # Discord rejects webhook names containing these words
    name = re.sub(r"(?i)(discord|clyde)", lambda m: m.group()[0] + "\u200b" + m.group()[1:], name)
    return name[:80] or "user"


async def fetch_reference(message):
    ref = message.reference
    if ref is None or ref.message_id is None:
        return None
    if isinstance(ref.resolved, discord.Message):
        return ref.resolved
    try:
        return await message.channel.fetch_message(ref.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


def build_reply(ref):
    """The grey 'Replying to' embed + the 'jump to message' link button."""
    snippet = ref.content.strip()
    if not snippet:
        if ref.attachments:
            snippet = "[attachment]"
        elif ref.embeds:
            snippet = "[embed]"
        else:
            snippet = "[message]"
    if len(snippet) > 200:
        snippet = snippet[:197] + "..."
    embed = discord.Embed(description=snippet, color=settings.embed_color or GREY)
    embed.set_author(
        name=f"Replying to {ref.author.display_name}",
        icon_url=ref.author.display_avatar.url,
    )
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="\u2191 jump to message", url=ref.jump_url))
    return embed, view


async def collect_files(message):
    """Download attachments BEFORE the original is deleted. Returns (files, skipped)."""
    files, skipped, total = [], 0, 0
    limit = message.guild.filesize_limit
    for attachment in message.attachments[:10]:
        if total + attachment.size > limit:
            skipped += 1
            continue
        try:
            files.append(await attachment.to_file(spoiler=attachment.is_spoiler()))
            total += attachment.size
        except discord.HTTPException:
            skipped += 1
    return files, skipped


async def send_via_webhook(channel, payload, files):
    for _ in range(2):  # second try only if the webhook was deleted
        try:
            hook = await get_webhook(channel)
            for f in files:
                f.reset()
            kwargs = dict(payload)
            if files:
                kwargs["files"] = files
            if isinstance(channel, discord.Thread):
                kwargs["thread"] = channel
            await hook.send(**kwargs)
            return True
        except discord.NotFound:
            forget_webhook(channel)
        except Exception:
            log.exception("Webhook send failed in #%s", getattr(channel, "name", channel))
            return False
    return False


async def repost(message):
    channel = message.channel
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    if isinstance(channel, discord.Thread) and channel.parent is None:
        return
    if breaker_tripped(channel.id):
        log.warning("Too many reposts in #%s, skipping to avoid spam", channel.name)
        return

    # Collect everything first: it is gone once the original is deleted.
    ref = await fetch_reference(message)
    files, skipped = await collect_files(message)
    text = transform_text(message.content)
    note = "\n*(an attachment was too large to repost)*" if skipped else ""
    if goon_locks.is_locked(message.guild.id, message.author.id):
        text = goonify(text, limit=2000 - len(note))  # ,goonlock: the repost carries the extra tags
    text = (text + note)[:2000]

    # Deleting first doubles as a claim: if another copy of the bot already
    # deleted it, we get NotFound and do nothing.
    try:
        await message.delete()
    except discord.NotFound:
        return
    except discord.Forbidden:
        log.error("Missing 'Manage Messages' permission in #%s, cannot repost", channel.name)
        return
    except discord.HTTPException:
        log.exception("Could not delete the original message")
        return

    author = message.author
    payload = dict(
        content=text,
        username=safe_username(author.display_name),
        avatar_url=author.display_avatar.url,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    embed = view = None
    if ref is not None:
        embed, view = build_reply(ref)
        payload["embed"] = embed
        payload["view"] = view

    if await send_via_webhook(channel, payload, files):
        return

    # Last resort so the message is never lost
    try:
        kwargs = {"content": f"**{author.display_name}:** {text}"[:2000]}
        if embed is not None:
            kwargs["embed"] = embed
            kwargs["view"] = view
        await channel.send(**kwargs)
    except discord.HTTPException:
        log.exception("Fallback send failed too")


# --------------------------------------------------------------------------
# ,goonlock: a locked person's messages are swapped (same webhook repost as the wordfilter) for a copy with extra tags
# --------------------------------------------------------------------------
GOONLOCK_FILE = os.path.join(DATA_DIR, "goonlock.json")
_DROPS = "\U0001F4A6"  # the sweat drops emoji
# Every message gets ONE of these at the end. Longer messages get up to GOON_EXTRA_TAGS more after a sentence, never the same tag twice in a
# message. Add, remove or reword freely (keep at least GOON_EXTRA_TAGS + 1 of them).
GOON_TAGS = (
    f"*nuts* {_DROPS}",
    f"*gooning* {_DROPS}",
    f"*nuts harder* {_DROPS}{_DROPS}",
    f"*gooning intensifies* {_DROPS}{_DROPS}",
    f"*goon session in progress* {_DROPS}",
    "*mid-goon* \U0001F975",                            # hot face
    "*edging* \U0001F629",                              # weary face
    "*goon cave activated* \U0001F573️",           # hole
    "*cannot stop gooning* \U0001F635‍\U0001F4AB",  # face with spiral eyes
    "*hand cramp* \U0001FAE0",                          # melting face
)
GOON_EXTRA_TAGS = 2       # most extra tags inside one message (on top of the one at the end)
GOON_EXTRA_CHANCE = 0.5   # chance that a sentence end gets an extra tag
_GOON_BREAKERS = re.compile(r"[*_~`|\[]")   # markdown a tag in the MIDDLE of a message could break: those messages only get the closing tag
_GOON_SENTENCE_END = re.compile(r"(?<!\d)[.!?]+(?=\s)")  # ". " "! " "?? " (not "1. " of a numbered list, not the very end)


class GoonLockStore:
    """Who is goon-locked, saved in goonlock.json: {server id: {user id: unix time it was locked}}."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = {str(g): {str(u): t for u, t in users.items()} for g, users in loaded.items() if isinstance(users, dict)}
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("Could not read %s, nobody is goon-locked", path)

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def is_locked(self, gid, uid):
        users = self.data.get(str(gid))
        return bool(users) and str(uid) in users

    def lock(self, gid, uid):
        self.data.setdefault(str(gid), {})[str(uid)] = int(time.time())
        self._save()

    def unlock(self, gid, uid):
        """Free someone. True if they were locked."""
        users = self.data.get(str(gid), {})
        was = users.pop(str(uid), None) is not None
        if not users:
            self.data.pop(str(gid), None)
        if was:
            self._save()
        return was

    def locked(self, gid):
        """The user ids locked in this server, oldest lock first."""
        users = self.data.get(str(gid), {})
        return [int(u) for u, _t in sorted(users.items(), key=lambda item: item[1])]


goon_locks = GoonLockStore(GOONLOCK_FILE)


def goonify(text, limit=2000):
    """What a goon-locked person's message becomes: their own words with a tag at the end and, in longer plain-text messages, one or two
    more after a sentence. Messages with markdown (bold, code, spoilers, links with a label) only get the closing tag so nothing breaks.
    Never longer than `limit`: a message that is too long is cut so the closing tag always fits."""
    body = text.strip()
    tags = random.sample(GOON_TAGS, GOON_EXTRA_TAGS + 1)
    closing = tags.pop()
    if not body:
        return closing  # a picture or file with no text: just the tag
    body = body[:max(0, limit - len(closing) - 1 - 4)].rstrip()  # 1 = the space before the tag, 4 = room to close a code block
    closer = "\n```" if body.count("```") % 2 else ""  # an unclosed code block would swallow the tag
    spare = limit - len(body) - len(closer) - 1 - len(closing)
    if _GOON_BREAKERS.search(body) is None:
        ends = [m.end() for m in _GOON_SENTENCE_END.finditer(body) if random.random() < GOON_EXTRA_CHANCE]
        picked = sorted(random.sample(ends, min(len(ends), GOON_EXTRA_TAGS)), reverse=True)  # from the back, so the earlier spots keep their place
        for pos, tag in zip(picked, tags):
            if len(tag) + 1 <= spare:
                body = body[:pos] + " " + tag + body[pos:]
                spare -= len(tag) + 1
    sep = "\n" if closer or body.endswith("```") else " "
    return f"{body}{closer}{sep}{closing}"


def goon_applies(message):
    """True when this message should be swapped for a goon-locked copy: its author is locked and there is something to copy.
    Things a webhook can't copy (stickers, polls, voice messages, forwards) and files too big to copy are left alone, so the lock
    never makes anything disappear."""
    if not goon_locks.is_locked(message.guild.id, message.author.id):
        return False
    if message.stickers or getattr(message, "poll", None) or getattr(message, "message_snapshots", None):
        return False
    if getattr(message.flags, "voice", False):
        return False
    if message.attachments and (len(message.attachments) > 10 or sum(a.size for a in message.attachments) > message.guild.filesize_limit):
        return False
    return bool(message.content.strip() or message.attachments)


# --------------------------------------------------------------------------
# Offline / back online notices
# --------------------------------------------------------------------------
# A bot that is switched off can't talk, so the notices work in two steps:
#   * When the bot is stopped the normal way (host Stop / Restart = SIGTERM, Ctrl+C), it posts "going offline" first.
#   * When it starts again it posts "back online". This is also the only message after a crash or a kill for high CPU,
#     because a killed bot gets no chance to say anything.
# Both go to the ONE channel set in STATUS_CHANNEL_ID (the default is the owner's channel below; put another id in .env to move
# it). If you empty it (STATUS_CHANNEL_ID=) they go to the channel where a command was last used in each server instead (only
# servers with a command in the last STATUS_NOTICE_HOURS before the bot went down). The only work it does is writing a tiny
# status.json once a minute. Turn it all off with STATUS_NOTICES=0 in .env.
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
STATUS_NOTICES = os.getenv("STATUS_NOTICES", "1").strip().lower() not in ("0", "false", "no", "off")
_status_channel = os.getenv("STATUS_CHANNEL_ID", "1563262719124050021").strip()
STATUS_CHANNEL_ID = int(_status_channel) if _status_channel.isdigit() else 0   # the channel for both notices (0 = last used channel of each server)
STATUS_BEAT_SECONDS = 60                                              # how often "I'm alive" is written to status.json
STATUS_NOTICE_HOURS = float(os.getenv("STATUS_NOTICE_HOURS", "24"))   # a server gets notices if a command was used in the last N hours
STATUS_MIN_OFFLINE = int(os.getenv("STATUS_MIN_OFFLINE", "15"))       # seconds: a faster restart sends no "back online" notice
STATUS_NOTICE_GAP = int(os.getenv("STATUS_NOTICE_GAP", "300"))        # seconds: at most one notice of each kind per 5 minutes (no spam in a crash loop)
STATUS_OFFLINE_TEXT = ("The owner is making updates, or the bot hit high CPU usage. "
                       "I'll send a message here as soon as I'm back.")
STATUS_BACK_TEXT = ("I was offline because the owner was making updates or because of high CPU usage. "
                    "The bot is back to work, you can use it now.")


class StatusStore:
    """status.json: when the bot was last alive, whether it was stopped on purpose, and the last channel used in each server."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            if not isinstance(self.data, dict):
                self.data = {}
        except FileNotFoundError:
            self.data = {}
        except Exception:
            log.exception("Could not read %s, starting without it", self.path)
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)


status = StatusStore(STATUS_FILE)
# What the PREVIOUS run left behind, copied now so commands used during start-up can't change it.
PREVIOUS_STATUS = json.loads(json.dumps(status.data))
_shutdown_announced = False


def note_activity(ctx):
    """Remember the channel a command was used in (memory only; status_loop writes it to disk)."""
    if ctx.guild is None or ctx.channel is None:
        return
    status.data.setdefault("channels", {})[str(ctx.guild.id)] = [ctx.channel.id, int(time.time())]


def notice_embed(title, text, small=None):
    """Plain neutral card: a bold line, one sentence, an optional small grey line."""
    lines = [f"**{title}**", text]
    if small:
        lines.append(f"-# {small}")
    return discord.Embed(description="\n".join(lines), color=theme())


def notice_targets(channels, since):
    """The channel ids that get a notice: STATUS_CHANNEL_ID when it is set, else the last used channel of every server
    whose last command was used after `since` (a unix time)."""
    if STATUS_CHANNEL_ID:
        return [STATUS_CHANNEL_ID]
    found = []
    for item in (channels or {}).values():
        try:
            channel_id, at = int(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if at >= since:
            found.append(channel_id)
    return found


async def send_notice(channel_id, embed):
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("Status notice: I can't see channel %s (wrong id, or I'm not in that server)", channel_id)
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        log.warning("Could not send the status notice in %s", channel_id, exc_info=True)


async def announce_back():
    """Start-up: tell the servers that used the bot before it went down that it is back."""
    prev = PREVIOUS_STATUS
    now = time.time()
    alive = prev.get("alive")
    if not STATUS_NOTICES:
        return
    small = None
    if isinstance(alive, (int, float)):
        offline_for = now - alive
        if offline_for < STATUS_MIN_OFFLINE:
            return  # a very quick restart: nothing to say
        shown = round(offline_for / 60) * 60 if offline_for >= 90 else round(offline_for / 5) * 5  # "about 5m", not "5m 1s"
        small = f"Offline for about {human_time(shown)}" if offline_for < 7 * 86400 else None
    elif not STATUS_CHANNEL_ID:
        return  # first start ever and no fixed channel: we don't know who used the bot
    # (with a fixed channel the very first start also says "back online", so you can see that the notices work)
    if now - float(prev.get("back_at") or 0) < STATUS_NOTICE_GAP:
        return
    status.data["back_at"] = now
    since = (alive if isinstance(alive, (int, float)) else now) - STATUS_NOTICE_HOURS * 3600
    targets = notice_targets(prev.get("channels"), since)
    for channel_id in targets:
        await send_notice(channel_id, notice_embed("Bot is back online", STATUS_BACK_TEXT, small))
        await asyncio.sleep(0.4)  # stay well inside Discord's rate limits
    log.info("Sent the back-online notice to %s channel(s)", len(targets))


async def announce_shutdown():
    """Called by close(): say we are going offline, then mark this stop as on purpose in status.json."""
    global _shutdown_announced
    if _shutdown_announced:
        return
    _shutdown_announced = True
    now = time.time()
    try:
        if STATUS_NOTICES and bot.is_ready() and now - float(status.data.get("offline_at") or 0) >= STATUS_NOTICE_GAP:
            status.data["offline_at"] = now
            targets = notice_targets(status.data.get("channels"), now - STATUS_NOTICE_HOURS * 3600)
            embed = notice_embed("Bot is going offline", STATUS_OFFLINE_TEXT)
            if targets:  # all servers at once and never longer than 6 seconds: a host only waits a few seconds after SIGTERM
                await asyncio.wait_for(
                    asyncio.gather(*(send_notice(c, embed) for c in targets), return_exceptions=True), timeout=6)
    except Exception:
        log.exception("The going-offline notice failed")
    status.data["clean"] = True
    status.data["alive"] = time.time()
    try:
        status.save()
    except OSError:
        log.exception("Could not write %s", STATUS_FILE)


async def status_loop():
    """Once a minute: write 'alive now' (and the last used channels) to status.json. Tiny file, almost no CPU."""
    await bot.wait_until_ready()
    while not bot.is_closed() and not _shutdown_announced:
        status.data["alive"] = time.time()
        status.data["clean"] = False
        try:
            status.save()
        except OSError:
            log.exception("Could not write %s", STATUS_FILE)
        await asyncio.sleep(STATUS_BEAT_SECONDS)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------
_ready_once = False


@bot.event
async def on_ready():
    global _ready_once
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    if _ready_once:
        return
    _ready_once = True
    try:  # register the slash commands (/match ...) with Discord; no manual sync needed
        synced = await bot.tree.sync()
        log.info("Synced %s slash commands", len(synced))
    except Exception:
        log.exception("Could not sync slash commands")
    asyncio.create_task(announce_back())  # "Bot is back online" in the servers that used it before the stop
    await cleanup_webhooks()
    for guild in bot.guilds:
        schedule_best_role(guild)
        schedule_goat_role(guild.id, delay=5)
        schedule_family_sync(guild)  # one-time clean-up of the old family roles and [TAG] nicknames


@bot.event
async def on_message(message):
    # Never read bots or webhooks. This is what stops loops and spam.
    if message.author.bot or message.webhook_id is not None:
        return
    if message.guild is None:
        return
    if message.type not in (discord.MessageType.default, discord.MessageType.reply):
        return
    if not handled.claim(message.id):
        return

    quest_message(message)  # counts toward the message quests (not commands, spaced out so spam doesn't count)

    try:
        ctx = await bot.get_context(message)
        if ctx.valid:
            root = ctx.command.root_parent or ctx.command
            # +prefix also answers to an @mention, so a bad prefix change can never lock you out
            mentioned = root.name == "prefix" and ctx.prefix.strip().startswith("<@")
            if ctx.prefix == prefix_for(root.name) or mentioned:
                await bot.invoke(ctx)  # commands are invoked exactly once, here
                return
            # right command but wrong prefix (like "+puh"): treat as a normal message
        if store.match(message.guild.id, message.content) or goon_applies(message):
            await repost(message)
    except Exception:
        log.exception("Error while handling a message")


# --------------------------------------------------------------------------
# Command errors: one plain line with the user's mention (red bar for real errors)
# --------------------------------------------------------------------------
PERMISSION_NAMES = {"manage_guild": "Manage Server", "administrator": "Administrator"}

class _NoTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def typing_for(ctx):
    """'Typing...' for prefix commands. Slash commands are already deferred (see defer_slash), and
    ctx.typing() would try to defer a second time and fail, so they get a no-op instead."""
    return _NoTyping() if ctx.interaction is not None else ctx.typing()


@bot.before_invoke
async def defer_slash(ctx):
    """Slash commands must answer within 3 seconds; this gives every bot.py command more time.
    Matchmaking commands (a cog) answer privately themselves, so they are left alone."""
    note_activity(ctx)  # the channel for the offline / back online notices
    if ctx.interaction is not None and ctx.cog is None and not ctx.interaction.response.is_done():
        try:
            await ctx.defer()
        except discord.HTTPException:
            pass


@bot.event
async def on_command_error(ctx, error):
    error = getattr(error, "original", error)
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        names = [PERMISSION_NAMES.get(p, p.replace("_", " ").title()) for p in error.missing_permissions]
        embed = status_embed(ctx, "warn", f"You're missing the **{', '.join(names)}** permission.")
    elif isinstance(error, commands.MissingRequiredAttachment):
        embed = status_embed(ctx, "warn", "Attach one or more pictures to your message.")
    elif isinstance(error, commands.MissingRequiredArgument):
        entry = next((e for e in HELP_COMMANDS if e[1] == ctx.command.qualified_name), None)
        hint = f" Example: `{help_example(entry)}`" if entry else ""
        embed = status_embed(ctx, "warn", f"You're missing the **{error.param.name}** argument.{hint}")
    elif isinstance(error, commands.CommandOnCooldown):
        embed = status_embed(ctx, "cooldown", f"Slow down, try again in **{human_time(error.retry_after)}**.")
    elif isinstance(error, commands.BadArgument):
        embed = status_embed(ctx, "warn", "I couldn't find that member.")
    elif isinstance(error, commands.NotOwner):
        embed = status_embed(ctx, "warn", "Only the bot owner can use this.")
    elif isinstance(error, commands.NoPrivateMessage):
        embed = status_embed(ctx, "warn", "This command only works in a server.")
    elif isinstance(error, commands.CheckFailure) and str(error):
        embed = status_embed(ctx, "warn", str(error))
    else:
        log.error("Command error", exc_info=error)
        embed = status_embed(ctx, "err", "Something went wrong. Check the bot console.")
    try:
        if ctx.command is not None and ctx.command.qualified_name.split(" ")[0] in ("wordfilter", "wf"):
            # .wordfilter errors are plain replies too (same text, no embed, no ping)
            await ctx.reply((embed.description or "").replace(f"{ctx.author.mention}: ", "", 1), allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send(embed=embed)
    except discord.HTTPException:
        pass


# --------------------------------------------------------------------------
# Pager: ‹ › buttons, a goto button and a ✕ under multi-page embeds
# --------------------------------------------------------------------------
class JumpModal(discord.ui.Modal):
    def __init__(self, pager):
        super().__init__(title="Go to page")
        self.pager = pager
        self.number = discord.ui.TextInput(placeholder=f"1 to {pager.total}", max_length=6)
        self.add_item(discord.ui.Label(text="Page number", component=self.number))

    async def on_submit(self, interaction):
        text = self.number.value.strip()
        if not text.isdigit() or not 1 <= int(text) <= self.pager.total:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", f"Pick a page from **1** to **{self.pager.total}**."),
                ephemeral=True,
            )
            return
        await self.pager.go(interaction, int(text) - 1)


class Pager(discord.ui.View):
    """Page buttons under an embed. Only the person who opened it can press them."""

    def __init__(self, owner_id, build, total, start=0):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.build = build  # build(index) -> the embed for that page
        self.total = total
        self.index = start
        self.message = None
        # icons from the clean icon pack: grey arrows, the page-jump icon and a white cross on the red close button
        # (the text stays on any button whose icon is missing: ‹ › goto ✕)
        style_buttons(self, {"\u2039": "left", "\u203a": "right", "goto": "jump", "\u2715": "cross"},
                      icon_only=("\u2039", "\u203a", "goto", "\u2715"))
        for item in self.children:  # a coloured cross on a red button is invisible, so without the white cross the close button turns grey
            if isinstance(item, discord.ui.Button) and item.style == discord.ButtonStyle.danger and item.emoji is not None \
                    and getattr(item.emoji, "name", "").lower() != "bl_cross_w":
                item.style = discord.ButtonStyle.secondary

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", "This menu isn't yours. Run the command yourself."),
                ephemeral=True,
            )
            return False
        return True

    async def go(self, interaction, index):
        self.index = index % self.total
        await interaction.response.edit_message(embed=self.build(self.index), view=self)

    @discord.ui.button(label="‹", style=discord.ButtonStyle.primary)
    async def previous(self, interaction, button):
        await self.go(interaction, self.index - 1)

    @discord.ui.button(label="›", style=discord.ButtonStyle.primary)
    async def following(self, interaction, button):
        await self.go(interaction, self.index + 1)

    @discord.ui.button(label="goto", style=discord.ButtonStyle.secondary)
    async def jump(self, interaction, button):
        await interaction.response.send_modal(JumpModal(self))

    @discord.ui.button(label="✕", style=discord.ButtonStyle.danger)
    async def close(self, interaction, button):
        await interaction.response.defer()
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass
        self.stop()

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# --------------------------------------------------------------------------
# Help: +help (overview with a dropdown) and +help <command> (one page per command)
# --------------------------------------------------------------------------
HELP_CATEGORIES = {
    "info": ("Info", "Help, invite link and bot stats"),
    "fun": ("Fun", "Meters, reputation and join games"),
    "family": ("Family", "Families, roles and the leaderboard"),
    "matchmaking": ("Matchmaking", "Find a friend, profiles and staff tools"),
    "server": ("Server", "Wordfilter, staff list and bot setup"),
}
HELP_GROUPS = {"wordfilter", "fam", "match", "reputation", "stafflist", "sop", "shop", "myrole", "title", "roles", "goonlock"}  # shown as "Group Command", with the subcommands on the next pages

# (category, name, args, description, permission, aliases)
HELP_COMMANDS = [
    ("wordfilter", "wordfilter", "", "Manage the trigger words. A message that contains one is reposted by the bot.",
     "Manage Server", ["wf"]),
    ("wordfilter", "wordfilter add", "<word>", "Add a trigger word to this server.",
     "Manage Server", ["wf add"]),
    ("wordfilter", "wordfilter remove", "<word>", "Remove a trigger word.",
     "Manage Server", ["wf remove", "wordfilter delete", "wf delete"]),
    ("wordfilter", "wordfilter defaults", "", "Add the built-in starter words (every new server gets them automatically).",
     "Manage Server", ["wf defaults"]),
    ("wordfilter", "wordfilter list", "", "Show every trigger word in this server.",
     "Manage Server", ["wf list"]),
    ("fun", "puh", "[member]", "Random tight or loose meter, with a percentage for each.",
     "Everyone", []),
    ("fun", "racist", "[member]", "Random racism meter: the percentage first, then the verdict (racist above 50%, peaceful at 50% or below).",
     "Everyone", []),
    ("fun", "ratio", "", f"Random W ratio (you earn a random amount of reputation) or L ratio (you lose a smaller amount, and only 30% of it once you're already below zero). Wins are more likely than losses. Saved to your reputation, and the result shows your new balance. Cooldown: {RATIO_COOLDOWN // 60} minutes.",
     "Everyone", []),
    ("fun", "bottle", "", f"Spin the bottle: the bot picks 3 random active members (people who wrote in this channel in the last {BOTTLE_ACTIVE_MINUTES} minutes). It skips the people from your last few bottles. Press Pick and a form opens where you choose who you kiss, slap and marry (picking the same person twice gives you a warning). The result is then posted as a new message and the first one is deleted. Just for fun, no reputation changes. Cooldown: {BOTTLE_COOLDOWN} seconds.",
     "Everyone", []),
    ("fun", "reputation", "[member]", "Show someone's reputation and their rank in this server.", "Everyone", ["rep"]),
    ("fun", "reputation top", "", "The reputation leaderboard (10 per page), with page arrows, My Stats and All Time / 7 Days buttons.", "Everyone", ["rep top", "rep leaderboard", "rep lb", "reputation leaderboard", "reputation lb"]),
    ("fun", "reputation give", "<member> <amount>", "Give some of your own reputation to someone in YOUR family (family members only). Any whole number up to what you have (37, 12345...), or 2k, 1m, half, all. Cooldown: 10 seconds.", "Everyone", ["rep give"]),
    ("fun", "daily", "", f"Claim free reputation once a day ({DAILY_AURA:,} to start). Claiming every day builds a streak with a bigger bonus. A Streak Freeze from the shop saves the streak when you miss a day.",
     "Everyone", []),
    ("fun", "duel", "<member> <amount>", f"Bet reputation on a coin flip. They have to accept and the winner takes it all. Amount can be 50 or all. With Duel Insurance from the shop, a lost duel only moves half of the bet (bets up to {BOOST_MAX_BET:,}; the other player is told before they accept).",
     "Everyone", []),
    ("fun", "coinflip", "<amount> [heads|tails]", f"Bet reputation on a coin flip against the bot. Win and you get your bet again on top, lose and the bet is gone. Bets go from {GAMBLE_MIN_BET:,} to {GAMBLE_MAX_BET:,}, amount can be 50, 2k, half or all. You can't bet into debt. Cooldown: {GAMBLE_COOLDOWN:g}s.",
     "Everyone", ["cf", "flip"]),
    ("fun", "slots", "<amount>", f"Spin three reels. Three matching pay 3x to 40x your bet (three sevens is the jackpot), any two matching pay {SLOTS_PAIR_PAYOUT:g}x, no match loses the bet. Same bet limits and cooldown as coinflip.",
     "Everyone", ["slot"]),
    ("fun", "plinko", "[amount] [low|medium|high]", f"Opens a plinko card with Set Bet, Low, Medium, High and Start buttons (the amount and risk are optional, you can change them on the card). Start drops a ball down an 8 row plinko board and shows it falling. It lands in a slot that pays a multiplier of your bet: low risk pays 0.4x to 2.5x and wins often, medium pays 0.4x to 11x, high pays 0.25x to 26x and mostly loses. Medium if you leave the risk out. Same bet limits and cooldown as coinflip. A Luck Boost and Half Loss from the shop work here (bets up to {BOOST_MAX_BET:,}), but a Luck Boost never lands the ball in a slot that pays more than {gambling.PLINKO_LUCK_MAX_MULT}x.",
     "Everyone", []),
    ("fun", "blackjack", "<amount>", f"Play blackjack against the bot: Hit, Stand or Double down and get closer to 21 than the dealer without going over. Blackjack pays {gambling.BLACKJACK_PAYS[0]}:{gambling.BLACKJACK_PAYS[1]}, a win pays 1:1, a tie gives your bet back, the dealer stands on 17. Same bet limits and cooldown as coinflip, one game at a time.",
     "Everyone", ["bj"]),
    ("fun", "rob", "<member>", f"Try to rob another member (not someone in your family). Get away and you take {int(ROB_STEAL[0] * 100)}-{int(ROB_STEAL[1] * 100)}% of their reputation ({ROB_CHANCE * 100:g}% chance), get caught and you pay them that much instead. It only moves reputation between the two of you. The member needs at least {ROB_MIN_BALANCE:,} reputation. A fine can put you in debt, down to -{ROB_DEBT_LIMIT:,} at most (then you can't rob until you're back above it). A robbed member is safe for {human_time(ROB_IMMUNITY)}, and so is anyone with a Rob Shield (if you rob while holding one, yours ends). A successful rob also collects every bounty on that member. A Lockpick from the shop makes your next rob work {round(LOCKPICK_CHANCE * 100)}% of the time, and a Vault keeps {VAULT_PERCENT}% of its owner's reputation out of reach. Cooldown: {human_time(ROB_COOLDOWN)}.",
     "Everyone", []),
    ("fun", "quests", "[claim]", "Your daily and weekly quests. Finish them (send messages, ratios, duels, daily, join games) and press Claim rewards, or use quests claim, to get reputation. A Quest Boost from the shop doubles your next claim, and a Quest Reroll swaps an unfinished quest (quests reroll <number>).",
     "Everyone", ["quest"]),
    ("fun", "shop", "", "The reputation shop, in categories: Upgrades (Streak Freeze, Quest Boost, Daily Boost, Quest Reroll), Rob and protection (Rob Shield, Lockpick, Vault), Gambling (Luck Boost, Half Loss, Duel Insurance) and Customization (Custom Role, Animated Title, Leaderboard Badge). Switch category with the left and right arrows at the top of the shop. Every item has a Buy button right next to it; once you own the Custom Role or the Animated Title it becomes Edit and opens a form: role name, color style (solid, gradient or holographic), hex codes and any emoji as the icon; title name, style and color.",
     "Everyone", []),
    ("fun", "shop buy", "<item> [amount]", f"Buy straight away: shield ({SHIELD_PRICE:,}), freeze ({FREEZE_PRICE:,}), boost ({BOOST_PRICE:,}), dailyboost ({DAILYBOOST_PRICE:,}), reroll ({REROLL_PRICE:,}), lockpick ({LOCKPICK_PRICE:,}), vault ({VAULT_PRICE:,}), luck ({LUCK_PRICE:,} each), halfloss ({HALF_PRICE:,} each), duelins ({DUELINS_PRICE:,} each), role ({ROLE_PRICE:,}). Luck, halfloss and duelins take an amount to buy several at once, like shop buy luck 3. Titles are made with title set and badges with badge.",
     "Everyone", []),
    ("fun", "inventory", "", "What you own from the shop: shield time left, Streak Freezes, Quest Boosts, the games left on your Luck Boost and Half Loss, your custom role, your title and any bounty on you.",
     "Everyone", ["inv", "items"]),
    ("fun", "myrole", "", "Show your custom role (bought in the shop). It sits right under the Soul role, and you edit it yourself like a booster role.",
     "Everyone", []),
    ("fun", "myrole name", "<text>", f"Rename your custom role (up to {CUSTOM_ROLE_NAME_MAX} characters, no staff-like names or links). Cooldown: 10 seconds.", "Everyone", []),
    ("fun", "myrole color", "<hex>", "Change the color of your custom role, with a hex like #ff00aa or a word like pink.", "Everyone", ["myrole colour"]),
    ("fun", "myrole icon", "<emoji or picture>", "Give your custom role an icon (picture up to 256 KB, or an emoji). The server needs Boost level 2. Write remove to take it off.", "Everyone", []),
    ("fun", "myrole delete", "", "Delete your custom role. There is no refund.", "Everyone", []),
    ("fun", "title", "[member]", "Show someone's animated title. A title is any short text with an animated effect, shown as a name plate on their ,rep card.",
     "Everyone", []),
    ("fun", "title set", "<effect> <text>", f"Write your title (up to {titlecard.MAX_CHARS} characters). Effects: {', '.join(titlecard.EFFECTS)}. The first title costs {TITLE_PRICE:,} reputation, changing it later {TITLE_CHANGE_PRICE:,}. Leave the effect out for rainbow.",
     "Everyone", []),
    ("fun", "title color", "<color>", f"Change the color of a flow or neon title (a hex like #ff00aa or a word like pink). Costs {TITLE_CHANGE_PRICE:,}.", "Everyone", []),
    ("fun", "title remove", "", "Remove your title. There is no refund.", "Everyone", []),
    ("fun", "title reset", "<member>", "Take someone's title away (no refund). For titles that shouldn't be there.", "Manage Server", []),
    ("fun", "bounty", "[member] [amount]", f"Without anything it lists the bounties. With a member it shows theirs. With an amount (at least {BOUNTY_MIN:,}) you put that reputation on their head, plus a {BOUNTY_FEE_PERCENT}% fee. Whoever robs them successfully collects it. A bounty nobody collects goes back to you after {BOUNTY_DAYS} days (without the fee).",
     "Everyone", []),
    ("fun", "badge", "<emoji or short text>", f"Set your leaderboard badge: an emoji or a short tag (up to {BADGE_MAX_CHARS} letters) shown after your name on the leaderboards. The first one costs {BADGE_PRICE:,} reputation, changing it {BADGE_CHANGE_PRICE:,}. Also in the shop under Customization.",
     "Everyone", []),
    ("fun", "wordstats", "<word>", "How many times you said a word in the channel you use it in, and how many times everyone said it there. Whole words only (moha does not count mohamed) and upper / lower case is the same. It counts the messages the bot has saved (the ,assistbackfill command imports older ones); deleted messages and commands are not counted. Cooldown: 5 seconds.",
     "Everyone", []),
    ("fun", "collect", "", f"Staff and server boosters collect {COLLECT_AMOUNT:,} reputation. Cooldown: {human_time(COLLECT_COOLDOWN)}.",
     "Staff / Boosters", []),
    ("fun", "sop", "", f"Smash or pass: posts one picture (the server's list, an adult anime character or an adult public figure) with Smash and Pass buttons. Anyone can press, and each press posts an embed pinging them with how many smashes or passes they have. Cooldown: {human_time(SOP_COOLDOWN_SECONDS)} per person. Never uses server members.",
     "Everyone", ["smashorpass", "smashpass"]),
    ("fun", "sop vote", "", "The long version: 5 pictures in a row. Each is open for 30 seconds with Smash and Pass buttons, then a new embed shows who smashed and who passed and 3 seconds later the next one comes. After the 5th it stops.",
     "Everyone", []),
    ("fun", "sop upload", "[name]", "Add pictures by attaching them to the message (up to 10 at a time, they are saved). Adults (18+) only.", "Manage Server", ["sop addpic"]),
    ("fun", "sop add", "<picture link> <name>", "Add someone with a direct link to a picture (.jpg .png .webp .gif). Adults (18+) only.", "Staff / Manage Server", []),
    ("fun", "sop remove", "<name>", "Remove someone from the list.", "Manage Server", []),
    ("fun", "sop list", "", "Show everyone on the list.", "Everyone", []),
    ("fun", "sop source", "[enable|disable] [people|anime|uploaded|all]", "Turn each picture source on or off: `people` (real people irl, random), `anime` (characters, random) and `uploaded` (this server's own pictures added with sop upload / sop add). Without arguments it shows which are on. `all` and `random` (people + anime) work too, and `anime off` is the same as `disable anime`. Anime characters need a stated age of 18+. Real people are mostly a featured list (Trump, Diddy, footballers, Tom Holland, Sabrina Carpenter...) plus some young actors, singers and models (18 to 35). Anime is about 90% of the random pictures.", "Server owner / Manage Server", []),
    ("fun", "sop block", "", "Never show the last random picture again in this server (for example a character who looks too young).", "Manage Server", []),
    ("fun", "gayest", "", f"Opens a {GAME_SECONDS} second join window, then picks one person at random.",
     "Everyone", []),
    ("fun", "tightest", "", f"Opens a {GAME_SECONDS} second join window, then picks one person at random.",
     "Everyone", []),
    ("fun", "goonlock", "", "Goon-lock people. The bot reposts everything a locked person says (as them) with a tag like *nuts* \U0001F4A6 added. Stickers, polls, voice messages and files too big to copy are left alone.",
     "Staff / Manage Messages", []),
    ("fun", "goonlock add", "<member>", "Put the lock on a member. Every message they send is reposted as them with a tag like *nuts* \U0001F4A6 at the end, and one or two more inside longer messages.",
     "Staff / Manage Messages", []),
    ("fun", "goonlock remove", "<member>", "Take the lock off a member. Their messages are left alone again.",
     "Staff / Manage Messages", ["goonlock delete"]),
    ("fun", "goonlock list", "", "Show who is goon-locked in this server.",
     "Staff / Manage Messages", []),
    ("family", "fam", "", "Start or join a family, manage it, kidnap members and climb the leaderboard.",
     "Everyone", []),
    ("family", "fam create", "<name>", "Start a family. Nicknames are never changed.",
     "Everyone", []),
    ("family", "fam invite", "<member>", "Invite someone. They get Accept and Decline buttons.",
     "Family admin", []),
    ("family", "fam info", "[member or family]", "Show a family: owner, rank, members and banner.",
     "Everyone", ["fam profile"]),
    ("family", "fam promote", "<member>", "Make a member an admin.", "Family owner", []),
    ("family", "fam demote", "<member>", "Make an admin a regular member.", "Family owner", []),
    ("family", "fam kick", "<member>", "Remove someone ranked below you.", "Family admin", []),
    ("family", "fam rename", "<name>", "Rename the family (2-20 characters).", "Family owner", []),
    ("family", "fam description", "<text>", "Set the description (up to 200 characters). Use clear to remove it.",
     "Family admin", ["fam desc"]),
    ("family", "fam banner", "", "Set the banner from an attached image, or use remove to clear it.",
     "Family admin", []),
    ("family", "fam transfer", "<member>", "Give the family to another member. You become an admin.",
     "Family owner", []),
    ("family", "fam leave", "", "Leave your family. Owners must transfer it or delete it first.",
     "Family member", []),
    ("family", "fam delete", "", "Delete the family.", "Family owner", []),
    ("family", "fam war", "", f"Today's family war: families score from their members' reputation. The top family's members each get {WAR_BONUS:,} reputation when the day ends (00:00 UTC).",
     "Everyone", []),
    ("family", "fam war channel", "[#channel|off]", "Choose where each day's winner is announced.", "Manage Server", []),
    ("family", "fam kidnap", "<member>", "Try to take a member from another family. It can fail, has a cooldown, and owners are safe.",
     "Family member", []),
    ("family", "fam rep", "", "Show how much reputation your family's bank holds and your family's rank.", "Family member", ["fam bank"]),
    ("family", "fam deposit", "<amount>", "Put some of your reputation into your family's bank (37, 2k, half, all...). The family leaderboard ranks families by what their bank holds.", "Family member", ["fam dep"]),
    ("family", "fam withdraw", "<amount>", "Take reputation out of the family bank into your own balance.", "Family admin", ["fam with"]),
    ("family", "fam top", "", "The 10 families with the most reputation in their bank (more members wins a tie).", "Everyone",
     ["fam leaderboard", "fam lb"]),
    ("family", "fam baserole", "[@role]", "The Best family role sits right under this role. Without one it sits under the Staff role. Use clear to unset it.",
     "Administrator", []),
    ("family", "fam admin_add", "<member> [family]", "Put someone in a family, whatever they were doing.",
     "Manage Server", []),
    ("family", "fam admin_remove", "<member>", "Take someone out of their family.", "Manage Server", []),
    ("family", "fam purge", "<family>", "Delete a family by name.", "Manage Server", []),
    ("matchmaking", "match", "", "Find a friend to chat with anonymously. Romantic matches are for 18+ only. Chats are logged for safety.",
     "Everyone", ["mm"]),
    ("matchmaking", "match profile", "", "Create or edit your profile with the buttons: About You, Your Vibes, Your Match.",
     "Everyone", ["match create", "match edit"]),
    ("matchmaking", "match me", "", "See your own profile card.", "Everyone", []),
    ("matchmaking", "match find", "[quick|deep]", "Look for a friend. Quick is fast, deep also compares interests. You get a DM when matched.",
     "Everyone", ["match partner", "match start"]),
    ("matchmaking", "match leave", "", "Stop searching.", "Everyone", []),
    ("matchmaking", "match end", "", "Leave your current match (it asks first).", "Everyone", ["match cancel"]),
    ("matchmaking", "match block", "", "End the chat and never match with them again.", "Everyone", []),
    ("matchmaking", "match unblockall", "", "Clear your block list.", "Everyone", []),
    ("matchmaking", "match reveal", "", "Ask your match to swap Discord usernames. They have to accept, and you both must be 18 or older.", "Everyone", []),
    ("matchmaking", "match icebreaker", "", "Send a conversation starter to your chat.", "Everyone", []),
    ("matchmaking", "match stats", "", "Your matches, profile likes and streak.", "Everyone", []),
    ("matchmaking", "match delete", "", "Delete your profile.", "Everyone", []),
    ("matchmaking", "match panel", "<#channel>", "Post the sticky sign-up panel in a channel.", "Manage Server", []),
    ("matchmaking", "match unpanel", "<#channel>", "Remove the sticky sign-up panel from a channel.", "Manage Server", []),
    ("matchmaking", "match server", "[on|off]", "Turn matchmaking on or off in this server.", "Manage Server", []),
    ("matchmaking", "match reports", "[id]", "View matchmaking reports.", "Matchmaking staff", []),
    ("matchmaking", "match resolve", "<id> [dismissed]", "Mark a report reviewed or dismissed.", "Matchmaking staff", []),
    ("matchmaking", "match ban", "<user> [reason]", "Ban a user from matchmaking everywhere.", "Matchmaking staff", []),
    ("matchmaking", "match unban", "<user>", "Unban a user from matchmaking.", "Matchmaking staff", []),
    ("matchmaking", "match staff", "", "Show the matchmaking staff safety tools.", "Matchmaking staff", []),
    ("matchmaking", "match staff chats", "", "List every chat running right now and who is searching.", "Matchmaking staff", []),
    ("matchmaking", "match staff chat", "<user> [partner] [count]", "Read a user's chat, live or their latest.", "Matchmaking staff", []),
    ("matchmaking", "match staff log", "[user|all] [count]", "Everything that happened: matches, messages, reports, bans.", "Matchmaking staff", []),
    ("matchmaking", "match staff profile", "<user>", "Open a full profile (Discord id, age, preferences, reports).", "Matchmaking staff", []),
    ("matchmaking", "match staff stop", "<user> [reason]", "End a user's chat right now.", "Matchmaking staff", []),
    ("matchmaking", "match staff list", "", "Show who is matchmaking staff.", "Matchmaking staff", []),
    ("matchmaking", "match staff add", "<user>", "Make someone matchmaking staff.", "Bot owner", []),
    ("matchmaking", "match staff remove", "<user>", "Remove someone from matchmaking staff.", "Bot owner", []),
    ("matchmaking", "match staff feed", "<#channel|off>", "Mirror all matchmaking activity into a private channel.", "Bot owner", []),
    ("info", "help", "[command]", "Show this menu, or how one command works.",
     "Everyone", ["commands"]),
    ("info", "prefix", "[command] [prefix]", "Show the prefix of every command, or change one. Use reset to restore a default. Works for any command, including this one.",
     "Server owner", []),
    ("info", "stafflist", "", "Post the staff list (everyone with a rank role: own, co own, Admin, Moderator, Helper, Partnering) in this channel. It stays at the bottom and updates itself.",
     "Manage Server", []),
    ("info", "stafflist off", "", "Remove the staff list.", "Manage Server", ["stafflist remove"]),
    ("info", "stats", "", "How many servers, members and matches the bot has.", "Everyone", []),
    ("info", "backup", "", "Send yourself, in DMs, ONE zip file with everything: everyone's reputation, quest progress, smash and pass counts, every family (members, ranks, bank, war, banners), matchmaking, the ,sop list with its uploaded pictures and the ,assist history. Matchmaking is global, so the bot owner's backup has all of it (profiles, bans, blocks, reports, staff, panels; not the chat log) and a server owner's backup only has this server's matchmaking switch and sticky panels. Bot owner: add all to include every server. Only when it is bigger than one Discord upload does it come as a few files, each one complete.",
     "Server owner", []),
    ("info", "restore", "[file or code]", "Load the backup .zip file from .backup (older backups: the .txt file or pasted code, plus the .zip picture files) to bring reputation, quest progress, smash and pass counts, families (with their banks and banners), matchmaking, the ,sop list and the ,assist history back after rebuilding the bot. People in the backup get their saved numbers; pictures already in the list are not added twice. Families are only loaded in a server that has none yet. Matchmaking is added to what is already there (profiles in the backup replace the same person's profile); only the bot owner restores the global part (profiles, bans, reports...).",
     "Server owner", []),
    ("info", "emojisync", "", "Create or refresh the bot's own icon emojis (pager arrows, page jump, tick, cross, warning, on / off pills, gambling pictures) on its account, right now. The bot also does this by itself every time it starts, so you only need it after updating the bot without restarting. Redrawn icons are replaced.",
     "Bot owner", []),
    ("info", "customize", "", "Change the bot's profile (picture, banner, username, bio) and style (embed color, name color, font).",
     "Administrator", ["customise"]),
    ("info", "gambling", "[on|off|list] [#channel]", "Turn ,coinflip ,slots ,plinko and ,blackjack off (or back on) in a channel. Without a channel it uses the one you type it in, and with no action it shows where gambling is off. A game already running keeps going.",
     "Manage Server", []),
    ("info", "invitelog", "[#channel|off]", "Post a full profile of every new member in a channel: names and former names, account age, badges, roles, who invited them and how, plus an alt check (looks normal / possible alt / likely alt, with the accounts they look like) and Timeout / Kick / Ban / Dismiss buttons for staff. The bot needs Manage Server to see invites and Ban Members to compare with the ban list. Without a channel it shows the current one.", "Manage Server", []),
    ("info", "joinguard", "[setting] [value]", "Security settings for new members: raid alert / lockdown, a quarantine role for brand new accounts and likely alts (staff press Approve), automatic timeout for a likely alt, early watch (invite links, scam links, mass pings in a member's first hour) and the leave log. Without a setting it shows them all.", "Manage Server", []),
    ("info", "roles", "", "Check where the Top 1, Best family, family and custom roles sit. Family, Best family and Top 1 go right under the Staff role, custom roles right under the Soul role.",
     "Manage Server", []),
    ("info", "roles fix", "", "Move every role under Staff / Soul right now (the bot also re-checks them every 15 minutes).", "Manage Server", []),
    ("info", "roles staff", "[@role]", "Pick the Staff role the bot uses when it can't find one named Staff.", "Manage Server", []),
    ("info", "roles soul", "[@role]", "Pick the Soul role the bot uses when it can't find one named Soul.", "Manage Server", []),
    ("info", "roles forget", "<staff|soul>", "Go back to finding the Staff or Soul role by its name.", "Manage Server", []),
    ("info", "goatrole", "[@role]", "Pick the role the #1 on the reputation leaderboard gets. The bot gives it to the new #1 and takes it from everyone else, whatever you rename the role to. Without a role it shows the current one.",
     "Manage Server", []),
]
# Put every command in a sensible category and order (Info -> Fun -> Family -> Matchmaking -> Server).
_SERVER_ROOTS = {"wordfilter", "stafflist", "customize", "prefix", "backup", "restore", "goatrole", "gambling", "roles", "invitelog", "joinguard", "emojisync"}
_ORDER = {
    "info": ["help", "stats"],
    "fun": ["puh", "racist", "ratio", "gayest", "tightest", "goonlock", "reputation", "reputation top", "reputation give", "daily", "duel", "bottle", "coinflip", "slots", "plinko", "blackjack", "rob", "bounty", "shop", "inventory", "title", "badge", "wordstats", "myrole", "quests", "collect", "sop"],
    "server": ["prefix", "wordfilter", "stafflist", "customize", "goatrole", "roles", "gambling", "invitelog", "joinguard", "backup", "restore", "emojisync"],
}


def _help_sort_key(entry):
    cat, name = entry[0], entry[1]
    root = name.split()[0]
    order = _ORDER.get(cat, [])
    place = order.index(name) if name in order else (order.index(root) + 0.5 if root in order else len(order))
    return (list(HELP_CATEGORIES).index(cat), place)


HELP_COMMANDS = [("server" if e[1].split()[0] in _SERVER_ROOTS else e[0],) + tuple(e[1:]) for e in HELP_COMMANDS]
HELP_COMMANDS.sort(key=_help_sort_key)  # stable: subcommands keep the order they were written in
HELP_EXAMPLES = {
    "wordfilter": "wordfilter add die",
    "wordfilter add": "wordfilter add die",
    "wordfilter remove": "wordfilter remove die",
    "wordfilter list": "wordfilter list",
    "puh": "puh @member",
    "racist": "racist @member",
    "ratio": "ratio",
    "reputation": "rep @member",
    "reputation give": "rep give @member 123",
    "reputation top": "rep top",
    "daily": "daily",
    "duel": "duel @member 50",
    "bottle": "bottle",
    "coinflip": "coinflip 50 heads",
    "slots": "slots 100",
    "plinko": "plinko 500 high",
    "rob": "rob @member",
    "blackjack": "blackjack 100",
    "quests": "quests claim",
    "sop": "sop",
    "sop vote": "sop vote",
    "sop upload": "sop upload Name",
    "sop add": "sop add https://i.imgur.com/example.jpg Name",
    "sop remove": "sop remove Name",
    "sop source": "sop source disable uploaded",
    "sop block": "sop block",
    "collect": "collect",
    "shop": "shop",
    "shop buy": "shop buy luck 3",
    "wordstats": "wordstats moha",
    "inventory": "inventory",
    "myrole": "myrole",
    "myrole name": "myrole name Moon Lord",
    "myrole color": "myrole color #ff00aa",
    "myrole icon": "myrole icon \U0001F525",
    "myrole delete": "myrole delete",
    "title": "title @member",
    "title set": "title set neon Menace",
    "title color": "title color pink",
    "title remove": "title remove",
    "title reset": "title reset @member",
    "bounty": "bounty @member 500",
    "badge": "badge 🔥",
    "roles": "roles",
    "roles fix": "roles fix",
    "roles staff": "roles staff @Staff",
    "roles soul": "roles soul @Soul",
    "roles forget": "roles forget soul",
    "fam war": "fam war",
    "fam war channel": "fam war channel #announcements",
    "match reveal": "match reveal",
    "match icebreaker": "match icebreaker",
    "match stats": "match stats",
    "stafflist": "stafflist",
    "stafflist off": "stafflist off",
    "stats": "stats",
    "prefix": "prefix puh !",
    "invitelog": "invitelog #welcome-log",
    "joinguard": "joinguard quarantine @Unverified",
    "backup": "backup",
    "restore": "restore",
    "emojisync": "emojisync",
    "gayest": "gayest",
    "tightest": "tightest",
    "goonlock": "goonlock add @member",
    "goonlock add": "goonlock add @member",
    "goonlock remove": "goonlock remove @member",
    "goonlock list": "goonlock list",
    "fam": "fam info",
    "fam create": "fam create Wolves",
    "fam invite": "fam invite @member",
    "fam info": "fam info Wolves",
    "fam promote": "fam promote @member",
    "fam demote": "fam demote @member",
    "fam kick": "fam kick @member",
    "fam rename": "fam rename Big Wolves",
    "fam description": "fam description We hunt at night",
    "fam banner": "fam banner",
    "fam transfer": "fam transfer @member",
    "fam leave": "fam leave",
    "fam delete": "fam delete",
    "fam kidnap": "fam kidnap @member",
    "fam top": "fam top",
    "fam rep": "fam rep",
    "fam deposit": "fam deposit 500",
    "fam withdraw": "fam withdraw 500",
    "fam baserole": "fam baserole @Families",
    "fam admin_add": "fam admin_add @member Wolves",
    "fam admin_remove": "fam admin_remove @member",
    "fam purge": "fam purge Wolves",
    "match": "match find",
    "match find": "match find quick",
    "match panel": "match panel #friends",
    "match staff chat": "match staff chat 123456",
    "help": "help wordfilter add",
    "customize": "customize",
    "goatrole": "goatrole @top 1#",
    "gambling": "gambling off #general",
}


def help_usage(entry):
    """The Syntax line, e.g.  .wordfilter add (word)"""
    name, args = entry[1], entry[2]
    if name in HELP_GROUPS:
        tail = "(subcommands) (args)"
    else:
        tail = " ".join(f"({a})" for a in re.findall(r"[<\[]([^>\]]+)[>\]]", args))
    return f"{help_prefix_for(name)}{name} {tail}".strip()


def help_example(entry):
    return f"{help_prefix_for(entry[1])}{HELP_EXAMPLES.get(entry[1], entry[1])}"


def help_params(entry):
    if entry[1] in HELP_GROUPS:
        return "n/a"
    return ", ".join(a.strip("<>[]") for a in entry[2].split()) or "n/a"


def find_help(text):
    """Entries matching what the user typed after help ("wordfilter", "wf add", ".puh")."""
    text = text.lower().strip()
    for p in all_prefixes():  # "+help .puh" or "help puh" both work
        if not p[0].isalnum() and text.startswith(p.lower()):
            text = text[len(p):]
            break
    text = " ".join(text.split())
    if not text:
        return []
    exact = [e for e in HELP_COMMANDS if text == e[1] or text in e[5]]
    if exact:
        return exact
    return [
        e for e in HELP_COMMANDS
        if e[1].split()[0] == text or any(a.split()[0] == text for a in e[5])
    ]


def help_lines(entries):
    blocks = []
    for entry in entries:
        block = f"`{help_usage(entry)}`\n{short_desc(entry[3])}"
        if entry[4] != "Everyone":
            block += f" ({entry[4].lower()})"
        blocks.append(block)
    return "\n".join(blocks)


POPULAR = ("wordfilter", "fam", "match", "puh", "racist", "gayest", "tightest")


def help_home_embed(user=None):
    embed = discord.Embed(
        title=bot.user.display_name,
        description=(
            f"{info_icon()}Browse the commands with the dropdown below, or use `{HELP_PREFIX}help (command)` for details on one.\n"
            f"Fun commands use the `{FUN_PREFIX}` prefix.\n\n"
            "**Popular** \u00b7 " + " \u00b7 ".join(f"`{help_prefix_for(n)}{n}`" for n in POPULAR)
        ),
        color=theme(),
    )
    return embed


def help_prefix_for(name):
    """Prefix of a help entry by its name ('wordfilter add' uses the prefix of 'wordfilter')."""
    return prefix_for(name.split()[0])


def short_desc(text):
    """First sentence only, so a page of commands stays short."""
    first = text.split(". ")[0].rstrip(".")
    return first if len(first) <= 70 else first[:67].rstrip() + "..."


def compact_names(entries):
    """Long categories: 'family' + all its subcommand names on a few wrapped lines instead of one line each."""
    order, by_root = [], {}
    for e in entries:
        root, _, rest = e[1].partition(" ")
        if root not in by_root:
            by_root[root] = []
            order.append((root, e))
        if rest:
            by_root[root].append(rest)
    out = []
    for root, first in order:
        if by_root[root]:
            out.append(f"`{help_prefix_for(root)}{root}` " + " \u00b7 ".join(f"`{r}`" for r in by_root[root]))
        else:
            out.append(f"`{help_usage(first)}`")
    return "\n".join(out)


def help_category_embed(key, user=None):
    label, _blurb = HELP_CATEGORIES[key]
    entries = [e for e in HELP_COMMANDS if e[0] == key and e[1] not in HELP_GROUPS]
    full = help_lines(entries)
    if len(full) <= 700:
        embed = discord.Embed(title=label, description=full, color=theme())
    else:  # long lists (family) show only the syntax; the details are one command away
        lines = compact_names(entries)
        groups = [e[1] for e in HELP_COMMANDS if e[0] == key and e[1] in HELP_GROUPS]
        hint = f"\n\n{info_icon()}Details: `{HELP_PREFIX}help {groups[0]}`" if groups else ""
        embed = discord.Embed(title=label, description=lines + hint, color=theme())
    embed.set_footer(text=f"{len(entries)} commands \u2022 Module: {key}")
    with_author(embed, user)
    return embed


HELP_SUBS_PER_PAGE = 4


def group_subs(name):
    return [e for e in HELP_COMMANDS if e[1].startswith(name + " ")]


def group_page_count(entry):
    return 1 + -(-len(group_subs(entry[1])) // HELP_SUBS_PER_PAGE)


def help_group_embed(entry, index, user=None):
    """A group command split into short pages: page 1 is the group card, then 5 subcommands per page."""
    category, name, _args, desc, perm, aliases = entry
    subs = group_subs(name)
    pages = group_page_count(entry)
    if index == 0:
        embed = discord.Embed(title=f"Group Command: {name}", description=desc, color=theme())
        embed.add_field(name="Aliases", value=", ".join(aliases) or "n/a", inline=True)
        who = f"{info_icon()}{perm}" if perm == "Everyone" else f"{emo('warn')} {perm}".strip()
        embed.add_field(name="Information", value=who, inline=True)
        usage = f"Syntax:  {help_usage(entry)}\nExample: {help_example(entry)}"
        embed.add_field(name="Usage", value=f"```\n{usage}\n```", inline=False)
    else:
        chunk = subs[(index - 1) * HELP_SUBS_PER_PAGE: index * HELP_SUBS_PER_PAGE]
        lines = []
        for e in chunk:
            line = f"`{help_usage(e)}`\n{short_desc(e[3])}"
            if e[4] != "Everyone":
                line += f" ({e[4].lower()})"
            lines.append(line)
        embed = discord.Embed(title=f"{name} commands", description="\n".join(lines), color=theme())
    with_author(embed, user)
    embed.set_footer(text=f"Page {index + 1}/{pages} ({len(subs)} entries) \u2022 Module: {category}")
    return embed


def help_page_embed(entry, user=None):
    """One command: description and a code block with the syntax and an example."""
    _category, name, _args, desc, perm, aliases = entry
    embed = discord.Embed(title=f"Command: {name}", description=desc, color=theme())
    with_author(embed, user)
    usage = f"Syntax:  {help_usage(entry)}\nExample: {help_example(entry)}"
    if aliases:
        usage += f"\nAliases: {', '.join(aliases)}"
    embed.add_field(name="How To Use", value=f"```\n{usage}\n```", inline=False)
    embed.set_footer(text=f"Module: {entry[0]}" + (f" \u2022 Requires: {perm}" if perm != "Everyone" else ""))
    return embed


class HelpView(discord.ui.View):
    """The overview's category dropdown."""

    def __init__(self, author_id, user=None):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.user = user
        self.message = None
        options = [discord.SelectOption(label="Overview", value="home", description="Back to the start")]
        for key, (label, blurb) in HELP_CATEGORIES.items():
            options.append(discord.SelectOption(label=label, value=key, description=blurb))
        self.menu = discord.ui.Select(placeholder="Click this to open a category", options=options)
        self.menu.callback = self.on_pick
        self.add_item(self.menu)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", f"This menu isn't yours. Run **{HELP_PREFIX}help** yourself."),
                ephemeral=True,
            )
            return False
        return True

    async def on_pick(self, interaction):
        value = self.menu.values[0]
        if value == "home":
            embed = help_home_embed(self.user)
        else:
            embed = help_category_embed(value, self.user)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        self.menu.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def send_help(ctx, text):
    """The page of the command that was asked for."""
    entries = find_help(text)
    if not entries:
        shown = discord.utils.escape_markdown(text[:40])
        await ctx.send(embed=status_embed(ctx, "warn", f"There is no command called **{shown}**."))
        return
    entry = entries[0]
    if entry[1] in HELP_GROUPS:  # groups are paged so the embed stays short
        total = group_page_count(entry)
        view = Pager(ctx.author.id, lambda i: help_group_embed(entry, i, ctx.author), total)
        view.message = await ctx.send(embed=help_group_embed(entry, 0, ctx.author), view=view)
        return
    await ctx.send(embed=help_page_embed(entry, ctx.author))


@bot.hybrid_command(name="help", aliases=["commands"], description="Show every command, or the help page of one command")
@app_commands.describe(command="A command to look up, like family or rep top")
async def help_command(ctx, *, command: str = None):
    if command:
        await send_help(ctx, command)
        return
    view = HelpView(ctx.author.id, ctx.author)
    view.message = await ctx.send(embed=help_home_embed(ctx.author), view=view)


# --------------------------------------------------------------------------
# Full access: the bot owner and the owner of the server the command is used in
# --------------------------------------------------------------------------
def is_server_owner(user, guild=None):
    """True when `user` owns the server (guild) the command is used in."""
    guild = guild or getattr(user, "guild", None)
    return guild is not None and user.id == guild.owner_id


async def has_full_access(user, guild=None):
    """The server owner or the bot owner: they can use every command."""
    return is_server_owner(user, guild) or await bot.is_owner(user)


def owner_check():
    """Replaces commands.is_owner(): the bot owner AND the server owner pass."""
    async def predicate(ctx):
        if await has_full_access(ctx.author, ctx.guild):
            return True
        raise commands.CheckFailure("Only the **server owner** or the bot owner can use this.")

    return commands.check(predicate)


# --------------------------------------------------------------------------
# +prefix: change the prefix of ANY command, any time (server owner / bot owner, global)
# --------------------------------------------------------------------------
PREFIX_MAX_LEN = 3
PREFIX_RESET_WORDS = {"reset", "default", "clear", "remove"}


def prefix_error(text):
    """Why a new prefix isn't allowed, or None if it's fine."""
    if not 1 <= len(text) <= PREFIX_MAX_LEN:
        return f"A prefix needs **1-{PREFIX_MAX_LEN}** characters."
    if any(c.isspace() or not c.isprintable() for c in text):
        return "A prefix can't contain spaces."
    if set("@<`") & set(text):
        return "A prefix can't contain @, < or backticks."
    return None


def root_name_of(text):
    """'fam', 'wf' or 'puh' -> the real top-level command name, or None if there is no such command."""
    words = (text or "").strip().lower().split()
    if not words:
        return None
    for p in all_prefixes():  # "+prefix .puh !" works too
        if not p[0].isalnum() and words[0].startswith(p.lower()) and len(words[0]) > len(p):
            words[0] = words[0][len(p):]
            break
    cmd = bot.get_command(words[0])
    return (cmd.root_parent or cmd).name if cmd else None


def prefix_overview_embed(user):
    """Every command grouped by the prefix it uses right now."""
    groups = {}
    for cmd in sorted(bot.commands, key=lambda c: c.name):
        groups.setdefault(prefix_for(cmd.name), []).append(cmd.name)
    lines = [
        f"`{p}`  " + " · ".join(names)
        for p, names in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    embed = discord.Embed(title="Command prefixes", description="\n".join(lines), color=theme())
    with_author(embed, user)
    embed.set_footer(text=f"{prefix_for('prefix')}prefix (command) (new prefix)  •  reset to restore the default")
    return embed


@bot.hybrid_command(name="prefix", description="Show or change the prefix of any command (server owner)")
@owner_check()
async def prefix_command(ctx, command: str = None, new_prefix: str = None):
    if command is None:
        await ctx.send(embed=prefix_overview_embed(ctx.author))
        return
    name = root_name_of(command)
    if name is None:
        shown = discord.utils.escape_markdown(command[:40])
        await ctx.send(embed=status_embed(ctx, "warn", f"There is no command called **{shown}**."))
        return
    if new_prefix is None:
        await ctx.send(embed=status_embed(
            ctx, "warn",
            f"`{name}` uses the prefix `{prefix_for(name)}`. Change it with `{prefix_for('prefix')}prefix {name} (new prefix)`."
        ))
        return
    if new_prefix.lower() in PREFIX_RESET_WORDS:
        settings.reset_prefix(name)
        default = default_prefix_for(name)
        await ctx.send(embed=status_embed(ctx, "ok", f"`{name}` is back to its default prefix `{default}`. Use `{default}{name}`."))
        return
    problem = prefix_error(new_prefix)
    if problem:
        await ctx.send(embed=status_embed(ctx, "warn", problem))
        return
    if new_prefix == default_prefix_for(name):
        settings.reset_prefix(name)  # same as the default, nothing to remember
    else:
        settings.set_prefix(name, new_prefix)
    text = f"`{name}` now uses the prefix `{new_prefix}`. Use `{new_prefix}{name}`."
    if name == "prefix":
        text += f" If you ever forget it, mention me: `@{bot.user.name} prefix prefix reset`."
    await ctx.send(embed=status_embed(ctx, "ok", text))


# --------------------------------------------------------------------------
# Wordfilter commands (Manage Server only). The checks sit on each subcommand
# because a check on the group does not run for subcommands.
# --------------------------------------------------------------------------
WORDS_PER_PAGE = 10


def words_page_embed(guild, words, index, user=None):
    pages = max(1, -(-len(words) // WORDS_PER_PAGE))
    start = index * WORDS_PER_PAGE
    shown = words[start:start + WORDS_PER_PAGE]
    rows = "\n".join(
        f"`{n}` **{discord.utils.escape_markdown(w)}**" for n, w in enumerate(shown, start=start + 1)
    )
    embed = discord.Embed(title=f"Trigger words in {guild.name}"[:256], description=rows, color=theme())
    with_author(embed, user)
    embed.set_footer(text=f"Page {index + 1}/{pages} ({len(words)} entries)")
    return embed


@bot.hybrid_group(name="wordfilter", aliases=["wf"], invoke_without_command=True, description="Manage the trigger words", fallback="help")
async def wordfilter(ctx):
    await send_help(ctx, "wordfilter")


@wordfilter.command(name="add", description="Add a trigger word")
@commands.has_permissions(manage_guild=True)
async def wordfilter_add(ctx, *, word: str):
    word = word.replace("`", "").strip().lower()
    if not word or len(word) > 100:
        await plain_reply(ctx, "warn", "Give a word between **1** and **100** characters.")
        return
    shown = discord.utils.escape_markdown(word)
    if store.add(ctx.guild.id, word):
        await plain_reply(ctx, "add", f"Added **{shown}** as a trigger word.")
    else:
        await plain_reply(ctx, "warn", f"**{shown}** is already a trigger word.")


@wordfilter.command(name="remove", aliases=["delete"], description="Remove a trigger word")
@commands.has_permissions(manage_guild=True)
async def wordfilter_remove(ctx, *, word: str):
    word = word.replace("`", "").strip().lower()
    shown = discord.utils.escape_markdown(word)
    if store.remove(ctx.guild.id, word):
        await plain_reply(ctx, "remove", f"Removed **{shown}** from the trigger words.")
    else:
        await plain_reply(ctx, "warn", f"**{shown}** is not a trigger word.")


@wordfilter.command(name="defaults", description="Add the built-in starter trigger words that are missing")
@commands.has_permissions(manage_guild=True)
async def wordfilter_defaults(ctx):
    added = store.add_defaults(ctx.guild.id)
    if added:
        await plain_reply(ctx, "add", f"Added **{added}** starter trigger word{'s' if added != 1 else ''}.")
    else:
        await plain_reply(ctx, "warn", "All the starter trigger words are already in the list.")


@wordfilter.command(name="list", description="Show every trigger word")
@commands.has_permissions(manage_guild=True)
async def wordfilter_list(ctx):
    words = store.words(ctx.guild.id)
    if not words:
        await plain_reply(ctx, "warn", f"No trigger words yet. Add one with `{prefix_for('wordfilter')}wordfilter add (word)`.")
        return
    # plain text, no embed: "1. word" per line, split over several messages when it doesn't fit in one (2000 characters)
    header = f"**Trigger words in {ctx.guild.name}** ({len(words)})"
    chunks, cur = [], header
    for n, w in enumerate(words, start=1):
        line = f"\n`{n}.` {discord.utils.escape_markdown(w)}"
        if len(cur) + len(line) > 1900:
            chunks.append(cur)
            cur = line.lstrip("\n")
        else:
            cur += line
    chunks.append(cur)
    none = discord.AllowedMentions.none()
    await ctx.reply(chunks[0], allowed_mentions=none)
    for extra in chunks[1:]:
        await ctx.send(extra, allowed_mentions=none)


# --------------------------------------------------------------------------
# Fun commands (own "," prefix): ,puh  ,racist  ,gayest  ,tightest
# --------------------------------------------------------------------------
def meter_embed(ctx, description, default_bar=False):
    """Shared look of the meters: author line and the result (no footer text).

    default_bar=True sends no color at all, so Discord draws its own default grey side bar
    (a color picked in .customize still wins). Otherwise the invisible theme bar is used."""
    color = (settings.embed_color or None) if default_bar else theme()
    embed = discord.Embed(description=description, color=color)
    with_author(embed, ctx.author)
    return embed


def puh_bars(tight, loose):
    """The two ,puh bars. Same look as meter_bar, but 45-55% used to round to 5 blocks on BOTH bars, so a 53 / 47 roll looked like a perfect tie.
    Now the bars are only equal on a real 50 / 50: otherwise the leader gets one block more and the other one less."""
    def blocks(percent):
        return max(0, min(METER_SEGMENTS, int(percent / 100 * METER_SEGMENTS + 0.5)))
    t, l = blocks(tight), blocks(loose)
    if tight != loose and t == l:
        t, l = (t + 1, l - 1) if tight > loose else (t - 1, l + 1)
    def draw(n):
        return "`" + "\u2588" * n + "\u2591" * (METER_SEGMENTS - n) + "`"
    return draw(t), draw(l)


@bot.hybrid_command(name="puh", description="Random tight or loose meter")
@commands.cooldown(1, 5, commands.BucketType.user)
@commands.guild_only()
async def puh(ctx, member: discord.Member = None):
    member = member or ctx.author
    tight, loose = roll_puh()
    lock = emo("lock", "\U0001F512")      # 🔒 tight
    unlock = emo("unlock", "\U0001F513")  # 🔓 loose
    if tight == loose:  # exactly 50 / 50 (1 roll in 101)
        head = f"\u2696\uFE0F {member.mention} is **perfectly balanced**, as all things should be\n"
    elif tight > loose:
        head = f"{lock} {member.mention} is **tight**\n"
    else:
        head = f"{unlock} {member.mention} is **loose**\n"
    tight_bar, loose_bar = puh_bars(tight, loose)
    await ctx.send(embed=meter_embed(
        ctx,
        head +  # only the result line has an emoji (lock / unlock / scales); the two bar lines are plain
        f"{tight_bar} {tight}% tight\n"
        f"{loose_bar} {loose}% loose",
    ))


def racist_verdict(percent):
    """Binary verdict for the ,racist meter."""
    return "racist" if percent > 50 else "peaceful"


@bot.hybrid_command(name="racist", description="Random racism meter")
@commands.cooldown(1, 5, commands.BucketType.user)
@commands.guild_only()
async def racist(ctx, member: discord.Member = None):
    member = member or ctx.author
    percent = random.randint(0, 100)
    verdict = racist_verdict(percent)
    icon = "\U0001F621" if verdict == "racist" else "\U0001F54A\uFE0F"  # angry face / dove
    # look: name on top, "icon @user is verdict", bar + percent
    embed = discord.Embed(
        description=(
            f"{icon} {member.mention} is **{verdict}**\n"
            f"{meter_bar(percent)} {percent}% racist"
        ),
        color=settings.embed_color or None,  # no color = Discord's default grey side bar
    )
    with_author(embed, member)
    await ctx.send(embed=embed)


@bot.hybrid_command(name="ratio", description="Win or lose a random amount of reputation")
@commands.guild_only()
async def ratio(ctx):
    """W ratio: you earn a random amount of reputation. L ratio: you lose a random amount. Saved, with a cooldown."""
    gid, uid = ctx.guild.id, ctx.author.id
    now = time.time()
    left = auras.user(gid, uid)["ratio_at"] + RATIO_COOLDOWN - now
    if left > 0:
        await ctx.send(embed=status_embed(ctx, "cooldown", f"Slow down, try again in **{human_time(left)}**."))
        return
    won, aura = roll_ratio()
    if not won and auras.balance(gid, uid) < 0:
        aura = max(1, round(aura * RATIO_DEBT_LOSS))  # already below zero: an L only takes 30% of what it would
    auras.user(gid, uid)["ratio_at"] = now
    total = give_aura(gid, uid, aura if won else -aura)  # the new balance, after the win or the loss
    quest_progress(gid, uid, "ratio")
    if won:
        quest_progress(gid, uid, "ratio_win")
        text = f"{emo('wratio') or emo('trophy', chr(0x1F3C6))} **W ratio**\n{ctx.author.mention} earned **{aura:,}** reputation"
    else:
        text = f"{emo('skull', chr(0x1F480))} **L ratio**\n{ctx.author.mention} lost **{aura:,}** reputation"
    text += "\n" + small(f"Balance {total:,}")  # tiny grey line with the balance after this ratio (can be negative), like the gambling cards
    await ctx.send(embed=meter_embed(ctx, text))


# --------------------------------------------------------------------------
# ,wordstats <word>: how many times you said a word in THIS channel, and how many times everybody did here.
# Nothing new is saved: it counts the messages the assistant already keeps in askai_messages.db (askai.py; ,assistbackfill imports older
# ones). Whole words only ("moha" does not count "mohamed"), upper / lower case is the same, deleted messages and bot commands are skipped.
# --------------------------------------------------------------------------
import pathlib
import sqlite3

WORDSTATS_DB = os.path.join(DATA_DIR, "askai_messages.db")
WORDSTATS_MAX_LEN = 40  # the longest word that can be looked up
_WS_SKIP = re.compile(r"https?://\S+|www\.\S+|<a?:\w+:\d+>|<@[!&]?\d+>|<#\d+>|<t:-?\d+(?::\w)?>", re.I)  # links, custom emojis, mentions, timestamps
_WS_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")  # letters / digits of any language, apostrophes allowed inside (don't)
wordstats_gate = asyncio.Semaphore(1)  # one lookup at a time, so several people at once can't pile work onto a small host


def wordstats_words(text):
    """The words of one message, lower-cased. Links, mentions and custom emojis are dropped first, so an emoji called :moha: is not the word moha."""
    text = unicodedata.normalize("NFC", _WS_SKIP.sub(" ", text or "")).replace("’", "'")
    return [w.casefold() for w in _WS_WORD.findall(text)]


def wordstats_target(text):
    """The one word that was asked about: (word, None), or (None, "none" | "many" | "long")."""
    found = wordstats_words(text)
    if not found:
        return None, "none"
    if len(found) > 1:
        return None, "many"
    if len(found[0]) > WORDSTATS_MAX_LEN:
        return None, "long"
    return found[0], None


def wordstats_patterns(word):
    """LIKE patterns that find every message that could hold the word. SQLite's LIKE only ignores case for plain A-Z, so a word with
    other letters (é, ç, Cyrillic...) is also tried in lower / UPPER / Capitalised form. The exact count is done in Python afterwards."""
    forms = {word} if word.isascii() else {word, word.lower(), word.upper(), word.capitalize(), word.title()}
    return ["%" + f.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for f in sorted(forms)]


def wordstats_is_command(text, prefixes, names):
    """True for a message that runs a bot command (",wordstats moha", "+help"), so the question itself never counts as saying the word."""
    text = text.lstrip()
    for p in prefixes:
        if not text.startswith(p):
            continue
        rest = text[len(p):]
        if p.startswith("<@"):  # "@Bot wordstats moha"
            rest = rest.lstrip()
        elif not rest or rest[0].isspace():  # ". shop is nice" is not the command ".shop"
            continue
        first = rest.split(None, 1)[0] if rest.strip() else ""
        if first and first in names:
            return True
    return False


def wordstats_count(rows, word, user_id, prefixes, names):
    """(times user_id said the word, times everybody said it) over rows of (author id, message text)."""
    mine = total = 0
    for author, text in rows:
        if not text or wordstats_is_command(text, prefixes, names):
            continue
        n = wordstats_words(text).count(word)
        if n:
            total += n
            if author == user_id:
                mine += n
    return mine, total


def wordstats_lookup(db_path, guild_id, channel_id, user_id, word, prefixes, names):
    """Runs in a worker thread and only reads (a second, read-only connection, so the message log is never blocked or changed).
    Counts ONE channel (a thread counts as its own channel). Returns (mine, total)."""
    con = sqlite3.connect(pathlib.Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        patterns = wordstats_patterns(word)
        like = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in patterns)
        rows = con.execute(f"SELECT author, content FROM msgs WHERE guild = ? AND channel = ? AND COALESCE(deleted, 0) = 0 AND ({like})",
                           [guild_id, channel_id, *patterns])
        return wordstats_count(rows, word, user_id, prefixes, names)
    finally:
        con.close()


def wordstats_times(n):
    return f"{n:,} time" + ("" if n == 1 else "s")


@bot.hybrid_command(name="wordstats", description="How many times you said a word, and how many times everyone did")
@app_commands.describe(word="The word to look up")
@commands.guild_only()
@commands.cooldown(1, 5, commands.BucketType.user)
async def wordstats_cmd(ctx, *, word: str):
    target, problem = wordstats_target(word)
    if target is None:
        usage = f"`{prefix_cmd('wordstats')} moha`"
        text = {"none": f"Write a word, like {usage}.", "many": f"One word at a time, like {usage}.",
                "long": f"That word is too long (up to {WORDSTATS_MAX_LEN} letters)."}[problem]
        await ctx.send(embed=status_embed(ctx, "warn", text))
        ctx.command.reset_cooldown(ctx)  # a typo shouldn't cost the cooldown
        return
    prefixes = tuple(all_prefixes()) + (f"<@{bot.user.id}>", f"<@!{bot.user.id}>")
    names = frozenset(bot.all_commands)  # every command name and alias
    try:
        async with wordstats_gate:
            async with typing_for(ctx):
                mine, total = await asyncio.to_thread(wordstats_lookup, WORDSTATS_DB, ctx.guild.id, ctx.channel.id, ctx.author.id, target, prefixes, names)
    except sqlite3.Error:
        log.exception("wordstats: could not read %s", WORDSTATS_DB)
        await ctx.send(embed=status_embed(ctx, "warn", "I haven't saved any messages yet, so there is nothing to count."))
        return
    if total == 0:
        text = f"Nobody has said **{target}** here yet\n" + small("Counts the messages I saved in this channel")
    elif mine == 0:
        text = f"{ctx.author.mention} hasn't said **{target}** here\n" + small(f"**{wordstats_times(total)}** by everyone here")
    else:
        text = f"{ctx.author.mention} said **{target}** **{wordstats_times(mine)}** here\n" + small(f"**{wordstats_times(total)}** by everyone here")
    await ctx.send(embed=meter_embed(ctx, text))


LEFTOVER_TAG = re.compile(r"^\[[^\]\w]{1,6}\]\s*")  # "[emoji] name": a tag made only of emojis / symbols (no letters or digits)


def plain_name(guild, member):
    """A member's display name without a family tag, for lists. Older versions forced "[TAG] name" nicknames, and people the bot can't
    rename (the owner, anyone above its role) still carry one. Cut off: the tag of any family in this server, and an emoji / symbol
    tag (left by a family that was deleted). A nickname like "[AFK] name" is left alone. Only what is shown changes, never the nickname."""
    name = member.display_name
    for fam in families.all(guild.id):
        prefix = f"[{fam['tag']}]" if fam.get("tag") else None
        if prefix and name.startswith(prefix):
            name = name[len(prefix):].lstrip()
    name = LEFTOVER_TAG.sub("", name, count=1)
    return name or member.name


BOARD_SIZE = 10  # people shown on the aura leaderboard


def aura_board_rows(guild, mode):
    """[(user id, number)] for the leaderboard, only people still in the server. mode: "all" or "week" (7 Days)."""
    rows = auras.ranking_recent(guild.id, 7) if mode == "week" else auras.ranking(guild.id)
    return [(uid, value) for uid, value in rows if guild.get_member(uid) is not None]


def aura_rank(guild, uid):
    """Someone's place on the all-time leaderboard (only people still in the server count), or None without reputation yet."""
    for place, (who, _value) in enumerate(aura_board_rows(guild, "all"), start=1):
        if who == uid:
            return place
    return None


def aura_board_pages(guild, mode):
    return max(1, -(-len(aura_board_rows(guild, mode)) // BOARD_SIZE))


def aura_board_embed(guild, mode="all", index=0, viewer=None):
    """One page of the reputation leaderboard, laid out like Bleed's top lists (same as the family top):
    a title (the crown + "Top users"), `rank` **name** - value lines, 'Your ...' line, then the 'Page 1/1 (N entries)' footer."""
    rows = aura_board_rows(guild, mode)
    pages = max(1, -(-len(rows) // BOARD_SIZE))
    index = max(0, min(index, pages - 1))
    start = index * BOARD_SIZE
    lines = []
    for n, (uid, value) in enumerate(rows[start:start + BOARD_SIZE], start=start + 1):
        name = discord.utils.escape_markdown(plain_name(guild, guild.get_member(uid)))
        shown = f"{value:+,}" if mode == "week" else f"{value:,}"
        lines.append(f"`{n}` **{name}**{title_tag(guild.id, uid)} - {shown}")
    if not lines:
        lines.append("Nobody has earned reputation in the last 7 days yet.")
    if viewer is not None:
        place = next((i for i, (uid, _v) in enumerate(rows, start=1) if uid == viewer.id), None)
        if mode == "week":
            gained, lost = auras.window(guild.id, 7).get(viewer.id, (0, 0))
            mine = f"{gained - lost:+,}"
        else:
            mine = f"{auras.balance(guild.id, viewer.id):,}"
        lines.append(f"Your reputation: **{mine}** - Rank: `{'#' + str(place) if place else 'Unranked'}`")
    title = "Top users (7 days)" if mode == "week" else "Top users"
    title = f"{emo('crown')} {title}".lstrip()  # the server crown in front of the title (no icon when the emoji isn't found)
    embed = discord.Embed(title=title, description="\n".join(lines), color=theme())  # no name line on top: the title starts the box
    embed.set_footer(text=f"Page {index + 1}/{pages} ({len(rows)} entries)")
    return embed


def streak_now(u):
    """Days of the daily streak that are still alive (claimed today or yesterday), else 0."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    try:
        gap = (today - datetime.date.fromisoformat(u.get("daily_day") or "")).days
    except ValueError:
        return 0
    held = u.get("items") if isinstance(u.get("items"), dict) else {}
    return u.get("streak", 0) if 0 <= gap <= 1 + held_count(held, "freezes") else 0  # a Streak Freeze covers each missed day


def rep_stats_embed(guild, member):
    """The private 'My Stats' card under the leaderboards."""
    gid = guild.id
    place = aura_rank(guild, member.id)
    gained, lost = auras.window(gid, 7).get(member.id, (0, 0))
    lines = [f"{emo('aura')} {member.mention} has **{auras.balance(gid, member.id):,}** reputation".lstrip(),
             f"Server rank: `#{place}`" if place else "Server rank: `Unranked`",
             f"Last 7 days: **{gained - lost:+,}** reputation"]
    days = streak_now(auras.data.get(str(gid), {}).get(str(member.id), {}))
    if days:
        lines.append(f"Daily streak: **{days}** day{'s' if days != 1 else ''}")
    fam = families.by_member(gid, member.id)
    if fam is not None:
        lines.append(f"Family: {fam_name(fam)}")
    embed = discord.Embed(description="\n".join(lines), color=theme())
    with_author(embed, member)
    return embed


class AuraBoardView(discord.ui.View):
    """Under the reputation leaderboard: page arrows (only enabled when there is more than one page), a range button that
    flips All Time <-> 7 Days, and My Stats (private). Anyone can press them. The embed has no name line on top."""

    def __init__(self, guild, viewer=None):
        super().__init__(timeout=300)
        self.guild = guild
        self.viewer = viewer
        self.mode = "all"
        self.index = 0
        self.message = None
        self.refresh_buttons()

    def build(self):
        return aura_board_embed(self.guild, self.mode, self.index, self.viewer)

    def refresh_buttons(self):
        pages = aura_board_pages(self.guild, self.mode)
        self.previous.disabled = self.following.disabled = pages <= 1
        # arrows = the clean pack arrows (icon only); All Time / 7 Days are plain text (no calendar emoji); My Stats keeps its emoji
        style_buttons(self, {"‹": "left", "›": "right", "My Stats": "mystats"}, icon_only=("‹", "›"))

    async def go(self, interaction, step):
        pages = aura_board_pages(self.guild, self.mode)
        self.index = (self.index + step) % pages
        await interaction.response.edit_message(embed=self.build(), view=self)

    @discord.ui.button(label="‹", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        await self.go(interaction, -1)

    @discord.ui.button(label="›", style=discord.ButtonStyle.secondary)
    async def following(self, interaction, button):
        await self.go(interaction, 1)

    @discord.ui.button(label="All Time", style=discord.ButtonStyle.secondary)
    async def switch(self, interaction, button):
        self.mode = "week" if self.mode == "all" else "all"
        self.index = 0
        button.label = "7 Days" if self.mode == "week" else "All Time"
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.build(), view=self)

    @discord.ui.button(label="My Stats", style=discord.ButtonStyle.secondary)
    async def my_stats(self, interaction, button):
        await interaction.response.send_message(embed=rep_stats_embed(self.guild, interaction.user), ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ---- the "goat" role: the bot makes it itself and gives it to the #1 person of the all-time reputation leaderboard ----
GOAT_ROLE_NAME = os.getenv("GOAT_ROLE_NAME", "Top 1#")  # the name of the role the bot CREATES (rename it in your server whenever you like: the bot finds it by its id)
GOAT_ROLE_FILE = os.path.join(DATA_DIR, "goat_roles.json")
GOAT_DELAY = 20          # seconds to wait after a reputation change before updating the role (many changes become one check)
_goat_pending = set()    # server ids with a check already scheduled
_goat_warned = set()     # server ids already warned about missing permissions (so the log isn't spammed)


class GoatRoleStore:
    """Which role is each server's top-reputation role, by its id: {server id: role id}. Remembering the id (not the name)
    is what makes renaming the role in the server safe: before this the bot looked the role up by its name, so after
    "goat" was renamed it didn't find it, made a new "goat" role and the old #1 kept the renamed one."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = {str(k): int(v) for k, v in loaded.items()}
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("Could not read %s, the goat role is looked up by name again", path)

    def get(self, gid):
        return self.data.get(str(gid))

    def set(self, gid, role_id):
        self.data[str(gid)] = int(role_id)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)


goat_roles = GoatRoleStore(GOAT_ROLE_FILE)


def find_goat_role(guild):
    """This server's top-reputation role: the one the bot remembers (whatever it is called now), otherwise a role named
    GOAT_ROLE_NAME (which is then remembered). None when there isn't one."""
    role_id = goat_roles.get(guild.id)
    role = guild.get_role(role_id) if role_id else None
    if role is None:
        role = discord.utils.find(lambda r: r.name.lower() == GOAT_ROLE_NAME.lower() and not r.managed and not r.is_default(), guild.roles)
        if role is not None:
            goat_roles.set(guild.id, role.id)
    return role


class ShopSettings:
    """Small per-server settings of the shop and role code, saved in shop_settings.json: {server id: {"staff": role id, "soul": role id}}.
    "staff" = the role the Top 1, Best family and family roles sit under, "soul" = the role the custom roles sit under.
    Only needed when the role can't be found by its name."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = {str(g): {k: int(v) for k, v in d.items() if isinstance(v, int)} for g, d in loaded.items() if isinstance(d, dict)}
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("Could not read %s, the Staff and Soul roles are found by name again", path)

    def get(self, gid, key):
        return self.data.get(str(gid), {}).get(key)

    def set(self, gid, key, role_id):
        """role_id None = forget the setting."""
        mine = self.data.setdefault(str(gid), {})
        if role_id is None:
            mine.pop(key, None)
        else:
            mine[key] = int(role_id)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)


shop_settings = ShopSettings(os.path.join(DATA_DIR, "shop_settings.json"))


def staff_anchor(guild):
    """The role the Top 1, Best family and family roles sit right under. In this order: the role picked with `.roles staff @role`,
    a role named Staff (any font or emojis), a role with the word "staff" in its name (the highest one), the lowest of the staff
    rank roles (own ... Partnering). None when the server has none of these."""
    pinned = shop_settings.get(guild.id, "staff")
    role = guild.get_role(pinned) if pinned else None
    if role is not None:
        return role
    role = next((r for r in guild.roles if role_key(r.name) == "staff"), None)
    if role is not None:
        return role
    named = [r for r in guild.roles if "staff" in role_key(r.name).split() and not r.managed and not r.is_default()]
    if named:
        return max(named, key=lambda r: r.position)
    ranks = [find_role(guild, *names) for _label, names in STAFF_RANKS]
    ranks = [r for r in ranks if r is not None]
    return min(ranks, key=lambda r: r.position) if ranks else None


goat_anchor_role = staff_anchor  # the old name


def family_group_ids(guild):
    """The roles the bot keeps under the Staff (or base) role: every family role, Best family and Top 1. They may sit next to each
    other under the anchor, so none of them keeps pushing the others around."""
    g = families.guild(guild.id)
    ids = {f.get("role") for f in families.all(guild.id)}
    ids.add(g.get("best_role"))
    ids.add(goat_roles.get(guild.id))
    return {int(i) for i in ids if i}


async def place_goat_role(guild, role, fresh=False):
    """Put the Top 1 role right under the Staff role. Runs every time the role is synced (not only when it is made), with the
    positions Discord has RIGHT NOW. Returns None when it is in place, else a sentence saying why it can't be."""
    anchor = staff_anchor(guild)
    if anchor is None:
        return "I can't find a Staff role. Pick it with the roles command: `.roles staff @role`"
    return await roleplace.place_under(guild, role, anchor, family_group_ids(guild), "The top reputation role sits under the Staff role", fresh)


role_problems = {}  # (server id, what) -> the last problem found while placing a role, shown by the .roles check


def note_problem(gid, what, problem):
    if problem:
        role_problems[(gid, what)] = problem
    else:
        role_problems.pop((gid, what), None)


async def sync_goat_role(guild, fresh=False):
    """Make sure the goat role exists (created by the bot), only the all-time #1 has it, and it sits under the Staff role.
    Returns a sentence about what is wrong, or None."""
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        if guild.id not in _goat_warned:
            _goat_warned.add(guild.id)
            log.info("Can't manage the goat role in %s: the bot needs Manage Roles", guild)
        return "I need the Manage Roles permission."
    rows = aura_board_rows(guild, "all")
    winner = guild.get_member(rows[0][0]) if rows and rows[0][1] > 0 else None
    role = find_goat_role(guild)
    if role is None:
        if winner is None:
            return None  # nobody to give it to yet, so don't create an empty role
        try:
            role = await guild.create_role(name=GOAT_ROLE_NAME, colour=discord.Colour.gold(), reason="Top of the reputation leaderboard")
        except discord.HTTPException:
            log.info("Could not create the goat role in %s", guild, exc_info=True)
            return "Discord refused to create the Top 1 role."
        goat_roles.set(guild.id, role.id)  # from now on it is found by its id, whatever it gets renamed to
        await asyncio.sleep(0.5)
    problem = await place_goat_role(guild, role, fresh)
    note_problem(guild.id, "goat", problem)
    if role >= me.top_role:
        if guild.id not in _goat_warned:
            _goat_warned.add(guild.id)
            log.info("The goat role in %s is above the bot's role, so it can't hand it out", guild)
        return problem or "The Top 1 role is above my highest role, so I can't give it out."
    try:
        for member in list(role.members):
            if winner is None or member.id != winner.id:
                await member.remove_roles(role, reason="No longer the top of the reputation leaderboard")
        if winner is not None and role not in winner.roles:
            await winner.add_roles(role, reason="Top of the reputation leaderboard")
    except discord.HTTPException:
        log.info("Could not update the goat role in %s", guild, exc_info=True)
        return problem or "Discord refused to change who has the Top 1 role."
    return problem


def schedule_goat_role(gid, delay=GOAT_DELAY):
    """Check the goat role soon. Called whenever someone's reputation changes and at start-up."""
    if gid in _goat_pending:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return

    async def run():
        try:
            await asyncio.sleep(delay)
            guild = bot.get_guild(int(gid))
            if guild is not None:
                await sync_goat_role(guild)
        except Exception:
            log.exception("The goat role check failed for %s", gid)
        finally:
            _goat_pending.discard(gid)
    _goat_pending.add(gid)
    asyncio.create_task(run())


@bot.hybrid_command(name="goatrole", description="Pick the role the #1 on the reputation leaderboard gets")
@app_commands.describe(role="The role to give the #1 person (leave it empty to see the current one)")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def goatrole_command(ctx, role: discord.Role = None):
    """`,goatrole @top 1#`: the bot gives THIS role to whoever is #1 and takes it from everybody else, whatever you rename it to."""
    guild = ctx.guild
    current = find_goat_role(guild)
    if role is None:
        if current is not None:
            await ctx.send(embed=status_embed(ctx, "ok", f"The top reputation role is {current.mention}. Change it with `goatrole @role`."))
        else:
            await ctx.send(embed=status_embed(ctx, "warn", f"There is no top reputation role yet. The bot makes one called **{GOAT_ROLE_NAME}** when someone gets reputation, or pick your own with `goatrole @role`."))
        return
    me = guild.me
    if role.is_default() or role.managed:
        await ctx.send(embed=status_embed(ctx, "warn", "Pick a normal role (not @everyone, and not a role that belongs to a bot)."))
        return
    if me is None or role >= me.top_role:
        await ctx.send(embed=status_embed(ctx, "warn", f"{role.mention} is above my highest role, so I can't give it out. Move my role above it in Server Settings, Roles."))
        return
    goat_roles.set(guild.id, role.id)
    extra = ""
    if current is not None and current.id != role.id and current < me.top_role:
        try:  # the role used before is no longer handled by the bot, so it is taken off whoever still has it
            for member in list(current.members):
                await member.remove_roles(current, reason="The top reputation role was changed")
            extra = f" I took {current.mention} off everyone who had it."
        except discord.HTTPException:
            log.info("Could not clean up the old goat role in %s", guild, exc_info=True)
    schedule_goat_role(guild.id, delay=1)
    await ctx.send(embed=status_embed(ctx, "ok", f"{role.mention} is now the top reputation role. The #1 gets it in a moment and everyone else loses it.{extra}"))


@bot.hybrid_group(name="reputation", aliases=["rep"], invoke_without_command=True, description="Show someone's reputation and rank", fallback="show")
@commands.guild_only()
async def aura_group(ctx, member: discord.Member = None):
    """Someone's reputation and their rank in this server (yours by default)."""
    member = member or ctx.author
    gid = ctx.guild.id
    place = aura_rank(ctx.guild, member.id)
    lines = [f"{emo('aura')} {member.mention} has **{auras.balance(gid, member.id):,}** reputation".lstrip(),
             f"Server rank: `#{place}`" if place else "Server rank: `Unranked`"]
    fam = families.by_member(gid, member.id)
    if fam is not None:
        lines.append(f"Family: {fam_name(fam)}")
    t = current_title(auras.items_of(gid, member.id))
    file = await title_file(t) if t else None  # the animated name plate (drawn once, then only sent)
    if t and file is None:  # no Pillow / drawing failed: show it as text
        lines.append(titlecard.plain_title(t["text"]))
    embed = discord.Embed(description="\n".join(lines), color=theme())
    with_author(embed, ctx.author)
    if file is not None:
        embed.set_image(url="attachment://title.gif")
        await ctx.send(embed=embed, file=file)
    else:
        await ctx.send(embed=embed)


@aura_group.command(name="top", aliases=["leaderboard", "lb"], description="The reputation leaderboard")
@commands.guild_only()
async def aura_top(ctx):
    """The reputation leaderboard (top 10)."""
    if not aura_board_rows(ctx.guild, "all"):
        await ctx.send(embed=status_embed(ctx, "warn", f"Nobody has reputation yet. Start with `{prefix_for('daily')}daily`."))
        return
    view = AuraBoardView(ctx.guild, ctx.author)
    view.message = await ctx.send(embed=view.build(), view=view)


@aura_group.command(name="give", description="Give some of your reputation to a member of your family")
@app_commands.describe(member="A member of your family", amount="Any whole number you have, like 37 or 12345. Also: 2k, 1m, half, all")
@commands.guild_only()
@commands.cooldown(1, 10, commands.BucketType.user)
async def rep_give(ctx, member: discord.Member, amount: str):
    """Send any amount of YOUR reputation (up to what you have) to someone in YOUR family. It comes out of your balance, so nothing is created. It doesn't count for the family war."""
    gid = ctx.guild.id
    if member.bot or member.id == ctx.author.id:
        await ctx.send(embed=status_embed(ctx, "warn", "Pick another member to give reputation to."))
        return
    my_family = families.by_member(gid, ctx.author.id)
    if my_family is None:
        await ctx.send(embed=status_embed(ctx, "warn", "You can only give reputation inside a family, and you're not in one."))
        return
    if str(member.id) not in my_family["members"]:
        await ctx.send(embed=status_embed(ctx, "warn", f"You can only give reputation to members of your own family ({fam_name(my_family)})."))
        return
    mine = auras.balance(gid, ctx.author.id)
    value = parse_amount(amount, mine)
    if value is None or value < 1:
        await ctx.send(embed=status_embed(ctx, "warn", "Give a whole number of reputation, like `37` or `12345` (`2k`, `half` and `all` work too)."))
        return
    if mine < value:
        await ctx.send(embed=status_embed(ctx, "warn", f"You only have **{mine:,}** reputation."))
        return
    auras.add(gid, ctx.author.id, -value)  # no await between the check above and these two lines, so it can't be done twice at once
    auras.add(gid, member.id, value)
    left = auras.balance(gid, ctx.author.id)
    await ctx.send(content=member.mention,
                   embed=status_embed(ctx, "ok", f"gave {member.mention} **{value:,}** reputation. You now have **{left:,}** reputation."),
                   allowed_mentions=discord.AllowedMentions(users=[member]))


@bot.hybrid_command(name="daily", description="Claim your free daily reputation")
@commands.guild_only()
async def daily(ctx):
    """Free reputation once a day (UTC). Claiming on consecutive days builds a streak and a bigger bonus."""
    gid, uid = ctx.guild.id, ctx.author.id
    u = auras.user(gid, uid)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    if u["daily_day"] == today.isoformat():
        tomorrow = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time.min, datetime.timezone.utc)
        await ctx.send(embed=status_embed(ctx, "cooldown", f"You already claimed today, come back <t:{int(tomorrow.timestamp())}:R>."))
        return
    try:
        last = datetime.date.fromisoformat(u["daily_day"]) if u["daily_day"] else None
    except ValueError:
        last = None
    freezes = held_count(auras.items_of(gid, uid), "freezes")
    u["streak"], used = shopkit.streak_after(last, today, int(u["streak"] or 0), freezes)  # missed days use up Streak Freezes
    if used:
        auras.items(gid, uid)["freezes"] = freezes - used
    u["daily_day"] = today.isoformat()
    amount = DAILY_AURA + min(u["streak"] - 1, DAILY_STREAK_MAX) * DAILY_STREAK_BONUS
    extra = 0
    dboosts = held_count(auras.items_of(gid, uid), "dboosts")
    if dboosts > 0:  # a Daily Boost doubles this daily (up to DAILYBOOST_CAP extra) and is used up
        extra = shopkit.boost_extra(amount, DAILYBOOST_CAP)
        auras.items(gid, uid)["dboosts"] = dboosts - 1
    total = give_aura(gid, uid, amount + extra)
    quest_progress(gid, uid, "daily")
    days = u["streak"]
    saved = f" {used} Streak Freeze{'s' if used != 1 else ''} kept your streak alive." if used else ""
    bonus = f" (**+{extra:,}** from your Daily Boost)" if extra else ""
    await ctx.send(embed=status_embed(
        ctx, "ok", f"claimed your daily **{amount + extra:,}** reputation{bonus}. Streak: **{days}** day{'s' if days != 1 else ''}.{saved} You now have **{total:,}** reputation."))


duel_busy = set()  # ids of people with a duel waiting for an answer
duel_quest_seen = {}  # (server, lower id, higher id) -> the UTC day a duel between these two last counted for a quest


def duel_counts_for_quests(gid, a, b, amount):
    """A duel win only counts for the duel quests when real reputation was at stake and these two people haven't already
    counted a duel today. Without this, two accounts could duel each other for 1 reputation, win a quest each and keep
    collecting the quest rewards (reputation out of nothing)."""
    if amount < GAMBLE_MIN_BET:
        return False
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    key = (gid, min(a, b), max(a, b))
    if duel_quest_seen.get(key) == today:
        return False
    if len(duel_quest_seen) > 5000:
        for k in [k for k, day in duel_quest_seen.items() if day != today]:
            del duel_quest_seen[k]
    duel_quest_seen[key] = today
    return True


class DuelView(discord.ui.View):
    """Accept / Decline under a duel challenge. Only the challenged member can press them."""

    def __init__(self, guild_id, challenger, opponent, amount):
        super().__init__(timeout=DUEL_SECONDS)
        self.guild_id, self.challenger, self.opponent, self.amount = guild_id, challenger, opponent, amount
        self.message = None
        self.done = False  # set by the first press / the timeout: nothing else can settle this duel afterwards
        style_buttons(self, {"Accept": "check", "Decline": "cross"})

    async def interaction_check(self, interaction):
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", "This duel isn't for you."), ephemeral=True)
            return False
        return True

    def finish(self):
        duel_busy.discard(self.challenger.id)
        duel_busy.discard(self.opponent.id)
        self.stop()

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        if self.done:  # a second press of the button (or one after Decline / the timeout) does nothing
            await interaction.response.defer()
            return
        self.done = True
        self.finish()
        gid, amount = self.guild_id, self.amount
        if auras.balance(gid, self.challenger.id) < amount or auras.balance(gid, self.opponent.id) < amount:
            embed = discord.Embed(
                description=status_text("warn", "The duel is off: one of you doesn't have enough reputation anymore."), color=WARN_COLOR)
        else:
            winner, loser = GAMBLE_RNG.sample([self.challenger, self.opponent], 2)  # a fair coin flip
            insured = duel_insure(gid, (self.challenger.id, self.opponent.id), amount)  # Duel Insurance: uses up one duel of each insured player
            pay = gambling.half_loss(amount) if loser.id in insured else amount  # an insured loser only loses half; the winner gets the same half (nothing is created)
            # Not through give_aura: a duel only moves reputation from one person to another, so it must not add war
            # points for the winner's family (two accounts could duel each other all day and farm the family war).
            auras.add(gid, winner.id, pay)
            auras.add(gid, loser.id, -pay)
            if duel_counts_for_quests(gid, winner.id, loser.id, amount):
                quest_progress(gid, winner.id, "duel_win")
            line = f"{emo('trophy', chr(0x1F3C6))} {winner.mention} won the duel and took **{pay:,}** reputation from {loser.mention}."
            if pay < amount:
                line += "\n" + small(f"Duel Insurance: the lost bet of {amount:,} was cut to half")
            embed = discord.Embed(description=line, color=OK_COLOR)
        await interaction.response.edit_message(content=None, embed=embed, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction, button):
        if self.done:
            await interaction.response.defer()
            return
        self.done = True
        self.finish()
        embed = discord.Embed(description=status_text("warn", f"{self.opponent.mention} declined the duel."), color=WARN_COLOR)
        await interaction.response.edit_message(content=None, embed=embed, view=None)

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        self.finish()
        if self.message is not None:
            embed = discord.Embed(description=status_text("warn", "Nobody answered, so the duel expired."), color=WARN_COLOR)
            try:
                await self.message.edit(content=None, embed=embed, view=None)
            except discord.HTTPException:
                pass


@bot.hybrid_command(name="duel", description="Bet reputation on a coin flip against someone")
@commands.guild_only()
@commands.cooldown(1, DUEL_COOLDOWN, commands.BucketType.user)
async def duel(ctx, member: discord.Member, amount: str):
    """Bet reputation on a coin flip. The other person has to accept; the winner takes it all."""
    gid = ctx.guild.id
    if member.bot or member.id == ctx.author.id:
        await ctx.send(embed=status_embed(ctx, "warn", "Pick another member to duel."))
        return
    mine, theirs = auras.balance(gid, ctx.author.id), auras.balance(gid, member.id)
    bet = parse_amount(amount, mine)
    if bet is None or bet < 1:
        await ctx.send(embed=status_embed(ctx, "warn", "Bet a whole number of reputation, like `50` or `all`."))
        return
    if mine < bet:
        await ctx.send(embed=status_embed(ctx, "warn", f"You only have **{mine:,}** reputation."))
        return
    if theirs < bet:
        await ctx.send(embed=status_embed(ctx, "warn", f"{member.mention} only has **{theirs:,}** reputation."))
        return
    if ctx.author.id in duel_busy or member.id in duel_busy:
        await ctx.send(embed=status_embed(ctx, "warn", "One of you already has a duel waiting for an answer."))
        return
    duel_busy.update((ctx.author.id, member.id))
    view = DuelView(gid, ctx.author, member, bet)
    embed = discord.Embed(
        description=(f"{emo('duel')} {ctx.author.mention} challenged {member.mention} to a duel for **{bet:,}** reputation.\n"
                     f"The winner takes it all. {member.mention}, do you accept?").lstrip(),
        color=theme())
    if bet <= BOOST_MAX_BET:  # tell the other player before they accept
        covered = [m.mention for m in (ctx.author, member) if held_count(auras.items_of(gid, m.id), "duel_ins_games") > 0]
        if covered:
            embed.description += "\n" + small(f"{' and '.join(covered)} {'has' if len(covered) == 1 else 'have'} Duel Insurance: if they lose, only half of the bet moves.")
    try:
        view.message = await ctx.send(content=member.mention, embed=embed, view=view,
                                      allowed_mentions=discord.AllowedMentions(users=[member]))
    except discord.HTTPException:
        view.finish()
        raise


# --------------------------------------------------------------------------
# ,bottle: kiss, slap, marry. The bot picks 3 random ACTIVE members, the player secretly decides who gets what
# (the choices are only shown to the player, in a message nobody else can see), then the result is announced. Choosing the same person twice gets a warning (only the player sees it).
# Just for fun: no reputation is won or lost.
# "Active" = people who wrote in THIS channel in the last 30 minutes. The bot remembers them as they write and, when its memory
# is empty (after a restart), reads the channel's last messages. Members shown as online count too, but that only works when the
# Presence intent is on (it is off by default). There is NO "anyone in the server" fallback on purpose: people who aren't around
# show up as @unknown-user in Discord, so with fewer than 3 active people the command says so instead of showing strangers.
# --------------------------------------------------------------------------
BOTTLE_SECONDS = 120                                       # how long the player has to choose
recent_chatters = {}  # channel id -> {user id: time of their last message}
bottle_seeded = {}    # channel id -> when the channel's last messages were last read to fill recent_chatters


@bot.listen("on_message")
async def track_recent_chatters(message):
    if message.guild is None or message.author.bot:
        return
    now = time.time()
    seen = recent_chatters.setdefault(message.channel.id, {})
    seen[message.author.id] = now
    if len(seen) > 500:  # keep it small: drop the old ones
        for uid in [u for u, t in seen.items() if t < now - BOTTLE_ACTIVE_MINUTES * 60]:
            del seen[uid]
    if len(recent_chatters) > 2000:  # forget the channels nobody has written in lately
        cutoff = now - BOTTLE_ACTIVE_MINUTES * 60
        for cid in [c for c, d in recent_chatters.items() if not d or max(d.values()) < cutoff]:
            del recent_chatters[cid]


async def bottle_seed_chatters(channel):
    """Fill recent_chatters from the channel's last messages. The memory is empty after a restart, and without this the bot would
    think nobody is active. Skipped when the memory already knows enough people; reads at most once a minute per channel."""
    now = time.time()
    cutoff = now - BOTTLE_ACTIVE_MINUTES * 60
    seen = recent_chatters.setdefault(channel.id, {})
    if sum(1 for t in seen.values() if t >= cutoff) >= 6 or now - bottle_seeded.get(channel.id, 0) < 60:
        return
    bottle_seeded[channel.id] = now
    if len(bottle_seeded) > 5000:
        bottle_seeded.clear()
    try:
        async for msg in channel.history(limit=100):  # newest first
            t = msg.created_at.timestamp()
            if t < cutoff:
                break
            if not msg.author.bot and t > seen.get(msg.author.id, 0):
                seen[msg.author.id] = t
    except (discord.HTTPException, AttributeError):  # no Read Message History permission, or a channel without history: use what we have
        pass


BOTTLE_MEMORY = 12    # how many people each player's latest bottles are remembered for (4 bottles), so the same faces don't come back
bottle_shown = {}     # (server id, player id) -> the ids shown to that player in their latest bottles (kept in memory only)


def bottle_pick(guild, author, channel):
    """3 random ACTIVE members for ,bottle, or None when fewer than 3 are active.
    Active = wrote in this channel in the last BOTTLE_ACTIVE_MINUTES, then members shown as online (needs the Presence intent).
    Never "anyone in the server": people who aren't around show up as @unknown-user. Everyone has to be able to see the channel.
    Anyone who was in this player's last few bottles is skipped while there are enough other people; if there aren't, the ones
    shown longest ago are used first, so a small group still rotates instead of repeating the same trio."""
    def ok(m):
        return not m.bot and m.id != author.id and channel.permissions_for(m).view_channel
    cutoff = time.time() - BOTTLE_ACTIVE_MINUTES * 60
    seen = recent_chatters.get(channel.id, {})
    groups = (  # each one is only built when the earlier ones didn't give 3 fresh people
        lambda: [m for m in (guild.get_member(u) for u, t in seen.items() if t >= cutoff) if m is not None and ok(m)],
        lambda: [m for m in guild.members if ok(m) and m.status != discord.Status.offline],
    )
    if len(bottle_shown) > 5000:  # keep it small
        bottle_shown.clear()
    shown = bottle_shown.setdefault((guild.id, author.id), deque(maxlen=BOTTLE_MEMORY))
    last_shown = {uid: i for i, uid in enumerate(shown)}  # bigger = shown more recently
    picked, used, stale = [], set(), []
    for build in groups:
        fresh = []
        for m in build():
            if m.id in used:
                continue
            used.add(m.id)
            (stale if m.id in last_shown else fresh).append(m)
        picked += random.sample(fresh, min(3 - len(picked), len(fresh)))
        if len(picked) == 3:
            break
    if len(picked) < 3:  # not enough new faces: use the ones this player saw longest ago
        stale.sort(key=lambda m: last_shown[m.id])
        picked += stale[:3 - len(picked)]
    if len(picked) < 3:
        return None
    random.shuffle(picked)
    shown.extend(m.id for m in picked)
    return picked


def bottle_icon(name):
    """The drawn icon (bottle / kiss / slap / ring) followed by a space, or nothing when the emoji isn't available (no unicode fallback on purpose)."""
    icon = emo(name)
    return icon + " " if icon else ""


def bottle_result_embed(player, kiss, slap, marry):
    lines = [f"-# {player.mention} picked",
             "",
             f"{bottle_icon('kiss')}**Kissed** {kiss.mention}",
             f"{bottle_icon('slap')}**Slapped** {slap.mention}",
             f"{bottle_icon('ring')}**Married** {marry.mention}"]
    return discord.Embed(description="\n".join(lines), color=theme())


class BottleModal(discord.ui.Modal):
    """The form the Pick button opens (like the Bot Style form): three dropdowns, one per person, with the 3 members in each.
    A form can't hide a person already chosen in another dropdown, so choosing the same person twice gets a warning instead."""

    def __init__(self, parent, warning=None):
        super().__init__(title="Spin the bottle")
        self.parent = parent
        self.warning = warning  # the "can't pick the same person twice" message this form was opened from (removed again afterwards)
        def options():
            return [discord.SelectOption(label=m.display_name[:100], value=str(m.id)) for m in parent.people]
        self.kiss = discord.ui.Select(placeholder="Choose someone", options=options(), required=True)
        self.slap = discord.ui.Select(placeholder="Choose someone", options=options(), required=True)
        self.marry = discord.ui.Select(placeholder="Choose someone", options=options(), required=True)
        self.add_item(discord.ui.Label(text="Kiss", description="Who do you want to kiss?", component=self.kiss))
        self.add_item(discord.ui.Label(text="Slap", description="Who do you want to slap?", component=self.slap))
        self.add_item(discord.ui.Label(text="Marry", description="Who do you want to marry?", component=self.marry))

    async def on_submit(self, interaction):
        parent = self.parent
        if parent.done:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", "This bottle already stopped. Spin a new one."), ephemeral=True)
            return
        by_id = {str(m.id): m for m in parent.people}
        chosen = [by_id.get(sel.values[0]) if sel.values else None for sel in (self.kiss, self.slap, self.marry)]
        if None in chosen or len({m.id for m in chosen}) < 3:
            # only the player sees this warning; the button opens the form again
            retry = BottleRetryView(parent)
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", "You can't pick the same person twice. Choose a different person for Kiss, Slap and Marry."),
                view=retry, ephemeral=True)
            retry.warning = interaction
            await drop_ephemeral(self.warning)  # the warning from the earlier try is replaced by this one
            return
        await interaction.response.defer()
        await parent.finish(chosen, interaction)
        await drop_ephemeral(self.warning)


async def drop_ephemeral(interaction):
    """Remove the private message an earlier interaction sent (the old warning). Does nothing if it is already gone."""
    if interaction is not None:
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass


class BottleRetryView(discord.ui.View):
    """Shown (only to the player) with the "can't pick the same person twice" warning: one button opens the form again."""

    def __init__(self, parent):
        super().__init__(timeout=BOTTLE_SECONDS)
        self.parent = parent
        self.warning = None  # the interaction that sent the warning, so the next form can remove it

    @discord.ui.button(label="Pick again", style=discord.ButtonStyle.primary)
    async def again(self, interaction, button):
        if self.parent.done:
            await interaction.response.defer()
            return
        await interaction.response.send_modal(BottleModal(self.parent, self.warning))


class BottleView(discord.ui.View):
    """The public message: shows the 3 people and a Pick button only the player can use, which opens the form."""

    def __init__(self, player, people):
        super().__init__(timeout=BOTTLE_SECONDS)
        self.player, self.people = player, people
        self.message = None
        self.done = False
        icon = find_emoji("bottle")
        if icon is not None:
            self.choose.emoji = icon

    async def interaction_check(self, interaction):
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", "This bottle isn't yours. Spin your own."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Pick", style=discord.ButtonStyle.primary)
    async def choose(self, interaction, button):
        if self.done:
            await interaction.response.defer()
            return
        await interaction.response.send_modal(BottleModal(self))

    async def finish(self, chosen, interaction):
        """The form was submitted with 3 different people: post the result as a brand-new message at the bottom of the chat,
        then delete the Pick message."""
        self.done = True
        self.stop()
        result = bottle_result_embed(self.player, *chosen)
        channel = self.message.channel if self.message is not None else interaction.channel
        # A plain message in the channel, NOT interaction.followup: Discord shows a followup as a reply to the Pick message, so
        # after that message is deleted the result said "Original message was deleted" above it.
        try:
            await channel.send(embed=result, allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, AttributeError):  # couldn't post a new one: turn the first message into the result instead of losing it
            if self.message is not None:
                try:
                    await self.message.edit(content=None, embed=result, view=None, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    pass
            return
        if self.message is not None:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        if self.done:
            return
        self.done = True
        if self.message is not None:
            embed = discord.Embed(description=status_text("warn", f"{self.player.mention} didn't choose in time, so the bottle stopped."), color=WARN_COLOR)
            try:
                await self.message.edit(content=None, embed=embed, view=None, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass


@bot.hybrid_command(name="bottle", description="Kiss, slap or marry 3 random members")
@commands.guild_only()
@commands.cooldown(1, BOTTLE_COOLDOWN, commands.BucketType.user)
async def bottle(ctx):
    """The bot picks 3 random active members. You secretly choose who to kiss, slap and marry, then it announces the result."""
    await bottle_seed_chatters(ctx.channel)
    people = bottle_pick(ctx.guild, ctx.author, ctx.channel)
    if people is None:
        ctx.command.reset_cooldown(ctx)
        await ctx.send(embed=status_embed(ctx, "warn", f"I need at least 3 other people who wrote in this channel in the last {BOTTLE_ACTIVE_MINUTES} minutes. Try again when the chat is more active."))
        return
    names = "\n".join(m.mention for m in people)
    embed = discord.Embed(
        description=(f"{bottle_icon('bottle')}**Spin the bottle**\n-# {ctx.author.mention} spun it and it picked\n\n{names}\n\n"
                     f"-# Press Pick to decide who you kiss, slap and marry (a different person for each one). Only you can see your picks."),
        color=theme())
    view = BottleView(ctx.author, people)
    view.message = await ctx.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


# --------------------------------------------------------------------------
# Gambling with reputation: ,coinflip ,slots and ,blackjack. Always against the bot, nobody else's reputation is touched.
# What keeps it honest (details and the tests are in CHANGES.md, Step 42):
#   * every random number comes from gambling.RNG (the operating system's generator, so results can't be predicted)
#   * a bet is checked and taken in ONE step with no `await` in between, so two commands sent at the same moment can't
#     both spend the same reputation
#   * coinflip and slots are settled BEFORE the animation is shown. Blackjack takes the stake out of the balance when the
#     game starts (it is saved as "held" so a restart gives it back) and pays out exactly once when the game ends
#   * a bet is never below the minimum, never above the maximum and never above what you have: nobody bets into debt
#   * gambling winnings do NOT count for the family war or for quests
# Every gambling embed is the same minimal dark card, Bleed style: big emojis (cards, coin, reels) on top, one short result
# line ("@you won +500"), and a tiny grey line under it with the bet and the balance. No fields, no author line, no footer.
# --------------------------------------------------------------------------
COINFLIP_SPIN_SECONDS = 1.0  # how long the tossed coin shows before the result (only when the animated coin emoji exists)
# Discord draws an emoji as big as the text around it, and emojis inside an embed are small. A line that starts with "# " is a
# markdown header (big text), so the emojis on it are drawn about twice as big. Set GAMBLE_BIG_EMOJI=0 in .env to turn it off.
GAMBLE_BIG_EMOJI = os.getenv("GAMBLE_BIG_EMOJI", "1").strip().lower() not in ("0", "false", "no", "off")
gamble_last = {}  # (server id, user id) -> when they last placed a bet (coinflip, slots and blackjack share the cooldown)
bj_games = {}     # (server id, user id) -> their running blackjack game (one at a time per person and server)

SLOT_EMOJI = {  # slot symbol -> the name of its drawn emoji (the plain symbol is shown until the emoji exists)
    "\U0001F352": "s_cherry", "\U0001F34B": "s_lemon", "\U0001F347": "s_grape",
    "\U0001F514": "s_bell", "\U0001F48E": "s_diamond", "7\ufe0f\u20e3": "s_seven",
}


def slots_payback():
    """The share of the money bet that slots pay back on average (about 0.955 with the default table)."""
    total = sum(w for _s, w, _m in SLOTS_SYMBOLS)
    paid = 0.0
    for a, wa, ma in SLOTS_SYMBOLS:
        for b, wb, _mb in SLOTS_SYMBOLS:
            for c, wc, _mc in SLOTS_SYMBOLS:
                p = (wa / total) * (wb / total) * (wc / total)
                paid += p * slots_payout([a, b, c])
    return paid


def gnote_for(user, kind, text):
    """A refusal or short note in the dark gambling style (the bar has no color)."""
    return discord.Embed(description=status_text(kind, f"{user.mention}: {text}"), color=theme())


def gnote(ctx, kind, text):
    return gnote_for(ctx.author, kind, text)


def gamble_embed(user, description):
    """The dark card all gambling results use. No author line and no fields: the text carries everything.
    (`user` isn't used any more, it stays so the calls don't change. To get the name + avatar line back, return
    with_author(discord.Embed(description=description, color=theme()), user).)"""
    return discord.Embed(description=description, color=theme())


def big(text, level=1):
    """A line of emojis drawn big: level 1 = biggest ("# "), level 2 = a bit smaller ("## ")."""
    return f"{'#' * level} {text}" if GAMBLE_BIG_EMOJI else text


def small(text):
    """Tiny grey text under the result (Discord's "-# " subtext)."""
    return f"-# {text}"


def net_text(net):
    """+500 with the green arrow, \u2212500 with the red arrow."""
    if net > 0:
        return f"{emo('up', chr(0x25B2))} **+{net:,}**"
    if net < 0:
        return f"{emo('down', chr(0x25BC))} **\u2212{-net:,}**"
    return ""


def result_line(user, net, win_word="won", lose_word="lost"):
    """`@you won +500` / `@you lost -500`: the one line that says how it went."""
    word = win_word if net > 0 else (lose_word if net < 0 else "broke even")
    return f"{user.mention} **{word}** {net_text(net)}".rstrip()


def stats_line(bet, balance, *front):
    """The grey line at the bottom: `Heads \u00b7 Bet 500 \u00b7 Balance 12,345`."""
    return small(" \u00b7 ".join([*front, f"Bet {bet:,}", f"Balance {balance:,}"]))


def slots_win_chance():
    """The normal chance that a spin pays more than the bet (what a Luck Boost starts from)."""
    total = sum(w for _s, w, _m in SLOTS_SYMBOLS)
    chance = 0.0
    for a, wa, _ma in SLOTS_SYMBOLS:
        for b, wb, _mb in SLOTS_SYMBOLS:
            for c, wc, _mc in SLOTS_SYMBOLS:
                if slots_payout([a, b, c]) > 1:
                    chance += (wa / total) * (wb / total) * (wc / total)
    return chance


def slots_spin():
    """Three random symbols, the rarer ones come up less often."""
    return GAMBLE_RNG.choices([sym for sym, _w, _m in SLOTS_SYMBOLS], weights=[w for _s, w, _m in SLOTS_SYMBOLS], k=3)


def slots_payout(reels):
    """How many times the bet comes back for these three reels (0 = the bet is lost)."""
    a, b, c = reels
    if a == b == c:
        return next(m for sym, _w, m in SLOTS_SYMBOLS if sym == a)
    if a == b or b == c or a == c:
        return SLOTS_PAIR_PAYOUT
    return 0


try:  # one line in the log with the odds that are in force, so a typo in .env or in the table is easy to spot
    log.info("Gambling odds: coinflip %.1f%% to win, slots pay back %.1f%% of every bet, plinko pays back %.1f%% / %.1f%% / %.1f%% (low / medium / high), blackjack pays %d:%d",
             COINFLIP_WIN_CHANCE * 100, slots_payback() * 100, *(gambling.plinko_payback(r) * 100 for r in gambling.PLINKO_RISKS),
             *gambling.BLACKJACK_PAYS)
except Exception:
    log.exception("Could not work out the gambling odds")


class ChannelSwitch:
    """gambling_off.json: {server id: [channel ids where gambling is turned off]}."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = {str(k): [int(c) for c in v] for k, v in loaded.items() if isinstance(v, list)}
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("Could not read %s, gambling stays on everywhere", path)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def off_list(self, gid):
        return list(self.data.get(str(gid), []))

    def is_off(self, gid, channel):
        """True when gambling is off in this channel (a thread follows the channel it belongs to)."""
        off = self.data.get(str(gid))
        if not off:
            return False
        return channel.id in off or getattr(channel, "parent_id", None) in off

    def set(self, gid, channel_id, off):
        """Turn gambling off (True) or on (False) in a channel. Returns False when nothing changed."""
        ids = self.data.setdefault(str(gid), [])
        if off and channel_id not in ids:
            ids.append(channel_id)
        elif not off and channel_id in ids:
            ids.remove(channel_id)
        else:
            return False
        if not ids:
            del self.data[str(gid)]
        self.save()
        return True


gambling_off = ChannelSwitch(os.path.join(DATA_DIR, "gambling_off.json"))
_gambling_channel = os.getenv("GAMBLING_CHANNEL_ID", "1557045887572906014").strip()
GAMBLING_CHANNEL_ID = int(_gambling_channel) if _gambling_channel.isdigit() else 0  # where people are sent when gambling is off in their channel


def gambling_off_note(ctx):
    """The warning embed when gambling is turned off in this channel (.gambling off), else None."""
    if not gambling_off.is_off(ctx.guild.id, ctx.channel):
        return None
    where = ctx.guild.get_channel(GAMBLING_CHANNEL_ID) if GAMBLING_CHANNEL_ID else None
    hint = f" Use it in {where.mention} instead." if where is not None else ""  # only when that channel is in this server
    return gnote(ctx, "warn", f"Gambling is turned off in this channel.{hint}")


def gamble_start(ctx, amount):
    """Check a bet. Returns (bet, None) when it can be placed (the cooldown starts now), or (None, the warning embed).
    Nothing is taken from the balance here, and there is no `await`, so the caller can take it right away."""
    gid, uid = ctx.guild.id, ctx.author.id
    off = gambling_off_note(ctx)  # turned off here with .gambling off (checked first: no cooldown, nothing taken)
    if off is not None:
        return None, off
    now = time.time()
    left = gamble_last.get((gid, uid), 0) + GAMBLE_COOLDOWN - now
    if left > 0:
        return None, gnote(ctx, "cooldown", f"Slow down, try again in **{human_time(left)}**.")
    have = auras.balance(gid, uid)
    if have < GAMBLE_MIN_BET:
        return None, gnote(ctx, "warn", f"You need at least **{GAMBLE_MIN_BET:,}** reputation to gamble. You have **{have:,}**.")
    bet = parse_amount(amount, have)
    if bet is not None and str(amount).strip().lower() == "all":
        bet = min(bet, GAMBLE_MAX_BET)  # `all` quietly takes the biggest allowed bet instead of showing the limit
    if bet is None or bet < GAMBLE_MIN_BET:
        return None, gnote(ctx, "warn", f"Bet a whole number from **{GAMBLE_MIN_BET:,}** to **{GAMBLE_MAX_BET:,}**, like `50`, `2k` or `all`.")
    if bet > have:
        return None, gnote(ctx, "warn", f"You only have **{have:,}** reputation.")
    if bet > GAMBLE_MAX_BET:
        return None, gnote(ctx, "warn", f"The biggest bet is **{GAMBLE_MAX_BET:,}** reputation.")
    gamble_last[(gid, uid)] = now
    if len(gamble_last) > 2000:  # forget old cooldowns so the dict can't grow forever
        for key in [k for k, t in gamble_last.items() if now - t > 60]:
            del gamble_last[key]
    return bet, None


# ---- shop boosts: Luck Boost and Half Loss (bought in ,shop, used up one game at a time) ---------------------------------
# Luck Boost: while it lasts, coinflip / slots / plinko (never blackjack) win LUCK_WIN_CHANCE of the time. It only raises the
# odds, a game can still be lost. Half Loss: a lost bet only costs half. Each one is counted in games, and every game played
# with them active uses up one game of each. The rules themselves are in gambling.py.
def boosts_start(gid, uid, bet):
    """A game is being played now: use up one game of each active boost. Returns (luck, half), which of them apply to it.
    Only for bets up to BOOST_MAX_BET: a bigger bet is played normally and uses no game. That keeps the boosts from paying for
    themselves with huge bets (at 70% a boosted game is worth a lot more than its price when the bet is big).
    There is no `await` in here, so it can sit right after gamble_start without another command getting in between."""
    if bet > BOOST_MAX_BET:
        return False, False
    held = auras.items_of(gid, uid)
    luck = held_count(held, "luck_games") > 0
    half = held_count(held, "half_games") > 0
    if luck or half:
        mine = auras.items(gid, uid)
        if luck:
            mine["luck_games"] = held_count(mine, "luck_games") - 1
        if half:
            mine["half_games"] = held_count(mine, "half_games") - 1
        auras.save()
    return luck, half


def duel_insure(gid, uids, bet):
    """A duel is being settled: use up one duel of Duel Insurance for each of `uids` who has one. Returns the set of ids that were
    insured for this duel. Only for bets up to BOOST_MAX_BET (a bigger duel is played normally and uses nothing). No `await` in here."""
    if bet > BOOST_MAX_BET:
        return set()
    used = set()
    for uid in uids:
        if held_count(auras.items_of(gid, uid), "duel_ins_games") > 0:
            mine = auras.items(gid, uid)
            mine["duel_ins_games"] = held_count(mine, "duel_ins_games") - 1
            used.add(uid)
    if used:
        auras.save()
    return used


def vault_safe(gid, uid, balance):
    """How much of `balance` the Vault of this member keeps away from robbers (0 without a Vault)."""
    if balance <= 0 or held_count(auras.items_of(gid, uid), "vault") < 1:
        return 0
    return int(balance * VAULT_PERCENT / 100)


def boost_tags(gid, uid, luck, half, bet=0):
    """Words for the grey stats line, like `Luck Boost 4 left`. Empty when no boost was used (or a note when the bet was too big)."""
    held = auras.items_of(gid, uid)
    tags = []
    if not luck and not half and bet > BOOST_MAX_BET and (held_count(held, "luck_games") or held_count(held, "half_games")):
        tags.append(f"Boosts only work on bets up to {BOOST_MAX_BET:,}")
    if luck:
        tags.append(f"Luck Boost {held_count(held, 'luck_games')} left")
    if half:
        tags.append(f"Half Loss {held_count(held, 'half_games')} left")
    return tags


def luck_roll(natural):
    """With a Luck Boost: must this game be a win? `natural` = the game's normal win chance (a boost never lowers it)."""
    return gambling.luck_decides_win(GAMBLE_RNG, natural, LUCK_WIN_CHANCE)


# ---- coinflip -------------------------------------------------------------------------
COINFLIP_SIDES = {"h": "heads", "head": "heads", "heads": "heads", "t": "tails", "tail": "tails", "tails": "tails"}


@bot.hybrid_command(name="gambling", description="Turn gambling on or off in a channel")
@app_commands.describe(action="on, off or list (leave it out to see this channel)", channel="The channel (this one if you leave it out)")
@app_commands.choices(action=[app_commands.Choice(name="off", value="off"), app_commands.Choice(name="on", value="on"),
                              app_commands.Choice(name="list", value="list")])
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def gambling_command(ctx, action: str = None, channel: discord.TextChannel = None):
    """`.gambling off #casino`: ,coinflip ,slots ,plinko and ,blackjack stop working in that channel. `.gambling on` turns them back on."""
    gid = ctx.guild.id
    word = (action or "").strip().lower()
    word = {"disable": "off", "enable": "on", "show": "list", "status": "list"}.get(word, word)
    if word not in ("", "on", "off", "list"):
        await ctx.send(embed=status_embed(ctx, "warn", f"Use `{prefix_for('gambling')}gambling off [#channel]`, `{prefix_for('gambling')}gambling on [#channel]` or `{prefix_for('gambling')}gambling list`."))
        return
    target = channel or ctx.channel
    if isinstance(target, discord.Thread) and target.parent is not None:
        target = target.parent  # a thread follows its channel
    if word in ("on", "off"):
        changed = gambling_off.set(gid, target.id, word == "off")
        if word == "off":
            text = (f"Gambling is now **off** in {target.mention}." if changed
                    else f"Gambling was already off in {target.mention}.")
        else:
            text = (f"Gambling is now **on** in {target.mention}." if changed
                    else f"Gambling was already on in {target.mention}.")
        await ctx.send(embed=status_embed(ctx, "ok" if changed else "warn", text))
        return
    off = [c for c in gambling_off.off_list(gid) if ctx.guild.get_channel(c) is not None]  # skip deleted channels
    state = "off" if gambling_off.is_off(gid, target) else "on"
    lines = [f"Gambling is **{state}** in {target.mention}."]
    lines.append("Off in: " + ", ".join(f"<#{c}>" for c in off) if off else "It is on in every channel.")
    await ctx.send(embed=status_embed(ctx, "ok", "\n".join(lines)))


@bot.hybrid_command(name="coinflip", aliases=["cf", "flip"], description="Bet reputation on a coin flip against the bot")
@app_commands.describe(amount="How much to bet: 50, 2k, half or all", side="heads or tails (random if you leave it out)")
@commands.guild_only()
async def coinflip_command(ctx, amount: str, side: str = None):
    """`,coinflip 50 heads`: win and you get your bet again on top, lose and it's gone."""
    if side and amount.strip().lower() in COINFLIP_SIDES and side.strip().lower() not in COINFLIP_SIDES:
        amount, side = side, amount  # `,coinflip heads 50` works too
    pick = None
    if side:
        pick = COINFLIP_SIDES.get(side.strip().lower())
        if pick is None:
            await ctx.send(embed=gnote(ctx, "warn", "Pick **heads** or **tails**, or leave it out."))
            return
    bet, problem = gamble_start(ctx, amount)
    if problem is not None:
        await ctx.send(embed=problem)
        return
    pick = pick or GAMBLE_RNG.choice(("heads", "tails"))
    luck, half = boosts_start(ctx.guild.id, ctx.author.id, bet)  # Luck Boost / Half Loss: uses up one game of each that is active
    won = luck_roll(COINFLIP_WIN_CHANCE) if luck else GAMBLE_RNG.random() < COINFLIP_WIN_CHANCE
    landed = pick if won else ("tails" if pick == "heads" else "heads")
    net = bet if won else -(gambling.half_loss(bet) if half else bet)
    total = auras.add(ctx.guild.id, ctx.author.id, net)  # no await between the check in gamble_start and this line
    coin = emo("coin_h" if landed == "heads" else "coin_t", "`H`" if landed == "heads" else "`T`")
    tags = boost_tags(ctx.guild.id, ctx.author.id, luck, half, bet)
    text = f"{big(coin)}\n{result_line(ctx.author, net)}\n{stats_line(bet, total, landed.capitalize(), *tags)}"
    final = gamble_embed(ctx.author, text)
    toss = emo("coin_flip")
    if not toss:  # no animated coin yet: just the result
        await ctx.send(embed=final)
        return
    try:  # the result above is already settled; this is only the show
        message = await ctx.send(embed=gamble_embed(ctx.author, f"{big(toss)}\n{small('Flipping...')}"))
        await asyncio.sleep(COINFLIP_SPIN_SECONDS)
        await message.edit(embed=final)
        return
    except discord.HTTPException:
        pass
    try:
        await ctx.send(embed=final)
    except discord.HTTPException:
        log.warning("Could not show a coinflip result", exc_info=True)


# ---- slots ----------------------------------------------------------------------------
def reel_text(symbol):
    """One reel: its drawn emoji, or the spinning tile when the symbol is None."""
    if symbol is None:
        return emo("s_spinning") or emo("s_spin", "\u2753")  # the animated scrolling reel, else the still tile
    return emo(SLOT_EMOJI[symbol], symbol) if symbol in SLOT_EMOJI else symbol


def slots_card(user, shown, line=None, stats=None):
    """The reels, big, and under them either the result line + stats or just "Spinning..."."""
    reels = "  ".join(reel_text(s) for s in shown)
    text = big(reels)
    if line:
        text += f"\n{line}"
    if stats:
        text += f"\n{stats}"
    return gamble_embed(user, text)


@bot.hybrid_command(name="slots", aliases=["slot"], description="Spin the slot machine with reputation")
@app_commands.describe(amount="How much to bet: 50, 2k, half or all")
@commands.guild_only()
async def slots_command(ctx, amount: str):
    """`,slots 100`: three reels. Three matching pay 3x to 40x, any two matching pay SLOTS_PAIR_PAYOUT x, no match loses the bet."""
    bet, problem = gamble_start(ctx, amount)
    if problem is not None:
        await ctx.send(embed=problem)
        return
    luck, half = boosts_start(ctx.guild.id, ctx.author.id, bet)  # Luck Boost / Half Loss: uses up one game of each that is active
    if luck:  # decided first (win or not), then the reels are drawn the normal way from the spins that fit
        reels = gambling.draw_matching(slots_spin, lambda r: slots_payout(r) > 1, luck_roll(slots_win_chance()))
    else:
        reels = slots_spin()
    multiplier = slots_payout(reels)
    back = int(bet * multiplier)
    net = back - bet
    if half:
        net = gambling.soften_loss(net, bet)
    total = auras.add(ctx.guild.id, ctx.author.id, net)  # settled right now, before the first frame is even sent
    jackpot = multiplier >= 40 and reels[0] == reels[1] == reels[2]
    if multiplier == 0:
        what = "No match"
    elif reels[0] == reels[1] == reels[2]:
        what = f"Three of a kind {multiplier:g}x"
    else:
        what = f"Two match {multiplier:g}x"
    line = result_line(ctx.author, net, lose_word="lost")
    if jackpot:
        line = f"{emo('jackpot')} {ctx.author.mention} hit the **JACKPOT** {net_text(net)}".strip()
    final = slots_card(ctx.author, reels, line, stats_line(bet, total, what, *boost_tags(ctx.guild.id, ctx.author.id, luck, half, bet)))
    spin = small("Spinning...")
    frames = [slots_card(ctx.author, [None, None, None], spin),
              slots_card(ctx.author, [reels[0], None, None], spin),
              slots_card(ctx.author, [reels[0], reels[1], None], spin)]
    message = None
    try:
        message = await ctx.send(embed=frames[0])
        for frame in frames[1:]:
            await asyncio.sleep(SLOTS_SPIN_SECONDS)
            await message.edit(embed=frame)
        await asyncio.sleep(SLOTS_SPIN_SECONDS)
        await message.edit(embed=final)
        return
    except discord.HTTPException:
        pass  # a frame couldn't be shown (message deleted, rate limit...): the result below is still shown
    try:
        await ctx.send(embed=final)
    except discord.HTTPException:
        log.warning("Could not show a slots result", exc_info=True)


# ---- plinko ---------------------------------------------------------------------------
# ,plinko opens ONE card (a Components V2 container, like the shop) with the bet, the risk and the buttons
#   [Set Bet] [Low] [Medium] [High] [Start]
# Start plays the drop in the same message: first a drawn board with the ball falling peg by peg (an animated image, see
# plinko_render.py), then the board again with the slot it landed in plus the result line, then the card goes back to the panel
# (with the result under the risk) so the next drop is one press away. The money is settled the moment Start is pressed, the
# pictures are only the show. The rules and payouts are in gambling.py (PLINKO_TABLES), they are not touched here.
PLINKO_TIMEOUT = 180                                              # seconds without a press before the buttons are switched off
PLINKO_SHOW_SECONDS = 2.5                                         # how long the finished board stays before the panel comes back


def plinko_money():
    """The reputation icon (the same emoji as in ,reputation and the shop) for the amounts in the plinko texts; `rep` if the emoji is missing."""
    return emo("aura", "rep")


PLINKO_SLOTS = gambling.PLINKO_ROWS + 1


def plinko_gallery(url):
    """A media gallery with one picture (an `attachment://name` of the same message)."""
    item = discord.MediaGalleryItem(url)
    try:
        return discord.ui.MediaGallery(item)
    except TypeError:  # some discord.py versions want the items as one list
        return discord.ui.MediaGallery([item])


def plinko_result_text(bet, net, multiplier):
    """`📉 Landed in 0.3x. Returned 4 💸, net loss 12 💸.` (a win says `📈` and `net profit`)."""
    won = net > 0
    chart = "\U0001F4C8" if won else "\U0001F4C9"  # chart going up / down
    return (f"{chart} Landed in `{multiplier:g}x`. Returned `{bet + net:,}` {plinko_money()}, "
            f"net {'profit' if won else 'loss'} `{abs(net):,}` {plinko_money()}.")


class PlinkoBetModal(discord.ui.Modal, title="Plinko bet"):
    """The form behind Set Bet: 500, 2k, half or all."""
    amount = discord.ui.TextInput(label="Bet", placeholder="50, 2k, half or all", min_length=1, max_length=20)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.amount.default = str(panel.bet) if panel.bet else None

    async def on_submit(self, interaction):
        panel = self.panel
        if panel.busy:  # a drop is running, the card is not the panel right now
            await interaction.response.defer()
            return
        have = auras.balance(panel.guild_id, panel.author.id)
        bet = parse_amount(self.amount.value, have)
        if bet is None or bet < 1:
            await interaction.response.send_message(embed=gnote_for(
                interaction.user, "warn", f"Bet a whole number from **{GAMBLE_MIN_BET:,}** to **{GAMBLE_MAX_BET:,}**, like `50`, `2k` or `all`."), ephemeral=True)
            return
        panel.bet = min(bet, have, GAMBLE_MAX_BET)  # `all` is never more than you have or than the biggest bet
        panel.draw()
        await interaction.response.edit_message(view=panel)

    async def on_error(self, interaction, error):
        log.warning("The plinko bet form failed", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Something went wrong, try again.", ephemeral=True)
            else:
                await interaction.response.send_message("Something went wrong, try again.", ephemeral=True)
        except discord.HTTPException:
            pass


class PlinkoView(discord.ui.LayoutView):
    """The plinko card. Only the person who opened it can press the buttons. While a drop is playing there are no buttons at all
    (the card shows the board), so a second Start can't happen; `busy` also covers a press that was already on its way."""

    def __init__(self, author, guild_id, bet, risk="medium"):
        super().__init__(timeout=PLINKO_TIMEOUT)
        self.author, self.guild_id = author, guild_id
        self.bet, self.risk = bet, risk
        self.last = None      # the result line of the last drop, shown under the risk
        self.last_tags = []   # boost words of the last drop (`Luck Boost 4 left`), shown in grey under it
        self.busy = False
        self.message = None
        self.draw()

    # ---- the three looks of the card ----
    def draw(self, disabled=False):
        """The panel: title, then Bet / Risk (and the last result), then the buttons."""
        self.clear_items()
        lines = [f"Bet: `{self.bet:,}`", f"Risk: `{self.risk}`"]
        if self.last:
            lines.append(self.last)
        if self.last_tags:
            lines.append(small(" \u00b7 ".join(self.last_tags)))
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay("### \U0001F7E1 Plinko"))
        card.add_item(discord.ui.Separator())
        card.add_item(discord.ui.TextDisplay("\n".join(lines)))
        card.add_item(discord.ui.Separator())
        row = discord.ui.ActionRow()
        set_bet = discord.ui.Button(label="Set Bet", style=discord.ButtonStyle.primary, disabled=disabled)
        set_bet.callback = self._set_bet
        row.add_item(set_bet)
        for name in gambling.PLINKO_RISKS:
            button = discord.ui.Button(label=name.capitalize(), disabled=disabled,
                                       style=discord.ButtonStyle.primary if name == self.risk else discord.ButtonStyle.secondary)
            button.callback = self._pick(name)
            row.add_item(button)
        start = discord.ui.Button(label="Start", style=discord.ButtonStyle.success, disabled=disabled)
        start.callback = self._start
        row.add_item(start)
        card.add_item(row)
        self.add_item(card)

    def draw_board(self, bet, url, slot=None, multiplier=None, result=None):
        """The card while a drop plays: title with the bet, the risk line, the picture and, once it landed, the result."""
        self.clear_items()
        grey = f"-# Risk: **{self.risk.capitalize()}**"
        if slot is not None:
            grey += f" | Bucket: `{slot + 1}/{PLINKO_SLOTS}` | Multiplier: `{multiplier:g}x`"
        card = discord.ui.Container()
        card.add_item(discord.ui.TextDisplay(f"### \U0001F7E1 Plinko - Bet: `{bet:,}` {plinko_money()}\n{grey}"))
        card.add_item(discord.ui.Separator())
        card.add_item(plinko_gallery(url))
        if result:
            card.add_item(discord.ui.Separator())
            card.add_item(discord.ui.TextDisplay(result))
        self.add_item(card)

    # ---- buttons ----
    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                embed=gnote_for(interaction.user, "warn", f"This isn't your game, use `{prefix_cmd('plinko')}` for yours."), ephemeral=True)
            return False
        return True

    async def _set_bet(self, interaction):
        if self.busy:
            await interaction.response.defer()
            return
        await interaction.response.send_modal(PlinkoBetModal(self))

    def _pick(self, name):
        async def pick(interaction):
            if self.busy:
                await interaction.response.defer()
                return
            self.risk = name
            self.draw()
            await interaction.response.edit_message(view=self)
        return pick

    async def _start(self, interaction):
        if self.busy:
            await interaction.response.defer()
            return
        gid, uid = self.guild_id, self.author.id
        here = types.SimpleNamespace(guild=interaction.guild, author=interaction.user, channel=interaction.channel)
        bet, problem = gamble_start(here, str(self.bet))  # channel switch, cooldown, limits and balance, same as the other games
        if problem is not None:
            await interaction.response.send_message(embed=problem, ephemeral=True)
            return
        self.busy = True
        # Everything from here to auras.add has no `await`: the drop is settled before anything is shown.
        level = self.risk
        luck, half = boosts_start(gid, uid, bet)  # Luck Boost / Half Loss: uses up one game of each that is active
        want = luck_roll(gambling.plinko_win_chance(level)) if luck else None
        path, multiplier = gambling.plinko_drop(level, GAMBLE_RNG, want)
        slot = gambling.plinko_slot(path)
        back = math.ceil(bet * multiplier) if multiplier > 1 else int(bet * multiplier)  # a win always pays at least 1 more than the bet
        net = back - bet
        if half:
            net = gambling.soften_loss(net, bet)
        auras.add(gid, uid, net)
        result = plinko_result_text(bet, net, multiplier)
        tags = boost_tags(gid, uid, luck, half, bet)
        try:
            await interaction.response.defer()
            await self._play(interaction, bet, path, slot, multiplier, result)
        except Exception:
            log.warning("Could not show the plinko drop", exc_info=True)  # the result above is already paid, the panel below still shows it
        finally:
            self.busy = False
            self.last, self.last_tags = result, tags
            self.bet = max(0, min(bet, GAMBLE_MAX_BET, auras.balance(gid, uid)))  # the next bet can't be more than what is left
            self.draw()
            try:
                await interaction.edit_original_response(view=self, attachments=[])
            except discord.HTTPException:
                log.warning("Could not show the plinko panel", exc_info=True)

    async def _play(self, interaction, bet, path, slot, multiplier, result):
        """The show: the falling ball, then the landed board with the result."""
        table = gambling.PLINKO_TABLES[self.risk]
        gif, seconds = await asyncio.to_thread(plinko_render.render_gif, table, path)
        png = await asyncio.to_thread(plinko_render.render_png, table, slot)
        self.draw_board(bet, "attachment://plinko_drop.gif")
        await interaction.edit_original_response(view=self, attachments=[discord.File(io.BytesIO(gif), filename="plinko_drop.gif")])
        await asyncio.sleep(seconds + 0.6)  # the animation, plus a moment for the picture to load
        self.draw_board(bet, "attachment://plinko_result.png", slot, multiplier, result)
        await interaction.edit_original_response(view=self, attachments=[discord.File(io.BytesIO(png), filename="plinko_result.png")])
        await asyncio.sleep(PLINKO_SHOW_SECONDS)

    async def on_timeout(self):
        if self.busy or self.message is None:  # a drop is playing: its own last step puts the panel back
            return
        self.draw(disabled=True)
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


@bot.hybrid_command(name="plinko", description="Drop a ball down the plinko board and bet reputation on where it lands")
@app_commands.describe(amount="How much to bet: 50, 2k, half or all (you can change it on the card)", risk="low, medium or high (medium if you leave it out)")
@app_commands.choices(risk=[app_commands.Choice(name=n.capitalize(), value=n) for n in gambling.PLINKO_RISKS])
@commands.guild_only()
async def plinko_command(ctx, amount: str = None, risk: str = None):
    """`,plinko 500 high` (or just `,plinko`): opens the plinko card with your bet and risk. Press Start and the ball falls through
    8 rows into a slot that pays a multiplier of your bet. Low risk wins often but small, high risk mostly loses but the edges pay up to 26x."""
    aliases = gambling.PLINKO_RISK_ALIASES
    if amount and amount.strip().lower() in aliases and (risk is None or risk.strip().lower() not in aliases):
        amount, risk = risk, amount  # `,plinko high 500` and `,plinko high` work too
    level = aliases.get((risk or "medium").strip().lower())
    if level is None:
        await ctx.send(embed=gnote(ctx, "warn", "Pick a risk: **low**, **medium** or **high**, or leave it out for medium."))
        return
    off = gambling_off_note(ctx)
    if off is not None:
        await ctx.send(embed=off)
        return
    have = auras.balance(ctx.guild.id, ctx.author.id)
    bet = GAMBLE_MIN_BET if amount is None else parse_amount(amount, have)
    if bet is None or bet < 0:
        await ctx.send(embed=gnote(ctx, "warn", f"Bet a whole number from **{GAMBLE_MIN_BET:,}** to **{GAMBLE_MAX_BET:,}**, like `50`, `2k` or `all`."))
        return
    bet = max(0, min(bet, have, GAMBLE_MAX_BET))  # `all` shows what you really have, like the card after a drop
    view = PlinkoView(ctx.author, ctx.guild.id, bet, level)
    view.message = await ctx.send(view=view)


# ---- rob ------------------------------------------------------------------------------
@bot.hybrid_command(name="rob", description="Try to rob another member's reputation")
@app_commands.describe(member="Who to rob")
@commands.guild_only()
async def rob_command(ctx, member: discord.Member):
    """`,rob @member`: get away and you take a share of their reputation, get caught and you pay them that share instead.
    Reputation only moves between the two of you, nothing is created. Once every ROB_COOLDOWN seconds."""
    gid, uid = ctx.guild.id, ctx.author.id
    now = time.time()
    u = auras.user(gid, uid)
    ready = u.get("rob_at", 0) + ROB_COOLDOWN
    if ready > now:
        await ctx.send(embed=gnote(ctx, "cooldown", f"The police are watching, you can rob again <t:{int(ready) + 1}:R>."))
        return
    if member.bot or member.id == uid:
        await ctx.send(embed=gnote(ctx, "warn", "Pick another member to rob."))
        return
    mine_family = families.by_member(gid, uid)
    if mine_family is not None and str(member.id) in mine_family["members"]:
        await ctx.send(embed=gnote(ctx, "warn", "You can't rob someone in your own family."))
        return
    safe_until = auras.data.get(str(gid), {}).get(str(member.id), {}).get("robbed_at", 0) + ROB_IMMUNITY
    if safe_until > now:
        await ctx.send(embed=gnote(ctx, "warn", f"{member.mention} was just robbed and is safe until <t:{int(safe_until) + 1}:R>."))
        return
    shielded = shield_until(gid, member.id)
    if shielded > now:
        await ctx.send(embed=gnote(ctx, "warn", f"{member.mention} has a Rob Shield until <t:{int(shielded) + 1}:R>."))
        return
    mine, theirs = auras.balance(gid, uid), auras.balance(gid, member.id)
    if mine <= -ROB_DEBT_LIMIT:
        await ctx.send(embed=gnote(ctx, "warn", f"You're **{-mine:,}** in debt, the limit for robbing is **{ROB_DEBT_LIMIT:,}**. Earn some reputation back first."))
        return
    safe = vault_safe(gid, member.id, theirs)  # a Vault keeps part of their reputation out of reach
    exposed = theirs - safe
    if exposed < ROB_MIN_BALANCE:
        await ctx.send(embed=gnote(ctx, "warn", f"{member.mention} doesn't have enough reputation to be worth robbing." + (" Their Vault keeps the rest safe." if safe else "")))
        return
    # no await from here to the end of the money moves: the two balances can't change in between
    amount = max(1, min(ROB_MAX, int(exposed * GAMBLE_RNG.uniform(*ROB_STEAL))))
    u["rob_at"] = now
    picked = held_count(auras.items_of(gid, uid), "lockpicks") > 0  # a Lockpick is used up by this rob, win or lose
    chance = max(ROB_CHANCE, LOCKPICK_CHANCE) if picked else ROB_CHANCE
    if picked:
        auras.items(gid, uid)["lockpicks"] = held_count(auras.items_of(gid, uid), "lockpicks") - 1
    dropped = shield_until(gid, uid) > now  # robbing while you hold a Rob Shield ends your own shield
    if dropped:
        auras.items(gid, uid)["shield_until"] = 0
    if GAMBLE_RNG.random() < chance:
        auras.add(gid, member.id, -amount)
        total = auras.add(gid, uid, amount)
        auras.user(gid, member.id)["robbed_at"] = now
        auras.save()
        reward = claim_bounty(gid, member.id, uid)  # every bounty on the victim (not your own) is yours now
        if reward:
            total = auras.balance(gid, uid)
        net = amount
        line = f"{ctx.author.mention} robbed {member.mention} and got away {net_text(net)}"
        if reward:
            line += f"\n{emo('trophy', chr(0x1F3C6))} and collected the **{reward:,}** bounty on {member.mention}"
    else:
        fine = min(amount, mine + ROB_DEBT_LIMIT)  # a fine can put you in debt, but never lower than -ROB_DEBT_LIMIT
        auras.add(gid, member.id, fine)
        total = auras.add(gid, uid, -fine)
        net = -fine
        line = f"{ctx.author.mention} got caught robbing {member.mention} and paid them {net_text(net)}"
    footer = small("Balance " + format(total, ",") + " \u00b7 Next rob in " + human_time(ROB_COOLDOWN) + (" \u00b7 Your Rob Shield ended" if dropped else "") + (" \u00b7 Lockpick used" if picked else "") + (f" \u00b7 Their Vault kept {safe:,} safe" if safe else ""))
    try:
        await ctx.send(content=member.mention, embed=gamble_embed(ctx.author, line + "\n" + footer),
                       allowed_mentions=discord.AllowedMentions(users=[member]))
    except discord.HTTPException:
        log.warning("Could not show a rob result", exc_info=True)


# ---- blackjack ------------------------------------------------------------------------
BJ_RESULT_TEXT = {  # short on purpose: the numbers are on the cards, the payout ratio is in the help text
    "blackjack": "**Blackjack!**",
    "win": "**You win**",
    "dealer_bust": "**Dealer busts**",
    "push": "**Push**",
    "push_blackjack": "**Push**",
    "lose": "**Dealer wins**",
    "bust": "**Bust**",
    "dealer_blackjack": "**Dealer blackjack**",
}


def card_emo(card):
    return emo(f"c_{card[0]}{card[1]}", f"`{card_label(card)}`")


def hand_text(cards, hide_hole=False):
    """A hand as big card emojis on one line. hide_hole shows the dealer's other cards face down."""
    parts = [card_emo(c) for c in cards]
    if hide_hole and len(parts) >= 2:
        parts[1:] = [emo("c_back", "`??`")] * (len(parts) - 1)
    line = ("" if all(p.startswith("<") for p in parts) else " ").join(parts)
    return big(line, 1 if len(cards) <= 4 else 2)  # a long hand gets a smaller header so it still fits on a phone


def total_text(cards):
    total, soft = hand_value(cards)
    if total > 21:
        return f"`{total}`"
    return f"`soft {total}`" if soft and total < 21 else f"`{total}`"


def blackjack_embed(user, game, held=None, balance=None):
    """The table: dealer's cards, your cards, nothing else. While the game runs the dealer's second card is face down;
    when it is over everything is open and the result + a grey Bet / Balance line are added
    (held = what was bet in total, balance = the new balance)."""
    playing = not game.done
    if playing:
        dealer = f"`{card_points(game.dealer[0][0])} + ?`"
    else:
        dealer = total_text(game.dealer)
    text = (f"**Dealer** {dealer}\n{hand_text(game.dealer, hide_hole=playing)}\n"
            f"**{discord.utils.escape_markdown(user.display_name)}** {total_text(game.player)}\n{hand_text(game.player)}")
    stake = held if held is not None else game.stake
    if playing:
        return gamble_embed(user, f"{text}\n{small(f'Bet {stake:,}')}")
    net = game.payout - stake
    result = BJ_RESULT_TEXT[game.outcome]
    if game.outcome == "blackjack" and emo("jackpot"):
        result = f"{emo('jackpot')} {result}"
    if net:
        result = f"{result} {net_text(net)}"
    return gamble_embed(user, f"{text}\n\n{result}\n{stats_line(stake, balance)}")


def blackjack_timeout_embed(user, game, held, balance, action):
    """An idle game that was NOT played out (refund or lose). The dealer's hole card stays face down, because the game
    never finished. refund = `Cancelled`, the bet is back; lose = `Timed out` and the bet is gone."""
    text = (f"**Dealer** `{card_points(game.dealer[0][0])} + ?`\n{hand_text(game.dealer, hide_hole=True)}\n"
            f"**{discord.utils.escape_markdown(user.display_name)}** {total_text(game.player)}\n{hand_text(game.player)}")
    if action == "lose":
        result = f"**Timed out** {net_text(-held)}"
    else:
        result = "**Cancelled** \u00b7 bet returned"
    return gamble_embed(user, f"{text}\n\n{result}\n{stats_line(held, balance, 'Too slow')}")


class BlackjackView(discord.ui.View):
    """Hit / Stand / Double under a running game. Only the player can press them.
    Every press runs under a lock and checks `settled` first, so double clicks, late clicks and a click that lands at the
    same moment as the timeout can never pay a game twice."""

    def __init__(self, author, guild_id, game):
        super().__init__(timeout=BJ_TIMEOUT)
        self.author, self.guild_id, self.game = author, guild_id, game
        self.held = game.bet          # what is parked in the bank for this game (the bet, or 2 x bet after a double down)
        self.lock = asyncio.Lock()
        self.settled = False
        self.message = None
        style_buttons(self, {"Hit": "hit", "Stand": "stand", "Double": "double"})
        self.double_button = next(c for c in self.children if getattr(c, "label", None) == "Double")

    # ---- paying ----
    def settle(self, payout=None):
        """Pay the game out. Runs once (later calls do nothing and return None). No `await`: stop flag, payout and
        cleanup happen together."""
        if self.settled:
            return None
        self.settled = True
        payout = self.game.payout if payout is None else payout
        total = auras.release(self.guild_id, self.author.id, self.held, payout)
        if bj_games.get((self.guild_id, self.author.id)) is self:
            del bj_games[(self.guild_id, self.author.id)]
        self.stop()
        return total

    def refresh_buttons(self):
        affordable = auras.balance(self.guild_id, self.author.id) >= self.game.bet
        self.double_button.disabled = not (self.game.can_double() and affordable)

    # ---- pressing ----
    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                embed=gnote_for(interaction.user, "warn", "This isn't your game."), ephemeral=True)
            return False
        return True

    async def act(self, interaction, action):
        async with self.lock:
            if self.settled or self.game.done:  # finished already (double click, late click): ignore it
                await interaction.response.defer()
                return
            try:
                if action == "hit":
                    self.game.hit()
                elif action == "stand":
                    self.game.stand()
                else:  # double down: take the second bet out of the balance first (one step: it fails if you can't pay it)
                    if not self.game.can_double():
                        await interaction.response.send_message(
                            embed=gnote_for(interaction.user, "warn", "You can only double down on your first two cards."),
                            ephemeral=True)
                        return
                    if not auras.hold(self.guild_id, self.author.id, self.game.bet):
                        await interaction.response.send_message(
                            embed=gnote_for(interaction.user, "warn", "You don't have enough reputation to double down."),
                            ephemeral=True)
                        return
                    self.held += self.game.bet
                    self.game.double()
            except Exception:
                log.exception("Blackjack went wrong, giving the bet back")
                self.settle(payout=self.held)
                await self._edit(interaction, gamble_embed(
                    self.author, f"{status_text('warn', 'Something went wrong, your bet was given back.')}"), None)
                return
            if self.game.done:
                total = self.settle()
                await self._edit(interaction, blackjack_embed(self.author, self.game, self.held, total), None)
            else:
                self.refresh_buttons()
                await self._edit(interaction, blackjack_embed(self.author, self.game), self)

    async def _edit(self, interaction, embed, view):
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.HTTPException:
            log.warning("Could not update a blackjack table", exc_info=True)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.secondary)
    async def hit_button(self, interaction, button):
        await self.act(interaction, "hit")

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand_button(self, interaction, button):
        await self.act(interaction, "stand")

    @discord.ui.button(label="Double", style=discord.ButtonStyle.secondary)
    async def double_button_cb(self, interaction, button):
        await self.act(interaction, "double")

    async def on_timeout(self):
        """Nobody pressed anything for BJ_TIMEOUT seconds. BJ_TIMEOUT_ACTION decides: `refund` (default) cancels the game and
        gives the whole bet back, `stand` plays the hand out like a Stand press, `lose` keeps the bet. Either way the game
        is paid exactly once (settle() runs once) and nothing stays held."""
        async with self.lock:
            if self.settled or self.game.done:
                return
            embed = None
            try:
                if BJ_TIMEOUT_ACTION == "stand":
                    self.game.stand()
                    total = self.settle()
                    embed = blackjack_embed(self.author, self.game, self.held, total)
                elif BJ_TIMEOUT_ACTION == "lose":
                    total = self.settle(payout=0)
                    embed = blackjack_timeout_embed(self.author, self.game, self.held, total, "lose")
                else:
                    total = self.settle(payout=self.held)
                    embed = blackjack_timeout_embed(self.author, self.game, self.held, total, "refund")
            except Exception:
                log.exception("Blackjack timeout went wrong, giving the bet back")
                total = self.settle(payout=self.held)
                self.message = None
            if self.message is not None and embed is not None:
                try:
                    await self.message.edit(embed=embed, view=None)
                except discord.HTTPException:
                    pass


@bot.hybrid_command(name="blackjack", aliases=["bj"], description="Play blackjack against the bot with reputation")
@app_commands.describe(amount="How much to bet: 50, 2k, half or all")
@commands.guild_only()
async def blackjack_command(ctx, amount: str):
    """`,blackjack 100`: get closer to 21 than the dealer without going over. A blackjack pays extra (2:1 by default)."""
    gid, uid = ctx.guild.id, ctx.author.id
    running = bj_games.get((gid, uid))
    if running is not None and not running.settled:
        await ctx.send(embed=gnote(ctx, "warn", "You already have a blackjack game running. Finish it first."))
        return
    bet, problem = gamble_start(ctx, amount)
    if problem is not None:
        await ctx.send(embed=problem)
        return
    if not auras.hold(gid, uid, bet):  # takes the bet out of the balance (and says no if it isn't all there)
        await ctx.send(embed=gnote(ctx, "warn", f"You only have **{auras.balance(gid, uid):,}** reputation."))
        return
    try:
        game = Blackjack(bet, GAMBLE_RNG)
        view = BlackjackView(ctx.author, gid, game)
    except Exception:
        auras.release(gid, uid, bet, bet)  # nothing was shown yet: put the bet back
        raise
    bj_games[(gid, uid)] = view
    if game.done:  # blackjack on the deal (either side): nothing to decide, pay it out right away
        total = view.settle()
        await ctx.send(embed=blackjack_embed(ctx.author, game, view.held, total))
        return
    view.refresh_buttons()
    try:
        view.message = await ctx.send(embed=blackjack_embed(ctx.author, game), view=view)
    except BaseException:
        view.settle(payout=view.held)  # the table was never shown: give the bet back
        raise


# --------------------------------------------------------------------------
# ,quests: daily and weekly goals that pay reputation. ,collect: a perk for staff and server boosters.
# --------------------------------------------------------------------------
QUEST_FILE = os.path.join(DATA_DIR, "quests.json")
DAILY_QUEST_COUNT = 3   # quests handed out every day (UTC)
WEEKLY_QUEST_COUNT = 2  # quests handed out every week (the family war week: Monday 00:00 UTC)
QUEST_MESSAGE_GAP = int(os.getenv("QUEST_MESSAGE_GAP", "3"))  # seconds between two messages that count (so spam doesn't)

# quest id -> (what it counts, how many, reputation reward, text). What it counts:
#   message / ratio / ratio_win / daily / duel_win / game (joined a ,gayest or ,tightest round) / quest_done (a claimed daily quest)
DAILY_QUESTS = {
    "d_msg20": ("message", 20, 60, "Send 20 messages"),
    "d_msg50": ("message", 50, 120, "Send 50 messages"),
    "d_ratio3": ("ratio", 3, 50, "Use the ratio command 3 times"),
    "d_ratiowin2": ("ratio_win", 2, 80, "Win 2 ratios"),
    "d_daily": ("daily", 1, 40, "Claim your daily reputation"),
    "d_duel": ("duel_win", 1, 100, "Win a duel"),
    "d_game": ("game", 1, 40, "Join a gayest or tightest round"),
}
WEEKLY_QUESTS = {
    "w_msg300": ("message", 300, 250, "Send 300 messages"),
    "w_ratiowin10": ("ratio_win", 10, 300, "Win 10 ratios"),
    "w_daily5": ("daily", 5, 300, "Claim your daily 5 times"),
    "w_duel3": ("duel_win", 3, 350, "Win 3 duels"),
    "w_game5": ("game", 5, 200, "Join 5 gayest or tightest rounds"),
    "w_quests10": ("quest_done", 10, 400, "Claim 10 daily quests"),
}
QUEST_KINDS = (("day", DAILY_QUESTS, DAILY_QUEST_COUNT), ("week", WEEKLY_QUESTS, WEEKLY_QUEST_COUNT))


class QuestStore:
    """Quest progress per server, saved in quests.json: {server: {user: {"day": {...}, "week": {...}}}}.
    Kept apart from aura.json so people who only chat never show up on the reputation leaderboard.
    Message progress changes all the time, so it is only marked dirty here and written by quest_save_loop."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.dirty = False
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {}
        except Exception:
            log.exception("Could not read %s, starting without quest progress", self.path)
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
        self.dirty = False

    def user(self, gid, uid):
        return self.data.setdefault(str(gid), {}).setdefault(str(uid), {})


quests = QuestStore(QUEST_FILE)
quest_last_msg = {}  # (server id, user id) -> when their last counted message was


def quest_period(u, kind):
    """One person's quests for today ("day") or this week ("week"): a fresh random set when the period has changed."""
    _kind, pool, count = next(k for k in QUEST_KINDS if k[0] == kind)
    stamp = datetime.datetime.now(datetime.timezone.utc).date().isoformat() if kind == "day" else week_id()
    st = u.get(kind)
    if not st or st.get("stamp") != stamp:
        st = u[kind] = {"stamp": stamp, "ids": random.sample(list(pool), count), "progress": {}, "claimed": []}
        quests.dirty = True
    return st


def quest_progress(gid, uid, event, amount=1):
    """Add `amount` to every quest of this person that counts `event` and isn't finished yet. Never raises."""
    try:
        u = quests.user(gid, uid)
        for kind, pool, _count in QUEST_KINDS:
            st = quest_period(u, kind)
            for qid in st["ids"]:
                q = pool.get(qid)
                have = st["progress"].get(qid, 0)
                if q is None or q[0] != event or qid in st["claimed"] or have >= q[1]:
                    continue
                st["progress"][qid] = min(q[1], have + amount)
                quests.dirty = True
    except Exception:
        log.exception("Could not add quest progress (%s)", event)


def quest_message(message):
    """Count a normal chat message for the message quests: not a command, at least 3 characters, one per QUEST_MESSAGE_GAP seconds."""
    content = message.content.strip()
    if len(content) < 3 or content.startswith(tuple(all_prefixes())):
        return
    key = (message.guild.id, message.author.id)
    now = time.monotonic()
    if now - quest_last_msg.get(key, -1e9) < QUEST_MESSAGE_GAP:
        return
    quest_last_msg[key] = now
    quest_progress(message.guild.id, message.author.id, "message")


def quest_ready(gid, uid):
    """How many finished quests are waiting to be claimed."""
    u = quests.user(gid, uid)
    waiting = 0
    for kind, pool, _count in QUEST_KINDS:
        st = quest_period(u, kind)
        for qid in st["ids"]:
            q = pool.get(qid)
            if q and qid not in st["claimed"] and st["progress"].get(qid, 0) >= q[1]:
                waiting += 1
    return waiting


def quest_claim(gid, uid):
    """Pay every finished quest that hasn't been paid. Returns (how many quests, total reputation, new balance, extra from a Quest Boost).
    There is no await in here, so two presses can never pay the same quest twice."""
    u = quests.user(gid, uid)
    paid, total = 0, 0
    for kind, pool, _count in QUEST_KINDS:
        st = quest_period(u, kind)
        done_here = 0
        for qid in st["ids"]:
            q = pool.get(qid)
            if q and qid not in st["claimed"] and st["progress"].get(qid, 0) >= q[1]:
                st["claimed"].append(qid)
                paid += 1
                done_here += 1
                total += q[2]
        if kind == "day" and done_here:
            quest_progress(gid, uid, "quest_done", done_here)  # counts toward the weekly "claim 10 daily quests"
    if not paid:
        return 0, 0, auras.balance(gid, uid), 0
    quests.save()
    extra = 0
    boosts = held_count(auras.items_of(gid, uid), "boosts")
    if boosts > 0:  # a Quest Boost doubles this claim (up to BOOST_CAP extra) and is used up
        extra = shopkit.boost_extra(total, BOOST_CAP)
        if extra:
            auras.items(gid, uid)["boosts"] = boosts - 1
            total += extra
    return paid, total, give_aura(gid, uid, total), extra


def quest_slots(u):
    """[(kind, pool, state, position, quest id)] in the order the quest card shows them (daily first, then weekly)."""
    out = []
    for kind, pool, _count in QUEST_KINDS:
        st = quest_period(u, kind)
        for pos, qid in enumerate(st["ids"]):
            out.append((kind, pool, st, pos, qid))
    return out


def quest_reroll(gid, uid, number):
    """Swap quest number `number` (as numbered on the quest card) for a new one, using a Quest Reroll. Only a quest that isn't
    finished or claimed can be swapped, and the new one starts at 0. Returns (ok, text). No await in here, so two presses can't both pay."""
    if held_count(auras.items_of(gid, uid), "rerolls") < 1:
        return False, f"You don't have a Quest Reroll. Buy one in `{prefix_cmd('shop')}` for **{REROLL_PRICE:,}** reputation."
    slots = quest_slots(quests.user(gid, uid))
    if not isinstance(number, int) or not 1 <= number <= len(slots):
        return False, f"Pick a quest number from 1 to {len(slots)}, as shown on `{prefix_cmd('quests')}`."
    kind, pool, st, pos, qid = slots[number - 1]
    q = pool.get(qid)
    if qid in st["claimed"] or (q is not None and st["progress"].get(qid, 0) >= q[1]):
        return False, "That quest is already finished, claim it instead. Only an unfinished quest can be rerolled."
    choices = [k for k in pool if k not in st["ids"]]
    if not choices:
        return False, "There is no other quest to swap it for."
    new = random.choice(choices)
    st["ids"][pos] = new
    st["progress"].pop(qid, None)
    quests.save()
    auras.items(gid, uid)["rerolls"] = held_count(auras.items_of(gid, uid), "rerolls") - 1
    auras.save()
    return True, f"swapped your quest for **{pool[new][3]}** (it starts at 0). You have **{held_count(auras.items_of(gid, uid), 'rerolls')}** Quest Reroll{'s' if held_count(auras.items_of(gid, uid), 'rerolls') != 1 else ''} left."


def boost_note(extra):
    return f" (**+{extra:,}** from your Quest Boost)" if extra else ""


def quest_embed(member):
    """The quest card: today's quests, this week's quests, a bar and the reward for each."""
    gid = member.guild.id
    u = quests.user(gid, member.id)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    tomorrow = int(datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time.min, datetime.timezone.utc).timestamp())
    blocks = []
    rerolls = held_count(auras.items_of(gid, member.id), "rerolls")
    number = 0
    for kind, pool, _count in QUEST_KINDS:
        st = quest_period(u, kind)
        title = "Daily quests" if kind == "day" else "Weekly quests"
        resets = tomorrow if kind == "day" else week_end()
        lines = [f"**{title}** \u00b7 resets <t:{resets}:R>"]
        for qid in st["ids"]:
            q = pool.get(qid)
            if q is None:
                continue
            _event, target, reward, text = q
            number += 1
            if rerolls:
                text = f"`{number}` {text}"  # numbered only while you hold a Quest Reroll, so you can pick one
            have = min(st["progress"].get(qid, 0), target)
            if qid in st["claimed"]:
                icon, tail = status_icon("ok"), "claimed"
            elif have >= target:
                icon, tail = "\U0001F381", "**ready to claim**"
            else:
                icon, tail = "\u25AB\uFE0F", f"+{reward:,} reputation"
            lines.append(f"{icon} **{text}**\n{meter_bar(have / target * 100)} {have:,}/{target:,} \u00b7 {tail}")
        blocks.append("\n".join(lines))
    embed = discord.Embed(description="\n\n".join(blocks), color=theme())
    with_author(embed, member)
    waiting = quest_ready(gid, member.id)
    hint = f" · {prefix_cmd('quests')} reroll <number> swaps one ({rerolls} left)" if rerolls else ""
    embed.set_footer(text=(f"{waiting} quest{'s' if waiting != 1 else ''} ready to claim" if waiting
                           else "Press Claim rewards when a quest is finished") + hint)
    return embed


class QuestView(discord.ui.View):
    """Under the quest card: Claim rewards (no icon on the button). Only the person whose quests they are can press it, and the claim result is an ephemeral embed only they see."""

    def __init__(self, member):
        super().__init__(timeout=180)
        self.member = member
        self.message = None
        self.claim.disabled = quest_ready(member.guild.id, member.id) == 0

    async def interaction_check(self, interaction):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", f"These are not your quests, use `{prefix_for('quests')}quests` for yours."),
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Claim rewards", style=discord.ButtonStyle.success)
    async def claim(self, interaction, button):
        paid, total, balance, extra = quest_claim(self.member.guild.id, self.member.id)
        if not paid:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", "Nothing to claim yet, finish a quest first."), ephemeral=True)
            return
        button.disabled = quest_ready(self.member.guild.id, self.member.id) == 0
        await interaction.response.edit_message(embed=quest_embed(self.member), view=self)
        await interaction.followup.send(embed=status_line(
            interaction.user, "ok", f"claimed **{paid}** quest{'s' if paid != 1 else ''} for **{total:,}** reputation{boost_note(extra)}. You now have **{balance:,}** reputation."),
            ephemeral=True)  # only the person who pressed Claim rewards sees it

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


@bot.hybrid_command(name="quests", aliases=["quest"], description="Your daily and weekly quests that pay reputation")
@app_commands.describe(action="claim collects every finished quest, reroll swaps one (with a Quest Reroll from the shop)", number="The quest number to reroll")
@commands.guild_only()
async def quests_cmd(ctx, action: str = None, number: int = None):
    """Show your quests. `quests claim` collects the finished ones, `quests reroll <number>` swaps an unfinished one."""
    if action and action.strip().lower() == "reroll":
        ok, text = quest_reroll(ctx.guild.id, ctx.author.id, number)
        await ctx.send(embed=status_embed(ctx, "ok" if ok else "warn", text))
        if ok:
            await ctx.send(embed=quest_embed(ctx.author))
        return
    if action and action.strip().lower() == "claim":
        paid, total, balance, extra = quest_claim(ctx.guild.id, ctx.author.id)
        if not paid:
            await ctx.send(embed=status_embed(ctx, "warn", "Nothing to claim yet, finish a quest first."))
        else:
            await ctx.send(embed=status_embed(
                ctx, "ok", f"claimed **{paid}** quest{'s' if paid != 1 else ''} for **{total:,}** reputation{boost_note(extra)}. You now have **{balance:,}** reputation."))
        return
    view = QuestView(ctx.author)
    view.message = await ctx.send(embed=quest_embed(ctx.author), view=view)


def staff_role_keys():
    """Role names that count as staff for ,collect: every rank in the staff list plus a plain 'Staff' role."""
    return {role_key(n) for _label, names in STAFF_RANKS for n in names} | {"staff"}


def collect_reason(member):
    """'staff', 'server booster' or None: why this member may use ,collect."""
    guild = member.guild
    if member.id == guild.owner_id or member.guild_permissions.administrator:
        return "staff"
    keys = staff_role_keys()
    if any(role_key(r.name) in keys for r in member.roles):
        return "staff"
    if member.premium_since is not None:
        return "server booster"
    return None


@bot.hybrid_command(name="collect", description="Staff and server boosters: collect free reputation")
@commands.guild_only()
async def collect(ctx):
    """Staff and server boosters get COLLECT_AMOUNT reputation every COLLECT_COOLDOWN seconds."""
    gid, uid = ctx.guild.id, ctx.author.id
    reason = collect_reason(ctx.author)
    if reason is None:
        await ctx.send(embed=status_embed(ctx, "warn", "`collect` is only for staff and server boosters."))
        return
    u = auras.user(gid, uid)
    left = u.get("collect_at", 0) + COLLECT_COOLDOWN - time.time()
    if left > 0:
        await ctx.send(embed=status_embed(ctx, "cooldown", f"You already collected, try again in **{human_time(left)}**."))
        return
    u["collect_at"] = time.time()
    total = give_aura(gid, uid, COLLECT_AMOUNT)  # also saves collect_at
    await ctx.send(embed=status_embed(
        ctx, "ok", f"collected **{COLLECT_AMOUNT:,}** reputation as {reason}. You now have **{total:,}** reputation."))


QUEST_SAVE_SECONDS = int(os.getenv("QUEST_SAVE_SECONDS", "300"))  # how often quest progress is written to disk


async def quest_save_loop():
    """Message progress is only marked dirty when it changes; this writes quests.json at most every 5 minutes."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(QUEST_SAVE_SECONDS)
        if quests.dirty:
            try:
                quests.save()
            except Exception:
                log.exception("Could not save %s", QUEST_FILE)


# ,gayest and ,tightest: people press Join for GAME_SECONDS (20) seconds, then one is picked
active_games = set()  # (channel id, game name) pairs that are running right now


def pick_winner(players):
    """Random pick from the people who joined, or None if nobody did."""
    return random.choice(players) if players else None


class JoinView(discord.ui.View):
    def __init__(self, question, accent, seconds=GAME_SECONDS):
        super().__init__(timeout=seconds + 60)
        self.question = question
        self.accent = accent
        self.ends_at = int(time.time()) + seconds
        self.players = {}  # user id -> member, in the order they joined
        self.closed = False
        style_buttons(self, {"Join": "add"})

    def build_embed(self):
        text = (
            f"**{self.question}**\n"
            f"Press **Join** below. A winner is picked <t:{self.ends_at}:R>.\n\n"
        )
        if self.players:
            names = [m.mention for m in self.players.values()]
            shown = ", ".join(names[:25])
            if len(names) > 25:
                shown += f" and {len(names) - 25} more"
            text += f"**Joined ({len(names)})**\n{shown}"
        else:
            text += "Nobody has joined yet."
        return discord.Embed(description=text, color=self.accent)

    @discord.ui.button(label="Join", style=discord.ButtonStyle.primary)
    async def join(self, interaction, button):
        if self.closed:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", "Too late, this one is closed."), ephemeral=True
            )
            return
        if interaction.user.id in self.players:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", "You already joined."), ephemeral=True
            )
            return
        self.players[interaction.user.id] = interaction.user
        if interaction.guild_id is not None:
            quest_progress(interaction.guild_id, interaction.user.id, "game")
        await interaction.response.edit_message(embed=self.build_embed())


async def run_pick_game(ctx, kind, question, accent, win_title, prize_key=None, prize_fallback=""):
    key = (ctx.channel.id, kind)
    if key in active_games:
        await ctx.send(embed=status_embed(ctx, "warn", "One is already running in this channel, go join it."))
        return
    active_games.add(key)
    view = None
    try:
        view = JoinView(question, accent)
        message = await ctx.send(embed=view.build_embed(), view=view)
        await asyncio.sleep(GAME_SECONDS)
        view.closed = True
        players = list(view.players.values())
        if not players:
            await message.edit(
                embed=status_embed(ctx, "warn", "This round ended, nobody joined so there is no winner."), view=None
            )
            return
        crowd = "1 person" if len(players) == 1 else f"{len(players)} people"
        # the join message just says it ended; the winner is posted as a new message below it
        try:
            ended = discord.Embed(description=f"**{question}**\nThis round ended. {crowd} joined.", color=accent)
            await message.edit(embed=ended, view=None)
        except discord.HTTPException:
            log.warning("Could not mark the %s join message as ended", kind, exc_info=True)
        rolling = discord.Embed(description=f"{emo('wait')} **Rolling...**\n{crowd} joined".lstrip(), color=accent)
        result = await ctx.send(embed=rolling)
        await asyncio.sleep(2.5)
        winner = pick_winner(players)
        badge = emo(prize_key, prize_fallback) if prize_key else ""  # the little icon in front of the winner
        embed = discord.Embed(
            description=f"{badge} **{win_title}** {winner.mention}\nPicked at random from {crowd}.".lstrip(),
            color=accent,
        )
        await result.edit(embed=embed)
    except discord.HTTPException:
        log.exception("Could not update the %s game message", kind)
    finally:
        if view is not None:
            view.stop()
        active_games.discard(key)


@bot.hybrid_command(name="gayest", description="Join game: who is the gayest?")
@commands.guild_only()
async def gayest(ctx):
    await run_pick_game(ctx, "gayest", "Who is the gayest?", theme(), "The gayest is",
                        prize_key="pride", prize_fallback="\U0001F3F3\uFE0F\u200D\U0001F308")


@bot.hybrid_command(name="tightest", description="Join game: who is the tightest?")
@commands.guild_only()
async def tightest(ctx):
    await run_pick_game(ctx, "tightest", "Who is the tightest?", theme(), "The tightest is", prize_key="trophy")


def goonlock_can_use(member):
    """Who may use ,goonlock: anyone with Manage Messages, or staff. 'Staff' means the same people ,collect and `sop add` count
    (collect_reason: the server owner, Administrators, the Staff role and the staff ranks Owner / Admin / Moderator / Helper...) plus
    whoever has the Staff role picked with `.roles staff @role` (staff_anchor), as long as that isn't @everyone."""
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.manage_messages or collect_reason(member) == "staff":
        return True
    anchor = staff_anchor(member.guild)
    return anchor is not None and not anchor.is_default() and anchor in member.roles


def goonlock_check():
    """Check for every ,goonlock subcommand: Manage Messages, a staff role, the server owner or the bot owner."""
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if goonlock_can_use(ctx.author) or await has_full_access(ctx.author, ctx.guild):
            return True
        raise commands.CheckFailure("Only **staff** (the Staff role) or members with the **Manage Messages** permission can use this.")

    return commands.check(predicate)


@bot.hybrid_group(name="goonlock", invoke_without_command=True, description="Goon-lock people: the bot adds a little extra to everything they say", fallback="help")
async def goonlock(ctx):
    """,goonlock add @member / remove @member / list. The swap itself happens in on_message / repost (goon_applies, goonify)."""
    await send_help(ctx, "goonlock")


@goonlock.command(name="add", description="Goon-lock a member")
@goonlock_check()
async def goonlock_add(ctx, member: discord.Member):
    gid = ctx.guild.id
    if member.bot:
        await ctx.send(embed=status_embed(ctx, "warn", "Bots can't be put on **goon lock**."))
        return
    if goon_locks.is_locked(gid, member.id):
        await ctx.send(embed=status_embed(ctx, "warn", f"{member.mention} is already on **goon lock**."))
        return
    if member.id != ctx.author.id and not await has_full_access(ctx.author, ctx.guild):
        if is_server_owner(member, ctx.guild) or member.top_role >= ctx.author.top_role:
            await ctx.send(embed=status_embed(ctx, "warn", "You can't put someone on **goon lock** if their highest role is equal to or above yours."))
            return
    goon_locks.lock(gid, member.id)
    await ctx.send(embed=status_embed(ctx, "add", f"put {member.mention} on **goon lock**."))


@goonlock.command(name="remove", aliases=["delete"], description="Free a goon-locked member")
@goonlock_check()
async def goonlock_remove(ctx, member: discord.Member):
    if goon_locks.unlock(ctx.guild.id, member.id):
        await ctx.send(embed=status_embed(ctx, "remove", f"took {member.mention} off **goon lock**."))
    else:
        await ctx.send(embed=status_embed(ctx, "warn", f"{member.mention} is not on **goon lock**."))


@goonlock.command(name="list", description="Show who is goon-locked in this server")
@goonlock_check()
async def goonlock_list(ctx):
    people = [m for m in (ctx.guild.get_member(uid) for uid in goon_locks.locked(ctx.guild.id)) if m is not None]
    if not people:
        await ctx.send(embed=status_embed(ctx, "warn", f"Nobody is on goon lock yet. Add someone with `{prefix_for('goonlock')}goonlock add (member)`."))
        return
    lines = [m.mention for m in people[:40]]
    if len(people) > 40:
        lines.append(f"...and {len(people) - 40} more")
    embed = discord.Embed(description=f"**On goon lock** ({len(people)})\n" + "\n".join(lines), color=theme())
    embed.set_footer(text=f"Take someone off with {prefix_for('goonlock')}goonlock remove (member)")
    await ctx.send(embed=embed)


# --------------------------------------------------------------------------
# ,sop (smash or pass): a picture of a character or person, everyone votes Smash or Pass.
# Staff fill the list (links or uploads). It only uses what staff add and never server members (some may be under 18).
# --------------------------------------------------------------------------
SMASHPASS_FILE = os.path.join(DATA_DIR, "smashpass.json")
SOP_DIR = os.path.join(DATA_DIR, "sop")  # uploaded pictures, one folder per server
SMASHPASS_SECONDS = int(os.getenv("SMASHPASS_SECONDS", "600"))  # how long the Smash / Pass buttons stay on the picture
SMASHPASS_MAX = 200                                            # pictures per server
SMASHPASS_DEFAULTS = []  # [("Name", "https://direct-link.jpg"), ...]: pictures every server gets on top of its own list. Adults (18+) only.
SMASHPASS_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
SOP_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
SOP_UPLOAD_BATCH = 10             # pictures taken from one upload message (Discord's own limit)
SOP_MAX_BYTES = 8 * 1024 * 1024   # biggest picture accepted


class SmashPassStore:
    """Each server's own list, saved in smashpass.json: {server: [{"name": ..., "url": ...} or {"name": ..., "file": "<server>/<id>.png"}]}.
    "url" is a direct link to a picture, "file" is a picture staff uploaded (kept in the sop folder, so it can't expire)."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {}
        except Exception:
            log.exception("Could not read %s, starting without smash or pass pictures", self.path)
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    async def save_async(self):
        """Same as save() but the disk write happens in a thread, so the bot never freezes (and buttons never time out)."""
        text = json.dumps(self.data, ensure_ascii=False, indent=2)  # copied on the loop, so later changes can't break it

        def write():
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        try:
            await asyncio.to_thread(write)
        except OSError:
            log.warning("Could not save %s", self.path, exc_info=True)

    def entries(self, gid):
        return self.data.setdefault(str(gid), [])


smashpass = SmashPassStore(SMASHPASS_FILE)
sop_stats = SmashPassStore(os.path.join(DATA_DIR, "smashpass_stats.json"))  # {server: {user id: {"smash": n, "pass": n}}}, how many times each person pressed each button


def sop_count(gid, uid, choice):
    """Add one to this person's smash or pass total in this server and return the new total (saved later by sop_stats.save_async)."""
    row = sop_stats.data.setdefault(str(gid), {}).setdefault(str(uid), {"smash": 0, "pass": 0})
    row[choice] = int(row.get(choice, 0)) + 1
    return row[choice]


SOP_SMASH_COLOR = OK_COLOR  # side bar of the Smash answer (green, like the Smash button)
SOP_PASS_COLOR = GREY       # side bar of the Pass answer (the dark default, like the grey Pass button)


def sop_answer_text(who, name, choice, total):
    """The line posted when someone presses Smash or Pass: '@user smashed Name \U0001F633', then in small text '5 smashes in total'."""
    plural = (("smash" if total == 1 else "smashes") if choice == "smash" else ("pass" if total == 1 else "passes"))
    if choice == "smash":
        line = f"{who} smashed **{name}** \U0001F633"
    else:
        line = f"{who} passed on **{name}**"
    return line, plural


def sop_answer_embed(user, name, choice, total):
    """The answer in the same clean embed style as the other replies: icon, @user smashed Name, a small text line with their total, a side bar."""
    icon = emo("smash") if choice == "smash" else emo("cross")  # the same icons as the buttons (none if the server doesn't have them)
    line, plural = sop_answer_text(user.mention, discord.utils.escape_markdown(name), choice, total)
    small = f"-# {total} {plural} in total"  # no name here: the @mention in the line above already shows who it is
    return discord.Embed(description=f"{icon} {line}\n{small}" if icon else f"{line}\n{small}",
                         color=SOP_SMASH_COLOR if choice == "smash" else SOP_PASS_COLOR)
sop_last = {}  # server id -> key of the last picture shown, so the same one doesn't come twice in a row


def sop_pool(gid):
    """[{name, url, path, key}] for a server: the built-in defaults plus what staff added. `url` or `path` is set, never both."""
    seen, out = set(), []
    items = [{"name": n, "url": u} for n, u in SMASHPASS_DEFAULTS] + list(smashpass.data.get(str(gid), []))
    for e in items:
        key = e.get("url") or e.get("file")
        if not key or key in seen:
            continue
        path = os.path.join(SOP_DIR, e["file"]) if e.get("file") else None
        if path is not None and not os.path.isfile(path):
            continue  # the file went missing (for example the data folder was wiped)
        seen.add(key)
        out.append({"name": e["name"], "url": e.get("url"), "path": path, "key": key})
    return out


def sop_link_error(url):
    """Why a link can't be used, or None. Discord only draws a picture inside an embed from a direct image link."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or len(url) > 500:
        return "Use a direct `https://` link to the picture."
    if not parsed.path.lower().endswith(SMASHPASS_EXTENSIONS):
        return "That isn't a direct image link. It has to end in `.jpg`, `.png`, `.webp` or `.gif`. You can also upload the picture with `sop upload`."
    return None


def sop_name_from(filename):
    """'emma_watson-2.jpg' -> 'emma watson 2' (the name shown when none was given)."""
    stem = os.path.splitext(filename)[0]
    return " ".join(stem.replace("_", " ").replace("-", " ").split())[:60] or "Unnamed"


def ic(name):
    """A custom emoji followed by a space, or nothing when the server doesn't have it."""
    e = emo(name)
    return e + " " if e else ""


SOP_ROUNDS = int(os.getenv("SOP_ROUNDS", "5"))              # pictures per ,sop session
SOP_VOTE_SECONDS = int(os.getenv("SOP_VOTE_SECONDS", "30"))  # how long each picture is open for votes
SOP_PAUSE_SECONDS = int(os.getenv("SOP_PAUSE_SECONDS", "3"))  # pause between the end of the votes and the next picture
sop_running = set()  # channel ids with a ,sop session going (one at a time per channel)


class SopRoundView(discord.ui.View):
    """Smash / Pass under one picture. Votes are only stored in memory (so a press answers instantly and can't time out),
    anybody can change their vote until the time is up, and the round message shows the result when it ends."""

    def __init__(self, entry):
        super().__init__(timeout=SOP_VOTE_SECONDS + 30)
        self.name, self.url, self.path = entry["name"], entry["url"], entry["path"]
        self.short = entry.get("short") or entry["name"]
        self.data = entry.get("data")
        ext = os.path.splitext(self.path)[1] if self.path else (entry.get("ext") or "")
        self.fname = "sop" + ext
        self.votes = {}      # user id -> "smash" / "pass"
        self.voters = {}     # user id -> member (for the result list)
        self.closed = False
        self.ends_at = 0
        self.message = None
        style_buttons(self, {"Smash": "smash", "Pass": "cross"})

    def build_embed(self, number, total):
        name = discord.utils.escape_markdown(self.name)
        embed = discord.Embed(description=f"**{name}**\n\n{ic('cooldown')}Voting ends <t:{int(self.ends_at)}:R>", color=theme())
        embed.set_image(url=f"attachment://{self.fname}" if (self.path or self.data) else self.url)
        embed.set_footer(text=f"Round {number}/{total}")
        return embed

    def build_result(self, number, total):
        """The votes, as their own embed (a new message under the picture)."""
        smash = [u for u, c in self.votes.items() if c == "smash"]
        passed = [u for u, c in self.votes.items() if c == "pass"]

        def names(ids):
            if not ids:
                return "nobody"
            out = ", ".join(self.voters[u].mention for u in ids[:20])
            return out + (f" +{len(ids) - 20} more" if len(ids) > 20 else "")
        if not self.votes:
            verdict = "Nobody voted."
        elif len(smash) > len(passed):
            verdict = "Chat says **SMASH**"
        elif len(passed) > len(smash):
            verdict = "Chat says **PASS**"
        else:
            verdict = "**Split decision**"
        embed = discord.Embed(description=(f"**{discord.utils.escape_markdown(self.name)}**\n\n"
                                           f"{ic('smash')}**Smash** ({len(smash)}): {names(smash)}\n"
                                           f"{ic('cross')}**Pass** ({len(passed)}): {names(passed)}\n\n{verdict}"), color=theme())
        thumb = self.message.attachments[0].url if (self.message is not None and self.message.attachments) else self.url
        if thumb:
            embed.set_thumbnail(url=thumb)
        embed.set_footer(text=f"Round {number}/{total} - votes are in")
        return embed

    async def cast(self, interaction, choice):
        # answer first, work after: this keeps every press inside Discord's 3 second window
        if self.closed:
            await interaction.response.send_message("Voting for this one is over.", ephemeral=True)
            return
        old = self.votes.get(interaction.user.id)
        self.votes[interaction.user.id] = choice
        self.voters[interaction.user.id] = interaction.user
        word = "Smash" if choice == "smash" else "Pass"
        await interaction.response.send_message(f"You voted **{word}**." if old is None else (f"Vote changed to **{word}**." if old != choice else f"You already voted **{word}**."), ephemeral=True)

    @discord.ui.button(label="Smash", style=discord.ButtonStyle.success)
    async def smash(self, interaction, button):
        await self.cast(interaction, "smash")

    @discord.ui.button(label="Pass", style=discord.ButtonStyle.secondary)
    async def pass_(self, interaction, button):
        await self.cast(interaction, "pass")


# --- random sources (age-gated) ---------------------------------------------
# Nothing here is "any random picture". Every candidate has to pass an age check first:
#   people: Wikidata, living, has a recorded birth date that makes them 18+ (and is not a year-only guess), known entertainers
#   anime:  AniList characters whose age is stated as a plain number of 18 or more ("17", "17-18", "Unknown" are all dropped)
# Anything that can't be verified is skipped. Staff can switch the sources per server (,sop source) and block a picture (,sop block).
SOP_SOURCES = ("anime", "people")
SOP_RANDOM_DEFAULT = [x for x in os.getenv("SOP_RANDOM_SOURCES", "anime,people").replace(" ", "").lower().split(",") if x in SOP_SOURCES]
SOP_MIN_AGE_PEOPLE = 18
SOP_MAX_AGE_PEOPLE = int(os.getenv("SOP_MAX_AGE_PEOPLE", "35"))  # random real people are young: 18 up to this age (the featured list below is exempt)
SOP_IRL_OCCUPATIONS = {"Q33999", "Q10800557", "Q10798782", "Q177220", "Q4610556", "Q2252262", "Q488205"}  # actor, film actor, TV actor, singer, model, rapper, singer-songwriter
SOP_MIN_AGE_ANIME = 18
SOP_REMOTE_TTL = 6 * 3600
SOP_REMOTE_HOSTS = ("wikimedia.org", "anilist.co", "myanimelist.net", "giphy.com", "tenor.com")  # pictures are only ever downloaded from these
SOP_UA = "SmashOrPassDiscordBot/1.0 (Discord bot; https://discord.com)"
SOP_BLOCK_MAX = 1000
SOP_WEIGHTS = {"people": int(os.getenv("SOP_WEIGHT_PEOPLE", "10")), "anime": int(os.getenv("SOP_WEIGHT_ANIME", "90"))}  # how often each source is picked
# picture quality: (smallest width, smallest height / width, biggest height / width). Portraits and squares only, so no wide group shots or tiny thumbnails.
SOP_MIN_FAME = int(os.getenv("SOP_MIN_FAME", "40"))  # a real person needs a Wikipedia page in at least this many languages (Trump, Sabrina Carpenter... have 100+)
SOP_QUALITY = {"people": (500, 0.85, 1.7), "anime": (200, 1.0, 2.0), "gif": (200, 0.5, 2.0)}
SOP_FEMALE_PERCENT = max(0, min(100, int(os.getenv("SOP_FEMALE_PERCENT", "65"))))  # how often a random picture is a woman / female character (65 = 65% female, 35% male)
_SOP_SEX_QIDS = {"Q6581072": "f", "Q1052281": "f", "Q6581097": "m", "Q2449503": "m"}  # Wikidata "sex or gender": female / trans woman, male / trans man


def sop_gender_code(raw):
    """'f' or 'm' from AniList's "Female"/"Male", a Wikidata sex-or-gender id (Q6581072...) or a plain word. Anything else (or unknown) is None."""
    text = str(raw or "").strip()
    if text in _SOP_SEX_QIDS:
        return _SOP_SEX_QIDS[text]
    text = text.lower()
    return "f" if text == "female" else "m" if text == "male" else None


SOP_MALE_ANIME_PERCENT = max(0, min(100, int(os.getenv("SOP_MALE_ANIME_PERCENT", "90"))))  # of the male pictures: this % anime, the rest real people (90 = 90% anime / 10% irl)


SOP_FEMALE_ANIME_PERCENT = max(0, min(100, int(os.getenv("SOP_FEMALE_ANIME_PERCENT", "90"))))  # of the female pictures: this % anime, the rest real women (90 = 90% anime / 10% irl)


def sop_weights_for(want):
    """How often each source is picked for this gender: males SOP_MALE_ANIME_PERCENT (90 / 10), females SOP_FEMALE_ANIME_PERCENT (90 / 10)."""
    anime = SOP_MALE_ANIME_PERCENT if want == "m" else SOP_FEMALE_ANIME_PERCENT
    return {"anime": anime, "people": 100 - anime}


def sop_want_gender():
    """Which gender this pick should be: female SOP_FEMALE_PERCENT % of the time, otherwise male."""
    return "f" if random.random() * 100 < SOP_FEMALE_PERCENT else "m"


def sop_prefer_gender(entries, want):
    """The entries of the wanted gender, or all of them when none of that gender is left (so a round never stalls)."""
    return [e for e in entries if e.get("gender") == want] or list(entries)
sop_cfg = SmashPassStore(os.path.join(DATA_DIR, "smashpass_cfg.json"))  # {server: {"sources": [...], "blocked": [keys]}}
sop_last_entry = {}  # server id -> the last picture shown {name, key, kind}, for ,sop block
_sop_cache = {}      # ("anime", page) / ("people", first birth year) -> (time, [entries])

ANILIST_QUERY = """query($page:Int){Page(page:$page,perPage:50){characters(sort:FAVOURITES_DESC){
id name{full} age gender image{large} media(perPage:1,sort:POPULARITY_DESC){nodes{title{romaji}}}}}}"""
WIKIDATA_QUERY = """SELECT ?item ?itemLabel ?image ?dob ?sex WHERE {
  VALUES ?occ { wd:Q33999 wd:Q10800557 wd:Q10798782 wd:Q177220 wd:Q4610556 wd:Q2252262 wd:Q488205 }
  ?item wdt:P31 wd:Q5; wdt:P106 ?occ; wdt:P18 ?image; wdt:P569 ?dob; wikibase:sitelinks ?links.
  OPTIONAL { ?item wdt:P21 ?sex. }
  FILTER NOT EXISTS { ?item wdt:P570 [] }
  FILTER(?links >= %(fame)d)
  FILTER(?dob >= "%(lo)d-01-01T00:00:00Z"^^xsd:dateTime && ?dob < "%(hi)d-01-01T00:00:00Z"^^xsd:dateTime)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
} LIMIT 250"""


SOP_JOKES = [
    "Be honest. Your mom is not in this chat. Probably.",
    "There are no wrong answers. Only questionable ones.",
    "Think carefully. This goes on your permanent record.",
    "Pass is also a valid answer, coward.",
    "You have 2 minutes. No pressure.",
    "Your answer says more about you than about them.",
    "The chat is watching. Choose wisely.",
    "Plot twist: they would pass on you too.",
    "Press a button. Live with the consequences.",
    "Be brave. Be honest. Be slightly unhinged.",
]

# --- picture cache: every good picture is kept on disk, so ,sop still works when the internet sources are down ---
SOP_CACHE_DIR = os.path.join(DATA_DIR, "sop_cache_v3")  # new folder: the old one still held pictures of older people
SOP_CACHE_MAX = 300    # pictures kept (oldest go first)
SOP_WARM_ON = os.getenv("SOP_WARM", "0") == "1"   # background cache filling is OFF by default (it used a lot of CPU on free hosting)
SOP_CHECK_AT_ONCE = int(os.getenv("SOP_CHECK_AT_ONCE", "1"))  # candidate pictures downloaded + checked at the same time (1 = lightest on CPU)
SOP_CACHE_WARM = 80    # keep filling the cache in the background until it has this many
SOP_DOWN_SECONDS = 300  # after a source fails completely it is skipped for this long (the cache is used meanwhile)
sop_cache_index = SmashPassStore(os.path.join(SOP_CACHE_DIR, "index.json"))  # {key: {name, short, kind, file, ext}}
_sop_down = {}         # source -> time it last failed completely
_sop_warming = False


sop_bad = SmashPassStore(os.path.join(DATA_DIR, "smashpass_bad.json"))  # {"hashes": [...]}: pictures that are never shown (placeholders, blocked ones)


def sop_hash(data):
    return hashlib.sha1(data).hexdigest()


def sop_bad_hashes():
    return sop_bad.data.setdefault("hashes", [])


def sop_mark_bad(h):
    """Never show a picture with this exact content again (a 'no image' placeholder or a blocked picture) and drop it from the cache."""
    bad = sop_bad_hashes()
    if h not in bad:
        bad.append(h)
        try:
            sop_bad.save()
        except OSError:
            log.warning("Could not save the bad picture list", exc_info=True)
    idx = sop_cache_index.data
    for k in [k for k, v in idx.items() if v.get("hash") == h]:
        info = idx.pop(k)
        try:
            os.remove(os.path.join(SOP_CACHE_DIR, info["file"]))
        except OSError:
            pass
    try:
        sop_cache_index.save()
    except OSError:
        pass


def sop_cache_fill_hashes():
    """Cached pictures from before hashes existed get one now."""
    for info in sop_cache_index.data.values():
        if "hash" not in info:
            try:
                with open(os.path.join(SOP_CACHE_DIR, info["file"]), "rb") as f:
                    info["hash"] = sop_hash(f.read())
            except OSError:
                info["hash"] = ""


def sop_is_shared_picture(h, key):
    """True when a different character already has exactly this picture: it is a placeholder, not a real portrait."""
    sop_cache_fill_hashes()
    return any(v.get("hash") == h and k != key for k, v in sop_cache_index.data.items())


async def sop_cache_put(entry):
    """Keep a downloaded, checked picture on disk (the file is written in a thread)."""
    key, idx = entry["key"], sop_cache_index.data
    if key in idx:
        return
    fname = re.sub(r"[^A-Za-z0-9_-]", "_", key) + entry["ext"]

    def write():
        os.makedirs(SOP_CACHE_DIR, exist_ok=True)
        with open(os.path.join(SOP_CACHE_DIR, fname), "wb") as f:
            f.write(entry["data"])
    try:
        await asyncio.to_thread(write)
    except OSError:
        log.warning("Could not cache the ,sop picture %s", key, exc_info=True)
        return
    idx[key] = {"name": entry["name"], "short": entry.get("short") or entry["name"], "kind": entry["kind"], "file": fname, "ext": entry["ext"], "hash": entry.get("hash") or sop_hash(entry["data"]),
                "gender": entry.get("gender")}
    while len(idx) > SOP_CACHE_MAX:
        info = idx.pop(next(iter(idx)))
        try:
            os.remove(os.path.join(SOP_CACHE_DIR, info["file"]))
        except OSError:
            pass
    await sop_cache_index.save_async()


def sop_cache_get(gid, kinds, want_gif=False, want=None):
    """A random picture from the cache (only the allowed sources, nothing blocked here, not the last one shown), or None."""
    blocked = set(sop_cfg_for(gid).get("blocked", []))
    idx = sop_cache_index.data
    sop_cache_fill_hashes()
    bad = set(sop_bad_hashes())
    keys = [k for k, v in idx.items() if v["kind"] in kinds and k not in blocked and k != sop_last.get(gid) and v.get("hash") not in bad]
    random.shuffle(keys)
    want = want or sop_want_gender()
    kinds_here = [k for k in kinds if any(idx[x]["kind"] == k for x in keys)]
    if len(kinds_here) > 1:  # both sources saved: pick one by the same weights as live pictures (males 80 / 20 anime / people)
        weights = sop_weights_for(want)
        pref = random.choices(kinds_here, weights=[max(weights[k], 1) for k in kinds_here])[0]
        keys.sort(key=lambda k: idx[k]["kind"] != pref)
    keys.sort(key=lambda k: idx[k].get("gender") != want)  # the wanted gender first (mostly female), the rest only if there is nothing else
    keys.sort(key=lambda k: k.startswith("gif:") != want_gif)  # the wanted kind first (gif or still), the rest only if there is nothing else
    for k in keys:
        info = idx[k]
        try:
            with open(os.path.join(SOP_CACHE_DIR, info["file"]), "rb") as f:
                data = f.read()
        except OSError:
            idx.pop(k, None)
            continue
        if not sop_is_complete(data):  # saved by an older version while cut off: throw it away
            idx.pop(k, None)
            try:
                os.remove(os.path.join(SOP_CACHE_DIR, info["file"]))
            except OSError:
                pass
            continue
        return {"name": info["name"], "short": info["short"], "url": None, "path": None, "data": data, "ext": info["ext"], "key": k, "kind": info["kind"], "hash": info.get("hash") or sop_hash(data), "gender": info.get("gender")}
    return None


async def sop_warm():
    """Quietly add pictures to the cache (runs in the background). Fills whichever source has fewer saved pictures first,
    and makes sure some Wikidata people lists are saved so people pictures are quick."""
    global _sop_warming
    if _sop_warming:
        return
    _sop_warming = True
    try:
        for _ in range(3):  # a few people lists, one at a time (Wikidata is slow and asks for patience)
            if len(sop_people_disk.data) >= 8:
                break
            try:
                await sop_people_window(random.randint(1950, datetime.date.today().year - SOP_MIN_AGE_PEOPLE - 5))
            except Exception:
                break
        for _ in range(2):  # and a couple of most-read days (quick)
            if len(sop_trend_disk.data) >= 6:
                break
            try:
                await sop_trending_day(datetime.date.today() - datetime.timedelta(days=random.randint(2, SOP_TREND_DAYS)))
            except Exception:
                break
        counts = {k: sum(1 for v in sop_cache_index.data.values() if v["kind"] == k) for k in SOP_SOURCES}
        for kind in sorted(SOP_SOURCES, key=lambda k: counts[k]):
            for _ in range(3):
                if len(sop_cache_index.data) >= SOP_CACHE_MAX:
                    return
                try:
                    if await asyncio.wait_for(sop_remote_entry(0, kind), timeout=60) is None:
                        break
                except Exception:
                    break
    finally:
        _sop_warming = False


try:  # optional: with opencv installed, a people picture must show exactly one clear face (no groups, no tiny faces)
    import cv2
    import numpy as np
    _sop_faces = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
except Exception:
    cv2 = None


def sop_one_face_sync(data):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    width = img.shape[1]
    faces = _sop_faces.detectMultiScale(img, 1.1, 5, minSize=(max(40, int(width * 0.12)),) * 2)
    return len(faces) == 1


async def sop_one_face(data):
    if cv2 is None:
        return True  # no opencv: the portrait shape check is all we have
    try:
        return await asyncio.to_thread(sop_one_face_sync, data)
    except Exception:
        return True


def sop_cfg_for(gid):
    return sop_cfg.data.setdefault(str(gid), {})


def sop_sources(gid):
    """The RANDOM sources that are on in this server: some of "anime", "people" (real people, irl)."""
    chosen = sop_cfg_for(gid).get("sources")
    return list(SOP_RANDOM_DEFAULT if chosen is None else chosen)


# The three picture sources the server owner can switch on and off (`sop source enable|disable ...`), in the order they are shown:
#   people   = real people (irl), random      anime = anime characters, random      uploaded = the server's own list (`sop upload` / `sop add`)
SOP_TOGGLES = ("people", "anime", "uploaded")
SOP_SOURCE_LABELS = {"people": "people irl", "anime": "anime", "uploaded": "uploaded pics"}
SOP_LIST_PERCENT = max(0, min(100, int(os.getenv("SOP_LIST_PERCENT", "30"))))  # when uploaded pics AND a random source are on: this % of the pictures come from the server's list


def sop_list_on(gid):
    """True when the server's own list (uploaded pics + added links) is switched on. On unless the owner turned it off."""
    return sop_cfg_for(gid).get("list", True) is not False


def sop_enabled(gid):
    """The sources that are on, as a set of SOP_TOGGLES names."""
    return set(sop_sources(gid)) | ({"uploaded"} if sop_list_on(gid) else set())


def sop_active_pool(gid):
    """The server's list as picture candidates: empty when "uploaded pics" is switched off."""
    return sop_pool(gid) if sop_list_on(gid) else []


def sop_off_note(gid):
    """A short line for after `sop add` / `sop upload` when "uploaded pics" is switched off (the new pictures won't be shown yet)."""
    if sop_list_on(gid):
        return ""
    return f"\nNote: **uploaded pics** is turned off, so they won't show until the server owner uses `{prefix_for('sop')}sop source enable uploaded`."


def sop_nothing_text(gid):
    """What ,sop says when it has nothing to show."""
    p = prefix_for("sop")
    if not sop_list_on(gid) and not sop_sources(gid):
        return f"Every picture source is off. The server owner can turn one on with `{p}sop source enable anime`, `{p}sop source enable people` or `{p}sop source enable uploaded`."
    return (f"There is nobody on the list yet. Staff can add pictures with `{p}sop upload` (attach them) or `{p}sop add <picture link> <name>`, "
            f"or the server owner can turn on random pictures with `{p}sop source enable anime` / `{p}sop source enable people`.")


def sop_stated_age(text, minimum):
    """AniList gives age as free text. It needs at least one number and EVERY number in it must be >= minimum: '19', '30-34', '24 (Part II)' pass;
    '17', '17-18', '15 (Pre-timeskip), 19 (Post-timeskip)' and 'Unknown' don't (a character who is under 18 at any stage is never used). Returns the lowest age, or None."""
    nums = [int(n) for n in re.findall(r"\d{1,4}", str(text or ""))]
    if not nums or min(nums) < minimum:
        return None
    return min(nums)


def sop_adult_by_birthdate(dob, minimum, today=None):
    """dob like '1985-03-12T00:00:00Z'. Wikidata fills unknown month/day with 01-01, so the birth YEAR also has to be at least minimum + 1 years back."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(dob or ""))
    if not m:
        return False
    today = today or datetime.date.today()
    try:
        born = datetime.date(int(m.group(1)), max(int(m.group(2)), 1), max(int(m.group(3)), 1))
    except ValueError:
        return False
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return years >= minimum and born.year <= today.year - minimum - 1


def sop_image_size(data):
    """(width, height) read from the picture's header (png / jpeg / gif / webp), or None. No image library needed."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X":
                return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
            if kind == b"VP8 ":
                return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
            if kind == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
                i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    except Exception:
        pass
    return None


def sop_good_picture(data, kind):
    """True for a clear portrait-style picture: big enough and not a wide shot."""
    size = sop_image_size(data)
    if not size or not size[0] or not size[1]:
        return False
    min_w, min_ratio, max_ratio = SOP_QUALITY[kind]
    width, height = size
    return width >= min_w and min_ratio <= height / width <= max_ratio


def sop_commons_url(raw):
    """P18 values look like http://commons.wikimedia.org/wiki/Special:FilePath/Name.jpg. Returns a https link to a resized copy, or None for formats we don't send."""
    parsed = urlparse(str(raw or ""))
    if parsed.netloc != "commons.wikimedia.org" or not parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        return None
    return f"https://commons.wikimedia.org{parsed.path}?width=800"


async def sop_anime_page(page):
    cached = _sop_cache.get(("anime", page))
    if cached and time.time() - cached[0] < SOP_REMOTE_TTL:
        return cached[1]
    out = []
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA}, timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post("https://graphql.anilist.co", json={"query": ANILIST_QUERY, "variables": {"page": page}}) as resp:
                resp.raise_for_status()
                payload = await resp.json()
        for c in payload["data"]["Page"]["characters"]:
            image = (c.get("image") or {}).get("large") or ""
            name = ((c.get("name") or {}).get("full") or "").strip()
            if not name or not image.startswith("https://") or "default" in image.lower():
                continue
            if sop_stated_age(c.get("age"), SOP_MIN_AGE_ANIME) is None:
                continue
            nodes = ((c.get("media") or {}).get("nodes")) or []
            series = ((nodes[0].get("title") or {}).get("romaji") if nodes else "") or ""
            label = f"{name} ({series})" if series else name
            out.append({"name": label[:80], "short": name[:60], "url": None, "path": None, "remote": image, "key": f"anilist:{c['id']}", "kind": "anime",
                        "gender": sop_gender_code(c.get("gender"))})
    except Exception:
        log.warning("Could not load anime characters for ,sop (page %s)", page, exc_info=True)
        return []
    _sop_cache[("anime", page)] = (time.time(), out)
    return out


sop_people_disk = SmashPassStore(os.path.join(DATA_DIR, "sop_people_v5.json"))  # {first birth year: {"t": time saved, "e": [entries]}}: Wikidata is slow, so every answer is kept for a week
SOP_PEOPLE_DISK_TTL = 7 * 24 * 3600
_sop_inflight = {}  # first birth year -> the running Wikidata request (it keeps going even if one ,sop gave up waiting, so the next one finds it done)


async def sop_people_window(lo):
    cached = _sop_cache.get(("people", lo))
    if cached and time.time() - cached[0] < SOP_REMOTE_TTL:
        return cached[1]
    saved = sop_people_disk.data.get(str(lo))
    if saved and time.time() - saved.get("t", 0) < SOP_PEOPLE_DISK_TTL and saved.get("e"):
        _sop_cache[("people", lo)] = (time.time(), saved["e"])
        return saved["e"]
    task = _sop_inflight.get(lo)
    if task is None or task.done():
        task = asyncio.create_task(_sop_people_fetch(lo))
        _sop_inflight[lo] = task
        task.add_done_callback(lambda _t, lo=lo: _sop_inflight.pop(lo, None))
    return await asyncio.shield(task)


async def _sop_people_fetch(lo):
    hi = min(lo + 5, datetime.date.today().year - SOP_MIN_AGE_PEOPLE)
    out, seen = [], set()
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA, "Accept": "application/sparql-results+json"},
                                         timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get("https://query.wikidata.org/sparql",
                                   params={"format": "json", "query": WIKIDATA_QUERY % {"lo": lo, "hi": hi, "fame": SOP_MIN_FAME}}) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        for row in payload["results"]["bindings"]:
            qid = row["item"]["value"].rsplit("/", 1)[-1]
            name = row.get("itemLabel", {}).get("value", "").strip()
            image = sop_commons_url(row["image"]["value"])
            if qid in seen or not image or not name or re.fullmatch(r"Q\d+", name):
                continue
            if not sop_adult_by_birthdate(row["dob"]["value"], SOP_MIN_AGE_PEOPLE):
                continue
            seen.add(qid)
            sex = row.get("sex", {}).get("value", "").rsplit("/", 1)[-1]
            out.append({"name": name[:80], "url": None, "path": None, "remote": image, "key": f"wikidata:{qid}", "kind": "people",
                        "gender": sop_gender_code(sex), "born": str(row["dob"]["value"])[:10]})
    except Exception:
        log.warning("Could not load people for ,sop (born %s-%s)", lo, hi, exc_info=True)
        return []
    _sop_cache[("people", lo)] = (time.time(), out)
    if out:
        sop_people_disk.data[str(lo)] = {"t": time.time(), "e": out}
        try:
            sop_people_disk.save()
        except OSError:
            log.warning("Could not save the people list", exc_info=True)
    return out


# --- second source of real people: Wikipedia's most-read articles of a day (fast, famous right now), checked through the Wikidata API ---
# Every article is looked up on Wikidata: it has to be a human, alive, with a birth date known to the day that makes them 18+, and a picture.
sop_trend_disk = SmashPassStore(os.path.join(DATA_DIR, "sop_trending_v5.json"))  # {"YYYY-MM-DD": [entries]}: a past day never changes, so it is kept
SOP_TREND_DAYS = 45


async def _sop_trend_json(session, url, params=None):
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def sop_trending_day(day):
    """People from the most-read English Wikipedia articles of one day (a datetime.date). [] if anything fails."""
    key = day.isoformat()
    cached = _sop_cache.get(("trend", key))
    if cached:
        return cached[1]
    saved = sop_trend_disk.data.get(key)
    if saved:
        _sop_cache[("trend", key)] = (time.time(), saved)
        return saved
    out = []
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA}, timeout=aiohttp.ClientTimeout(total=25)) as session:
            top = await _sop_trend_json(session, f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{day:%Y/%m/%d}")
            titles = [a["article"].replace("_", " ") for a in top["items"][0]["articles"][:400]
                      if ":" not in a["article"] and a["article"] not in ("Main_Page", "-")]

            async def qids_for(chunk):
                data = await _sop_trend_json(session, "https://en.wikipedia.org/w/api.php", {
                    "action": "query", "format": "json", "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1, "titles": "|".join(chunk)})
                return [p["pageprops"]["wikibase_item"] for p in (data.get("query", {}).get("pages") or {}).values() if "pageprops" in p and "wikibase_item" in p["pageprops"]]

            groups = await asyncio.gather(*(qids_for(titles[i:i + 50]) for i in range(0, len(titles), 50)), return_exceptions=True)
            qids = [q for g in groups if isinstance(g, list) for q in g]

            async def entities_for(chunk):
                data = await _sop_trend_json(session, "https://www.wikidata.org/w/api.php", {
                    "action": "wbgetentities", "format": "json", "ids": "|".join(chunk), "props": "claims|labels|sitelinks", "languages": "en"})
                return list((data.get("entities") or {}).values())

            groups = await asyncio.gather(*(entities_for(qids[i:i + 50]) for i in range(0, len(qids), 50)), return_exceptions=True)
        for ent in [e for g in groups if isinstance(g, list) for e in g]:
            claims = ent.get("claims") or {}

            def first(prop):
                for c in claims.get(prop, []):
                    v = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
                    if v is not None:
                        return v
                return None

            if not any(((c.get("mainsnak") or {}).get("datavalue", {}).get("value") or {}).get("id") == "Q5" for c in claims.get("P31", [])):
                continue  # not a human
            if "P570" in claims:
                continue  # has a date of death
            if len(ent.get("sitelinks") or {}) < SOP_MIN_FAME:
                continue  # not famous enough
            dob = first("P569")
            if not isinstance(dob, dict) or int(dob.get("precision", 0)) < 11 or not sop_adult_by_birthdate(str(dob.get("time", "")).lstrip("+"), SOP_MIN_AGE_PEOPLE):
                continue  # birth date unknown, only a year or month, or not 18+
            try:
                born_year = int(str(dob.get("time", "")).lstrip("+")[:4])
            except ValueError:
                continue
            if born_year < datetime.date.today().year - SOP_MAX_AGE_PEOPLE:
                continue  # too old: random real people are young
            jobs = {((c.get("mainsnak") or {}).get("datavalue", {}).get("value") or {}).get("id") for c in claims.get("P106", [])}
            if not jobs & SOP_IRL_OCCUPATIONS:
                continue  # only actors, singers, models and rappers
            image = first("P18")
            name = ((ent.get("labels") or {}).get("en") or {}).get("value", "").strip()
            if not isinstance(image, str) or not name or re.fullmatch(r"Q\d+", name):
                continue
            url = sop_commons_url("https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(image.replace(" ", "_")))
            if not url:
                continue
            sex = first("P21")
            out.append({"name": name[:80], "url": None, "path": None, "remote": url, "key": f"wikidata:{ent['id']}", "kind": "people",
                        "gender": sop_gender_code(sex.get("id") if isinstance(sex, dict) else None),
                        "born": str(dob.get("time", "")).lstrip("+")[:10]})
    except Exception:
        log.warning("Could not load the most-read people for ,sop (%s)", key, exc_info=True)
        return []
    _sop_cache[("trend", key)] = (time.time(), out)
    if out:
        sop_trend_disk.data[key] = out
        for old in sorted(sop_trend_disk.data)[:-SOP_TREND_DAYS]:
            sop_trend_disk.data.pop(old, None)
        try:
            sop_trend_disk.save()
        except OSError:
            log.warning("Could not save the most-read people list", exc_info=True)
    return out


async def sop_people_pool():
    """Candidates of real people from one of two sources: the most-read Wikipedia articles of a random recent day (quick),
    or a Wikidata list by birth years (slow, saved for a week). The other one is used if the first gives nothing."""
    trend_first = random.random() < 0.6 or not sop_people_disk.data
    order = ("trend", "wikidata") if trend_first else ("wikidata", "trend")
    for src in order:
        if src == "trend":
            day = datetime.date.today() - datetime.timedelta(days=random.randint(2, SOP_TREND_DAYS))
            pool = await sop_trending_day(day)
        else:
            this_year = datetime.date.today().year
            pool = await sop_people_window(random.randint(max(1950, this_year - SOP_MAX_AGE_PEOPLE), this_year - SOP_MIN_AGE_PEOPLE - 5))
        if pool:
            return pool
    return []


# --- featured people: famous (and infamous) adults that come up a lot. Looked up by name on Wikidata, picture from Wikimedia Commons ---
# (name to search, name shown, "m" / "f"). Add or remove lines freely. Anyone without a free picture on Wikimedia, or without a known birth date
# that makes them 18+, is skipped automatically. Not affected by the age limit above.
SOP_FEATURED = [
    # famous / infamous, for fun
    ("Donald Trump", "Donald Trump", "m"),
    ("Sean Combs", "Diddy", "m"),
    ("Benjamin Netanyahu", "Benjamin Netanyahu", "m"),
    ("Vladimir Putin", "Vladimir Putin", "m"),
    ("Saddam Hussein", "Saddam Hussein", "m"),
    ("Stephen Hawking", "Stephen Hawking", "m"),
    # footballers
    ("Cristiano Ronaldo", "Cristiano Ronaldo", "m"),
    ("Lionel Messi", "Lionel Messi", "m"),
    ("Kylian Mbappé", "Kylian Mbappé", "m"),
    ("Neymar", "Neymar", "m"),
    ("Erling Haaland", "Erling Haaland", "m"),
    ("Vinícius Júnior", "Vinícius Júnior", "m"),
    ("Jude Bellingham", "Jude Bellingham", "m"),
    ("Phil Foden", "Phil Foden", "m"),
    ("Bukayo Saka", "Bukayo Saka", "m"),
    ("Pedri", "Pedri", "m"),
    ("Lamine Yamal", "Lamine Yamal", "m"),
    ("Michael Olise", "Michael Olise", "m"),
    # young good-looking men (the 4th value, when given, is a job the right person must have: here actor)
    ("Tom Holland", "Tom Holland", "m", {"Q33999", "Q10800557", "Q10798782"}),
    ("Timothée Chalamet", "Timothée Chalamet", "m"),
    ("Jacob Elordi", "Jacob Elordi", "m"),
    ("Harry Styles", "Harry Styles", "m"),
    ("Austin Butler", "Austin Butler", "m"),
    ("Paul Mescal", "Paul Mescal", "m"),
    ("Jungkook", "Jungkook", "m"),
    ("Cha Eun-woo", "Cha Eun-woo", "m"),
    ("Shawn Mendes", "Shawn Mendes", "m"),
    # young good-looking women
    ("Sabrina Carpenter", "Sabrina Carpenter", "f"),
    ("Bonnie Blue", "Bonnie Blue", "f"),
    ("Zendaya", "Zendaya", "f"),
    ("Sydney Sweeney", "Sydney Sweeney", "f"),
    ("Olivia Rodrigo", "Olivia Rodrigo", "f"),
    ("Billie Eilish", "Billie Eilish", "f"),
    ("Jenna Ortega", "Jenna Ortega", "f"),
    ("Anya Taylor-Joy", "Anya Taylor-Joy", "f"),
    ("Madison Beer", "Madison Beer", "f"),
    ("Addison Rae", "Addison Rae", "f"),
    ("Dua Lipa", "Dua Lipa", "f"),
    ("Emma Watson", "Emma Watson", "f"),
    ("Bella Hadid", "Bella Hadid", "f"),
    ("Margot Robbie", "Margot Robbie", "f"),
]
SOP_FEATURED_PERCENT = max(0, min(100, int(os.getenv("SOP_FEATURED_PERCENT", "90"))))  # of the real-person pictures, this % come from the featured list (the rest: random young actors / singers / models)
SOP_FEATURED_TTL = 30 * 24 * 3600
sop_featured_disk = SmashPassStore(os.path.join(DATA_DIR, "sop_featured_v2.json"))  # {search name: {"t": time, "e": entry}}
_sop_featured_failed = {}  # search name -> time of the last failed lookup (tried again after an hour)


async def sop_featured_lookup(search, shown, gender, jobs=None):
    """One featured person -> an entry, or None. Must be a human with a known birth date that makes them 18+ and a free picture."""
    async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA}, timeout=aiohttp.ClientTimeout(total=20)) as session:
        found = await _sop_trend_json(session, "https://www.wikidata.org/w/api.php", {
            "action": "wbsearchentities", "format": "json", "language": "en", "type": "item", "limit": 10, "search": search})
        hits = [r for r in found.get("search", []) if str((r.get("match") or {}).get("text", "")).strip().lower() == search.lower()]
        if not hits:
            return None
        data = await _sop_trend_json(session, "https://www.wikidata.org/w/api.php", {
            "action": "wbgetentities", "format": "json", "ids": "|".join(r["id"] for r in hits), "props": "claims|sitelinks", "languages": "en"})
    best = None
    for r in hits:
        ent = (data.get("entities") or {}).get(r["id"]) or {}
        claims = ent.get("claims") or {}

        def first(prop):
            for c in claims.get(prop, []):
                v = (c.get("mainsnak") or {}).get("datavalue", {}).get("value")
                if v is not None:
                    return v
            return None

        if not any(((c.get("mainsnak") or {}).get("datavalue", {}).get("value") or {}).get("id") == "Q5" for c in claims.get("P31", [])):
            continue  # not a human
        if jobs and not jobs & {((c.get("mainsnak") or {}).get("datavalue", {}).get("value") or {}).get("id") for c in claims.get("P106", [])}:
            continue  # the wrong person with the same name (for example the historian Tom Holland)
        dob, image = first("P569"), first("P18")
        if not isinstance(dob, dict) or not isinstance(image, str) or not sop_adult_by_birthdate(str(dob.get("time", "")).lstrip("+"), SOP_MIN_AGE_PEOPLE):
            continue
        url = sop_commons_url("https://commons.wikimedia.org/wiki/Special:FilePath/" + quote(image.replace(" ", "_")))
        if not url:
            continue
        links = len(ent.get("sitelinks") or {})
        if best is None or links > best[0]:
            best = (links, {"name": shown[:80], "short": shown[:60], "url": None, "path": None, "remote": url, "key": f"wikidata:{r['id']}", "kind": "people", "gender": gender,
                                        "born": str(dob.get("time", "")).lstrip("+")[:10]})
    return best[1] if best else None


async def sop_featured_pool():
    """The featured people that could be found (looked up once, kept for 30 days). [] if none."""
    out, todo = [], []
    for t in SOP_FEATURED:
        search = t[0]
        saved = sop_featured_disk.data.get(search)
        if saved and saved.get("e") and time.time() - saved.get("t", 0) < SOP_FEATURED_TTL:
            out.append(saved["e"])
        elif time.time() - _sop_featured_failed.get(search, 0) > 3600:
            todo.append(t)
    if todo:
        todo = todo[:6]  # a few new lookups per picture, so the first ,sop isn't slow and Wikidata isn't flooded; the rest follow on the next ones
        results = await asyncio.gather(*(sop_featured_lookup(*t) for t in todo), return_exceptions=True)
        changed = False
        for t, res in zip(todo, results):
            search = t[0]
            if isinstance(res, dict):
                sop_featured_disk.data[search] = {"t": time.time(), "e": res}
                out.append(res)
                changed = True
            else:
                _sop_featured_failed[search] = time.time()
                if isinstance(res, Exception):
                    log.warning("Could not look up the featured person %s for ,sop", search, exc_info=res)
                else:
                    log.info("No usable picture or birth date found for the featured person %s, skipping", search)
        if changed:
            try:
                sop_featured_disk.save()
            except OSError:
                log.warning("Could not save the featured people", exc_info=True)
    return out


# --- featured anime characters: well-known adults, looked up by name on AniList. They go through the SAME age check as every other character
# (every age AniList lists must be 18+), so anyone who is under 18 at any stage of their story is skipped automatically. ---
SOP_ANIME_FEATURED = [
    "Monkey D. Luffy", "Son Goku", "Ken Kaneki", "Satoru Gojo", "Levi Ackerman", "Roronoa Zoro", "Sanji", "Itachi Uchiha", "Kakashi Hatake",
    "Vegeta", "Osamu Dazai", "Makima", "Yor Forger", "Nami", "Nico Robin", "Boa Hancock", "Esdeath", "Erza Scarlet", "Rangiku Matsumoto",
    "Yoruichi Shihouin", "Tsunade", "Mitsuri Kanroji", "Shanks", "Trafalgar Law", "Eustass Kid", "Dabi", "Hawks", "Rengoku Kyojuro",
]
SOP_ANIME_FEATURED_PERCENT = max(0, min(100, int(os.getenv("SOP_ANIME_FEATURED_PERCENT", "35"))))  # of the anime pictures, this % come from the list above
ANILIST_SEARCH_QUERY = """query($s:String){Character(search:$s){id name{full} age gender image{large} media(perPage:1,sort:POPULARITY_DESC){nodes{title{romaji}}}}}"""
sop_anime_featured_disk = SmashPassStore(os.path.join(DATA_DIR, "sop_anime_featured_v1.json"))  # {search name: {"t": time, "e": entry}}
_sop_anime_featured_failed = {}


async def sop_anime_lookup(search):
    """One featured anime character -> an entry, or None (not found, no real picture, or not 18+ at every listed age)."""
    async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA}, timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post("https://graphql.anilist.co", json={"query": ANILIST_SEARCH_QUERY, "variables": {"s": search}}) as resp:
            resp.raise_for_status()
            payload = await resp.json()
    c = ((payload or {}).get("data") or {}).get("Character")
    if not c:
        return None
    image = (c.get("image") or {}).get("large") or ""
    name = ((c.get("name") or {}).get("full") or "").strip()
    if not name or not image.startswith("https://") or "default" in image.lower():
        return None
    if search.split()[-1].lower() not in name.lower():
        return None  # AniList gave a different character
    if sop_stated_age(c.get("age"), SOP_MIN_AGE_ANIME) is None:
        return None
    nodes = ((c.get("media") or {}).get("nodes")) or []
    series = ((nodes[0].get("title") or {}).get("romaji") if nodes else "") or ""
    label = f"{name} ({series})" if series else name
    return {"name": label[:80], "short": name[:60], "url": None, "path": None, "remote": image, "key": f"anilist:{c['id']}", "kind": "anime",
            "gender": sop_gender_code(c.get("gender"))}


async def sop_anime_featured_pool():
    """The featured anime characters that were found and passed the age check (looked up once, kept 30 days). [] if none."""
    out, todo = [], []
    for search in SOP_ANIME_FEATURED:
        saved = sop_anime_featured_disk.data.get(search)
        if saved and saved.get("e") and time.time() - saved.get("t", 0) < SOP_FEATURED_TTL:
            out.append(saved["e"])
        elif time.time() - _sop_anime_featured_failed.get(search, 0) > 3600:
            todo.append(search)
    if todo:
        todo = todo[:6]
        results = await asyncio.gather(*(sop_anime_lookup(t) for t in todo), return_exceptions=True)
        changed = False
        for search, res in zip(todo, results):
            if isinstance(res, dict):
                sop_anime_featured_disk.data[search] = {"t": time.time(), "e": res}
                out.append(res)
                changed = True
            else:
                _sop_anime_featured_failed[search] = time.time()
                if isinstance(res, Exception):
                    log.warning("Could not look up the featured character %s for ,sop", search, exc_info=res)
                else:
                    log.info("Skipping the featured character %s (not found, no picture, or not 18+ at every listed age)", search)
        if changed:
            try:
                sop_anime_featured_disk.save()
            except OSError:
                log.warning("Could not save the featured characters", exc_info=True)
    return out


def sop_is_complete(data):
    """False for a picture that was cut off (it would show only the top part and then grey)."""
    try:
        if data[:2] == b"\xff\xd8":
            return data.rstrip(b"\x00").endswith(b"\xff\xd9")
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return b"IEND" in data[-16:]
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return data.rstrip(b"\x00")[-1:] == b";"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return int.from_bytes(data[4:8], "little") + 8 <= len(data)
    except Exception:
        return False
    return False


async def sop_download(url):
    """Download a picture from one of SOP_REMOTE_HOSTS. Returns (bytes, extension) or raises."""
    host = urlparse(url).netloc.lower()
    if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in SOP_REMOTE_HOSTS):
        raise ValueError("picture host not allowed")
    async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA}, timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.get(url, allow_redirects=True) as resp:
            resp.raise_for_status()
            ext = SOP_IMAGE_TYPES.get(resp.headers.get("Content-Type", "").split(";")[0].strip().lower())
            if ext is None:
                raise ValueError("not a picture")
            expected = resp.content_length
            buf = bytearray()
            async for chunk in resp.content.iter_chunked(65536):  # read() only gives what has arrived so far, which cut pictures in half
                buf += chunk
                if len(buf) > SOP_MAX_BYTES:
                    raise ValueError("picture too big")
    data = bytes(buf)
    if not data or (expected and len(data) != expected) or not sop_is_complete(data):
        raise ValueError("picture is cut off or empty")
    return data, ext


async def sop_jikan_candidate():
    """A second anime source (MyAnimeList through Jikan), used when AniList fails. Needs a stated age of 18+ in the character's text and many fans."""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA}, timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get("https://api.jikan.moe/v4/random/characters") as resp:
                resp.raise_for_status()
                c = (await resp.json())["data"]
    except Exception:
        log.warning("Could not load a Jikan character for ,sop", exc_info=True)
        return None
    ages = re.findall(r"(?i)\bage\s*:\s*(\d{1,3})\b", c.get("about") or "")
    if not ages or any(sop_stated_age(a, SOP_MIN_AGE_ANIME) is None for a in ages):
        return None
    image = ((c.get("images") or {}).get("jpg") or {}).get("image_url") or ""
    name = (c.get("name") or "").strip()
    if (c.get("favorites") or 0) < 300 or not name or not image.startswith("https://") or "questionmark" in image:
        return None
    sex = re.search(r"(?i)\b(?:gender|sex)\s*:\s*(female|male)\b", c.get("about") or "")
    return {"name": name[:80], "short": name[:60], "url": None, "path": None, "remote": image, "key": f"mal:{c['mal_id']}", "kind": "anime",
            "gender": sop_gender_code(sex.group(1) if sex else None)}


# --- gifs: about 20% of the pictures are a gif of a character / person that already passed the age check ---------------
# Needs a free key in .env: GIPHY_API_KEY (developers.giphy.com) and/or TENOR_API_KEY (Google Tenor v2). Without a key gifs are simply off.
SOP_GIF_PERCENT = max(0, min(100, int(os.getenv("SOP_GIF_PERCENT", "20"))))
GIPHY_API_KEY = os.getenv("GIPHY_API_KEY", "").strip()
TENOR_API_KEY = os.getenv("TENOR_API_KEY", "").strip()
SOP_GIF_MAX_BYTES = 6 * 1024 * 1024


async def sop_gif_results(query):
    """[(id, https url)] of gifs for a search, from Giphy (rated PG-13 or lower) and / or Tenor (medium content filter)."""
    out = []
    async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA}, timeout=aiohttp.ClientTimeout(total=10)) as session:
        if GIPHY_API_KEY:
            try:
                async with session.get("https://api.giphy.com/v1/gifs/search", params={"api_key": GIPHY_API_KEY, "q": query, "limit": 12, "rating": "pg-13", "lang": "en"}) as resp:
                    resp.raise_for_status()
                    for g in (await resp.json()).get("data", []):
                        img = (g.get("images") or {}).get("downsized") or (g.get("images") or {}).get("fixed_height") or {}
                        if str(img.get("url", "")).startswith("https://"):
                            out.append((f"giphy-{g['id']}", img["url"]))
            except Exception:
                log.warning("Giphy search failed for ,sop", exc_info=True)
        if TENOR_API_KEY:
            try:
                async with session.get("https://tenor.googleapis.com/v2/search", params={"key": TENOR_API_KEY, "client_key": "sop_bot", "q": query, "limit": 12, "contentfilter": "medium", "media_filter": "gif"}) as resp:
                    resp.raise_for_status()
                    for g in (await resp.json()).get("results", []):
                        url = ((g.get("media_formats") or {}).get("gif") or {}).get("url", "")
                        if url.startswith("https://"):
                            out.append((f"tenor-{g['id']}", url))
            except Exception:
                log.warning("Tenor search failed for ,sop", exc_info=True)
    return out


async def sop_gif_entry(gid, kind, want=None):
    """A gif for a random character / person that passed the age check (anime 18+ / people 18+), or None. The gif is whatever the search finds for that name."""
    if not (GIPHY_API_KEY or TENOR_API_KEY):
        return None
    blocked = set(sop_cfg.data.get(str(gid), {}).get("blocked", []))
    for _ in range(4):
        if kind == "anime":
            pool = await sop_anime_page(random.randint(1, 40))
        else:
            pool = await sop_people_pool()
        if not pool:
            continue
        base = random.choice(sop_prefer_gender(pool, want or sop_want_gender()))
        if kind == "anime":
            m = re.fullmatch(r"(.*) \((.*)\)", base["name"])
            query = f"{m.group(1)} {m.group(2)} anime" if m else f"{base['name']} anime"
        else:
            query = base["name"]
        try:
            results = await sop_gif_results(query)
        except Exception:
            continue
        random.shuffle(results)
        for gif_id, url in results[:4]:
            key = f"gif:{base['key']}:{gif_id}"
            if key in blocked or key == sop_last.get(gid):
                continue
            try:
                data, ext = await sop_download(url)
            except Exception:
                continue
            if ext != ".gif" or len(data) > SOP_GIF_MAX_BYTES or not sop_good_picture(data, "gif"):
                continue
            h = sop_hash(data)
            if h in sop_bad_hashes():
                continue
            entry = {"name": base["name"], "short": base.get("short") or base["name"], "url": None, "path": None, "data": data, "ext": ext, "key": key, "kind": kind, "hash": h,
                     "gender": base.get("gender")}
            await sop_cache_put(entry)
            return entry
    return None


async def sop_timed(coro, seconds):
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        return None
    except Exception:
        log.warning("A ,sop picture source failed", exc_info=True)
        return None


_sop_photo_years = {}  # picture file name -> year the photo was taken (None = unknown)


async def sop_photo_year(remote):
    """The year a Wikimedia Commons photo was TAKEN (its own 'date taken' field, not the upload date), or None when unknown."""
    name = unquote(urlparse(remote).path.rsplit("/", 1)[-1])
    if name in _sop_photo_years:
        return _sop_photo_years[name]
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": SOP_UA}, timeout=aiohttp.ClientTimeout(total=15)) as session:
            data = await _sop_trend_json(session, "https://commons.wikimedia.org/w/api.php", {
                "action": "query", "format": "json", "titles": "File:" + name, "prop": "imageinfo",
                "iiprop": "extmetadata", "iiextmetadatafilter": "DateTimeOriginal"})
        year = None
        for page in ((data.get("query") or {}).get("pages") or {}).values():
            meta = ((page.get("imageinfo") or [{}])[0].get("extmetadata") or {}).get("DateTimeOriginal") or {}
            years = [int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(meta.get("value", "")))]
            if years:
                year = min(years)  # if the text gives several years, the earliest counts (the strictest)
    except Exception:
        log.warning("Could not read the date of the picture %s", name, exc_info=True)
        return None
    _sop_photo_years[name] = year
    return year


async def sop_photo_adult(entry):
    """False when the picture could show the person under 18. Needs the birth date; for anyone born in the last 40 years the photo's own
    date must be known and its year must be at least 19 years after the birth year (so they were 18+ for sure). Pictures with no date are skipped."""
    m = re.match(r"(\d{4})", str(entry.get("born") or ""))
    if not m:
        return False
    born_year = int(m.group(1))
    if born_year <= datetime.date.today().year - 40:
        return True
    year = await sop_photo_year(entry["remote"])
    return year is not None and year - born_year >= 19


async def sop_check_candidate(entry, kind):
    """Download one candidate and check it. Returns the finished entry, or None (and the reason is logged)."""
    if kind == "people" and not await sop_photo_adult(entry):
        return None  # the photo may show them under 18 (or has no date): never used
    try:
        data, ext = await sop_download(entry["remote"])
    except Exception:
        log.warning("Could not download the ,sop picture %s", entry["key"], exc_info=True)
        return None
    h = await asyncio.to_thread(sop_hash, data)
    if h in sop_bad_hashes():
        return None
    if sop_is_shared_picture(h, entry["key"]):
        sop_mark_bad(h)  # the same picture on two different characters: it's the "no image" placeholder
        return None
    if not await asyncio.to_thread(sop_good_picture, data, kind) or (kind == "people" and not await sop_one_face(data)):
        return None  # too small, a wide shot or not exactly one face
    return dict(entry, data=data, ext=ext, hash=h)


async def sop_remote_entry(gid, kind, want=None):
    """A random, age-checked, downloaded, good-looking picture from one source, or None. Checks 3 candidates at a time,
    tries many rounds, and anime also falls back to a second site."""
    blocked = set(sop_cfg.data.get(str(gid), {}).get("blocked", []))
    last = sop_last.get(gid)
    bad = set()
    for attempt in range(8):
        if kind == "anime" and attempt >= 3:
            one = await sop_jikan_candidate()  # AniList keeps failing: try the other site
            picks = [one] if one else []
        else:
            if kind == "anime":
                pool = await sop_anime_featured_pool() if random.random() * 100 < SOP_ANIME_FEATURED_PERCENT else []
                pool = pool or await sop_anime_page(random.randint(1, 30))  # the 1500 most popular characters
            else:
                pool = await sop_featured_pool() if random.random() * 100 < SOP_FEATURED_PERCENT else []
                pool = pool or await sop_people_pool()
            pool = [e for e in pool if e["key"] not in blocked and e["key"] not in bad and e["key"] != last]
            pool = sop_prefer_gender(pool, want or sop_want_gender())  # mostly women / female characters (SOP_FEMALE_PERCENT)
            picks = random.sample(pool, min(max(SOP_CHECK_AT_ONCE, 1), len(pool)))
        picks = [e for e in picks if e["key"] not in blocked and e["key"] not in bad and e["key"] != last]
        if not picks:
            continue
        results = await asyncio.gather(*(sop_check_candidate(e, kind) for e in picks))
        for e, r in zip(picks, results):
            if r is not None:
                await sop_cache_put(r)
                return r
            bad.add(e["key"])
    return None


async def sop_get_entry(gid, sources, pool):
    """One picture for a round (random source -> other source -> saved picture -> the server's list), or None."""
    order = []
    want = sop_want_gender()  # decided first: 65% female / 35% male. Then the source: males 80% anime / 20% real people, females 85% / 15%
    entry = None
    if pool and sources and random.random() * 100 < SOP_LIST_PERCENT:
        # "uploaded pics" is a source of its own: when it is on together with a random source, this share of the pictures comes from the server's list
        entry = dict(random.choice([e for e in pool if e["key"] != sop_last.get(gid)] or pool), kind="list")
    elif sources:
        weights = sop_weights_for(want)
        first = random.choices(sources, weights=[max(weights[x], 1) for x in sources])[0]
        order = [first] + [x for x in sources if x != first]
    want_gif = bool(sources) and (GIPHY_API_KEY or TENOR_API_KEY) and random.random() * 100 < SOP_GIF_PERCENT  # about 20% of the time
    for choice in order:
        if time.time() - _sop_down.get(choice, 0) < SOP_DOWN_SECONDS:
            continue  # this source just failed, don't make people wait for it again
        if want_gif:
            entry = await sop_timed(sop_gif_entry(gid, choice, want), 15)
            if entry is not None:
                break
        entry = await sop_timed(sop_remote_entry(gid, choice, want), 20)
        if entry is not None:
            break
        if not (choice == "people" and _sop_inflight):
            _sop_down[choice] = time.time()
    if entry is None and sources:
        entry = sop_cache_get(gid, sources, want_gif, want)
    if entry is None and pool:
        entry = dict(random.choice([e for e in pool if e["key"] != sop_last.get(gid)] or pool), kind="list")
    if entry is not None:
        sop_last[gid] = entry["key"]
        sop_last_entry[gid] = {"name": entry["name"], "key": entry["key"], "kind": entry["kind"], "hash": entry.get("hash")}
    return entry


async def sop_send_round(ctx, entry, number, total, first):
    view = SopRoundView(entry)
    view.ends_at = time.time() + SOP_VOTE_SECONDS
    if entry.get("data"):
        extra = {"file": discord.File(io.BytesIO(entry["data"]), filename=view.fname)}
    elif entry["path"]:
        extra = {"file": discord.File(entry["path"], filename=view.fname)}
    else:
        extra = {}
    send = ctx.send if first else ctx.channel.send
    view.message = await send(embed=view.build_embed(number, total), view=view, **extra)
    return view


class SopQuickView(discord.ui.View):
    """Smash / Pass under one picture for the plain ,sop. Anybody can press (once per picture), and every press posts its own
    embed that pings the person and shows how many smashes or passes they have in total."""

    def __init__(self, entry, gid):
        super().__init__(timeout=SMASHPASS_SECONDS)
        self.name, self.url, self.path = entry["name"], entry["url"], entry["path"]
        self.data = entry.get("data")
        ext = os.path.splitext(self.path)[1] if self.path else (entry.get("ext") or "")
        self.fname = "sop" + ext
        self.gid = gid
        self.answered = set()  # user ids that already pressed a button on this picture
        self.message = None
        style_buttons(self, {"Smash": "smash", "Pass": "cross"})

    def build_embed(self):
        embed = discord.Embed(description=f"**{discord.utils.escape_markdown(self.name)}**", color=theme())
        embed.set_image(url=f"attachment://{self.fname}" if (self.path or self.data) else self.url)
        return embed

    async def cast(self, interaction, choice):
        if interaction.user.id in self.answered:
            await interaction.response.send_message("You already answered this one.", ephemeral=True)
            return
        self.answered.add(interaction.user.id)
        total = sop_count(self.gid, interaction.user.id, choice)
        await interaction.response.send_message(embed=sop_answer_embed(interaction.user, self.name, choice, total))
        asyncio.create_task(sop_stats.save_async())

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(view=None)  # the picture stays, only the buttons go
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Smash", style=discord.ButtonStyle.success)
    async def smash(self, interaction, button):
        await self.cast(interaction, "smash")

    @discord.ui.button(label="Pass", style=discord.ButtonStyle.secondary)
    async def pass_(self, interaction, button):
        await self.cast(interaction, "pass")


@bot.hybrid_group(name="sop", aliases=["smashorpass", "smashpass"], invoke_without_command=True,
                  description="Smash or pass one picture", fallback="play")
@commands.cooldown(1, SOP_COOLDOWN_SECONDS, commands.BucketType.user)
@commands.guild_only()
async def sop_group(ctx):
    """Posts one picture with Smash and Pass buttons. Everyone can press, and each press posts an embed saying who smashed or passed and how many smashes or passes they have. One ,sop per person every minute."""
    gid = ctx.guild.id
    pool = sop_active_pool(gid)  # empty when "uploaded pics" is switched off
    sources = sop_sources(gid)
    if not sources and not pool:
        ctx.command.reset_cooldown(ctx)
        await ctx.send(embed=status_embed(ctx, "warn", sop_nothing_text(gid)))
        return
    if ctx.interaction is None or not ctx.interaction.response.is_done():
        await ctx.defer()  # the picture can take a moment
    entry = await sop_get_entry(gid, sources, pool)
    if entry is None:
        ctx.command.reset_cooldown(ctx)  # nothing was shown, so it doesn't count
        await ctx.send(embed=status_embed(ctx, "warn", "Couldn't get a picture right now. Try again in a moment."))
        return
    view = SopQuickView(entry, gid)
    if entry.get("data"):
        extra = {"file": discord.File(io.BytesIO(entry["data"]), filename=view.fname)}
    elif entry["path"]:
        extra = {"file": discord.File(entry["path"], filename=view.fname)}
    else:
        extra = {}
    view.message = await ctx.send(embed=view.build_embed(), view=view, **extra)
    if SOP_WARM_ON and sources and (len(sop_cache_index.data) < SOP_CACHE_WARM or ("people" in sources and (len(sop_people_disk.data) < 8 or len(sop_trend_disk.data) < 6))):
        asyncio.create_task(sop_warm())  # fill the cache in the background


@sop_group.command(name="vote", description="Smash or pass: 5 pictures in a row, everyone votes on each")
@commands.cooldown(1, 5, commands.BucketType.user)
@commands.guild_only()
async def sop_vote(ctx):
    """Starts a round of 5 pictures. Each one is open for 30 seconds, then the votes are posted as a new embed, and 3 seconds later the next picture comes. After the 5th it stops: use the command again to play again."""
    gid, cid = ctx.guild.id, ctx.channel.id
    if cid in sop_running:
        await ctx.send(embed=status_embed(ctx, "warn", "A smash or pass is already running in this channel. Wait until it ends."))
        return
    pool = sop_active_pool(gid)  # empty when "uploaded pics" is switched off
    sources = sop_sources(gid)
    if not sources and not pool:
        await ctx.send(embed=status_embed(ctx, "warn", sop_nothing_text(gid)))
        return
    sop_running.add(cid)
    try:
        if ctx.interaction is None or not ctx.interaction.response.is_done():
            await ctx.defer()  # the first picture can take a moment
        nxt = asyncio.create_task(sop_get_entry(gid, sources, pool))
        played = 0
        for number in range(1, SOP_ROUNDS + 1):
            entry = await nxt
            if entry is None:
                await ctx.channel.send(embed=status_embed(ctx, "warn", "Couldn't get a picture right now, so I stopped here. Try again in a moment."))
                break
            view = await sop_send_round(ctx, entry, number, SOP_ROUNDS, first=(number == 1))
            played += 1
            if number < SOP_ROUNDS:
                nxt = asyncio.create_task(sop_get_entry(gid, sources, pool))  # fetched while people vote, so the next one is instant
            await asyncio.sleep(max(view.ends_at - time.time(), 0))
            view.closed = True
            view.stop()
            for uid, choice in view.votes.items():  # everyone's totals, counted once per picture
                sop_count(gid, uid, choice)
            try:
                await view.message.edit(view=None)  # the picture embed stays as it is, only the buttons go
            except discord.HTTPException:
                pass
            try:
                await ctx.channel.send(embed=view.build_result(number, SOP_ROUNDS), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.warning("Could not show the ,sop votes", exc_info=True)
            if view.votes:
                asyncio.create_task(sop_stats.save_async())
            if number < SOP_ROUNDS:
                await asyncio.sleep(SOP_PAUSE_SECONDS)
        if SOP_WARM_ON and sources and (len(sop_cache_index.data) < SOP_CACHE_WARM or ("people" in sources and (len(sop_people_disk.data) < 8 or len(sop_trend_disk.data) < 6))):
            asyncio.create_task(sop_warm())  # fill the cache in the background
    except Exception:
        log.exception("The ,sop vote session crashed")
    finally:
        sop_running.discard(cid)


# Words accepted after `sop source`: a source (or a group of sources) plus an on / off word, in either order.
SOP_SOURCE_NAMES = {
    "people": ["people"], "irl": ["people"], "real": ["people"],
    "anime": ["anime"],
    "uploaded": ["uploaded"], "upload": ["uploaded"], "uploads": ["uploaded"], "pics": ["uploaded"], "pictures": ["uploaded"], "list": ["uploaded"],
    "random": ["people", "anime"], "both": ["people", "anime"],
    "all": ["people", "anime", "uploaded"],
}
SOP_SOURCE_ON = {"enable", "on", "add", "allow"}
SOP_SOURCE_OFF = {"disable", "off", "none", "remove"}
SOP_SOURCE_RANDOM_ONLY = {"anime", "people", "irl", "real", "both", "random"}  # on their own these set the random sources exactly (as before) and leave uploaded pics alone


def sop_source_check():
    """Check for `sop source`: the server owner, the bot owner, or anyone with Manage Server / Administrator."""
    async def predicate(ctx):
        member = ctx.author
        if await has_full_access(member, ctx.guild):
            return True
        if isinstance(member, discord.Member) and (member.guild_permissions.manage_guild or member.guild_permissions.administrator):
            return True
        raise commands.CheckFailure("Only the **server owner** or members with the **Manage Server** permission can change the picture sources.")

    return commands.check(predicate)


def sop_source_status(gid):
    """'people irl: on, anime: off, uploaded pics: on' for this server."""
    on = sop_enabled(gid)
    return ", ".join(f"**{SOP_SOURCE_LABELS[s]}**: {'on' if s in on else 'off'}" for s in SOP_TOGGLES)


@sop_group.command(name="source", description="Server owner: turn each picture source on or off (people irl, anime, uploaded pics)")
@app_commands.describe(choice="enable or disable (or: anime, people, both, off, all)", source="With enable or disable: people, anime, uploaded, random or all")
@sop_source_check()
@commands.guild_only()
async def sop_source(ctx, choice: str = None, source: str = None):
    """The three picture sources: 1) `people` (real people, irl, random), 2) `anime` (characters, random), 3) `uploaded` (this server's own pictures:
    `sop upload` and `sop add`). `sop source` shows which are on.
    `sop source enable <people|anime|uploaded|random|all>` and `sop source disable <...>` turn them on or off one by one (`sop source anime off` works too).
    Older forms still work: `sop source anime|people|both` set exactly those random sources and `sop source off` turns the random ones off; uploaded pics are left alone.
    Allowed: the server owner, the bot owner and Manage Server."""
    gid = ctx.guild.id
    p = prefix_for("sop")
    words = [w.strip().lower() for w in (choice, source) if w and w.strip()]
    if not words:
        await ctx.send(embed=status_embed(
            ctx, "ok", f"Picture sources: {sop_source_status(gid)}.\n"
                       "**people irl**: random real people (a featured list plus young actors, singers and models, 18 to 35).\n"
                       "**anime**: random anime characters with a stated age of 18+.\n"
                       f"**uploaded pics**: this server's own list ({len(sop_pool(gid))} now, added with `{p}sop upload` and `{p}sop add`)."
                       f" With a random source also on, about {SOP_LIST_PERCENT}% of the pictures come from it.\n"
                       f"Turn one on or off: `{p}sop source enable people`, `{p}sop source disable anime`, `{p}sop source enable uploaded` (or `all`)."))
        return
    now = sop_enabled(gid)
    usage = f"Use `{p}sop source enable|disable people|anime|uploaded|all`."
    new = None
    if len(words) == 1:
        word = words[0]
        if word in SOP_SOURCE_RANDOM_ONLY:               # anime / people / both: exactly these random sources, uploaded pics unchanged
            new = (now & {"uploaded"}) | set(SOP_SOURCE_NAMES[word])
        elif word in ("off", "none"):                     # off: the random sources off, uploaded pics unchanged
            new = now & {"uploaded"}
        elif word == "all":
            new = set(SOP_TOGGLES)
    elif len(words) == 2:
        first, second = words
        if first in SOP_SOURCE_NAMES and (second in SOP_SOURCE_ON or second in SOP_SOURCE_OFF):
            first, second = second, first                 # "anime off" -> "off anime"
        if second in SOP_SOURCE_NAMES and (first in SOP_SOURCE_ON or first in SOP_SOURCE_OFF):
            touched = set(SOP_SOURCE_NAMES[second])
            new = (now | touched) if first in SOP_SOURCE_ON else (now - touched)
    if new is None:
        await ctx.send(embed=status_embed(ctx, "warn", usage))
        return
    if new == now:
        await ctx.send(embed=status_embed(ctx, "ok", f"Nothing changed. Picture sources: {sop_source_status(gid)}."))
        return
    cfg = sop_cfg_for(gid)
    cfg["sources"] = [s for s in SOP_SOURCES if s in new]  # the random ones, in the order the picker expects
    cfg["list"] = "uploaded" in new
    sop_cfg.save()
    text = f"picture sources: {sop_source_status(gid)}."
    if not new:
        text += f"\nEverything is off now, so `{p}sop` won't show anything until one is turned on."
    await ctx.send(embed=status_embed(ctx, "add" if len(new) >= len(now) else "remove", text))


@sop_group.command(name="block", description="Never show the last random picture again in this server")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def sop_block(ctx):
    """For a random picture that shouldn't be there (for example a character who looks too young). Staff-added pictures use `sop remove`."""
    gid = ctx.guild.id
    last = sop_last_entry.get(gid)
    if not last:
        await ctx.send(embed=status_embed(ctx, "warn", "Nothing has been shown yet."))
        return
    if last["kind"] == "list":
        await ctx.send(embed=status_embed(ctx, "warn", f"That one is on the server's list. Use `{prefix_for('sop')}sop remove {last['name']}`."))
        return
    blocked = sop_cfg_for(gid).setdefault("blocked", [])
    if last["key"] not in blocked:
        blocked.append(last["key"])
        del blocked[:-SOP_BLOCK_MAX]
        sop_cfg.save()
    if last.get("hash"):
        sop_mark_bad(last["hash"])  # also removes it from the saved pictures, and any copy of it (a placeholder) everywhere
    await ctx.send(embed=status_embed(ctx, "remove", f"blocked **{discord.utils.escape_markdown(last['name'])}**. It won't come up again here."))


def sop_can_add(member):
    """Who may use `sop add`: Manage Server, Administrator, the server owner, or anyone with a staff role. 'Staff role' means the same
    roles that count as staff for ,collect (collect_reason): the plain Staff role plus Owner / Co Owner / Admin / Moderator / Helper / Partnerships."""
    if not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    return bool(perms.manage_guild or perms.administrator or collect_reason(member) == "staff")


def sop_staff_check():
    """Check for `sop add`: staff role, Manage Server, server owner or bot owner."""
    async def predicate(ctx):
        if sop_can_add(ctx.author) or await bot.is_owner(ctx.author):
            return True
        raise commands.CheckFailure("Only **staff** (the Staff role) or members with the **Manage Server** permission can add pictures.")

    return commands.check(predicate)


@sop_group.command(name="add", description="Add someone with a direct picture link (an adult, 18+). Staff only")
@sop_staff_check()
@commands.guild_only()
async def sop_add(ctx, url: str, *, name: str):
    """`sop add <picture link> <name>`: the link has to be a direct image link (.jpg .png .webp .gif). Staff role, Manage Server or the server owner."""
    url = url.strip("<>")
    name = name.replace("`", "").strip()
    if not 1 <= len(name) <= 60:
        await ctx.send(embed=status_embed(ctx, "warn", "Give a name between **1** and **60** characters."))
        return
    problem = sop_link_error(url)
    if problem:
        await ctx.send(embed=status_embed(ctx, "warn", problem))
        return
    entries = smashpass.entries(ctx.guild.id)
    if any(e.get("url") == url for e in entries):
        await ctx.send(embed=status_embed(ctx, "warn", "That picture is already on the list."))
        return
    if len(entries) >= SMASHPASS_MAX:
        await ctx.send(embed=status_embed(ctx, "warn", f"The list is full (**{SMASHPASS_MAX}**). Remove one first."))
        return
    entries.append({"name": name, "url": url})
    smashpass.save()
    await ctx.send(embed=status_embed(
        ctx, "add", f"added **{discord.utils.escape_markdown(name)}**. The list has **{len(sop_pool(ctx.guild.id))}** now." + sop_off_note(ctx.guild.id)))


@sop_group.command(name="upload", aliases=["addpic"], description="Add pictures by uploading them (adults, 18+ only)")
@app_commands.describe(image="The picture", name="The name to show (when you upload one picture)")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def sop_upload(ctx, image: discord.Attachment = None, *, name: str = None):
    """Attach up to 10 pictures to the message: `sop upload`. One picture can get a name: `sop upload Name`.
    Without a name each picture is called after its file name. The pictures are saved, so they never expire."""
    files = list(ctx.message.attachments) if ctx.interaction is None else ([image] if image is not None else [])
    if not files:
        await ctx.send(embed=status_embed(ctx, "warn", "Attach one or more pictures to your message."))
        return
    await ctx.defer()
    gid = ctx.guild.id
    entries = smashpass.entries(gid)
    wanted = name.replace("`", "").strip()[:60] if name and len(files) == 1 else ""
    added, skipped = 0, []
    for att in files[:SOP_UPLOAD_BATCH]:
        ctype = (att.content_type or "").split(";")[0].strip().lower()
        ext = SOP_IMAGE_TYPES.get(ctype) or next((x for x in SMASHPASS_EXTENSIONS if att.filename.lower().endswith(x)), None)
        if ext is None:
            skipped.append(f"{att.filename}: not a picture")
            continue
        if att.size > SOP_MAX_BYTES:
            skipped.append(f"{att.filename}: bigger than {SOP_MAX_BYTES // (1024 * 1024)} MB")
            continue
        if len(entries) >= SMASHPASS_MAX:
            skipped.append(f"{att.filename}: the list is full ({SMASHPASS_MAX})")
            break
        rel = f"{gid}/{uuid.uuid4().hex}{ext}"
        try:
            os.makedirs(os.path.join(SOP_DIR, str(gid)), exist_ok=True)
            await att.save(os.path.join(SOP_DIR, rel))
        except (discord.HTTPException, OSError):
            log.warning("Could not save the smash or pass picture %s", att.filename, exc_info=True)
            skipped.append(f"{att.filename}: couldn't save it")
            continue
        entries.append({"name": wanted or sop_name_from(att.filename), "file": rel})
        added += 1
    if added:
        smashpass.save()
    if len(files) > SOP_UPLOAD_BATCH:
        skipped.append(f"{len(files) - SOP_UPLOAD_BATCH} more were over the limit of {SOP_UPLOAD_BATCH} per message")
    text = f"added **{added}** picture{'s' if added != 1 else ''}. The list has **{len(sop_pool(gid))}** now." if added else "no picture was added."
    if skipped:
        text += "\nSkipped: " + "; ".join(skipped[:4]) + (" ..." if len(skipped) > 4 else "")
    if added:
        text += sop_off_note(gid)
    await ctx.send(embed=status_embed(ctx, "add" if added else "warn", text))


@sop_group.command(name="remove", description="Remove someone by name")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def sop_remove(ctx, *, name: str):
    entries = smashpass.entries(ctx.guild.id)
    wanted = name.strip().lower()
    found = next((e for e in entries if e["name"].lower() == wanted), None)
    if found is None:
        await ctx.send(embed=status_embed(ctx, "warn", f"Nobody on the list has that name. Check `{prefix_for('sop')}sop list`."))
        return
    entries.remove(found)
    smashpass.save()
    if found.get("file"):
        try:
            os.remove(os.path.join(SOP_DIR, found["file"]))
        except OSError:
            pass
    await ctx.send(embed=status_embed(ctx, "remove", f"removed **{discord.utils.escape_markdown(found['name'])}**."))


@sop_group.command(name="list", description="Show who is on the list")
@commands.guild_only()
async def sop_list(ctx):
    pool = sop_pool(ctx.guild.id)
    now = [SOP_SOURCE_LABELS[x] for x in SOP_TOGGLES if x in sop_sources(ctx.guild.id)]  # the random sources that are on, by their shown names
    if not pool:
        await ctx.send(embed=status_embed(ctx, "warn", "The list is empty." + (f" Random pictures are on: {', '.join(now)}." if now else "")))
        return
    lines, size = [], 0
    for n, e in enumerate(pool, start=1):
        line = f"{n}. {discord.utils.escape_markdown(e['name'])}"
        if size + len(line) > 3800:
            lines.append(f"and {len(pool) - n + 1} more")
            break
        lines.append(line)
        size += len(line) + 1
    if not sop_list_on(ctx.guild.id):
        lines.append(f"\n**uploaded pics** is turned off, so this list isn't used. The server owner can turn it on with `{prefix_for('sop')}sop source enable uploaded`.")
    if now:
        lines.append(f"\nRandom pictures are also on: {', '.join(now)}.")
    embed = discord.Embed(title="Smash or pass list", description="\n".join(lines), color=theme())
    with_author(embed, ctx.author)
    await ctx.send(embed=embed)


# --------------------------------------------------------------------------
# .customize: appearance / style / reset buttons (server administrators and the bot owner)
#   appearance -> form: username, profile picture, banner, bio   (changes the bot everywhere)
#   style      -> form: embed color, name color, secondary color, name font, name effect
#                 (font/effect/colors are Discord's display name styles, set per server)
#   reset      -> puts the style back to default (never touches the profile)
# --------------------------------------------------------------------------
IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
COLOR_ROLE_NAME = "Bot name color"  # only used to clean up after the old role-based name color

# Discord's own display name styles (the fonts and effects Nitro users get). Bots set them per
# server through PATCH /guilds/{id}/members/@me. Discord has not documented this endpoint, so the
# ids below come from community write-ups: key -> (label, Discord id, short description).
NAME_FONTS = {
    "default": ("Default", 11, "Standard Discord font"),
    "tempo": ("Tempo", 1, "Bold, athletic"),
    "sakura": ("Sakura", 3, "Soft brush script"),
    "jellybean": ("Jellybean", 4, "Playful, rounded"),
    "modern": ("Modern", 6, "Clean, geometric"),
    "medieval": ("Medieval", 7, "Gothic blackletter"),
    "8bit": ("8bit", 8, "Retro pixel style"),
    "vampyre": ("Vampyre", 10, "Sharp display font"),
}
NAME_EFFECTS = {
    "solid": ("Solid", 1, "Single flat color"),
    "gradient": ("Gradient", 2, "Blend between two colors"),
    "neon": ("Neon", 3, "Glowing neon effect"),
    "toon": ("Toon", 4, "Cartoon outline style"),
    "pop": ("Pop", 5, "Bubble pop style"),
    "glow": ("Glow", 6, "Soft glow effect"),
}
NAME_STYLE_FALLBACK_COLOR = 0xFFFFFF  # effects always need a color, so no color means white

# label, color (names that parse_color understands)
COLOR_PRESETS = [
    ("Red", 0xF1665F), ("Orange", 0xFFA24C), ("Yellow", 0xFFC85C), ("Green", 0xA4EC7C),
    ("Mint", 0x5FE3C0), ("Cyan", 0x5CC8FF), ("Blue", 0x5B7CFF), ("Purple", 0xA35BFF),
    ("Pink", 0xF78FB3), ("White", 0xFFFFFF), ("Black", 0x010101),
]


def is_image(data):
    return data.startswith(IMAGE_SIGNATURES) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


def parse_color(text):
    """"#ff66aa", "f6a" or a preset name like "pink" -> int, or None if it isn't a color."""
    t = text.strip().lower().lstrip("#")
    for label, value in COLOR_PRESETS:
        if t == label.lower():
            return value
    if len(t) == 3 and all(c in "0123456789abcdef" for c in t):
        t = "".join(c * 2 for c in t)
    if len(t) == 6 and all(c in "0123456789abcdef" for c in t):
        return int(t, 16) or 0x010101  # 000000 means "no color" to Discord
    return None


def hex_text(value):
    return f"#{value:06X}" if value else ""


def font_options(current):
    return [
        discord.SelectOption(label=label, value=key, description=blurb, default=(key == current))
        for key, (label, _id, blurb) in NAME_FONTS.items()
    ]


def effect_options(current):
    return [
        discord.SelectOption(label=label, value=key, description=blurb, default=(key == current))
        for key, (label, _id, blurb) in NAME_EFFECTS.items()
    ]


def result_embed(lines, title=None):
    """Several results at once, one line each. Green if all went fine, red if something failed."""
    kinds = {kind for kind, _ in lines}
    color = ERR_COLOR if "err" in kinds else OK_COLOR if kinds == {"ok"} else GREY
    return discord.Embed(description="\n".join(status_text(kind, text) for kind, text in lines), color=color)


async def load_profile():
    """The bot's user from the API. Only this fresh copy knows the banner."""
    try:
        return await bot.fetch_user(bot.user.id)
    except discord.HTTPException:
        return bot.user


async def load_bio():
    try:
        return (await bot.application_info()).description or ""
    except discord.HTTPException:
        return ""


async def set_bio(text):
    await bot.http.request(discord.http.Route("PATCH", "/applications/@me"), json={"description": text})


async def read_upload(upload):
    """First file of an upload box -> (bytes, None), (None, None) if empty, or (None, problem)."""
    files = upload.values
    if not files:
        return None, None
    attachment = files[0]
    if attachment.size > MAX_IMAGE_BYTES:
        return None, "that file is over 8 MB."
    try:
        data = await attachment.read()
    except discord.HTTPException:
        return None, "I couldn't download that file."
    if not is_image(data):
        return None, "that isn't a PNG, JPG, GIF or WEBP image."
    return data, None


async def apply_appearance(modal):
    """Username, picture, banner and bio from the appearance form. Returns [(status, text)]."""
    lines = []
    edits = {}
    name = (modal.username.value or "").strip()
    if name and name != bot.user.name:
        if 2 <= len(name) <= 32:
            edits["username"] = name
        else:
            lines.append(("warn", "A username needs 2 to 32 characters."))
    for key, label, upload in (("avatar", "Profile picture", modal.avatar), ("banner", "Banner", modal.banner)):
        data, problem = await read_upload(upload)
        if problem:
            lines.append(("warn", f"{label}: {problem}"))
        elif data is not None:
            edits[key] = data
    if edits:
        try:
            await bot.user.edit(**edits)
        except discord.HTTPException as e:
            lines.append(("err", f"Discord refused the profile change: {e.text or e}"))
        except ValueError:
            lines.append(("warn", "Discord can't use that image type."))
        else:
            done = {"username": "Username", "avatar": "Profile picture", "banner": "Banner"}
            for key in edits:
                lines.append(("ok", f"{done[key]} updated."))
    bio = (modal.bio.value or "").strip()
    if bio != (modal.current_bio or "").strip():
        try:
            await set_bio(bio)
        except discord.HTTPException as e:
            lines.append(("err", f"Discord refused the bio: {e.text or e}"))
        else:
            modal.panel.bio = bio
            lines.append(("ok", "Bio updated. It can take a minute to show on the profile."))
    if not lines:
        lines.append(("warn", "Nothing changed."))
    return lines


def style_echoed(data, font_id, effect_id, colors):
    """Did Discord's reply contain the style we asked for? (It may be flat or nested.)"""
    if not isinstance(data, dict):
        return False
    nested = data.get("display_name_styles")
    nested = nested if isinstance(nested, dict) else {}
    got_font = data.get("display_name_font_id", nested.get("font_id"))
    got_effect = data.get("display_name_effect_id", nested.get("effect_id"))
    got_colors = data.get("display_name_colors", nested.get("colors"))
    return got_font == font_id and got_effect == effect_id and list(got_colors or []) == list(colors)


async def send_name_style(guild, font_id, effect_id, colors):
    route = discord.http.Route("PATCH", "/guilds/{guild_id}/members/@me", guild_id=guild.id)
    return await bot.http.request(route, json={
        "display_name_font_id": font_id,
        "display_name_effect_id": effect_id,
        "display_name_colors": list(colors),
    })


async def apply_name_style(guild, font, effect, primary, secondary):
    """Put Discord's own display name font, effect and colors on the bot in this server."""
    font_label, font_id, _ = NAME_FONTS[font]
    effect_label, effect_id, _ = NAME_EFFECTS[effect]
    note = ""
    if primary is None:
        primary = NAME_STYLE_FALLBACK_COLOR
        note = " No Name Color was set, so I used white."
    colors = [primary, secondary] if effect == "gradient" else [primary]
    try:
        data = await send_name_style(guild, font_id, effect_id, colors)
    except discord.Forbidden:
        return "err", "I need the **Change Nickname** permission to change my name style."
    except discord.HTTPException as e:
        return "err", f"Discord refused the name style: {e.text or e}"
    shown = " \u2192 ".join(hex_text(c) for c in colors)
    text = f"Name style set to **{font_label}**, **{effect_label}**, {shown}.{note}"
    if style_echoed(data, font_id, effect_id, colors):
        return "ok", text
    return "warn", (
        text + " Discord accepted it but didn't confirm it. If my name looks the same, turn on "
        "**Display Name Styles** in your own Discord settings under Accessibility."
    )


async def clear_name_style(guild):
    """Back to the plain Discord name. Returns (status, text)."""
    try:
        await send_name_style(guild, 0, 0, [])
    except discord.Forbidden:
        return "err", "I need the **Change Nickname** permission to reset my name style."
    except discord.HTTPException as e:
        return "err", f"Discord refused the reset: {e.text or e}"
    return "ok", "Name style is back to the default."


async def clean_up_legacy_style(guild):
    """Older versions faked fonts with a special-character nickname and a color role. Undo that."""
    try:
        if guild.me.nick is not None:
            await guild.me.edit(nick=None)
        role = discord.utils.get(guild.roles, name=COLOR_ROLE_NAME)
        if role is not None:
            await role.delete(reason="Replaced by Discord's display name styles")
    except discord.HTTPException:
        pass


async def apply_style(modal, guild):
    """Everything in the style form. Colors are checked first, so a typo changes nothing."""
    bad = []
    parsed = {}
    for key, label, box in (
        ("embed", "Embed Theme Color", modal.embed_color),
        ("name", "Name Color", modal.name_color),
        ("second", "Secondary Color", modal.secondary),
    ):
        text = (box.value or "").strip()
        parsed[key] = None
        if text:
            parsed[key] = parse_color(text)
            if parsed[key] is None:
                bad.append(label)
    if bad:
        return [("warn", f"{' and '.join(bad)} isn't a valid color. Use a hex like `#FF66AA`.")]
    mine = settings.guild(guild.id)
    legacy = mine["font"] not in NAME_FONTS or mine["effect"] not in NAME_EFFECTS
    old_font = mine["font"] if mine["font"] in NAME_FONTS else "default"
    old_effect = mine["effect"] if mine["effect"] in NAME_EFFECTS else "solid"
    font = modal.font.values[0] if modal.font.values else old_font
    effect = modal.effect.values[0] if modal.effect.values else old_effect
    if font not in NAME_FONTS:
        font = "default"
    if effect not in NAME_EFFECTS:
        effect = "solid"
    if effect == "gradient" and (parsed["name"] is None or parsed["second"] is None):
        return [("warn", "A gradient needs both a Name Color and a Secondary Color.")]

    lines = []
    settings.set_embed_color(parsed["embed"])
    if parsed["embed"] is None:
        lines.append(("ok", "Embed color is the default."))
    else:
        lines.append(("ok", f"Embed color set to **{hex_text(parsed['embed'])}**."))
    had_style = (
        mine["font"] != "default" or mine["effect"] != "solid"
        or mine["name_color"] is not None or legacy
    )
    settings.set_guild(
        guild.id, name_color=parsed["name"], secondary_color=parsed["second"], font=font, effect=effect
    )
    if legacy:
        await clean_up_legacy_style(guild)
    wants_style = font != "default" or effect != "solid" or parsed["name"] is not None
    if wants_style:
        lines.append(await apply_name_style(guild, font, effect, parsed["name"], parsed["second"]))
    elif had_style:
        lines.append(await clear_name_style(guild))
    return lines


async def reset_style(guild):
    """Embed color, name color and name font back to default. The profile is left alone."""
    lines = []
    settings.set_embed_color(None)
    settings.clear_guild(guild.id)
    lines.append(("ok", "Embed color is back to the default."))
    lines.append(await clear_name_style(guild))
    await clean_up_legacy_style(guild)
    return lines


def customize_embed(profile, guild):
    mine = settings.guild(guild.id)
    embed = discord.Embed(
        description=(
            "**Customize the bot**\n"
            "**appearance** changes the picture, banner, username and bio everywhere.\n"
            "**style** changes the embed color and my name font, effect and color in this server.\n"
            "**reset** puts the style back to the default."
        ),
        color=theme(),
    )
    with_author(embed, profile)
    banner = getattr(profile, "banner", None)
    if banner:
        embed.set_image(url=banner.url)
    if mine["effect"] == "gradient" and mine["name_color"] and mine["secondary_color"]:
        name_color = f"{hex_text(mine['name_color'])} \u2192 {hex_text(mine['secondary_color'])}"
    else:
        name_color = hex_text(mine["name_color"]) or "Default"
    font_label = NAME_FONTS.get(mine["font"], NAME_FONTS["default"])[0]
    effect_label = NAME_EFFECTS.get(mine["effect"], NAME_EFFECTS["solid"])[0]
    font_text = font_label if effect_label == "Solid" else f"{font_label} \u00b7 {effect_label}"
    embed.add_field(name="Embed color", value=hex_text(settings.embed_color) or "Default", inline=True)
    embed.add_field(name="Name color", value=name_color, inline=True)
    embed.add_field(name="Name font", value=font_text, inline=True)
    embed.set_footer(
        text="Name fonts, effects and colors are Discord's display name styles, set per server."
    )
    return embed


class AppearanceModal(discord.ui.Modal):
    def __init__(self, panel, bio):
        super().__init__(title="Bot Appearance")
        self.panel = panel
        self.current_bio = bio
        self.username = discord.ui.TextInput(
            default=bot.user.name, min_length=2, max_length=32, required=False, placeholder="Bot username"
        )
        self.avatar = discord.ui.FileUpload(required=False, min_values=0, max_values=1)
        self.banner = discord.ui.FileUpload(required=False, min_values=0, max_values=1)
        self.bio = discord.ui.TextInput(
            style=discord.TextStyle.paragraph, default=bio[:400] or None, max_length=400,
            required=False, placeholder="Tell people what this bot does",
        )
        self.add_item(discord.ui.Label(
            text="Username", description="Changes the bot everywhere. Discord allows 2 changes per hour.",
            component=self.username))
        self.add_item(discord.ui.Label(
            text="Profile Picture", description="Upload a PNG, JPG, GIF or WEBP, or leave it empty.",
            component=self.avatar))
        self.add_item(discord.ui.Label(
            text="Banner", description="Upload a wide image, or leave it empty.", component=self.banner))
        self.add_item(discord.ui.Label(
            text="Bio", description="The About Me text on the bot's profile.", component=self.bio))

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            lines = await apply_appearance(self)
        except Exception:
            log.exception("Appearance change failed")
            lines = [("err", "Something went wrong. Check the bot console.")]
        await interaction.followup.send(embed=result_embed(lines, "Appearance"), ephemeral=True)
        await self.panel.refresh_embed(interaction.guild)


class StyleModal(discord.ui.Modal):
    def __init__(self, panel, guild):
        super().__init__(title="Bot Style")
        self.panel = panel
        mine = settings.guild(guild.id)
        self.embed_color = discord.ui.TextInput(
            default=hex_text(settings.embed_color) or None, placeholder="#faecea", required=False, max_length=20)
        self.name_color = discord.ui.TextInput(
            default=hex_text(mine["name_color"]) or None, placeholder="#6D9F8B", required=False, max_length=20)
        self.secondary = discord.ui.TextInput(
            default=hex_text(mine["secondary_color"]) or None, placeholder="#5CD5DA", required=False, max_length=20)
        self.font = discord.ui.Select(options=font_options(mine["font"]), required=False)
        self.effect = discord.ui.Select(options=effect_options(mine["effect"]), required=False)
        self.add_item(discord.ui.Label(
            text="Embed Theme Color", description="Set a custom embed color (hex: #faecea or faecea).",
            component=self.embed_color))
        self.add_item(discord.ui.Label(
            text="Name Color", description="Display name color (hex: #6D9F8B). Blank uses white.", component=self.name_color))
        self.add_item(discord.ui.Label(
            text="Secondary Color", description="Second color, only for the Gradient effect (hex).",
            component=self.secondary))
        self.add_item(discord.ui.Label(
            text="Display Name Font", description="Discord's own name fonts, shown next to my name in this server.",
            component=self.font))
        self.add_item(discord.ui.Label(
            text="Name Color Effect", description="Effects use the Name Color. Gradient needs both colors.",
            component=self.effect))

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            lines = await apply_style(self, interaction.guild)
        except Exception:
            log.exception("Style change failed")
            lines = [("err", "Something went wrong. Check the bot console.")]
        await interaction.followup.send(embed=result_embed(lines, "Style"), ephemeral=True)
        await self.panel.refresh_embed(interaction.guild)


async def can_customize(user):
    """The bot owner, the server owner, or a server member with the Administrator permission."""
    if await has_full_access(user):
        return True
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and perms.administrator)


def customize_check():
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if await can_customize(ctx.author):
            return True
        raise commands.MissingPermissions(["administrator"])

    return commands.check(predicate)


class CustomizeView(discord.ui.View):
    def __init__(self, bio=""):
        super().__init__(timeout=600)
        self.bio = bio
        self.message = None
        style_buttons(self, {"reset": "remove"})

    async def interaction_check(self, interaction):
        if not await can_customize(interaction.user):
            await interaction.response.send_message(
                embed=status_line(interaction.user, "err", "You need the **Administrator** permission to use this."),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="appearance", style=discord.ButtonStyle.secondary)
    async def appearance(self, interaction, button):
        await interaction.response.send_modal(AppearanceModal(self, self.bio))

    @discord.ui.button(label="style", style=discord.ButtonStyle.secondary)
    async def style(self, interaction, button):
        await interaction.response.send_modal(StyleModal(self, interaction.guild))

    @discord.ui.button(label="reset", style=discord.ButtonStyle.danger)
    async def reset(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        lines = await reset_style(interaction.guild)
        await interaction.followup.send(embed=result_embed(lines, "Style reset"), ephemeral=True)
        await self.refresh_embed(interaction.guild)

    async def refresh_embed(self, guild):
        """Show the new profile and style on the main message."""
        if self.message is None or guild is None:
            return
        try:
            self.message = await self.message.edit(embed=customize_embed(await load_profile(), guild), view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


@bot.hybrid_command(name="customize", aliases=["customise"], description="Change the bot's profile and style")
@customize_check()
async def customize(ctx):
    view = CustomizeView(await load_bio())
    view.message = await ctx.send(embed=customize_embed(await load_profile(), ctx.guild), view=view)


# --------------------------------------------------------------------------
# Families: ,fam create / invite / info / promote / ... / kidnap / top
# One family per person. Every family has a role (like a booster role) that all
# its members get, and a 1-3 character tag that is forced into their nickname
# as "[TAG] name". Family roles sit under an optional base role.
# (v34: family roles and forced [TAG] nicknames are gone. The tag is now only a label on the family card and top list.)
# --------------------------------------------------------------------------
FAMILY_FILE = os.path.join(DATA_DIR, "families.json")
FAMILY_BANNER_DIR = os.path.join(DATA_DIR, "family_banners")
FAMILY_MAX_MEMBERS = max(1, int(os.getenv("FAMILY_MAX_MEMBERS", "10")))
KIDNAP_CHANCE = min(1.0, max(0.0, float(os.getenv("KIDNAP_CHANCE", "0.5"))))  # 0.5 = 50% success
KIDNAP_COOLDOWN = int(os.getenv("KIDNAP_COOLDOWN", "600"))    # seconds between tries, per person
KIDNAP_IMMUNITY = int(os.getenv("KIDNAP_IMMUNITY", "1800"))   # seconds a victim is safe afterwards
INVITE_SECONDS = 60
NAME_MIN, NAME_MAX, TAG_MAX, DESC_MAX, ROLE_NAME_MAX = 2, 20, 3, 200, 50
RANK_LEVEL = {"member": 0, "admin": 1, "owner": 2}
TOP_PER_PAGE = 10
# fallback marks (used until the bl_* emojis are uploaded): plain symbols, no phone emojis
RANK_FALLBACK = {"owner": "\u2605", "admin": "\u25C6", "member": "\u2022"}
RANK_EMOJI = {"owner": "owner", "admin": "base", "member": "member"}  # bl_owner crown, bl_base shield, bl_member


def rank_icon(rank):
    return emo(RANK_EMOJI[rank], RANK_FALLBACK[rank])


def warn_line(text):
    """One warning line inside a bigger embed, e.g. a problem the bot ran into."""
    return status_text("warn", text)
BAD_NAME_CHARS = set("@<>`")
BAD_TAG_CHARS = set("@<>`[]")


class FamilyStore:
    """All families, per server, saved in families.json."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {}
        except Exception:
            log.exception("Could not read %s, starting without families", self.path)
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def guild(self, gid):
        g = self.data.setdefault(str(gid), {})
        g.setdefault("base_role", None)
        g.setdefault("best_role", None)
        g.setdefault("next_id", 1)
        g.setdefault("families", {})
        g.setdefault("cooldowns", {})
        g.setdefault("immune", {})
        return g

    def all(self, gid):
        return list(self.guild(gid)["families"].values())

    def get(self, gid, fid):
        return self.guild(gid)["families"].get(str(fid))

    def by_member(self, gid, uid):
        uid = str(uid)
        for fam in self.guild(gid)["families"].values():
            if uid in fam["members"]:
                return fam
        return None

    def by_name(self, gid, text):
        key = text.casefold().strip()
        for fam in self.all(gid):
            if fam["name"].casefold() == key:
                return fam
        return None

    def name_taken(self, gid, name, ignore=None):
        key = name.casefold()
        return any(f["name"].casefold() == key and f is not ignore for f in self.all(gid))

    def tag_taken(self, gid, tag, ignore=None):
        key = tag.casefold()
        return any(f["tag"].casefold() == key and f is not ignore for f in self.all(gid))

    def create(self, gid, owner_id, name, tag, nick):
        g = self.guild(gid)
        fid = str(g["next_id"])
        g["next_id"] += 1
        now = int(time.time())
        fam = {
            "id": fid, "name": name, "tag": tag, "description": "", "banner": None,
            "created": now, "role": None, "role_name": None, "color": None,
            "kidnaps": 0, "lost": 0, "bank": 0,
            "members": {str(owner_id): {"rank": "owner", "nick": nick, "joined": now}},
        }
        g["families"][fid] = fam
        self.save()
        return fam

    def add_member(self, fam, uid, nick, rank="member"):
        fam["members"][str(uid)] = {"rank": rank, "nick": nick, "joined": int(time.time())}
        self.save()

    def delete(self, gid, fam):
        self.guild(gid)["families"].pop(fam["id"], None)
        self.save()

    def ranking(self, gid):
        """Most reputation in the bank first; a tie goes to the family with more members, then the older one."""
        return sorted(self.all(gid), key=lambda f: (-int(f.get("bank", 0)), -len(f["members"]), f["created"], f["name"].casefold()))


families = FamilyStore(FAMILY_FILE)


# ---- small helpers --------------------------------------------------------
def fam_esc(text):
    return discord.utils.escape_markdown(text)


def fam_embed(kind, text, user=None):
    """Same clean one-line style as the rest of the bot:  \u2713 @user: text"""
    if user is not None:
        return status_line(user, kind, text)
    return discord.Embed(description=status_text(kind, text), color=STATUS_STYLES[kind][1])


async def fam_say(ctx, kind, text):
    await ctx.send(embed=fam_embed(kind, text, ctx.author))


def fam_name(fam):
    return f"**{fam_esc(fam['name'])}**"


def owner_id(fam):
    for uid, info in fam["members"].items():
        if info["rank"] == "owner":
            return int(uid)
    return None


def bank_of(fam):
    return int(fam.get("bank", 0))


def refund_bank(gid, fam, uids):
    """A family is ending: its bank is shared out between these members (the first one gets the leftover)."""
    amount = bank_of(fam)
    uids = [int(u) for u in uids]
    fam["bank"] = 0
    if amount <= 0 or not uids:
        return
    share, extra = divmod(amount, len(uids))
    for i, uid in enumerate(uids):
        auras.add(gid, uid, share + (extra if i == 0 else 0))


def rank_of(fam, uid):
    return fam["members"][str(uid)]["rank"]


def name_error(name):
    if not NAME_MIN <= len(name) <= NAME_MAX:
        return f"The family name needs **{NAME_MIN}-{NAME_MAX}** characters."
    if BAD_NAME_CHARS & set(name):
        return "The name can't contain @, < or >, or backticks."
    return None


def tag_error(tag):
    if not 1 <= len(tag) <= TAG_MAX:
        return f"The tag can be at most **{TAG_MAX}** characters (an emoji counts as one)."
    if BAD_TAG_CHARS & set(tag) or any(c.isspace() or not c.isprintable() for c in tag):
        return "The tag can't contain spaces, brackets or @."
    return None


def hex_arg(text):
    """'#ff66aa', '#f6a' or 'ff66aa' -> color int, else None (a plain word is not a color)."""
    t = text.strip()
    if re.fullmatch(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})", t):
        return parse_color(t)
    if re.fullmatch(r"[0-9a-fA-F]{6}", t) and any(c.isdigit() for c in t):
        return parse_color(t)
    return None


def u16len(text):
    return len(text.encode("utf-16-le")) // 2


def nick_for(tag, base):
    """'[TAG] name', cut so it fits Discord's 32 character nickname limit."""
    prefix = f"[{tag}] "
    base = base or ""
    while base and u16len(prefix + base) > 32:
        base = base[:-1]
    return prefix + base


def strip_tag(nick, tag):
    if not tag:  # new families have no tag
        return nick
    prefix = f"[{tag}] "
    if nick and nick.startswith(prefix):
        return nick[len(prefix):] or None
    return nick


def image_ext(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def banner_path(fam):
    return os.path.join(FAMILY_BANNER_DIR, fam["banner"]) if fam.get("banner") else None


def remove_banner_file(fam):
    path = banner_path(fam)
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def hand_over_crown(fam):
    """The owner is gone: the oldest admin (else the oldest member) becomes owner."""
    pool = [(info["rank"] != "admin", info["joined"], uid) for uid, info in fam["members"].items()]
    if not pool:
        return None
    _, _, uid = min(pool)
    fam["members"][uid]["rank"] = "owner"
    return int(uid)


async def need_family(ctx, min_rank="member"):
    """The caller's family, or None after telling them why not."""
    fam = families.by_member(ctx.guild.id, ctx.author.id)
    if fam is None:
        await fam_say(ctx, "err", "You are not in a family.")
        return None
    if RANK_LEVEL[rank_of(fam, ctx.author.id)] < RANK_LEVEL[min_rank]:
        who = "the family owner" if min_rank == "owner" else "family admins and the owner"
        await fam_say(ctx, "err", f"Only {who} can do that.")
        return None
    return fam


def family_admin_check():
    """Manage Server (or the server owner / bot owner), for the server-side family commands."""
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if await has_full_access(ctx.author, ctx.guild) or ctx.author.guild_permissions.manage_guild:
            return True
        raise commands.MissingPermissions(["manage_guild"])

    return commands.check(predicate)


# ---- roles and nicknames --------------------------------------------------
def family_anchor(guild):
    """The role family roles (and Best family) sit right under: the base role when one is set with `fam baserole`, otherwise the Staff role."""
    base_id = families.guild(guild.id)["base_role"]
    base = guild.get_role(base_id) if base_id else None
    return base or staff_anchor(guild)


async def place_role(guild, role, fresh=False):
    """Move a family role (or the Best family role) directly under the base role, or under the Staff role when no base role is set.

    Returns None when the role is where it should be, otherwise a short reason why it isn't.
    The positions are read fresh from Discord whenever a move is needed (the cached ones go stale right after a role is made or
    moved), and one lock per server keeps the family, Best family and Top 1 syncs from undoing each other."""
    base_id = families.guild(guild.id)["base_role"]
    if base_id and guild.get_role(base_id) is None:
        return "the base role was deleted. Set it again with the baserole command"
    anchor = family_anchor(guild)
    if anchor is None:
        return "I can't find a Staff role (or a base role). Pick one with `.roles staff @role`"
    return await roleplace.place_under(guild, role, anchor, family_group_ids(guild), "Family roles go under the Staff / base role", fresh)


async def ensure_role(guild, fam):
    """The family's role; made again if it was deleted. None if the bot isn't allowed to."""
    role = guild.get_role(fam.get("role") or 0)
    if role is not None:
        return role
    color = discord.Colour(fam["color"]) if fam.get("color") else discord.Colour.default()
    try:
        role = await guild.create_role(
            name=fam.get("role_name") or fam["name"], colour=color,
            reason=f"Role for the family {fam['name']}",
        )
    except discord.HTTPException:
        return None
    fam["role"] = role.id
    families.save()
    await asyncio.sleep(0.5)  # let Discord's role list settle before moving it
    await place_role(guild, role)
    return role


_sync_locks = {}


async def sync_family(guild, fam, fresh=False):
    """Families no longer have a role or a forced [TAG] nickname (v34). This only cleans up what older versions left behind,
    once per family: the old family role is deleted and every member gets their own nickname back. Returns a list of short problems."""
    if fam.get("cleaned"):
        return []
    lock = _sync_locks.setdefault((guild.id, fam["id"]), asyncio.Lock())
    problems = []
    async with lock:
        role = guild.get_role(fam.get("role") or 0)
        if role is not None:
            try:
                await role.delete(reason="Family roles were removed")
            except discord.HTTPException:
                problems.append(f"I couldn't delete the old role of {fam['name']} (I need Manage Roles and my role must be above it).")
        prefix = f"[{fam['tag']}] " if fam.get("tag") else None
        for uid, info in list(fam["members"].items()):
            member = guild.get_member(int(uid))
            if prefix and member is not None and member.nick and member.nick.startswith(prefix):
                try:
                    await member.edit(nick=strip_tag(member.nick, fam["tag"]), reason="Family tags are no longer forced")
                except discord.HTTPException:
                    problems.append(f"I couldn't reset the nickname of {member.mention} (the server owner and people above my role can't be renamed).")
                await asyncio.sleep(0.3)
        if not problems:  # try again on the next start when something failed
            fam["role"] = None
            fam["cleaned"] = True
            families.save()
    return problems


async def sync_all_families(guild, fresh=False):
    """Startup / timer repair for one server: every family gets the same check as sync_family. Returns the problems found."""
    found = []
    for fam in families.all(guild.id):
        try:
            found.extend(await sync_family(guild, fam, fresh))
            fresh = False  # the positions were just read from Discord, the next family can use them
        except Exception:
            log.exception("Could not sync family %s", fam.get("name"))
        await asyncio.sleep(1)
    return found


def schedule_family_sync(guild):
    task = asyncio.create_task(sync_all_families(guild))
    _best_tasks.add(task)
    task.add_done_callback(_best_tasks.discard)


async def apply_member(guild, fam, member):
    """Someone joined a family. Nothing is given or renamed any more (no family role, no [TAG] nickname). Returns a list of problems (always empty)."""
    schedule_best_role(guild)
    return []


async def release_member(guild, fam, uid, info, remove_role=True):
    """Someone left a family. Only removes a leftover old family role if one still exists (see sync_family)."""
    member = guild.get_member(int(uid))
    if member is None:
        return
    role = guild.get_role(fam.get("role") or 0)
    if remove_role and role is not None and role in member.roles:
        try:
            await member.remove_roles(role, reason="Left a family")
        except discord.HTTPException:
            pass


async def drop_role_and_banner(guild, fam):
    role = guild.get_role(fam.get("role") or 0)
    if role is not None:
        try:
            await role.delete(reason="Family deleted")
        except discord.HTTPException:
            pass
    remove_banner_file(fam)


async def depart(guild, fam, uid):
    """Take someone out of a family. If it was the owner the crown moves on (or the family ends).
    Returns (new_owner_id, family_deleted)."""
    info = fam["members"].pop(str(uid), None)
    if info is None:
        return None, False
    new_owner, gone = None, False
    if info["rank"] == "owner":
        if fam["members"]:
            new_owner = hand_over_crown(fam)
        else:
            gone = True
    if gone:
        refund_bank(guild.id, fam, [uid])  # the last member takes the bank with them
        families.delete(guild.id, fam)
    else:
        families.save()
    await release_member(guild, fam, uid, info)
    if gone:
        await drop_role_and_banner(guild, fam)
    schedule_best_role(guild)
    return new_owner, gone


async def delete_family(guild, fam):
    members = dict(fam["members"])
    refund_bank(guild.id, fam, sorted(members, key=lambda u: members[u]["rank"] != "owner"))  # shared out, the owner first
    families.delete(guild.id, fam)
    for uid, info in members.items():
        await release_member(guild, fam, uid, info, remove_role=False)
        await asyncio.sleep(0.3)
    await drop_role_and_banner(guild, fam)
    schedule_best_role(guild)


# ---- "Best family" role: held by every member of the #1 family -------------
BEST_ROLE_NAME = "Best family"
_best_locks = {}
_best_tasks = set()


async def sync_best_role(guild, fresh=False):
    """Make sure the bot-made 'Best family' role exists, sits under the Staff / base role, and only the top family's members hold it.
    Returns a sentence about what is wrong, or None."""
    lock = _best_locks.setdefault(guild.id, asyncio.Lock())
    problem = None
    async with lock:
        g = families.guild(guild.id)
        ranking = families.ranking(guild.id)
        role = guild.get_role(g["best_role"] or 0)
        if role is None:
            if not ranking:
                return None
            try:
                role = await guild.create_role(
                    name=BEST_ROLE_NAME, colour=discord.Colour.gold(), reason="Role for the top family",
                )
            except discord.HTTPException:
                return "Discord refused to create the Best family role."
            g["best_role"] = role.id
            families.save()
            await asyncio.sleep(0.5)
        problem = await place_role(guild, role, fresh)  # every run, not only when the role is made
        note_problem(guild.id, "best", problem)
        winners = set(ranking[0]["members"]) if ranking else set()
        for member in list(role.members):
            if str(member.id) not in winners:
                try:
                    await member.remove_roles(role, reason="No longer in the top family")
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.3)
        for uid in winners:
            member = guild.get_member(int(uid))
            if member is not None and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Member of the top family")
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.3)
    return problem


def schedule_best_role(guild):
    """Run sync_best_role in the background so commands don't wait for it."""
    task = asyncio.create_task(sync_best_role(guild))
    _best_tasks.add(task)
    task.add_done_callback(_best_tasks.discard)


# ---- buttons --------------------------------------------------------------
class ConfirmView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.confirmed = False
        style_buttons(self, {"Confirm": "check", "Cancel": "cross"})

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(embed=fam_embed("warn", "This isn't your confirmation.", interaction.user), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def yes(self, interaction, button):
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction, button):
        await interaction.response.defer()
        self.stop()


async def fam_confirm(ctx, text):
    view = ConfirmView(ctx.author.id)
    message = await ctx.send(embed=fam_embed("warn", text, ctx.author), view=view)
    await view.wait()
    return view.confirmed, message


class InviteView(discord.ui.View):
    def __init__(self, fam_id, inviter, target):
        super().__init__(timeout=INVITE_SECONDS)
        self.fam_id = fam_id
        self.inviter = inviter
        self.target = target
        self.message = None
        self.done = False
        style_buttons(self, {"Accept": "check", "Decline": "cross"})

    async def interaction_check(self, interaction):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message(
                embed=fam_embed("warn", f"This invite is for {self.target.mention}.", interaction.user), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        await interaction.response.defer()
        self.done = True
        self.stop()
        guild, member = interaction.guild, interaction.user
        fam = families.get(guild.id, self.fam_id)
        if fam is None:
            embed = fam_embed("err", "That family doesn't exist anymore.", member)
        elif families.by_member(guild.id, member.id):
            embed = fam_embed("err", "You are already in a family.", member)
        elif len(fam["members"]) >= FAMILY_MAX_MEMBERS:
            embed = fam_embed("err", f"{fam_name(fam)} is full.", member)
        else:
            families.add_member(fam, member.id, strip_tag(member.nick, fam["tag"]))
            problems = await apply_member(guild, fam, member)
            text = f"You joined {fam_name(fam)}!"
            if problems:
                text += "\n" + "\n".join(warn_line(p) for p in problems)
            embed = fam_embed("add", text, member)
        await interaction.edit_original_response(content=None, embed=embed, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction, button):
        self.done = True
        self.stop()
        await interaction.response.edit_message(
            content=None, embed=fam_embed("warn", "You declined the invite.", self.target), view=None
        )

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        try:
            await self.message.edit(content=None, embed=fam_embed("warn", "The invite expired.", self.inviter), view=None)
        except discord.HTTPException:
            pass


# ---- the command group ----------------------------------------------------
@bot.hybrid_group(name="fam", invoke_without_command=True, description="Families: create, join, manage and compete", fallback="help")
@commands.guild_only()
async def family(ctx):
    await send_help(ctx, "fam")


@family.command(name="create", description="Start a family (just a name)")
@commands.cooldown(1, 5, commands.BucketType.user)
async def fam_create(ctx, *, text: str = ""):
    name = " ".join(text.split())
    if not name:
        await fam_say(ctx, "warn", f"Usage: `{prefix_for('fam')}fam create (name)`. Example: `{prefix_for('fam')}fam create Wolves`")
        return
    gid = ctx.guild.id
    if families.by_member(gid, ctx.author.id):
        await fam_say(ctx, "err", "You are already in a family. Leave it first.")
        return
    problem = name_error(name)
    if problem:
        await fam_say(ctx, "warn", problem)
        return
    if families.name_taken(gid, name):
        await fam_say(ctx, "err", f"A family called **{fam_esc(name)}** already exists.")
        return
    fam = families.create(gid, ctx.author.id, name, "", ctx.author.nick)
    problems = await apply_member(ctx.guild, fam, ctx.author)
    lines = [status_text("ok", f"{ctx.author.mention}: Family **{fam_esc(name)}** created.")]
    lines.append(f"Recruit with `{prefix_for('fam')}fam invite @user`.")
    lines += [warn_line(p) for p in problems]
    await ctx.send(embed=discord.Embed(description="\n".join(lines), color=OK_COLOR))


@family.command(name="invite", description="Invite someone to your family")
async def fam_invite(ctx, member: discord.Member):
    fam = await need_family(ctx, "admin")
    if fam is None:
        return
    if member.bot or member.id == ctx.author.id:
        await fam_say(ctx, "err", "Pick someone else to invite.")
        return
    if families.by_member(ctx.guild.id, member.id):
        await fam_say(ctx, "err", f"{member.mention} is already in a family.")
        return
    if len(fam["members"]) >= FAMILY_MAX_MEMBERS:
        await fam_say(ctx, "err", f"Your family is full ({FAMILY_MAX_MEMBERS}/{FAMILY_MAX_MEMBERS}).")
        return
    ends = int(time.time()) + INVITE_SECONDS
    view = InviteView(fam["id"], ctx.author, member)
    embed = discord.Embed(
        description=(
            f"{ctx.author.mention} invited {member.mention} to join {fam_name(fam)}.\n"
            f"The invite expires <t:{ends}:R>."
        ),
        color=theme(),
    )
    view.message = await ctx.send(
        content=member.mention, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(users=[member])
    )


def mention_list(uids, rank, limit=15, gid=None):
    """One mention per line with the rank icon in front (and the person's title after the name when `gid` is given), cut off
    after `limit` people, or earlier when the lines would not fit in one embed field (1024 characters)."""
    icon = rank_icon(rank)
    lines = []
    size = 0
    for uid in uids[:limit]:
        line = f"{icon} <@{uid}>" + (title_tag(gid, uid) if gid is not None else "")
        if size + len(line) + 1 > 960:  # leave room for the "and N more" line
            break
        lines.append(line)
        size += len(line) + 1
    if len(uids) > len(lines):
        lines.append(f"and {len(uids) - len(lines)} more")
    return "\n".join(lines)


def tree_lines(lines):
    """Bleed-style tree: every line but the last gets the 'whitereplyextend' connector, the last one 'whitereply'.
    Without those two emojis on a server the lines are returned as plain text."""
    mid, last = emo("replyextend"), emo("reply")
    if not (mid and last) or len(lines) < 2:
        return "\n".join(lines)
    return "\n".join(f"{mid if i < len(lines) - 1 else last} {line}" for i, line in enumerate(lines))


def info_embed(guild, fam, viewer=None, problems=None):
    """A family card in the same look as Bleed's embeds: author line, bold title, bold labels with the
    value underneath, footer with the details. The owner has their own field, never mixed with the members."""
    ranking = families.ranking(guild.id)
    place = [f["id"] for f in ranking].index(fam["id"]) + 1
    roster = sorted(fam["members"].items(), key=lambda kv: kv[1]["joined"])
    admins = [uid for uid, info in roster if info["rank"] == "admin"]
    members = [uid for uid, info in roster if info["rank"] == "member"]

    embed = discord.Embed(
        title=fam["name"],
        description=fam["description"] or "No description set.",
        color=theme(),
    )
    with_author(embed, viewer)
    embed.add_field(name="Owner", value=f"{rank_icon('owner')} <@{owner_id(fam)}>{title_tag(guild.id, owner_id(fam))}", inline=False)
    if admins:
        embed.add_field(name=f"Admins ({len(admins)})", value=mention_list(admins, "admin", gid=guild.id), inline=False)
    embed.add_field(
        name=f"Members ({len(members)})",
        value=mention_list(members, "member", gid=guild.id) if members else "No other members yet.",
        inline=False,
    )
    info_lines = [
        f"Created: <t:{fam['created']}:D>",
        f"Bank: {bank_of(fam):,} reputation",
        f"Kidnaps: {fam['kidnaps']} taken \u00b7 {fam['lost']} lost",
    ]
    embed.add_field(name="Information", value=tree_lines(info_lines), inline=False)
    if problems:
        embed.add_field(name="Needs attention", value="\n".join(warn_line(p) for p in problems), inline=False)
    embed.set_footer(
        text=f"Rank #{place} of {len(ranking)} \u2022 {len(fam['members'])}/{FAMILY_MAX_MEMBERS} members \u2022 Module: family"
    )
    return embed


@family.command(name="info", aliases=["profile"], description="Show a family")
async def fam_info(ctx, *, who: str = None):
    gid = ctx.guild.id
    if who is None:
        fam = families.by_member(gid, ctx.author.id)
        if fam is None:
            await fam_say(ctx, "err", "You are not in a family.")
            return
    else:
        member = None
        by_name = None if re.fullmatch(r"<@!?\d+>|\d{15,}", who.strip()) else families.by_name(gid, who)
        if by_name is None:
            try:
                member = await commands.MemberConverter().convert(ctx, who)
            except commands.BadArgument:
                member = None
        if member is not None:
            fam = families.by_member(gid, member.id)
            if fam is None:
                await fam_say(ctx, "err", f"{member.mention} is not in a family.")
                return
        else:
            fam = by_name
            if fam is None:
                await fam_say(ctx, "err", f"I couldn't find a family or member called **{fam_esc(who[:40])}**.")
                return
    problems = []
    try:
        async with typing_for(ctx):
            problems = await sync_family(ctx.guild, fam)  # fixes missing roles first, so the card is always current
    except Exception:
        log.exception("Could not sync family %s before showing it", fam.get("name"))
    embed = info_embed(ctx.guild, fam, ctx.author, problems)
    path = banner_path(fam)
    file = None
    if path and os.path.exists(path):
        ext = path.rsplit(".", 1)[-1]
        file = discord.File(path, filename=f"banner.{ext}")
        embed.set_image(url=f"attachment://banner.{ext}")
    await ctx.send(embed=embed, file=file)


async def same_family_target(ctx, fam, member):
    if member.id == ctx.author.id:
        await fam_say(ctx, "err", "You can't do that to yourself.")
        return False
    if str(member.id) not in fam["members"]:
        await fam_say(ctx, "err", f"{member.mention} is not in your family.")
        return False
    return True


@family.command(name="promote", description="Make a member a family admin")
async def fam_promote(ctx, member: discord.Member):
    fam = await need_family(ctx, "owner")
    if fam is None or not await same_family_target(ctx, fam, member):
        return
    if rank_of(fam, member.id) == "admin":
        await fam_say(ctx, "warn", f"{member.mention} is already an admin. Use `{prefix_for('fam')}fam transfer` to hand over the family.")
        return
    fam["members"][str(member.id)]["rank"] = "admin"
    families.save()
    await fam_say(ctx, "add", f"{member.mention} was promoted to **admin** of {fam_name(fam)}.")


@family.command(name="demote", description="Make a family admin a regular member")
async def fam_demote(ctx, member: discord.Member):
    fam = await need_family(ctx, "owner")
    if fam is None or not await same_family_target(ctx, fam, member):
        return
    if rank_of(fam, member.id) != "admin":
        await fam_say(ctx, "warn", f"{member.mention} is not an admin.")
        return
    fam["members"][str(member.id)]["rank"] = "member"
    families.save()
    await fam_say(ctx, "remove", f"{member.mention} was demoted to **member**.")


@family.command(name="kick", description="Remove someone from your family")
async def fam_kick(ctx, member: discord.Member):
    fam = await need_family(ctx, "admin")
    if fam is None or not await same_family_target(ctx, fam, member):
        return
    if RANK_LEVEL[rank_of(fam, ctx.author.id)] <= RANK_LEVEL[rank_of(fam, member.id)]:
        await fam_say(ctx, "err", "You can only kick people below your rank.")
        return
    await depart(ctx.guild, fam, member.id)
    await fam_say(ctx, "remove", f"{member.mention} was kicked from {fam_name(fam)}.")


@family.command(name="rename", description="Rename your family")
@commands.cooldown(1, 10, commands.BucketType.user)
async def fam_rename(ctx, *, name: str):
    fam = await need_family(ctx, "owner")
    if fam is None:
        return
    name = " ".join(name.split())
    problem = name_error(name)
    if problem:
        await fam_say(ctx, "warn", problem)
        return
    if families.name_taken(ctx.guild.id, name, ignore=fam):
        await fam_say(ctx, "err", f"A family called **{fam_esc(name)}** already exists.")
        return
    fam["name"] = name
    families.save()
    await fam_say(ctx, "ok", f"Your family is now called {fam_name(fam)}.")


@family.command(name="description", aliases=["desc"], description="Set the family description")
async def fam_description(ctx, *, text: str):
    fam = await need_family(ctx, "admin")
    if fam is None:
        return
    if text.strip().lower() in ("clear", "remove", "none"):
        fam["description"] = ""
        families.save()
        await fam_say(ctx, "remove", "Description removed.")
        return
    if len(text) > DESC_MAX:
        await fam_say(ctx, "warn", f"Keep the description under **{DESC_MAX}** characters.")
        return
    fam["description"] = text.strip()
    families.save()
    await fam_say(ctx, "ok", "Description updated.")


@family.command(name="banner", description="Set or clear the family banner")
@commands.cooldown(1, 10, commands.BucketType.user)
async def fam_banner(ctx, image: discord.Attachment = None, *, text: str = None):
    fam = await need_family(ctx, "admin")
    if fam is None:
        return
    if text and text.strip().lower() in ("clear", "remove", "none"):
        remove_banner_file(fam)
        fam["banner"] = None
        families.save()
        await fam_say(ctx, "remove", "Banner removed.")
        return
    if image is None:
        await fam_say(ctx, "warn", f"Attach an image to the message: `{prefix_for('fam')}fam banner` (PNG, JPG, GIF or WEBP, under 8 MB). Use `{prefix_for('fam')}fam banner remove` to clear it.")
        return
    attachment = image
    if attachment.size > 8 * 1024 * 1024:
        await fam_say(ctx, "warn", "That image is over 8 MB.")
        return
    data = await attachment.read()
    ext = image_ext(data)
    if ext is None:
        await fam_say(ctx, "warn", "Use a PNG, JPG, GIF or WEBP image.")
        return
    remove_banner_file(fam)
    os.makedirs(FAMILY_BANNER_DIR, exist_ok=True)
    fam["banner"] = f"{ctx.guild.id}_{fam['id']}.{ext}"
    with open(banner_path(fam), "wb") as f:
        f.write(data)
    families.save()
    await fam_say(ctx, "ok", "Banner updated.")


@family.command(name="transfer", description="Give the family to another member")
async def fam_transfer(ctx, member: discord.Member):
    fam = await need_family(ctx, "owner")
    if fam is None or not await same_family_target(ctx, fam, member):
        return
    fam["members"][str(ctx.author.id)]["rank"] = "admin"
    fam["members"][str(member.id)]["rank"] = "owner"
    families.save()
    await fam_say(ctx, "ok", f"{member.mention} is now the owner of {fam_name(fam)}. You are an admin.")


@family.command(name="leave", description="Leave your family")
async def fam_leave(ctx):
    fam = await need_family(ctx)
    if fam is None:
        return
    if rank_of(fam, ctx.author.id) == "owner":
        if len(fam["members"]) == 1:
            await fam_say(ctx, "warn", f"You're the only member. Use `{prefix_for('fam')}fam delete` to end the family.")
        else:
            await fam_say(ctx, "warn", f"You own this family. Use `{prefix_for('fam')}fam transfer @member` first, or `{prefix_for('fam')}fam delete`.")
        return
    await depart(ctx.guild, fam, ctx.author.id)
    await fam_say(ctx, "remove", f"You left {fam_name(fam)}.")


@family.command(name="delete", description="Delete your family")
async def fam_delete(ctx):
    fam = await need_family(ctx, "owner")
    if fam is None:
        return
    yes, message = await fam_confirm(ctx, f"Delete {fam_name(fam)} for good? Its {len(fam['members'])} member(s) go away.")
    if not yes or families.get(ctx.guild.id, fam["id"]) is None:
        await message.edit(embed=fam_embed("warn", "Nothing was deleted.", ctx.author), view=None)
        return
    await delete_family(ctx.guild, fam)
    await message.edit(embed=fam_embed("remove", f"Family **{fam_esc(fam['name'])}** was deleted.", ctx.author), view=None)


@family.command(name="kidnap", description="Try to take a member from another family")
async def fam_kidnap(ctx, member: discord.Member):
    gid = ctx.guild.id
    mine = await need_family(ctx)
    if mine is None:
        return
    if member.bot or member.id == ctx.author.id:
        await fam_say(ctx, "err", "Pick someone from another family.")
        return
    theirs = families.by_member(gid, member.id)
    if theirs is None:
        await fam_say(ctx, "err", f"{member.mention} is not in a family.")
        return
    if theirs["id"] == mine["id"]:
        await fam_say(ctx, "err", "That's your own family.")
        return
    if owner_id(theirs) == member.id:
        await fam_say(ctx, "err", "Family owners can't be kidnapped.")
        return
    g = families.guild(gid)
    now = time.time()
    g["cooldowns"] = {u: t for u, t in g["cooldowns"].items() if t > now}
    g["immune"] = {u: t for u, t in g["immune"].items() if t > now}
    wait = g["cooldowns"].get(str(ctx.author.id))
    if wait:
        await fam_say(ctx, "cooldown", f"You're laying low. Try again <t:{int(wait)}:R>.")
        return
    safe = g["immune"].get(str(member.id))
    if safe:
        await fam_say(ctx, "warn", f"{member.mention} was just kidnapped and is protected until <t:{int(safe)}:R>.")
        return
    if len(mine["members"]) >= FAMILY_MAX_MEMBERS:
        await fam_say(ctx, "err", f"Your family is full ({FAMILY_MAX_MEMBERS}/{FAMILY_MAX_MEMBERS}), there's no room.")
        return
    until = int(now + KIDNAP_COOLDOWN)
    g["cooldowns"][str(ctx.author.id)] = now + KIDNAP_COOLDOWN
    if random.random() >= KIDNAP_CHANCE:
        families.save()
        await ctx.send(embed=discord.Embed(
            description=(
                status_text("err", f"{ctx.author.mention}: tried to kidnap {member.mention} from {fam_name(theirs)} and failed.") + "\n"
                f"Next try <t:{until}:R>."
            ),
            color=ERR_COLOR,
        ))
        return
    info = theirs["members"].pop(str(member.id))
    theirs["lost"] += 1
    mine["kidnaps"] += 1
    mine["members"][str(member.id)] = {"rank": "member", "nick": info.get("nick"), "joined": int(now)}
    g["immune"][str(member.id)] = now + KIDNAP_IMMUNITY
    families.save()
    problems = await apply_member(ctx.guild, mine, member)
    text = (
        status_text("ok", f"{ctx.author.mention}: kidnapped {member.mention} from {fam_name(theirs)} for {fam_name(mine)}!") + "\n"
        f"They are protected from kidnaps until <t:{int(now + KIDNAP_IMMUNITY)}:R>. Next try for you <t:{until}:R>."
    )
    text += "".join("\n" + warn_line(p) for p in problems)
    old_owner = ctx.guild.get_member(owner_id(theirs) or 0)
    pings = " ".join(m.mention for m in (member, old_owner) if m is not None)
    await ctx.send(
        content=pings, embed=discord.Embed(description=text, color=OK_COLOR),
        allowed_mentions=discord.AllowedMentions(users=True),
    )


def top_page_embed(guild, ranking, index, viewer=None):
    """One page of the leaderboard, laid out like Bleed's top lists: ranked lines, then 'Your ...', then the page footer."""
    pages = max(1, -(-len(ranking) // TOP_PER_PAGE))
    start = index * TOP_PER_PAGE
    lines = []
    for n, fam in enumerate(ranking[start:start + TOP_PER_PAGE], start=start + 1):
        count = len(fam["members"])
        badge = emo("owner", "\u2605") if n == 1 else f"`{n}`"
        lines.append(f"{badge} **{fam_esc(fam['name'])}** has **{bank_of(fam):,}** in the bank ({count} member{'s' if count != 1 else ''})")
    mine = families.by_member(guild.id, viewer.id) if viewer is not None else None
    if mine is not None:
        place = [f["id"] for f in ranking].index(mine["id"]) + 1
        lines.append(f"Your family: **{fam_esc(mine['name'])}** - Rank: `#{place}`")
    else:
        lines.append("Your family: **none** - Rank: `Unranked`")
    embed = discord.Embed(title="Top families", description="\n".join(lines), color=theme())
    with_author(embed, viewer)
    embed.set_footer(text=f"Page {index + 1}/{pages} ({len(ranking)} entries)")
    return embed


@family.command(name="top", aliases=["leaderboard", "lb"], description="The families with the most reputation in their bank")
async def fam_top(ctx):
    ranking = families.ranking(ctx.guild.id)
    if not ranking:
        await fam_say(ctx, "warn", f"No families yet. Start one with `{prefix_for('fam')}fam create (name)`.")
        return
    pages = -(-len(ranking) // TOP_PER_PAGE)

    def build(i):
        return top_page_embed(ctx.guild, ranking, i, ctx.author)

    if pages == 1:
        await ctx.send(embed=build(0))
        return
    view = Pager(ctx.author.id, build, pages)
    view.message = await ctx.send(embed=build(0), view=view)


# ---- family bank: members put reputation in, the leaderboard ranks families by it ------------
@family.command(name="rep", aliases=["bank"], description="Show how much reputation your family's bank holds")
async def fam_bank(ctx):
    fam = await need_family(ctx)
    if fam is None:
        return
    place = [f["id"] for f in families.ranking(ctx.guild.id)].index(fam["id"]) + 1
    await fam_say(ctx, "ok", f"{fam_name(fam)} has **{bank_of(fam):,}** reputation in the bank. Rank: `#{place}`.")


@family.command(name="deposit", aliases=["dep"], description="Put some of your reputation into your family's bank")
@app_commands.describe(amount="How much: 500, 2k, half or all")
@commands.cooldown(1, 5, commands.BucketType.user)
async def fam_deposit(ctx, amount: str):
    fam = await need_family(ctx)
    if fam is None:
        return
    gid, uid = ctx.guild.id, ctx.author.id
    mine = auras.balance(gid, uid)
    value = parse_amount(amount, mine)
    if value is None or value < 1:
        await fam_say(ctx, "warn", "Give a whole number of reputation, like `500` (`2k`, `half` and `all` work too).")
        return
    if value > mine:
        await fam_say(ctx, "warn", f"You only have **{mine:,}** reputation.")
        return
    auras.add(gid, uid, -value)  # no await between the check above and these lines, so it can't be done twice at once
    fam["bank"] = bank_of(fam) + value
    families.save()
    schedule_best_role(ctx.guild)
    await fam_say(ctx, "ok", f"put **{value:,}** reputation into the bank of {fam_name(fam)}. The bank now holds **{bank_of(fam):,}**.")


@family.command(name="withdraw", aliases=["with"], description="Take reputation out of the family bank (family admins and the owner)")
@app_commands.describe(amount="How much: 500, 2k, half or all")
@commands.cooldown(1, 5, commands.BucketType.user)
async def fam_withdraw(ctx, amount: str):
    fam = await need_family(ctx, "admin")
    if fam is None:
        return
    held = bank_of(fam)
    value = parse_amount(amount, held)
    if value is None or value < 1:
        await fam_say(ctx, "warn", "Give a whole number of reputation, like `500` (`2k`, `half` and `all` work too).")
        return
    if value > held:
        await fam_say(ctx, "warn", f"The bank only holds **{held:,}** reputation.")
        return
    fam["bank"] = held - value
    families.save()
    auras.add(ctx.guild.id, ctx.author.id, value)
    schedule_best_role(ctx.guild)
    await fam_say(ctx, "ok", f"took **{value:,}** reputation out of the bank of {fam_name(fam)}. It now holds **{bank_of(fam):,}**.")


# ---- family war: families score from their members' aura each day ----------------------------
war_posts = []  # (server id, result) waiting to be announced in the war channel


def war_roll(gid, now=None):
    """Close the previous war when a new day (UTC) has started: the top family's members get WAR_BONUS reputation.
    Returns the result when a war was closed, else None. Safe to call any time (it only acts once per day).
    The first call after the weekly version was replaced closes the unfinished week the same way."""
    g = families.guild(gid)
    cur = war_day_id(now)
    last = g.get("war_week")
    if last == cur:
        return None
    result = None
    if last is not None:
        scored = [f for f in families.all(gid) if (f.get("war") or {}).get("week") == last and f["war"]["score"] > 0]
        if scored:
            win = max(scored, key=lambda f: (f["war"]["score"], -f["created"]))  # a tie goes to the older family
            for uid in win["members"]:
                auras.add(gid, int(uid), WAR_BONUS)  # not through give_aura: the bonus doesn't count as war score
            result = {"week": last, "family": win["name"], "tag": win["tag"], "score": win["war"]["score"],
                      "bonus": WAR_BONUS, "members": len(win["members"])}
            g["last_war"] = result
            war_posts.append((gid, result))
    g["war_week"] = cur
    for fam in families.all(gid):
        fam["war"] = {"week": cur, "score": 0}
    families.save()
    return result


def give_aura(gid, uid, amount, war=True):
    """Change someone's reputation (saved). A gain also counts for their family in today's war. Returns the new total."""
    war_roll(gid)
    total = auras.add(gid, uid, amount)
    if war and amount > 0:
        fam = families.by_member(gid, uid)
        if fam is not None:
            cur = war_day_id()
            w = fam.get("war")
            if not w or w.get("week") != cur:
                w = fam["war"] = {"week": cur, "score": 0}
            w["score"] += amount
            families.save()
    return total


async def flush_war_posts():
    """Announce finished wars in each server's war channel (if one is set)."""
    while war_posts:
        gid, res = war_posts.pop(0)
        guild = bot.get_guild(gid)
        channel = guild.get_channel(families.guild(gid).get("war_channel") or 0) if guild else None
        if channel is None:
            continue
        text = (f"{emo('announce')} **{fam_esc(res['family'])}** won yesterday's family war with "
                f"**{res['score']:,}** reputation! Each of its **{res['members']}** members got **{res['bonus']:,}** reputation.").lstrip()
        try:
            await channel.send(embed=discord.Embed(description=text, color=theme()))
        except discord.HTTPException:
            log.warning("Could not announce the family war result in %s", channel, exc_info=True)


async def war_loop():
    """Every minute: close the day's war for every server that is due (also right after a restart). Cheap: it only compares two strings."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            for gid in list(families.data):
                war_roll(int(gid))
            await flush_war_posts()
        except Exception:
            log.exception("The family war check failed")
        await asyncio.sleep(60)


def war_page_embed(guild, rows, index, viewer):
    """One page of the daily war standings (rows = [(family, score)], best first)."""
    pages = max(1, -(-len(rows) // TOP_PER_PAGE))
    start = index * TOP_PER_PAGE
    lines = [f"Ends <t:{war_end()}:R>. The top family's members each get **{WAR_BONUS:,}** reputation."]
    for n, (fam, score) in enumerate(rows[start:start + TOP_PER_PAGE], start=start + 1):
        badge = emo("owner", "\u2605") if n == 1 else f"`{n}`"
        lines.append(f"{badge} {fam_name(fam)} earned **{score:,}** reputation")
    mine = families.by_member(guild.id, viewer.id)
    if mine is None:
        lines.append("Your family: **none** - Rank: `Unranked`")
    else:
        place = next((i + 1 for i, (f, _s) in enumerate(rows) if f["id"] == mine["id"]), None)
        shown = f"#{place}" if place else "Unranked"
        lines.append(f"Your family: **{fam_esc(mine['name'])}** - Rank: `{shown}`")
    last = families.guild(guild.id).get("last_war")
    if last:
        lines.append(f"Yesterday: **{fam_esc(last['family'])}** won with **{last['score']:,}** reputation.")
    embed = discord.Embed(title="Family war", description="\n".join(lines), color=theme())
    with_author(embed, viewer)
    embed.set_footer(text=f"Page {index + 1}/{pages} ({len(rows)} entries)")
    return embed


@family.group(name="war", invoke_without_command=True, description="Today's family war", fallback="view")
@commands.guild_only()
async def fam_war(ctx):
    """Today's family war standings."""
    gid = ctx.guild.id
    war_roll(gid)
    await flush_war_posts()
    cur = war_day_id()
    rows = [(f, f["war"]["score"]) for f in families.all(gid) if (f.get("war") or {}).get("week") == cur and f["war"]["score"] > 0]
    rows.sort(key=lambda r: (-r[1], r[0]["created"]))
    if not rows:
        text = (f"No family has earned reputation today yet. Earn some with `{prefix_for('daily')}daily`, "
                f"`{prefix_for('ratio')}ratio` or `{prefix_for('quests')}quests`. Ends <t:{war_end()}:R>.")
        await fam_say(ctx, "warn", text)
        return
    pages = -(-len(rows) // TOP_PER_PAGE)

    def build(i):
        return war_page_embed(ctx.guild, rows, i, ctx.author)

    if pages == 1:
        await ctx.send(embed=build(0))
        return
    view = Pager(ctx.author.id, build, pages)
    view.message = await ctx.send(embed=build(0), view=view)


@fam_war.command(name="channel", description="Where the daily war winner is announced")
@commands.has_permissions(manage_guild=True)
async def fam_war_channel(ctx, *, where: str = None):
    """Where each day's winner is announced (off to stop)."""
    g = families.guild(ctx.guild.id)
    if where is None:
        channel = ctx.guild.get_channel(g.get("war_channel") or 0)
        if channel is None:
            await fam_say(ctx, "warn", f"No war channel is set. Use `{prefix_for('fam')}fam war channel #channel`.")
        else:
            await fam_say(ctx, "ok", f"The daily winner is announced in {channel.mention}.")
        return
    if where.strip().lower() in ("off", "none", "clear", "remove", "reset"):
        g["war_channel"] = None
        families.save()
        await fam_say(ctx, "ok", "I won't announce the daily winner anymore.")
        return
    try:
        channel = await commands.TextChannelConverter().convert(ctx, where.strip())
    except commands.BadArgument:
        await fam_say(ctx, "warn", "I couldn't find that channel.")
        return
    perms = channel.permissions_for(ctx.guild.me)
    if not (perms.send_messages and perms.embed_links):
        await fam_say(ctx, "warn", f"I can't send embeds in {channel.mention}.")
        return
    g["war_channel"] = channel.id
    families.save()
    await fam_say(ctx, "ok", f"The daily winner will be announced in {channel.mention}.")


# ---- staff list: a board of the people with the Staff role and their rank, kept at the bottom of a channel -------
STAFF_BOARD_FILE = os.path.join(DATA_DIR, "staffboard.json")
STAFF_BOARD_DELAY = int(os.getenv("STAFF_BOARD_DELAY", "5"))  # seconds of quiet before the board moves back to the bottom
# Highest rank first: (what is shown, role names that count). Names are matched ignoring case, emojis, symbols and fancy
# letter styles ("co own", "Co-Own", "⚡ co own" and "𝗰𝗼 𝗼𝘄𝗻" are all the same role). Someone with several of these shows with the
# highest. Everyone with at least one of these roles is listed, whether or not they also have the Staff role; people who only have
# the Staff role (the general mods role) are not listed.
STAFF_RANKS = [
    ("Owner", ("own", "owner")),
    ("Co Owner", ("co own", "co owner", "coown")),
    ("Admin", ("admin", "administrator")),
    ("Moderator", ("moderator", "mod")),
    ("Helper", ("helper",)),
    ("Partnerships manager", ("partnering", "partnership", "partnerships", "partnerships manager")),
]
_SMALL_CAPS = {ord(a): b for a, b in zip("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡʏᴢ", "abcdefghijklmnopqrstuvwyz")}


def role_key(name):
    """A role name as plain lowercase words, whatever font it is written in: '⚡ Co-Own', '𝗰𝗼 𝗼𝘄𝗻' and 'ᴄᴏ ᴏᴡɴ' -> 'co own'."""
    plain = unicodedata.normalize("NFKC", name).translate(_SMALL_CAPS).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def find_role(guild, *names):
    wanted = {role_key(n) for n in names}
    return next((r for r in guild.roles if role_key(r.name) in wanted), None)


def staff_rows(guild):
    """[(member, rank shown)] for everyone with at least one rank role, highest rank first, then A-Z.
    None when none of the rank roles exist in the server."""
    ranks = [(label, find_role(guild, *names)) for label, names in STAFF_RANKS]
    if all(role is None for _label, role in ranks):
        return None
    best = {}  # member id -> (rank number, member, rank shown); the ranks are visited top first, so the highest one wins
    for order, (label, role) in enumerate(ranks):
        for member in (role.members if role is not None else []):
            best.setdefault(member.id, (order, member, label))
    rows = sorted(best.values(), key=lambda r: (r[0], r[1].display_name.lower()))
    return [(member, label) for _order, member, label in rows]


def staff_board_embed(guild):
    """'1- @name Owner' lines under the title "Current staff", the server picture as the thumbnail, and the same
    'Page 1/1 (N entries)' footer as the other top lists."""
    rows = staff_rows(guild)
    if rows is None:
        lines = ["I can't find any of the staff roles (own, co own, Admin, Moderator, Helper, Partnering)."]
    elif not rows:
        lines = ["Nobody has a staff role yet."]
    else:
        lines = [f"{n}- {member.mention} {label}" for n, (member, label) in enumerate(rows, start=1)]
    embed = discord.Embed(title="Current staff", description="\n".join(lines)[:4000], color=theme())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=f"Page 1/1 ({len(rows or [])} entries)")
    return embed


class StaffBoardStore:
    """Where each server's staff board is: {server: {channel, message}}, saved in staffboard.json."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("Could not read %s, starting without staff boards", path)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)


staff_boards = StaffBoardStore(STAFF_BOARD_FILE)
staff_pending = {"move": set(), "edit": set()}  # server ids with a refresh already scheduled
staff_tasks = set()


async def build_staff_board(guild):
    if not guild.chunked:  # big servers don't have every member loaded yet
        try:
            await guild.chunk()
        except Exception:
            log.info("Could not load all members of %s for the staff board", guild, exc_info=True)
    return staff_board_embed(guild)


async def place_staff_board(guild, channel):
    """Post a fresh board at the bottom of `channel` and delete the old one (wherever it was)."""
    embed = await build_staff_board(guild)
    info = staff_boards.data.get(str(guild.id)) or {}
    msg = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    staff_boards.data[str(guild.id)] = {"channel": channel.id, "message": msg.id}
    staff_boards.save()
    old_channel = guild.get_channel(info.get("channel") or 0)
    if old_channel is not None and info.get("message") and info["message"] != msg.id:
        try:
            await old_channel.get_partial_message(info["message"]).delete()
        except discord.HTTPException:
            pass  # already gone


async def edit_staff_board(guild, channel):
    """Update the board where it is (staff changed); post a new one if somebody deleted it."""
    info = staff_boards.data.get(str(guild.id)) or {}
    if not info.get("message"):
        await place_staff_board(guild, channel)
        return
    try:
        await channel.get_partial_message(info["message"]).edit(embed=await build_staff_board(guild))
    except discord.NotFound:
        await place_staff_board(guild, channel)


async def run_staff_job(kind, gid):
    try:
        await asyncio.sleep(STAFF_BOARD_DELAY)
        guild = bot.get_guild(gid)
        info = staff_boards.data.get(str(gid))
        if guild is None or not info:
            return
        channel = guild.get_channel(info["channel"])
        if channel is None:  # the channel is gone
            staff_boards.data.pop(str(gid), None)
            staff_boards.save()
            return
        if kind == "move":
            await place_staff_board(guild, channel)
        else:
            await edit_staff_board(guild, channel)
    except discord.HTTPException:
        log.warning("Could not refresh the staff board of server %s", gid, exc_info=True)
    except Exception:
        log.exception("The staff board refresh failed for server %s", gid)
    finally:
        staff_pending[kind].discard(gid)


def schedule_staff_board(kind, gid):
    """kind 'move' = chat in the channel pushed it up, 'edit' = the staff changed. Several triggers become one refresh."""
    if gid in staff_pending[kind]:
        return
    staff_pending[kind].add(gid)
    task = asyncio.create_task(run_staff_job(kind, gid))
    staff_tasks.add(task)
    task.add_done_callback(staff_tasks.discard)


@bot.listen("on_message")
async def staff_board_sticky(message):
    if message.guild is None or message.author.id == bot.user.id:
        return
    info = staff_boards.data.get(str(message.guild.id))
    if info and info["channel"] == message.channel.id:
        schedule_staff_board("move", message.guild.id)


def staff_related(roles):
    """True when one of these roles is one of the rank roles."""
    keys = {role_key(n) for _label, names in STAFF_RANKS for n in names}
    return any(role_key(r.name) in keys for r in roles)


@bot.listen("on_member_update")
async def staff_board_roles(before, after):
    if str(after.guild.id) in staff_boards.data and set(before.roles) != set(after.roles):
        if staff_related(set(before.roles) ^ set(after.roles)):
            schedule_staff_board("edit", after.guild.id)


@bot.listen("on_guild_role_update")
async def staff_board_role_renamed(before, after):
    """A role was renamed into (or out of) the Staff / rank names."""
    if str(after.guild.id) in staff_boards.data and role_key(before.name) != role_key(after.name) and staff_related([before, after]):
        schedule_staff_board("edit", after.guild.id)


@bot.listen("on_member_remove")
async def staff_board_left(member):
    if str(member.guild.id) in staff_boards.data and staff_related(member.roles):
        schedule_staff_board("edit", member.guild.id)


@bot.hybrid_group(name="stafflist", invoke_without_command=True, description="Post the staff list in this channel", fallback="post")
@commands.guild_only()
@commands.has_permissions(manage_guild=True)
async def stafflist(ctx):
    """Post the staff list in this channel. It stays at the bottom and updates itself."""
    guild = ctx.guild
    if staff_rows(guild) is None:
        await ctx.send(embed=status_embed(
            ctx, "warn", "I can't find any of the staff roles (own, co own, Admin, Moderator, Helper, Partnering)."))
        return
    perms = ctx.channel.permissions_for(guild.me)
    if not (perms.send_messages and perms.embed_links):
        await ctx.send(embed=status_embed(ctx, "warn", "I can't send embeds in this channel."))
        return
    await place_staff_board(guild, ctx.channel)
    missing = [label for label, names in STAFF_RANKS if find_role(guild, *names) is None]
    if missing:
        await ctx.send(embed=status_embed(
            ctx, "warn", f"I couldn't find a role for **{', '.join(missing)}**, so nobody shows with that rank yet."))


@stafflist.command(name="off", aliases=["remove", "stop"], description="Remove the staff list")
@commands.guild_only()
@commands.has_permissions(manage_guild=True)
async def stafflist_off(ctx):
    """Remove the staff list."""
    info = staff_boards.data.pop(str(ctx.guild.id), None)
    if not info:
        await ctx.send(embed=status_embed(ctx, "warn", "There is no staff list in this server."))
        return
    staff_boards.save()
    channel = ctx.guild.get_channel(info["channel"])
    if channel is not None and info.get("message"):
        try:
            await channel.get_partial_message(info["message"]).delete()
        except discord.HTTPException:
            pass
    await ctx.send(embed=status_embed(ctx, "ok", "The staff list is gone."))


# ---- server admin commands (Manage Server) ---------------------------------
def server_owner_check():
    """The server owner, a server Administrator, or the bot owner may set the base role."""
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if await has_full_access(ctx.author, ctx.guild) or ctx.author.guild_permissions.administrator:
            return True
        raise commands.CheckFailure("Only the **server owner** or an **administrator** can set the family base role.")

    return commands.check(predicate)


@family.command(name="baserole", description="The Best family role sits right under this role")
@server_owner_check()
async def fam_baserole(ctx, *, who: str = None):
    g = families.guild(ctx.guild.id)
    if who is None:
        base = ctx.guild.get_role(g["base_role"] or 0)
        if base is None:
            anchor = staff_anchor(ctx.guild)
            where = f"The Best family role sits right under {anchor.mention}." if anchor else "I can't find a Staff role either, pick one with `.roles staff @role`."
            await fam_say(ctx, "warn", f"No base role is set. {where} To use another role: `{prefix_for('fam')}fam baserole @role`.")
        else:
            await fam_say(ctx, "ok", f"The base role is {base.mention}. The Best family role sits right under it.")
        return
    if who.strip().lower() in ("clear", "none", "off", "reset", "remove"):
        g["base_role"] = None
        families.save()
        schedule_best_role(ctx.guild)
        await fam_say(ctx, "remove", "Base role cleared. The Best family role now sits right under the Staff role.")
        return
    try:
        role = await commands.RoleConverter().convert(ctx, who.strip())
    except commands.BadArgument:
        await fam_say(ctx, "warn", "I couldn't find that role.")
        return
    if role.is_default():
        await fam_say(ctx, "err", "Pick a real role, not @everyone.")
        return
    if role.position >= ctx.guild.me.top_role.position:
        await fam_say(ctx, "err", f"My highest role must be above {role.mention}. Drag my role up in Server Settings > Roles, then try again.")
        return
    g["base_role"] = role.id
    families.save()
    moved, reasons = 0, []
    async with typing_for(ctx):
        # the "Best family" role goes right under the base role
        targets = [ctx.guild.get_role(g["best_role"] or 0)]
        for existing in targets:
            if existing is None:
                continue
            reason = await place_role(ctx.guild, existing)
            if reason is None:
                moved += 1
            elif reason not in reasons:
                reasons.append(reason)
            await asyncio.sleep(1)
    text = f"Base role set to {role.mention}. The Best family role sits right under it ({moved} checked)."
    text += "".join("\n" + warn_line(f"Some roles couldn't be moved: {r}.") for r in reasons)
    await fam_say(ctx, "ok" if not reasons else "warn", text)


@family.command(name="admin_add", aliases=["adminadd"], description="Put someone in a family (staff)")
@family_admin_check()
async def fam_admin_add(ctx, member: discord.Member, *, name: str = None):
    gid = ctx.guild.id
    fam = families.by_name(gid, name) if name else families.by_member(gid, ctx.author.id)
    if fam is None:
        await fam_say(ctx, "err", "I couldn't find that family. Give its name." if name else "Give a family name.")
        return
    if member.bot:
        await fam_say(ctx, "err", "Bots can't join families.")
        return
    if families.by_member(gid, member.id):
        await fam_say(ctx, "err", f"{member.mention} is already in a family.")
        return
    if len(fam["members"]) >= FAMILY_MAX_MEMBERS:
        await fam_say(ctx, "err", f"{fam_name(fam)} is full.")
        return
    families.add_member(fam, member.id, strip_tag(member.nick, fam["tag"]))
    problems = await apply_member(ctx.guild, fam, member)
    text = f"{member.mention} was added to {fam_name(fam)}."
    text += "".join("\n" + warn_line(p) for p in problems)
    await fam_say(ctx, "ok", text)


@family.command(name="admin_remove", aliases=["adminremove"], description="Take someone out of their family (staff)")
@family_admin_check()
async def fam_admin_remove(ctx, member: discord.Member):
    fam = families.by_member(ctx.guild.id, member.id)
    if fam is None:
        await fam_say(ctx, "err", f"{member.mention} is not in a family.")
        return
    new_owner, gone = await depart(ctx.guild, fam, member.id)
    text = f"{member.mention} was removed from {fam_name(fam)}."
    if gone:
        text += " It had no one left, so the family was deleted."
    elif new_owner:
        text += f" <@{new_owner}> is the new owner."
    await fam_say(ctx, "ok", text)


@family.command(name="purge", description="Delete a family by name (staff)")
@family_admin_check()
async def fam_purge(ctx, *, name: str):
    fam = families.by_name(ctx.guild.id, name)
    if fam is None:
        await fam_say(ctx, "err", f"I couldn't find a family called **{fam_esc(name[:40])}**.")
        return
    yes, message = await fam_confirm(ctx, f"Delete {fam_name(fam)} for good? Its {len(fam['members'])} member(s) go away.")
    if not yes or families.get(ctx.guild.id, fam["id"]) is None:
        await message.edit(embed=fam_embed("warn", "Nothing was deleted.", ctx.author), view=None)
        return
    await delete_family(ctx.guild, fam)
    await message.edit(embed=fam_embed("remove", f"Family **{fam_esc(fam['name'])}** was deleted.", ctx.author), view=None)


# ---- clean up when people leave (v34: nicknames are no longer forced) ------------------
@bot.event
async def on_member_remove(member):
    fam = families.by_member(member.guild.id, member.id)
    if fam is not None:
        await depart(member.guild, fam, member.id)
    rid = auras.items_of(member.guild.id, member.id).get("role")  # custom role: deleted now, made again (free) when they come back and use ,myrole
    role = member.guild.get_role(rid) if isinstance(rid, int) else None
    if role is not None:
        try:
            await role.delete(reason="The owner left the server")
        except discord.HTTPException:
            log.info("Could not delete the custom role of %s", member, exc_info=True)


# --------------------------------------------------------------------------
# Reputation shop: ,shop ,inventory ,myrole ,title ,bounty and the .roles check.
# The rules live in shopkit.py, the animated pictures in titlecard.py, the role moving in roleplace.py.
# What a member owns is saved inside their reputation record (aura.json, key "items"), so .backup and .restore carry it.
# --------------------------------------------------------------------------
def _num(d, key):
    """A number from a saved record, 0 when it is missing or isn't a usable number (a restored backup can hold anything)."""
    try:
        value = float(d.get(key, 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def held_count(d, key):
    return max(0, int(_num(d, key)))


def prefix_cmd(name):
    """The prefix + name of a command, as typed today (it follows .prefix changes)."""
    return f"{prefix_for(name)}{name}"


# ---- the Soul role (custom roles sit right under it) and the custom roles --------------------------------------
def soul_anchor(guild):
    """The role custom roles sit right under: the one picked with `.roles soul @role`, a role named Soul (any font or emojis),
    else the highest role with the word "soul" in its name. None when there isn't one."""
    pinned = shop_settings.get(guild.id, "soul")
    role = guild.get_role(pinned) if pinned else None
    if role is not None:
        return role
    role = next((r for r in guild.roles if role_key(r.name) == "soul" and not r.is_default()), None)
    if role is not None:
        return role
    named = [r for r in guild.roles if "soul" in role_key(r.name).split() and not r.managed and not r.is_default()]
    return max(named, key=lambda r: r.position) if named else None


def custom_role_ids(guild):
    """The ids of every custom role bought in this server."""
    out = set()
    for rec in auras.data.get(str(guild.id), {}).values():
        held = rec.get("items") if isinstance(rec, dict) else None
        rid = held.get("role") if isinstance(held, dict) else None
        if isinstance(rid, int) and not isinstance(rid, bool):
            out.add(rid)
    return out


def reserved_role_names(guild, own_id=None):
    """Names a custom role may not take: every other role in the server and the staff-like names."""
    names = [r.name for r in guild.roles if r.id != own_id]
    names += list(SOUL_RESERVED) + [n for _label, group in STAFF_RANKS for n in group]
    return [n for n in names if role_key(n)]


def role_name_problem(guild, text, own_id=None):
    """(clean name, None) or (None, what is wrong)."""
    name, problem = shopkit.clean_name(text, CUSTOM_ROLE_NAME_MAX, reserved_role_names(guild, own_id), role_key)
    if problem is None and set(role_key(name).split()) & ROLE_LOOKALIKE_WORDS:
        return None, "That name looks like a staff role. Pick another one."
    if problem is None and store.match(guild.id, name):
        return None, "That name isn't allowed in this server."
    return name, problem


async def make_custom_role(guild, member, name, color):
    """Make a custom role, put it right under the Soul role and give it to `member`. Returns (role, None) or (None, why not).
    Nothing is left behind when a step fails (the role is deleted again)."""
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return None, "I need the Manage Roles permission."
    anchor = soul_anchor(guild)
    if anchor is None:
        return None, f"I can't find a role called Soul. Staff can pick it with `{prefix_cmd('roles')} soul @role`."
    if anchor >= me.top_role:
        return None, f"My highest role has to be above {anchor.mention}. Drag my role up in Server Settings, Roles."
    try:
        role = await guild.create_role(name=name, colour=discord.Colour(color), permissions=discord.Permissions.none(),
                                       reason=f"Custom role of {member}")
    except discord.HTTPException:
        log.info("Could not create a custom role in %s", guild, exc_info=True)
        return None, "Discord refused to create the role."
    await asyncio.sleep(0.5)  # let Discord's role list settle before moving it
    problem = await roleplace.place_under(guild, role, anchor, custom_role_ids(guild) | {role.id},
                                          "Custom roles sit right under the Soul role", True)
    if problem is None:
        try:
            await member.add_roles(role, reason="Custom role")
        except discord.HTTPException:
            problem = "I couldn't give you the role (my role has to be above it)."
    if problem is not None:
        try:
            await role.delete(reason="Custom role could not be set up")
        except discord.HTTPException:
            pass
        return None, problem
    return role, None


def role_color_fields(look):
    """The role.edit() arguments that give a role this look (solid, gradient or holographic).
    Older discord.py versions without role styles only get the main color."""
    first, second, third = shopstyle.colors_for(look)
    if hasattr(discord, "RoleColours"):
        return {"colours": discord.RoleColours(primary=discord.Colour(first),
                                               secondary=discord.Colour(second) if second is not None else None,
                                               tertiary=discord.Colour(third) if third is not None else None)}
    return {"colour": discord.Colour(first)}


async def emoji_icon_bytes(guild, emoji_id):
    """The picture of a custom emoji (for a role icon): this server's emojis first, then any server the bot is in, then Discord's
    picture of that id. None when it can't be loaded."""
    if not emoji_id:
        return None
    emoji = guild.get_emoji(emoji_id) or bot.get_emoji(emoji_id)
    if emoji is None:
        emoji = discord.PartialEmoji(name="icon", id=int(emoji_id), animated=False)
    try:
        return await emoji.read()
    except (discord.HTTPException, AttributeError):
        return None


def find_emoji_id(guild, name):
    """The id of the custom emoji called `name`: this server's first, then any server the bot is in. None when there is none."""
    low = name.lower()
    for pool in (guild.emojis, bot.emojis):
        found = next((e for e in pool if e.name.lower() == low), None)
        if found is not None:
            return found.id
    return None


def look_icon_text(guild, look):
    """The icon of a look as text for an embed / a form box: '<:name:id>', the normal emoji, or ''."""
    if look.get("uemoji"):
        return look["uemoji"]
    if look.get("emoji"):
        e = guild.get_emoji(look["emoji"]) or bot.get_emoji(look["emoji"])
        return str(e) if e is not None else f"<:icon:{look['emoji']}>"
    return ""


async def apply_role_look(guild, member, name, look, change_icon):
    """Save the editor panel on the member's custom role: name, color style + hex codes and (when change_icon) the emoji icon.
    Everything is checked BEFORE Discord is asked, and it is one single edit. Returns (kind, text, role or None). Free."""
    role, problem = await ensure_my_role(guild, member)
    if role is None:
        if problem == "none":
            problem = f"You don't have a custom role. Buy one in `{prefix_cmd('shop')}` (**{ROLE_PRICE:,}** reputation)."
        return "warn", problem, None
    look = shopstyle.clean_look(look)
    problem = shopstyle.look_problem(look, guild.features)
    if problem:
        return "warn", problem, None
    fields = {}
    if name and name.strip() and name.strip() != role.name:
        clean, why = role_name_problem(guild, name, role.id)
        if clean is None:
            return "warn", why, None
        fields["name"] = clean
    fields.update(role_color_fields(look))
    if change_icon:
        if look["emoji"] is None and not look["uemoji"]:
            fields["display_icon"] = None
        else:
            if "ROLE_ICONS" not in guild.features:
                return "warn", "Role icons need this server to reach Boost level 2.", None
            if look["uemoji"]:
                fields["display_icon"] = look["uemoji"]  # a normal emoji is sent as text
            else:
                data = await emoji_icon_bytes(guild, look["emoji"])
                if data is None:
                    return "warn", "I couldn't load that emoji. Try another one.", None
                fields["display_icon"] = data
    try:
        await role.edit(reason=f"Custom role edited by {member}", **fields)
    except discord.Forbidden:
        return "warn", "I can't edit your role. My highest role has to be above it.", None
    except discord.HTTPException as e:
        log.info("Could not edit a custom role: %s", e)
        return "warn", "Discord refused that change (an animated emoji, or one Discord can't use as an icon, is refused).", None
    mine = auras.items(guild.id, member.id)
    mine["role_look"], mine["role_color"] = look, look["c1"]
    if "name" in fields:
        mine["role_name"] = fields["name"]
    auras.save()
    return "ok", f"updated {role.mention} ({shopstyle.describe(look)}).", role


async def restore_role_look(guild, role, held):
    """A custom role that was made again (someone deleted it) gets its saved style and icon back. Best effort: never fails the caller."""
    saved = held.get("role_look") if isinstance(held, dict) else None
    if not isinstance(saved, dict):
        return
    look = shopstyle.clean_look(saved)
    fields = {}
    if shopstyle.look_problem(look, guild.features) is None:
        fields.update(role_color_fields(look))
    if look["uemoji"] and "ROLE_ICONS" in guild.features:
        fields["display_icon"] = look["uemoji"]
    elif look["emoji"] and "ROLE_ICONS" in guild.features:
        data = await emoji_icon_bytes(guild, look["emoji"])
        if data is not None:
            fields["display_icon"] = data
    if fields:
        try:
            await role.edit(reason="Custom role made again", **fields)
        except discord.HTTPException:
            log.info("Could not restore the look of a custom role", exc_info=True)


async def ensure_my_role(guild, member):
    """The member's custom role, made again (free) if someone deleted it and handed back if it was taken off them.
    Returns (role, None), or (None, why not). (None, "none") means they never bought one."""
    held = auras.items_of(guild.id, member.id)
    try:
        rid = int(held.get("role") or 0)
    except (TypeError, ValueError):
        rid = 0
    if not rid:
        return None, "none"
    role = guild.get_role(rid)
    if role is None:
        name, problem = role_name_problem(guild, held.get("role_name") or "")
        if name is None:
            name = "Custom role"
        try:
            color = int(held.get("role_color", ROLE_DEFAULT_COLOR))
        except (TypeError, ValueError):
            color = ROLE_DEFAULT_COLOR
        role, problem = await make_custom_role(guild, member, name, color if 0 <= color <= 0xFFFFFF else ROLE_DEFAULT_COLOR)
        if role is None:
            return None, problem
        mine = auras.items(guild.id, member.id)
        mine["role"] = role.id
        auras.save()
        await restore_role_look(guild, role, held)
    elif role not in member.roles:
        try:
            await member.add_roles(role, reason="Custom role")
        except discord.HTTPException:
            return None, "I couldn't give you the role back (my role has to be above it)."
    return role, None


shop_busy = set()  # (server, member) with a purchase that is still being set up, so one press can't buy twice


async def buy_custom_role(guild, member):
    """Returns (ok, text). The role is made FIRST and only paid for once it exists, so nobody pays for a role that failed."""
    gid, uid = guild.id, member.id
    key = (gid, uid)
    if key in shop_busy:
        return False, "Your purchase is still going through, one moment."
    held = auras.items_of(gid, uid)
    if held.get("role"):
        role, problem = await ensure_my_role(guild, member)
        if role is not None:
            return False, f"You already have {role.mention}. Change it with `{prefix_cmd('myrole')}`."
        return False, problem or "You already own a custom role."
    if auras.balance(gid, uid) < ROLE_PRICE:
        return False, f"A custom role costs **{ROLE_PRICE:,}** reputation and you have **{auras.balance(gid, uid):,}**."
    shop_busy.add(key)
    try:
        name, _problem = role_name_problem(guild, member.display_name)
        role, problem = await make_custom_role(guild, member, name or "Custom role", ROLE_DEFAULT_COLOR)
        if role is None:
            return False, problem
        if not auras.spend(gid, uid, ROLE_PRICE):  # the balance changed while the role was being made
            try:
                await role.delete(reason="Not paid")
            except discord.HTTPException:
                pass
            return False, "You don't have enough reputation anymore."
        mine = auras.items(gid, uid)
        mine["role"], mine["role_name"], mine["role_color"] = role.id, role.name, ROLE_DEFAULT_COLOR
        mine["role_look"] = shopstyle.clean_look({"style": "solid", "c1": ROLE_DEFAULT_COLOR})
        auras.save()
        return True, f"bought {role.mention}. Open `{prefix_cmd('shop')}`, go to **Customization** and press **Edit Custom Role** to pick its name, colors and icon."
    finally:
        shop_busy.discard(key)


# ---- simple items: Rob Shield, Streak Freeze, Quest Boost ------------------------------------------------------
def buy_item(gid, uid, key):
    """Buy a shield, freeze or boost. Returns (ok, text). There is no await in here, so the check and the payment can't be
    split by another command."""
    now = time.time()
    held = auras.items_of(gid, uid)
    if key == "shield":
        until = max(_num(held, "shield_until"), now)
        if until - now >= SHIELD_MAX_HOURS * 3600 - 60:
            return False, f"Your shield already lasts {SHIELD_MAX_HOURS} hours, the most it can hold."
        price = SHIELD_PRICE
    elif key == "freeze":
        if held_count(held, "freezes") >= FREEZE_MAX:
            return False, f"You already hold {FREEZE_MAX} Streak Freezes, the most you can hold."
        price = FREEZE_PRICE
    elif key == "boost":
        if held_count(held, "boosts") >= BOOST_MAX:
            return False, f"You already hold {BOOST_MAX} Quest Boosts, the most you can hold."
        price = BOOST_PRICE
    elif key == "dailyboost":
        if held_count(held, "dboosts") >= DAILYBOOST_MAX:
            return False, f"You already hold {DAILYBOOST_MAX} Daily Boosts, the most you can hold."
        price = DAILYBOOST_PRICE
    elif key == "reroll":
        if held_count(held, "rerolls") >= REROLL_MAX:
            return False, f"You already hold {REROLL_MAX} Quest Rerolls, the most you can hold."
        price = REROLL_PRICE
    elif key == "lockpick":
        if held_count(held, "lockpicks") >= LOCKPICK_MAX:
            return False, f"You already hold {LOCKPICK_MAX} Lockpicks, the most you can hold."
        price = LOCKPICK_PRICE
    elif key == "vault":
        if held_count(held, "vault") >= 1:
            return False, "You already own a Vault."
        price = VAULT_PRICE
    else:
        return False, "I don't know that item."
    if not auras.spend(gid, uid, price):
        return False, f"That costs **{price:,}** reputation and you have **{auras.balance(gid, uid):,}**."
    mine = auras.items(gid, uid)
    if key == "shield":
        mine["shield_until"] = min(until + SHIELD_HOURS * 3600, now + SHIELD_MAX_HOURS * 3600)
        text = f"bought a **Rob Shield**. Nobody can rob you until <t:{int(mine['shield_until'])}:f>."
    elif key == "freeze":
        mine["freezes"] = held_count(held, "freezes") + 1
        text = f"bought a **Streak Freeze**. You hold **{mine['freezes']}**; one is used when you miss a daily."
    elif key == "lockpick":
        mine["lockpicks"] = held_count(held, "lockpicks") + 1
        text = f"bought a **Lockpick**. You hold **{mine['lockpicks']}**; your next rob has a {round(LOCKPICK_CHANCE * 100)}% chance to work."
    elif key == "vault":
        mine["vault"] = 1
        text = f"bought a **Vault**. Robbers can never touch {VAULT_PERCENT}% of your reputation."
    elif key == "dailyboost":
        mine["dboosts"] = held_count(held, "dboosts") + 1
        text = f"bought a **Daily Boost**. You hold **{mine['dboosts']}**; your next `daily` pays double (up to {DAILYBOOST_CAP:,} extra)."
    elif key == "reroll":
        mine["rerolls"] = held_count(held, "rerolls") + 1
        text = f"bought a **Quest Reroll**. You hold **{mine['rerolls']}**; use `{prefix_cmd('quests')} reroll <number>` to swap an unfinished quest."
    else:
        mine["boosts"] = held_count(held, "boosts") + 1
        text = f"bought a **Quest Boost**. You hold **{mine['boosts']}**; your next quest claim pays double."
    auras.save()
    return True, text + f" You now have **{auras.balance(gid, uid):,}** reputation."


# Luck Boost and Half Loss: key -> (name, the field in the member's items, price of one, games one gives)
BOOST_ITEMS = {
    "luck": ("Luck Boost", "luck_games", LUCK_PRICE, LUCK_GAMES),
    "halfloss": ("Half Loss", "half_games", HALF_PRICE, HALF_GAMES),
    "duelins": ("Duel Insurance", "duel_ins_games", DUELINS_PRICE, DUELINS_DUELS),
}


def buy_boost(gid, uid, key, qty=1):
    """Buy `qty` Luck Boosts or Half Loss at once. Each one adds its games to what is left, so they stack and can be bought
    again at any time, also when the games ran out. Returns (ok, text). No await in here, so the check and the payment can't be split."""
    name, field, price, games = BOOST_ITEMS[key]
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
        return False, "Pick a whole number of 1 or more."
    have = held_count(auras.items_of(gid, uid), field)
    room = (BOOST_GAMES_MAX - have) // games
    if room < 1:
        return False, f"You already have **{have}** games of {name} left, the most you can hold is {BOOST_GAMES_MAX}."
    if qty > room:
        return False, f"You can hold at most {BOOST_GAMES_MAX} games, so you can buy **{room}** more {name} right now."
    cost = price * qty
    if not auras.spend(gid, uid, cost):
        return False, f"{qty} x {name} costs **{cost:,}** reputation and you have **{auras.balance(gid, uid):,}**."
    mine = auras.items(gid, uid)
    mine[field] = have + games * qty
    auras.save()
    return True, (f"bought **{qty} x {name}** (+{games * qty} games). You have **{mine[field]}** games left. "
                  f"You now have **{auras.balance(gid, uid):,}** reputation.")


def shield_until(gid, uid):
    return _num(auras.items_of(gid, uid), "shield_until")


def shop_items():
    """[(key, name, price, short text for the menu, full text)] built when needed so the prefixes are current."""
    return [
        ("shield", "Rob Shield", SHIELD_PRICE, f"Nobody can rob you for {SHIELD_HOURS}h",
         f"Nobody can rob you for {SHIELD_HOURS}h, stacks up to {SHIELD_MAX_HOURS}h. If you rob someone yourself it ends."),
        ("freeze", "Streak Freeze", FREEZE_PRICE, "Saves your daily streak when you miss a day",
         f"Saves your daily streak when you miss a day. Used by itself, hold up to {FREEZE_MAX}."),
        ("boost", "Quest Boost", BOOST_PRICE, "Your next quest claim pays double",
         f"Your next quest claim pays double (up to {BOOST_CAP:,} extra). Hold up to {BOOST_MAX}."),
        ("dailyboost", "Daily Boost", DAILYBOOST_PRICE, "Your next ,daily pays double",
         f"Your next ,daily pays double (up to {DAILYBOOST_CAP:,} extra). It is used by itself when you claim. Hold up to {DAILYBOOST_MAX}."),
        ("reroll", "Quest Reroll", REROLL_PRICE, "Swap a quest you don't like for a new one",
         f"Swap one unfinished quest for a new one (it starts at 0). Use ,quests reroll <number>, the quests are numbered while you hold one. Hold up to {REROLL_MAX}."),
        ("luck", "Luck Boost", LUCK_PRICE, f"{LUCK_GAMES} games with a {round(LUCK_WIN_CHANCE * 100)}% win chance",
         f"For {LUCK_GAMES} games your win chance in coinflip, slots and plinko goes up to {round(LUCK_WIN_CHANCE * 100)}% (not blackjack). It only raises your odds, you can still lose. Bets up to {BOOST_MAX_BET:,} only. Buy as many as you like, they stack (up to {BOOST_GAMES_MAX} games)."),
        ("halfloss", "Half Loss", HALF_PRICE, f"Lose only half your bet for {HALF_GAMES} games",
         f"For {HALF_GAMES} games a lost bet in coinflip, slots and plinko only costs half of it. Bets up to {BOOST_MAX_BET:,} only. Buy as many as you like, they stack (up to {BOOST_GAMES_MAX} games)."),
        ("duelins", "Duel Insurance", DUELINS_PRICE, f"A lost ,duel only costs half for {DUELINS_DUELS} duels",
         f"For {DUELINS_DUELS} duels, if you lose, only half of the bet moves (the winner gets the same half). The other player sees it before they accept. Bets up to {BOOST_MAX_BET:,} only. Buy as many as you like, they stack (up to {BOOST_GAMES_MAX}).",),
        ("lockpick", "Lockpick", LOCKPICK_PRICE, f"Your next rob has a {round(LOCKPICK_CHANCE * 100)}% chance to work",
         f"Your next ,rob works {round(LOCKPICK_CHANCE * 100)}% of the time instead of {round(ROB_CHANCE * 100)}%. It is used up by that rob, win or lose. Hold up to {LOCKPICK_MAX}."),
        ("vault", "Vault", VAULT_PRICE, f"Robbers can't touch {VAULT_PERCENT}% of your reputation",
         f"Bought once, kept for good. {VAULT_PERCENT}% of your reputation is out of reach of ,rob (a robber only takes a share of the rest). Different from Rob Shield, which blocks robbing for a set time and stacks with it."),
        ("role", "Custom Role", ROLE_PRICE, "Your own role, right under Soul",
         "Your own role right under the Soul role. Press Edit to change its name, color style (solid, gradient, holographic) and icon (any emoji)."),
        ("title", "Animated Title", TITLE_PRICE, "Any text with an animated effect on your rep card",
         f"Write any short text, pick an effect, and it shows as an animated name plate on your rep card. Changing it later costs {TITLE_CHANGE_PRICE:,}."),
        ("badge", "Leaderboard Badge", BADGE_PRICE, "An emoji or short tag next to your name",
         f"An emoji or a short tag (up to {BADGE_MAX_CHARS} letters) shown right after your name on the leaderboards. Press Edit to change it later for {BADGE_CHANGE_PRICE:,}."),
    ]


SHOP_ALIASES = {"shield": "shield", "rob": "shield", "freeze": "freeze", "streak": "freeze", "boost": "boost", "quest": "boost",
                "duelins": "duelins", "insurance": "duelins", "duel": "duelins", "lockpick": "lockpick", "pick": "lockpick", "vault": "vault",
                "dailyboost": "dailyboost", "daily": "dailyboost", "dboost": "dailyboost", "reroll": "reroll", "badge": "badge",
                "luck": "luck", "lucky": "luck", "luckboost": "luck", "half": "halfloss", "halfloss": "halfloss", "saver": "halfloss",
                "role": "role", "customrole": "role", "custom": "role", "title": "title"}


def shop_icon():
    """The server's :dcshop: emoji for the store (a shopping bag when the server doesn't have it)."""
    return emo("shop", "\U0001F6CD\uFE0F")


def update_icon():
    """The server's :update: emoji for edit / refresh buttons (a refresh arrows emoji when the server doesn't have it)."""
    return find_emoji("update") or "\U0001F504"


# ---- the shop: one card with categories (switch with the left / right arrows), a Buy / Edit button right next to every item ----
SHOP_CATEGORIES = (
    # (key, name, what the dropdown says under it, the items in it)
    ("upgrades", "Upgrades", "Streak Freeze, Quest Boost, Daily Boost and Quest Reroll", ("freeze", "boost", "dailyboost", "reroll")),
    ("rob", "Rob and protection", "Rob Shield, Lockpick and Vault", ("shield", "lockpick", "vault")),
    ("gambling", "Gambling", "Luck Boost, Half Loss and Duel Insurance", ("luck", "halfloss", "duelins")),
    ("customize", "Customization", "Your own role, animated title and leaderboard badge", ("role", "title", "badge")),
)
SHOP_DEFAULT_CATEGORY = SHOP_CATEGORIES[0][0]


def shop_category(key):
    """The (key, name, blurb, items) of a category; the first one when the key is unknown."""
    return next((c for c in SHOP_CATEGORIES if c[0] == key), SHOP_CATEGORIES[0])


def shop_owned(held):
    """Which one-off items the member already has (their button says Edit instead of Buy)."""
    return {"role": bool(held.get("role")), "title": current_title(held) is not None, "badge": bool(badge_of(held)), "vault": held_count(held, "vault") >= 1}


def shop_held_line(key, held):
    """The small grey line under an item: what the member has of it right now."""
    now = time.time()
    if key == "shield":
        until = _num(held, "shield_until")
        return f"Active until <t:{int(until)}:R>" if until > now else "Not active"
    if key == "freeze":
        return f"You hold {held_count(held, 'freezes')}/{FREEZE_MAX}"
    if key == "boost":
        return f"You hold {held_count(held, 'boosts')}/{BOOST_MAX}"
    if key == "dailyboost":
        return f"You hold {held_count(held, 'dboosts')}/{DAILYBOOST_MAX}"
    if key == "reroll":
        return f"You hold {held_count(held, 'rerolls')}/{REROLL_MAX}"
    if key == "badge":
        return f"Yours: {badge_of(held)}" if badge_of(held) else "You don't have one yet"
    if key == "luck":
        return f"{held_count(held, 'luck_games')} games left"
    if key == "halfloss":
        return f"{held_count(held, 'half_games')} games left"
    if key == "duelins":
        return f"{held_count(held, 'duel_ins_games')} duels left"
    if key == "lockpick":
        return f"You hold {held_count(held, 'lockpicks')}/{LOCKPICK_MAX}"
    if key == "vault":
        return "You own one" if held_count(held, "vault") else "You don't have one yet"
    if key == "role":
        return "You own one" if held.get("role") else "You don't have one yet"
    t = current_title(held)
    return f"Yours: {titlecard.plain_title(t['text'])} ({titlecard.EFFECTS[t['effect']]})" if t else "You don't have one yet"


async def shop_press(interaction, key, view):
    """A Buy / Edit button of the shop. Returns True when something may have changed (so the shop page is drawn again).
    Edit opens a form straight away (a form has to be the first answer to the button)."""
    guild, member = interaction.guild, interaction.user
    if key in BOOST_ITEMS:  # Luck Boost / Half Loss: a small form asks how many (a form has to be the first answer to the button)
        await interaction.response.send_modal(BoostForm(member, key, interaction.message, view.category))
        return False
    if key == "badge":
        await interaction.response.send_modal(BadgeForm(member, interaction.message, view.category))
        return False
    if key in ("shield", "freeze", "boost", "dailyboost", "reroll", "lockpick", "vault"):
        ok, text = buy_item(guild.id, member.id, key)
        await interaction.response.send_message(embed=status_line(member, "ok" if ok else "warn", text), ephemeral=True)
        return True
    if key == "role":
        held = auras.items_of(guild.id, member.id)
        if held.get("role"):  # already yours: the Edit form (the role is made again for free on Save if somebody deleted it)
            await interaction.response.send_modal(RoleForm(member, interaction.message, view.category))
            return False
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, text = await buy_custom_role(guild, member)
        await interaction.followup.send(embed=status_line(member, "ok" if ok else "warn", text), ephemeral=True)
        return True
    await interaction.response.send_modal(TitleForm(member, interaction.message, view.category))  # the title is bought (and changed) in its form
    return False


class ShopView(discord.ui.LayoutView):
    """The shop is ONE card (a Components V2 container, so the buttons sit inside it instead of under it). Top: the title and your
    balance, then a row with arrows to switch category (left / right, wrapping around) and the category name between them.
    Below: every item of that category with its Buy (or Edit) button right next to it. Only the person who opened the shop can press.
    A message made with a LayoutView can't hold embeds or plain content, so everything is drawn by this class."""

    def __init__(self, member, category=SHOP_DEFAULT_CATEGORY):
        super().__init__(timeout=180)
        self.member = member
        self.category = shop_category(category)[0]
        self.message = None
        gid, uid = member.guild.id, member.id
        keys = [c[0] for c in SHOP_CATEGORIES]
        place = keys.index(self.category)
        _key, label, _blurb, item_keys = shop_category(self.category)
        held = auras.items_of(gid, uid)
        owned = shop_owned(held)
        items = {k: (name, price, full) for k, name, price, _short, full in shop_items()}
        card = discord.ui.Container()  # no accent colour = no side line, like every other card
        head = f"{emo('aura')} You have **{auras.balance(gid, uid):,}** reputation".lstrip()
        card.add_item(discord.ui.TextDisplay(f"## {shop_icon()} Reputation shop\n{head}"))
        row = discord.ui.ActionRow()  # [<]  Gambling 2/3  [>]  [refresh]
        back = discord.ui.Button(label="\u25C0", style=discord.ButtonStyle.secondary)
        back.callback = self._step(-1)
        where = discord.ui.Button(label=f"{label} \u00b7 {place + 1}/{len(keys)}", style=discord.ButtonStyle.secondary, disabled=True)  # only a label
        forward = discord.ui.Button(label="\u25B6", style=discord.ButtonStyle.secondary)
        forward.callback = self._step(1)
        refresh = discord.ui.Button(emoji=update_icon(), style=discord.ButtonStyle.secondary)
        refresh.callback = self._refresh
        for button in (back, where, forward, refresh):
            row.add_item(button)
        card.add_item(row)
        card.add_item(discord.ui.Separator())
        for n, key in enumerate(item_keys):
            name, price, full = items[key]
            mine = owned.get(key, False)
            text = f"**{name}** \u00b7 {price:,}" + (" \u00b7 yours" if mine else "") + f"\n{full}\n{small(shop_held_line(key, held))}"
            if key == "vault" and mine:  # nothing to edit on a Vault: the button only says it is yours
                button = discord.ui.Button(label="Owned", style=discord.ButtonStyle.secondary, disabled=True)
            else:
                button = discord.ui.Button(label="Edit" if mine else "Buy", style=discord.ButtonStyle.primary if mine else discord.ButtonStyle.secondary,
                                           emoji=update_icon() if mine else find_emoji("shop"))
            button.callback = self._pressed(key)
            card.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=button))  # the button sits right of its item
            if n < len(item_keys) - 1:
                card.add_item(discord.ui.Separator())
        self.add_item(card)

    def _pressed(self, key):
        async def pressed(interaction):
            if await shop_press(interaction, key, self):
                await self.redraw(interaction)
        return pressed

    def _step(self, direction):
        """The left / right arrow: the previous / next category (the first one comes after the last, and the other way round)."""
        async def step(interaction):
            keys = [c[0] for c in SHOP_CATEGORIES]
            self.category = keys[(keys.index(self.category) + direction) % len(keys)]
            await self.redraw(interaction)
        return step

    async def _refresh(self, interaction):
        await self.redraw(interaction)

    async def redraw(self, interaction):
        """The same page (or the next / previous category) with the current balance and owned items."""
        fresh = ShopView(self.member, self.category)
        fresh.message = self.message
        try:
            if interaction.response.is_done():
                await interaction.message.edit(view=fresh)
            else:
                await interaction.response.edit_message(view=fresh)
        except (discord.HTTPException, AttributeError):
            return
        self.stop()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                embed=status_line(interaction.user, "warn", f"This isn't your menu, use `{prefix_cmd('shop')}` for yours."), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.walk_children():  # buttons inside the card too, not only the top level
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def refresh_shop(message, member, category=SHOP_DEFAULT_CATEGORY):
    """Draw the shop page again after a form changed what the member owns."""
    if message is None:
        return
    fresh = ShopView(member, category)
    fresh.message = message
    try:
        await message.edit(view=fresh)
    except (discord.HTTPException, AttributeError):
        pass


@bot.hybrid_group(name="shop", invoke_without_command=True, description="Spend your reputation", fallback="show")
@commands.guild_only()
async def shop_group(ctx):
    """The reputation shop: Rob Shield, Streak Freeze, Quest Boost, Custom Role and Animated Title."""
    view = ShopView(ctx.author)
    view.message = await ctx.send(view=view)


@shop_group.command(name="buy", description="Buy something from the shop")
@app_commands.describe(item="shield, freeze, boost, dailyboost, reroll, lockpick, vault, luck, halfloss, duelins, role, title or badge", amount="How many (only for luck, halfloss and duelins)")
@app_commands.choices(item=[app_commands.Choice(name=n, value=k) for k, n in (("shield", "Rob Shield"), ("freeze", "Streak Freeze"), ("boost", "Quest Boost"),
                                                                              ("dailyboost", "Daily Boost"), ("reroll", "Quest Reroll"),
                                                                              ("lockpick", "Lockpick"), ("vault", "Vault"),
                                                                              ("luck", "Luck Boost"), ("halfloss", "Half Loss"), ("duelins", "Duel Insurance"),
                                                                              ("role", "Custom Role"), ("title", "Animated Title"), ("badge", "Leaderboard Badge"))])
@commands.guild_only()
@commands.cooldown(1, 3, commands.BucketType.user)
async def shop_buy(ctx, item: str, amount: int = 1):
    key = SHOP_ALIASES.get(item.strip().lower())
    if key is None:
        await ctx.send(embed=status_embed(ctx, "warn", "Pick one of: shield, freeze, boost, dailyboost, reroll, lockpick, vault, luck, halfloss, duelins, role, title, badge."))
        return
    if key in BOOST_ITEMS:
        ok, text = buy_boost(ctx.guild.id, ctx.author.id, key, amount)
        await ctx.send(embed=status_embed(ctx, "ok" if ok else "warn", text))
        return
    if key == "title":
        await ctx.send(embed=status_embed(ctx, "warn", f"Write your title with `{prefix_cmd('title')} set <effect> <text>` (the first one costs {TITLE_PRICE:,})."))
        return
    if key == "badge":
        await ctx.send(embed=status_embed(ctx, "warn", f"Pick your badge with `{prefix_cmd('badge')} <emoji or short text>` (the first one costs {BADGE_PRICE:,}), or press Buy in `{prefix_cmd('shop')}`."))
        return
    if key == "role":
        ok, text = await buy_custom_role(ctx.guild, ctx.author)
    else:
        ok, text = buy_item(ctx.guild.id, ctx.author.id, key)
    await ctx.send(embed=status_embed(ctx, "ok" if ok else "warn", text))


@bot.hybrid_command(name="inventory", aliases=["inv", "items"], description="What you own from the shop")
@commands.guild_only()
async def inventory_cmd(ctx):
    gid, uid = ctx.guild.id, ctx.author.id
    held = auras.items_of(gid, uid)
    now = time.time()
    until = _num(held, "shield_until")
    lines = [f"**Reputation** {auras.balance(gid, uid):,}",
             f"**Rob Shield** {'until <t:%d:R>' % int(until) if until > now else 'none'}",
             f"**Streak Freezes** {held_count(held, 'freezes')}/{FREEZE_MAX}",
             f"**Quest Boosts** {held_count(held, 'boosts')}/{BOOST_MAX}",
             f"**Daily Boosts** {held_count(held, 'dboosts')}/{DAILYBOOST_MAX}",
             f"**Quest Rerolls** {held_count(held, 'rerolls')}/{REROLL_MAX}",
             f"**Luck Boost** {held_count(held, 'luck_games')} games left",
             f"**Half Loss** {held_count(held, 'half_games')} games left",
             f"**Duel Insurance** {held_count(held, 'duel_ins_games')} duels left",
             f"**Lockpicks** {held_count(held, 'lockpicks')}/{LOCKPICK_MAX}",
             f"**Vault** {('yes, ' + str(VAULT_PERCENT) + '% safe from robbers') if held_count(held, 'vault') else 'none'}"]
    role = ctx.guild.get_role(held["role"]) if isinstance(held.get("role"), int) else None
    lines.append(f"**Custom role** {role.mention if role else ('none' if not held.get('role') else '`will be made again with ' + prefix_cmd('myrole') + '`')}")
    t = current_title(held)
    lines.append(f"**Title** {titlecard.plain_title(t['text']) + ' (' + titlecard.EFFECTS[t['effect']] + ')' if t else 'none'}")
    lines.append(f"**Badge** {badge_of(held) or 'none'}")
    live, _expired = shopkit.split_bounty(auras.data.get(str(gid), {}).get(str(uid), {}).get("bounty"), now, BOUNTY_DAYS * 86400)
    if live:
        lines.append(f"**Bounty on you** {shopkit.bounty_total(live):,}")
    embed = discord.Embed(title="Inventory", description="\n".join(lines), color=theme())
    with_author(embed, ctx.author)
    await ctx.send(embed=embed)


# ---- animated titles ------------------------------------------------------------------------------------------
def current_title(held):
    """The title saved in an items record as {"text", "effect", "color"}, or None. Anything odd in the record counts as no title."""
    t = held.get("title") if isinstance(held, dict) else None
    if not isinstance(t, dict):
        return None
    text, effect, color = t.get("text"), t.get("effect"), t.get("color")
    if not isinstance(text, str) or not text.strip() or len(text) > titlecard.MAX_CHARS or effect not in titlecard.EFFECTS:
        return None
    if effect not in titlecard.USES_COLOR:
        color = None
    elif isinstance(color, bool) or not isinstance(color, int) or not 0 <= color <= 0xFFFFFF:
        color = titlecard.DEFAULT_COLOR.get(effect)
    return {"text": text.strip(), "effect": effect, "color": color}


def title_path(t):
    return os.path.join(TITLE_DIR, titlecard.title_key(t["text"], t["effect"], t["color"]) + ".gif")


_title_lock = None


async def ensure_title_file(t):
    """The path of the title's GIF, drawn now (one at a time, in a worker thread) when it isn't on disk yet. None without Pillow
    or when drawing failed: the callers then show the title as plain text."""
    global _title_lock
    if not titlecard.available():
        return None
    path = title_path(t)
    if os.path.isfile(path):
        return path
    if _title_lock is None:
        _title_lock = asyncio.Lock()
    async with _title_lock:
        if not os.path.isfile(path):
            try:
                await asyncio.to_thread(titlecard.render_title, t["text"], t["effect"], t["color"], path)
            except Exception:
                log.warning("Could not draw the title %r", t["text"], exc_info=True)
                return None
    return path if os.path.isfile(path) else None


async def title_file(t):
    path = await ensure_title_file(t)
    return discord.File(path, filename="title.gif") if path else None


def prune_title_file(t):
    """Delete a title's picture when nobody uses it any more."""
    path = title_path(t)
    for users in auras.data.values():
        for rec in users.values():
            other = current_title(rec.get("items") if isinstance(rec, dict) else None)
            if other is not None and title_path(other) == path:
                return
    try:
        os.remove(path)
    except OSError:
        pass


title_busy = set()
title_changed_at = {}


async def apply_title(guild, member, text, effect, color_text=None):
    """Set or change a member's title. Returns (kind, text for the embed, discord.File or None). The picture is drawn BEFORE
    paying, so nobody pays for a title that couldn't be made."""
    gid, uid = guild.id, member.id
    if (gid, uid) in title_busy:
        return "warn", "Your title is being drawn, one moment.", None
    wait = title_changed_at.get((gid, uid), 0) + TITLE_COOLDOWN - time.time()
    if wait > 0:
        return "cooldown", f"Wait {human_time(wait)} before you change your title again.", None
    effect = (effect or "rainbow").strip().lower()
    if effect not in titlecard.EFFECTS:
        return "warn", "Pick an effect: " + ", ".join(titlecard.EFFECTS) + ".", None
    cleaned, problem = titlecard.clean_title(text)
    if cleaned is None:
        return "warn", problem, None
    if store.match(gid, cleaned):
        return "warn", "That text isn't allowed in this server.", None
    cur = current_title(auras.items_of(gid, uid))
    color = None
    if effect in titlecard.USES_COLOR:
        asked = (color_text or "").strip()
        color = titlecard.parse_color(asked, None) if asked else None
        if asked and color is None:
            return "warn", "I don't know that color. Use a hex like `#ff00aa` or a word like pink.", None
        if color is None:
            color = cur["color"] if cur and cur["effect"] == effect and cur["color"] is not None else titlecard.DEFAULT_COLOR.get(effect)
    new = {"text": cleaned, "effect": effect, "color": color}
    if cur == new:
        return "warn", "That is already your title.", None
    price = TITLE_CHANGE_PRICE if cur else TITLE_PRICE
    if auras.balance(gid, uid) < price:
        return "warn", f"{'Changing your title' if cur else 'A title'} costs **{price:,}** reputation and you have **{auras.balance(gid, uid):,}**.", None
    title_busy.add((gid, uid))
    try:
        await ensure_title_file(new)
        if not auras.spend(gid, uid, price):
            return "warn", "You don't have enough reputation anymore.", None
        auras.items(gid, uid)["title"] = new
        auras.save()
        title_changed_at[(gid, uid)] = time.time()
    finally:
        title_busy.discard((gid, uid))
    if cur is not None and title_path(cur) != title_path(new):
        prune_title_file(cur)
    return "ok", f"your title is now **{discord.utils.escape_markdown(cleaned)}** ({titlecard.EFFECTS[effect]}). You now have **{auras.balance(gid, uid):,}** reputation.", await title_file(new)


def title_embed(user, kind, text, file):
    """An embed with a status line and (when there is one) the animated title under it."""
    embed = discord.Embed(description=status_text(kind, f"{user.mention}: {text}"), color=STATUS_STYLES[kind][1])
    if file is not None:
        embed.set_image(url="attachment://title.gif")
    return embed


async def send_title(ctx, kind, text, file):
    embed = title_embed(ctx.author, kind, text, file)
    if file is not None:
        await ctx.send(embed=embed, file=file)
    else:
        await ctx.send(embed=embed)


@bot.hybrid_group(name="title", invoke_without_command=True, description="Your animated title", fallback="show")
@app_commands.describe(member="Whose title to show (yours by default)")
@commands.guild_only()
async def title_group(ctx, member: discord.Member = None):
    """Show a title, or how to get one."""
    member = member or ctx.author
    t = current_title(auras.items_of(ctx.guild.id, member.id))
    if t is None:
        if member.id == ctx.author.id:
            text = (f"You don't have a title yet. `{prefix_cmd('title')} set <effect> <text>` makes one (**{TITLE_PRICE:,}** reputation). "
                    f"Effects: {', '.join(titlecard.EFFECTS)}.")
        else:
            text = f"{member.mention} doesn't have a title."
        await ctx.send(embed=status_embed(ctx, "warn", text))
        return
    file = await title_file(t)
    shown = f"{member.mention}'s title: **{discord.utils.escape_markdown(t['text'])}** ({titlecard.EFFECTS[t['effect']]})"
    embed = discord.Embed(description=shown, color=theme())
    with_author(embed, ctx.author)
    if file is not None:
        embed.set_image(url="attachment://title.gif")
        await ctx.send(embed=embed, file=file)
    else:
        await ctx.send(embed=embed)


@title_group.command(name="set", description="Write your title and pick its effect")
@app_commands.describe(effect="rainbow, flow, neon, glitch, gold or fire", text="Up to 20 characters, anything you like")
@app_commands.choices(effect=[app_commands.Choice(name=n, value=k) for k, n in titlecard.EFFECTS.items()])
@commands.guild_only()
async def title_set(ctx, effect: str, *, text: str = None):
    """`,title set neon Menace`. Leave the effect out and it is rainbow: `,title set Menace`."""
    if effect.lower() not in titlecard.EFFECTS:  # no effect given: the first word is part of the text
        text = f"{effect} {text or ''}".strip()
        effect = "rainbow"
    async with ctx.typing():
        kind, message, file = await apply_title(ctx.guild, ctx.author, text, effect)
    await send_title(ctx, kind, message, file)


@title_group.command(name="color", description="Change the color of a flow or neon title")
@app_commands.describe(color="A hex like #ff00aa, or a word like pink")
@commands.guild_only()
async def title_color(ctx, color: str):
    t = current_title(auras.items_of(ctx.guild.id, ctx.author.id))
    if t is None:
        await ctx.send(embed=status_embed(ctx, "warn", "You don't have a title yet."))
        return
    if t["effect"] not in titlecard.USES_COLOR:
        await ctx.send(embed=status_embed(ctx, "warn", f"The {titlecard.EFFECTS[t['effect']]} effect has its own colors. Only flow and neon use a color you pick."))
        return
    async with ctx.typing():
        kind, message, file = await apply_title(ctx.guild, ctx.author, t["text"], t["effect"], color)
    await send_title(ctx, kind, message, file)


def remove_my_title(guild, member):
    """Returns (kind, text). No refund."""
    t = current_title(auras.items_of(guild.id, member.id))
    if t is None:
        return "warn", "You don't have a title."
    auras.items(guild.id, member.id).pop("title", None)
    auras.save()
    prune_title_file(t)
    return "ok", "removed your title."


@title_group.command(name="remove", description="Remove your title (no refund)")
@commands.guild_only()
async def title_remove(ctx):
    kind, text = remove_my_title(ctx.guild, ctx.author)
    await ctx.send(embed=status_embed(ctx, kind, text))


@title_group.command(name="reset", description="Staff: take someone's title away")
@app_commands.describe(member="Whose title to remove")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def title_reset(ctx, member: discord.Member):
    t = current_title(auras.items_of(ctx.guild.id, member.id))
    if t is None:
        await ctx.send(embed=status_embed(ctx, "warn", f"{member.mention} doesn't have a title."))
        return
    auras.items(ctx.guild.id, member.id).pop("title", None)
    auras.save()
    prune_title_file(t)
    await ctx.send(embed=status_embed(ctx, "ok", f"removed the title of {member.mention} (no refund)."))


BADGE_EMOJI = re.compile(r"^<a?:\w{2,32}:\d{15,25}>$")
BADGE_BANNED = set("<>@#`*_~|\\[]()")


def badge_of(held):
    """The leaderboard badge saved in an items record, or '' (anything odd in the record counts as no badge)."""
    b = held.get("badge") if isinstance(held, dict) else None
    return b.strip() if isinstance(b, str) and 0 < len(b.strip()) <= 60 else ""


def clean_badge(gid, text):
    """(badge, problem): a Unicode emoji, a custom emoji of a server the bot is in, or up to BADGE_MAX_CHARS plain letters."""
    text = (text or "").strip()
    if not text or "\n" in text:
        return None, "Write an emoji or a short tag on one line."
    if BADGE_EMOJI.match(text):
        match = re.search(r":(\d+)>", text)
        if bot.get_emoji(int(match.group(1))) is None:
            return None, "I can't use that emoji, it has to be from a server I'm in."
        return text, None
    if len(text) > BADGE_MAX_CHARS or any(c in BADGE_BANNED or ord(c) < 32 for c in text) or "http" in text.lower():
        return None, f"A badge is an emoji or up to {BADGE_MAX_CHARS} plain letters (no @, #, links or markdown)."
    if store.match(gid, text):
        return None, "That text isn't allowed in this server."
    return text, None


def buy_badge(gid, uid, text):
    """Buy (or change) the leaderboard badge. Returns (ok, text). No await in here, so the check and the payment can't be split."""
    cleaned, problem = clean_badge(gid, text)
    if cleaned is None:
        return False, problem
    cur = badge_of(auras.items_of(gid, uid))
    if cur == cleaned:
        return False, "That is already your badge."
    price = BADGE_CHANGE_PRICE if cur else BADGE_PRICE
    if not auras.spend(gid, uid, price):
        return False, f"{'Changing your badge' if cur else 'A badge'} costs **{price:,}** reputation and you have **{auras.balance(gid, uid):,}**."
    auras.items(gid, uid)["badge"] = cleaned
    auras.save()
    return True, f"your leaderboard badge is now {cleaned}. You now have **{auras.balance(gid, uid):,}** reputation."


def title_tag(gid, uid):
    """' badge 『text』' after a name in a list (each part only when the person has it), or ''."""
    held = auras.items_of(gid, uid)
    t = current_title(held)
    badge = badge_of(held)
    return (" " + badge if badge else "") + (" " + titlecard.plain_title(t["text"]) if t else "")


@bot.hybrid_command(name="badge", description="Set your leaderboard badge (an emoji or a short tag)")
@app_commands.describe(text="An emoji or a short tag")
@commands.guild_only()
@commands.cooldown(1, 5, commands.BucketType.user)
async def badge_cmd(ctx, *, text: str):
    ok, msg = buy_badge(ctx.guild.id, ctx.author.id, text)
    await ctx.send(embed=status_embed(ctx, "ok" if ok else "warn", msg))


# ---- custom role: the member edits it like a booster role ---------------------------------------------------------
async def my_role_or_say(ctx):
    """The caller's custom role (made again when it was deleted), or None after saying why not."""
    role, problem = await ensure_my_role(ctx.guild, ctx.author)
    if role is None:
        if problem == "none":
            problem = f"You don't have a custom role. Buy one in `{prefix_cmd('shop')}` (**{ROLE_PRICE:,}** reputation)."
        await ctx.send(embed=status_embed(ctx, "warn", problem))
    return role


@bot.hybrid_group(name="myrole", invoke_without_command=True, description="Your custom role", fallback="show")
@commands.guild_only()
async def myrole_group(ctx):
    """Show your custom role and how to change it."""
    role = await my_role_or_say(ctx)
    if role is None:
        return
    p = prefix_cmd("myrole")
    lines = [f"{role.mention}", f"Color `#{role.colour.value:06x}`" if role.colour.value else "Color `default`",
             small(f"{p} name <text> · {p} color <hex> · {p} icon <emoji or picture> · {p} delete")]
    embed = discord.Embed(description="\n".join(lines), color=role.colour if role.colour.value else theme())
    with_author(embed, ctx.author)
    await ctx.send(embed=embed)


async def edit_my_role(ctx, **fields):
    role = await my_role_or_say(ctx)
    if role is None:
        return None
    try:
        await role.edit(reason=f"Custom role edited by {ctx.author}", **fields)
    except discord.Forbidden:
        await ctx.send(embed=status_embed(ctx, "warn", "I can't edit your role. My highest role has to be above it."))
        return None
    except discord.HTTPException as e:
        log.info("Could not edit a custom role: %s", e)
        await ctx.send(embed=status_embed(ctx, "warn", "Discord refused that change."))
        return None
    return role


async def edit_role_core(guild, member, name=None, color_text=None):
    """Rename and / or recolor the member's custom role (a blank value is left alone). Returns (kind, text).
    The commands and the shop form both use it, so they follow the same rules."""
    role, problem = await ensure_my_role(guild, member)
    if role is None:
        if problem == "none":
            problem = f"You don't have a custom role. Buy one in `{prefix_cmd('shop')}` (**{ROLE_PRICE:,}** reputation)."
        return "warn", problem
    fields, saved, done = {}, {}, []
    if name is not None and name.strip():
        clean, why = role_name_problem(guild, name, role.id)
        if clean is None:
            return "warn", why
        fields["name"], saved["role_name"] = clean, clean
        done.append("renamed")
    if color_text is not None and color_text.strip():
        value = titlecard.parse_color(color_text, None)
        if value is None:
            return "warn", "I don't know that color. Use a hex like `#ff00aa` or a word like pink."
        look = shopstyle.clean_look(auras.items_of(guild.id, member.id).get("role_look") or {"c1": value})
        look.update({"style": "solid", "c1": value, "c2": None})
        fields.update(role_color_fields(look))
        saved["role_color"], saved["role_look"] = value, look
        done.append(f"color `#{value:06x}`")
    if not fields:
        return "warn", "Write a new name or a color."
    try:
        await role.edit(reason=f"Custom role edited by {member}", **fields)
    except discord.Forbidden:
        return "warn", "I can't edit your role. My highest role has to be above it."
    except discord.HTTPException as e:
        log.info("Could not edit a custom role: %s", e)
        return "warn", "Discord refused that change."
    mine = auras.items(guild.id, member.id)
    mine.update(saved)
    auras.save()
    return "ok", f"updated {role.mention} ({', '.join(done)})."


@myrole_group.command(name="name", description="Rename your custom role")
@app_commands.describe(text=f"The new name (up to {CUSTOM_ROLE_NAME_MAX} characters)")
@commands.guild_only()
@commands.cooldown(1, 10, commands.BucketType.user)
async def myrole_name(ctx, *, text: str):
    kind, message = await edit_role_core(ctx.guild, ctx.author, name=text)
    await ctx.send(embed=status_embed(ctx, kind, message))


@myrole_group.command(name="color", aliases=["colour"], description="Change the color of your custom role")
@app_commands.describe(color="A hex like #ff00aa, or a word like pink")
@commands.guild_only()
@commands.cooldown(1, 10, commands.BucketType.user)
async def myrole_color(ctx, color: str):
    kind, message = await edit_role_core(ctx.guild, ctx.author, color_text=color)
    await ctx.send(embed=status_embed(ctx, kind, message))


@myrole_group.command(name="icon", description="Give your custom role an icon (the server needs Boost level 2)")
@app_commands.describe(image="A small picture (up to 256 KB)", emoji="Or a normal emoji. Write remove to take the icon off")
@commands.guild_only()
@commands.cooldown(1, 10, commands.BucketType.user)
async def myrole_icon(ctx, image: discord.Attachment = None, emoji: str = None):
    if "ROLE_ICONS" not in ctx.guild.features:
        await ctx.send(embed=status_embed(ctx, "warn", "Role icons need this server to reach Boost level 2."))
        return
    if image is not None:
        if image.size > 256 * 1024 or not (image.content_type or "").startswith("image/"):
            await ctx.send(embed=status_embed(ctx, "warn", "Send a picture (png, jpg or gif) of at most 256 KB."))
            return
        icon = await image.read()
    elif emoji and emoji.strip().lower() in ("remove", "none", "off"):
        icon = None
    elif emoji and emoji.strip():
        icon = emoji.strip()
    else:
        await ctx.send(embed=status_embed(ctx, "warn", f"Attach a picture or write an emoji: `{prefix_cmd('myrole')} icon \U0001F525`. `remove` takes it off."))
        return
    role = await edit_my_role(ctx, display_icon=icon)
    if role is not None:
        await ctx.send(embed=status_embed(ctx, "ok", f"updated the icon of {role.mention}."))


async def delete_my_role(guild, member):
    """Delete the member's custom role and forget they own it. Returns (kind, text). No refund."""
    held = auras.items_of(guild.id, member.id)
    if not held.get("role"):
        return "warn", "You don't have a custom role."
    role = guild.get_role(held["role"]) if isinstance(held.get("role"), int) else None
    if role is not None:
        try:
            await role.delete(reason="Deleted by its owner")
        except discord.HTTPException:
            return "warn", "Discord refused to delete the role."
    mine = auras.items(guild.id, member.id)
    for key in ("role", "role_name", "role_color", "role_look"):
        mine.pop(key, None)
    auras.save()
    return "ok", "deleted your custom role."


@myrole_group.command(name="delete", description="Delete your custom role (no refund)")
@commands.guild_only()
async def myrole_delete(ctx):
    if not auras.items_of(ctx.guild.id, ctx.author.id).get("role"):
        await ctx.send(embed=status_embed(ctx, "warn", "You don't have a custom role."))
        return
    view = ConfirmView(ctx.author.id)
    prompt = await ctx.send(embed=status_embed(ctx, "warn", f"Delete your custom role? You don't get your **{ROLE_PRICE:,}** reputation back."), view=view)
    await view.wait()
    try:
        await prompt.edit(view=None)
    except discord.HTTPException:
        pass
    if not view.confirmed:
        return
    kind, text = await delete_my_role(ctx.guild, ctx.author)
    await ctx.send(embed=status_embed(ctx, kind, text))


# ---- the forms opened by the Edit buttons of the shop (a popup with boxes and dropdowns, like the matchmaking forms) ------
role_form_at = {}  # (server, member) -> when they last sent the role form (so it can't be spammed)


def form_label(text, component, description=None):
    """A titled box inside a popup (needs discord.py 2.6+)."""
    return discord.ui.Label(text=text[:45], component=component, description=description[:100] if description else None)


class ShopForm(discord.ui.Modal):
    """Base of the Edit forms: a failure always tells the person instead of 'interaction failed'."""

    async def on_error(self, interaction, error):
        log.warning("A shop form failed", exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("Something went wrong, try again.", ephemeral=True)
            else:
                await interaction.response.send_message("Something went wrong, try again.", ephemeral=True)
        except discord.HTTPException:
            pass


def role_look_of(held, role):
    """The look of a custom role: what the form saved, else read from the real role (or the defaults when the role is gone)."""
    saved = held.get("role_look") if isinstance(held, dict) else None
    if isinstance(saved, dict):
        return shopstyle.clean_look(saved)
    if role is None:
        try:
            color = int(held.get("role_color", ROLE_DEFAULT_COLOR))
        except (TypeError, ValueError, AttributeError):
            color = ROLE_DEFAULT_COLOR
        return shopstyle.clean_look({"style": "solid", "c1": color})
    look = {"style": "solid", "c1": role.colour.value or shopstyle.DEFAULT_C1, "c2": None, "emoji": None, "uemoji": None}
    cols = getattr(role, "colours", None)
    second = getattr(getattr(cols, "secondary", None), "value", None)
    third = getattr(getattr(cols, "tertiary", None), "value", None)
    if second is not None and third is not None:
        look["style"] = "holo"
    elif second is not None:
        look["style"], look["c2"] = "gradient", second
    return shopstyle.clean_look(look)


class RoleForm(ShopForm):
    """Edit button of the Custom Role: name, color style (dropdown), hex codes and ANY emoji as the icon, all in one popup."""

    def __init__(self, member, shop_message=None, category=SHOP_DEFAULT_CATEGORY):
        super().__init__(title="Edit your custom role", timeout=600)
        guild = member.guild
        self.member, self.shop_message, self.category = member, shop_message, category
        held = auras.items_of(guild.id, member.id)
        role = guild.get_role(held.get("role")) if isinstance(held.get("role"), int) else None
        self.look = role_look_of(held, role)
        name = role.name if role is not None else str(held.get("role_name") or "")
        self.old_icon = look_icon_text(guild, self.look)
        self.name_in = discord.ui.TextInput(max_length=CUSTOM_ROLE_NAME_MAX, default=name or None, placeholder="Your role name")
        self.add_item(form_label("Role name", self.name_in))
        self.style = discord.ui.Select(placeholder="Pick a color style", options=[
            discord.SelectOption(label=label, value=key, description=shopstyle.STYLE_HINT[key][:100], default=(key == self.look["style"]))
            for key, label in shopstyle.STYLES.items()])
        self.add_item(form_label("Color style", self.style))
        self.c1 = discord.ui.TextInput(required=False, max_length=12, placeholder="#ff00aa", default=shopstyle.hex_text(self.look["c1"]) if self.look["style"] != "holo" else None)
        self.add_item(form_label("Main color (hex code)", self.c1, "Used by Solid and Gradient"))
        self.c2 = discord.ui.TextInput(required=False, max_length=12, placeholder="#00aaff", default=shopstyle.hex_text(self.look["c2"]) if self.look["c2"] is not None else None)
        self.add_item(form_label("Second color (hex code)", self.c2, "Only used by Gradient"))
        self.icon = discord.ui.TextInput(required=False, max_length=80, placeholder="Any emoji, or its name like :cat:", default=self.old_icon or None)
        self.add_item(form_label("Role icon (any emoji)", self.icon, "Paste any emoji. Empty or 'none' removes it."))

    async def on_submit(self, interaction):
        guild, member = interaction.guild, interaction.user

        async def warn(text):
            await interaction.response.send_message(embed=status_line(member, "warn", text), ephemeral=True)

        look = dict(self.look)
        look["style"] = self.style.values[0] if self.style.values else look["style"]
        for key, field in (("c1", self.c1), ("c2", self.c2)):
            asked = (field.value or "").strip()
            if not asked or asked == "-":
                continue
            value = titlecard.parse_color(asked, None)
            if value is None:
                await warn(f"I don't know the color `{asked[:12]}`. Use a hex code like `#ff00aa`.")
                return
            look[key] = value
        if look["style"] == "gradient" and look["c2"] is None:
            look["c2"] = shopstyle.DEFAULT_C2
        kind, value = shopstyle.parse_icon(self.icon.value, lambda n: find_emoji_id(guild, n))
        if kind == "bad":
            await warn("I don't know that emoji. Paste an emoji, or type the name of one I can see, like `:cat:`.")
            return
        if kind == "keep" and self.old_icon:  # the box was emptied
            kind = "remove"
        change_icon = False
        if kind == "remove":
            change_icon = bool(self.old_icon)
            look["emoji"], look["uemoji"] = None, None
        elif kind == "custom":
            change_icon = value != self.look["emoji"]
            look["emoji"], look["uemoji"] = value, None
        elif kind == "unicode":
            change_icon = value != self.look["uemoji"]
            look["emoji"], look["uemoji"] = None, value
        key = (guild.id, member.id)
        wait = role_form_at.get(key, 0) + 10 - time.time()
        if wait > 0:
            await interaction.response.send_message(embed=status_line(member, "cooldown", f"Wait {human_time(wait)} before you save again."), ephemeral=True)
            return
        role_form_at[key] = time.time()
        await interaction.response.defer(ephemeral=True, thinking=True)
        result, text, _role = await apply_role_look(guild, member, self.name_in.value, look, change_icon)
        await interaction.followup.send(embed=status_line(member, result, text), ephemeral=True)
        if result == "ok":
            await refresh_shop(self.shop_message, member, self.category)


class BoostForm(ShopForm):
    """Buy button of the Luck Boost and Half Loss: asks how many to buy (each one adds its games, they stack)."""

    def __init__(self, member, key, shop_message=None, category=SHOP_DEFAULT_CATEGORY):
        name, _field, price, games = BOOST_ITEMS[key]
        super().__init__(title=f"Buy {name}"[:45], timeout=600)
        self.member, self.key, self.shop_message, self.category = member, key, shop_message, category
        self.amount = discord.ui.TextInput(max_length=4, default="1", placeholder="1")
        self.add_item(form_label("How many?", self.amount, f"{price:,} reputation each, {games} games each"))

    async def on_submit(self, interaction):
        guild, member = interaction.guild, interaction.user
        text = (self.amount.value or "").strip().replace(",", "")
        if not text.isdigit() or int(text) < 1:
            await interaction.response.send_message(embed=status_line(member, "warn", "Write a whole number of 1 or more."), ephemeral=True)
            return
        ok, msg = buy_boost(guild.id, member.id, self.key, int(text))
        await interaction.response.send_message(embed=status_line(member, "ok" if ok else "warn", msg), ephemeral=True)
        if ok:
            await refresh_shop(self.shop_message, member, self.category)


class BadgeForm(ShopForm):
    """Buy / Edit button of the Leaderboard Badge: one box for the emoji or short tag. Pressing Submit is the purchase."""

    def __init__(self, member, shop_message=None, category=SHOP_DEFAULT_CATEGORY):
        cur = badge_of(auras.items_of(member.guild.id, member.id))
        super().__init__(title="Edit your badge" if cur else "Buy your leaderboard badge", timeout=600)
        self.member, self.shop_message, self.category = member, shop_message, category
        price = BADGE_CHANGE_PRICE if cur else BADGE_PRICE
        self.text = discord.ui.TextInput(max_length=60, default=cur or None, placeholder="An emoji or a short tag")
        self.add_item(form_label("Badge", self.text, f"{'Changing it' if cur else 'Buying it'} costs {price:,} reputation"))

    async def on_submit(self, interaction):
        member = interaction.user
        ok, msg = buy_badge(interaction.guild.id, member.id, self.text.value)
        await interaction.response.send_message(embed=status_line(member, "ok" if ok else "warn", msg), ephemeral=True)
        if ok:
            await refresh_shop(self.shop_message, member, self.category)


class TitleForm(ShopForm):
    """Buy / Edit button of the Animated Title: name, style (dropdown) and the color for flow / neon, all in one popup.
    Pressing Submit is the purchase (the first title, or changing it)."""

    def __init__(self, member, shop_message=None, category=SHOP_DEFAULT_CATEGORY):
        current = current_title(auras.items_of(member.guild.id, member.id))
        super().__init__(title="Edit your title" if current else "Buy your animated title", timeout=600)
        self.member, self.shop_message, self.category = member, shop_message, category
        price = TITLE_CHANGE_PRICE if current else TITLE_PRICE
        self.text = discord.ui.TextInput(max_length=titlecard.MAX_CHARS, default=current["text"] if current else None, placeholder="Anything you like")
        self.add_item(form_label("Title name", self.text, f"{'Changing it' if current else 'Buying it'} costs {price:,} reputation"))
        effect = current["effect"] if current else "rainbow"
        self.effect = discord.ui.Select(placeholder="Pick a style", options=[
            discord.SelectOption(label=label, value=key, default=(key == effect)) for key, label in titlecard.EFFECTS.items()])
        self.add_item(form_label("Title style", self.effect))
        self.color = discord.ui.TextInput(required=False, max_length=12, placeholder="#ff00aa",
                                          default=f"#{current['color']:06x}" if current and current["color"] is not None else None)
        self.add_item(form_label("Color (hex code)", self.color, "Only the Flow and Neon styles use a color"))

    async def on_submit(self, interaction):
        guild, member = interaction.guild, interaction.user
        await interaction.response.defer(ephemeral=True, thinking=True)
        effect = self.effect.values[0] if self.effect.values else "rainbow"
        kind, text, file = await apply_title(guild, member, self.text.value, effect, self.color.value)
        embed = title_embed(member, kind, text, file)
        if file is not None:
            await interaction.followup.send(embed=embed, file=file, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)
        if kind == "ok":
            await refresh_shop(self.shop_message, member, self.category)


# ---- bounties ---------------------------------------------------------------------------------------------------
bounty_last = {}  # (server, member) -> when they last put a bounty up


def settle_bounty(gid, uid):
    """The bounties still running on this person. Ones that ran out are paid back to whoever put them there (the fee isn't)."""
    rec = auras.data.get(str(gid), {}).get(str(uid))
    if not isinstance(rec, dict) or "bounty" not in rec:
        return []
    live, expired = shopkit.split_bounty(rec.get("bounty"), time.time(), BOUNTY_DAYS * 86400)
    for who, amount, _at in expired:
        auras.credit(gid, who, amount)
    if expired or len(live) != len(rec.get("bounty") or []):
        rec["bounty"] = live
        auras.save()
    return live


def claim_bounty(gid, target_id, hunter_id):
    """A successful rob: pay the hunter every bounty on the target except ones the hunter put there. Returns the amount."""
    live = settle_bounty(gid, target_id)
    paid = [e for e in live if e[0] != hunter_id]
    total = shopkit.bounty_total(paid)
    if total > 0:
        auras.data[str(gid)][str(target_id)]["bounty"] = [e for e in live if e[0] == hunter_id]
        auras.add(gid, hunter_id, total)
    return total


def sweep_bounties():
    """Give back every bounty that ran out, in every server (also the ones nobody looked at)."""
    for gid, users in list(auras.data.items()):
        for uid, rec in list(users.items()):
            if isinstance(rec, dict) and rec.get("bounty"):
                settle_bounty(gid, uid)


def bounty_board_embed(guild, viewer):
    rows = []
    for uid in list(auras.data.get(str(guild.id), {})):
        member = guild.get_member(int(uid))
        live = settle_bounty(guild.id, uid) if member is not None else []
        if live:
            rows.append((shopkit.bounty_total(live), member, len(live)))
    rows.sort(key=lambda r: (-r[0], r[1].id))
    lines = [f"`{n}` **{discord.utils.escape_markdown(plain_name(guild, m))}** - {total:,}" for n, (total, m, _c) in enumerate(rows[:BOARD_SIZE], start=1)]
    if not lines:
        lines.append(f"Nobody has a bounty. `{prefix_cmd('bounty')} @member <amount>` puts one on them.")
    embed = discord.Embed(title="Bounties", description="\n".join(lines), color=theme())
    with_author(embed, viewer)
    embed.set_footer(text=f"Rob a member with a bounty and you collect it. Unclaimed bounties go back after {BOUNTY_DAYS} days.")
    return embed


@bot.hybrid_group(name="bounty", invoke_without_command=True, description="Put reputation on someone's head", fallback="place")
@app_commands.describe(member="Who the bounty is on", amount="How much (50, 2k, half...). The fee is paid on top")
@commands.guild_only()
async def bounty_group(ctx, member: discord.Member = None, amount: str = None):
    """`,bounty` lists them, `,bounty @member` shows one, `,bounty @member 500` puts one up. Whoever robs that member successfully collects it."""
    gid, uid = ctx.guild.id, ctx.author.id
    if member is None:
        await ctx.send(embed=bounty_board_embed(ctx.guild, ctx.author))
        return
    live = settle_bounty(gid, member.id)
    if amount is None:
        text = (f"{member.mention} has **{shopkit.bounty_total(live):,}** on their head from {len(live)} bount{'y' if len(live) == 1 else 'ies'}."
                if live else f"{member.mention} has no bounty.")
        await ctx.send(embed=status_embed(ctx, "ok" if live else "warn", text))
        return
    if member.bot or member.id == uid:
        await ctx.send(embed=status_embed(ctx, "warn", "Pick another member."))
        return
    wait = bounty_last.get((gid, uid), 0) + 20 - time.time()
    if wait > 0:
        await ctx.send(embed=status_embed(ctx, "cooldown", f"Wait {human_time(wait)} before you put up another bounty."))
        return
    balance = auras.balance(gid, uid)
    value = parse_amount(amount, balance * 100 // (100 + BOUNTY_FEE_PERCENT))
    if value is None or value < BOUNTY_MIN:
        await ctx.send(embed=status_embed(ctx, "warn", f"A bounty is at least **{BOUNTY_MIN:,}** reputation. Write a whole number like `500` (`2k`, `half` and `all` work too)."))
        return
    fee = shopkit.bounty_fee(value, BOUNTY_FEE_PERCENT)
    if len(live) >= BOUNTY_MAX_ENTRIES:
        await ctx.send(embed=status_embed(ctx, "warn", f"{member.mention} already has {BOUNTY_MAX_ENTRIES} bounties, wait until some are collected."))
        return
    # no await from here to the end: the payment and the bounty are one step
    if not auras.spend(gid, uid, value + fee):
        await ctx.send(embed=status_embed(ctx, "warn", f"That costs **{value + fee:,}** ({value:,} + {fee:,} fee) and you have **{balance:,}**."))
        return
    bounty_last[(gid, uid)] = time.time()
    rec = auras.user(gid, member.id)
    rec["bounty"] = live + [[uid, value, time.time()]]
    auras.save()
    total = shopkit.bounty_total(rec["bounty"])
    await ctx.send(embed=status_embed(ctx, "ok", f"put **{value:,}** on {member.mention}'s head (fee **{fee:,}**). Their bounty is now **{total:,}**. "
                                                f"Rob them successfully to collect it."))


# ---- keeping the bot's roles where they belong (Staff / Soul) ------------------------------------------------
async def place_custom_roles(guild, fresh=False):
    """Keep every custom role right under the Soul role. Returns a list of problems."""
    anchor = soul_anchor(guild)
    ids = custom_role_ids(guild)
    if anchor is None or not ids:
        return []
    for rid in sorted(ids):
        role = guild.get_role(rid)
        if role is None:
            continue
        problem = await roleplace.place_under(guild, role, anchor, ids, "Custom roles sit right under the Soul role", fresh)
        fresh = False
        note_problem(guild.id, "custom roles", problem)
        if problem:
            return [f"The custom roles aren't under {anchor.name}: {problem}."]
    return []


async def check_roles(guild, fresh=True):
    """Put the Top 1, Best family, family and custom roles where they belong, using the positions Discord has right now.
    Returns the problems found (an empty list = everything is in place)."""
    problems = []
    for step in (sync_goat_role(guild, fresh), sync_best_role(guild, fresh)):
        problem = await step
        if problem:
            problems.append(problem)
    problems.extend(await sync_all_families(guild, fresh))
    problems.extend(await place_custom_roles(guild, fresh))
    return problems


async def role_check_loop():
    """Every ROLE_CHECK_SECONDS: re-check where the bot's roles sit and hand back expired bounties. One role list request per role per
    server, nothing else, so it costs next to no CPU."""
    await bot.wait_until_ready()
    await asyncio.sleep(90)
    while True:
        try:
            sweep_bounties()
        except Exception:
            log.exception("Could not sweep the bounties")
        for guild in list(bot.guilds):
            try:
                await check_roles(guild, True)
            except Exception:
                log.exception("The role check failed in %s", guild)
            await asyncio.sleep(2)
        await asyncio.sleep(ROLE_CHECK_SECONDS)


def role_state(guild, role_id, anchor, group):
    """A short word for the .roles check: where one role sits compared to its anchor."""
    if anchor is None:
        return "no anchor role"
    role = guild.get_role(role_id) if role_id else None
    if role is None:
        return "not made yet"
    state = roleplace.where(guild.roles, role.id, anchor.id, group)
    return {"ok": "right under " + anchor.name, "above": f"ABOVE {anchor.name}", "below": f"under {anchor.name}, but other roles are in between",
            "missing": "not found"}.get(state, state)


def roles_report(guild):
    staff, soul, me = staff_anchor(guild), soul_anchor(guild), guild.me
    base_id = families.guild(guild.id).get("base_role")
    base = guild.get_role(base_id) if base_id else None
    fam_anchor = family_anchor(guild)
    group = family_group_ids(guild)
    lines = [f"**Staff role** {staff.mention if staff else '`not found`'}",
             f"**Soul role** {soul.mention if soul else '`not found`'}"]
    if base is not None:
        lines.append(f"**Family base role** {base.mention}")
    if me is not None:
        lines.append(f"**My highest role** {me.top_role.mention}")
        for label, anchor in (("Staff", staff), ("Soul", soul), ("base", base)):
            if anchor is not None and anchor >= me.top_role:
                lines.append(warn_line(f"{anchor.mention} is not below my highest role. I can't move roles under it until you drag **my role above it** (Server Settings, Roles)."))
    lines.append("")
    lines.append(f"**Top 1** {role_state(guild, (find_goat_role(guild) or discord.Object(0)).id, staff, group)}")
    lines.append(f"**Best family** {role_state(guild, families.guild(guild.id).get('best_role'), fam_anchor, group)}")
    for fam in families.all(guild.id):
        lines.append(f"**{fam_esc(fam['name'])}** {role_state(guild, fam.get('role'), fam_anchor, group)}")
    ids = custom_role_ids(guild)
    if ids:
        placed = sum(1 for rid in ids if guild.get_role(rid) and role_state(guild, rid, soul, ids).startswith("right under"))
        lines.append(f"**Custom roles** {placed}/{len([r for r in ids if guild.get_role(r)])} right under Soul")
    notes = [text for (gid, _what), text in role_problems.items() if gid == guild.id]
    if notes:
        lines.append("")
        lines.append("**Last problems**")
        lines.extend(f"- {n}" for n in sorted(set(notes)))
    return "\n".join(lines)


@bot.hybrid_group(name="roles", invoke_without_command=True, description="Check where the bot's roles sit (Staff and Soul)", fallback="check")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def roles_group(ctx):
    """Shows the Staff and Soul roles the bot uses and whether the Top 1, family and custom roles sit right under them."""
    embed = discord.Embed(title="Role positions", description=roles_report(ctx.guild), color=theme())
    with_author(embed, ctx.author)
    p = prefix_cmd("roles")
    embed.set_footer(text=f"{p} fix · {p} staff @role · {p} soul @role")
    await ctx.send(embed=embed)


@roles_group.command(name="fix", description="Move every role under Staff / Soul right now")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
@commands.cooldown(1, 30, commands.BucketType.guild)
async def roles_fix(ctx):
    async with ctx.typing():
        problems = await check_roles(ctx.guild, True)
    if problems:
        await ctx.send(embed=status_embed(ctx, "warn", "Not everything could be placed:\n" + "\n".join(f"- {p}" for p in problems[:8])))
    else:
        await ctx.send(embed=status_embed(ctx, "ok", "every role is where it belongs."))


async def _pin_role(ctx, key, label, role):
    guild = ctx.guild
    current = staff_anchor(guild) if key == "staff" else soul_anchor(guild)
    if role is None:
        shown = f"{current.mention}" if current else "none found"
        await ctx.send(embed=status_embed(ctx, "ok" if current else "warn", f"The {label} role is {shown}. Pick another with `{prefix_cmd('roles')} {key} @role`."))
        return
    if role.is_default() or role.managed:
        await ctx.send(embed=status_embed(ctx, "warn", "Pick a normal role (not @everyone, and not a role that belongs to a bot)."))
        return
    shop_settings.set(guild.id, key, role.id)
    extra = ""
    if guild.me is not None and role >= guild.me.top_role:
        extra = " My highest role is **below** it, so drag my role above it first (Server Settings, Roles)."
    if key == "staff":
        schedule_goat_role(guild.id, delay=1)
        schedule_best_role(guild)
        schedule_family_sync(guild)
    else:
        asyncio.create_task(place_custom_roles(guild, True))
    await ctx.send(embed=status_embed(ctx, "ok", f"{role.mention} is now the {label} role.{extra}"))


@roles_group.command(name="staff", description="Pick the Staff role (Top 1, Best family and family roles sit under it)")
@app_commands.describe(role="The Staff role")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def roles_staff(ctx, role: discord.Role = None):
    await _pin_role(ctx, "staff", "Staff", role)


@roles_group.command(name="soul", description="Pick the Soul role (custom roles sit under it)")
@app_commands.describe(role="The Soul role")
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def roles_soul(ctx, role: discord.Role = None):
    await _pin_role(ctx, "soul", "Soul", role)


@roles_group.command(name="forget", description="Go back to finding the Staff or Soul role by its name")
@app_commands.describe(which="staff or soul")
@app_commands.choices(which=[app_commands.Choice(name="staff", value="staff"), app_commands.Choice(name="soul", value="soul")])
@commands.has_permissions(manage_guild=True)
@commands.guild_only()
async def roles_forget(ctx, which: str):
    which = which.strip().lower()
    if which not in ("staff", "soul"):
        await ctx.send(embed=status_embed(ctx, "warn", "Write staff or soul."))
        return
    shop_settings.set(ctx.guild.id, which, None)
    await ctx.send(embed=status_embed(ctx, "ok", f"the {which.title()} role is found by its name again."))


# --------------------------------------------------------------------------
# .backup / .restore: save reputation + quest progress + smash/pass counts + families (with their banks) + matchmaking + the ,sop list as a code, plus the pictures added with ,sop upload as zip file(s),
# and load it all again on a new host (server owner / bot owner).
# Nothing runs in the background: the work happens only when one of the two commands is used.
# --------------------------------------------------------------------------
BACKUP_HEADER = "REPBK1:"
BACKUP_MAX_BYTES = 8 * 1024 * 1024      # biggest backup file accepted
BACKUP_MAX_UNPACKED = 64 * 1024 * 1024  # stops a tiny file that unpacks into something huge
BACKUP_PART_BYTES = int(float(os.getenv("BACKUP_PART_MB", "9")) * 1024 * 1024)  # pictures per zip file (a Discord DM takes about 10 MB per file)
BACKUP_MAX_ZIP_BYTES = 64 * 1024 * 1024  # biggest picture zip .restore accepts
BACKUP_MAX_ZIP_FILES = 500               # most pictures in one zip
BACKUP_MANIFEST = "manifest.json"        # inside every picture zip: which picture belongs to which name
BACKUP_PICTURE_RE = re.compile(r"^(\d{1,25})/([0-9a-f]{32})(\.jpg|\.jpeg|\.png|\.webp|\.gif)$")  # "<server id>/<random id>.<type>", how ,sop upload names the saved pictures


class BackupError(Exception):
    pass


def backup_sop_links(gid):
    """[{name, url}] of the ,sop list entries of this server that are picture links (small, they go inside the backup code)."""
    return [{"name": e["name"], "url": e["url"]} for e in smashpass.data.get(str(gid), []) if e.get("url") and e.get("name")]


def backup_pack(gids, mm=None):
    """One line of text (compressed) holding the reputation, quest progress, ,sop picture links, families and matchmaking (`mm`, see backup_mm_pack) of these server ids."""
    gids = [str(g) for g in gids]
    payload = {"v": 1, "made": int(time.time()),
               "aura": {g: auras.data[g] for g in gids if g in auras.data},
               "quests": {g: quests.data[g] for g in gids if g in quests.data},
               "sop": {g: links for g in gids for links in [backup_sop_links(g)] if links},
               "sop_stats": {g: sop_stats.data[g] for g in gids if sop_stats.data.get(g)},  # how many times each person smashed / passed
               "families": {g: families.data[g] for g in gids if families.data.get(g, {}).get("families")}}  # families, ranks, banks, war, cooldowns
    if mm:
        payload["mm"] = mm  # matchmaking (older backups don't have this key, older bots ignore it)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")  # copied here, on the loop, so nothing changes under it
    return raw


# ---- matchmaking (matchmaking.py) inside the backup -------------------------------------------------------------------
# What goes in: everything matchmaking.json holds. The BOT OWNER's backup has all of it: every profile (with date of birth), bans, the
# under-age list, blocks, rematch memory, reports (with the chat snapshot they carry), chats in progress, staff, the live-feed channel,
# daily lookups and totals, plus every server's off switch and sticky sign-up panels. A SERVER OWNER's backup only has that server's off
# switch and sticky panels: profiles are global (people from other servers) and must not end up in a server owner's DMs.
# NOT included: matchmaking_log.jsonl, the safety log with the relayed chat text. The bot deletes it after MATCH_LOG_DAYS days on purpose,
# a backup would keep it forever.
MM_GLOBAL_KEYS = ("profiles", "banned", "underage", "blocks", "matches", "recent", "reports", "staff", "feed_channel", "lookups", "next_report", "totals")


def _bk_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _bk_id(x):
    return isinstance(x, str) and x.isascii() and x.isdigit()


def _bk_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def backup_mm_pack(gids, full):
    """The matchmaking part of a backup, or None when matchmaking isn't running or has nothing to save.
    full=True is the bot owner's backup (everything, every server); otherwise only the off switch and sticky panels of `gids`."""
    cog = bot.get_cog("Matchmaking")
    if cog is None:
        return None
    d = cog.store.data
    wanted = {int(g) for g in gids}
    mm = {"guild_off": [g for g in d.get("guild_off", []) if full or g in wanted],
          "panels": {cid: info for cid, info in d.get("panels", {}).items() if full or (isinstance(info, dict) and info.get("guild_id") in wanted)}}
    if full:
        mm["global"] = True
        for key in MM_GLOBAL_KEYS:
            if key in d:
                mm[key] = d[key]
    return {k: v for k, v in mm.items() if v or k == "global"} or None


def backup_clean_mm(mm):
    """The matchmaking part of a backup with every wrong-looking entry left out, so a damaged backup can't put bad data in the store. {} when there is none."""
    if not isinstance(mm, dict):
        return {}

    def part(key, kind):
        value = mm.get(key)
        return value if isinstance(value, kind) else kind()

    out = {"guild_off": [g for g in part("guild_off", list) if _bk_int(g)],
           "panels": {cid: {"message_id": info["message_id"] if _bk_int(info.get("message_id")) else None, "guild_id": info["guild_id"]}
                      for cid, info in part("panels", dict).items() if _bk_id(cid) and isinstance(info, dict) and _bk_int(info.get("guild_id"))}}
    if mm.get("global") is True:  # the bot owner's full backup
        out["global"] = True
        out["profiles"] = {uid: p for uid, p in part("profiles", dict).items()
                           if _bk_id(uid) and isinstance(p, dict) and isinstance(p.get("name") or "", str)
                           and all(p.get(k) is None or _bk_int(p[k]) for k in ("pid", "age"))
                           and (p.get("dob") is None or isinstance(p["dob"], str)) and isinstance(p.get("likers", []), list)}
        out["banned"] = {uid: why for uid, why in part("banned", dict).items() if _bk_id(uid) and isinstance(why, str)}
        out["underage"] = [u for u in part("underage", list) if _bk_id(u)]
        out["staff"] = [u for u in part("staff", list) if _bk_id(u)]
        out["blocks"] = {uid: [b for b in lst if _bk_int(b)] for uid, lst in part("blocks", dict).items() if _bk_id(uid) and isinstance(lst, list)}
        out["matches"] = {uid: {"partner": m["partner"], "since": m["since"] if _bk_num(m.get("since")) else 0}
                          for uid, m in part("matches", dict).items() if _bk_id(uid) and isinstance(m, dict) and _bk_int(m.get("partner"))}
        out["recent"] = {uid: {p: ts for p, ts in row.items() if _bk_id(p) and _bk_num(ts)}
                         for uid, row in part("recent", dict).items() if _bk_id(uid) and isinstance(row, dict)}
        out["lookups"] = {uid: {"day": rec["day"], "used": rec["used"]} for uid, rec in part("lookups", dict).items()
                          if _bk_id(uid) and isinstance(rec, dict) and isinstance(rec.get("day"), str) and _bk_int(rec.get("used")) and rec["used"] >= 0}
        out["reports"] = [{**r, "chat": [c for c in r.get("chat", []) if isinstance(c, dict)] if isinstance(r.get("chat", []), list) else []}
                          for r in part("reports", list)
                          if isinstance(r, dict) and _bk_int(r.get("id")) and _bk_int(r.get("reporter")) and _bk_int(r.get("target"))
                          and r.get("status") in ("open", "reviewed", "dismissed") and isinstance(r.get("reason", ""), str)]
        out["feed_channel"] = mm["feed_channel"] if _bk_int(mm.get("feed_channel")) and mm["feed_channel"] > 0 else 0
        out["next_report"] = mm["next_report"] if _bk_int(mm.get("next_report")) and mm["next_report"] >= 1 else 1
        out["totals"] = {k: v for k, v in part("totals", dict).items() if isinstance(k, str) and _bk_int(v) and v >= 0}
    return out if (out.get("global") or out["guild_off"] or out["panels"]) else {}


def backup_mm_merge(d, mm, allowed):
    """Put the (cleaned) matchmaking part of a backup into the live matchmaking data `d` WITHOUT throwing away what is already there:
    profiles of the backup replace the same person's profile, bans / staff / blocks / the under-age list are added to, reports are added when their id
    isn't there yet, chats in progress only come back when both people are free, the live-feed channel is only set when none is set.
    Server settings (off switch, sticky panels) are only taken for the servers in `allowed` (None = every server); everything else only when
    `allowed` is None (the bot owner) and the backup is a full one. Returns (profiles loaded, other records added)."""
    profiles = other = 0
    off = d.setdefault("guild_off", [])
    for gid in mm.get("guild_off", []):
        if (allowed is None or str(gid) in allowed) and gid not in off:
            off.append(gid)
            other += 1
    panels = d.setdefault("panels", {})
    for cid, info in mm.get("panels", {}).items():
        if (allowed is None or str(info["guild_id"]) in allowed) and cid not in panels:
            panels[cid] = dict(info)
            other += 1
    if allowed is not None or not mm.get("global"):
        return profiles, other
    have = d.setdefault("profiles", {})
    owners = {q.get("pid"): u for u, q in have.items() if isinstance(q, dict) and q.get("pid") is not None}  # friend ids must stay unique
    for uid, p in mm.get("profiles", {}).items():
        if p.get("pid") is not None and owners.get(p["pid"], uid) != uid:  # another person already has this friend id on the new host
            new = random.randint(100000, 999999)
            while new in owners:
                new = random.randint(10 ** 7, 10 ** 8)
            p["pid"] = new
        if p.get("pid") is not None:
            owners[p["pid"]] = uid
        have[uid] = p
        profiles += 1
    banned = d.setdefault("banned", {})
    for uid, why in mm.get("banned", {}).items():
        if uid not in banned:
            banned[uid] = why
            other += 1
    for key in ("underage", "staff"):
        current = d.setdefault(key, [])
        for uid in mm.get(key, []):
            if uid not in current:
                current.append(uid)
                other += 1
    blocks = d.setdefault("blocks", {})
    for uid, lst in mm.get("blocks", {}).items():
        mine = blocks.setdefault(uid, [])
        for partner in lst:
            if partner not in mine:
                mine.append(partner)
                other += 1
    recent = d.setdefault("recent", {})
    for uid, row in mm.get("recent", {}).items():
        mine = recent.setdefault(uid, {})
        for partner, ts in row.items():
            mine[partner] = max(mine.get(partner, 0), ts)
    lookups = d.setdefault("lookups", {})
    for uid, rec in mm.get("lookups", {}).items():
        now = lookups.get(uid)
        if not now or rec["day"] > now.get("day", ""):
            lookups[uid] = dict(rec)
        elif rec["day"] == now.get("day"):
            now["used"] = max(now.get("used", 0), rec["used"])
    reports = d.setdefault("reports", [])
    seen = {r.get("id") for r in reports if isinstance(r, dict)}
    for r in mm.get("reports", []):
        if r["id"] not in seen:
            reports.append(r)
            seen.add(r["id"])
            other += 1
    d["next_report"] = max(d.get("next_report", 1) if _bk_int(d.get("next_report", 1)) else 1, mm.get("next_report", 1), max((i for i in seen if _bk_int(i)), default=0) + 1)
    matches = d.setdefault("matches", {})
    for uid, m in mm.get("matches", {}).items():  # a chat in progress needs both directions and two free people
        partner = m["partner"]
        back = mm["matches"].get(str(partner))
        if back and back["partner"] == int(uid) and uid not in matches and str(partner) not in matches:
            matches[uid] = dict(m)
            matches[str(partner)] = dict(back)
            other += 1
    if not d.get("feed_channel") and mm.get("feed_channel"):
        d["feed_channel"] = mm["feed_channel"]
    totals = d.setdefault("totals", {})
    for key, value in mm.get("totals", {}).items():
        totals[key] = max(totals.get(key, 0) if _bk_int(totals.get(key, 0)) else 0, value)
    return profiles, other


def backup_encode(raw):
    return BACKUP_HEADER + base64.urlsafe_b64encode(zlib.compress(raw, 6)).decode("ascii")


def backup_clean_families(guild_data):
    """One server's families from a backup, with every family that is damaged left out. None when nothing usable is left."""
    if not isinstance(guild_data, dict) or not isinstance(guild_data.get("families"), dict):
        return None
    good = {}
    for fid, fam in guild_data["families"].items():
        if not (isinstance(fid, str) and fid.isdigit() and isinstance(fam, dict) and fam.get("id") == fid):
            continue
        if not (isinstance(fam.get("name"), str) and isinstance(fam.get("tag"), str) and isinstance(fam.get("members"), dict)):
            continue
        members = {}
        for uid, info in fam["members"].items():
            if isinstance(uid, str) and uid.isdigit() and isinstance(info, dict) and info.get("rank") in RANK_LEVEL:
                members[uid] = info
        if not members or sum(1 for i in members.values() if i["rank"] == "owner") != 1:
            continue
        bank = fam.get("bank", 0)
        fam["bank"] = bank if isinstance(bank, int) and not isinstance(bank, bool) and bank >= 0 else 0
        fam["members"] = members
        good[fid] = fam
    if not good:
        return None
    guild_data["families"] = good
    guild_data["next_id"] = max(int(guild_data.get("next_id", 1)) if str(guild_data.get("next_id", 1)).isdigit() else 1,
                                max(int(f) for f in good) + 1)
    return guild_data


def backup_decode(text):
    """The cleaned-up backup {"aura": {server: {user: record}}, "quests": {server: {user: record}}, "sop": {server: [{name, url}]}, ..., "mm": matchmaking part}, or BackupError."""
    text = "".join(str(text).split())
    if not text.startswith(BACKUP_HEADER):
        raise BackupError("That isn't a backup code. It should start with `REPBK1:`.")
    body = text[len(BACKUP_HEADER):]
    try:
        packed = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        unpacker = zlib.decompressobj()
        raw = unpacker.decompress(packed, BACKUP_MAX_UNPACKED)
        if unpacker.unconsumed_tail:
            raise BackupError("That backup is too big.")
        data = json.loads(raw.decode("utf-8"))
    except BackupError:
        raise
    except Exception:
        raise BackupError("That backup code is damaged or cut off. Copy the whole thing again.")
    if not isinstance(data, dict) or data.get("v") != 1:
        raise BackupError("That backup is from a version I don't know.")
    clean = {"aura": {}, "quests": {}, "sop": {}, "sop_stats": {}, "families": {}, "mm": {}}
    for part in ("aura", "quests"):
        servers = data.get(part)
        if not isinstance(servers, dict):
            continue
        for gid, users in servers.items():
            if not (isinstance(gid, str) and gid.isdigit() and isinstance(users, dict)):
                continue
            keep = {}
            for uid, rec in users.items():
                if not (isinstance(uid, str) and uid.isdigit() and isinstance(rec, dict)):
                    continue
                if part == "aura" and (isinstance(rec.get("aura", 0), bool) or not isinstance(rec.get("aura", 0), int)):
                    continue
                keep[uid] = rec
            if keep:
                clean[part][gid] = keep
    servers = data.get("sop_stats")  # older backups have no smash / pass counts
    if isinstance(servers, dict):
        for gid, users in servers.items():
            if not (isinstance(gid, str) and gid.isdigit() and isinstance(users, dict)):
                continue
            keep = {}
            for uid, row in users.items():
                if not (isinstance(uid, str) and uid.isdigit() and isinstance(row, dict)):
                    continue
                smash, passed = row.get("smash", 0), row.get("pass", 0)
                if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (smash, passed)):
                    continue
                keep[uid] = {"smash": smash, "pass": passed}
            if keep:
                clean["sop_stats"][gid] = keep
    servers = data.get("families")  # older backups have no families
    if isinstance(servers, dict):
        for gid, guild_data in servers.items():
            if isinstance(gid, str) and gid.isdigit():
                cleaned = backup_clean_families(guild_data)
                if cleaned is not None:
                    clean["families"][gid] = cleaned
    servers = data.get("sop")  # older backups have no ,sop part
    if isinstance(servers, dict):
        for gid, items in servers.items():
            if not (isinstance(gid, str) and gid.isdigit() and isinstance(items, list)):
                continue
            keep = []
            for it in items[:SMASHPASS_MAX]:
                if not isinstance(it, dict) or not isinstance(it.get("name"), str) or not isinstance(it.get("url"), str):
                    continue
                name = it["name"].replace("`", "").strip()[:60]
                if name and not sop_link_error(it["url"]):
                    keep.append({"name": name, "url": it["url"]})
            if keep:
                clean["sop"][gid] = keep
    clean["mm"] = backup_clean_mm(data.get("mm"))  # older backups have no matchmaking part
    return clean


def backup_sop_files(gids):
    """[(server id, name, file name, path, size)] for every picture added with ,sop upload in these servers that still exists on disk."""
    out = []
    for g in gids:
        g = str(g)
        for e in smashpass.data.get(g, []):
            rel = e.get("file")
            m = BACKUP_PICTURE_RE.match(rel) if isinstance(rel, str) else None
            if m is None or m.group(1) != g:
                continue
            path = os.path.join(SOP_DIR, rel)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue  # the picture went missing: nothing to save
            out.append((g, str(e.get("name") or "Unnamed"), rel, path, size))
    return out


def backup_sop_parts(files):
    """The pictures split into groups that each fit one zip file (BACKUP_PART_BYTES)."""
    parts, current, size = [], [], 0
    for item in files:
        if current and size + item[4] > BACKUP_PART_BYTES:
            parts.append(current)
            current, size = [], 0
        current.append(item)
        size += item[4]
    if current:
        parts.append(current)
    return parts


def backup_build_zip(part):
    """(zip bytes, number of pictures) for one group, or None when none of its pictures could be read any more.
    Inside: the pictures under "<server id>/<id>.<type>" and manifest.json {"v": 1, "sop": {server: [{name, file}]}}."""
    buf = io.BytesIO()
    manifest = {"v": 1, "sop": {}}
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:  # pictures are already compressed, so storing them is faster and just as small
        for gid, name, rel, path, _size in part:
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            z.writestr(rel, data)
            manifest["sop"].setdefault(gid, []).append({"name": name, "file": rel})
            count += 1
        if not count:
            return None
        z.writestr(BACKUP_MANIFEST, json.dumps(manifest, ensure_ascii=False))
    return buf.getvalue(), count


def backup_picture_ok(data):
    """True when these bytes really are a jpg, png, gif or webp picture."""
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or data[:6] in (b"GIF87a", b"GIF89a")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def backup_read_zip(blob, allowed):
    """The pictures inside one picture zip: [{gid, name, file, data}]. Servers not in `allowed` (None = every server) are skipped.
    Nothing is written here. Only files named exactly like the ones ,sop upload makes are read, so nothing in the zip can escape the sop folder."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        raise BackupError("That zip is damaged, or it isn't one of my picture backups.")
    with z:
        infos = {i.filename: i for i in z.infolist()}
        if len(infos) > BACKUP_MAX_ZIP_FILES + 1 or BACKUP_MANIFEST not in infos or infos[BACKUP_MANIFEST].file_size > 4 * 1024 * 1024:
            raise BackupError("That zip isn't one of my picture backups.")
        try:
            manifest = json.loads(z.read(BACKUP_MANIFEST).decode("utf-8"))
        except Exception:
            raise BackupError("The list inside that zip is damaged.")
        sop = manifest.get("sop") if isinstance(manifest, dict) and manifest.get("v") == 1 else None
        if not isinstance(sop, dict):
            raise BackupError("That zip is from a version I don't know.")
        out, total = [], 0
        for gid, items in sop.items():
            if not (isinstance(gid, str) and gid.isdigit() and isinstance(items, list)) or (allowed is not None and gid not in allowed):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                rel, name = it.get("file"), it.get("name")
                m = BACKUP_PICTURE_RE.match(rel) if isinstance(rel, str) else None
                if m is None or m.group(1) != gid or rel not in infos or not isinstance(name, str):
                    continue
                info = infos[rel]
                if info.file_size > SOP_MAX_BYTES:
                    continue
                total += info.file_size
                if total > BACKUP_MAX_UNPACKED:
                    raise BackupError("That zip is too big.")
                try:
                    data = z.read(info)  # never more than the size the zip declares
                except Exception:
                    continue
                if backup_picture_ok(data):
                    out.append({"gid": gid, "name": name.replace("`", "").strip()[:60] or "Unnamed", "file": rel, "data": data})
    return out


def backup_write_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


BANNER_FILE_RE = re.compile(r"^(\d{1,25})_(\d{1,9})\.(jpg|jpeg|png|webp|gif)$")  # how a family banner is named: "<server id>_<family id>.<type>"


def backup_banner_files(gids):
    """[(server id, file name, path, size)] of the family banners of these servers that still exist on disk."""
    out = []
    for g in gids:
        g = str(g)
        for fam in families.data.get(g, {}).get("families", {}).values():
            rel = fam.get("banner")
            m = BANNER_FILE_RE.match(rel) if isinstance(rel, str) else None
            if m is None or m.group(1) != g:
                continue
            path = os.path.join(FAMILY_BANNER_DIR, rel)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            out.append((g, rel, path, size))
    return out


def backup_build_banner_zip(files):
    """(zip bytes, number of banners) with the family banners at the top and manifest.json {"v": 1, "banners": [file names]}."""
    buf = io.BytesIO()
    names = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for _gid, rel, path, _size in files:
            try:
                with open(path, "rb") as f:
                    z.writestr(rel, f.read())
            except OSError:
                continue
            names.append(rel)
        if not names:
            return None
        z.writestr(BACKUP_MANIFEST, json.dumps({"v": 1, "banners": names}))
    return buf.getvalue(), len(names)


def backup_zip_is_banners(blob):
    """True when this zip is a family banner zip (its manifest has a \"banners\" list)."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            info = z.getinfo(BACKUP_MANIFEST)
            if info.file_size > 4 * 1024 * 1024:
                return False
            manifest = json.loads(z.read(info).decode("utf-8"))
        return isinstance(manifest, dict) and isinstance(manifest.get("banners"), list)
    except Exception:
        return False


def backup_zip_is_assist(blob):
    """True when this zip is an assist-history zip (its manifest says \"assist\": true). askai.py reads it."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            info = z.getinfo(BACKUP_MANIFEST)
            if info.file_size > 4 * 1024 * 1024:
                return False
            manifest = json.loads(z.read(info).decode("utf-8"))
        return isinstance(manifest, dict) and manifest.get("assist") is True
    except Exception:
        return False


def backup_read_banner_zip(blob, allowed):
    """[(server id, file name, bytes)] from a family banner zip. Servers not in `allowed` are skipped, and only files named exactly
    like the banners the bot makes are read, so nothing can escape the banner folder."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        raise BackupError("That zip is damaged, or it isn't one of my banner backups.")
    out, total = [], 0
    with z:
        infos = {i.filename: i for i in z.infolist()}
        manifest = json.loads(z.read(BACKUP_MANIFEST).decode("utf-8"))
        for rel in manifest.get("banners", [])[:BACKUP_MAX_ZIP_FILES]:
            m = BANNER_FILE_RE.match(rel) if isinstance(rel, str) else None
            if m is None or rel not in infos or (allowed is not None and m.group(1) not in allowed):
                continue
            info = infos[rel]
            if info.file_size > BACKUP_PART_BYTES:
                continue
            total += info.file_size
            if total > BACKUP_MAX_UNPACKED:
                raise BackupError("That zip is too big.")
            try:
                data = z.read(info)
            except Exception:
                continue
            if backup_picture_ok(data):
                out.append((m.group(1), rel, data))
    return out


BACKUP_BUNDLE_MARKER = "bundle.json"   # inside the one-file backup: {"v": 1, "bundle": true}
BACKUP_BUNDLE_TEXT = "backup.txt"      # inside the one-file backup: the backup code
BACKUP_BUNDLE_MEMBER_RE = re.compile(r"^(backup\.txt|[A-Za-z0-9._-]{1,80}\.zip)$")  # the only names read from a bundle, so nothing in it can escape to another folder
BACKUP_BUNDLE_MAX_MEMBERS = 40


class BackupBundle:
    """Writes the whole backup as ONE zip file on disk (backup.txt + the family banner, picture and assist zips inside it).
    It only starts a second file when the first would be bigger than one Discord upload (`limit` bytes), and then every file is still a
    complete bundle that .restore reads. Files go to `folder`, one piece at a time, so the pictures are never all in memory together."""

    def __init__(self, folder, limit):
        self.folder, self.limit = folder, limit
        self.paths = []
        self._zip = None
        self._size = 0

    def _open(self):
        path = os.path.join(self.folder, f"bundle{len(self.paths) + 1}.zip")
        self._zip = zipfile.ZipFile(path, "w", zipfile.ZIP_STORED)  # everything inside is already compressed
        self._size = 0
        self.paths.append(path)

    def _close(self):
        if self._zip is not None:
            self._zip.writestr(BACKUP_BUNDLE_MARKER, json.dumps({"v": 1, "bundle": True}))
            self._zip.close()
            self._zip = None

    def add(self, name, data):
        if self._zip is not None and self._size + len(data) > self.limit:
            self._close()
        if self._zip is None:
            self._open()
        self._zip.writestr(name, data)
        self._size += len(data) + 200

    def finish(self):
        """Closes the last file. Returns the list of file paths (always at least one once something was added)."""
        self._close()
        return list(self.paths)


def backup_zip_is_bundle(blob):
    """True when this zip is a one-file backup made by .backup (it has the bundle marker)."""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            info = z.getinfo(BACKUP_BUNDLE_MARKER)
            if info.file_size > 4096:
                return False
            meta = json.loads(z.read(info).decode("utf-8"))
        return isinstance(meta, dict) and meta.get("bundle") is True
    except Exception:
        return False


def backup_unpack_bundle(blob):
    """[(file name, bytes)] of what is inside a one-file backup: backup.txt and the .zip files. Anything else in it is ignored, and the
    sizes are counted while reading (not taken from the zip's own header), so a lying zip can't fill the memory."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        raise BackupError("That zip is damaged, or it isn't one of my backups.")
    out, total = [], 0
    with z:
        infos = [i for i in z.infolist() if BACKUP_BUNDLE_MEMBER_RE.match(i.filename)]
        if len(infos) > BACKUP_BUNDLE_MAX_MEMBERS:
            raise BackupError("That backup holds too many files.")
        for info in infos:
            limit = BACKUP_MAX_BYTES if info.filename == BACKUP_BUNDLE_TEXT else BACKUP_MAX_ZIP_BYTES
            try:
                with z.open(info) as f:
                    data = f.read(limit + 1)
            except Exception:
                raise BackupError(f"{info.filename} inside it is damaged.")
            if len(data) > limit:
                raise BackupError(f"{info.filename} inside it is too big.")
            total += len(data)
            if total > BACKUP_MAX_UNPACKED:
                raise BackupError("That backup is too big.")
            out.append((info.filename, data))
    return out


def sop_restore_links(gid, items):
    """Add the picture links of a backup to this server's ,sop list (links already there are skipped). Returns (added, already there)."""
    entries = smashpass.entries(gid)
    have = {e.get("url") for e in entries if e.get("url")}
    added = already = 0
    for it in items:
        if it["url"] in have:
            already += 1
        elif len(entries) < SMASHPASS_MAX:
            entries.append({"name": it["name"], "url": it["url"]})
            have.add(it["url"])
            added += 1
    return added, already


async def sop_restore_pictures(items):
    """Put pictures from a backup zip back: the file in the sop folder and the entry in the list. Returns (added, already there, list full, failed).
    Restoring the same zip twice adds nothing twice (the file names are kept). A picture whose entry exists but whose file is gone gets its file back."""
    added = already = full = failed = 0
    for it in items:
        entries = smashpass.entries(it["gid"])
        has_entry = any(e.get("file") == it["file"] for e in entries)
        path = os.path.join(SOP_DIR, it["file"])
        if has_entry and os.path.isfile(path):
            already += 1
            continue
        if not has_entry and len(entries) >= SMASHPASS_MAX:
            full += 1
            continue
        try:
            await asyncio.to_thread(backup_write_file, path, it["data"])
        except OSError:
            log.warning("Could not restore the smash or pass picture %s", it["file"], exc_info=True)
            failed += 1
            continue
        if not has_entry:
            entries.append({"name": it["name"], "file": it["file"]})
        added += 1
    return added, already, full, failed


def count_text(n, one, many):
    return f"**{n:,}** {one if n == 1 else many}"


@bot.hybrid_command(name="emojisync", description="Create or refresh the bot's own icon emojis (bot owner)")
@commands.is_owner()
@commands.cooldown(1, 20, commands.BucketType.user)
async def emojisync_command(ctx):
    """Runs the start-up emoji step again right now: creates the icon emojis (arrows, page jump, tick, cross, pills, gambling pictures...) that
    are missing on the bot's own account and re-creates the ones whose drawing changed, so a new icon pack shows without restarting the bot."""
    cog = bot.get_cog("Matchmaking")
    if cog is None or not hasattr(cog, "make_app_emojis"):
        await ctx.send(embed=status_embed(ctx, "warn", "The emoji pack lives in `matchmaking.py`, and it isn't loaded. Check the console."))
        return
    async with ctx.typing():
        result = await cog.make_app_emojis()
    if result.get("unsupported"):
        await ctx.send(embed=status_embed(
            ctx, "warn", "This discord.py can't create the bot's own emojis (it needs **2.5** or newer). Upload the pictures from the `ui_emojis` folder to any server the bot is in instead: "
                         "they are named `bl_left`, `bl_right`..., and the bot finds them by name."))
    elif result.get("error"):
        await ctx.send(embed=status_embed(ctx, "err", "The icon sync failed. The console has the reason."))
    elif result.get("failed"):
        await ctx.send(embed=status_embed(
            ctx, "warn", f"Created **{result['made']}** icons, **{result['failed']}** failed (the console has the reason; run `{prefix_for('emojisync')}emojisync` again to retry). "
                         f"The bot's account has **{result['total']}** emojis."))
    elif result["made"]:
        await ctx.send(embed=status_embed(
            ctx, "ok", f"Created **{result['made']}** icons ({result['replaced']} of them redrawn). The bot's account now has **{result['total']}** emojis, and they show straight away."))
    else:
        await ctx.send(embed=status_embed(ctx, "ok", f"Nothing to do, every icon is already there (**{result['total']}** emojis on the bot's account)."))


@bot.hybrid_command(name="backup", description="Send you ONE file with reputation, quests, families, matchmaking, smash or pass, assist history (server owner)")
@app_commands.describe(scope="Bot owner only: write 'all' to include every server")
@owner_check()
@commands.guild_only()
@commands.cooldown(1, 30, commands.BucketType.guild)
async def backup_command(ctx, scope: str = None):
    """Sends you, in DMs, ONE zip file with everything: the backup code (reputation, quest progress, families, matchmaking and the ,sop list), the pictures added with
    ,sop upload, the family banners and the assist history. .restore with that file brings everything back after a rebuild. (Only when it is bigger than one
    Discord upload does it come as a few files, each one complete.)"""
    everything = (scope or "").lower() == "all"
    is_bot_owner = await bot.is_owner(ctx.author)
    if everything and not is_bot_owner:
        await ctx.send(embed=status_embed(ctx, "warn", "Only the **bot owner** can back up every server. Use the command without `all` for this server."))
        return
    gids = list(set(auras.data) | set(quests.data) | set(smashpass.data) | set(sop_stats.data) | set(families.data)) if everything else [ctx.guild.id]
    mm_part = backup_mm_pack(gids, is_bot_owner)  # the bot owner gets all of matchmaking (it is global), a server owner only this server's switch and panels
    raw = backup_pack(gids, mm_part)
    code = await asyncio.to_thread(backup_encode, raw)
    people = len({u for g in gids for u in auras.data.get(str(g), {})})
    questers = len({u for g in gids for u in quests.data.get(str(g), {})})
    links = sum(len(backup_sop_links(g)) for g in gids)
    files = backup_sop_files(gids)
    parts = backup_sop_parts(files)
    stamp = f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d}"
    counters = len({u for g in gids for u in sop_stats.data.get(str(g), {})})
    fam_count = sum(len(families.data.get(str(g), {}).get("families", {})) for g in gids)
    banners = backup_banner_files(gids)
    ask_mod = sys.modules.get("askai")  # the ,assist history (askai.py); None when it didn't load
    assist_gids = None if everything else [ctx.guild.id]
    assist_counts = None
    if ask_mod is not None and hasattr(ask_mod, "backup_iter"):
        try:
            assist_counts = await ask_mod.backup_counts(assist_gids)
        except Exception:
            log.exception("Could not count the assist history for the backup")
    has_assist = bool(assist_counts and any(assist_counts))
    text = f"Backup of {count_text(people, 'person', 'people')} (reputation) and {count_text(questers, 'quest record', 'quest records')}."
    text += f"\n{count_text(counters, 'person', 'people')} with smash and pass counts, {count_text(fam_count, 'family', 'families')} (members, ranks, banks, war) and {count_text(len(banners), 'family banner', 'family banners')}."
    if mm_part and mm_part.get("global"):
        text += (f"\nMatchmaking: {count_text(len(mm_part.get('profiles', {})), 'profile', 'profiles')}, {count_text(len(mm_part.get('banned', {})), 'ban', 'bans')}, "
                 f"{count_text(len(mm_part.get('reports', [])), 'report', 'reports')}, blocks, staff, chats in progress and the server settings. The safety chat log is not included.")
    elif mm_part:
        text += "\nMatchmaking: this server's on/off switch and sticky sign-up panels."
    if links or files:
        text += f"\nSmash or pass list: {count_text(links, 'picture link', 'picture links')} and {count_text(len(files), 'uploaded picture', 'uploaded pictures')}."
    if has_assist:
        a_msgs, a_pics, a_leaves, a_nicks = assist_counts
        text += (f"\nAssist history: {count_text(a_msgs, 'saved message', 'saved messages')}, {count_text(a_pics, 'picture record', 'picture records')}, "
                 f"{count_text(a_leaves, 'leave', 'leaves')} and {count_text(a_nicks, 'taught nickname', 'taught nicknames')}.")
    # ---- everything goes into ONE zip file (written on disk piece by piece, so the pictures are never all in memory at once) ----
    folder = await asyncio.to_thread(tempfile.mkdtemp, prefix="backup_")
    bundle = BackupBundle(folder, BACKUP_PART_BYTES)
    failed = 0
    try:
        await asyncio.to_thread(bundle.add, BACKUP_BUNDLE_TEXT, code.encode("ascii"))
        banner_parts, current, size = [], [], 0
        for item in banners:  # the family banners, split into zips that fit one Discord file
            if current and size + item[3] > BACKUP_PART_BYTES:
                banner_parts.append(current)
                current, size = [], 0
            current.append(item)
            size += item[3]
        if current:
            banner_parts.append(current)
        for number, part in enumerate(banner_parts, start=1):
            built = await asyncio.to_thread(backup_build_banner_zip, part)
            if built is not None:
                await asyncio.to_thread(bundle.add, f"family-banners-{stamp}-part{number}of{len(banner_parts)}.zip", built[0])
        for number, part in enumerate(parts, start=1):  # one zip at a time
            built = await asyncio.to_thread(backup_build_zip, part)
            if built is not None:
                await asyncio.to_thread(bundle.add, f"sop-pictures-{stamp}-part{number}of{len(parts)}.zip", built[0])
        if has_assist:  # the assist history, one zip at a time (the message log can be big)
            assist_number = 0
            try:
                async for blob in ask_mod.backup_iter(assist_gids, BACKUP_PART_BYTES):
                    assist_number += 1
                    await asyncio.to_thread(bundle.add, f"assist-history-{stamp}-part{assist_number}.zip", blob)
            except Exception:
                log.exception("Could not build the assist history for the backup")
                failed += 1
        paths = await asyncio.to_thread(bundle.finish)
        total = len(paths)
        if total == 1:
            text += f"\nEverything is in this one file. Keep it private. On the new host use `{prefix_for('restore')}restore` and attach it."
        else:
            text += (f"\nIt was too big for one Discord file, so it comes as **{total}** files (each one is complete). Keep them private. "
                     f"On the new host use `{prefix_for('restore')}restore` and attach all of them in one message.")
        if failed:
            text += "\nThe assist history could not be added (the console has the reason)."
        sent = 0
        for number, path in enumerate(paths, start=1):
            filename = f"backup-{stamp}.zip" if total == 1 else f"backup-{stamp}-part{number}of{total}.zip"
            try:
                with open(path, "rb") as fh:
                    attachment = discord.File(fh, filename=filename)
                    if number == 1:
                        await ctx.author.send(embed=discord.Embed(description=text, color=theme()), file=attachment)
                    else:
                        await ctx.author.send(f"Backup file **{number}/{total}**", file=attachment)
                sent += 1
            except discord.HTTPException:
                if number == 1:
                    await ctx.send(embed=status_embed(ctx, "warn", "I couldn't DM you the backup (your DMs are closed, or the file is too big for Discord). "
                                                                    "Open your DMs for this server and run it again. If it keeps failing, put a smaller number in `BACKUP_PART_MB` (for example 5). "
                                                                    "I don't post the backup here so nobody else can copy it."))
                    return
                log.warning("Could not DM backup file %s of %s", number, total, exc_info=True)
                failed += 1
    finally:
        await asyncio.to_thread(shutil.rmtree, folder, True)
    if sent < total:
        await ctx.send(embed=status_embed(ctx, "warn", f"Sent you {sent} of the {total} backup files, the rest couldn't be sent. Put a smaller number in `BACKUP_PART_MB` (for example 5) and run it again."))
        return
    await ctx.send(embed=status_embed(ctx, "ok", "Sent you the backup in your DMs: **1** file with everything." if total == 1 else f"Sent you the backup in your DMs: **{total}** files, keep them all."))


@bot.hybrid_command(name="restore", description="Load a backup: reputation, quests, families, matchmaking, smash or pass, assist (server owner)")
@app_commands.describe(file="The backup .zip file from .backup (or an older .txt / picture .zip file)", code="Or paste the code here")
@owner_check()
@commands.guild_only()
@commands.cooldown(1, 30, commands.BucketType.guild)
async def restore_command(ctx, file: discord.Attachment = None, *, code: str = None):
    """Put the saved reputation, quest progress and ,sop list back. Attach the backup .zip file from .backup (older backups: the .txt file and the .zip file(s) of pictures, all in one message
    or one at a time). People in the backup get their saved numbers; everybody else is left alone. Pictures already in the list are not added twice."""
    attachments = list(ctx.message.attachments) if ctx.interaction is None else ([file] if file is not None else [])
    if not attachments and not code:
        await ctx.send(embed=status_embed(ctx, "warn", f"Attach the backup file: `{prefix_for('restore')}restore` + the backup .zip file (older backups: the .txt file and the .zip files with pictures). You can also paste the code after the command."))
        return
    texts, zips, problems = [], [], []  # texts: [(name, backup code)], zips: [(name, bytes)]
    for att in attachments[:10]:
        is_zip = att.filename.lower().endswith(".zip")
        label = discord.utils.escape_markdown(att.filename)
        if att.size > (BACKUP_MAX_ZIP_BYTES if is_zip else BACKUP_MAX_BYTES):
            problems.append(f"{label}: that file is too big to be a backup")
            continue
        try:
            raw_file = await att.read()
        except discord.HTTPException:
            problems.append(f"{label}: I couldn't read that file, try attaching it again")
            continue
        if is_zip and backup_zip_is_bundle(raw_file):  # the one-file backup from .backup: backup.txt + the zips inside it
            try:
                inside = await asyncio.to_thread(backup_unpack_bundle, raw_file)
            except BackupError as e:
                problems.append(f"{label}: {e}")
                continue
            for member, member_data in inside:
                if member == BACKUP_BUNDLE_TEXT:
                    texts.append((label, member_data.decode("utf-8", "ignore")))
                else:
                    zips.append((f"{label} / {discord.utils.escape_markdown(member)}", member_data))
        elif is_zip:
            zips.append((label, raw_file))
        else:
            texts.append((label, raw_file.decode("utf-8", "ignore")))
    if code:
        texts.append(("the pasted code", code))
    allowed = None if await bot.is_owner(ctx.author) else {str(ctx.guild.id)}  # the server owner only restores their own server
    people = questers = links = pictures = already = counters = fams = banner_count = 0
    mm_profiles = mm_other = 0
    assist_done = {"messages": 0, "pictures": 0, "leaves": 0, "nicknames": 0}
    ask_mod = sys.modules.get("askai")
    mm_cog = bot.get_cog("Matchmaking")  # None when matchmaking.py didn't load
    rep_done, fam_done = set(), set()
    for label, text in texts:
        try:
            data = await asyncio.to_thread(backup_decode, text)
        except BackupError as e:
            problems.append(f"{label}: {e}")
            continue
        for gid, users in data["aura"].items():
            if allowed is None or gid in allowed:
                auras.data.setdefault(gid, {}).update(users)
                people += len(users)
                rep_done.add(gid)
        for gid, users in data["quests"].items():
            if allowed is None or gid in allowed:
                quests.data.setdefault(gid, {}).update(users)
                questers += len(users)
                rep_done.add(gid)
        for gid, users in data["sop_stats"].items():
            if allowed is None or gid in allowed:
                sop_stats.data.setdefault(gid, {}).update(users)
                counters += len(users)
        for gid, guild_data in data["families"].items():
            if allowed is not None and gid not in allowed:
                continue
            if families.data.get(gid, {}).get("families"):
                problems.append(f"{label}: this server already has families, so the saved ones were not loaded (delete them with `fam purge` first)")
                continue
            families.data[gid] = guild_data
            fams += len(guild_data["families"])
            fam_done.add(gid)
        for gid, items in data["sop"].items():
            if allowed is None or gid in allowed:
                added, seen = sop_restore_links(gid, items)
                links += added
                already += seen
        if data["mm"]:  # matchmaking: server settings for the allowed servers; profiles, bans, reports... only for the bot owner (they are global)
            if mm_cog is None:
                problems.append(f"{label}: matchmaking isn't running, so its part was skipped")
            else:
                if allowed is not None and data["mm"].get("global"):
                    problems.append(f"{label}: matchmaking profiles, bans and reports are only restored by the bot owner")
                added_profiles, added_other = backup_mm_merge(mm_cog.store.data, data["mm"], allowed)  # no await inside, so nothing changes under it
                mm_profiles += added_profiles
                mm_other += added_other
    for label, blob in zips:
        if backup_zip_is_assist(blob):  # the ,assist history: saved messages, picture records, who left, taught nicknames
            if ask_mod is None or not hasattr(ask_mod, "restore_zip"):
                problems.append(f"{label}: ,assist isn't running, so its history was skipped")
                continue
            try:
                got = await ask_mod.restore_zip(blob, allowed, BACKUP_MAX_UNPACKED)
            except ask_mod.AssistBackupError as e:
                problems.append(f"{label}: {e}")
                continue
            except Exception:
                log.exception("Could not restore the assist history")
                problems.append(f"{label}: the assist history couldn't be loaded")
                continue
            for key, value in got.items():
                assist_done[key] += value
            if not any(got.values()):
                already += 1
            continue
        if backup_zip_is_banners(blob):
            try:
                found = await asyncio.to_thread(backup_read_banner_zip, blob, allowed)
            except BackupError as e:
                problems.append(f"{label}: {e}")
                continue
            for _gid, rel, data in found:
                try:
                    await asyncio.to_thread(backup_write_file, os.path.join(FAMILY_BANNER_DIR, rel), data)
                    banner_count += 1
                except OSError:
                    log.warning("Could not restore the family banner %s", rel, exc_info=True)
                    problems.append(f"{label}: a banner couldn't be saved")
            continue
        try:
            items = await asyncio.to_thread(backup_read_zip, blob, allowed)
        except BackupError as e:
            problems.append(f"{label}: {e}")
            continue
        added, seen, full, failed = await sop_restore_pictures(items)
        pictures += added
        already += seen
        if full:
            problems.append(f"{label}: {count_text(full, 'picture', 'pictures')} not added, the list is full ({SMASHPASS_MAX})")
        if failed:
            problems.append(f"{label}: {count_text(failed, 'picture', 'pictures')} couldn't be saved")
    try:
        if rep_done:
            auras.save()
            quests.save()
        if counters:
            sop_stats.save()
        if fam_done:
            families.save()
        if links or pictures:
            smashpass.save()
        if (mm_profiles or mm_other) and mm_cog is not None:
            mm_cog.store.save()
    except OSError:
        log.exception("Could not save the restored backup")
        await ctx.send(embed=status_embed(ctx, "err", "I loaded it, but couldn't write it to disk. Check the storage of your host."))
        return
    for gid in rep_done:
        schedule_goat_role(int(gid))
    for gid in fam_done:  # clean up old family roles / [TAG] nicknames from the backup, and the Best family role
        guild = bot.get_guild(int(gid))
        if guild is not None:
            schedule_family_sync(guild)
            schedule_best_role(guild)
    notes = ("\nSkipped: " + "; ".join(problems[:4]) + (" ..." if len(problems) > 4 else "")) if problems else ""
    if not (rep_done or links or pictures or counters or fam_done or banner_count or mm_profiles or mm_other or any(assist_done.values())):
        if problems:
            await ctx.send(embed=status_embed(ctx, "warn", "Nothing was restored." + notes))
        elif already:
            await ctx.send(embed=status_embed(ctx, "ok", "Everything in that backup is already here."))
        else:
            await ctx.send(embed=status_embed(ctx, "warn", "This backup has nothing for this server."))
        return
    bits = []
    if people or questers:
        bits += [count_text(people, "person", "people") + " (reputation)", count_text(questers, "quest record", "quest records")]
    if links or pictures:
        bits += [count_text(links, "smash or pass link", "smash or pass links"), count_text(pictures, "picture", "pictures")]
    if counters:
        bits.append(count_text(counters, "person", "people") + " with smash and pass counts")
    if fams:
        bits.append(count_text(fams, "family", "families"))
    if banner_count:
        bits.append(count_text(banner_count, "family banner", "family banners"))
    if mm_profiles:
        bits.append(count_text(mm_profiles, "matchmaking profile", "matchmaking profiles"))
    if mm_other:
        bits.append(count_text(mm_other, "other matchmaking record", "other matchmaking records"))
    if assist_done["messages"]:
        bits.append(count_text(assist_done["messages"], "saved assist message", "saved assist messages"))
    if assist_done["pictures"]:
        bits.append(count_text(assist_done["pictures"], "assist picture record", "assist picture records"))
    if assist_done["leaves"]:
        bits.append(count_text(assist_done["leaves"], "leave record", "leave records"))
    if assist_done["nicknames"]:
        bits.append(count_text(assist_done["nicknames"], "taught nickname", "taught nicknames"))
    summary = ", ".join(bits[:-1]) + " and " + bits[-1] if len(bits) > 1 else bits[0]
    await ctx.send(embed=status_embed(ctx, "warn" if problems else "ok", f"Restored {summary}." + notes))


# --------------------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN:
        print("Missing DISCORD_TOKEN. Add it to your environment variables.")
        sys.exit(1)
    acquire_single_instance_lock()
    try:
        bot.run(TOKEN)
    except discord.PrivilegedIntentsRequired:
        print("Turn on BOTH 'Message Content Intent' and 'Server Members Intent' in the Discord Developer Portal "
              "(your app -> Bot -> Privileged Gateway Intents), save, then start the bot again.")
        sys.exit(1)
