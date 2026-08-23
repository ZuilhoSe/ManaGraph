"""Mana-base sizing policy: static per-color-count land floor and the land-count alert.

This is the piece hypergeometric.py's recommended_land_count/recommended_color_sources
is meant to eventually replace. Kept separate from mana_symbols.py (fact extraction)
and curve.py (curve-shape policy) because it's a distinct judgment: not "what does
this card cost/produce" or "does the curve shape match the plan", but "is the mana
base itself big enough".
"""

from __future__ import annotations

from roles import ROLE_QUOTAS

from deck_analysis.mana_symbols import COLORS

PIP_PER_SOURCE_WARN = 2.0
MIN_SOURCES = {1: 14, 2: 10, 3: 7, 4: 6, 5: 5}
# Land count off-quota, as a fraction of the breached bound: <5% mild, 5-10% moderate, >=10% severe.
# Severe threshold confirmed explicitly at 10% (~3 lands off the 34 low bound) -- a build
# landing even a handful of lands short of quota is not a minor issue. The mild/moderate
# split below that is provisional pending a separate pass on the moderate multiplier.
LAND_SEVERITY_BANDS = (0.05, 0.10)
# A land card's pull toward the quota is this scale times a flat 0.8 (shape_bonus), stacked
# on top of role_need_bonus's flat +2.5 for the "land" role while under quota (roles.py).
# Raised from the original 1.0/1.8/3.0 scale after a real build stalled short of quota --
# a well-matched nonland card's synergy score kept outscoring more lands once the count
# got close but not yet at the target. Expect further tuning as this keeps getting exercised.
LAND_SEVERITY_SCALE = {"none": 0.0, "mild": 1.5, "moderate": 2.5, "severe": 4.0}


def land_alert(land_count: int, curve_by_cmc: dict[int, int] | None = None) -> dict:
    """How far the land count sits outside ROLE_QUOTAS['land'], with a severity tier.

    One flat string among a dozen deficits reads the same for 39 lands and 67 lands.
    This gives a dedicated, magnitude-aware signal so both the solver bias and the
    human-facing warning can react proportionally instead of just "in range or not".

    `curve_by_cmc` (exact-CMC counts, unlike the "0".."7+" display curve) is part of
    the universal mana-target input contract -- a curve-aware strategy (e.g. the
    hypergeometric one) needs it to size the target; the static quota here ignores it.
    """
    _ = curve_by_cmc
    low, high = ROLE_QUOTAS["land"]
    if land_count < low:
        status, delta, bound = "low", low - land_count, low
    elif land_count > high:
        status, delta, bound = "high", land_count - high, high
    else:
        return {
            "status": "ok",
            "count": land_count,
            "quota": [low, high],
            "delta": 0,
            "pct": 0.0,
            "severity": "none",
        }
    pct = delta / bound
    mild, moderate = LAND_SEVERITY_BANDS
    severity = "severe" if pct >= moderate else "moderate" if pct >= mild else "mild"
    return {
        "status": status,
        "count": land_count,
        "quota": [low, high],
        "delta": delta,
        "pct": round(pct, 3),
        "severity": severity,
    }


def min_sources_for(identity: list[str] | None) -> int:
    n = max(len([c for c in (identity or []) if c in COLORS]), 1)
    return MIN_SOURCES.get(n, 8)


def color_floors(
    identity: list[str] | None,
    pip_reqs_by_color: dict[str, list[tuple[int, int]]] | None = None,
) -> dict[str, int]:
    """Minimum source count per color in identity -- the per-color contract shape
    a swappable strategy (e.g. a future hypergeometric one) needs to speak. The
    static strategy has no per-color signal, so every color gets the same
    min_sources_for(identity) floor.

    `pip_reqs_by_color` (per-card (cmc, pips) pairs, part of the universal mana-target
    input contract) is what a pip-aware strategy needs to vary the floor by color;
    ignored here on purpose.
    """
    _ = pip_reqs_by_color
    floor = min_sources_for(identity)
    return {color: floor for color in (identity or []) if color in COLORS}
