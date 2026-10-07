"""Preços latentes π e força de carta (plano economia-de-operadores, Fase 4 / seção 3.1).

Cada carta é uma troca: o custo de mana compra um vetor de efeitos. Se o design é
aproximadamente balanceado, existem preços π com

    mana_value(k) ≈ b_era(k) + b_0 + π · (E_k − C_k)

π_mana = 1 é o numerário (o alvo é o próprio mana value). `E_k` e `C_k` saem dos
operadores compilados do Forge (Fase 2), nunca de popularidade:

  efeito    feature "contexto|api|alvo" com a magnitude do passo (3 de dano, 2 cartas)
  contexto  once (mágica, ETB), dies, turn (todo turno), combat, event:<modo> (gatilho
            recorrente), act (ativada repetível, descontada pelo custo de ativação),
            static:<modo>, repl:<evento>, kw:<palavra-chave>
  custo     custos adicionais, drawbacks (Defender, upkeep), efeitos que pioram você

A força é o resíduo `s_k = π·(E_k − C_k) + b − mana_value(k)`: carta acima da curva
tem s_k > 0 (entrega mais do que o custo paga). `commander_features` ajusta os
efeitos às regras do formato (efeitos "cada oponente" × 3, vida vale metade com
40 de vida) antes de pontuar; o ajuste de π é sempre no design (1 contra 1).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.optimize import minimize

from operators.compile import CardOps, Operator, Step, parse_mana_cost

# Magnitude parameter read first, per API family.
_MAGNITUDE_ORDER = ("NumDmg", "NumCards", "LifeAmount", "CounterNum", "Amount", "ChangeNum", "DigNum",
                    "ScryNum", "SurveilNum", "Num", "TokenAmount")
_TOKEN_PT = re.compile(r"^[wubrgc]+_(\d+)_(\d+)")
_CHARM_STEP = re.compile(r"Step\(api='([^']+)', magnitude=\(([^)]*)\)\)?, target='([^']*)'")
_NUM = re.compile(r"-?\d+")

# Keywords that are drawbacks: priced on the cost side.
DRAWBACK_KEYWORDS = {"Defender", "Cumulative upkeep", "Echo", "Vanishing", "Fading",
                     "You may choose not to untap CARDNAME during your untap step."}
# Keywords that change what the card costs: mana value is not its price.
SELF_COST_KEYWORDS = {"Affinity", "Convoke", "Delve", "Improvise", "Emerge", "Evoke", "Dash", "Blitz",
                      "Foretell", "Plot", "Warp", "Bestow", "Mutate", "Overload", "Miracle", "Madness",
                      "Ninjutsu", "Prowl", "Surge", "Spectacle", "Suspend", "Morph", "Megamorph", "Disguise",
                      "Offering", "Undaunted", "Assist", "Casualty", "Freerunning", "Sneak"}
# Effects that hurt their controller when aimed at "You".
_SELF_HARM = {"LoseLife", "Discard", "Sacrifice", "DealDamage", "SacrificeAll"}
_IGNORED_APIS = {"Cleanup", "StoreSVar", "ChooseType", "ChooseColor", "ChoosePlayer", "ChooseCard",
                 "ChooseSource", "ChooseNumber", "Reveal", "Shuffle"}
# Fitted (design compensates them) but left out of strength: more coloured pips buy more
# effect in WotC's curve, yet WW is not a stronger card than 1W; and the colour pie gives
# green more stats per mana, yet a green 3/3 for 2 is a stronger card, not a "fair" one.
NUISANCE = {"cost|colored_pips", "color|W", "color|U", "color|B", "color|R", "color|G", "color|multi"}
# Normative floors (decisão em aberto 3): the design curve barely charges mana for speed
# (Quick Study and Divination both cost 3), yet an instant does everything the sorcery
# does and more. The game value of speed is measured in Fase 8; until then, a floor.
FLOORS = {"speed|instant": 0.15, "kw|Flash": 0.15}
# Strict positivity: every effect and every cost has a price of at least EPSILON per unit,
# so a card plus any extra benefit is strictly stronger (a 1/1 with Trample beats a 1/1).
# Applied to the coarsest key of each back-off chain and to standalone features.
EPSILON = 0.05
NO_EPSILON = {"body|big", "body|variable"}  # shape terms, not effects
# Combat keywords whose value the damage clock measures; priced per point of power.
COMBAT_KEYWORDS = {"Trample": "trample", "Haste": "haste", "Double Strike": "double_strike",
                   "Flying": "evasive", "Menace": "evasive", "Shadow": "evasive", "Horsemanship": "evasive",
                   "Fear": "evasive", "Intimidate": "evasive", "Skulk": "evasive", "Landwalk": "evasive"}

# Effects saturate: 9999 damage or −9999/−9999 is "it dies", 1000 life is "you win".
MAGNITUDE_CAP = 10.0
COMMANDER_OPPONENTS = 3
LIFE_RATIO = 20 / 40  # a point of life in a 40-life format is worth half of one in 20


# ---------------------------------------------------------------------------
# Feature extraction: E_k − C_k
# ---------------------------------------------------------------------------

@dataclass
class CardVector:
    """Signed features of one card. sign +1: benefit (π ≥ 0); −1: cost (π ≥ 0, enters
    negated); 0: sign not known a priori (fitted freely)."""

    name: str
    mana_value: int
    features: dict[str, float] = field(default_factory=dict)
    signs: dict[str, int] = field(default_factory=dict)
    types: tuple[str, ...] = ()
    excluded: str = ""  # why mana value is not this card's price ("" if it is)
    groups: list[tuple[tuple[str, ...], float]] = field(default_factory=list)  # (back-off chain, value)

    def add(self, key: str, value: float, sign: int = 1, coarse: tuple[str, ...] = ()) -> None:
        """One effect. `coarse` are back-off keys (same value and sign): the fine key
        prices what is specific, the coarse ones share strength across contexts, and a
        card whose fine key is too rare to fit is still priced by them."""
        if value == 0:
            return
        chain = tuple(dict.fromkeys((key, *coarse)))
        for k in chain:
            self.features[k] = self.features.get(k, 0.0) + value
            self.signs.setdefault(k, sign)
        self.groups.append((chain, value))


def _target_class(step: Step) -> str:
    shape = dict(step.shape)
    defined = shape.get("Defined", "")
    if step.target:
        base = step.target.split(",")[0].split(".")[0]
        return "tgt_" + (base or "any") + ("+" if "," in step.target else "")
    if defined.startswith(("Player.Opponent", "Opponent")):
        return "each_opp"
    if defined in ("You", ""):
        return "you"
    if defined in ("Player", "Player.All"):
        return "each_player"
    for key in ("ValidCards", "ValidTgts", "ChangeType", "ValidPlayer"):
        if shape.get(key):
            base = shape[key].split(",")[0].split(".")[0]
            qual = "_yours" if "YouCtrl" in shape[key] else ("_theirs" if "OppCtrl" in shape[key] else "")
            return "all_" + base + qual
    return "other"


def _magnitude(api: str, magnitude: dict[str, str], shape: dict[str, str]) -> tuple[float, bool]:
    """Main magnitude of a step and whether it is variable (X, counts)."""
    if api in ("Pump", "PumpAll"):
        att, dfn = magnitude.get("NumAtt", "0"), magnitude.get("NumDef", "0")
        values = [_NUM.fullmatch(v.lstrip("+")) for v in (att, dfn)]
        if all(values):
            return float(int(att.lstrip("+")) + int(dfn.lstrip("+"))), False
        return 1.0, True
    for key in _MAGNITUDE_ORDER:
        if key in magnitude:
            raw = magnitude[key]
            if _NUM.fullmatch(raw):
                return float(int(raw)), False
            return 1.0, True
    return 1.0, False


def _scale_for_cost(cost_mv: int, other_costs: tuple[str, ...]) -> float:
    """Repeatable value per point of effect, discounted by what each use costs."""
    extra = sum(1 for c in other_costs if c != "T" and not c.startswith(("AddCounter<", "SubCounter<")))
    return 1.0 / (1.0 + cost_mv + 0.5 * extra)


_TOKEN_KINDS = ("treasure", "clue", "food", "blood", "map", "powerstone", "gold", "junk", "shard", "incubator")


def _token_kind(script: str) -> str:
    first = script.split(",")[0].strip().lower()
    if _TOKEN_PT.match(first):
        return "creature"
    return next((k for k in _TOKEN_KINDS if k in first), "other")


def _context_class(ctx: str) -> str:
    return "once" if ctx in ("once", "dies", "act_once") else "rep"


# A broader target class contains this narrower one ("any target" can hit a creature).
_NARROWER = {"tgt_Any": "tgt_Creature", "tgt_Permanent": "tgt_Creature", "tgt_Card": "tgt_Creature"}


def _chain(ctx: str, api: str, tgt: str, variable: bool, cond: bool) -> tuple[str, ...]:
    """Back-off chain of an effect, finest first. It contains every weaker variant of the
    effect (the conditional version of an unconditional one, the single-type version of
    "creature or planeswalker"), so a stronger effect is priced as the weaker one plus
    non-negative increments: dominance in the effect implies dominance in price."""
    x = "|X" if variable else ""
    targets = (tgt, tgt.rstrip("+")) if tgt.endswith("+") else (tgt,)
    if targets[-1] in _NARROWER:
        targets = (*targets, _NARROWER[targets[-1]])
    conds = ("|?",) if cond else ("", "|?")
    variants = [f"{ctx}|{api}|{t}{x}{c}" for t in targets for c in conds]
    cls = _context_class(ctx)
    return tuple(dict.fromkeys([*variants, f"{cls}|{api}|{targets[-1]}", f"{cls}|{api}"]))


def _step_features(vec: CardVector, ctx: str, steps, scale: float, conditional: bool) -> None:
    for step in steps:
        if step.api in _IGNORED_APIS:
            continue
        magnitude, shape = dict(step.magnitude), dict(step.shape)
        if step.api == "Charm":
            _charm(vec, ctx, shape.get("Choices", ""), scale, conditional)
            continue
        value, variable = _magnitude(step.api, magnitude, shape)
        api = step.api
        if api in ("Pump", "PumpAll") and value < 0:
            api, value = api + "-", -value  # −X/−X is removal, not a pump
        value = min(value, MAGNITUDE_CAP)
        tgt = _target_class(step)
        sign = -1 if (api in _SELF_HARM and tgt == "you") else 1
        cond = conditional or any(k not in ("Optional", "OptionalDecider") for k, _ in step.conditions)
        local = scale
        if shape.get("Cost"):  # "you may pay {1}: draw" inside the effect (Mind's Eye)
            local *= _scale_for_cost(parse_mana_cost(shape["Cost"]).mana_value, ())
        if api == "Token":
            tgt = _token_kind(shape.get("TokenScript", ""))  # creature, treasure, clue, food, ...
        if api == "Token" and shape.get("TokenOwner", "You") not in ("You", ""):
            api, sign = "Token-gift", -1  # tokens for someone else (Hunted Horror)
        elif api in ("Draw", "GainLife") and (tgt == "each_opp" or (
                ctx.startswith("event_opp") and shape.get("Defined", "").startswith("Triggered"))):
            api, sign = api + "-gift", -1  # the opponent draws / gains (Forced Fruition)
        chain = _chain(ctx, api, tgt, variable, cond)
        vec.add(chain[0], value * local, sign, chain[1:])
        if api in ("Token", "Token-gift"):
            for script in shape.get("TokenScript", "").split(","):
                m = _TOKEN_PT.match(script.strip())
                if m:
                    vec.add(f"{ctx}|{api}|pt" + ("|?" if cond else ""),
                            value * (int(m.group(1)) + int(m.group(2))) * local, sign, (f"{_context_class(ctx)}|{api}|pt",))
        if api == "Mana":
            produced = shape.get("Produced", "")
            if produced.startswith("Any") or produced == "Combo Any":
                vec.add(f"{ctx}|Mana|any_color", value * local, 1)


def _charm(vec: CardVector, ctx: str, choices: str, scale: float, conditional: bool) -> None:
    """Modal spell: every mode at 1/n (plus the option value of choosing)."""
    modes = [m for m in choices.split("]},{[") if m]
    if not modes:
        return
    vec.add(f"{ctx}|Charm|modes", len(modes) * scale, 1)
    for mode in modes:
        for api, mags, target in _CHARM_STEP.findall(mode):
            nums = [int(n) for n in re.findall(r"'(-?\d+)'", mags)]
            value = float(abs(sum(nums))) if nums else 1.0
            tgt = "tgt_" + target.split(",")[0].split(".")[0] if target else "other"
            chain = _chain(ctx, api, tgt, False, conditional)
            vec.add(chain[0], value * scale / len(modes), 1, chain[1:])


def _trigger_context(op: Operator) -> str:
    shape = dict(op.shape)
    valid = shape.get("ValidCard", "")
    if op.kind == "ChangesZone" and valid == "Card.Self":
        if shape.get("Destination") == "Battlefield":
            return "once"
        if shape.get("Origin") == "Battlefield":
            return "dies"
    if op.kind == "Phase":
        player = shape.get("ValidPlayer", "")
        return "turn" if player in ("", "You") else "turn_any"
    if op.kind in ("Attacks", "AttackersDeclared", "DamageDone", "DamageDoneOnce", "Blocks",
                   "AttackerBlocked", "AttackerUnblocked") and "Self" in valid + shape.get("ValidSource", ""):
        return "combat"
    opp = any("Opp" in v or v == "Opponent" for v in shape.values())
    return f"event{'_opp' if opp else ''}:{op.kind}"


def card_vector(card: CardOps) -> CardVector:
    """E_k − C_k of a compiled card, with the reasons it cannot anchor the fit."""
    vec = CardVector(card.name, card.mana_cost.mana_value, types=tuple(sorted(card.types)))
    if "Land" in card.types:
        vec.excluded = "terreno"
    elif card.faces:
        vec.excluded = "várias faces"
    elif "X" in card.mana_cost.other or not card.mana_cost.raw or card.mana_cost.raw == "no cost":
        vec.excluded = "custo X ou sem custo"
    elif card.unresolved:
        vec.excluded = "atom não resolvido"
    for t in card.types:
        if t in ("Instant", "Sorcery"):
            vec.add("type|Spell", 1.0, 0)
            if t == "Instant":
                vec.add("speed|instant", 1.0, 1)  # instant speed is never worse than sorcery speed
        else:
            vec.add(f"type|{t}", 1.0, 0)
    if "Legendary" in card.supertypes:
        vec.add("type|Legendary", 1.0, 0)
    pips = sum(n for _, n in card.mana_cost.pips)
    vec.add("cost|colored_pips", float(pips), 0)  # colour requirements, fitted freely
    colors = [c for c, _ in card.mana_cost.pips]
    for c in colors:  # the colour pie prices bodies and keywords differently (green: more stats)
        vec.add(f"color|{c}", 1.0, 0)
    if len(colors) > 1:
        vec.add("color|multi", 1.0, 0)
    if "Creature" in card.types:
        power, toughness = _NUM.fullmatch(card.power or ""), _NUM.fullmatch(card.toughness or "")
        if power and toughness:
            p, t = min(15.0, max(0.0, float(card.power))), min(15.0, max(0.0, float(card.toughness)))
            vec.add("body|power", p, 1)
            vec.add("body|toughness", t, 1)
            vec.add("body|big", max(0.0, p + t - 8), 1)  # big bodies cost more per point
            for op in card.operators:  # combat keywords are worth more on a bigger body
                if op.source == "keyword" and op.kind in COMBAT_KEYWORDS and p > 0:
                    vec.add(f"kw|{op.kind}×power", p, 1)
        else:
            vec.add("body|variable", 1.0, 1)
    for op in card.operators:
        conditional = any(k not in ("Optional", "OptionalDecider") for k, _ in op.conditions)
        if op.source == "keyword":
            if op.kind in SELF_COST_KEYWORDS and not vec.excluded:
                vec.excluded = f"custo alternativo ({op.kind})"
            if op.kind.startswith("Protection"):
                vec.add("kw|Protection", 1.0, 1)
            else:
                vec.add(f"kw|{op.kind}", 1.0, -1 if op.kind in DRAWBACK_KEYWORDS else 1)
            continue
        if op.source == "spell":
            for c in op.cost_other:
                vec.add("cost|" + c.split("<")[0], 1.0, -1)
            _step_features(vec, "once", op.steps, 1.0, conditional)
        elif op.source == "activated":
            self_sac = any(c.startswith("Sac<") and "CARDNAME" in c for c in op.cost_other)
            mv = op.cost.mana_value if op.cost else 0
            if self_sac:
                _step_features(vec, "act_once", op.steps, 1.0 / (1.0 + mv), conditional)
            else:
                loyalty = [c for c in op.cost_other if c.startswith(("AddCounter<", "SubCounter<")) and "LOYALTY" in c]
                if loyalty:  # a minus ability spends loyalty: an ultimate is not a free repeatable effect
                    spent = sum(int(m) for c in loyalty if c.startswith("SubCounter<")
                                for m in re.findall(r"<(\d+)/", c))
                    _step_features(vec, "loyalty", op.steps, 1.0 / (1.0 + spent), conditional)
                else:
                    _step_features(vec, "act", op.steps, _scale_for_cost(mv, op.cost_other), conditional)
        elif op.source == "trigger":
            _step_features(vec, _trigger_context(op), op.steps, 1.0, conditional)
        elif op.source == "static":
            _static_features(vec, op, conditional)
        elif op.source == "replacement":
            etb_self = op.kind == "Moved" and all(dict(st.shape).get("ETB") == "True" for st in op.steps) and op.steps
            if etb_self:  # enters tapped / with counters: sign learned (slumber vs +1/+1)
                for st in op.steps:
                    value, _ = _magnitude(st.api, dict(st.magnitude), dict(st.shape))
                    counter = dict(st.shape).get("CounterType", "")
                    vec.add(f"etb|{st.api}" + (f"|{counter}" if counter in ("P1P1", "M1M1") else ""), value, 0)
            else:
                vec.add(f"repl|{op.kind}" + ("|?" if conditional else ""), 1.0, 0)
    return vec


def _static_features(vec: CardVector, op: Operator, conditional: bool) -> None:
    shape, magnitude = dict(op.shape), dict(op.magnitude)
    if op.kind == "AlternativeCost" and not vec.excluded:
        vec.excluded = "custo alternativo"
    if op.kind == "ReduceCost" and dict(op.shape).get("ValidCard", "") == "Card.Self" and not vec.excluded:
        vec.excluded = "custo reduzido pela própria carta"
    affected = shape.get("Affected", shape.get("ValidCard", ""))
    if affected in ("Card.Self", "Creature.Self"):
        scope = "self"
    elif "EquippedBy" in affected or "EnchantedBy" in affected or "AttachedBy" in affected:
        scope = "attached"
    elif "YouCtrl" in affected:
        scope = "yours"
    elif "OppCtrl" in affected:
        scope = "theirs"
    else:
        scope = "all"
    suffix = "|?" if conditional else ""
    if op.kind == "Continuous":
        power = magnitude.get("AddPower", "0")
        tough = magnitude.get("AddToughness", "0")
        if _NUM.fullmatch(power.lstrip("+")) and _NUM.fullmatch(tough.lstrip("+")):
            total = int(power.lstrip("+")) + int(tough.lstrip("+"))
            if total:
                vec.add(f"static|pt|{scope}{suffix}", float(abs(total)), 1 if (total > 0) != (scope == "theirs") else -1)
        elif "AddPower" in magnitude:
            vec.add(f"static|pt|{scope}|X{suffix}", 1.0, 0)  # −X/−X (Death's Shadow) or +X
        if shape.get("AddKeyword"):
            vec.add(f"static|kw|{scope}{suffix}", float(len(shape["AddKeyword"].split(" & "))), 0)
        if not (magnitude or shape.get("AddKeyword")):
            vec.add(f"static|Continuous|{scope}{suffix}", 1.0, 0)
        return
    if op.kind in ("CantAttack", "CantBlock", "CantBlockBy", "MustAttack") and scope == "self":
        vec.add(f"static|{op.kind}|self", 1.0, -1 if op.kind != "CantBlockBy" else 1)
        return
    vec.add(f"static|{op.kind}|{scope}{suffix}", 1.0, 0)


def commander_features(vec: CardVector) -> dict[str, float]:
    """The same card under Commander rules: "each opponent" hits three players, a
    recurring trigger on opponents' actions fires for three of them (Mind's Eye), and
    a point of life is worth half (40 life). The factor applies to the whole back-off
    chain of the effect; fitted π are reused unchanged."""
    out = dict(vec.features)
    for chain, value in vec.groups:
        fine = chain[0]
        factor = 1.0
        # Only one-sided effects triple: "each player" is symmetric (Wheel of Fortune
        # refills three opponents too), so its 1-vs-1 price stays.
        if "|each_opp" in fine or fine.startswith("event_opp:"):
            factor *= COMMANDER_OPPONENTS
        if "|GainLife|" in fine or "|LoseLife|" in fine:
            factor *= LIFE_RATIO
        if factor != 1.0:
            for k in chain:
                out[k] += value * (factor - 1.0)
    return out


# ---------------------------------------------------------------------------
# Era: first printing, from Forge's edition files
# ---------------------------------------------------------------------------

ERA_BINS = ((1996, "1993–96"), (2002, "1997–02"), (2008, "2003–08"), (2014, "2009–14"),
            (2019, "2015–19"), (2022, "2020–22"), (9999, "2023+"))
_EDITION_CARD = re.compile(r"^\S+\s+\S+\s+(.+?)(?:\s+@.*)?$")


def first_printings(editions_dir: Path) -> dict[str, tuple[str, str]]:
    """Lowercased card name → (date, edition type) of its first printing."""
    first: dict[str, tuple[str, str]] = {}
    for path in editions_dir.glob("*.txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        date = re.search(r"^Date=(\S+)", text, re.M)
        etype = re.search(r"^Type=(\S+)", text, re.M)
        if not date or (etype and etype.group(1) == "Funny"):
            continue
        in_cards = False
        for line in text.splitlines():
            if line.startswith("["):
                in_cards = line.strip().lower() == "[cards]"
                continue
            if not in_cards or not line.strip():
                continue
            m = _EDITION_CARD.match(line.strip())
            if not m:
                continue
            name = m.group(1).strip().lower()
            if name not in first or date.group(1) < first[name][0]:
                first[name] = (date.group(1), etype.group(1) if etype else "")
    return first


def era_group(date: str, edition_type: str) -> str:
    year = int(date[:4]) if date[:4].isdigit() else 9999
    era = next(label for limit, label in ERA_BINS if year <= limit)
    product = "commander" if edition_type in ("Commander", "Multiplayer") else "set"
    return f"era|{era}|{product}"


# ---------------------------------------------------------------------------
# Fit: Huber regression with sign constraints, π_mana = 1
# ---------------------------------------------------------------------------

@dataclass
class Prices:
    features: list[str]
    weights: np.ndarray           # π, one per feature (sign already applied in the design)
    signs: dict[str, int]
    intercepts: dict[str, float]  # b_0 and b_era|product
    support: dict[str, int]
    # Mean residual (prediction − mana value) by mana value: the regression-to-the-mean bias.
    calib_mv: np.ndarray = field(default_factory=lambda: np.zeros(0))
    calib_value: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def design_row(self, features: dict[str, float]) -> float:
        """π·(E−C) for one card (features without a price contribute nothing)."""
        index = self._index
        return sum(value * (self.signs.get(key, 1) or 1) * self.weights[index[key]]
                   for key, value in features.items() if key in index)

    def complete(self, vec: CardVector) -> bool:
        """Every effect of the card has at least one priced key in its back-off chain."""
        index = self._index
        chained = {k for chain, _ in vec.groups for k in chain}
        return all(any(k in index for k in chain) for chain, _ in vec.groups) and             all(k in index for k in vec.features if k not in chained)

    @property
    def _index(self) -> dict[str, int]:
        if not hasattr(self, "_idx"):
            self._idx = {f: i for i, f in enumerate(self.features)}
        return self._idx

    def predict(self, features: dict[str, float], group: str) -> float:
        return self.intercepts.get("b0", 0.0) + self.intercepts.get(group, 0.0) + self.design_row(features)

    def strength(self, features: dict[str, float], mana_value: int) -> float:
        """s_k: mana value the effects are worth minus the mana value paid, centred on
        the cards of the same cost. Absolute: the era effect is left out (it is a
        nuisance for fitting π; a 2023 card is not weaker for being newer). Predicting
        cost from effects regresses to the mean (cheap cards look strong, expensive
        ones weak); subtracting the mean residual of the cost bucket removes that bias
        without inflating the scale."""
        return self._effect_value(features) - mana_value - self.bias(mana_value)

    def _effect_value(self, features: dict[str, float]) -> float:
        """b_0 + π·(E − C) without the nuisance terms (era, colour requirements)."""
        return self.intercepts.get("b0", 0.0) + self.design_row(
            {k: v for k, v in features.items() if k not in NUISANCE})

    def bias(self, mana_value: int) -> float:
        if not len(self.calib_mv):
            return 0.0
        return float(np.interp(mana_value, self.calib_mv, self.calib_value))

    def calibrate(self, vectors: list[CardVector], min_count: int = 30) -> None:
        """Mean residual by mana value, over buckets pooled until they have enough cards.
        Pool-adjacent-violators keeps the mean prediction from falling as cost rises, or a
        cheaper copy of a card could come out weaker than the dearer one."""
        by_mv: dict[int, list[float]] = {}
        for v in vectors:
            by_mv.setdefault(v.mana_value, []).append(self._effect_value(v.features))
        blocks, pending = [], [0.0, 0.0, 0]
        for m in sorted(by_mv):
            vals = by_mv[m]
            pending = [pending[0] + m * len(vals), pending[1] + sum(vals), pending[2] + len(vals)]
            if pending[2] >= min_count:
                blocks.append(pending)
                pending = [0.0, 0.0, 0]
        if pending[2] and blocks:
            blocks[-1] = [blocks[-1][i] + pending[i] for i in range(3)]
        merged: list[list[float]] = []
        for b in blocks:
            merged.append(list(b))
            while len(merged) > 1 and merged[-1][1] / merged[-1][2] < merged[-2][1] / merged[-2][2]:
                last = merged.pop()
                merged[-1] = [merged[-1][i] + last[i] for i in range(3)]
        self.calib_mv = np.array([b[0] / b[2] for b in merged])
        self.calib_value = np.array([b[1] / b[2] - b[0] / b[2] for b in merged])


def _huber(r: np.ndarray, delta: float) -> tuple[np.ndarray, np.ndarray]:
    a = np.abs(r)
    loss = np.where(a <= delta, 0.5 * r * r, delta * (a - 0.5 * delta))
    grad = np.where(a <= delta, r, delta * np.sign(r))
    return loss, grad


def fit_prices(vectors: list[CardVector], groups: list[str], min_support: int = 10,
               l2: float = 1.0, delta: float = 1.0, sample_weight: np.ndarray | None = None,
               floors: dict[str, float] | None = None) -> Prices:
    """π* = argmin Σ Huber(π·(E−C) + b − mana_value) + λ‖π‖², π ≥ 0 on signed features,
    π ≥ floor on the normative floors."""
    floors = dict(FLOORS if floors is None else floors)
    chained_inner = {k for v in vectors for chain, _ in v.groups for k in chain[:-1]}
    support = Counter(k for v in vectors for k in v.features)
    features = sorted(k for k, n in support.items() if n >= min_support)
    index = {f: i for i, f in enumerate(features)}
    signs: dict[str, int] = {}
    for v in vectors:
        for k, s in v.signs.items():
            signs.setdefault(k, s)
    group_names = sorted(set(groups))
    gindex = {g: i for i, g in enumerate(group_names)}
    rows, cols, vals = [], [], []
    for i, v in enumerate(vectors):
        for k, value in v.features.items():
            j = index.get(k)
            if j is not None:
                s = signs.get(k, 1)
                rows.append(i)
                cols.append(j)
                vals.append(value * (s if s else 1))
    n, m, g = len(vectors), len(features), len(group_names)
    X = sparse.csr_matrix((vals, (rows, cols)), shape=(n, m))
    G = sparse.csr_matrix((np.ones(n), (np.arange(n), [gindex[x] for x in groups])), shape=(n, g))
    y = np.array([v.mana_value for v in vectors], dtype=float)
    w8 = np.ones(n) if sample_weight is None else sample_weight

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        pi, b0, b = theta[:m], theta[m], theta[m + 1:]
        r = X @ pi + b0 + G @ b - y
        loss, grad_r = _huber(r, delta)
        grad_r = grad_r * w8
        f = float(np.sum(loss * w8)) + l2 * float(pi @ pi) + 0.1 * l2 * float(b @ b)
        grad = np.concatenate([X.T @ grad_r + 2 * l2 * pi, [grad_r.sum()], G.T @ grad_r + 0.2 * l2 * b])
        return f, grad

    for f in features:
        if signs.get(f, 1) and f not in chained_inner and f not in NO_EPSILON:
            floors[f] = max(floors.get(f, 0.0), EPSILON)
    bounds = [(floors.get(f, 0.0), None) if signs.get(f, 1) else (None, None) for f in features]         + [(None, None)] * (1 + g)
    theta0 = np.array([max(0.0, floors.get(f, 0.0)) for f in features] + [0.0] * (1 + g))
    theta0[m] = float(np.median(y))
    res = minimize(objective, theta0, jac=True, method="L-BFGS-B", bounds=bounds,
                   options={"maxiter": 3000})
    intercepts = {"b0": float(res.x[m]), **{name: float(res.x[m + 1 + i]) for name, i in gindex.items()}}
    prices = Prices(features, res.x[:m], {f: signs.get(f, 1) for f in features}, intercepts,
                    {f: support[f] for f in features})
    prices.calibrate(vectors)
    return prices


def mean_by_type_baseline(train: list[CardVector], test: list[CardVector]) -> list[float]:
    """Baseline the fit must beat: mean mana value of the card's type line."""
    sums: dict[tuple, list[float]] = {}
    for v in train:
        sums.setdefault(v.types, []).append(v.mana_value)
    overall = float(np.mean([v.mana_value for v in train]))
    return [float(np.mean(sums[v.types])) if v.types in sums else overall for v in test]


def huber_mae(errors) -> float:
    return float(np.mean(np.abs(errors)))




# ---------------------------------------------------------------------------
# Rule-derived floors for combat keywords (terminal anchor: the damage clock)
# ---------------------------------------------------------------------------

def combat_keyword_damage(samples: int = 300, horizon: int = 10) -> dict[str, float]:
    """Damage a combat keyword adds per point of power, in units of one point of power.

    A p/p creature for p mana (p = 1..6) is added 12 times to a deck of 36 lands and
    plain bodies; the keyword's extra damage by turn `horizon` is regressed on p through
    the origin and divided by the mean damage of +1 power. Monotone by construction of
    the clock (a keyword never removes damage)."""
    from operators.clock import ClockParams, basic_land, simulate, vanilla, with_

    params = ClockParams(horizon=horizon, samples=samples)
    land = basic_land()
    filler = [vanilla(f"Bear{i % 3}", 2, 2) for i in range(25)] + [vanilla("Ogre", 3, 3)] * 14         + [vanilla("Giant", 4, 4)] * 12

    def damage(card) -> float:
        return simulate([land] * 36 + filler + [card] * 12, params=params).damage_by[horizon]

    per_power, gains = [], {k: [] for k in set(COMBAT_KEYWORDS.values())}
    for p in range(1, 7):
        base = vanilla("Test", p, p)
        d0 = damage(base)
        per_power.append(damage(with_(base, power=p + 1)) - d0)
        for flag in gains:
            gains[flag].append((p, damage(with_(base, **{flag: True})) - d0))
    unit = float(np.mean(per_power))
    return {flag: max(0.0, sum(p * g for p, g in pts) / sum(p * p for p, _ in pts)) / unit
            for flag, pts in gains.items()}


def combat_floors(power_price: float, damage: dict[str, float]) -> dict[str, float]:
    """Floor on `kw|<keyword>×power`: the clock's damage per power point, priced at π_power."""
    return {f"kw|{kw}×power": damage[flag] * power_price for kw, flag in COMBAT_KEYWORDS.items()}


def fit_with_rule_floors(vectors: list[CardVector], groups: list[str], damage: dict[str, float],
                         **kw) -> Prices:
    """Two passes: fit, read π_power, set the clock floors in mana, fit again."""
    base = dict(kw.pop("floors", None) or FLOORS)
    first = fit_prices(vectors, groups, floors=base, **kw)
    power = float(first.weights[first.features.index("body|power")])
    return fit_prices(vectors, groups, floors={**base, **combat_floors(power, damage)}, **kw)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

PRICES_PATH = Path(__file__).resolve().parents[2] / "data" / "ontology" / "prices_v1.json"


def save_prices(prices: Prices, path: Path = PRICES_PATH) -> None:
    data = {
        "version": 1,
        "model": "mana_value ≈ b0 + b_era|produto + π·(E − C); força centrada por mana value",
        "features": {f: {"pi": round(float(w), 6), "sign": prices.signs.get(f, 1), "support": prices.support[f]}
                     for f, w in zip(prices.features, prices.weights)},
        "intercepts": {k: round(v, 6) for k, v in prices.intercepts.items()},
        "calibration": {"mana_value": [round(float(x), 4) for x in prices.calib_mv],
                        "bias": [round(float(x), 6) for x in prices.calib_value]},
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_prices(path: Path = PRICES_PATH) -> Prices | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    features = list(data["features"])
    return Prices(
        features,
        np.array([data["features"][f]["pi"] for f in features]),
        {f: data["features"][f]["sign"] for f in features},
        data["intercepts"],
        {f: data["features"][f]["support"] for f in features},
        np.array(data["calibration"]["mana_value"]),
        np.array(data["calibration"]["bias"]),
    )


def card_strength(card: CardOps, prices: Prices, commander: bool = True) -> tuple[float, bool]:
    """(s_k, every effect priced) of a compiled card; Commander rules by default."""
    vec = card_vector(card)
    features = commander_features(vec) if commander else vec.features
    return prices.strength(features, vec.mana_value), prices.complete(vec)
