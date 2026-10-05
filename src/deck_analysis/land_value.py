"""A nonbasic land's value relative to the basic it would replace (plan Fase 0).

Basics are the default mana base. A nonbasic earns its slot only by swap, when

    Δ = colour access gained − cost(enters tapped) − cost(drawbacks) + ramp + utility > 0

measured against the best basic for the deck's current colour needs. Drawbacks are
predicates read off the Oracle text, so "gives an opponent something" or "only
pays for Slivers" is a fact about the card, not something cosine similarity to
the commander can paper over.

Costs are in "fraction of one untapped, on-colour land drop". They are a stopgap:
Fase 5 replaces this with lands as mana operators priced by V(D).
"""

from __future__ import annotations

import re

from deck_analysis.mana_symbols import COLORS, LAND_TYPES

# Fatal: the land's mana is paid for by helping an opponent.
FATAL_COST = 10.0
DRAWBACK_COSTS = {
    "opponent_benefit": FATAL_COST,
    "gives_away": FATAL_COST,
    "nonland_front": FATAL_COST,  # a spell that only later becomes a land
    "sacrifices_lands": 2.0,  # two or more land drops thrown away
    "sacrifices_land": 1.0,
    "self_sacrifice": 1.0,
    "no_untap": 1.0,
    "upkeep_cost": 1.0,
    "depletes": 0.6,
    "enters_tapped": 0.5,
    "self_bounce": 0.5,
    "bounces_land": 0.3,
    "pain": 0.3,
    "spend_restricted": 0.2,
    "conditional_tapped": 0.15,
}
# Colour access per ability kind: a filter that nets no mana is not a source.
ACCESS_FULL = 1.0
ACCESS_UNCERTAIN = 0.6  # Exotic Orchard: depends on what opponents play
ACCESS_MODAL = 0.6  # pick one face (Pathways)
ACCESS_FILTER = 0.5  # nets mana but needs coloured input (Rugged Prairie)
ACCESS_EXTRA_COST = 0.5  # tap a creature, sacrifice a creature, pay energy
# Weight of a colour whose source floor is met. Fixing is worth ΔP(right sources
# by turn t), which is ~0 once the floor holds, so a dual stops beating a basic.
SATISFIED_COLOR_WEIGHT = 0.0
RAMP_VALUE = 0.5  # per extra mana per tap
UTILITY_VALUE = 0.4
UTILITY_CAP = 1

_ADD_RE = re.compile(r"\badd\b", re.I)
_SYMBOL_RE = re.compile(r"\{([^}]+)\}")
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_FETCH_RE = re.compile(
    r"search your library for (?:a|an|up to \w+) ([^.]*?)cards?\b", re.I
)
_OPPONENT_BENEFIT_RE = re.compile(
    r"\b(?:target|an|each|that) (?:opponent|player)\b[^.]*?\b"
    r"(?:creates?|draws?|gains? (?!control)|adds?|puts?)\b",
    re.I,
)


_REMINDER_RE = re.compile(r"\([^)]*\)")
_SACRIFICE_LANDS_RE = re.compile(
    r"sacrifice (two|three|a|an) (?:untapped )?"
    r"(?:lands?|plains|islands?|swamps?|mountains?|forests?)\b"
)
_BASIC_GATE_RE = re.compile(
    r"activate only if you control an? (?:plains|island|swamp|mountain|forest)\b"
)
_TYPE_GATED_RE = re.compile(r"activate only if you control an? |tap an untapped \w+ you control")


def _line_drawbacks(low: str) -> set[str]:
    """Drawback predicates on one lowercased Oracle line (any ability kind)."""
    found: set[str] = set()
    if "damage to you" in low:
        found.add("pain")
    if "enters tapped" in low:
        found.add("conditional_tapped" if "unless" in low or "if you don't" in low else "enters_tapped")
    if "gains control of this" in low:
        found.add("gives_away")
    if _OPPONENT_BENEFIT_RE.search(low):
        found.add("opponent_benefit")
    sacrificed = _SACRIFICE_LANDS_RE.search(low)
    if sacrificed and "search" not in low:
        found.add("sacrifices_land" if sacrificed.group(1) in ("a", "an") else "sacrifices_lands")
    if "return a land you control to its owner's hand" in low:
        found.add("bounces_land")
    if "return this land to its owner's hand" in low:
        found.add("self_bounce")
    if re.search(r"\b(?:at the beginning|when)\b[^.]*sacrifice this land", low):
        found.add("self_sacrifice")
    if "cumulative upkeep" in low or ("beginning of your upkeep" in low and "unless you pay" in low):
        found.add("upkeep_cost")
    if "doesn't untap" in low:
        found.add("no_untap")
    return found


def _effect_access(effect_low: str) -> float:
    """How reliably a mana ability's effect delivers the colours it names."""
    if "could produce" in effect_low or " among " in effect_low or "drafted" in effect_low:
        # Depends on what someone else plays (Exotic Orchard) or on board state we
        # do not model (Gond Gate, The Grey Havens, Paliano).
        return ACCESS_UNCERTAIN if "opponent" in effect_low else 0.0
    if "instead add" in effect_low:
        return 0.0  # Gemstone Caverns: colour only with a luck counter
    if "activate only" in effect_low and not _BASIC_GATE_RE.search(effect_low):
        return 0.0  # Mirrex: only the turn it entered
    return ACCESS_FULL


def _lines(oracle_text: str) -> list[str]:
    return [line.strip() for line in (oracle_text or "").split("\n") if line.strip()]


def _split_cost(line: str) -> tuple[str, str]:
    """`{1}, {T}: Add {R}.` → (`{1}, {T}`, `Add {R}.`). No colon → ('', line)."""
    if ":" not in line:
        return "", line
    cost, effect = line.split(":", 1)
    return cost, effect


def _mana_in_cost(cost: str) -> float:
    total = 0.0
    for sym in _SYMBOL_RE.findall(cost):
        s = sym.upper()
        if s in ("T", "Q", "E"):
            continue
        if s.isdigit():
            total += int(s)
        else:
            total += 1  # coloured, hybrid, {C}
    return total


def _mana_out(effect: str) -> tuple[set[str], float, bool]:
    """Colours reachable, mana amount, and whether it is 'any colour'."""
    tail = _ADD_RE.split(effect, 1)[-1]
    after = tail.lower()
    count_match = re.search(r"\b(one|two|three|four|five)\s+mana\b", after)
    if "any color" in after or "any one color" in after or "any combination of colors" in after:
        n = _WORD_NUM.get(count_match.group(1), 1) if count_match else 1
        return set(COLORS), float(n), True
    colors: set[str] = set()
    for s in (sym.upper() for sym in _SYMBOL_RE.findall(tail)):
        if s in COLORS:
            colors.add(s)
    # "{R} or {W}" / "{R}{R}, {R}{W}, or {W}{W}": one choice's worth of mana.
    first_choice = re.split(r",|\bor\b", tail, maxsplit=1)[0]
    amount = float(
        sum(int(s) if s.isdigit() else 1 for s in _SYMBOL_RE.findall(first_choice))
    )
    if count_match:
        amount = float(_WORD_NUM.get(count_match.group(1), 1))
    return colors, max(amount, 1.0), False


def land_profile(type_line: str, oracle_text: str, identity: list[str] | None) -> dict:
    """Colour access, ramp, drawbacks and utility of one land, for this identity."""
    ident = [c for c in (identity or []) if c in COLORS]
    access: dict[str, float] = {}
    drawbacks: set[str] = set()
    utility: set[str] = set()
    max_mana = 0.0

    def grant(colors, level: float):
        for c in colors:
            if c in ident:
                access[c] = max(access.get(c, 0.0), level)

    tl = (type_line or "").lower()
    faces = [face.strip() for face in tl.split("//")]
    if "land" not in faces[0]:
        return {"access": {}, "max_mana": 0.0, "drawbacks": ["nonland_front"], "utility": []}
    modal = len(faces) > 1 and all("land" in face for face in faces)
    typed = {color for word, color in LAND_TYPES.items() if word in tl}
    if typed:
        grant(typed, ACCESS_FULL)
        max_mana = 1.0

    for raw in _lines(oracle_text):
        # Reminder text repeats or explains another line; it is never its own ability.
        line = _REMINDER_RE.sub("", raw).strip()
        if not line:
            continue
        low = line.lower()
        drawbacks |= _line_drawbacks(low)
        cost, effect = _split_cost(line)
        effect_low = effect.lower()
        cost_low = cost.lower()

        if _ADD_RE.search(effect_low) and "{t}" in cost_low:
            if "spend this mana only" in effect_low:
                drawbacks.add("spend_restricted")
                continue
            colors, amount, _any = _mana_out(effect)
            if "commander's color identity" in effect_low or "commanders' color identity" in effect_low:
                colors = set(ident)
            input_mana = _mana_in_cost(cost)
            if "pay" in cost_low and "life" in cost_low:
                drawbacks.add("pain")
            if "counter from" in cost_low:
                drawbacks.add("depletes")
            net = amount - input_mana
            level = _effect_access(effect_low)
            if "sacrifice this land" in cost_low or "sacrifice ~" in cost_low:
                level = 0.0  # one shot, not a source
            elif "sacrifice" in cost_low or "tap an untapped" in cost_low or "{e}" in cost_low:
                level = min(level, ACCESS_EXTRA_COST)
            if input_mana > 0:
                level = min(level, ACCESS_FILTER if net > 0 else 0.0)
            grant(colors, level)
            max_mana = max(max_mana, net)
            continue

        fetch = _FETCH_RE.search(line)
        if fetch and "sacrifice" in cost_low:
            target = fetch.group(1).lower()
            if "basic land" in target and not any(w in target for w in LAND_TYPES):
                fetched = set(ident)
            else:
                fetched = {color for word, color in LAND_TYPES.items() if word in target}
            grant(fetched, ACCESS_FULL)
            max_mana = max(max_mana, 1.0)
            if "battlefield tapped" in low or _mana_in_cost(cost) > 0:
                drawbacks.add("enters_tapped")
            if "pay" in cost_low and "life" in cost_low:
                drawbacks.add("pain")
            continue

        # An ability gated on a creature type ("Activate only if you control a
        # Sliver") is tribal, not general utility.
        if "{t}" in cost_low and not _TYPE_GATED_RE.search(low):
            utility.add("activated")
        elif low.startswith("you have no maximum hand size"):
            utility.add("static")
        elif "scry" in low and "enters" in low:
            utility.add("etb_scry")

    if modal:  # one face is chosen on play
        access = {c: min(level, ACCESS_MODAL) for c, level in access.items()}
    return {
        "access": access,
        "max_mana": max_mana,
        "drawbacks": sorted(drawbacks),
        "utility": sorted(utility),
    }


def color_weights(
    identity: list[str] | None,
    sources: dict | None = None,
    floors: dict | None = None,
) -> dict[str, float]:
    """1.0 for a colour still short of its source floor, SATISFIED_COLOR_WEIGHT otherwise."""
    sources = sources or {}
    floors = floors or {}
    weights = {}
    for c in identity or []:
        if c not in COLORS:
            continue
        have = float(sources.get(c) or 0) + float(sources.get("any") or 0)
        weights[c] = 1.0 if have < float(floors.get(c) or 0) or not floors else SATISFIED_COLOR_WEIGHT
    return weights


def land_delta(
    type_line: str,
    oracle_text: str,
    identity: list[str] | None,
    sources: dict | None = None,
    floors: dict | None = None,
) -> dict:
    """Δ of this land over the best basic, with the terms that make it up."""
    profile = land_profile(type_line, oracle_text, identity)
    weights = color_weights(identity, sources, floors)
    access = profile["access"]
    if weights:
        value = sum(weights[c] * access.get(c, 0.0) for c in weights)
        basic_value = max(weights.values())
    else:  # colourless commander: every land is a Wastes-equivalent
        value = 1.0 if profile["max_mana"] >= 1 else 0.0
        basic_value = 1.0
    ramp = RAMP_VALUE * max(0.0, profile["max_mana"] - 1.0)
    utility = UTILITY_VALUE * min(len(profile["utility"]), UTILITY_CAP)
    costs = {d: DRAWBACK_COSTS.get(d, 0.0) for d in profile["drawbacks"]}
    delta = value - basic_value + ramp + utility - sum(costs.values())
    return {
        **profile,
        "delta": round(delta, 4),
        "access_gain": round(value - basic_value, 4),
        "ramp": ramp,
        "utility_value": utility,
        "costs": costs,
    }
