"""Deterministic, explainable card capabilities derived from catalog fields."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

from roles import classify_roles


@dataclass(frozen=True)
class CardFacts:
    name: str
    type_line: str
    oracle_text: str
    roles: tuple[str, ...]
    is_land: bool = False
    is_creature: bool = False
    is_counter: bool = False
    is_removal: bool = False
    is_draw: bool = False
    is_ramp: bool = False
    is_protection: bool = False
    is_extra_combat: bool = False
    is_evasion: bool = False
    is_tutor: bool = False
    is_token_engine: bool = False
    is_graveyard: bool = False
    is_sacrifice: bool = False
    attribute_evidence: dict[str, dict[str, object]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.attribute_evidence:
            return
        evidence = {
            item.name: {"origin": "oracle_rule", "confidence": 1.0}
            for item in fields(self)
            if item.name.startswith("is_")
        }
        object.__setattr__(self, "attribute_evidence", evidence)

    def to_dict(self) -> dict:
        return asdict(self)


def classify_card(
    name: str,
    type_line: str = "",
    oracle_text: str = "",
    keywords=None,
) -> CardFacts:
    """Classify capabilities from Oracle text without an LLM call.

    These are intentionally capabilities, not claims that a card is good in a
    particular deck. Selection remains the solver's responsibility.
    """

    tl = (type_line or "").lower()
    ot = (oracle_text or "").lower()
    roles = tuple(sorted(classify_roles(type_line, oracle_text, keywords)))
    return CardFacts(
        name=name,
        type_line=type_line or "",
        oracle_text=oracle_text or "",
        roles=roles,
        is_land="land" in tl,
        is_creature="creature" in tl,
        is_counter="counter target" in ot,
        is_removal=any(
            phrase in ot
            for phrase in (
                "destroy target",
                "exile target",
                "return target",
                "destroy all",
                "exile all",
            )
        ),
        is_draw=any(
            phrase in ot
            for phrase in (
                "draw a card",
                "draw two cards",
                "draw an additional card",
                "look at the top",
                "put that card into your hand",
            )
        ),
        is_ramp=any(
            phrase in ot
            for phrase in (
                "add {",
                "search your library for a basic land",
                "search your library for a land",
                "create a treasure",
                "treasure token",
            )
        ),
        is_protection=any(
            phrase in ot
            for phrase in (
                "hexproof",
                "indestructible",
                "protection from",
                "shroud",
                "phase out",
                "can't be countered",
                "prevent all damage",
            )
        ),
        is_extra_combat=any(
            phrase in ot
            for phrase in (
                "additional combat phase",
                "additional combat",
                "after this main phase, there is an additional combat phase",
                "after this phase, there is an additional combat",
                "untap all creatures",
                "untap all attacking",
                "another combat phase",
            )
        ),
        is_evasion=any(
            phrase in ot
            for phrase in (
                "can't be blocked",
                "unblockable",
                "menace",
                "flying",
                "trample",
                "double strike",
            )
        ),
        is_tutor=any(
            phrase in ot
            for phrase in (
                "search your library",
                "search your graveyard",
                "search your hand",
            )
        ),
        is_token_engine=any(
            phrase in ot
            for phrase in (
                "create a ",
                "create one ",
                "create two ",
                "token",
            )
        ),
        is_graveyard=any(
            phrase in ot
            for phrase in (
                "graveyard",
                "from your graveyard",
                "return target card",
                "mill ",
            )
        ),
        is_sacrifice=any(
            phrase in ot
            for phrase in (
                "sacrifice",
                "sacrificed",
                "dies",
                "creature dies",
            )
        ),
    )


def requirement_families(query: str) -> list[str]:
    """Extract stable symbolic requirement families from natural language."""

    q = " ".join((query or "").lower().split())
    families: list[tuple[str, tuple[str, ...]]] = [
        ("counter", ("counterspell", "counterspells", "counter target", "permission", "contramagica", "contramágica")),
        ("removal", (
            "removal", "destroy", "exile", "bounce", "board wipe", "limpeza de mesa",
            "devolver para a mao", "devolver para a mão", "devolucao", "devolução",
        )),
        ("draw", ("draw", "card advantage", "card draw", "comprar carta", "compra de cartas")),
        ("ramp", ("ramp", "mana rock", "acceleration", "add mana", "aceleracao de mana", "aceleração de mana")),
        ("protection", ("protect", "protection", "hexproof", "indestructible", "shroud", "protecao", "proteção")),
        ("extra_combat", (
            "extra combat", "additional combat", "multiple combat", "combat turns",
            "extra combats", "combate extra", "combates extras", "combate adicional",
            "combates adicionais", "fase de combate extra",
        )),
        ("evasion", ("evasion", "trample", "double strike", "can't be blocked")),
        ("tutor", ("tutor", "search your library")),
        ("token_engine", ("token", "go wide", "fichas")),
        ("graveyard", ("graveyard", "reanimate", "recursion", "mill", "reanimar", "cemiterio", "cemitério")),
        ("sacrifice", (
            "sacrifice", "aristocrats", "dies", "sac outlet", "sacrifice outlet",
            "sacrificio", "sacrifício", "aristocratas",
        )),
        ("haste", ("haste", "grant haste", "gains haste", "gain haste", "celeridade")),
        ("convoke", ("convoke", "delve", "improvise", "convocar", "improvisar")),
        ("cost_reduction", (
            "cost reduction", "affinity", "this spell costs", "spells you cast cost",
            "reducao de custo", "redução de custo", "afinidade",
        )),
        ("delirium", ("delirium", "delirio", "delírio")),
        ("threshold", ("threshold", "limiar")),
        ("hellbent", ("hellbent", "obstinado")),
        ("metalcraft", ("metalcraft", "metalurgia")),
        # Bounce is also a removal trigger; this family only fires on bounce wording.
        ("bounce", (
            "bounce", "devolver para a mao", "devolver para a mão", "devolucao", "devolução",
        )),
    ]
    return [name for name, triggers in families if any(t in q for t in triggers)]


# Oracle fragments that retrieve a capability. Add a phrase when a new
# rules template appears; do not add card names.
FAMILY_SEARCH_QUERIES: dict[str, tuple[str, ...]] = {
    "extra_combat": (
        "additional combat phase",
        "untap all attacking creatures",
    ),
    "protection": (
        "target creature gains hexproof",
        "target creature gains indestructible",
        "protection from",
    ),
    "draw": (
        "draw a card",
        "draw two cards",
    ),
    "ramp": (
        "search your library for a basic land",
        "add one mana",
    ),
    "counter": (
        "counter target spell",
    ),
    "removal": (
        "destroy target creature",
        "exile target creature",
    ),
    "tutor": (
        "search your library for a",
    ),
    "token_engine": (
        "create a token",
        "creature token",
    ),
    "graveyard": (
        "return target card from your graveyard",
    ),
    "sacrifice": (
        "sacrifice a creature",
        "whenever a creature you control dies",
    ),
    "evasion": (
        "can't be blocked",
        "gains flying",
    ),
    "haste": (
        "gains haste",
        "gain haste",
    ),
    "convoke": (
        "convoke",
        "delve",
    ),
    "cost_reduction": (
        "this spell costs",
        "spells you cast cost",
    ),
    "delirium": (
        "delirium",
    ),
    "threshold": (
        "threshold",
    ),
    "hellbent": (
        "hellbent",
    ),
    "metalcraft": (
        "metalcraft",
    ),
    "bounce": (
        "return target",
        "return it to its owner's hand",
    ),
}
