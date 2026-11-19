"""Re-check that Luck Boost / Half Loss can't be farmed. Run it after changing plinko tables, prices, LUCK_WIN_CHANCE or BOOST_MAX_BET:

    python check_boosts.py

It works out the exact expected result (no random sampling) of 5 boosted games at the biggest boosted bet, using the plinko tables
and rules straight from gambling.py, and compares it with what the boost costs. The numbers below the "keep in sync" line are copies
of the values in bot.py (bot.py can't be imported without discord.py), so update them there and here together.
Exit code 1 = at least one case pays for itself.
"""
import itertools
import math
import sys
from math import comb

import gambling

# ---- keep in sync with bot.py ----------------------------------------------------------------------------------------
BET = 25000                # BOOST_MAX_BET
GAMES = 5                  # LUCK_GAMES / HALF_GAMES
LUCK_WIN_CHANCE = 0.70
PRICES = {"luck": 20000, "half": 12000, "both": 32000}   # LUCK_PRICE, HALF_PRICE, both
COINFLIP_WIN_CHANCE = 0.54
SLOTS = [(22, 3), (20, 4), (18, 5), (16, 8), (8, 20), (4, 40)]   # SLOTS_SYMBOLS: (weight, three-of-a-kind payout)
SLOTS_PAIR = 1.6           # SLOTS_PAIR_PAYOUT
# -----------------------------------------------------------------------------------------------------------------------


def settle(net, half):
    return gambling.soften_loss(net, BET) if half and net < 0 else net


def luck_split(outcomes, is_win, natural):
    """Expected net of a luck-decided game: win with max(LUCK_WIN_CHANCE, natural) drawn from the winning results only."""
    wins = [(p, n) for p, n, w in outcomes if w]
    losses = [(p, n) for p, n, w in outcomes if not w]
    pw = max(LUCK_WIN_CHANCE, natural)
    return (pw * sum(p * n for p, n in wins) / sum(p for p, _ in wins)
            + (1 - pw) * sum(p * n for p, n in losses) / sum(p for p, _ in losses))


def plinko(risk, luck, half):
    table = gambling.PLINKO_TABLES[risk]
    def net(m):
        back = math.ceil(BET * m) if m > 1 else int(BET * m)
        return back - BET
    natural = gambling.plinko_win_chance(risk)
    if not luck:
        return sum(comb(8, k) / 256 * settle(net(m), half) for k, m in enumerate(table))
    out = [(comb(8, k) / 256, settle(net(m), half), (1 < m <= gambling.PLINKO_LUCK_MAX_MULT)) for k, m in enumerate(table)
           if 1 < m <= gambling.PLINKO_LUCK_MAX_MULT or m < 1]
    return luck_split(out, None, natural)


def coinflip(luck, half):
    p = max(LUCK_WIN_CHANCE, COINFLIP_WIN_CHANCE) if luck else COINFLIP_WIN_CHANCE
    return p * BET + (1 - p) * settle(-BET, half)


def slots(luck, half):
    total = sum(w for w, _ in SLOTS)
    out = []
    for a, b, c in itertools.product(range(len(SLOTS)), repeat=3):
        p = SLOTS[a][0] * SLOTS[b][0] * SLOTS[c][0] / total ** 3
        m = SLOTS[a][1] if a == b == c else (SLOTS_PAIR if len({a, b, c}) < 3 else 0)
        out.append((p, settle(int(BET * m) - BET, half), m > 1))
    natural = sum(p for p, _, w in out if w)
    return luck_split(out, None, natural) if luck else sum(p * n for p, n, _ in out)


def main():
    games = [("coinflip", coinflip), ("slots", slots)] + [("plinko " + r, lambda l, h, r=r: plinko(r, l, h)) for r in gambling.PLINKO_RISKS]
    print("5 boosted games at a bet of %d (the biggest bet a boost works on)\n" % BET)
    print("%-14s %-5s %12s %12s %8s" % ("game", "boost", "avg profit", "price", "share"))
    bad = False
    for name, fn in games:
        for mode, (l, h) in {"luck": (True, False), "half": (False, True), "both": (True, True)}.items():
            profit = fn(l, h) * GAMES
            share = profit / PRICES[mode]
            bad |= share >= 1
            print("%-14s %-5s %+12.0f %12d %7.0f%%%s" % (name, mode, profit, PRICES[mode], share * 100, "   <-- PAYS FOR ITSELF" if share >= 1 else ""))
    for risk in gambling.PLINKO_RISKS:
        print("plinko %-6s normal payback %.1f%%, win chance %.1f%%" % (risk, gambling.plinko_payback(risk) * 100, gambling.plinko_win_chance(risk) * 100))
    print("\nRESULT:", "FARMABLE, fix the prices / cap" if bad else "ok, no boost pays for itself")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
