"""Gambling logic that has nothing to do with Discord: the random generator and the blackjack game.

Why it is a separate file: it can be tested on its own (see CHANGES.md, Step 42), and the rules live in ONE place,
so the bot can't pay out differently from what the rules say.

Rules of the blackjack table (all fixed, none of them depends on who plays):
  * A fresh, freshly shuffled 6-deck shoe for EVERY game, so counting cards is pointless.
  * You get two cards, the dealer gets two (one hidden). If the dealer shows an ace or a ten-value card they check
    for blackjack first, so a double down can never lose to a dealer blackjack.
  * Blackjack (ace + ten-value card on the first two cards) pays BLACKJACK_PAYS (3:2 here; the bot sets it from
    BLACKJACK_PAYOUT in .env, 2:1 by default), rounded down. Both have it: push.
  * Hit = one more card. Stand = the dealer plays. Double down = only on your first two cards, doubles the bet,
    you get exactly one more card, then the dealer plays. No splitting, no insurance, no surrender.
  * The dealer draws until 17 or more and stands on every 17 (also soft 17).
  * Win pays 1:1, push gives the bet back, bust or a lower total loses the bet.
"""
import random

# SystemRandom takes its numbers from the operating system, not from Python's Mersenne Twister, so nobody can work out
# the next result from the ones the bot has already shown. Every bet in the bot uses this one generator.
RNG = random.SystemRandom()

SUITS = "shdc"  # spades, hearts, diamonds, clubs
RANKS = ("a", "2", "3", "4", "5", "6", "7", "8", "9", "10", "j", "q", "k")
SUIT_SYMBOL = {"s": "\u2660", "h": "\u2665", "d": "\u2666", "c": "\u2663"}
DECKS = 6
DEALER_STANDS_ON = 17
BLACKJACK_PAYS = (3, 2)  # (numerator, denominator): what a blackjack pays. The bot changes it from .env (BLACKJACK_PAYOUT).


def parse_ratio(text, default=(3, 2)):
    """'2:1' / '3:2' / '6:5' -> (2, 1) / (3, 2) / (6, 5). Anything else (or a side outside 1..20) gives the default."""
    try:
        a, b = str(text).replace(" ", "").split(":")
        a, b = int(a), int(b)
    except ValueError:
        return default
    return (a, b) if 1 <= a <= 20 and 1 <= b <= 20 else default


class GameOver(Exception):
    """An action was tried on a game that is already finished (or isn't allowed right now)."""


def new_shoe(rng=RNG, decks=DECKS):
    """A shuffled shoe: a list of (rank, suit) cards. Cards are taken from the end with pop()."""
    cards = [(rank, suit) for _ in range(decks) for suit in SUITS for rank in RANKS]
    rng.shuffle(cards)
    return cards


def card_points(rank):
    if rank == "a":
        return 11
    if rank in ("10", "j", "q", "k"):
        return 10
    return int(rank)


def hand_value(hand):
    """(total, soft). Aces count 11 unless that would bust, then 1. soft = an ace is still counted as 11."""
    total = sum(card_points(r) for r, _s in hand)
    aces = sum(1 for r, _s in hand if r == "a")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def is_blackjack(hand):
    return len(hand) == 2 and hand_value(hand)[0] == 21


def card_label(card):
    """Plain text for a card, like A\u2660 or 10\u2665 (used when the card emojis aren't available)."""
    rank, suit = card
    return rank.upper() + SUIT_SYMBOL[suit]


# What each way a game can end is called, and what it pays. `payout` is the TOTAL the player gets back:
#   0 = the stake is lost   stake = the stake back (push)   2 x stake = a win   stake + BLACKJACK_PAYS x stake = blackjack
OUTCOMES = ("blackjack", "win", "dealer_bust", "push", "push_blackjack", "lose", "bust", "dealer_blackjack")
WINNING = ("blackjack", "win", "dealer_bust")
PUSHING = ("push", "push_blackjack")


def payout_for(outcome, stake):
    """What comes back to the player for a finished game: 0, stake, 2 x stake or stake + stake x BLACKJACK_PAYS (rounded down)."""
    if outcome == "blackjack":
        num, den = BLACKJACK_PAYS
        return stake + stake * num // den
    if outcome in ("win", "dealer_bust"):
        return stake * 2
    if outcome in PUSHING:
        return stake
    return 0


class Blackjack:
    """One game. It only knows cards and rules. Moving reputation around is the caller's job (and the caller does it
    exactly once, when `done` becomes True)."""

    def __init__(self, bet, rng=RNG):
        if not isinstance(bet, int) or isinstance(bet, bool) or bet < 1:
            raise ValueError("the bet must be a whole number of at least 1")
        self.bet = bet
        self.stake = bet            # becomes 2 x bet after a double down
        self.doubled = False
        self.shoe = new_shoe(rng)
        self.player = []
        self.dealer = []
        for _ in range(2):          # real deal order: player, dealer, player, dealer
            self.player.append(self.shoe.pop())
            self.dealer.append(self.shoe.pop())
        self.done = False
        self.outcome = None
        self.payout = 0
        self._check_naturals()

    # ---- state ---------------------------------------------------------------------------
    def player_total(self):
        return hand_value(self.player)[0]

    def dealer_total(self):
        return hand_value(self.dealer)[0]

    def can_double(self):
        return (not self.done) and len(self.player) == 2 and not self.doubled

    def _finish(self, outcome):
        self.done = True
        self.outcome = outcome
        self.payout = payout_for(outcome, self.stake)

    def _check_naturals(self):
        player_bj, dealer_bj = is_blackjack(self.player), is_blackjack(self.dealer)
        if player_bj and dealer_bj:
            self._finish("push_blackjack")
        elif player_bj:
            self._finish("blackjack")
        elif dealer_bj:
            self._finish("dealer_blackjack")

    # ---- actions (each one raises GameOver when it isn't allowed, so a stray click can't change a finished game) ----
    def _need_open(self):
        if self.done:
            raise GameOver("this game is finished")

    def hit(self):
        self._need_open()
        self.player.append(self.shoe.pop())
        total = self.player_total()
        if total > 21:
            self._finish("bust")
        elif total == 21:           # nothing more to gain, stand automatically
            self._dealer_turn()

    def stand(self):
        self._need_open()
        self._dealer_turn()

    def double(self):
        """Double down. The caller must already have taken the second bet out of the player's balance."""
        self._need_open()
        if not self.can_double():
            raise GameOver("you can only double down on your first two cards")
        self.stake = self.bet * 2
        self.doubled = True
        self.player.append(self.shoe.pop())
        if self.player_total() > 21:
            self._finish("bust")
        else:
            self._dealer_turn()

    def _dealer_turn(self):
        while self.dealer_total() < DEALER_STANDS_ON:
            self.dealer.append(self.shoe.pop())
        mine, theirs = self.player_total(), self.dealer_total()
        if theirs > 21:
            self._finish("dealer_bust")
        elif mine > theirs:
            self._finish("win")
        elif mine == theirs:
            self._finish("push")
        else:
            self._finish("lose")


# ======================================================================================================================
# Shop boosts (Luck Boost, Half Loss) and Plinko. Pure rules, no Discord, so they can be tested on their own.
#   * Luck Boost: while it lasts, a game (coinflip, slots, plinko; never blackjack) is decided FIRST: a win with LUCK_WIN_CHANCE
#     (70%), a loss otherwise. The result is then drawn the normal way, but only from the results that fit (so jackpots stay as
#     rare as before among the wins). It raises the chance, it never guarantees a win.
#   * Half Loss: a lost game only costs half the bet (rounded up).
# ======================================================================================================================
LUCK_WIN_CHANCE = 0.70


def half_loss(bet):
    """What a lost game costs while Half Loss is active: half the bet, rounded up (a bet of 1 still costs 1)."""
    return (int(bet) + 1) // 2


def soften_loss(net, bet):
    """`net` is the player's profit or loss of a game. With Half Loss a loss is cut to half the bet; a win or a push stays."""
    return max(net, -half_loss(bet)) if net < 0 else net


def luck_decides_win(rng=RNG, natural=0.0, chance=LUCK_WIN_CHANCE):
    """With a Luck Boost active: does this game have to be a win? `natural` is the normal win chance of the game: a boost
    never makes it worse (plinko on low risk already wins 73% of the time, so it keeps that)."""
    return rng.random() < max(chance, natural)


def draw_matching(make, is_win, want_win, tries=500):
    """Call make() until its result is a win (want_win=True) or a loss (False). A result that is neither (a push) never matches.
    Gives the last result after `tries` attempts so it can never hang (only possible when no result of that kind exists)."""
    result = make()
    for _ in range(tries):
        if (is_win(result) if want_win else is_loss_of(result, is_win)):
            return result
        result = make()
    return result


def is_loss_of(result, is_win):
    """Helper for draw_matching: a result counts as a loss when it isn't a win (pushes are avoided by the tables)."""
    return not is_win(result)


# ---- Plinko ---------------------------------------------------------------------------------------------------------
# The ball falls through PLINKO_ROWS rows of pegs. At every row it goes left or right (50/50), so it lands in slot k
# (0..PLINKO_ROWS) with the chance C(rows, k) / 2^rows: the middle is common, the edges are rare. A slot pays `multiplier x bet`.
# No slot pays exactly 1.0, so every drop is clearly a win (above 1) or a loss (below 1).
# Average payback (Step 62): low about 99.9%, medium about 100.5%, high about 100.3%. That is a fair game for everyone who plays
# without a boost (bets under 50 can land a little under or over because payouts are whole numbers: wins round up, losses round down).
# Boosters get a small edge instead: a Luck Boost makes medium / high about +8% per game, Half Loss adds a bit on top (low is already
# a 73% winner, so Luck Boost doesn't change it; Half Loss gives it about +3%). Run check_boosts.py after changing any of this.
PLINKO_ROWS = 8
PLINKO_LUCK_MAX_MULT = 3   # a Luck Boost can't steer the ball into a slot that pays more than this (medium / high: only the 1.3x / 1.4x slots)
PLINKO_RISKS = ("low", "medium", "high")
PLINKO_TABLES = {
    "low":    (2.5, 1.5, 1.25, 1.15, 0.4, 1.15, 1.25, 1.5, 2.5),  # wins often (73%), small wins
    "medium": (11, 3.5, 1.3, 0.7, 0.4, 0.7, 1.3, 3.5, 11),       # wins about 29% of the time
    "high":   (26, 4, 1.4, 0.4, 0.25, 0.4, 1.4, 4, 26),          # wins about 29% of the time, the edges pay 26x
}
PLINKO_RISK_ALIASES = {"l": "low", "low": "low", "easy": "low", "m": "medium", "med": "medium", "medium": "medium",
                       "h": "high", "high": "high", "hard": "high"}


def plinko_path(rng=RNG):
    """One drop: a tuple of 0 (left) and 1 (right), one per row. The number of 1s is the slot the ball lands in."""
    return tuple(rng.randint(0, 1) for _ in range(PLINKO_ROWS))


def plinko_slot(path):
    return sum(path)


def plinko_multiplier(risk, path):
    return PLINKO_TABLES[risk][plinko_slot(path)]


def plinko_drop(risk, rng=RNG, want_win=None):
    """(path, multiplier). want_win None = a normal drop. With a Luck Boost: True = a drop that wins, but never one of the big
    edge slots (above PLINKO_LUCK_MAX_MULT), False = a drop that loses (pays less than the bet)."""
    table = PLINKO_TABLES[risk]
    if want_win is None:
        path = plinko_path(rng)
    elif want_win:
        path = draw_matching(lambda: plinko_path(rng), lambda p: 1 < table[sum(p)] <= PLINKO_LUCK_MAX_MULT, True)
    else:
        path = draw_matching(lambda: plinko_path(rng), lambda p: table[sum(p)] < 1, True)
    return path, table[sum(path)]


def plinko_win_chance(risk):
    """The normal chance that a drop pays more than the bet."""
    from math import comb
    table = PLINKO_TABLES[risk]
    return sum(comb(PLINKO_ROWS, k) for k, m in enumerate(table) if m > 1) / 2 ** PLINKO_ROWS


def plinko_payback(risk):
    """Average share of the bet that comes back (a normal drop)."""
    from math import comb
    table = PLINKO_TABLES[risk]
    return sum(comb(PLINKO_ROWS, k) * m for k, m in enumerate(table)) / 2 ** PLINKO_ROWS
