"""Join log (discord.py 2.x extension, loaded from bot.py like matchmaking.py).

When someone joins, the bot posts a detailed profile of them in the log channel:
  ONE clean embed (no color bar, drawn as a box by cleanbox.py) with a status line: red alert = alt detected (jailed or not),
  yellow alert = suspicious, green check = safe account. It holds:
  * who they are: names, ID, avatar, banner, badges, account age, nickname, roles, flags
  * how they joined: which invite, who made it, uses, expiry, channel (or vanity URL / added bot)
  * ALT CHECK: a score + verdict (looks normal / suspicious / possible alt / likely alt) with the reasons
    and the accounts they look like (same avatar, same or similar name, created at the same moment,
    banned users, ...)
  * name history: every username / display name / nickname / avatar the bot has seen them use
  * join history and extra details (mutual servers, elevated permissions, ...)

  * STAFF BUTTONS under every profile: Approve (release from quarantine), Timeout 24h, Kick, Ban (with a confirm step), Dismiss.
    Only members who have the matching permission can press them.
  * JOIN GUARD (settings with .joinguard):
      - raid alert: many accounts joining in a few seconds -> alert (and optionally a temporary "highest" verification level)
      - quarantine role for brand new accounts / likely alts until staff press Approve
      - optional automatic 24 hour timeout for a "likely alt" score
      - early watch: a member's first hour is watched for invite links, scam links and mass pings
      - leave log: how long they stayed, and whether they left, were kicked or were banned (and by who)

Commands (default prefix "."):
  .invitelog #channel|off   pick the channel everything is posted in (Manage Server)
  .joinguard                show / change the security settings (Manage Server)

Needs: "Manage Server" for the bot (Discord only lets bots with it read invites), "Ban Members" (compare with the ban list,
       Ban button), "Kick Members", "Moderate Members" (timeout), "Manage Roles" (quarantine role, must sit BELOW the bot's role)
       and "View Audit Log" (who kicked / banned, who added a bot). Anything missing is skipped and the profile says so.

Honest limits:
  * Discord does NOT give bots anyone's old usernames. This file remembers every name it has SEEN (members already in
    the server are saved when the bot starts, later changes are saved live), so history starts the day you install it.
  * The alt check is a set of hints, never proof. Always look at the matches before banning.

Saved in invites.json inside DATA_DIR.
"""

import asyncio
import datetime
import difflib
import json
import logging
import os
import re
import sys
import time
import unicodedata
from collections import deque
from typing import Optional

import discord
from discord.ext import commands

log = logging.getLogger("wordfilter.invites")

DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
INVITE_FILE = os.path.join(DATA_DIR, "invites.json")
DEFAULT_COLOR = 0x2B2D31
OK_COLOR = 0x3BA55D
WARN_COLOR = 0xFAA61A
RED = 0xD9534F
ORANGE = 0xF0883E
YELLOW = 0xE5C07B

HISTORY_LIMIT = 25      # names / avatars remembered per person
BAN_CACHE_SECONDS = 600
MAX_NAME_POOL = 40000   # most current members compared by name

# join guard
GUARD_DEFAULTS = {"raid": "alert", "autotimeout": False, "quarantine": None, "minage": 1, "earlywatch": True, "leavelog": True}
RAID_JOINS = 8          # this many joins ...
RAID_WINDOW = 30        # ... within this many seconds = a raid alert
RAID_LOCK_SECONDS = 600  # how long "lockdown" keeps the highest verification level
EARLY_SECONDS = 3600    # how long after joining a member is watched
TIMEOUT_HOURS = 24
INVITE_LINK = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/([\w-]+)", re.I)
SCAM_WORDS = re.compile(r"discord\.gift|free\s+nitro|nitro\s+(?:gift|giveaway)|steamcommunity\.com/gift|dlscord|discorcl", re.I)


def _color():
    """The bot's embed color (.customize), or the default grey."""
    try:
        return sys.modules["__main__"].theme()
    except Exception:
        return DEFAULT_COLOR


TEXT_BUDGET = 3400  # characters of text in one join log embed; cleanbox.py can draw up to about 3900 in one box


def _icon(name):
    """One of the bot's own drawn icons (bl_jl_alert / bl_jl_warn / bl_jl_safe, see make_joinlog_icons.py) as text.
    Empty text when the icon isn't available yet (the bot creates them on start-up, or run .emojisync): never a classic emoji."""
    try:
        return str(sys.modules["__main__"].emo(name, "") or "")
    except Exception:
        return ""


def _ok_icon():
    """The green tick of every status message (the bot's bl_check)."""
    try:
        return str(sys.modules["__main__"].status_icon("ok") or "\u2705")
    except Exception:
        return "\u2705"


def _status(alt, jailed, is_bot=False):
    """The first line of a join log: (icon, text). Red alert = alt detected, yellow alert = suspicious, green check = safe."""
    if is_bot:
        return _icon("jl_warn"), "Bot added"
    if alt is None:
        return _icon("jl_warn"), "Alt check failed"
    score = alt["score"]
    if score >= 4:
        return _icon("jl_alert"), "Alt detected, jailed" if jailed else "Alt detected, not jailed"
    if score >= 2 or jailed:
        return _icon("jl_warn"), "Suspicious, jailed" if jailed else "Suspicious"
    return _icon("jl_safe"), "Safe account"


def _embed_chars(embed):
    return len(embed)


def _clip(text, limit=900):
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _ts(dt, style="F"):
    return discord.utils.format_dt(dt, style) if dt else "unknown"


def _unix(ts, style="F"):
    return discord.utils.format_dt(datetime.datetime.fromtimestamp(ts, datetime.timezone.utc), style) if ts else "unknown"


def _age(dt):
    """'2 years, 41 days' / '5 days' / '3 hours' for how long ago dt was."""
    if not dt:
        return "unknown"
    return _span(datetime.datetime.now(datetime.timezone.utc) - dt)


def _span(delta):
    days = delta.days
    if days >= 365:
        years, days = divmod(days, 365)
        return f"{years} year{'s' if years != 1 else ''}, {days} day{'s' if days != 1 else ''}"
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''}"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    minutes = max(1, delta.seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _nice(name):
    return name.replace("_", " ").title()


def _norm(name):
    """A name reduced to its letters, so 'John_Smith99' and 'johnsmith' compare equal. '' when too short to compare."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name)).casefold()
    text = "".join(ch for ch in text if ch.isalpha())
    return text if len(text) >= 3 else ""


def _push(history, value):
    """Add a value to a [{"v":..., "t":...}] history unless it is already the latest. True when something was added."""
    if not value:
        return False
    if history and history[-1].get("v") == value:
        return False
    history.append({"v": value, "t": int(time.time())})
    del history[:-HISTORY_LIMIT]
    return True


class InviteStore:
    """invites.json:
    {guild id: {"log": channel id or None, "members": {user id: join record}, "inviters": {user id: {"joins": n, "left": n}}},
     "_users": {user id: {"names": [...], "globals": [...], "avatars": [...], "nicks": {guild id: [...]}, "created": unix, "banned": {guild id: unix}}}}"""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.data = loaded if isinstance(loaded, dict) else {}
        except FileNotFoundError:
            self.data = {}
        except Exception:
            log.exception("Could not read %s, starting empty", self.path)
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---- per server ----
    def guild(self, guild_id):
        g = self.data.setdefault(str(guild_id), {})
        g.setdefault("log", None)
        g.setdefault("members", {})
        g.setdefault("inviters", {})
        return g

    def previous(self, guild_id, user_id):
        return self.guild(guild_id)["members"].get(str(user_id))

    def guard(self, guild_id):
        """The join guard settings of a server, with the defaults filled in."""
        cfg = self.guild(guild_id).setdefault("guard", {})
        for key, value in GUARD_DEFAULTS.items():
            cfg.setdefault(key, value)
        return cfg

    def record_join(self, guild_id, user_id, how, inviter_id, code):
        g = self.guild(guild_id)
        prev = g["members"].get(str(user_id)) or {}
        now = int(time.time())
        g["members"][str(user_id)] = {
            "how": how, "inviter": inviter_id, "code": code, "at": now, "left": False,
            "joins": prev.get("joins", 0) + 1, "first": prev.get("first", now), "last_left": prev.get("last_left"),
        }
        if inviter_id:
            stats = g["inviters"].setdefault(str(inviter_id), {"joins": 0, "left": 0})
            stats["joins"] += 1
        self.save()

    def record_leave(self, guild_id, user_id):
        g = self.guild(guild_id)
        rec = g["members"].get(str(user_id))
        if not rec or rec.get("left"):
            return
        rec["left"] = True
        rec["last_left"] = int(time.time())
        if rec.get("inviter"):
            stats = g["inviters"].setdefault(str(rec["inviter"]), {"joins": 0, "left": 0})
            stats["left"] += 1
        self.save()

    # ---- per person (all servers) ----
    @property
    def users(self):
        return self.data.setdefault("_users", {})

    def user(self, user_id):
        rec = self.users.setdefault(str(user_id), {})
        rec.setdefault("names", [])
        rec.setdefault("globals", [])
        rec.setdefault("avatars", [])
        rec.setdefault("nicks", {})
        rec.setdefault("banned", {})
        return rec

    def note_user(self, user):
        """Remember the current username, global name and avatar. True when something new was recorded."""
        rec = self.user(user.id)
        changed = _push(rec["names"], user.name)
        changed |= _push(rec["globals"], getattr(user, "global_name", None))
        avatar = getattr(user, "avatar", None)
        if avatar is not None:
            changed |= _push(rec["avatars"], avatar.key)
        if rec.get("created") is None:
            rec["created"] = int(user.created_at.timestamp())
            changed = True
        return changed

    def note_nick(self, guild_id, user_id, nick):
        rec = self.user(user_id)
        return _push(rec["nicks"].setdefault(str(guild_id), []), nick)


store = InviteStore(INVITE_FILE)


# ------------------------------------------------------------------ staff buttons
ACTIONS = {
    "approve": ("Approve", discord.ButtonStyle.success),
    "timeout": (f"Timeout {TIMEOUT_HOURS}h", discord.ButtonStyle.secondary),
    "kick": ("Kick", discord.ButtonStyle.secondary),
    "ban": ("Ban", discord.ButtonStyle.danger),
    "dismiss": ("Dismiss", discord.ButtonStyle.secondary),
}
NEEDS = {  # the button works for anyone with ONE of these permissions
    "approve": ("moderate_members", "manage_roles", "kick_members", "ban_members"),
    "timeout": ("moderate_members",),
    "kick": ("kick_members",),
    "ban": ("ban_members",),
    "dismiss": ("moderate_members", "kick_members", "ban_members", "manage_guild"),
}
NEED_NAMES = {"moderate_members": "Timeout Members", "manage_roles": "Manage Roles", "kick_members": "Kick Members", "ban_members": "Ban Members", "manage_guild": "Manage Server"}


def _allowed(member, act):
    perms = member.guild_permissions
    return perms.administrator or any(getattr(perms, n, False) for n in NEEDS[act])


def action_view(user_id, quarantined=False):
    view = discord.ui.View(timeout=None)
    for act in (["approve"] if quarantined else []) + ["timeout", "kick", "ban", "dismiss"]:
        view.add_item(ActionButton(act, user_id))
    return view


def _walk(items):
    for item in items:
        yield item
        kids = getattr(item, "children", None)
        if kids:
            yield from _walk(list(kids))


def _is_v2(message):
    return bool(getattr(getattr(message, "flags", None), "components_v2", False))


def _relayout(message, uid, acts):
    """A copy of a clean (Components V2) join log message with its staff buttons replaced by `acts` (none = the whole row goes).
    None when the message can't be rebuilt. The join log is drawn as a clean box by cleanbox.py, and Discord does not allow an
    edit with `view=None` or a bare button View on such a message, so the box is rebuilt from the message itself."""
    try:
        view = discord.ui.LayoutView.from_message(message, timeout=None)
        for row in [i for i in _walk(list(view.children)) if isinstance(i, discord.ui.ActionRow)]:
            for child in list(row.children):
                row.remove_item(child)
            for act in acts:
                row.add_item(ActionButton(act, uid))
            if not acts:
                for box in [i for i in _walk(list(view.children)) if hasattr(i, "remove_item") and row in (getattr(i, "children", None) or [])]:
                    box.remove_item(row)
                    kids = list(getattr(box, "children", []))
                    if kids and isinstance(kids[-1], discord.ui.Separator):  # no divider left hanging at the bottom
                        box.remove_item(kids[-1])
        return view
    except Exception:
        log.info("Could not rebuild the join log message", exc_info=True)
        return None


async def _set_buttons(interaction, uid, acts):
    """Change the staff buttons of the message the button sits on (`acts` = [] removes them). Works on clean boxes and on old embeds."""
    message = interaction.message
    if _is_v2(message):
        view = _relayout(message, uid, acts)
        if view is not None:
            return await interaction.response.edit_message(view=view)
        return await interaction.response.send_message("I couldn't change the buttons on this message.", ephemeral=True)
    return await interaction.response.edit_message(view=action_view(uid, quarantined=False) if acts else None)


async def _say(message, text):
    """A short line under the profile saying what staff just did (mentions never ping)."""
    try:
        await message.channel.send(embed=discord.Embed(description=text, color=_color()), allowed_mentions=discord.AllowedMentions.none())
    except (discord.HTTPException, AttributeError):
        pass


async def _deny(interaction, text):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def handle_action(interaction, act, uid):
    guild, staff = interaction.guild, interaction.user
    if guild is None or not isinstance(staff, discord.Member):
        return await _deny(interaction, "Use this button in the server.")
    if not _allowed(staff, act):
        names = " or ".join(NEED_NAMES[n] for n in NEEDS[act])
        return await _deny(interaction, f"You need the **{names}** permission for that button.")
    source = interaction.message
    target = guild.get_member(uid)

    if act == "dismiss":
        return await _set_buttons(interaction, uid, [])

    if act == "approve":
        cfg = store.guard(guild.id)
        role = guild.get_role(cfg["quarantine"]) if cfg.get("quarantine") else None
        if target is None or role is None:
            return await _deny(interaction, "They already left, or the quarantine role no longer exists.")
        try:
            await target.remove_roles(role, reason=f"Approved by {staff} from the join log")
        except discord.HTTPException:
            return await _deny(interaction, "I couldn't remove the role (is it above my highest role, do I have Manage Roles?).")
        await _set_buttons(interaction, uid, ["timeout", "kick", "ban", "dismiss"])
        return await _say(source, f"{_ok_icon()} {staff.mention} approved <@{uid}> and released them from quarantine.")

    if target is not None:  # never act on the owner, or on someone at or above the person pressing the button
        if target.id == guild.owner_id:
            return await _deny(interaction, "That is the server owner.")
        if staff.id != guild.owner_id and target.top_role >= staff.top_role:
            return await _deny(interaction, "Their highest role is not below yours.")

    if act == "timeout":
        if target is None:
            return await _deny(interaction, "They are not in the server any more.")
        try:
            await target.timeout(datetime.timedelta(hours=TIMEOUT_HOURS), reason=f"Timeout by {staff} from the join log")
        except discord.HTTPException:
            return await _deny(interaction, "I couldn't time them out (check my role position and the Moderate Members permission).")
        await interaction.response.send_message(f"Timed out for {TIMEOUT_HOURS} hours.", ephemeral=True)
        return await _say(source, f"\u23F3 {staff.mention} timed out <@{uid}> for {TIMEOUT_HOURS} hours.")

    # kick / ban: ask again first, so a mis-tap does nothing
    shown = "ban" if act == "ban" else "kick"
    await interaction.response.send_message(f"Really **{shown}** <@{uid}>?", view=ConfirmView(staff.id, act, uid, source), ephemeral=True)


class ActionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"jl:(?P<act>approve|timeout|kick|ban|dismiss):(?P<uid>[0-9]+)"):
    """One button of the staff row. The user ID lives inside the custom id, so the buttons keep working after a bot restart."""

    def __init__(self, act, uid):
        label, style = ACTIONS[act]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"jl:{act}:{uid}"))
        self.act = act
        self.uid = int(uid)

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["act"], int(match["uid"]))

    async def callback(self, interaction):
        await handle_action(interaction, self.act, self.uid)


class ConfirmView(discord.ui.View):
    def __init__(self, staff_id, act, uid, source):
        super().__init__(timeout=60)
        self.staff_id, self.act, self.uid, self.source = staff_id, act, uid, source

    async def interaction_check(self, interaction):
        return interaction.user.id == self.staff_id

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        guild = interaction.guild
        reason = f"{self.act} by {interaction.user} from the join log"
        try:
            if self.act == "ban":
                await guild.ban(discord.Object(id=self.uid), reason=reason, delete_message_seconds=0)
            else:
                target = guild.get_member(self.uid)
                if target is None:
                    return await interaction.response.edit_message(content="They are not in the server any more.", view=None)
                await target.kick(reason=reason)
        except discord.HTTPException:
            return await interaction.response.edit_message(content="I couldn't do that (check my role position and permissions).", view=None)
        await interaction.response.edit_message(content="Done.", view=None)
        try:
            view = _relayout(self.source, self.uid, []) if _is_v2(self.source) else None
            if view is not None or not _is_v2(self.source):
                await self.source.edit(view=view)
        except discord.HTTPException:
            pass
        verb = "banned" if self.act == "ban" else "kicked"
        await _say(self.source, f"\U0001F528 {interaction.user.mention} {verb} <@{self.uid}>.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


def _snapshot(invites):
    """{code: details} from a list of discord.Invite. Details are kept so the join profile can show them even after the invite is gone."""
    out = {}
    for i in invites:
        out[i.code] = {
            "uses": i.uses or 0,
            "max": i.max_uses or 0,
            "inviter": i.inviter.id if i.inviter else None,
            "channel": i.channel.id if i.channel else None,
            "created": int(i.created_at.timestamp()) if i.created_at else None,
            "max_age": i.max_age or 0,
            "temp": bool(i.temporary),
        }
    return out


def _name_matches(targets, pools):
    """Runs in a thread. targets: set of normalized names. pools: [(kind, user id, shown name, [normalized names])].
    -> [(kind, user id, shown name, best ratio)] for every pool entry with a ratio of at least 0.85."""
    out = []
    for kind, uid, shown, norms in pools:
        best = 0.0
        for n in norms:
            for t in targets:
                if n == t:
                    r = 1.0
                else:
                    if abs(len(n) - len(t)) > max(2, len(t) // 3):
                        continue
                    sm = difflib.SequenceMatcher(None, t, n)
                    if sm.real_quick_ratio() < 0.85 or sm.quick_ratio() < 0.85:
                        continue
                    r = sm.ratio()
                if r > best:
                    best = r
        if best >= 0.85:
            out.append((kind, uid, shown, best))
    return out


class InviteTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}       # guild id -> {code: details}
        self.vanity = {}      # guild id -> vanity uses
        self.locks = {}       # guild id -> asyncio.Lock (joins are handled one at a time)
        self.no_perm = set()  # guilds where we already complained about the missing permission
        self.ban_cache = {}   # guild id -> (time, [ban dicts] or None)
        self.joins = {}       # guild id -> deque of (time, user id) for the raid check
        self.raid_until = {}  # guild id -> time until which "join flood" counts as a warning sign
        self.alerted = {}     # (guild id, user id) -> time of the last early-watch alert
        self._init_task = None

    async def cog_load(self):
        self.bot.add_dynamic_items(ActionButton)  # the staff buttons keep working after a restart
        self._init_task = asyncio.create_task(self._prime_all())

    async def cog_unload(self):
        self.bot.remove_dynamic_items(ActionButton)
        if self._init_task:
            self._init_task.cancel()

    # ---------------------------------------------------------------- cache
    async def _fetch(self, guild):
        """(invite snapshot, vanity uses). Raises discord.Forbidden without Manage Server."""
        invites = await guild.invites()
        vanity_uses = None
        if "VANITY_URL" in guild.features:
            try:
                v = await guild.vanity_invite()
                vanity_uses = (v.uses or 0) if v else None
            except (discord.Forbidden, discord.HTTPException):
                vanity_uses = None
        return _snapshot(invites), vanity_uses

    async def _prime(self, guild):
        try:
            self.cache[guild.id], self.vanity[guild.id] = await self._fetch(guild)
        except discord.Forbidden:
            log.info("No Manage Server permission in %s: invite tracking is off there", guild.id)
        except discord.HTTPException:
            log.info("Could not read the invites of %s", guild.id, exc_info=True)

    async def _prime_all(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._prime(guild)
            await asyncio.sleep(0.5)  # be gentle with the rate limit
        self._seed_names()
        for guild in self.bot.guilds:  # a raid lockdown that was running when the bot stopped still has to end
            restore = store.guard(guild.id).get("restore")
            if restore:
                asyncio.create_task(self._restore_level(guild.id, max(0, restore.get("until", 0) - time.time())))

    def _seed_names(self):
        """Save the names everyone already has, so a later change shows up as 'former name'."""
        changed = False
        for guild in self.bot.guilds:
            for m in guild.members:
                if m.bot:
                    continue
                changed |= store.note_user(m)
                if m.nick:
                    changed |= store.note_nick(guild.id, m.id, m.nick)
        if changed:
            store.save()

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        await self._prime(guild)
        self._seed_names()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.cache.pop(guild.id, None)
        self.vanity.pop(guild.id, None)
        self.ban_cache.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if invite.guild is None:
            return
        self.cache.setdefault(invite.guild.id, {}).update(_snapshot([invite]))

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        if invite.guild is None:
            return
        cached = self.cache.get(invite.guild.id, {})
        entry = cached.get(invite.code)
        # keep an invite that was one use away from its limit: its last user may join right after this event
        if entry and not (entry["max"] and entry["uses"] == entry["max"] - 1):
            cached.pop(invite.code, None)

    # ---------------------------------------------------- name history events
    @commands.Cog.listener()
    async def on_user_update(self, before, after):
        if after.bot:
            return
        if store.note_user(after):
            store.save()

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if after.bot or before.nick == after.nick:
            return
        changed = False
        for nick in (before.nick, after.nick):  # the old nickname is kept too, so it shows up as a former one
            if nick:
                changed |= store.note_nick(after.guild.id, after.id, nick)
        if changed:
            store.save()

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        store.user(user.id)["banned"][str(guild.id)] = int(time.time())
        store.note_user(user)
        store.save()
        self.ban_cache.pop(guild.id, None)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        store.user(user.id)["banned"].pop(str(guild.id), None)
        store.save()
        self.ban_cache.pop(guild.id, None)

    # ----------------------------------------------------------------- join
    async def _resolve(self, guild):
        """-> (how, inviter id, code, invite details). how: invite / vanity / guess / unknown / noperm"""
        lock = self.locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            old = self.cache.get(guild.id)
            try:
                new, vanity_uses = await self._fetch(guild)
            except discord.Forbidden:
                return "noperm", None, None, None
            except discord.HTTPException:
                log.info("Could not read the invites of %s", guild.id, exc_info=True)
                return "unknown", None, None, None
            if old is None:  # never had a baseline (the bot only just got the permission)
                self.cache[guild.id], self.vanity[guild.id] = new, vanity_uses
                return "unknown", None, None, None

            used = [c for c, d in new.items() if d["uses"] > old.get(c, {"uses": 0})["uses"]]
            vanity_up = vanity_uses is not None and vanity_uses > (self.vanity.get(guild.id) or 0)
            gone = [c for c, d in old.items() if c not in new and d["max"] and d["uses"] == d["max"] - 1]  # last use just spent

            self.cache[guild.id], self.vanity[guild.id] = new, vanity_uses
            if len(used) == 1 and not vanity_up:
                return "invite", new[used[0]]["inviter"], used[0], new[used[0]]
            if not used and vanity_up:
                return "vanity", None, "vanity", {"uses": vanity_uses}
            if not used and not vanity_up and len(gone) == 1:
                d = dict(old[gone[0]])
                d["uses"] = d["max"]
                return "guess", d["inviter"], gone[0], d
            return "unknown", None, None, None  # nothing moved, or several people joined in the same moment

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        raid = self._raid_check(member)
        if raid:
            asyncio.create_task(self._raid_response(guild, raid))
        if member.bot:
            how, inviter_id, code, info = "bot", None, None, None
            # a bot is added by someone with Manage Server; the audit log says who
            try:
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.bot_add):
                    if entry.target and entry.target.id == member.id:
                        inviter_id = entry.user.id if entry.user else None
                        break
            except (discord.Forbidden, discord.HTTPException):
                pass
        else:
            how, inviter_id, code, info = await self._resolve(guild)
        if how == "noperm" and guild.id not in self.no_perm:
            self.no_perm.add(guild.id)
            await self._notify(guild, [discord.Embed(
                description="I can't see who invited people because I'm missing the **Manage Server** permission. Give it to my role and the invite part of the join profile will fill in.",
                color=WARN_COLOR)])

        # what we knew about them BEFORE this join (history, rejoin, names)
        prev = store.previous(guild.id, member.id)
        prev = dict(prev) if prev else None  # copy: record_join replaces it
        user_before = json.loads(json.dumps(store.users.get(str(member.id), {})))  # copy of their name history before today's snapshot

        alt = None
        if not member.bot:
            try:
                alt = await self._alt_check(member, how, inviter_id, code, prev)
            except Exception:
                log.exception("Alt check failed for %s", member.id)

        auto, quarantined = await self._auto_actions(member, alt)

        store.note_user(member)
        if member.nick:
            store.note_nick(guild.id, member.id, member.nick)
        store.record_join(guild.id, member.id, "unknown" if how == "noperm" else how, inviter_id, code)  # saves
        rec = store.previous(guild.id, member.id)
        if rec is not None and alt:
            rec["score"], rec["verdict"] = alt["score"], alt["verdict"]
            store.save()

        jailed = quarantined or any(line.startswith("Automatically timed out") for line in auto)  # quarantine role or automatic timeout
        embeds = await self._profile_embeds(member, how, inviter_id, code, info, prev, alt, user_before, auto, jailed)
        await self._notify(guild, embeds, action_view(member.id, quarantined))

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        store.record_leave(member.guild.id, member.id)
        if store.guard(member.guild.id)["leavelog"] and store.guild(member.guild.id).get("log"):
            asyncio.create_task(self._leave_log(member))

    # ---------------------------------------------------------- join guard
    async def _auto_actions(self, member, alt):
        """Quarantine role and optional automatic timeout. -> (list of lines for the profile, quarantined?)"""
        guild = member.guild
        cfg = store.guard(guild.id)
        auto, quarantined = [], False
        if member.bot:
            return auto, quarantined
        score = alt["score"] if alt else 0
        role = guild.get_role(cfg["quarantine"]) if cfg.get("quarantine") else None
        young = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days < cfg["minage"]
        if role and (young or score >= 4):
            try:
                await member.add_roles(role, reason="Join guard: new account or suspicious score")
                quarantined = True
                auto.append(f"Quarantined with {role.mention} ({'account under ' + str(cfg['minage']) + ' day(s) old' if young else 'alt score ' + str(score)}). Press **Approve** to release them.")
            except discord.HTTPException:
                auto.append("Wanted to quarantine them but couldn't give the role (the role must be below mine, and I need Manage Roles).")
        if cfg["autotimeout"] and score >= 7:
            try:
                await member.timeout(datetime.timedelta(hours=TIMEOUT_HOURS), reason=f"Join guard: likely alt (score {score})")
                auto.append(f"Automatically timed out for {TIMEOUT_HOURS} hours (alt score {score}).")
            except discord.HTTPException:
                auto.append("Wanted to time them out but couldn't (check Moderate Members and my role position).")
        return auto, quarantined

    def _raid_check(self, member):
        """Remember this join. -> the list of recent joins when this one tips over into a raid alert, else None."""
        gid, now = member.guild.id, time.time()
        recent = self.joins.setdefault(gid, deque())
        recent.append((now, member.id))
        while recent and now - recent[0][0] > RAID_WINDOW:
            recent.popleft()
        if len(recent) >= RAID_JOINS and now > self.raid_until.get(gid, 0) and store.guard(gid)["raid"] != "off":
            self.raid_until[gid] = now + RAID_LOCK_SECONDS
            return list(recent)
        return None

    async def _raid_response(self, guild, recent):
        cfg = store.guard(guild.id)
        lines = []
        for _t, uid in recent[:15]:
            m = guild.get_member(uid)
            lines.append(f"<@{uid}> account {_age(m.created_at)} old" if m else f"<@{uid}>")
        e = discord.Embed(title="\U0001F6A8 Possible raid", color=RED, timestamp=discord.utils.utcnow(),
                          description=f"**{len(recent)} accounts** joined in the last {RAID_WINDOW} seconds.\n" + _clip("\n".join(lines), 1500))
        if cfg["raid"] == "lockdown":
            note = f"Verification level raised to the highest for {RAID_LOCK_SECONDS // 60} minutes."
            if cfg.get("restore"):
                note = "Lockdown was already on, it keeps running."
            else:
                try:
                    old = guild.verification_level
                    await guild.edit(verification_level=discord.VerificationLevel.highest, reason="Join guard: possible raid")
                    cfg["restore"] = {"level": old.value, "until": time.time() + RAID_LOCK_SECONDS}
                    store.save()
                    asyncio.create_task(self._restore_level(guild.id, RAID_LOCK_SECONDS))
                except discord.HTTPException:
                    note = "I tried to raise the verification level but couldn't (I need Manage Server)."
            e.add_field(name="Lockdown", value=note, inline=False)
        else:
            e.add_field(name="Tip", value="Use `joinguard raid lockdown` to raise the verification level automatically next time. Each new profile in the next 10 minutes gets extra warning points.", inline=False)
        e.set_footer(text="Pause invites in Server Settings > Invites if it keeps going.")
        await self._notify(guild, [e])

    async def _restore_level(self, guild_id, delay):
        await asyncio.sleep(delay)
        guild = self.bot.get_guild(guild_id)
        cfg = store.guard(guild_id)
        restore = cfg.get("restore")
        if guild is None or not restore:
            return
        try:
            await guild.edit(verification_level=discord.VerificationLevel(restore["level"]), reason="Join guard: raid lockdown over")
        except (discord.HTTPException, ValueError):
            log.info("Could not restore the verification level of %s", guild_id, exc_info=True)
        cfg["restore"] = None
        store.save()
        await self._notify(guild, [discord.Embed(description="Raid lockdown is over, the verification level is back to what it was.", color=OK_COLOR)])

    async def _leave_log(self, member):
        guild = member.guild
        await asyncio.sleep(2)  # the audit log entry for a kick / ban shows up a moment late
        how, who, reason = "left", None, None
        try:
            for action, word in ((discord.AuditLogAction.kick, "kicked"), (discord.AuditLogAction.ban, "banned")):
                async for entry in guild.audit_logs(limit=5, action=action):
                    if entry.target and entry.target.id == member.id and (discord.utils.utcnow() - entry.created_at).total_seconds() < 20:
                        how, who, reason = word, entry.user, entry.reason
                        break
                if how != "left":
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass
        rec = store.previous(guild.id, member.id) or {}
        title = {"left": "Member left", "kicked": "Member kicked", "banned": "Member banned"}[how]
        e = discord.Embed(title=title, color={"left": _color(), "kicked": ORANGE, "banned": RED}[how], timestamp=discord.utils.utcnow(),
                          description=f"{member.mention} (`{member.id}`)")
        e.set_thumbnail(url=member.display_avatar.url)  # no name line on top: the mention already shows the name
        if who:
            e.add_field(name="By", value=f"{who.mention}" + (f"\nReason: {_clip(reason, 300)}" if reason else ""), inline=False)
        if member.joined_at:
            e.add_field(name="Stayed", value=f"**{_span(discord.utils.utcnow() - member.joined_at)}**\nJoined {_ts(member.joined_at, 'R')}", inline=True)
        e.add_field(name="Account age", value=_age(member.created_at), inline=True)
        if rec.get("how") in ("invite", "guess") and rec.get("inviter"):
            e.add_field(name="Came through", value=f"<@{rec['inviter']}> (`{rec.get('code')}`)", inline=True)
        elif rec.get("how") == "vanity":
            e.add_field(name="Came through", value="custom server URL", inline=True)
        if rec.get("score") is not None:
            e.add_field(name="Alt score at join", value=f"{rec['score']} ({rec.get('verdict', '')})", inline=True)
        if rec.get("first_msg"):
            e.add_field(name="First message", value=_unix(rec["first_msg"], "R"), inline=True)
        roles = [r.mention for r in reversed(getattr(member, "roles", [])) if r.id != guild.id]
        e.add_field(name=f"Roles ({len(roles)})", value=_clip(" ".join(roles) or "none", 600), inline=False)
        await self._notify(guild, [e])

    @commands.Cog.listener()
    async def on_message(self, message):
        """Early watch: the first hour of a new member is checked for invite links, scam links and mass pings."""
        author = message.author
        if message.guild is None or author.bot or not isinstance(author, discord.Member) or not author.joined_at:
            return
        gid = message.guild.id
        age = (discord.utils.utcnow() - author.joined_at).total_seconds()
        rec = store.guild(gid)["members"].get(str(author.id))
        if rec is not None and not rec.get("first_msg") and age < 7 * 86400:
            rec["first_msg"] = int(time.time())
            rec["first_msg_after"] = int(age)
            store.save()
        if age > EARLY_SECONDS or not store.guard(gid)["earlywatch"] or not store.guild(gid).get("log"):
            return
        text = message.content or ""
        reasons = []
        own = set(self.cache.get(gid, {})) | {message.guild.vanity_url_code or ""}
        foreign = [c for c in INVITE_LINK.findall(text) if c not in own]
        if foreign:
            reasons.append("posted an invite link to **another server**")
        if SCAM_WORDS.search(text):
            reasons.append("posted a link that looks like a **Nitro / gift scam**")
        if message.mention_everyone or len(message.mentions) + len(message.role_mentions) >= 5:
            reasons.append("**mass pinged** people")
        if not reasons:
            return
        key = (gid, author.id)
        if time.time() - self.alerted.get(key, 0) < 600:
            return
        self.alerted[key] = time.time()
        e = discord.Embed(title="\u26A0\uFE0F New member posted something suspicious", color=ORANGE, timestamp=discord.utils.utcnow(),
                          description=f"{author.mention} (`{author.id}`) " + ", ".join(reasons))
        e.set_thumbnail(url=author.display_avatar.url)  # no name line on top: the mention already shows the name
        e.add_field(name="Where", value=f"{message.channel.mention} \u00b7 [jump to message]({message.jump_url})", inline=False)
        e.add_field(name="Joined", value=f"{_ts(author.joined_at, 'R')} (account {_age(author.created_at)} old)", inline=True)
        if rec and rec.get("score") is not None:
            e.add_field(name="Alt score at join", value=f"{rec['score']} ({rec.get('verdict', '')})", inline=True)
        e.add_field(name="Message", value="```" + _clip(text.replace("`", "'"), 600) + "```", inline=False)
        e.set_footer(text="Nothing was deleted. Delete the message yourself, or use the buttons.")
        await self._notify(message.guild, [e], action_view(author.id))

    # ------------------------------------------------------------ alt check
    async def _bans(self, guild):
        """The ban list (cached for 10 minutes), or None when the bot can't read it."""
        cached = self.ban_cache.get(guild.id)
        if cached and time.time() - cached[0] < BAN_CACHE_SECONDS:
            return cached[1]
        out = []
        try:
            async for entry in guild.bans(limit=2000):
                u = entry.user
                out.append({"id": u.id, "name": u.name, "global": getattr(u, "global_name", None),
                            "avatar": u.avatar.key if u.avatar else None, "reason": entry.reason})
        except (discord.Forbidden, discord.HTTPException):
            out = None
        self.ban_cache[guild.id] = (time.time(), out)
        return out

    async def _alt_check(self, member, how, inviter_id, code, prev):
        """Score how much this new member looks like an alt. -> dict(score, verdict, action, color, reasons, matches, checked)."""
        guild = member.guild
        now = datetime.datetime.now(datetime.timezone.utc)
        score = 0
        reasons = []     # (points, text)
        matches = {}     # user id -> {"weight": n, "tags": set, "why": [..]}

        def add(points, text):
            nonlocal score
            score += points
            reasons.append((points, text))

        def match(uid, weight, tag, why):
            m = matches.setdefault(uid, {"weight": 0, "tags": set(), "why": []})
            m["weight"] += weight
            m["tags"].add(tag)
            m["why"].append(why)

        g = store.guild(guild.id)
        banned_ids = {int(uid) for uid, rec in store.users.items() if str(guild.id) in (rec.get("banned") or {})}
        bans = await self._bans(guild)
        if bans:
            banned_ids |= {b["id"] for b in bans}
        ban_info = {b["id"]: b for b in bans} if bans else {}

        # --- account age -------------------------------------------------
        age = now - member.created_at
        if age.days < 1:
            add(3, "Account is **less than a day** old")
        elif age.days < 7:
            add(2, "Account is **less than a week** old")
        elif age.days < 30:
            add(1, "Account is **less than a month** old")
        if member.joined_at and (member.joined_at - member.created_at) < datetime.timedelta(hours=1):
            add(1, "Joined **within an hour** of creating the account")

        # --- profile look ------------------------------------------------
        if member.avatar is None:
            add(1, "Default avatar (never set a picture)")
        for n in (member.name, member.global_name or ""):
            if re.fullmatch(r"[A-Za-z._]{2,}\d{4,}", n or "") or re.fullmatch(r"(?i)user\d+", n or ""):
                add(1, "Username looks auto generated (`" + discord.utils.escape_markdown(n) + "`)")
                break

        # --- same picture as someone else ---------------------------------
        if member.avatar is not None:
            key = member.avatar.key
            for uid, rec in store.users.items():
                if uid == str(member.id):
                    continue
                if any(a.get("v") == key for a in rec.get("avatars", [])):
                    tag = "banned" if int(uid) in banned_ids else "known"
                    match(int(uid), 6 if tag == "banned" else 3, tag, "uses the **same avatar**")
            for b in bans or []:
                if b["avatar"] == key:
                    match(b["id"], 6, "banned", "uses the **same avatar** as this banned user")

        # --- same / similar name as a banned, past or current member --------
        targets = {n for n in (_norm(member.name), _norm(member.global_name)) if n}
        pools = []
        for b in bans or []:
            norms = [n for n in (_norm(b["name"]), _norm(b["global"])) if n]
            if norms:
                pools.append(("banned", b["id"], b["name"], norms))
        here = {m.id for m in guild.members}
        known_pool_ids = {b["id"] for b in bans or []}
        for uid, rec in store.users.items():
            uid_i = int(uid)
            if uid_i == member.id or uid_i in known_pool_ids or uid_i in here:
                continue
            if uid not in g["members"] and str(guild.id) not in rec.get("banned", {}):
                continue  # only people connected to this server
            norms = {_norm(h.get("v")) for h in rec.get("names", []) + rec.get("globals", [])}
            norms = [n for n in norms if n]
            if norms:
                kind = "banned" if str(guild.id) in rec.get("banned", {}) else "left"
                pools.append((kind, uid_i, (rec["names"][-1]["v"] if rec.get("names") else str(uid_i)), norms))
        for m in list(guild.members)[:MAX_NAME_POOL]:
            if m.id == member.id or m.bot:
                continue
            norms = [n for n in (_norm(m.name), _norm(m.global_name), _norm(m.nick)) if n]
            if norms:
                pools.append(("here", m.id, m.name, norms))
        if targets and pools:
            longest = max(len(t) for t in targets)
            for kind, uid, shown, ratio in await asyncio.to_thread(_name_matches, targets, pools):
                exact = ratio >= 0.999
                if kind != "banned" and longest < 5:
                    continue  # short names like "alex" match too many harmless people
                if kind == "banned":
                    match(uid, 5 if exact else 4, "banned", "has the **same name**" if exact else f"has a **very similar name** ({int(ratio * 100)}%)")
                elif exact:
                    match(uid, 2, kind, "has the **same name**")
                elif ratio >= 0.9:
                    match(uid, 1, kind, f"has a **similar name** ({int(ratio * 100)}%)")

        # --- accounts made at the same moment ------------------------------
        if age.days < 90:
            created = member.created_at.timestamp()
            close = set()
            for m in guild.members:
                if m.id != member.id and not m.bot and abs(m.created_at.timestamp() - created) <= 180:
                    close.add(m.id)
            for uid, rec in g["members"].items():
                other = store.users.get(uid, {}).get("created")
                if other and int(uid) != member.id and abs(other - created) <= 180:
                    close.add(int(uid))
            for uid in list(close)[:5]:
                tag = "banned" if uid in banned_ids else "known"
                match(uid, 3 if tag == "banned" else 2, tag, "account was created **within 3 minutes** of this one")

        # --- the same invite brought several people at once -----------------
        if code and how in ("invite", "guess"):
            cutoff = time.time() - 900
            burst = [uid for uid, rec in g["members"].items() if rec.get("code") == code and rec.get("at", 0) >= cutoff and int(uid) != member.id]
            if len(burst) >= 4:
                add(3, f"**{len(burst)} other people** joined with the same invite in the last 15 minutes (raid pattern)")
            elif len(burst) >= 2:
                add(2, f"**{len(burst)} other people** joined with the same invite in the last 15 minutes")

        # --- the person who invited them ------------------------------------
        if inviter_id and how in ("invite", "guess"):
            invitees = [uid for uid, rec in g["members"].items() if rec.get("inviter") == inviter_id and int(uid) != member.id]
            bad = [uid for uid in invitees if int(uid) in banned_ids]
            if len(bad) >= 3:
                add(3, f"Invited by someone whose invites include **{len(bad)} banned users**")
            elif bad:
                add(2, "Invited by someone who already brought a **banned user**")
            stats = g["inviters"].get(str(inviter_id), {"joins": 0, "left": 0})
            if stats["joins"] >= 5 and stats["left"] / stats["joins"] >= 0.7:
                add(1, f"Most people this inviter brings leave ({stats['left']} of {stats['joins']})")

        # --- many accounts joined at once ------------------------------------
        if time.time() < self.raid_until.get(guild.id, 0):
            add(2, "Joined during a **join flood alert** (many accounts joined at the same time)")

        # --- the sum of the matches (banned matches count in full, harmless "same name" matches only the top two) ----
        soft = 0
        for uid, m in sorted(matches.items(), key=lambda kv: -kv[1]["weight"]):
            label = " (banned)" if "banned" in m["tags"] else " (left the server)" if "left" in m["tags"] else " (still here)" if "here" in m["tags"] else ""
            if "banned" in m["tags"] or m["weight"] >= 3:
                points = m["weight"]
            else:
                soft += 1
                points = m["weight"] if soft <= 2 else 0
            add(points, f"<@{uid}> " + ", ".join(m["why"]) + label)

        if score >= 7:
            verdict, action, color = "\U0001F534 LIKELY ALT", "Consider **banning** (or kick) after a quick look at the matches below.", RED
        elif score >= 4:
            verdict, action, color = "\U0001F7E0 POSSIBLE ALT", "**Review** them: check the matches below and watch what they do first.", ORANGE
        elif score >= 2:
            verdict, action, color = "\U0001F7E1 Slightly suspicious", "Nothing to do yet, keep an eye on them.", YELLOW
        else:
            verdict, action, color = "\U0001F7E2 Looks normal", "No action needed.", OK_COLOR
        ordered = sorted(matches.items(), key=lambda kv: -kv[1]["weight"])
        return {
            "score": score, "verdict": verdict, "action": action, "color": color,
            "reasons": reasons, "matches": ordered,
            "checked": f"{len(guild.members)} current members, {len(bans) if bans is not None else 'no'} bans{'' if bans is not None else ' (missing Ban Members permission)'}, {len(g['members'])} past joins",
        }

    # --------------------------------------------------------------- output
    async def _user(self, user_id):
        """A discord.User for an ID (cached or fetched), or None."""
        if not user_id:
            return None
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                return None
        return user

    def _how_text(self, guild, how, inviter_id, code):
        who = f"<@{inviter_id}>" if inviter_id else "someone"
        if how == "invite":
            return f"Invited by {who} with invite `{code}`"
        if how == "guess":
            return f"Probably invited by {who} with invite `{code}` (it ran out of uses as they joined)"
        if how == "vanity":
            return f"Joined with the server's custom invite link (`discord.gg/{guild.vanity_url_code or 'vanity'}`)"
        if how == "bot":
            return f"Bot added by {who}" if inviter_id else "Bot added to the server"
        if how == "noperm":
            return "Can't tell how they joined (I need the **Manage Server** permission)"
        return "Could not tell how they joined (Discovery, Server Onboarding, or several people joined at once)"

    def _history_lines(self, entries, current, with_time=True):
        """Older entries of a name history (the current one left out), newest first."""
        old = [e for e in entries if e.get("v") != current]
        seen, lines = set(), []
        for e in reversed(old):
            if e["v"] in seen:
                continue
            seen.add(e["v"])
            lines.append(f"`{e['v']}`" + (f" ({_unix(e['t'], 'R')})" if with_time else ""))
        return lines

    async def _profile_embeds(self, member, how, inviter_id, code, info, prev, alt, user_before, auto=None, jailed=False):
        """The join log message: ONE clean embed (no color) with everything in it. -> [embed]"""
        guild = member.guild
        try:  # the banner and accent color are only in the full profile
            full = await self.bot.fetch_user(member.id)
        except discord.HTTPException:
            full = None
        accent = getattr(full, "accent_color", None) if full else None
        banner = getattr(full, "banner", None) if full else None
        badges = [_nice(n) for n, v in member.public_flags if v]

        # ------------------------------------------------ the top: status, who, how
        icon, headline = _status(alt, jailed, member.bot)
        top = (f"{icon} " if icon else "") + f"**{headline}**" + (f" (score {alt['score']})" if alt else "")
        top += f"\n{member.mention} (`{member.id}`)\n{self._how_text(guild, how, inviter_id, code)}"
        e = discord.Embed(title="Member joined" if not member.bot else "Bot joined", description=top, timestamp=discord.utils.utcnow())
        e.set_thumbnail(url=member.display_avatar.url)  # no name line on top: the mention below already shows the name
        if banner:
            e.set_image(url=banner.url)

        # ------------------------------------------------ the member
        names = [f"`{member.name}`"]
        if member.global_name and member.global_name != member.name:
            names.append(f"global `{member.global_name}`")
        if member.nick:
            names.append(f"nickname `{member.nick}`")
        if getattr(member, "discriminator", "0") not in ("0", "0000", None):
            names.append(f"`#{member.discriminator}`")
        e.add_field(name="Names", value=_clip(" \u00b7 ".join(names), 300), inline=True)
        e.add_field(name="Created", value=f"{_ts(member.created_at)} ({_age(member.created_at)} old)", inline=True)
        e.add_field(name="Joined", value=f"{_ts(member.joined_at)} (member **#{guild.member_count}**)", inline=True)

        links = [f"[Avatar]({member.display_avatar.url})"]
        if member.guild_avatar:
            links.append(f"[Server avatar]({member.guild_avatar.url})")
        if member.avatar:
            links.append(f"[Global avatar]({member.avatar.url})")
        if banner:
            links.append(f"[Banner]({banner.url})")
        deco = getattr(member, "avatar_decoration", None)
        if deco:
            links.append(f"[Decoration]({deco.url})")
        profile = [" \u00b7 ".join(links), "Avatar: " + ("**default (none set)**" if member.avatar is None else "custom")]
        if accent:
            profile.append(f"Accent `#{accent.value:06X}`")
        clan = getattr(member, "primary_guild", None)
        tag = getattr(clan, "tag", None) if clan else None
        if tag:
            profile.append(f"Server tag `{tag}`")
        e.add_field(name="Profile", value=_clip(" \u00b7 ".join(profile[:1]) + "\n" + " \u00b7 ".join(profile[1:]), 500), inline=False)

        if badges:
            e.add_field(name=f"Badges ({len(badges)})", value=_clip(", ".join(badges), 300), inline=False)
        flags = [_nice(n) for n, v in getattr(member, "flags", []) if v]
        if flags:
            e.add_field(name="Server member flags", value=_clip(", ".join(flags), 200), inline=False)
        roles = [r for r in member.roles if r.id != guild.id]
        if roles:
            e.add_field(name=f"Roles on join ({len(roles)})", value=_clip(" ".join(r.mention for r in reversed(roles)), 400), inline=False)
        status = []
        if member.pending:
            status.append("Pending membership screening")
        if member.timed_out_until:
            status.append(f"Timed out until {_ts(member.timed_out_until)}")
        if status:
            e.add_field(name="Status", value=" \u00b7 ".join(status), inline=False)

        # ------------------------------------------------ how they joined
        lines = []
        if how in ("invite", "guess"):
            if info:
                uses = f"{info['uses']}" + (f" / {info['max']}" if info.get("max") else " (unlimited)")
                bit = f"`{code}` \u00b7 uses {uses}"
                if info.get("channel"):
                    bit += f" \u00b7 <#{info['channel']}>"
                lines.append(bit)
                if info.get("created"):
                    lines.append(f"Created {_unix(info['created'], 'R')} \u00b7 expires " + (_unix(info["created"] + info["max_age"], "R") if info.get("max_age") else "never"))
                if info.get("temp"):
                    lines.append("Temporary membership (kicked when they go offline without a role)")
            else:
                lines.append(f"`{code}`")
            if inviter_id:
                inviter = await self._user(inviter_id)
                in_server = guild.get_member(inviter_id)
                bit = f"Inviter <@{inviter_id}>"
                if inviter:
                    bit += f" (`{inviter}`, account created {_ts(inviter.created_at, 'R')}"
                    bit += ", still in the server)" if in_server else ", **no longer in the server**)"
                stats = store.guild(guild.id)["inviters"].get(str(inviter_id), {"joins": 0, "left": 0})
                bit += f"\nTheir invites: **{stats['joins']}** joined \u00b7 **{stats['left']}** left \u00b7 **{stats['joins'] - stats['left']}** still here"
                lines.append(bit)
            else:
                lines.append("Inviter unknown (the invite has no owner)")
        elif how == "vanity":
            lines.append(f"https://discord.gg/{guild.vanity_url_code}" if guild.vanity_url_code else "Custom server URL")
            if info and info.get("uses") is not None:
                lines.append(f"Vanity uses: {info['uses']}")
        elif how == "bot":
            lines.append(f"Added by <@{inviter_id}> (`{inviter_id}`)" if inviter_id else "Added by unknown (needs View Audit Log)")
        else:
            lines.append(self._how_text(guild, how, None, None))
        method = {"invite": "Invite link", "guess": "Invite link (best guess)", "vanity": "Custom server URL", "bot": "Added bot",
                  "noperm": "Unknown (missing permission)", "unknown": "Unknown"}[how]
        e.add_field(name=f"How they joined: {method}", value=_clip("\n".join(lines), 700), inline=False)

        # ------------------------------------------------ alt check
        if alt and (alt["score"] >= 2 or alt["matches"]):
            e.add_field(name="Suggested action", value=alt["action"], inline=False)
            signs = [f"`+{p}` {t}" for p, t in alt["reasons"] if "<@" not in t][:6]
            if signs:
                e.add_field(name="Warning signs", value=_clip("\n".join(signs), 600), inline=False)
            if alt["matches"]:
                mlines = []
                for uid, m in alt["matches"][:4]:
                    label = "banned" if "banned" in m["tags"] else "left" if "left" in m["tags"] else "still here" if "here" in m["tags"] else "known"
                    mlines.append(f"<@{uid}> (`{uid}`, {label}): " + ", ".join(m["why"]))
                e.add_field(name="Might be the same person as", value=_clip("\n".join(mlines), 600), inline=False)
        if auto:
            e.add_field(name="Automatic actions", value=_clip("\n".join(auto), 450), inline=False)

        # ------------------------------------------------ name history and join history
        rec = store.user(member.id)
        old_names = self._history_lines(rec["names"], member.name)
        old_globals = self._history_lines(rec["globals"], member.global_name)
        old_nicks = self._history_lines(rec["nicks"].get(str(guild.id), []), member.nick)
        hist = []
        if old_names:
            hist.append(f"Former usernames ({len(old_names)}): " + ", ".join(old_names[:5]))
        if old_globals:
            hist.append(f"Former display names ({len(old_globals)}): " + ", ".join(old_globals[:5]))
        if old_nicks:
            hist.append(f"Former nicknames here ({len(old_nicks)}): " + ", ".join(old_nicks[:5]))
        known_before = bool(user_before.get("names"))
        hist.append(f"Avatars seen: {len(rec['avatars'])}" + (f" (hash `{member.avatar.key}`)" if member.avatar else "") + (" \u00b7 seen before" if known_before else " \u00b7 first time I see this account"))
        if prev:
            hist.append(f"Joined this server **{prev.get('joins', 1) + 1}** times (rejoin) \u00b7 first seen {_unix(prev.get('first') or prev.get('at'), 'R')} \u00b7 last joined {_unix(prev.get('at'), 'R')}"
                        + (f" \u00b7 last left {_unix(prev['last_left'], 'R')}" if prev.get("last_left") else ""))
            if prev.get("how") in ("invite", "guess") and prev.get("inviter"):
                hist.append(f"Last time came through <@{prev['inviter']}> (`{prev.get('code')}`)")
        else:
            hist.append("First time I have seen them join this server")
        e.add_field(name="Name and join history", value=_clip("\n".join(hist), 900), inline=False)

        # ------------------------------------------------ extras
        extra = []
        try:
            mutual = [g.name for g in member.mutual_guilds if g.id != guild.id]
            extra.append(f"Other servers with this bot: **{len(mutual)}**" + (f" ({_clip(', '.join(mutual[:5]), 120)})" if mutual else ""))
        except Exception:
            pass
        perms = [_nice(n) for n, v in member.guild_permissions if v and n in ("administrator", "manage_guild", "manage_roles", "manage_channels", "ban_members", "kick_members", "manage_messages", "mention_everyone", "moderate_members")]
        extra.append("Elevated permissions: " + (", ".join(perms) if perms else "none"))
        extra.append(f"Verification level: {_nice(str(guild.verification_level.name))}")
        e.add_field(name="More details", value=_clip("\n".join(extra), 500), inline=False)

        foot = f"User ID: {member.id}"
        if alt:
            foot += f" \u00b7 Hints, not proof. Compared with {alt['checked']}"
        e.set_footer(text=_clip(foot, 300))

        # A clean (Components V2) message holds about 3900 characters of text: drop the least important parts first if needed.
        for name in ("More details", "Name and join history", "Profile", "Server member flags", "Badges"):
            if _embed_chars(e) <= TEXT_BUDGET:
                break
            for i, f in enumerate(e.fields):
                if f.name.startswith(name):
                    e.remove_field(i)
                    break
        return [e]

    async def _notify(self, guild, embeds, view=None):
        channel_id = store.guild(guild.id).get("log")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            return
        chunks, cur, size = [], [], 0
        for e in embeds:  # one message holds at most 6000 characters of embeds
            n = len(e)
            if cur and size + n > 5500:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(e)
            size += n
        if cur:
            chunks.append(cur)
        for i, chunk in enumerate(chunks):
            buttons = view if (view is not None and i == 0) else discord.utils.MISSING  # the staff buttons go under the first message
            try:
                if len(chunk) == 1:
                    await channel.send(embed=chunk[0], view=buttons, allowed_mentions=discord.AllowedMentions.none())
                else:
                    await channel.send(embeds=chunk, view=buttons, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.info("Could not post the join profile in %s, trying one embed at a time", channel_id, exc_info=True)
                for j, e in enumerate(chunk):
                    try:
                        await channel.send(embed=e, view=buttons if j == 0 else discord.utils.MISSING, allowed_mentions=discord.AllowedMentions.none())
                    except discord.HTTPException:
                        log.info("Could not post an embed in %s", channel_id, exc_info=True)

    # ------------------------------------------------------------- commands
    @commands.hybrid_command(name="invitelog", description="Post a full profile + alt check in a channel every time someone joins (or off)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def invitelog(self, ctx, *, channel: Optional[str] = None):
        g = store.guild(ctx.guild.id)
        if channel is None:
            current = ctx.guild.get_channel(g["log"]) if g["log"] else None
            text = f"Join profiles go to {current.mention}. Change it with `invitelog #channel`, turn it off with `invitelog off`." if current \
                else "Join profiles are off. Pick a channel with `invitelog #channel`."
            return await ctx.send(embed=discord.Embed(description=text, color=_color()))
        if channel.strip().lower() in ("off", "none", "disable", "remove"):
            g["log"] = None
            store.save()
            return await ctx.send(embed=discord.Embed(description="Join profiles are off.", color=OK_COLOR))
        try:
            target = await commands.TextChannelConverter().convert(ctx, channel.strip())
        except commands.BadArgument:
            return await ctx.send(embed=discord.Embed(description="I couldn't find that channel. Use `invitelog #channel` or `invitelog off`.", color=WARN_COLOR))
        g["log"] = target.id
        store.save()
        await ctx.send(embed=discord.Embed(description=f"Join profiles will be posted in {target.mention}.", color=OK_COLOR))


    @commands.hybrid_command(name="joinguard", description="Security settings for new members: raid alert, quarantine role, auto timeout")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def joinguard(self, ctx, setting: Optional[str] = None, *, value: Optional[str] = None):
        cfg = store.guard(ctx.guild.id)
        key = (setting or "").strip().lower()
        value = (value or "").strip()
        low = value.lower()

        def done(text):
            store.save()
            return ctx.send(embed=discord.Embed(description=text, color=OK_COLOR))

        def bad(text):
            return ctx.send(embed=discord.Embed(description=text, color=WARN_COLOR))

        if key in ("raid",):
            if low not in ("off", "alert", "lockdown"):
                return await bad("Use `joinguard raid off`, `alert` or `lockdown`.")
            cfg["raid"] = low
            return await done(f"Raid protection: **{low}**.")
        if key in ("autotimeout", "earlywatch", "leavelog"):
            if low not in ("on", "off"):
                return await bad(f"Use `joinguard {key} on` or `off`.")
            cfg[key] = low == "on"
            return await done(f"`{key}` is now **{low}**.")
        if key == "quarantine":
            if low in ("off", "none", "remove", ""):
                cfg["quarantine"] = None
                return await done("Quarantine is **off**.")
            try:
                role = await commands.RoleConverter().convert(ctx, value)
            except commands.BadArgument:
                return await bad("I couldn't find that role. Use `joinguard quarantine @role` or `joinguard quarantine off`.")
            if role.managed or role.is_default():
                return await bad("Pick a normal role (not @everyone or a bot role).")
            me = ctx.guild.me
            if me is not None and role >= me.top_role:
                return await bad("That role is not below my highest role, so I couldn't give it. Move my role above it first.")
            cfg["quarantine"] = role.id
            return await done(f"New accounts under **{cfg['minage']}** day(s) old, and anyone with an alt score of 4 or more, get {role.mention} until staff press **Approve**. "
                              "Set that role up so it can only see a waiting room channel.")
        if key == "minage":
            if not low.isdigit() or not 0 <= int(low) <= 365:
                return await bad("Use `joinguard minage <days>` (0 to 365).")
            cfg["minage"] = int(low)
            return await done(f"Accounts younger than **{int(low)}** day(s) count as new for the quarantine.")

        role = ctx.guild.get_role(cfg["quarantine"]) if cfg.get("quarantine") else None
        log_ch = ctx.guild.get_channel(store.guild(ctx.guild.id).get("log") or 0)
        e = discord.Embed(title="Join guard", color=_color(), description=(
            f"Join profile channel: {log_ch.mention if log_ch else '**not set** (`invitelog #channel`)'}\n\n"
            f"`raid` **{cfg['raid']}**: off / alert / lockdown ({RAID_JOINS} joins in {RAID_WINDOW}s; lockdown raises the verification level for {RAID_LOCK_SECONDS // 60} min)\n"
            f"`quarantine` {role.mention if role else '**off**'}: role for new accounts and alt score 4+, until staff press Approve\n"
            f"`minage` **{cfg['minage']}** day(s): accounts younger than this count as new\n"
            f"`autotimeout` **{'on' if cfg['autotimeout'] else 'off'}**: {TIMEOUT_HOURS} hour timeout for a likely alt (score 7+)\n"
            f"`earlywatch` **{'on' if cfg['earlywatch'] else 'off'}**: alert on invite links, scam links and mass pings in a member's first hour\n"
            f"`leavelog` **{'on' if cfg['leavelog'] else 'off'}**: post who left, was kicked or banned\n\n"
            "Change one with e.g. `joinguard raid lockdown` or `joinguard quarantine @Unverified`."))
        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(InviteTracker(bot))
