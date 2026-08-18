from __future__ import annotations

import re

ROLE_QUOTAS = {
    "land": (34, 38),
    "ramp": (8, 14),
    "draw": (8, 14),
    "interaction": (8, 15),
    "threat": (12, 40),
}

_RAMP = (
    "search your library for a land",
    "search your library for a basic",
    "add {",
    "add one mana",
    "add two mana",
    "treasure token",
    "create a treasure",
)
_DRAW_UNITS = (
    (re.compile(r"draw three cards"), 3),
    (re.compile(r"draw two cards"), 2),
    (re.compile(r"draws a card"), 1),
    (re.compile(r"draw a card"), 1),
)
_DISCARD_UNITS = (
    (re.compile(r"discard three cards"), 3),
    (re.compile(r"discard two cards"), 2),
    (re.compile(r"discard a card"), 1),
    (re.compile(r"discard one card"), 1),
)
_INTERACTION = (
    "destroy target",
    "exile target",
    "counter target",
    "damage to",
    "deals damage",
    "fight",
    "return target",
    "sacrifice a creature",
)


def _count_units(text: str, units: tuple) -> int:
    return sum(weight * len(rx.findall(text)) for rx, weight in units)


def is_card_advantage_draw(oracle_text: str) -> bool:
    """True if the card draws and is not a net-negative loot (Bazaar: draw 2, discard 3)."""
    ot = (oracle_text or "").lower()
    draws = _count_units(ot, _DRAW_UNITS)
    if draws == 0 and "loot" in ot:
        draws = 1
    if draws == 0:
        return False
    discards = _count_units(ot, _DISCARD_UNITS)
    return draws >= discards


def classify_roles(type_line: str = "", oracle_text: str = "") -> set[str]:
    tl = (type_line or "").lower()
    ot = (oracle_text or "").lower()
    roles: set[str] = set()
    is_land = "land" in tl
    if is_land:
        roles.add("land")
    if not is_land and any(p in ot for p in _RAMP):
        roles.add("ramp")
    if is_card_advantage_draw(oracle_text):
        roles.add("draw")
    if any(p in ot for p in _INTERACTION):
        roles.add("interaction")
    if "creature" in tl or "planeswalker" in tl:
        roles.add("threat")
    if not roles:
        roles.add("other")
    return roles


def role_counts(cards: list[dict]) -> dict[str, int]:
    counts = {role: 0 for role in list(ROLE_QUOTAS) + ["other"]}
    for card in cards:
        qty = int(card.get("quantity", 1))
        for role in classify_roles(card.get("type_line") or "", card.get("oracle_text") or ""):
            counts[role] = counts.get(role, 0) + qty
    return counts


def role_need_bonus(role: str, counts: dict[str, int]) -> float:
    if role not in ROLE_QUOTAS:
        return 0.0
    low, high = ROLE_QUOTAS[role]
    n = counts.get(role, 0)
    if n < low:
        return 2.5
    if n < high:
        return 0.4
    return -1.5
