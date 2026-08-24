"""Turns a diagnose() report into a soft fill/cut score term for the solver."""

from __future__ import annotations

from roles import ROLE_QUOTAS

from deck_analysis.curve import CURVE_TARGETS, cmc_bucket
from deck_analysis.mana_base import (
    LAND_SEVERITY_SCALE,
    PIP_PER_SOURCE_WARN,
    color_floors,
    land_alert,
)
from deck_analysis.mana_symbols import is_fast_mana, is_land_card, parse_mana_cost, produced_mana


def shape_bonus(info: dict, report: dict | None, identity: list[str] | None = None) -> dict:
    """Soft fill/cut terms from the current diagnosis. Modest so geometry still leads."""
    empty = {"total": 0.0, "curve_penalty": 0.0, "curve_bonus": 0.0, "land_bonus": 0.0, "mana_bonus": 0.0}
    if not report:
        return empty
    identity = list(identity or report.get("identity") or [])
    tl = info.get("type_line") or ""
    ot = info.get("oracle_text") or ""
    cost = info.get("mana_cost") or ""
    cmc = float(info.get("cmc") or 0)
    land_bonus = 0.0
    curve_bonus = 0.0
    curve_penalty = 0.0
    mana_bonus = 0.0
    prod = produced_mana(tl, ot, cost)
    land_count = int(report.get("land_count") or 0)
    land_low, land_high = ROLE_QUOTAS["land"]
    alert = report.get("land_alert") or land_alert(land_count, report.get("curve_by_cmc"))
    severity_scale = LAND_SEVERITY_SCALE.get(alert.get("severity"), 1.0)

    if is_land_card(tl):
        if alert["status"] == "low":
            land_bonus = 0.8 * severity_scale
        elif alert["status"] == "high":
            land_bonus = -1.5 * severity_scale
        needed = False
        floors = report.get("min_sources") or color_floors(identity, report.get("pip_reqs_by_color"))
        sources = report.get("sources") or {}
        for color in identity:
            have = float(sources.get(color) or 0) + float(sources.get("any") or 0)
            if have < floors.get(color, 0) and (prod.get(color) or prod.get("any")):
                needed = True
        if needed:
            mana_bonus += 0.5
        total = land_bonus + mana_bonus
        return {
            "total": total,
            "curve_penalty": 0.0,
            "curve_bonus": 0.0,
            "land_bonus": land_bonus,
            "mana_bonus": mana_bonus,
        }

    bucket = cmc_bucket(cmc)
    n = int((report.get("curve") or {}).get(bucket) or 0)
    raw_targets = report.get("curve_targets") or CURVE_TARGETS
    bounds = raw_targets.get(bucket, (0, 18))
    low, high = bounds[0], bounds[1]
    if n < low:
        curve_bonus = 1.0
    elif n > high:
        curve_penalty = 1.0

    sources = report.get("sources") or {}
    floors = report.get("min_sources") or color_floors(identity, report.get("pip_reqs_by_color"))
    for color in identity:
        have = float(sources.get(color) or 0) + float(sources.get("any") or 0)
        if have < floors.get(color, 0) and (prod.get(color) or prod.get("any") or prod.get("C")):
            mana_bonus += 0.6
            break
    if is_fast_mana(tl, ot, cmc) and int(report.get("fast_mana") or 0) < 4:
        mana_bonus += 0.4

    pips_ps = report.get("pips_per_source") or {}
    cost_pips = parse_mana_cost(cost)
    for color in identity:
        if float(pips_ps.get(color) or 0) > PIP_PER_SOURCE_WARN and cost_pips.get(color, 0) >= 2:
            mana_bonus -= 0.4
            break

    total = land_bonus + curve_bonus - curve_penalty + mana_bonus
    return {
        "total": total,
        "curve_penalty": curve_penalty,
        "curve_bonus": curve_bonus,
        "land_bonus": land_bonus,
        "mana_bonus": mana_bonus,
    }
