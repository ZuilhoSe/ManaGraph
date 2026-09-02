"""Deck shape diagnosis: walks a decklist and reports curve, mana, and role gaps."""

from __future__ import annotations

from archetypes import quotas_for
from catalog import DB_NAME, enrich_deck, get_oracle_cards
from roles import role_counts

from deck_analysis.card_classify import is_cheat_card
from deck_analysis.curve import CURVE_PROFILES, cmc_bucket, curve_plan, select_curve_profile
from deck_analysis.mana_base import PIP_PER_SOURCE_WARN
from deck_analysis.mana_symbols import (
    COLORS,
    _add_pips,
    empty_pips,
    empty_sources,
    is_fast_mana,
    is_land_card,
    parse_mana_cost,
    produced_mana,
)
from deck_analysis.strategies import HypergeometricStrategy, ManaTargetStrategy


def diagnose(
    cards: list[dict],
    commander: dict | None = None,
    identity: list[str] | None = None,
    remaining_slots: int = 0,
    slot_count: int = 0,
    budget_cap: float | None = None,
    budget_used: float | None = None,
    archetype: str | None = None,
    strategy: ManaTargetStrategy | None = None,
) -> dict:
    """Deck shape report. Soft diagnosis — not a legality gate."""
    strategy = strategy or HypergeometricStrategy()
    quotas = quotas_for(archetype)
    identity = list(identity or (commander or {}).get("color_identity") or [])
    pips = empty_pips()
    sources = empty_sources()
    curve = {key: 0 for key in CURVE_PROFILES["mid"]["targets"]}
    # Universal mana-target input contract: exact-CMC curve and per-card (cmc, pips)
    # pairs, collected regardless of which strategy is active below -- the static one
    # ignores them, a curve/pip-aware one (e.g. hypergeometric) needs them. In
    # pip_reqs_by_color a 0-cost pip-bearing card is bucketed as CMC 1, not CMC 0
    # (curve_by_cmc keeps the real 0): a free spell's color still has to be paid on
    # turn 1 at the earliest, so treating it as "CMC 0" would silently drop that color
    # requirement from a pip-aware strategy's target.
    curve_by_cmc: dict[int, int] = {}
    pip_reqs_by_color: dict[str, list[tuple[int, int]]] = {c: [] for c in COLORS}
    land_count = 0
    fast_mana = 0
    cmc_sum = 0.0
    nonlands = 0

    if commander:
        _add_pips(pips, parse_mana_cost(commander.get("mana_cost") or ""))

    for card in cards:
        qty = int(card.get("quantity") or 1)
        tl = card.get("type_line") or ""
        ot = card.get("oracle_text") or ""
        cost = card.get("mana_cost") or ""
        cmc = float(card.get("cmc") or 0)
        prod = produced_mana(tl, ot, cost)
        _add_pips(sources, prod, qty)
        if is_land_card(tl):
            land_count += qty
            continue
        card_pips = parse_mana_cost(cost)
        _add_pips(pips, card_pips, qty)
        curve[cmc_bucket(cmc)] = curve.get(cmc_bucket(cmc), 0) + qty
        curve_cmc = int(cmc)
        curve_by_cmc[curve_cmc] = curve_by_cmc.get(curve_cmc, 0) + qty
        pip_cmc = curve_cmc if curve_cmc >= 1 else 1
        for color in COLORS:
            pip_n = round(card_pips.get(color) or 0)
            if pip_n >= 1:
                pip_reqs_by_color[color].extend([(pip_cmc, pip_n)] * qty)
        cmc_sum += cmc * qty
        nonlands += qty
        if is_fast_mana(tl, ot, cmc):
            fast_mana += qty

    avg_cmc = round(cmc_sum / nonlands, 3) if nonlands else 0.0
    floors = strategy.color_floors(identity, pip_reqs_by_color)
    pips_per_source = {}
    for color in COLORS:
        pool = sources[color] + sources["any"]
        pips_per_source[color] = round(pips[color] / max(pool, 0.5), 3)

    roles = role_counts(cards, archetype)
    cheat_count = 0
    for card in cards:
        if is_cheat_card(card.get("type_line") or "", card.get("oracle_text") or ""):
            cheat_count += int(card.get("quantity") or 1)
    profile = select_curve_profile(commander, cards, roles)
    plan = curve_plan(profile)
    targets = plan["targets"]
    avg_band = plan["avg"]

    deficits: list[str] = []
    land_low, land_high = quotas["land"]
    alert = strategy.land_target(land_count, curve_by_cmc)
    if alert["status"] != "ok":
        direction = "too few" if alert["status"] == "low" else "too many"
        deficits.append(
            f"LAND {alert['severity'].upper()}: {land_count} lands is {direction} by "
            f"{alert['delta']} vs quota {land_low}-{land_high} ({alert['pct'] * 100:.0f}% off)"
        )

    curve_gaps = []
    if nonlands >= 8:
        for bucket, (low, high) in targets.items():
            n = curve.get(bucket, 0)
            status = "ok"
            if n < low:
                status = "low"
                deficits.append(f"curve {bucket}: {n} < {low} ({profile})")
            elif n > high:
                status = "high"
                deficits.append(f"curve {bucket}: {n} > {high} ({profile})")
            curve_gaps.append({"bucket": bucket, "count": n, "low": low, "high": high, "status": status})
        lo, hi = avg_band
        if avg_cmc and avg_cmc < lo:
            deficits.append(f"avg_cmc: {avg_cmc} < {lo} ({profile})")
        elif avg_cmc > hi:
            deficits.append(f"avg_cmc: {avg_cmc} > {hi} ({profile})")

    for color in identity:
        if color not in COLORS:
            continue
        have = sources[color] + sources["any"]
        floor = floors.get(color, 0)
        if have < floor:
            deficits.append(f"sources {color}: {have:g} < {floor}")
        if pips[color] > 0 and pips_per_source[color] > PIP_PER_SOURCE_WARN:
            deficits.append(
                f"pips {color}: {pips[color]:g} on {have:g} sources ({pips_per_source[color]})"
            )

    role_gaps = []
    for role, (low, high) in quotas.items():
        n = roles.get(role, 0)
        status = "ok"
        if n < low:
            status = "low"
            if role != "land" or f"lands: {land_count} < {land_low}" not in deficits:
                deficits.append(f"{role}: {n} < {low}")
        elif n > high:
            status = "high"
        role_gaps.append({"role": role, "count": n, "low": low, "high": high, "status": status})

    round_pips = {k: round(v, 3) for k, v in pips.items()}
    round_src = {k: round(v, 3) for k, v in sources.items()}
    report = {
        "identity": identity,
        "curve": curve,
        "curve_by_cmc": curve_by_cmc,
        "pip_reqs_by_color": pip_reqs_by_color,
        "curve_profile": profile,
        "curve_targets": {k: list(v) for k, v in targets.items()},
        "curve_gaps": curve_gaps,
        "avg_cmc": avg_cmc,
        "avg_cmc_band": list(avg_band),
        "land_count": land_count,
        "land_quota": list(quotas["land"]),
        "archetype": archetype or "generic",
        "nonlands": nonlands,
        "fast_mana": fast_mana,
        "cheat_count": cheat_count,
        "pips": round_pips,
        "sources": round_src,
        "pips_per_source": pips_per_source,
        "min_sources": floors,
        "roles": roles,
        "role_gaps": role_gaps,
        "land_alert": alert,
        "deficits": deficits,
        "remaining_slots": remaining_slots,
        "slot_count": slot_count,
    }
    if budget_cap is not None:
        report["budget_cap"] = budget_cap
        report["budget_used"] = budget_used
        if budget_used is not None:
            report["budget_slack"] = round(float(budget_cap) - float(budget_used), 2)
    report.setdefault("ontology_counts", {})
    report.setdefault("ontology_deficits", [])
    return report


def diagnose_deck(deck, db_path: str = DB_NAME, strategy: ManaTargetStrategy | None = None) -> dict:
    card_list = deck.card_list()
    # One batched query for the whole decklist (+ commander) instead of a
    # get_oracle_card() connection per card -- this runs on every architect
    # turn and every Supervisor gate, so the per-card cost was paid twice per
    # deck (once for the commander's own diagnosis, and again by every other
    # caller doing the same for their own purposes).
    lookup = get_oracle_cards([*card_list.keys(), deck.commander], db_path)
    cards = []
    for name, qty in card_list.items():
        info = lookup.get(name.lower()) or {
            "name": name,
            "mana_cost": "",
            "oracle_text": "",
            "type_line": "",
            "cmc": 0,
        }
        cards.append({**info, "quantity": qty})
    commander = lookup.get(deck.commander.lower()) if deck.commander else None
    budget_used = None
    if deck.budget_cap is not None:
        budget_used = enrich_deck(deck, db_path)["budget_used"]
    report = diagnose(
        cards,
        commander=commander,
        identity=deck.identity or (commander or {}).get("color_identity"),
        remaining_slots=deck.remaining_slots(),
        slot_count=deck.slot_count(),
        budget_cap=deck.budget_cap,
        budget_used=budget_used,
        archetype=getattr(deck, "archetype", None),
        strategy=strategy,
    )
    from ontology.diagnose import attach_ontology_deficits

    names = [*card_list.keys()]
    if deck.commander:
        names.append(deck.commander)
    attach_ontology_deficits(report, names, db_path, card_list)
    return report
