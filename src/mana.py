"""Symbolic mana and curve. Numbers the solver adds — not embeddings, not the LLM."""

from __future__ import annotations

import re

from archetypes import quotas_for
from catalog import DB_NAME, enrich_deck, get_oracle_card
from roles import ROLE_QUOTAS, role_counts

COLORS = ("W", "U", "B", "R", "G")
LAND_TYPES = {
    "plains": "W",
    "island": "U",
    "swamp": "B",
    "mountain": "R",
    "forest": "G",
}
WORD_NUM = {
    "a": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
}

SYMBOL_RE = re.compile(r"\{([^}]+)\}")
ADD_BRACES_RE = re.compile(
    r"(?i)add\s+((?:\{[^}]+\}(?:\s*(?:or|/|,|and)\s*)?)+)"
)
ADD_ANY_RE = re.compile(
    r"(?i)add\s+(a|one|two|three|four|five|six|seven|\d+)\s+mana\s+of\s+any(?:\s+one)?\s+color"
)
TREASURE_RE = re.compile(r"(?i)(?:create|creates)\s+(a|one|two|three|four|five|\d+)?\s*treasure")
# Urborg / Yavimaya: grant a basic land type (and thus {T}: Add that color).
_LAND_TYPE_GRANT = re.compile(
    r"(?i)(?:each land|lands? you control)\s+(?:is|are|becomes?)\s+(?:a\s+)?(plains|island|swamp|mountain|forest)"
)

# Cheat: put a big spell into play without paying its printed CMC.
_CHEAT = (
    "without paying",
    "onto the battlefield",
    "from your graveyard to the battlefield",
    "from your hand onto the battlefield",
    "cascade",
    "discover",
    "unearth",
    "put a creature card",
    "put creature cards",
    "return target creature card from your graveyard",
)
_RAMP_CMD = (
    "search your library for a land",
    "search your library for two land",
    "play an additional land",
    "extra land",
)
_FAST_TRIBES = ("goblin", "elf", "sliver", "soldier", "warrior")

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
PIP_PER_SOURCE_WARN = 2.0
MIN_SOURCES = {1: 14, 2: 10, 3: 7, 4: 6, 5: 5}
# Land count off-quota, as a fraction of the breached bound: <15% mild, 15-40% moderate, >40% severe.
LAND_SEVERITY_BANDS = (0.15, 0.40)
LAND_SEVERITY_SCALE = {"none": 0.0, "mild": 1.0, "moderate": 1.8, "severe": 3.0}


def land_alert(land_count: int) -> dict:
    """How far the land count sits outside ROLE_QUOTAS['land'], with a severity tier.

    One flat string among a dozen deficits reads the same for 39 lands and 67 lands.
    This gives a dedicated, magnitude-aware signal so both the solver bias and the
    human-facing warning can react proportionally instead of just "in range or not".
    """
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


def cmc_bucket(cmc: float) -> str:
    if float(cmc or 0) >= 7:
        return "7+"
    return str(int(float(cmc or 0)))


def empty_pips() -> dict[str, float]:
    pips = {c: 0.0 for c in COLORS}
    pips.update({"C": 0.0, "generic": 0.0, "x": 0.0, "hybrid": 0.0, "phyrexian": 0.0, "colored": 0.0})
    return pips


def empty_sources() -> dict[str, float]:
    src = {c: 0.0 for c in COLORS}
    src.update({"C": 0.0, "any": 0.0})
    return src


def _add_pips(dst: dict, src: dict, qty: float = 1.0):
    for key, value in src.items():
        dst[key] = dst.get(key, 0.0) + value * qty


def parse_mana_cost(cost: str | None) -> dict[str, float]:
    """Parse a Scryfall mana_cost string into pip counts."""
    pips = empty_pips()
    if not cost:
        return pips
    for inner in SYMBOL_RE.findall(cost):
        token = inner.strip().upper()
        if token == "X":
            pips["x"] += 1
            continue
        if token in ("S", "H"):  # snow / half
            pips["generic"] += 1
            continue
        if token.isdigit():
            pips["generic"] += int(token)
            continue
        if token == "C":
            pips["C"] += 1
            continue
        if "/" in token:
            parts = [p for p in token.split("/") if p]
            colored = [p for p in parts if p in COLORS]
            generic = [p for p in parts if p.isdigit()]
            phy = "P" in parts
            if phy and colored:
                pips[colored[0]] += 1
                pips["phyrexian"] += 1
                pips["colored"] += 1
            elif generic and colored:
                pips[colored[0]] += 0.5
                pips["generic"] += 0.5
                pips["hybrid"] += 1
                pips["colored"] += 0.5
            elif len(colored) >= 2:
                share = 1.0 / len(colored)
                for color in colored:
                    pips[color] += share
                pips["hybrid"] += 1
                pips["colored"] += 1
            continue
        if token in COLORS:
            pips[token] += 1
            pips["colored"] += 1
    return pips


def _qty_word(text: str | None) -> int:
    if not text:
        return 1
    raw = text.strip().lower()
    if raw.isdigit():
        return int(raw)
    return WORD_NUM.get(raw, 1)


def produced_mana(
    type_line: str = "",
    oracle_text: str = "",
    mana_cost: str = "",
) -> dict[str, float]:
    """How much mana this card can add. Lands with basic types use the type line only."""
    src = empty_sources()
    tl = type_line or ""
    ot = oracle_text or ""
    tl_l = tl.lower()
    is_land = "land" in tl_l
    typed = []
    for word, color in LAND_TYPES.items():
        if word in tl_l:
            src[color] += 1
            typed.append(color)

    if is_land and typed:
        return src

    for match in ADD_BRACES_RE.finditer(ot):
        chunk = match.group(1)
        symbols = [s.upper() for s in SYMBOL_RE.findall(chunk)]
        flexible = bool(re.search(r"(?i)\bor\b|/|,", chunk))
        if flexible:
            src["any"] += 1
            continue
        for token in symbols:
            if token in COLORS:
                src[token] += 1
            elif token == "C":
                src["C"] += 1
            elif token.isdigit():
                src["C"] += int(token)

    for match in ADD_ANY_RE.finditer(ot):
        src["any"] += _qty_word(match.group(1))

    if TREASURE_RE.search(ot):
        src["any"] += 1

    for match in _LAND_TYPE_GRANT.finditer(ot):
        color = LAND_TYPES.get(match.group(1).lower())
        if color:
            src[color] += 1

    _ = mana_cost
    return src


def produces_mana(type_line: str = "", oracle_text: str = "", mana_cost: str = "") -> bool:
    src = produced_mana(type_line, oracle_text, mana_cost)
    return any(src[k] > 0 for k in (*COLORS, "C", "any"))


def is_land_card(type_line: str) -> bool:
    return "land" in (type_line or "").lower()


def is_fast_mana(type_line: str, oracle_text: str, cmc: float) -> bool:
    if is_land_card(type_line):
        return False
    if float(cmc or 0) > 1:
        return False
    produced = produced_mana(type_line, oracle_text)
    return any(produced[k] > 0 for k in (*COLORS, "C", "any"))


def min_sources_for(identity: list[str] | None) -> int:
    n = max(len([c for c in (identity or []) if c in COLORS]), 1)
    return MIN_SOURCES.get(n, 8)


def is_cheat_card(type_line: str = "", oracle_text: str = "") -> bool:
    ot = (oracle_text or "").lower()
    return any(phrase in ot for phrase in _CHEAT)


def is_fast_commander(commander: dict | None) -> bool:
    """Token / combat / low-CMC tribal: wants to win before a high curve matters."""
    if not commander:
        return False
    ot = (commander.get("oracle_text") or "").lower()
    tl = (commander.get("type_line") or "").lower()
    cmc = float(commander.get("cmc") or 0)
    if cmc > 4:
        return False
    tokens = "token" in ot and ("create" in ot or "creates" in ot)
    haste = "haste" in ot
    combat = "combat damage" in ot or "attacks" in ot
    tribal = any(word in tl for word in _FAST_TRIBES) and "creature" in tl
    return tokens or haste or combat or tribal


def is_ramp_commander(commander: dict | None) -> bool:
    if not commander:
        return False
    ot = (commander.get("oracle_text") or "").lower()
    return any(phrase in ot for phrase in _RAMP_CMD) or ot.count("add {") >= 2


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


def diagnose(
    cards: list[dict],
    commander: dict | None = None,
    identity: list[str] | None = None,
    remaining_slots: int = 0,
    slot_count: int = 0,
    budget_cap: float | None = None,
    budget_used: float | None = None,
    archetype: str | None = None,
) -> dict:
    """Deck shape report. Soft diagnosis — not a legality gate."""
    quotas = quotas_for(archetype)
    identity = list(identity or (commander or {}).get("color_identity") or [])
    pips = empty_pips()
    sources = empty_sources()
    curve = {key: 0 for key in CURVE_PROFILES["mid"]["targets"]}
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
        _add_pips(pips, parse_mana_cost(cost), qty)
        curve[cmc_bucket(cmc)] = curve.get(cmc_bucket(cmc), 0) + qty
        cmc_sum += cmc * qty
        nonlands += qty
        if is_fast_mana(tl, ot, cmc):
            fast_mana += qty

    avg_cmc = round(cmc_sum / nonlands, 3) if nonlands else 0.0
    min_src = min_sources_for(identity)
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
    alert = land_alert(land_count)
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
        if have < min_src:
            deficits.append(f"sources {color}: {have:g} < {min_src}")
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
        "min_sources": min_src,
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
    return report


def diagnose_deck(deck, db_path: str = DB_NAME) -> dict:
    cards = []
    for name, qty in deck.card_list().items():
        info = get_oracle_card(name, db_path) or {
            "name": name,
            "mana_cost": "",
            "oracle_text": "",
            "type_line": "",
            "cmc": 0,
        }
        cards.append({**info, "quantity": qty})
    commander = get_oracle_card(deck.commander, db_path) if deck.commander else None
    budget_used = None
    if deck.budget_cap is not None:
        budget_used = enrich_deck(deck, db_path)["budget_used"]
    return diagnose(
        cards,
        commander=commander,
        identity=deck.identity or (commander or {}).get("color_identity"),
        remaining_slots=deck.remaining_slots(),
        slot_count=deck.slot_count(),
        budget_cap=deck.budget_cap,
        budget_used=budget_used,
        archetype=getattr(deck, "archetype", None),
    )


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
    alert = report.get("land_alert") or land_alert(land_count)
    severity_scale = LAND_SEVERITY_SCALE.get(alert.get("severity"), 1.0)

    if is_land_card(tl):
        if alert["status"] == "low":
            land_bonus = 0.8 * severity_scale
        elif alert["status"] == "high":
            land_bonus = -1.5 * severity_scale
        needed = False
        min_src = int(report.get("min_sources") or min_sources_for(identity))
        sources = report.get("sources") or {}
        for color in identity:
            have = float(sources.get(color) or 0) + float(sources.get("any") or 0)
            if have < min_src and (prod.get(color) or prod.get("any")):
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
    min_src = int(report.get("min_sources") or min_sources_for(identity))
    for color in identity:
        have = float(sources.get(color) or 0) + float(sources.get("any") or 0)
        if have < min_src and (prod.get(color) or prod.get("any") or prod.get("C")):
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
