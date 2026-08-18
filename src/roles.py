from __future__ import annotations

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
_DRAW = (
    "draw a card",
    "draw two cards",
    "draw three cards",
    "draws a card",
    "loot",
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


def classify_roles(type_line: str = "", oracle_text: str = "") -> set[str]:
    tl = (type_line or "").lower()
    ot = (oracle_text or "").lower()
    roles: set[str] = set()
    is_land = "land" in tl
    if is_land:
        roles.add("land")
    if not is_land and any(p in ot for p in _RAMP):
        roles.add("ramp")
    if any(p in ot for p in _DRAW):
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
