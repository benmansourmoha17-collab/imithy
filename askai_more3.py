"""
askai_more3.py  -  third batch of ,assist tools (v22). Loaded by askai.py right after askai_more2.py.

  calculate      exact maths (an AI is bad at arithmetic): + - * / // % **, brackets, sqrt, round, log, sin, factorial, min, max...
  date_math      days between two dates, a date plus / minus days / weeks / months / years, the weekday of a date, how old something is
  convert_units  length, weight, volume, temperature, speed, area, data size, time (offline; money is in `lookup`)
  random_pick    dice (2d6), a coin, a number, one or more of a list of options, a random member (giveaways)
  word_stats     how many times a word or a short phrase was said in the saved messages: total, per person, per channel
                 (same rules as the ,wordstats command: whole words, no deleted messages, no bot commands)
  chat_stats     who talks the most, the busiest channels, activity by hour / by day (admins)
  bot_help       what a command of THIS bot does and how to type it (read live from the +help list, so it is never out of date)

Nothing here changes anything on the server, and nothing needs a new key or a new package.
"""
import ast
import asyncio
import calendar
import datetime
import logging
import math
import operator
import pathlib
import random
import re
import sqlite3
import sys
import time
from decimal import Decimal, ROUND_HALF_UP

from askai_more import S, N, B, U, CH, LIST, _enum

log = logging.getLogger("wordfilter")

UTILITY = {"calculate", "date_math", "convert_units", "random_pick", "bot_help"}  # everybody may use these (random_pick can't pick a random member for normal members)
CORE = {"calculate", "bot_help"}  # always offered to the AI, even when the tool router narrows the list down

PROMPT = (
    " MORE TOOLS (maths, dates, units, stats, help): calculate (ALWAYS use it for any arithmetic, percentages, averages, powers or roots: never calculate in your head), "
    "date_math (days between two dates, 'how many days until X', a date plus N days / weeks / months, the weekday of a date, how old something is: ALWAYS use it, never count days yourself; "
    "it needs real dates, so look the date up or ask first), convert_units (km / miles, kg / lb, C / F, GB / MB, hours / minutes...; for money use lookup(currency)), "
    "random_pick (dice like 2d6, a coin flip, a random number, one or more of a list of options, or a random member / giveaway winner: ALWAYS use it instead of 'picking randomly' yourself), "
    "word_stats (how many times a word or a short phrase was said in the saved messages: total, per person, per channel. Use it for 'how many times did X say Y' and 'who says Y the most'; "
    "it counts whole words and skips deleted messages and bot commands. Use search_messages instead when you need to SEE the messages), "
    "chat_stats (admins: who talks the most, the busiest channels, activity by hour or by day in a time window), "
    "bot_help (what a command of THIS bot does and exactly how to type it: ALWAYS call it before explaining a command, never guess one). "
)
PUBLIC_PROMPT = ""  # filled in by build(): depends on whether normal members may search the message history

ROUTE_GROUPS = [
    (r"how many days|days (until|till|since|left|from|between|ago)|\bweeks?\b|\bmonths?\b|how old|\bage\b|birthday|weekday|what day|which day|\bdate\b|until|countdown|\bago\b|from now",
     {"date_math", "current_time", "web_search"}),
    (r"convert|how many (km|kilometers?|miles?|meters?|metres?|cm|inch|inches|feet|foot|ft|kg|kilos?|pounds?|lbs?|grams?|liters?|litres?|gallons?|ml|mb|gb|tb|kb|bytes?|hours?|minutes?|seconds?)\b|"
     r"\b(in|to|into) (km|kilometers?|miles?|meters?|cm|inch|inches|feet|ft|kg|pounds?|lbs?|grams?|celsius|fahrenheit|kelvin|liters?|gallons?|mph|km/h|mb|gb|tb|kb|bytes?|minutes?|hours?)\b|celsius|fahrenheit|kelvin|\bmph\b|km/h",
     {"convert_units", "calculate"}),
    (r"random|\bpick\b|choose|dice|\broll\b|coin|flip|giveaway|winner|\bdraw\b|lottery|raffle|\bd\d+\b|heads or tails|who should",
     {"random_pick", "find_members"}),
    (r"how many times|how often|who (says?|said|use[sd]?|types?|spams?).{0,40}(most|more|the)|said the word|say the word|\bword\b|mention(ed)? .{1,30} (times|how)|count (of )?(the )?word|times (did|has|have)",
     {"word_stats", "search_messages", "member_info"}),
    (r"most active|top (chatters?|talkers?|posters?|members?)|who (talks|chats|posts|types|spams|is active)|busiest|quietest|dead (channel|chat)|activity|messages? (per|a|each) (day|hour)|peak hours?|chat stats|how active|message count|how many messages",
     {"chat_stats", "search_messages", "find_members", "server_info"}),
]


# --------------------------------------------------------------------------------------------- calculate
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _round_half_up(x, nd=0):
    """round(2.5) is 3, not 2 (what people expect), and round(1.005, 2) is 1.01."""
    nd = int(nd)
    q = Decimal(1).scaleb(-nd)
    r = Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP)
    return int(r) if nd <= 0 else float(r)


def _factorial(n):
    if not isinstance(n, int) or n < 0 or n > 1000:
        raise ValueError("factorial needs a whole number from 0 to 1000")
    return math.factorial(n)


_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": _round_half_up, "floor": math.floor, "ceil": math.ceil, "log": math.log, "ln": math.log, "log10": math.log10,
          "log2": math.log2, "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos, "atan": math.atan,
          "degrees": math.degrees, "radians": math.radians, "factorial": _factorial, "gcd": math.gcd, "min": min, "max": max}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _check_pow(a, b):
    if abs(b) > 10000:
        raise ValueError("that exponent is too big")
    if isinstance(a, int) and isinstance(b, int) and b > 0 and abs(a) > 1 and b * math.log10(abs(a)) > 400:
        raise ValueError("that number would be far too huge to write out")


def calc_eval(expr):
    """The value of a maths expression. Only numbers, + - * / // % **, brackets, the functions in _FUNCS and the constants pi / e / tau are allowed
    (it is parsed, never run as Python, so nothing else can be reached). Raises ValueError with a readable reason."""
    expr = str(expr or "").strip().replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    if not expr:
        raise ValueError("give an expression, like 15 / 100 * 80")
    if len(expr) > 300:
        raise ValueError("that expression is too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        raise ValueError("I can't read that expression: use numbers, + - * / **, brackets and functions like sqrt(16) (no % for percent: write 0.15 * 80)")
    if sum(1 for _ in ast.walk(tree)) > 250:
        raise ValueError("that expression is too complicated")

    def ev(n, depth=0):
        if depth > 60:
            raise ValueError("that expression is nested too deeply")
        if isinstance(n, ast.Expression):
            return ev(n.body, depth + 1)
        if isinstance(n, ast.Constant):
            if isinstance(n.value, bool) or not isinstance(n.value, (int, float)):
                raise ValueError("only numbers are allowed")
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = ev(n.left, depth + 1), ev(n.right, depth + 1)
            if isinstance(n.op, ast.Pow):
                _check_pow(a, b)
            r = _BIN[type(n.op)](a, b)
            if isinstance(r, complex):
                raise ValueError("that has no real result")
            return r
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY:
            return _UNARY[type(n.op)](ev(n.operand, depth + 1))
        if isinstance(n, ast.Name):
            if n.id in _CONSTS:
                return _CONSTS[n.id]
            raise ValueError(f"I don't know '{n.id}' (allowed names: pi, e, tau)")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            if not n.args:
                raise ValueError(f"{n.func.id}() needs a number")
            return _FUNCS[n.func.id](*[ev(x, depth + 1) for x in n.args])
        raise ValueError("that is not plain maths (allowed functions: " + ", ".join(sorted(_FUNCS)) + ")")

    try:
        v = ev(tree)
    except ZeroDivisionError:
        raise ValueError("division by zero")
    except OverflowError:
        raise ValueError("that number is too big")
    except RecursionError:
        raise ValueError("that expression is nested too deeply")
    except (TypeError, ArithmeticError) as e:
        raise ValueError(f"that doesn't work ({e})")
    if isinstance(v, complex):
        raise ValueError("that has no real result")
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        raise ValueError("that has no finite result")
    return v


def calc_present(v):
    """{'result': ..., 'pretty': ...}: exact whole numbers stay whole, floats lose the 0.1 + 0.2 noise, big numbers get thousands separators."""
    if isinstance(v, float):
        r = float(f"{v:.12g}")
        if r.is_integer() and abs(r) < 1e15:
            v = int(r)
        else:
            return {"result": r, "pretty": f"{r:,}"}
    if isinstance(v, int):
        s = str(abs(v))
        if len(s) > 60:
            return {"result": f"{v:.6e}", "pretty": f"about {v:.6e} ({len(s)} digits)"}
        return {"result": v if len(s) <= 15 else str(v), "pretty": f"{v:,}"}
    return {"result": v}


# --------------------------------------------------------------------------------------------- date_math
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y")


def parse_date(text, today):
    """A date from 'today' / 'tomorrow' / 'yesterday', ISO (2027-03-01, also with a time), or a written date. 01/03/2027 is day / month / year."""
    t = str(text or "").strip()
    low = t.lower()
    if low in ("", "today", "now"):
        return today
    if low == "tomorrow":
        return today + datetime.timedelta(days=1)
    if low == "yesterday":
        return today - datetime.timedelta(days=1)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$", t)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            raise ValueError(f"'{t}' is not a real date")
    t = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", t, flags=re.I).replace("  ", " ")
    for f in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(t, f).date()
        except ValueError:
            continue
    raise ValueError(f"I can't read the date '{text}': write it as YYYY-MM-DD")


def add_months(d, n):
    y, m = divmod(d.month - 1 + int(n), 12)
    year, month = d.year + y, m + 1
    if not 1 <= year <= 9999:
        raise ValueError("that date is out of range")
    return datetime.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def span_ymd(a, b):
    """(years, months, days) from a to b, for a <= b (months are calendar months, the day is clamped at the end of a short month)."""
    months = (b.year - a.year) * 12 + b.month - a.month
    if add_months(a, months) > b:
        months -= 1
    days = (b - add_months(a, months)).days
    return months // 12, months % 12, days


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _ymd_text(y, m, d):
    parts = [_plural(n, w) for n, w in ((y, "year"), (m, "month"), (d, "day")) if n]
    return ", ".join(parts) or "0 days"


def date_calc(op, date=None, other=None, amount=None, unit="days", today=None):
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    a = parse_date(date, today)
    if op in ("diff", "age"):
        b = parse_date(other, today)
        lo, hi = (a, b) if a <= b else (b, a)
        days = (hi - lo).days
        y, mo, d = span_ymd(lo, hi)
        rel = "the same day as" if a == b else (f"{_plural(days, 'day')} after" if a > b else f"{_plural(days, 'day')} before")
        return {"from": a.isoformat(), "to": b.isoformat(), "days_apart": days, "weeks_and_days": f"{days // 7} weeks, {days % 7} days",
                "calendar_span": _ymd_text(y, mo, d), "meaning": f"{a.isoformat()} is {rel} {b.isoformat()}" + (" (today)" if b == today and other in (None, "", "today", "now") else "")}
    if op == "add":
        if amount is None:
            raise ValueError("say how much to add (amount + unit)")
        n = int(amount)
        unit = str(unit or "days").lower().rstrip("s")
        try:
            if unit in ("day", "d"):
                r = a + datetime.timedelta(days=n)
            elif unit in ("week", "w"):
                r = a + datetime.timedelta(weeks=n)
            elif unit in ("month", "m"):
                r = add_months(a, n)
            elif unit in ("year", "y"):
                r = add_months(a, 12 * n)
            else:
                raise ValueError("unit must be days, weeks, months or years")
        except OverflowError:
            raise ValueError("that date is out of range")
        return {"start": a.isoformat(), "result": r.isoformat(), "weekday": r.strftime("%A"), "days_from_today": (r - today).days}
    if op == "weekday":
        return {"date": a.isoformat(), "weekday": a.strftime("%A"), "iso_week": a.isocalendar()[1], "day_of_year": a.timetuple().tm_yday,
                "days_in_month": calendar.monthrange(a.year, a.month)[1], "leap_year": calendar.isleap(a.year), "days_from_today": (a - today).days}
    raise ValueError("op must be diff, add, weekday or age")


# --------------------------------------------------------------------------------------------- convert_units
_UNITS = {
    "length": {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344, "nmi": 1852.0},
    "mass": {"mg": 1e-6, "g": 0.001, "kg": 1.0, "t": 1000.0, "oz": 0.028349523125, "lb": 0.45359237, "st": 6.35029318},
    "volume": {"ml": 0.001, "l": 1.0, "m3": 1000.0, "tsp": 0.00492892159375, "tbsp": 0.01478676478125, "floz": 0.0295735295625, "cup": 0.2365882365,
               "pt": 0.473176473, "qt": 0.946352946, "gal": 3.785411784},
    "speed": {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "kn": 1852 / 3600, "ft/s": 0.3048},
    "area": {"mm2": 1e-6, "cm2": 1e-4, "m2": 1.0, "km2": 1e6, "ha": 1e4, "in2": 0.00064516, "ft2": 0.09290304, "yd2": 0.83612736, "ac": 4046.8564224, "mi2": 2589988.110336},
    "data": {"bit": 0.125, "byte": 1.0, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "pb": 1e15, "kib": 1024.0, "mib": 1024.0 ** 2, "gib": 1024.0 ** 3, "tib": 1024.0 ** 4,
             "kbit": 125.0, "mbit": 125000.0, "gbit": 125000000.0},
    "time": {"ms": 0.001, "s": 1.0, "min": 60.0, "h": 3600.0, "day": 86400.0, "week": 604800.0, "month": 2629800.0, "year": 31557600.0},
}
_ALIAS = {}


def _al(canon, *names):
    for n in names:
        _ALIAS[n] = canon


_al("mm", "millimeter", "millimeters", "millimetre", "millimetres"); _al("cm", "centimeter", "centimeters", "centimetre", "centimetres")
_al("m", "meter", "meters", "metre", "metres"); _al("km", "kilometer", "kilometers", "kilometre", "kilometres", "kms")
_al("in", "inch", "inches", '"'); _al("ft", "foot", "feet", "'"); _al("yd", "yard", "yards"); _al("mi", "mile", "miles"); _al("nmi", "nautical mile", "nautical miles")
_al("mg", "milligram", "milligrams"); _al("g", "gram", "grams"); _al("kg", "kilogram", "kilograms", "kilo", "kilos", "kgs")
_al("t", "tonne", "tonnes", "ton", "tons", "metric ton"); _al("oz", "ounce", "ounces"); _al("lb", "lbs", "pound", "pounds"); _al("st", "stone", "stones")
_al("ml", "milliliter", "milliliters", "millilitre", "millilitres"); _al("l", "liter", "liters", "litre", "litres"); _al("m3", "cubic meter", "cubic meters")
_al("tsp", "teaspoon", "teaspoons"); _al("tbsp", "tablespoon", "tablespoons"); _al("floz", "fl oz", "fluid ounce", "fluid ounces")
_al("cup", "cups"); _al("pt", "pint", "pints"); _al("qt", "quart", "quarts"); _al("gal", "gallon", "gallons")
_al("m/s", "mps", "meters per second"); _al("km/h", "kph", "kmh", "kmph", "kilometers per hour"); _al("mph", "miles per hour")
_al("kn", "knot", "knots", "kt"); _al("ft/s", "fps")
_al("m2", "sqm", "square meter", "square meters", "sq m"); _al("km2", "sqkm", "square kilometer", "square kilometers"); _al("cm2", "sqcm", "square centimeter", "square centimeters")
_al("ha", "hectare", "hectares"); _al("ac", "acre", "acres"); _al("ft2", "sqft", "square foot", "square feet", "sq ft"); _al("in2", "sqin", "square inch", "square inches")
_al("mi2", "sqmi", "square mile", "square miles"); _al("yd2", "sqyd", "square yard", "square yards"); _al("mm2", "sqmm")
_al("bit", "bits"); _al("byte", "bytes"); _al("kb", "kilobyte", "kilobytes"); _al("mb", "megabyte", "megabytes"); _al("gb", "gigabyte", "gigabytes", "gig", "gigs")
_al("tb", "terabyte", "terabytes"); _al("pb", "petabyte", "petabytes"); _al("kib", "kibibyte", "kibibytes"); _al("mib", "mebibyte", "mebibytes"); _al("gib", "gibibyte", "gibibytes")
_al("tib", "tebibyte", "tebibytes"); _al("kbit", "kilobit", "kilobits"); _al("mbit", "megabit", "megabits"); _al("gbit", "gigabit", "gigabits")
_al("ms", "millisecond", "milliseconds"); _al("s", "sec", "secs", "second", "seconds"); _al("min", "mins", "minute", "minutes"); _al("h", "hr", "hrs", "hour", "hours")
_al("day", "days", "d"); _al("week", "weeks", "wk", "wks"); _al("month", "months", "mo"); _al("year", "years", "yr", "yrs", "y")
_TEMP = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}


def _unit_key(u):
    u = re.sub(r"\s+", " ", str(u or "").strip().lower().replace("°", "").replace("²", "2").replace("³", "3")).strip(". ")
    return _ALIAS.get(u, u)


def convert(value, frm, to):
    """(result, category) or ValueError. Temperatures are handled separately (they are not a plain factor)."""
    v = float(value)
    a, b = _unit_key(frm), _unit_key(to)
    if a in _TEMP and b in _TEMP:
        c = v if _TEMP[a] == "c" else (v - 32) * 5 / 9 if _TEMP[a] == "f" else v - 273.15
        if c < -273.15 - 1e-9:
            raise ValueError("that is below absolute zero")
        out = c if _TEMP[b] == "c" else c * 9 / 5 + 32 if _TEMP[b] == "f" else c + 273.15
        return out, "temperature"
    for cat, table in _UNITS.items():
        if a in table and b in table:
            return v * table[a] / table[b], cat
    known_a = [c for c, t in _UNITS.items() if a in t] + (["temperature"] if a in _TEMP else [])
    known_b = [c for c, t in _UNITS.items() if b in t] + (["temperature"] if b in _TEMP else [])
    if known_a and known_b:
        raise ValueError(f"you can't convert {known_a[0]} to {known_b[0]}")
    raise ValueError(f"I don't know the unit '{frm if not known_a else to}'")


# --------------------------------------------------------------------------------------------- random_pick
_RNG = random.SystemRandom()


def roll_dice(spec):
    """'2d6', 'd20', '3d8+2' -> (rolls, modifier, total). Up to 100 dice with up to 1000 sides."""
    m = re.fullmatch(r"\s*(\d*)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*", str(spec or "1d6").lower())
    if not m:
        raise ValueError("dice look like 2d6, d20 or 3d8+2")
    n, sides = int(m.group(1) or 1), int(m.group(2))
    mod = int(m.group(3).replace(" ", "")) if m.group(3) else 0
    if not 1 <= n <= 100 or not 2 <= sides <= 1000:
        raise ValueError("use 1-100 dice with 2-1000 sides")
    rolls = [_RNG.randint(1, sides) for _ in range(n)]
    return rolls, mod, sum(rolls) + mod


# --------------------------------------------------------------------------------------------- word / chat statistics (pure helpers)
def count_sequence(words, needle):
    """How many times the word list `needle` (one word, or a phrase) appears in `words`."""
    n = len(needle)
    if n == 1:
        return words.count(needle[0])
    return sum(1 for i in range(len(words) - n + 1) if words[i:i + n] == needle)


def _ro(db_file):
    return sqlite3.connect(pathlib.Path(db_file).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def word_sync(db_file, guild_id, channel_id, author_id, since_ts, needle, patterns, words_fn, is_cmd, allowed, seconds=5.0):
    """Runs in a worker thread, read only. Counts exact occurrences of the word / phrase `needle` (a list of lower-case words)."""
    con = _ro(db_file)
    try:
        like = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in patterns)
        sql = f"SELECT channel, author, author_name, content FROM msgs WHERE guild = ? AND COALESCE(deleted, 0) = 0 AND ({like})"
        args = [guild_id, *patterns]
        for col, val in (("channel", channel_id), ("author", author_id)):
            if val:
                sql += f" AND {col} = ?"
                args.append(val)
        if since_ts:
            sql += " AND ts >= ?"
            args.append(since_ts)
        total = msgs = 0
        per_person, per_channel, partial, deadline = {}, {}, False, time.time() + seconds
        for n, (ch, au, name, content) in enumerate(con.execute(sql, args)):
            if n % 2000 == 0 and time.time() > deadline:
                partial = True
                break
            if allowed is not None and ch not in allowed:
                continue
            if not content or is_cmd(content):
                continue
            k = count_sequence(words_fn(content), needle)
            if k:
                total += k
                msgs += 1
                per_person[au] = [name, per_person.get(au, [name, 0])[1] + k]
                per_channel[ch] = per_channel.get(ch, 0) + k
        first = con.execute("SELECT MIN(ts) FROM msgs WHERE guild = ?", (guild_id,)).fetchone()[0]
        return {"total": total, "messages": msgs, "people": per_person, "channels": per_channel, "partial": partial, "first": first}
    finally:
        con.close()


def stats_sync(db_file, kind, guild_id, channel_id, since_ts, limit):
    """Runs in a worker thread, read only: message counts grouped by person / channel / hour / day from the saved log."""
    con = _ro(db_file)
    try:
        where, args = "guild = ? AND ts >= ? AND COALESCE(deleted, 0) = 0", [guild_id, since_ts]
        if channel_id:
            where += " AND channel = ?"
            args.append(channel_id)
        total = con.execute(f"SELECT COUNT(*) FROM msgs WHERE {where}", args).fetchone()[0]
        if kind == "top_chatters":
            rows = con.execute(f"SELECT author, author_name, COUNT(*) AS n, MAX(ts) FROM msgs WHERE {where} GROUP BY author ORDER BY n DESC LIMIT ?", args + [limit]).fetchall()
            rows = [(r[1], r[2]) for r in rows]
        elif kind == "busiest_channels":
            rows = con.execute(f"SELECT channel, COUNT(*) AS n FROM msgs WHERE {where} GROUP BY channel ORDER BY n DESC LIMIT ?", args + [limit]).fetchall()
        elif kind == "by_hour":
            rows = con.execute(f"SELECT strftime('%H', ts, 'unixepoch') AS h, COUNT(*) FROM msgs WHERE {where} GROUP BY h ORDER BY h", args).fetchall()
        else:  # by_day
            rows = con.execute(f"SELECT strftime('%Y-%m-%d', ts, 'unixepoch') AS d, COUNT(*) FROM msgs WHERE {where} GROUP BY d ORDER BY d DESC LIMIT ?", args + [limit]).fetchall()
        first = con.execute("SELECT MIN(ts) FROM msgs WHERE guild = ?", (guild_id,)).fetchone()[0]
        return {"total": total, "rows": rows, "first": first}
    finally:
        con.close()


def help_search(entries, query, prefix_of, limit=6):
    """The help-list entries that fit `query`, best first. entries = (category, name, args, description, who, aliases)."""
    q = str(query or "").strip().lower().lstrip(",.+!/ ")
    toks = [t for t in re.findall(r"[\w']+", q) if t not in ("the", "a", "an", "of", "to", "do", "does", "what", "how", "is", "command", "cmd", "use", "i", "you", "it")]
    scored = []
    for e in entries:
        try:
            cat, name, args, desc, who, aliases = e[:6]
        except (ValueError, TypeError):
            continue
        low_name, al = str(name).lower(), [str(x).lower() for x in (aliases or [])]
        score = 0
        if q and (q == low_name or q in al):
            score += 100
        for t in toks:
            if t == low_name or t in al or t == low_name.split()[0]:
                score += 60
            elif t in low_name or any(t in x for x in al):
                score += 30
            elif t in str(desc).lower():
                score += 8
        if score:
            scored.append((score, cat, name, args, desc, who, aliases))
    scored.sort(key=lambda r: (-r[0], len(r[2])))
    return [{"command": f"{prefix_of(n)}{n} {a}".strip(), "what_it_does": str(d)[:700], "who_can_use_it": w, "aliases": list(al or [])[:6], "category": c}
            for _s, c, n, a, d, w, al in scored[:limit]]


# --------------------------------------------------------------------------------------------- build
def build(ns):
    global PUBLIC_PROMPT
    resolve_user, find_channel, find_role = ns["resolve_user"], ns["find_channel"], ns["find_role"]
    db_file, db, db_lock = ns["DB_FILE"], ns["_db"], ns["_db_lock"]
    ns["UTILITY_TOOLS"].update(UTILITY)
    public_history = bool(ns.get("PUBLIC_HISTORY"))
    if public_history:
        ns["PUBLIC_TOOLS"].add("word_stats")  # normal members may count words, but only in channels they can read (see t_word_stats)
    PUBLIC_PROMPT = (" You can also use calculate, date_math, convert_units and random_pick (dice, coin, numbers, options; not random members), and bot_help "
                     "(the exact, current list of this bot's commands: ALWAYS call it before you explain a command, never guess one)"
                     + (", and word_stats (how many times a word was said, only in channels the asker can see)" if public_history else "") + ". "
                     "Never do arithmetic, unit conversions or date counting in your head: use the tools. ")

    def main_mod():
        m = sys.modules.get("__main__")
        return m if m is not None and hasattr(m, "wordstats_words") else None

    async def is_admin(ctx):
        return (await ns["level"](ctx)) in ("owner", "full")

    # ------------------------------------------------------------------ calculate / dates / units / random
    async def t_calculate(ctx, a):
        try:
            return {"expression": str(a.get("expression"))[:120], **calc_present(calc_eval(a.get("expression")))}
        except ValueError as e:
            return {"error": str(e)}

    async def t_date_math(ctx, a):
        try:
            return date_calc(str(a.get("op") or "diff"), a.get("date"), a.get("other"), a.get("amount"), a.get("unit") or "days")
        except ValueError as e:
            return {"error": str(e)}
        except (OverflowError, TypeError) as e:
            return {"error": f"that date maths doesn't work ({e})"}

    async def t_convert_units(ctx, a):
        try:
            if a.get("value") is None:
                return {"error": "give the value to convert"}
            out, cat = convert(a["value"], a.get("from_unit"), a.get("to_unit"))
        except (ValueError, TypeError) as e:
            return {"error": str(e)}
        r = float(f"{out:.10g}")
        return {"category": cat, "result": r, "text": f"{float(a['value']):g} {a.get('from_unit')} = {r:,} {a.get('to_unit')}"}

    async def t_random_pick(ctx, a):
        kind = str(a.get("kind") or "").lower()
        try:
            if kind == "coin":
                return {"result": _RNG.choice(["heads", "tails"])}
            if kind == "dice":
                rolls, mod, total = roll_dice(a.get("dice"))
                return {"rolls": rolls, "modifier": mod, "total": total}
            if kind == "number":
                lo = int(a["min"]) if a.get("min") is not None else 1
                hi = int(a["max"]) if a.get("max") is not None else 100
                if lo > hi:
                    lo, hi = hi, lo
                return {"result": _RNG.randint(lo, hi), "range": [lo, hi]}
            if kind == "choice":
                opts = [str(x).strip() for x in (a.get("options") or []) if str(x).strip()][:200]
                if len(opts) < 2:
                    return {"error": "give at least 2 options"}
                k = max(1, min(int(a.get("count") or 1), len(opts), 20))
                return {"picked": _RNG.sample(opts, k), "out_of": len(opts)}
            if kind == "member":
                if not await is_admin(ctx):
                    return {"error": "only admins can pick a random member"}
                pool = [m for m in ctx.guild.members if not m.bot]
                if a.get("role"):
                    r, err = find_role(ctx.guild, a["role"])
                    if err:
                        return {"error": err}
                    pool = [m for m in pool if r in m.roles]
                if a.get("recent_hours"):
                    since = time.time() - float(a["recent_hours"]) * 3600

                    def active():
                        return {r[0] for r in db.execute("SELECT DISTINCT author FROM msgs WHERE guild = ? AND ts >= ? AND COALESCE(deleted, 0) = 0", (ctx.guild.id, since))}
                    async with db_lock:
                        ids = await asyncio.to_thread(active)
                    pool = [m for m in pool if m.id in ids]
                if not pool:
                    return {"error": "nobody matches"}
                k = max(1, min(int(a.get("count") or 1), len(pool), 20))
                return {"picked": [{"name": m.display_name, "mention": f"<@{m.id}>"} for m in _RNG.sample(pool, k)], "pool_size": len(pool)}
        except (ValueError, TypeError) as e:
            return {"error": str(e)}
        return {"error": "kind must be dice, coin, number, choice or member"}

    # ------------------------------------------------------------------ word_stats / chat_stats
    async def t_word_stats(ctx, a):
        main = main_mod()
        if main is None:
            return {"error": "word_stats needs the newest bot.py (it holds the ,wordstats code)"}
        needle = main.wordstats_words(str(a.get("word") or ""))
        if not needle:
            return {"error": "give a word or a short phrase to count"}
        if len(needle) > 5 or any(len(w) > 40 for w in needle):
            return {"error": "give one word or a short phrase (up to 5 words)"}
        author_id = channel_id = since = None
        if (a.get("user") or "").strip():
            author_id, _n, _m, err = resolve_user(ctx, a["user"])
            if err:
                return {"error": err}
        if (a.get("channel") or "").strip():
            ch, err = find_channel(ctx, a["channel"])
            if err:
                return {"error": err}
            channel_id = ch.id
        if a.get("since_hours"):
            since = time.time() - float(a["since_hours"]) * 3600
        allowed = ns["_readable_ids"](ctx) if (await ns["level"](ctx)) == "public" else None
        prefixes = tuple(main.all_prefixes()) + (f"<@{ctx.bot.user.id}>", f"<@!{ctx.bot.user.id}>")
        names = frozenset(ctx.bot.all_commands)
        longest = max(needle, key=len)
        patterns = main.wordstats_patterns(longest)
        res = await asyncio.to_thread(word_sync, db_file, ctx.guild.id, channel_id, author_id, since, needle, patterns, main.wordstats_words,
                                      lambda text: main.wordstats_is_command(text, prefixes, names), allowed)
        out = {"word": " ".join(needle), "occurrences": res["total"], "messages_containing_it": res["messages"],
               "log_starts": ns["_fmt"](res["first"]) if res["first"] else "empty",
               "note": "whole words, upper / lower case the same; deleted messages and bot commands are not counted; only the saved messages are counted"}
        if res["partial"]:
            out["warning"] = "stopped early (too much history): the numbers are a minimum"
        if not author_id and res["people"]:
            out["top_people"] = [[n, c] for n, c in sorted(res["people"].values(), key=lambda x: -x[1])[:10]]
        if not channel_id and res["channels"]:
            def label(cid):
                c = ctx.guild.get_channel(cid) or ctx.guild.get_thread(cid)
                return f"#{c.name}" if c else "a deleted channel"
            out["top_channels"] = [[label(cid), c] for cid, c in sorted(res["channels"].items(), key=lambda kv: -kv[1])[:5]]
        return out

    async def t_chat_stats(ctx, a):
        if not await is_admin(ctx):
            return {"error": "only admins can see chat statistics"}
        kind = str(a.get("kind") or "top_chatters")
        if kind not in ("top_chatters", "busiest_channels", "by_hour", "by_day"):
            return {"error": "kind must be top_chatters, busiest_channels, by_hour or by_day"}
        hours = max(1.0, min(float(a.get("since_hours") or 168), 24 * 3650))
        limit = int(max(1, min(a.get("limit") or 10, 25)))
        channel_id = None
        if (a.get("channel") or "").strip():
            ch, err = find_channel(ctx, a["channel"])
            if err:
                return {"error": err}
            channel_id = ch.id
        res = await asyncio.to_thread(stats_sync, db_file, kind, ctx.guild.id, channel_id, time.time() - hours * 3600, limit)
        total = res["total"] or 0

        def pct(n):
            return round(100 * n / total, 1) if total else 0
        if kind == "top_chatters":
            rows = [{"name": n, "messages": c, "share_pct": pct(c)} for n, c in res["rows"]]
        elif kind == "busiest_channels":
            def label(cid):
                c = ctx.guild.get_channel(cid) or ctx.guild.get_thread(cid)
                return f"#{c.name}" if c else "a deleted channel"
            rows = [{"channel": label(cid), "messages": c, "share_pct": pct(c)} for cid, c in res["rows"]]
        elif kind == "by_hour":
            rows = [{"hour_utc": f"{h}:00", "messages": c, "share_pct": pct(c)} for h, c in res["rows"]]
        else:
            rows = [{"day": d, "messages": c} for d, c in res["rows"]]
        return {"kind": kind, "window_hours": hours, "total_messages": total, "rows": rows, "log_starts": ns["_fmt"](res["first"]) if res["first"] else "empty",
                "note": "counts the saved messages (deleted ones excluded); commands and bot messages are not filtered"}

    async def t_bot_help(ctx, a):
        main = sys.modules.get("__main__")
        entries = getattr(main, "HELP_COMMANDS", None)
        if not entries:
            return {"error": "I can't read the command list right now"}
        prefix_of = getattr(main, "help_prefix_for", lambda name: ".")
        q = str(a.get("query") or "").strip()
        if not q:
            cats = {}
            for e in entries:
                try:
                    cat, name = e[0], str(e[1])
                except (IndexError, TypeError):
                    continue
                if " " not in name:
                    cats.setdefault(cat, []).append(f"{prefix_of(name)}{name}")
            return {"commands_by_category": cats, "tip": "call bot_help again with a query (a command name or a topic) for how to use one"}
        found = help_search(entries, q, prefix_of)
        if not found:
            return {"error": f"no command matches '{q}'. It may not exist: say you're not sure instead of guessing"}
        return {"matches": found}

    ns["_more3_loaded"] = True
    A = _enum
    return {
        "calculate": (t_calculate, "Exact maths. expression uses numbers, + - * / // % ** (or ^), brackets, constants pi / e and functions sqrt, abs, round, floor, ceil, log, log10, log2, exp, sin, cos, tan, factorial, gcd, min, max. "
                      "No thousands separators (write 1500000 not 1,500,000) and no % sign for percentages: '15% of 80' = 0.15 * 80. ALWAYS use this for arithmetic instead of working it out yourself.",
                      {"expression": {"type": "string", "description": "e.g. 0.15 * 80 or sqrt(144) + 2 ** 10"}}, ["expression"]),
        "date_math": (t_date_math, "Exact date maths. op=diff: days / weeks / years-months-days between date and other (other defaults to today; 'how many days until X' = date X, other empty). "
                      "op=add: date (default today) plus amount of unit (days, weeks, months, years; negative = before). op=weekday: which day of the week a date is. op=age: how long ago a date was (years, months, days). "
                      "Dates are YYYY-MM-DD, or today / tomorrow / yesterday, or written out (March 3, 2027).",
                      {"op": A("diff", "add", "weekday", "age"), "date": S, "other": S, "amount": N, "unit": A("days", "weeks", "months", "years")}, ["op"]),
        "convert_units": (t_convert_units, "Convert between units offline: length (mm cm m km in ft yd mi), mass (mg g kg t oz lb st), volume (ml l tsp tbsp cup pt qt gal floz), temperature (C F K), speed (m/s km/h mph knots), "
                          "area (m2 km2 ha acre ft2), data (bit byte KB MB GB TB KiB MiB GiB), time (ms s min h day week month year). For money use lookup(currency).",
                          {"value": N, "from_unit": S, "to_unit": S}, ["value", "from_unit", "to_unit"]),
        "random_pick": (t_random_pick, "A truly random result (use it, never 'pick' yourself): kind=dice (dice='2d6', 'd20', '3d8+2'), coin, number (min, max), choice (options = a list, count = how many "
                        "different ones), member (admins only: a random member, optionally with a role and / or who wrote in the last recent_hours hours; count winners).",
                        {"kind": A("dice", "coin", "number", "choice", "member"), "dice": S, "min": N, "max": N, "options": LIST, "count": N, "role": S, "recent_hours": N}, ["kind"]),
        "word_stats": (t_word_stats, "How many times a word or short phrase was said in the saved messages: total occurrences, messages containing it, and who / which channels said it most. "
                       "Optional filters: user (only that person), channel, since_hours. Whole words only (moha does not count mohamed), case-insensitive, deleted messages and bot commands are skipped. "
                       "Normal members only get channels they can read.",
                       {"word": {"type": "string", "description": "one word or a short phrase"}, "user": U, "channel": CH, "since_hours": N}, ["word"]),
        "chat_stats": (t_chat_stats, "Activity statistics from the saved messages (admins only): kind=top_chatters (most messages per person), busiest_channels, by_hour (UTC), by_day. "
                       "since_hours = the time window (default 168 = one week), optional channel, limit (default 10).",
                       {"kind": A("top_chatters", "busiest_channels", "by_hour", "by_day"), "since_hours": N, "channel": CH, "limit": N}, []),
        "bot_help": (t_bot_help, "Look up the commands of THIS bot, live from its help list: what a command does, exactly how to type it (with the current prefix), who can use it. "
                     "query = a command name or a topic ('shop', 'rob', 'wordstats', 'how to play plinko'); empty = the list of all commands. Use it before explaining any command.",
                     {"query": S}, []),
    }
