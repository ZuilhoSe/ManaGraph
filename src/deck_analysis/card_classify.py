"""Heuristic card classification from oracle text: cheat effects, ramp/fast commanders."""

from __future__ import annotations

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
