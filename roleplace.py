"""Keeps the roles the bot makes (Top 1, Best family, family roles, custom roles) right under an anchor role.

Why it is its own file: the moving logic needs no Discord connection, so it can be tested with plain fake roles.

What was wrong before (see CHANGES.md, Step 48):
  * the Top 1 role was moved under Staff ONCE, when the bot created it, and with the role positions the bot had cached
    (those are often out of date right after a role was made or moved);
  * family roles only moved when a "base role" was set by hand;
  * the Top 1, Best family and family syncs could run at the same moment and undo each other's moves.
Now every move reads FRESH positions from Discord, runs under one lock per server, and is re-checked on a timer.
"""
import asyncio
import logging

log = logging.getLogger("roleplace")

# What a plan / move can come back as. REASONS turns the codes into a sentence for staff.
REASONS = {
    "missing": "the role or the anchor role doesn't exist any more",
    "same": "the role is the anchor role itself",
    "no room": "there is no room under the anchor role",
    "hierarchy": "my highest role has to be ABOVE the anchor role (drag my role up in Server Settings, Roles)",
    "forbidden": "Discord refused: I need the Manage Roles permission and my role must be above these roles",
    "failed": "Discord refused to move the role",
}


def plan_move(roles, mine_id, anchor_id, group_ids=(), limit=None):
    """Work out the moves that put role `mine_id` directly under role `anchor_id`.

    roles     any objects with .id and .position and is_default() (discord.Role or a fake)
    group_ids other roles that are allowed to sit between the role and the anchor (the bot's own roles that share this
              anchor: without this every role would fight for the single spot right under it)
    limit     the position of the bot's highest role: it can only move roles below it (None = don't check)

    Returns (positions, status). status is "ok" (already fine, positions is {}), "move" (positions is {role id: new
    position} for every role that changes) or one of the codes in REASONS (positions is None)."""
    by_id = {r.id: r for r in roles}
    mine, anchor = by_id.get(mine_id), by_id.get(anchor_id)
    if mine is None or anchor is None:
        return None, "missing"
    if mine.id == anchor.id:
        return None, "same"
    group = set(group_ids) | {mine_id}
    if mine.position < anchor.position:
        between = [r for r in by_id.values() if mine.position < r.position < anchor.position]
        if all(r.id in group for r in between):
            return {}, "ok"
        target = anchor.position - 1
    else:
        target = anchor.position
    if target < 1:
        return None, "no room"
    lo, hi = sorted((mine.position, target))
    if limit is not None and hi >= limit:
        return None, "hierarchy"
    between = sorted((r for r in by_id.values() if lo <= r.position <= hi and r.id != mine.id and not r.is_default()),
                     key=lambda r: (r.position, r.id))
    if mine.position > target:   # moving down: the role takes the lowest spot, the others shift up by one
        positions = {mine.id: lo}
        positions.update({r.id: lo + 1 + i for i, r in enumerate(between)})
    else:                        # moving up: the others shift down by one, the role takes the top spot
        positions = {r.id: lo + i for i, r in enumerate(between)}
        positions[mine.id] = hi
    return positions, "move"


_locks = {}


def role_lock(guild_id):
    """One lock per server: only one role move runs at a time, so two syncs can't undo each other."""
    lock = _locks.get(guild_id)
    if lock is None:
        lock = _locks[guild_id] = asyncio.Lock()
    return lock


def bot_top_position(guild, roles):
    """Position of the bot's highest role (read from `roles`, which are fresh), 0 when unknown."""
    mine = {r.id for r in guild.me.roles} if guild.me is not None else set()
    return max((r.position for r in roles if r.id in mine), default=0)


async def place_under(guild, role, anchor, group_ids=(), reason="Bot roles go under the anchor role", fresh=False):
    """Move `role` directly under `anchor`. Returns None when it is where it should be, else a short sentence saying why not.

    The positions cached by discord.py are checked first (free). Only when they say a move is needed, or fresh=True
    (the timer does this), are the real positions asked from Discord, because the cached ones go stale."""
    if role is None or anchor is None:
        return REASONS["missing"]
    async with role_lock(guild.id):
        if not fresh:
            try:
                _positions, status = plan_move(guild.roles, role.id, anchor.id, group_ids)
            except Exception:
                status = "move"
            if status == "ok":
                return None
        try:
            roles = list(await guild.fetch_roles())
        except Exception:
            roles = list(guild.roles)
        limit = bot_top_position(guild, roles)
        positions, status = plan_move(roles, role.id, anchor.id, group_ids, limit)
        if status == "ok":
            return None
        if status != "move":
            return REASONS.get(status, REASONS["failed"])
        by_id = {r.id: r for r in roles}
        try:
            if hasattr(guild, "edit_role_positions"):
                await guild.edit_role_positions({by_id[i]: p for i, p in positions.items() if i in by_id}, reason=reason)
            else:
                await role.edit(position=positions[role.id], reason=reason)
        except Exception as e:
            name = type(e).__name__
            log.info("Could not move role %s under %s in %s: %s", role.id, anchor.id, guild.id, name, exc_info=True)
            return REASONS["forbidden"] if name == "Forbidden" else REASONS["failed"]
    return None


def where(roles, role_id, anchor_id, group_ids=()):
    """For the .roles check: 'ok', 'above' (the role is above the anchor), 'below' (below it, but other roles sit in between)
    or 'missing'."""
    by_id = {r.id: r for r in roles}
    mine, anchor = by_id.get(role_id), by_id.get(anchor_id)
    if mine is None or anchor is None:
        return "missing"
    _positions, status = plan_move(roles, role_id, anchor_id, group_ids)
    if status == "ok":
        return "ok"
    return "above" if mine.position > anchor.position else "below"
