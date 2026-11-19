"""cleanbox.py - the "clean embed" look (Bleed style): no side line, one rounded box.

Why: a normal Discord embed always has a coloured bar on its left edge. Discord's newer "container" layout
(Components V2, discord.py 2.6+) has no bar when it gets no accent colour, and it can hold the buttons
inside the box (that is what Bleed's new cards are). This file draws every embed the bot sends as such a
container, so the whole bot gets the look without rewriting the ~250 places that build embeds.

How it works (all automatic, bot.py calls `install()` once):
  * send / reply / edit / interaction replies are wrapped. When a call carries an embed, the embed is drawn
    as a container instead:
        author            -> small grey line on top
        title             -> heading
        description       -> text
        thumbnail         -> picture on the right of the first text block
        fields            -> under a divider. A short inline field is one line  **Aliases:** n/a  (Bleed's help card);
                             any other field is a bold label with its value under it
        image             -> picture in the box
        footer/timestamp  -> small grey line
        buttons / menus   -> under a divider, INSIDE the box (an old-style discord.ui.View is moved in and
                             keeps working: it still decides who may press, what a timeout does, ...)
    The embed colour is ignored on purpose (that is the side line).
  * Anything that can't be a container is sent EXACTLY as before (the old embed): more than ~3900
    characters of text (Discord allows 4000 in a container message), a non-picture file next to the embed,
    buttons made of DynamicItems, ... .
  * If Discord ever refuses a container message, the same message is sent again as the old embed. Three such
    refusals in a row turn the clean look off until the bot restarts (the log says so).
  * A "ping line" (message text that is only @mentions, sent above an embed so those people get a notification) is dropped when the
    box already mentions the same person: the box's own mention pings them (the call's allowed_mentions still decides who). Mentions
    the box does not contain stay as a short line above it. If the normal-embed version has to be sent instead, the ping line is
    sent with it, as written. CLEAN_KEEP_PING_LINE=1 in .env keeps the line above the box always.
  * CLEAN_EMBEDS=0 in .env turns the whole thing off. Needs discord.py 2.6+ (requirements.txt says 2.7+).

Nothing in here needs a Discord connection: the pure parts (`embed_children`, `build_view`) can be tested
with a stand-in for discord.py (see CHANGES.md).
"""
import collections
import contextvars
import functools
import logging
import os
import re

import discord

log = logging.getLogger("cleanbox")

MISSING = discord.utils.MISSING

_V2_NAMES = ("LayoutView", "Container", "Section", "TextDisplay", "Thumbnail", "Separator", "ActionRow", "MediaGallery")
HAS_V2 = all(hasattr(discord.ui, n) for n in _V2_NAMES) and hasattr(discord, "MediaGalleryItem")

TEXT_LIMIT = 3900        # Discord: at most 4000 characters of text in one container message (a little room is kept)
MAX_FAILS = 3            # container sends refused by Discord in a row before the clean look turns itself off
REMEMBER = 400           # how many recent messages are remembered (so an edit of "only the buttons" can redraw the box)
SHORT_FIELD = 80         # an inline field with a one-line value up to this long is drawn as one `**Label:** value` line
ENABLED = False          # set by install()
# Status embeds (success / error / warning lines) keep their coloured side bar inside the clean box. Same colours as bot.py's
# OK_COLOR / ERR_COLOR / WARN_COLOR. CLEAN_STATUS_BAR=0 in .env gives them the plain clean look like everything else.
STATUS_BAR = os.getenv("CLEAN_STATUS_BAR", "1").strip().lower() not in ("0", "off", "false", "no")
STATUS_COLORS = {0x3BA55D, 0xD9534F, 0xFAA61A}
_fails = 0

# While the normal-embed version of a message is being sent again, the wrappers further down the call chain
# (Context.send -> Messageable.send, InteractionMessage.edit -> Webhook.edit_message, ...) must leave it alone.
_plain_now = contextvars.ContextVar("cleanbox_plain_now", default=False)

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

_MENTION = re.compile(r"<@!?(\d+)>")
_PING_LINE = re.compile(r"(?:\s|<@!?\d+>)*")  # text that is nothing but user mentions
KEEP_PING_LINE = os.getenv("CLEAN_KEEP_PING_LINE", "").strip().lower() in ("1", "on", "true", "yes")
_ITEM_NAMES = ("Button", "Select", "UserSelect", "RoleSelect", "MentionableSelect", "ChannelSelect")
_ITEM_CLASSES = tuple(getattr(discord.ui, n) for n in _ITEM_NAMES if hasattr(discord.ui, n))


class _Skip(Exception):
    """This message is not turned into a container: it is sent the way it was written."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _given(value):
    return value is not MISSING and value is not None


def _text_of(value):
    return str(value) if _given(value) and str(value) != "" else None


def _link(text, url):
    return f"[{text}]({url})" if url else str(text)


def _gallery(url):
    """A media gallery with one picture (a link, or `attachment://name` of the same message)."""
    item = discord.MediaGalleryItem(url)
    try:
        return discord.ui.MediaGallery(item)
    except TypeError:  # some discord.py versions want the items as one list
        return discord.ui.MediaGallery([item])


def _status_accent(embed):
    """The bar colour for a status line (text only, in the success / error / warning colour), else None (= no bar, the clean look)."""
    if not STATUS_BAR:
        return None
    value = getattr(getattr(embed, "color", None), "value", None)
    if value not in STATUS_COLORS:
        return None
    if embed.title or embed.fields or getattr(embed.image, "url", None) or getattr(embed.thumbnail, "url", None) \
            or getattr(embed.author, "name", None):
        return None  # a real card (title / fields / picture / author line) stays clean
    return discord.Colour(value)


def _container(children, accent):
    if accent is None:
        return discord.ui.Container(*children)  # no accent colour: that is what removes the side line
    try:
        return discord.ui.Container(*children, accent_colour=accent)
    except TypeError:  # older spelling
        return discord.ui.Container(*children, accent_color=accent)


class _Budget:
    """Counts the characters of every text block: a container message may hold TEXT_LIMIT in total."""

    def __init__(self):
        self.used = 0

    def text(self, value):
        value = str(value)
        self.used += len(value)
        if self.used > TEXT_LIMIT:
            raise _Skip("more text than one container message can hold")
        return discord.ui.TextDisplay(value)


# --------------------------------------------------------------------------
# Embed -> the children of one container
# --------------------------------------------------------------------------
def _fields_text(fields):
    """Embed fields as stacked text, the way Bleed's help cards read.
    A short inline field (`Aliases` / `n/a`) is one line:  **Aliases:** n/a  and neighbours sit directly under each other.
    Every other field is a bold label with its value under it, with a blank line around it."""
    out, prev_short = "", False
    for f in fields:
        name, value = str(f.get("name") or "").strip(), str(f.get("value") or "").strip()
        if not name and not value:
            continue
        short = bool(f.get("inline")) and bool(name) and bool(value) and "\n" not in value and len(value) <= SHORT_FIELD
        if short:
            piece = f"**{name}:** {value}"
        elif name and value:
            piece = f"**{name}**\n{value}"
        else:
            piece = f"**{name or value}**"
        if out:
            out += "\n" if (prev_short and short) else "\n\n"
        out += piece
        prev_short = short
    return out


def embed_children(embed, budget):
    """(the container children for one embed, the set of `attachment://` names the embed points at)."""
    data = embed.to_dict()
    refs = set()

    def note(url):
        if isinstance(url, str) and url.startswith("attachment://"):
            refs.add(url[len("attachment://"):])
        return url

    head = []
    author = data.get("author") or {}
    if author.get("name"):
        head.append("-# " + _link(author["name"], author.get("url")))
    if data.get("title"):
        head.append("### " + _link(data["title"], data.get("url")))
    if data.get("description"):
        head.append(str(data["description"]))
    head_text = "\n".join(head)

    body = _fields_text(data.get("fields") or [])

    foot = []
    footer = data.get("footer") or {}
    if footer.get("text"):
        foot.append(str(footer["text"]))
    stamp = getattr(embed, "timestamp", None)
    if stamp is not None:
        try:
            foot.append(f"<t:{int(stamp.timestamp())}:f>")
        except (AttributeError, OverflowError, OSError, ValueError):
            pass
    foot_text = "  ·  ".join(foot)

    thumb = note((data.get("thumbnail") or {}).get("url"))
    image = note((data.get("image") or {}).get("url"))

    children = []
    if thumb and (head_text or body):  # the small picture sits on the right of the first text block, like an embed thumbnail
        carry = head_text or body
        children.append(discord.ui.Section(budget.text(carry), accessory=discord.ui.Thumbnail(thumb)))
        if not head_text:
            body = ""
    elif head_text:
        children.append(budget.text(head_text))
    elif thumb:  # nothing but a thumbnail: show it as a normal picture
        children.append(_gallery(thumb))
    if body:
        if head_text:
            children.append(discord.ui.Separator())
        children.append(budget.text(body))
    if image:
        children.append(_gallery(image))
    if foot_text:
        children.append(budget.text("-# " + foot_text))
    return children, refs


# --------------------------------------------------------------------------
# Old-style View -> rows of buttons / menus inside the container
# --------------------------------------------------------------------------
def _plain_item(item):
    """A normal button / menu, or a DynamicItem that wraps one (the join log's staff buttons, which keep working after a restart)."""
    if isinstance(item, _ITEM_CLASSES):
        return True
    dynamic = getattr(discord.ui, "DynamicItem", None)
    return dynamic is not None and isinstance(item, dynamic) and isinstance(getattr(item, "item", None), _ITEM_CLASSES)


def _legacy_ok(view):
    return (isinstance(view, discord.ui.View) and not isinstance(view, discord.ui.LayoutView)
            and all(_plain_item(item) for item in view.children))


def _width(item):
    width = getattr(item, "width", None)
    if isinstance(width, int) and width > 0:
        return width
    return 1 if isinstance(item, discord.ui.Button) else 5


def _pack(items):
    """The rows a legacy View shows: the row discord.py gave each item, and items without one fill the first row with room."""
    placed, free = {}, []
    for item in items:
        row = getattr(item, "_rendered_row", None)
        if row is None:
            row = getattr(item, "row", None)
        if isinstance(row, int):
            placed.setdefault(row, []).append(item)
        else:
            free.append(item)
    rows = [placed[k] for k in sorted(placed)]
    weights = [sum(_width(i) for i in r) for r in rows]
    for item in free:
        w = _width(item)
        for n, row in enumerate(rows):
            if weights[n] + w <= 5 and len(row) < 5:
                row.append(item)
                weights[n] += w
                break
        else:
            rows.append([item])
            weights.append(w)
    if len(rows) > 5:
        raise _Skip("more than 5 rows of buttons")
    return rows


def _detach(item):
    """Take a button out of the box it was in before (a legacy View is drawn again on every edit)."""
    parent = getattr(item, "_parent", None)
    if parent is not None and hasattr(parent, "remove_item"):
        try:
            parent.remove_item(item)
        except Exception:
            pass


def _legacy_rows(view):
    rows = []
    for items in _pack(list(view.children)):
        row = discord.ui.ActionRow()
        for item in items:
            _detach(item)
            row.add_item(item)
        rows.append(row)
    return rows


if HAS_V2:
    class CleanView(discord.ui.LayoutView):
        """The LayoutView that carries a clean box. When it carries an old-style View's buttons it hands everything that
        is decided per view (who may press, errors, the timeout, stop()) to that View, so those keep behaving the same."""

        def __init__(self, legacy=None):
            super().__init__(timeout=(legacy.timeout if legacy is not None else None))
            self._legacy = legacy
            self._superseded = False  # a newer drawing of the same legacy View exists: this one's timeout must stay silent

        def __getattr__(self, name):  # only called when the normal lookup fails: let code that reads view.something find it on the old View
            legacy = self.__dict__.get("_legacy")
            if legacy is None or name.startswith("__"):
                raise AttributeError(name)
            return getattr(legacy, name)

        async def interaction_check(self, interaction):
            legacy = self._legacy
            if legacy is None:
                return True
            return await legacy.interaction_check(interaction)

        async def on_error(self, interaction, error, item):
            legacy = self._legacy
            if legacy is None:
                return await super().on_error(interaction, error, item)
            return await legacy.on_error(interaction, error, item)

        async def on_timeout(self):
            legacy = self._legacy
            if legacy is None or self._superseded:
                return
            try:  # marks the old View as timed out (its wait() returns) and runs its on_timeout in a task
                legacy._dispatch_timeout()
            except Exception:
                await legacy.on_timeout()


def _link_legacy(legacy, clean):
    """Make `clean` the current drawing of `legacy`: stop() on the old View also stops the drawing, the previous drawing goes quiet."""
    previous = legacy.__dict__.get("_cleanbox_view")
    if previous is not None and previous is not clean:
        previous._superseded = True
    legacy._cleanbox_view = clean
    if not legacy.__dict__.get("_cleanbox_stop"):
        original = legacy.stop

        def stop():
            original()
            current = legacy.__dict__.get("_cleanbox_view")
            if current is not None:
                current.stop()

        legacy.stop = stop
        legacy._cleanbox_stop = True


# --------------------------------------------------------------------------
# The whole message
# --------------------------------------------------------------------------
def build_view(embeds, content=None, legacy=None, file_names=()):
    """A CleanView with one container per embed (the buttons of `legacy`, if any, inside the last one).
    Raises _Skip when this message can't be a clean container."""
    embeds = [e for e in (embeds or ()) if e is not None][:10]
    if not embeds:
        raise _Skip("no embed")
    if legacy is not None and not _legacy_ok(legacy):
        raise _Skip("buttons that can't move into a container")
    budget = _Budget()
    view = CleanView(legacy)
    if content:  # text written next to the embed stays above the box (a mention in it still pings if the call allowed it)
        view.add_item(budget.text(content))
    boxes, refs, accents = [], set(), []
    for embed in embeds:
        children, found = embed_children(embed, budget)
        boxes.append(children)
        accents.append(_status_accent(embed))
        refs |= found
    for name in file_names:  # a file the embed does not point at would be hidden in a container message
        if name in refs:
            continue
        if not str(name).lower().endswith(_IMAGE_EXTS):
            raise _Skip("a file that is not a picture")
        boxes[-1].append(_gallery(f"attachment://{name}"))
    if legacy is not None and legacy.children:
        if boxes[-1]:
            boxes[-1].append(discord.ui.Separator())
        boxes[-1].extend(_legacy_rows(legacy))
    for children, accent in zip(boxes, accents):
        if not children:
            raise _Skip("an embed with nothing to show")
        view.add_item(_container(children, accent))
    if legacy is not None:
        _link_legacy(legacy, view)
    return view


# --------------------------------------------------------------------------
# Remembering what was drawn (so "remove the buttons" / "disable the buttons" can redraw the same box)
# --------------------------------------------------------------------------
class _Shown:
    __slots__ = ("embeds", "content", "legacy")

    def __init__(self, embeds, content, legacy):
        self.embeds = [e.copy() for e in embeds]
        self.content = content
        self.legacy = legacy


_SHOWN = collections.OrderedDict()


def _remember(message_id, embeds, content, legacy):
    if not isinstance(message_id, int):
        return
    _SHOWN[message_id] = _Shown(embeds, content, legacy)
    _SHOWN.move_to_end(message_id)
    while len(_SHOWN) > REMEMBER:
        _SHOWN.popitem(last=False)


def _id_of(result):
    """The message id inside what a send returned (a Message, or an interaction response holding one)."""
    value = getattr(result, "id", None)
    if isinstance(value, int):
        return value
    inner = getattr(result, "resource", None)
    value = getattr(inner, "id", None)
    return value if isinstance(value, int) else None


def _is_v2(message):
    return bool(getattr(getattr(message, "flags", None), "components_v2", False))


# --------------------------------------------------------------------------
# Reading a call
# --------------------------------------------------------------------------
def _embeds_of(kwargs):
    out = []
    if _given(kwargs.get("embed", MISSING)):
        out.append(kwargs["embed"])
    many = kwargs.get("embeds", MISSING)
    if _given(many):
        out.extend(many)
    return out


def _file_names(kwargs):
    names = []
    for key in ("file", "files", "attachments"):
        value = kwargs.get(key, MISSING)
        if not _given(value):
            continue
        for f in (value if isinstance(value, (list, tuple)) else [value]):
            if isinstance(f, discord.File):
                names.append(f.filename)
    return names


def _rewind(kwargs):
    """Files are used up by a send: before the same message is sent again they have to be read from the start."""
    for key in ("file", "files", "attachments"):
        value = kwargs.get(key, MISSING)
        if not _given(value):
            continue
        for f in (value if isinstance(value, (list, tuple)) else [value]):
            if isinstance(f, discord.File):
                try:
                    f.reset(seek=True)
                except Exception:
                    pass


def _without_repeated_pings(content, embeds):
    """The message text of a send, minus a ping line that the box already says. Only text made of nothing but user @mentions counts as a
    ping line; any real text next to the embed is left alone. Mentions that are not inside the embeds stay (as one short line)."""
    if not content or KEEP_PING_LINE or _PING_LINE.fullmatch(content) is None:
        return content
    inside = set()
    for embed in embeds:
        inside.update(_MENTION.findall(str(embed.to_dict())))
    left = [m.group(0) for m in _MENTION.finditer(content) if m.group(1) not in inside]
    return " ".join(dict.fromkeys(left)) or None


def _plan_send(args, kwargs):
    """(new args, new kwargs, what to remember) for a send that carries an embed, or None to send it unchanged."""
    embeds = _embeds_of(kwargs)
    if not embeds or len(args) > 1 or (args and "content" in kwargs):
        return None
    for key in ("poll", "stickers", "suppress_embeds", "tts"):  # a container message can't carry these: send it as it was written
        value = kwargs.get(key, MISSING)
        if _given(value) and value:
            return None
    content = _text_of(args[0] if args else kwargs.get("content", MISSING))
    view = kwargs.get("view", MISSING)
    legacy = view if _given(view) else None
    if legacy is not None and not isinstance(view, discord.ui.View):
        return None  # a LayoutView next to an embed is not something Discord accepts anyway
    content = _without_repeated_pings(content, embeds)  # the box mentions them already: no second @mention above it
    try:
        clean = build_view(embeds, content, legacy, _file_names(kwargs))
    except _Skip as why:
        log.debug("sent as a normal embed: %s", why)
        return None
    except Exception:
        log.warning("Could not draw a clean box, sending the normal embed", exc_info=True)
        return None
    new = {k: v for k, v in kwargs.items() if k not in ("content", "embed", "embeds", "view")}
    new["view"] = clean
    return (), new, (embeds, content, legacy)


def _plan_edit(message, message_id, kwargs):
    """(new kwargs, what to remember) for an edit that changes the embed or only the buttons of a clean box; None = unchanged."""
    embeds = _embeds_of(kwargs)
    view = kwargs.get("view", MISSING)
    known = _SHOWN.get(message_id) if message_id is not None else None
    is_v2 = _is_v2(message) or known is not None
    new = dict(kwargs)
    if embeds:
        if _given(view):
            if not isinstance(view, discord.ui.View):
                return None
            legacy = view
        elif view is None:
            legacy = None
        else:  # no view in the call: whatever buttons the message has stay
            legacy = known.legacy if known is not None else None
        content = _text_of(kwargs.get("content", MISSING))
        try:
            clean = build_view(embeds, content, legacy, _file_names(kwargs))
        except _Skip as why:
            log.debug("edited as a normal embed: %s", why)
            return None
        except Exception:
            log.warning("Could not draw a clean box, editing with the normal embed", exc_info=True)
            return None
        for key in ("content", "embed", "embeds", "view"):
            new.pop(key, None)
        new["view"] = clean
        if not is_v2:  # a normal message turns into a container one only if it is emptied in the same edit
            new["content"] = None
            new["embed"] = None
        return new, (embeds, content, legacy)
    # Only the buttons change (remove them, or the same View with its buttons disabled) on a box we drew earlier.
    if is_v2 and known is not None and "view" in kwargs and not _given(kwargs.get("content", MISSING)):
        legacy = view if _given(view) else None
        if _given(view) and not isinstance(view, discord.ui.View):
            return None
        try:
            clean = build_view(known.embeds, known.content, legacy, ())
        except Exception as why:
            log.debug("could not redraw the box: %s", why)
            return None
        new["view"] = clean
        return new, (known.embeds, known.content, legacy)
    return None


async def _again_plain(original, self, args, kwargs):
    """Run the original call with every embed conversion switched off for the duration of the call."""
    token = _plain_now.set(True)
    try:
        return await original(self, *args, **kwargs)
    finally:
        _plain_now.reset(token)


def _fell_back(exc):
    """Was this the sign that Discord / discord.py did not take the container version of the message?"""
    if isinstance(exc, (TypeError, ValueError)):
        return True
    return isinstance(exc, discord.HTTPException) and getattr(exc, "status", None) == 400


def _good():
    global _fails
    _fails = 0


def _bad(where, exc):
    """A container send was refused and the normal embed worked: count it, and give up on containers after MAX_FAILS in a row."""
    global ENABLED, _fails
    _fails += 1
    log.warning("Container message refused in %s (%s), sent as a normal embed instead", where, exc)
    if _fails >= MAX_FAILS:
        ENABLED = False
        log.error("Discord refused %s container messages in a row: clean embeds are OFF until the bot restarts. "
                  "Check discord.py is 2.6 or newer, or set CLEAN_EMBEDS=0 to silence this.", _fails)


# --------------------------------------------------------------------------
# The wrappers
# --------------------------------------------------------------------------
def _wrap_send(original, where, applies=None):
    @functools.wraps(original)
    async def send(self, *args, **kwargs):
        plan = None
        if ENABLED and not _plain_now.get() and (applies is None or applies(self)):
            try:
                plan = _plan_send(args, kwargs)
            except Exception:
                log.warning("cleanbox could not read a %s call", where, exc_info=True)
        if plan is None:
            return await original(self, *args, **kwargs)
        new_args, new_kwargs, shown = plan
        try:
            result = await original(self, *new_args, **new_kwargs)
        except Exception as exc:
            if not _fell_back(exc):
                raise
            _rewind(kwargs)
            result = await _again_plain(original, self, args, kwargs)  # the normal embed; if this fails too, that is the real error
            _bad(where, exc)
            return result
        _good()
        try:
            _remember(_id_of(result), *shown)
        except Exception:
            pass
        return result

    send._cleanbox = True
    return send


def _wrap_edit(original, where, target, applies=None):
    """`target(self, args, kwargs)` -> (the Message if there is one, the message id or None)."""

    @functools.wraps(original)
    async def edit(self, *args, **kwargs):
        plan = None
        message_id = None
        if ENABLED and not _plain_now.get() and (applies is None or applies(self)):
            try:
                message, message_id = target(self, args, kwargs)
                plan = _plan_edit(message, message_id, kwargs)
            except Exception:
                log.warning("cleanbox could not read a %s call", where, exc_info=True)
        if plan is None:
            return await original(self, *args, **kwargs)
        new_kwargs, shown = plan
        try:
            result = await original(self, *args, **new_kwargs)
        except Exception as exc:
            if not _fell_back(exc):
                raise
            _rewind(kwargs)
            return await _again_plain(original, self, args, kwargs)  # edits do not count towards turning the look off (old messages can be refused)
        try:
            _remember(message_id if message_id is not None else _id_of(result), *shown)
        except Exception:
            pass
        return result

    edit._cleanbox = True
    return edit


def _t_message(self, args, kwargs):
    return (self if hasattr(self, "flags") else None), getattr(self, "id", None)


def _t_response(self, args, kwargs):
    message = getattr(getattr(self, "_parent", None), "message", None)
    return message, getattr(message, "id", None)


def _t_original(self, args, kwargs):
    message = getattr(self, "message", None)
    return message, getattr(message, "id", None)


def _t_webhook(self, args, kwargs):
    mid = args[0] if args else kwargs.get("message_id")
    return None, (mid if isinstance(mid, int) else None)


def _is_interaction_webhook(webhook):
    """Only the follow-up webhook of a slash command / button is wrapped, never the bot's own channel webhooks (the wordfilter reposts)."""
    kind = getattr(webhook, "type", None)
    return kind is not None and getattr(kind, "name", "") == "application"


def install():
    """Wrap discord.py's send / edit calls. Safe to call twice. Returns True when the clean look is on."""
    global ENABLED
    raw = os.getenv("CLEAN_EMBEDS", "").strip().lower()
    if not HAS_V2:
        log.warning("Clean embeds need discord.py 2.6 or newer (containers): leaving the normal embeds as they are")
        ENABLED = False
        return False
    if raw in ("0", "off", "false", "no"):
        log.info("Clean embeds are off (CLEAN_EMBEDS=%s)", raw)
        ENABLED = False
        return False
    from discord.ext import commands

    g = lambda name: getattr(discord, name, None)  # noqa: E731 (a class a future discord.py might rename is just skipped)
    # (class, method, name for the log, "send" or "edit", how to find the message of an edit, only-when check)
    jobs = [
        (g("abc") and discord.abc.Messageable, "send", "send", "send", None, None),
        (commands.Context, "send", "ctx.send", "send", None, None),
        (g("InteractionResponse"), "send_message", "response.send_message", "send", None, None),
        (g("Webhook"), "send", "followup.send", "send", None, _is_interaction_webhook),
        (g("InteractionResponse"), "edit_message", "response.edit_message", "edit", _t_response, None),
        (g("Interaction"), "edit_original_response", "edit_original_response", "edit", _t_original, None),
        (g("Webhook"), "edit_message", "followup.edit_message", "edit", _t_webhook, _is_interaction_webhook),
        (g("Message"), "edit", "message.edit", "edit", _t_message, None),
        (g("PartialMessage"), "edit", "partial message.edit", "edit", _t_message, None),
        (g("WebhookMessage"), "edit", "webhook message.edit", "edit", _t_message, None),
        (g("InteractionMessage"), "edit", "interaction message.edit", "edit", _t_message, None),
    ]
    done = 0
    for cls, name, where, kind, target, applies in jobs:
        if cls is None:
            continue
        current = cls.__dict__.get(name)
        if current is None or getattr(current, "_cleanbox", False):
            continue  # not defined on this class (the parent's one is wrapped), or already wrapped
        wrapped = _wrap_send(current, where, applies) if kind == "send" else _wrap_edit(current, where, target, applies)
        setattr(cls, name, wrapped)
        done += 1
    ENABLED = True
    log.info("Clean embeds are ON: embeds are drawn as containers without a side line (%d calls wrapped)", done)
    return True
