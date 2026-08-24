"""Mana-cost parsing and mana-production detection: what a card costs and what it makes.

Pure fact extraction from card text, no judgment about whether a deck's mana
base is any good.
"""

from __future__ import annotations

import re

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
