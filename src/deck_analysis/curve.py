"""Curve-shape policy: which fast/mid/high profile a deck should follow, and its bucketing."""

from __future__ import annotations

from roles import ROLE_QUOTAS, role_counts

from deck_analysis.card_classify import is_cheat_card, is_fast_commander, is_ramp_commander
from deck_analysis.mana_symbols import is_land_card

# Curve is a function of plan, not a universal "low is good".
# fast = win early; mid = default; high = ramp or cheat into expensive spells.
CURVE_PROFILES = {
    "fast": {
        "avg": (2.0, 3.2),
        "targets": {
            "0": (0, 6),
            "1": (8, 16),
            "2": (12, 20),
            "3": (8, 14),
            "4": (3, 8),
            "5": (0, 4),
            "6": (0, 2),
            "7+": (0, 1),
        },
    },
    "mid": {
        "avg": (2.4, 3.6),
        "targets": {
            "0": (0, 4),
            "1": (6, 14),
            "2": (10, 18),
            "3": (8, 16),
            "4": (4, 12),
            "5": (2, 8),
            "6": (0, 4),
            "7+": (0, 3),
        },
    },
    "high": {
        "avg": (2.8, 4.4),
        "targets": {
            "0": (0, 4),
            "1": (4, 12),
            "2": (8, 16),
            "3": (8, 16),
            "4": (6, 14),
            "5": (4, 12),
            "6": (2, 8),
            "7+": (2, 8),
        },
    },
}
CURVE_TARGETS = CURVE_PROFILES["mid"]["targets"]
AVG_CMC_BAND = CURVE_PROFILES["mid"]["avg"]


def cmc_bucket(cmc: float) -> str:
    if float(cmc or 0) >= 7:
        return "7+"
    return str(int(float(cmc or 0)))


def select_curve_profile(
    commander: dict | None,
    cards: list[dict],
    roles: dict[str, int] | None = None,
) -> str:
    """Ramp/cheat can support a high curve; a fast commander with little ramp wants a low one."""
    roles = roles or role_counts(cards)
    ramp_n = int(roles.get("ramp") or 0)
    cheat_n = 0
    nonlands = 0
    for card in cards:
        qty = int(card.get("quantity") or 1)
        if is_land_card(card.get("type_line") or ""):
            continue
        nonlands += qty
        if is_cheat_card(card.get("type_line") or "", card.get("oracle_text") or ""):
            cheat_n += qty

    if nonlands < 8:
        if is_ramp_commander(commander):
            return "high"
        if is_fast_commander(commander):
            return "fast"
        return "mid"

    if cheat_n >= 2 or ramp_n >= 10:
        return "high"
    if is_fast_commander(commander) and ramp_n < 8 and cheat_n == 0:
        return "fast"
    if ramp_n >= ROLE_QUOTAS["ramp"][0] and not is_fast_commander(commander):
        return "high"
    return "mid"


def curve_plan(profile: str) -> dict:
    return CURVE_PROFILES.get(profile) or CURVE_PROFILES["mid"]
