"""Commander game plans. Tribal is one plan, not the default."""

from __future__ import annotations

from dataclasses import dataclass

from roles import ROLE_QUOTAS

VALID_ARCHETYPES = (
    "control",
    "mill",
    "stax",
    "combo",
    "tokens",
    "tribal",
    "spellslinger",
    "voltron",
    "reanimator",
    "aggro",
    "midrange",
    "generic",
)

_TRIBE_WORDS = (
    "goblin",
    "goblins",
    "elf",
    "elves",
    "merfolk",
    "zombie",
    "zombies",
    "vampire",
    "vampires",
    "dragon",
    "dragons",
    "sliver",
    "slivers",
    "pirate",
    "pirates",
    "dinosaur",
    "dinosaurs",
    "angel",
    "angels",
    "demon",
    "demons",
    "elfball",
    "typal",
    "tribal",
)

_BASE_SEARCH = (
    "draw a card",
    "draw two cards",
    "add mana ramp artifact",
    "basic land",
)


@dataclass(frozen=True)
class ArchetypeProfile:
    enforce_tribe: bool = False
    retrieve_tribe: bool = False
    creature_is_threat: bool = True
    max_creatures: int | None = None
    protect_roles: tuple[str, ...] = ()
    quotas: dict[str, tuple[int, int]] | None = None
    search_queries: tuple[str, ...] = ()


_PROFILES: dict[str, ArchetypeProfile] = {
    "tribal": ArchetypeProfile(
        enforce_tribe=True,
        retrieve_tribe=True,
        creature_is_threat=True,
        max_creatures=None,
        protect_roles=("threat",),
        quotas={"threat": (12, 28), "interaction": (6, 12)},
        search_queries=("creature tokens", "lord anthem creatures you control"),
    ),
    "tokens": ArchetypeProfile(
        enforce_tribe=False,
        retrieve_tribe=False,
        creature_is_threat=True,
        max_creatures=None,
        protect_roles=("threat",),
        quotas={"threat": (10, 24)},
        search_queries=("create creature tokens", "tokens you control"),
    ),
    "aggro": ArchetypeProfile(
        enforce_tribe=False,
        retrieve_tribe=False,
        creature_is_threat=True,
        max_creatures=None,
        quotas={"threat": (16, 32), "interaction": (6, 12)},
        search_queries=("haste creature", "deals damage to"),
    ),
    "midrange": ArchetypeProfile(
        creature_is_threat=True,
        max_creatures=28,
        quotas={"threat": (10, 20), "interaction": (8, 14)},
        search_queries=("destroy target creature", "draw a card"),
    ),
    "control": ArchetypeProfile(
        enforce_tribe=False,
        retrieve_tribe=False,
        creature_is_threat=False,
        max_creatures=14,
        protect_roles=("interaction", "draw"),
        quotas={
            "threat": (3, 10),
            "interaction": (12, 22),
            "draw": (10, 16),
            "ramp": (8, 12),
        },
        search_queries=(
            "counter target spell",
            "return target to owner hand",
            "destroy target creature",
            "draw a card",
            "draw two cards",
            "exile the top card of your library",
        ),
    ),
    "mill": ArchetypeProfile(
        enforce_tribe=False,
        retrieve_tribe=False,
        creature_is_threat=False,
        max_creatures=18,
        protect_roles=("mill", "draw"),
        quotas={
            "mill": (10, 18),
            "threat": (4, 12),
            "interaction": (6, 12),
            "draw": (8, 14),
        },
        search_queries=(
            "mill target player",
            "each opponent mills",
            "library into their graveyard",
            "draw a card",
        ),
    ),
    "stax": ArchetypeProfile(
        creature_is_threat=False,
        max_creatures=16,
        protect_roles=("interaction",),
        quotas={"interaction": (12, 20), "threat": (4, 12)},
        search_queries=("players can't", "skip", "sacrifice a", "tax"),
    ),
    "combo": ArchetypeProfile(
        creature_is_threat=False,
        max_creatures=16,
        protect_roles=("draw",),
        quotas={"threat": (4, 12), "interaction": (8, 14), "draw": (10, 16)},
        search_queries=("search your library", "untap", "you win the game"),
    ),
    "spellslinger": ArchetypeProfile(
        creature_is_threat=False,
        max_creatures=12,
        protect_roles=("draw", "interaction"),
        quotas={"threat": (4, 10), "interaction": (10, 18), "draw": (10, 16)},
        search_queries=("instant or sorcery", "copy target spell", "prowess"),
    ),
    "voltron": ArchetypeProfile(
        creature_is_threat=False,
        max_creatures=10,
        protect_roles=("threat",),
        quotas={"threat": (6, 14), "interaction": (8, 14)},
        search_queries=("equip", "aura attach", "hexproof"),
    ),
    "reanimator": ArchetypeProfile(
        creature_is_threat=True,
        max_creatures=22,
        quotas={"threat": (10, 20), "reanimate": (6, 14)},
        search_queries=("return target creature from graveyard", "enters from graveyard"),
    ),
    "generic": ArchetypeProfile(
        enforce_tribe=False,
        retrieve_tribe=False,
        creature_is_threat=True,
        max_creatures=28,
        quotas={"threat": (8, 18), "interaction": (8, 14)},
        search_queries=("destroy target creature", "draw a card"),
    ),
}


def infer_archetype(query: str) -> str:
    """Plan from the request. Commander creature types do not imply tribal."""
    q = (query or "").lower()
    if any(p in q for p in ("mill", "self-mill", "self mill", "deck out")):
        return "mill"
    if any(p in q for p in ("stax", "prison", "tax the table", "hard lock")):
        return "stax"
    if any(
        p in q
        for p in (
            "control",
            "permission",
            "counterspell",
            "counterspells",
            "counters, bounce",
            "with counters",
        )
    ) or (
        "counter" in q
        and any(
            p in q
            for p in (
                "bounce",
                "removal",
                "board wipe",
                "permission",
                "spell",
                "spells",
            )
        )
    ):
        return "control"
    if any(p in q for p in ("combo", "infinite", "win the game on the spot")):
        return "combo"
    if any(p in q for p in ("reanimator", "reanimate", "from the graveyard")):
        return "reanimator"
    if any(p in q for p in ("voltron", "commander damage", "equip the commander")):
        return "voltron"
    if any(p in q for p in ("spellslinger", "instants and sorceries", "prowess")):
        return "spellslinger"
    if "tribal" in q or "typal" in q:
        return "tribal"
    tribe_hit = any(f" {w} " in f" {q} " or q.startswith(w) or q.endswith(w) for w in _TRIBE_WORDS)
    if tribe_hit and any(p in q for p in ("token", "tokens", "lord", "lords", "kindred")):
        return "tribal"
    if tribe_hit:
        return "tribal"
    if any(p in q for p in ("token", "tokens", "go wide", "go-wide")):
        return "tokens"
    if any(p in q for p in ("aggro", "beatdown", "fast damage", "combat damage")):
        return "aggro"
    if "midrange" in q:
        return "midrange"
    return "generic"


def profile_for(archetype: str | None) -> ArchetypeProfile:
    key = archetype if archetype in _PROFILES else "generic"
    return _PROFILES[key]


def quotas_for(archetype: str | None) -> dict[str, tuple[int, int]]:
    merged = dict(ROLE_QUOTAS)
    extra = profile_for(archetype).quotas or {}
    merged.update(extra)
    return merged


def search_queries_for(archetype: str | None) -> tuple[str, ...]:
    profile = profile_for(archetype)
    seen = []
    for q in _BASE_SEARCH + profile.search_queries:
        if q not in seen:
            seen.append(q)
    return tuple(seen)


def is_mill_card(oracle_text: str = "", keywords=None) -> bool:
    ot = (oracle_text or "").lower()
    if "mill" in ot:
        return True
    kws = keywords or []
    if isinstance(kws, str):
        kws = [kws]
    if any(str(k).lower() == "mill" for k in kws):
        return True
    if "graveyard" in ot and "library" in ot and "top" in ot:
        return True
    return False


def is_alt_win(oracle_text: str = "") -> bool:
    ot = (oracle_text or "").lower()
    return "you win the game" in ot or "wins the game" in ot


def is_extra_turn(oracle_text: str = "") -> bool:
    ot = (oracle_text or "").lower()
    return "extra turn" in ot or "additional turn" in ot


def is_payoff_threat(
    type_line: str = "",
    oracle_text: str = "",
    keywords=None,
    archetype: str | None = "generic",
) -> bool:
    """Win pieces that are not 'any creature of the commander's types'."""
    from roles import is_reanimate_card

    tl = (type_line or "").lower()
    if is_alt_win(oracle_text) or is_extra_turn(oracle_text):
        return True
    if "planeswalker" in tl:
        return True
    if archetype == "mill" and is_mill_card(oracle_text, keywords):
        return True
    if archetype == "voltron" and (
        "equip" in (oracle_text or "").lower() or "aura" in tl or "equipment" in tl
    ):
        return True
    if archetype == "reanimator" and is_reanimate_card(oracle_text):
        return True
    return False


def planned_roles(
    type_line: str = "",
    oracle_text: str = "",
    keywords=None,
    archetype: str | None = "generic",
) -> set[str]:
    """Roles for fill/diagnosis: creatures are threats only when the plan says so."""
    from roles import classify_roles

    roles = classify_roles(type_line, oracle_text, keywords)
    profile = profile_for(archetype)
    payoff = is_payoff_threat(type_line, oracle_text, keywords, archetype)
    if is_mill_card(oracle_text, keywords):
        roles.add("mill")
    if payoff:
        roles.add("threat")
    creature = any("creature" in face.lower() for face in (type_line or "").split("//"))
    if not profile.creature_is_threat and creature and not payoff:
        roles.discard("threat")
    # classify_roles() falls back to "other" when nothing else fires, but that
    # check runs before the discard above -- a creature whose only role was
    # "threat" (stripped here because this archetype doesn't treat creatures
    # as threats) must not come back as an empty set.
    if not roles:
        roles.add("other")
    return roles
