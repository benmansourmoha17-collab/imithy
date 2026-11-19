"""The rules of the reputation shop that need no Discord: name checks, streak freezes, bounties.

Why it is a separate file: it can be tested on its own, and the rules live in ONE place so the commands can't disagree with them.
"""
import unicodedata

NAME_BAD_PARTS = ("@everyone", "@here", "<@", "http", "www.", ".gg", "discord.", "```")


def clean_name(text, max_len, reserved_keys=(), key_fn=None):
    """A role name from a member. Returns (name, None) or (None, what is wrong in one sentence).
    reserved_keys: plain names nobody may take (staff ranks, Staff, Soul, the roles that already exist...), compared through key_fn."""
    text = "".join(ch for ch in str(text or "") if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    text = " ".join(text.split())
    if not text:
        return None, "Write a name."
    if len(text) > max_len:
        return None, f"A name can have at most {max_len} characters."
    low = text.lower()
    if any(bad in low for bad in NAME_BAD_PARTS):
        return None, "No mentions or links in a name."
    if key_fn is not None and key_fn(text) in {key_fn(k) for k in reserved_keys}:
        return None, "That name is taken or reserved. Pick another one."
    return text, None


def streak_after(last_day, today, streak, freezes):
    """The daily streak when someone claims on `today`.
    last_day is the date of the last claim (or None), freezes how many Streak Freezes they hold. Every missed day uses one
    freeze; with too few the streak starts again at 1. Returns (new streak, freezes used)."""
    if last_day is None:
        return 1, 0
    gap = (today - last_day).days
    if gap <= 0:
        return max(1, streak), 0
    if gap == 1:
        return streak + 1, 0
    missed = gap - 1
    if streak > 0 and 0 < missed <= freezes:
        return streak + 1, missed
    return 1, 0


def boost_extra(total, cap):
    """What a Quest Boost adds to a claim: the same again, but never more than `cap`."""
    return max(0, min(int(total), int(cap)))


# ---- bounties: [[who put it there, amount, unix time], ...] on the person it is for -----------------------------
def split_bounty(entries, now, ttl):
    """(entries still running, entries that ran out). Anything that isn't a well-formed entry is dropped."""
    live, expired = [], []
    for e in entries or []:
        try:
            who, amount, at = int(e[0]), int(e[1]), float(e[2])
        except (TypeError, ValueError, IndexError):
            continue
        if amount < 1:
            continue
        (expired if now - at >= ttl else live).append([who, amount, at])
    return live, expired


def bounty_total(entries):
    return sum(int(e[1]) for e in entries or [])


def bounty_fee(amount, percent):
    """The fee on top of a bounty, rounded up, at least 1 when there is a percentage."""
    if percent <= 0:
        return 0
    return max(1, -(-int(amount) * int(percent) // 100))
