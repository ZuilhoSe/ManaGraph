"""Ordem parcial de dominância entre cartas (Fase 2).

A ≥ B quando as duas têm a mesma *assinatura* (mesmos tipos e a mesma estrutura
de operadores) e A é pelo menos tão boa em tudo que varia:

  custo      mana value ≤ e, cor a cor, pips ≤ (custo incolor é mais flexível)
  velocidade instantâneo ≥ feitiço
  corpo      poder/resistência ≥
  palavras   palavras-chave positivas de A ⊇ as de B
  operadores por operador, na ordem: custo de ativação ≤, condições ⊆,
             alvo ⊇, magnitude ≥ (com a polaridade do efeito)

A domina B estritamente quando A ≥ B e não B ≥ A; quando valem os dois, são
equivalentes (reimpressões funcionais). Nada aqui usa popularidade ou preço.

Commander é singleton: dominância não tira B do jogo, ordena. B só deve entrar
se A já estiver no deck ou não estiver disponível (x_B ≤ x_A no otimizador).
Os subtipos ficam fora da assinatura (a relação vem marcada quando diferem),
porque um Goblin pode valer mais que um Bear num deck tribal.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from operators.compile import CardOps, ManaCost, Operator, Step

POSITIVE_KEYWORDS = {
    "Flying", "Trample", "Vigilance", "Haste", "Flash", "Reach", "Menace",
    "First Strike", "Double Strike", "Deathtouch", "Lifelink", "Hexproof",
    "Indestructible", "Shroud", "Intimidate", "Fear", "Shadow", "Horsemanship",
    "Skulk", "Prowess", "Exalted", "Landwalk", "Changeling",
}

BENEFICIAL = {
    "Draw", "GainLife", "Token", "PutCounter", "PutCounterAll", "Pump", "PumpAll", "Mana",
    "Scry", "Surveil", "Dig", "Investigate", "Untap", "Proliferate", "Amass", "Explore",
    "Connive", "Learn", "Incubate", "Animate", "Protection", "ProtectionAll", "Regenerate",
    "PreventDamage", "Fog", "BecomeMonarch", "TakeInitiative", "DigUntil", "Discover",
}
HARMFUL = {
    "DealDamage", "Destroy", "LoseLife", "Discard", "Mill", "Sacrifice", "Counter", "Tap",
    "Poison", "Debuff", "RemoveCounter", "Fight",
}
# ChangeZone moves that are card advantage for the controller (tutor, ramp, recursion).
GOOD_MOVES = {"Library>Hand", "Library>Battlefield", "Graveyard>Hand", "Graveyard>Battlefield",
              "Exile>Hand", "Exile>Battlefield"}
REMOVAL_MOVES = {"Battlefield>Exile", "Battlefield>Hand", "Battlefield>Library",
                 "Stack>Exile", "Stack>Hand", "Stack>Library"}
SELF = {"", "You"}
SELF_OBJECT = {"Self", "Card.Self", "You"}
# Subtypes that carry rules text: a land's basic types ARE its mana abilities.
RULES_SUBTYPES = {
    "Plains", "Island", "Swamp", "Mountain", "Forest", "Aura", "Equipment", "Vehicle",
    "Saga", "Class", "Room", "Case", "Fortification", "Background",
}
OPPONENT_MARKERS = ("Opponent", "Opp")


def _num(value: str) -> int | None:
    return int(value) if re.fullmatch(r"-?\d+", value or "") else None


def _polarity(step: Step) -> int:
    """+1: more is better for the controller; -1: less is better; 0: must be equal."""
    shape = dict(step.shape)
    defined = shape.get("Defined", "")
    api = step.api
    targets_opponent = any(m in (step.target or defined) for m in OPPONENT_MARKERS)
    chosen_target = bool(step.target) and "You" not in step.target
    if api.startswith("ChangeZone"):
        move = api.split(".", 1)[-1]
        if move in GOOD_MOVES and defined in SELF and not step.target:
            return 1
        if move in REMOVAL_MOVES and chosen_target:
            return 1
        return 0
    if api in ("Pump", "PumpAll"):
        # The controller picks the creatures, buff or debuff; the sign of the
        # numbers is handled in _magnitude_ge.
        return 1
    if api in BENEFICIAL:
        if targets_opponent:
            return -1
        return 1 if defined in SELF or "You" in defined or chosen_target else 0
    if api in HARMFUL:
        if defined in SELF_OBJECT or (defined in SELF and not step.target and api in ("LoseLife", "Discard", "Sacrifice", "Mill", "Tap")):
            return -1
        if targets_opponent or chosen_target:
            return 1
        return 0
    return 0


def _magnitude_ge(a: Iterable[tuple[str, str]], b: Iterable[tuple[str, str]], polarity: int,
                  pump: bool = False) -> bool:
    da, db = dict(a), dict(b)
    if da.keys() != db.keys():
        return False
    for key, va in da.items():
        vb = db[key]
        if va == vb:
            continue
        na, nb = _num(va), _num(vb)
        if na is None or nb is None:
            return False
        pol = 1 if key in ("UnlessCost", "TargetMax", "CharmNum") else polarity
        if pump and key in ("NumAtt", "NumDef"):
            # A pump on a chosen creature is a buff or a debuff: +3 beats +2,
            # -3 beats -2, and +2 vs -2 is not an order at all.
            if na >= 0 and nb >= 0:
                pol = 1
            elif na <= 0 and nb <= 0:
                pol = -1
            else:
                return False
        if pol == 0 or (pol > 0 and na < nb) or (pol < 0 and na > nb):
            return False
    return True


def target_ge(a: str, b: str) -> bool:
    """A's target scope ⊇ B's."""
    if a == b:
        return True
    if not a or not b:
        return False
    if a == "Any":
        return True
    a_set, b_set = set(a.split(",")), set(b.split(","))
    if b_set <= a_set:
        return True
    return all(any(x == y or x.startswith(y + ".") for y in a_set) for x in b_set)


def mana_le(a: ManaCost | None, b: ManaCost | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if a.other != b.other:
        return False
    if a.mana_value > b.mana_value:
        return False
    return all(a.pip(c) <= b.pip(c) for c in "WUBRG")


def _conditions_le(a: Iterable, b: Iterable, polarity: int = 1) -> bool:
    """Fewer conditions on something good is better; on a drawback ("enters
    tapped unless ...") more conditions are better; unknown polarity: equal."""
    sa, sb = set(a), set(b)
    if polarity > 0:
        return sa <= sb
    if polarity < 0:
        return sa >= sb
    return sa == sb


def _operator_polarity(op: Operator) -> int:
    signs = {_polarity(step) for step in op.steps}
    if signs == {1} or signs == {1, 0} and op.source in ("spell", "activated"):
        return 1
    if signs == {-1}:
        return -1
    if not op.steps and op.source in ("spell", "activated", "trigger"):
        return 1
    return 0


def _step_ge(a: Step, b: Step) -> bool:
    if a.api != b.api or a.shape != b.shape:
        return False
    pol = _polarity(a)
    if not _conditions_le(a.conditions, b.conditions, pol if pol else 1 if not a.conditions and not b.conditions else 0):
        return False
    if not target_ge(a.target, b.target):
        return False
    return _magnitude_ge(a.magnitude, b.magnitude, pol, pump=a.api in ("Pump", "PumpAll"))


_SPEED = {"sorcery": 0, "instant": 1, "": 0}


def _operator_ge(a: Operator, b: Operator) -> bool:
    if (a.source, a.kind, a.cost_other, a.frequency, a.shape) != (
        b.source, b.kind, b.cost_other, b.frequency, b.shape
    ):
        return False
    if a.source != "spell" and _SPEED[a.speed] < _SPEED[b.speed]:
        return False
    if a.cost is not None or b.cost is not None:
        if not mana_le(a.cost, b.cost):
            return False
    if not _conditions_le(a.conditions, b.conditions, _operator_polarity(a)):
        return False
    if len(a.steps) != len(b.steps) or not all(_step_ge(x, y) for x, y in zip(a.steps, b.steps)):
        return False
    if a.magnitude or b.magnitude:
        affected = dict(a.shape).get("Affected", "")
        pol = 1 if "YouCtrl" in affected else -1 if "OppCtrl" in affected else 0
        if not _magnitude_ge(a.magnitude, b.magnitude, pol):
            return False
    return True


def _keywords(card: CardOps) -> tuple[frozenset[str], tuple]:
    positive, rest = set(), []
    for op in card.operators:
        if op.source != "keyword":
            continue
        if op.kind in POSITIVE_KEYWORDS and not op.shape:
            positive.add(op.kind)
        else:
            rest.append((op.kind, op.shape))
    return frozenset(positive), tuple(sorted(rest))


def _type_class(card: CardOps) -> tuple:
    types = set(card.types)
    if types & {"Instant", "Sorcery"} and not types - {"Instant", "Sorcery", "Kindred", "Tribal"}:
        types = (types - {"Instant", "Sorcery"}) | {"spell"}
    return tuple(sorted(types)), tuple(sorted(card.supertypes - {"Snow"}))


def _op_signature(op: Operator) -> tuple:
    return (
        op.source, op.kind, op.cost_other, op.frequency, op.shape,
        tuple(sorted(k for k, _ in op.magnitude)),
        op.cost is not None,
        tuple((s.api, s.shape, tuple(sorted(k for k, _ in s.magnitude)), bool(s.target)) for s in op.steps),
    )


def signature(card: CardOps) -> tuple:
    """Cards are comparable only inside the same signature."""
    _positive, other_keywords = _keywords(card)
    ops = tuple(_op_signature(op) for op in card.operators if op.source != "keyword")
    body = (bool(card.power), bool(card.toughness))
    rules_subtypes = tuple(sorted(card.subtypes & RULES_SUBTYPES))
    faces = tuple(signature(face) for face in getattr(card, "faces", []))
    return (_type_class(card), rules_subtypes, body, other_keywords, ops, card.mana_cost.other, faces)


def _body_ge(a: CardOps, b: CardOps) -> bool:
    for va, vb in ((a.power, b.power), (a.toughness, b.toughness)):
        if va == vb:
            continue
        na, nb = _num(va), _num(vb)
        if na is None or nb is None or na < nb:
            return False
    return True


def _spell_speed(card: CardOps) -> int:
    if "Instant" in card.types or any(op.kind == "Flash" for op in card.operators if op.source == "keyword"):
        return 1
    return 0


def ge(a: CardOps, b: CardOps) -> bool:
    """A is at least as good as B in every dimension that varies (same signature)."""
    if signature(a) != signature(b):
        return False
    return _face_ge(a, b) and all(_face_ge(x, y) for x, y in zip(a.faces, b.faces))


def _face_ge(a: CardOps, b: CardOps) -> bool:
    if not mana_le(a.mana_cost, b.mana_cost):
        return False
    if _spell_speed(a) < _spell_speed(b):
        return False
    if not _body_ge(a, b):
        return False
    pa, _ = _keywords(a)
    pb, _ = _keywords(b)
    if not pa >= pb:
        return False
    ops_a = [op for op in a.operators if op.source != "keyword"]
    ops_b = [op for op in b.operators if op.source != "keyword"]
    return all(_operator_ge(x, y) for x, y in zip(ops_a, ops_b))


@dataclass(frozen=True)
class Relation:
    dominator: str
    dominated: str
    subtypes_differ: bool


def dominance(cards: Iterable[CardOps]) -> tuple[list[Relation], list[list[str]]]:
    """Strict dominance relations and equivalence classes (functional reprints)."""
    buckets: dict[tuple, list[CardOps]] = defaultdict(list)
    for card in cards:
        if card.unresolved:
            continue  # a parameter we could not read: never claim dominance over it
        buckets[signature(card)].append(card)
    relations: list[Relation] = []
    equivalents: list[list[str]] = []
    for group in buckets.values():
        if len(group) < 2:
            continue
        seen_equiv: set[str] = set()
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                ab, ba = ge(a, b), ge(b, a)
                if ab and ba:
                    if a.name not in seen_equiv or b.name not in seen_equiv:
                        equivalents.append(sorted((a.name, b.name)))
                        seen_equiv |= {a.name, b.name}
                elif ab:
                    relations.append(Relation(a.name, b.name, a.subtypes != b.subtypes))
                elif ba:
                    relations.append(Relation(b.name, a.name, a.subtypes != b.subtypes))
    return relations, equivalents


DOMINANCE_PATH = Path(__file__).resolve().parents[2] / "data" / "ontology" / "dominance_v1.json"


@lru_cache(maxsize=4)
def load_dominators(path: str = str(DOMINANCE_PATH)) -> dict[str, tuple[tuple[str, bool], ...]]:
    """Lowercased dominated name → ((dominator, subtypes_differ), ...). Empty if not built."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    out: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for rel in data.get("relations") or []:
        out[rel["dominated"].lower()].append((rel["dominator"], bool(rel.get("subtypes_differ"))))
    return {k: tuple(v) for k, v in out.items()}
