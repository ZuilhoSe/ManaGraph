"""Hypergeometric mana-base calculator: curve-only land and colored-source targets.

Standalone module -- no dependency on mana.py/solver.py, see feedback_mana_calc_scope
memory. Level 1 only (curve + pips, no ramp/draw adjustment); that is a later tier.

Commander convention: 99-card library, 7-card opening hand, everyone draws on turn 1
(house rule confirmed for this project's playgroup).
"""

from __future__ import annotations

from math import comb

DECK_SIZE = 99

# target_confidence(avg_cmc) = TARGET_INTERCEPT + TARGET_SLOPE * avg_cmc, a flat
# confidence bar the curve's weighted P_mana has to clear -- fit (least squares,
# R^2=0.91) on 6 real Commander decks the user was happy with at their real land count,
# each deck's own curve weights used to compute both the fitted point and (later) the
# achievability score. avg_cmc ranged 2.29-3.80 across those 6; higher avg_cmc decks
# settled for *less* achieved confidence at their real land count, not more. Two decks
# were excluded from the fit on purpose, not by curve alone: a commander with a
# land-reanimation mechanic (wanted 3 more lands than curve alone predicts) and a
# heavily ramp-supported deck (wanted ~1 fewer, ramp substituting for lands in a way
# this curve-only tier can't see -- exactly the gap Level 2, ramp/draw-aware, is meant
# to close later).
TARGET_INTERCEPT = 1.042114923522117
TARGET_SLOPE = -0.0983877635386525
TARGET_MIN = 0.05
TARGET_MAX = 0.99


def hand_size(turn: int) -> int:
    """Cards seen by a given turn: 7-card hand plus one draw per turn, turn 1 included."""
    return 7 + turn


def hypergeom_at_least(population: int, successes: int, draws: int, need: int) -> float:
    """P(X >= need) for X ~ Hypergeometric(population, successes, draws)."""
    if need <= 0:
        return 1.0
    if successes <= 0 or draws <= 0:
        return 0.0
    draws = min(draws, population)
    total = comb(population, draws)
    if total == 0:
        return 0.0
    max_i = min(need - 1, successes, draws)
    if max_i < 0:
        return 1.0
    cum = sum(
        comb(successes, i) * comb(population - successes, draws - i)
        for i in range(max_i + 1)
    )
    return 1.0 - cum / total


def hand_confidence_by_cmc(curve: dict[int, int], population: int = DECK_SIZE) -> dict[int, float]:
    """Informational only: chance of holding a card of CMC c by turn c, per CMC bucket.

    This is a property of the spell base (how many cards share that CMC), not of the
    mana base -- land count cannot move it, so it is reported separately and never
    gates the recommenders below.
    """
    return {
        c: hypergeom_at_least(population, n_c, hand_size(c), 1)
        for c, n_c in curve.items()
        if c >= 1 and n_c > 0
    }


def average_cmc(curve: dict[int, int]) -> float:
    """Nonland-card-count-weighted average CMC. Drives `target_confidence`."""
    buckets = {c: n for c, n in curve.items() if c >= 1 and n > 0}
    total = sum(buckets.values())
    if total == 0:
        return 0.0
    return sum(c * n for c, n in buckets.items()) / total


def target_confidence(avg_cmc: float) -> float:
    """Flat confidence bar for a deck with this average CMC, clamped to [TARGET_MIN,
    TARGET_MAX] so a pathological avg_cmc can't invert or zero out the target.
    """
    raw = TARGET_INTERCEPT + TARGET_SLOPE * avg_cmc
    return max(TARGET_MIN, min(TARGET_MAX, raw))


def _min_amount_for_target(
    weight: dict,
    target: float,
    turn_of,
    need_of,
    population: int,
    max_amount: int,
) -> int:
    """Smallest resource count (lands or color sources) whose weighted chance of
    'have `need_of(key)` in hand by turn `turn_of(key)`' clears `target`.

    Shared by `recommended_land_count` and `recommended_color_sources`: both search for
    the minimum of a resource against a curve/pip-weighted hypergeometric target, only
    differing in how a turn and a need are read off each bucket's key.
    """
    for amount in range(1, max_amount + 1):
        score = sum(
            w * hypergeom_at_least(population, amount, hand_size(turn_of(key)), need_of(key))
            for key, w in weight.items()
        )
        if score >= target:
            return amount
    return max_amount


def recommended_land_count(
    curve: dict[int, int],
    population: int = DECK_SIZE,
    max_lands: int = 80,
) -> int:
    """Minimum land count so the curve-weighted chance of 'can pay for a turn-c play'
    reaches `target_confidence(avg_cmc)`, weighted by how much of the curve actually
    sits at each CMC.
    """
    buckets = {c: n for c, n in curve.items() if c >= 1 and n > 0}
    total = sum(buckets.values())
    if total == 0:
        return 0
    avg = average_cmc(buckets)
    weight = {c: n_c / total for c, n_c in buckets.items()}
    target = target_confidence(avg)
    return _min_amount_for_target(weight, target, lambda c: c, lambda c: c, population, max_lands)


def recommended_color_sources(
    pip_requirements: list[tuple[int, int]],
    avg_cmc: float,
    population: int = DECK_SIZE,
    max_sources: int = 40,
) -> int:
    """Minimum source count for one color so the pip-weighted chance of 'can pay the
    pips this card wants' reaches `target_confidence(avg_cmc)` -- same target function
    and same weighting rationale as `recommended_land_count`.

    `avg_cmc` is the *deck's* average CMC (`average_cmc(curve)`), not this color's own
    subset -- how demanding a turn-relative bar the deck sets is about the deck's
    overall plan, not about this one color. `pip_requirements` is one (cmc, pips) entry
    per card that uses the color, pooled by exact (cmc, pips) pair: a demanding card
    (e.g. {W}{W}{W}{W}{W}) is a dead card if unpaid, unlike a land count shortfall
    which only costs a turn, so pip requirements are dampened by count rather than
    taken as a single worst-case -- a handful of double/triple-pip cards nudge the
    target, not set it alone.
    """
    requirements = [(c, p) for c, p in pip_requirements if c >= 1 and p >= 1]
    if not requirements:
        return 0
    buckets: dict[tuple[int, int], int] = {}
    for c, p in requirements:
        buckets[(c, p)] = buckets.get((c, p), 0) + 1
    total = sum(buckets.values())
    weight = {key: n / total for key, n in buckets.items()}
    target = target_confidence(avg_cmc)
    return _min_amount_for_target(
        weight, target, lambda key: key[0], lambda key: key[1], population, max_sources
    )


def mana_base_report(
    curve: dict[int, int],
    pip_requirements_by_color: dict[str, list[tuple[int, int]]],
    population: int = DECK_SIZE,
) -> dict:
    """Land count target and per-color source targets for the given curve/pips."""
    avg = average_cmc(curve)
    return {
        "avg_cmc": round(avg, 3),
        "target_confidence": round(target_confidence(avg), 3),
        "land_count": recommended_land_count(curve, population),
        "sources": {
            color: recommended_color_sources(pips, avg, population)
            for color, pips in pip_requirements_by_color.items()
        },
    }
