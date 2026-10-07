"""Relógio até a vitória (plano economia-de-operadores, Fase 2.5 / seções 2.6–2.7).

Mede quão rápido um deck chega a um estado terminal das regras, a partir só dos
operadores compilados. Todas as rotas da seção 2.7 contam:

  vida        combate, dano direto e drenos tiram dos mesmos 120 pontos (3 × 40)
  comandante  dano de combate do comandante (3 × 21)
  veneno      infect/toxic (3 × 10)
  biblioteca  mill dos oponentes (3 × 85)
  alternativa WinsGame / "oponentes perdem" com a condição do script avaliada no
              estado simulado (Gates de nomes diferentes ≥ 10, devoção ≥
              biblioteca, comprar com a biblioteca vazia, segunda conjuração,
              vida ≥ 40, domínio + cores = 10, ...)

O estado é uma pequena máquina de eventos (criatura morre, mágica lançada,
landfall, ganho de vida, manutenção): gatilhos de aristocrats, spellslinger,
pings, lifegain → dreno e mill por gatilho disparam quando o evento acontece.
A esperança sobre a ordem de compra é estimada por amostragem (goldfish
abstrato: sem Forge; os oponentes só bloqueiam, a uma taxa-base, até a Fase 8).

Efeitos condicionais só contam quando a condição é avaliável e verdadeira no
estado; uma expressão que o avaliador não entende não promete nada.
"""

from __future__ import annotations

import random
import re
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from statistics import mean, pvariance

from operators.compile import CardOps

LIFE_TOTAL = 120        # 3 opponents × 40
POISON_TOTAL = 30       # 3 × 10
COMMANDER_TOTAL = 63    # 3 × 21
OPP_LIBRARY_TOTAL = 3 * 85
OPPONENTS = 3
OUR_LIFE = 40
MAX_TRIGGER_DEPTH = 12  # guards trigger chains (life gained → trigger that gains life → ...)
EVASIVE = {"Flying", "Menace", "Shadow", "Horsemanship", "Fear", "Intimidate", "Skulk",
           "Landwalk", "Islandwalk", "Swampwalk", "Forestwalk", "Mountainwalk", "Plainswalk"}
BASIC_TYPES = ("Plains", "Island", "Swamp", "Mountain", "Forest")
_COLOR_WORDS = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}
_TOKEN_PT = re.compile(r"^([wubrgc]+)_(\d+)_(\d+)_?(.*)$")
_COUNT_CREATURES = "Count$Valid Creature.YouCtrl"
_COUNT_YOURS = re.compile(r"Count\$Valid \w+\.YouCtrl")
_BENIGN_CONDITIONS = {"Optional", "OptionalDecider"}
_CMP = re.compile(r"^(GE|GT|LE|LT|EQ|NE)(.+)$")


# ---------------------------------------------------------------------------
# Conditions: Forge Count$ expressions evaluated on the simulated state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Condition:
    """lhs <op> rhs, both Forge expressions (SVar bodies or numbers).

    kind "svar": ConditionCheckSVar/ConditionSVarCompare; kind "present":
    IsPresent/PresentZone/PresentCompare (lhs is the filter, zone in `zone`).
    """

    lhs: str
    op: str
    rhs: str
    kind: str = "svar"
    zone: str = "Battlefield"
    svars: tuple[tuple[str, str], ...] = ()


def _strip(value: str) -> str:
    v = value.strip()
    return v[1:-1] if v.startswith("{") and v.endswith("}") else v


def _parse_condition(conds: dict[str, str], svars: dict[str, str]) -> Condition | None | bool:
    """Condition from condition params: True if there is none, None if there is
    one the clock cannot read."""
    keys = {k for k in conds if k not in _BENIGN_CONDITIONS}
    if not keys:
        return True
    items = tuple(sorted(svars.items()))
    if keys <= {"ConditionCheckSVar", "ConditionSVarCompare"} and "ConditionCheckSVar" in conds:
        m = _CMP.match(conds.get("ConditionSVarCompare", "GE1"))
        if not m:
            return None
        lhs = _strip(conds["ConditionCheckSVar"])
        return Condition(svars.get(lhs, lhs), m.group(1), m.group(2), svars=items)
    if keys <= {"IsPresent", "PresentCompare", "PresentZone", "PresentPlayer"} and "IsPresent" in conds:
        m = _CMP.match(conds.get("PresentCompare", "GE1"))
        if not m:
            return None
        return Condition(conds["IsPresent"], m.group(1), m.group(2), kind="present",
                         zone=conds.get("PresentZone", "Battlefield"), svars=items)
    return None


def _compare(a: float, op: str, b: float) -> bool:
    return {"GE": a >= b, "GT": a > b, "LE": a <= b, "LT": a < b, "EQ": a == b, "NE": a != b}[op]


# ---------------------------------------------------------------------------
# Card model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Effect:
    """One thing that happens when something resolves, triggers or is activated."""

    kind: str  # drain | gain | mill_opp | mill_self | mill_choice | draw | tokens | fetch | opp_loses | win
    amount: int = 0
    scale: str = ""  # "trigger": amount × the event's amount (life gained); "second_cast";
    #                  "half": half of the milled player's library (Traumatize)
    token: tuple[int, int, int, str] | None = None  # count, power, toughness, subtype
    fetch_type: str = ""
    condition: Condition | None = None  # for win / opp_loses
    expr: str = ""  # amount × this Forge expression, evaluated when it happens (devotion, ...)
    svars: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Trigger:
    event: str  # dies_yours | dies_any | cast_spell | cast_any | landfall | lifegain | upkeep |
    #             etb_creature | etb_any | opp_lifeloss | token_created | sac_token
    effects: tuple[Effect, ...]


@dataclass(frozen=True)
class Ability:
    """A non-mana activated ability the clock can use."""

    mana: int
    tap: bool
    sac: bool          # sacrifices another creature as a cost (sac outlet)
    return_self: bool  # Maze's End
    effects: tuple[Effect, ...]
    sac_type: str = ""  # what a sac outlet eats: "Creature", "Goblin", ...


@dataclass(frozen=True)
class WinRule:
    when: str  # resolve | upkeep | draw_empty | second_cast
    condition: Condition | None = None
    opponents_lose: bool = False


@dataclass(frozen=True)
class ClockCard:
    """The magnitudes and rules the clock reads off one card's operators."""

    name: str
    cmc: int = 0
    land: bool = False
    creature: bool = False
    power: int = 0
    toughness: int = 0
    haste: bool = False
    evasive: bool = False
    trample: bool = False
    double_strike: bool = False
    infect: bool = False
    lifelink: bool = False
    mana: int = 0              # mana per turn from a permanent (rock, dork)
    ramp_lands: int = 0        # lands put onto the battlefield when it resolves
    draw: int = 0              # cards drawn when it resolves
    tokens: tuple[tuple[int, int, int, str], ...] = ()  # (count, power, toughness, subtype) on resolve
    tokens_per_turn: tuple[tuple[int, int, int], ...] = ()  # each upkeep
    tap_tokens: tuple[int, int, int] | None = None  # {T}: create tokens
    tap_tokens_scale: bool = False  # count = creatures you control (Krenko)
    anthem: int = 0            # +power to your creatures while it stays
    overrun: int = 0           # +power to your creatures this turn (one-shot)
    overrun_per_creature: bool = False  # Craterhoof: +X where X = your creatures
    overrun_trample: bool = False
    attach_power: int = 0      # equipment/aura: +power to the creature it is attached to
    attach_per_land: bool = False  # +X where X = your lands (Blackblade Reforged)
    equip_cost: int = 0        # mana to attach (0 for auras)
    mill: int = 0              # opponents' cards milled on resolve (total)
    alt_win: bool = False      # unconditional "you win" on resolve
    # Routes beyond combat
    burn: int = 0              # life removed from the opponents' pool on resolve (total)
    life_gain: int = 0         # life you gain on resolve
    self_mill: int = 0         # your cards milled on resolve
    mill_choice: int = 0       # "target player mills N": you or an opponent, by plan
    on_resolve: tuple[Effect, ...] = ()  # effects with amounts read off the state (Gray Merchant)
    triggers: tuple[Trigger, ...] = ()
    abilities: tuple[Ability, ...] = ()
    wins: tuple[WinRule, ...] = ()
    types: frozenset[str] = frozenset()
    subtypes: frozenset[str] = frozenset()
    pips: tuple[tuple[str, int], ...] = ()


def _int(v: str | None, default: int = 0) -> int:
    return int(v) if v and re.fullmatch(r"-?\d+", v) else default


def _conditional(conditions) -> bool:
    return any(k not in _BENIGN_CONDITIONS for k, _ in conditions)


def _comparison_gate(op) -> bool:
    """Trigger gated by a comparison kept as magnitude (LifeAmount$ GE50, ...)."""
    return op.source == "trigger" and any(v.startswith("var:") for _, v in op.magnitude)


def _token(script: str, n: int) -> tuple[int, int, int, str] | None:
    m = _TOKEN_PT.match(script.strip())
    if not m:
        return None
    subtype = m.group(4).split("_")[0].capitalize() if m.group(4) else ""
    return n, int(m.group(2)), int(m.group(3)), subtype


def _opponents(step, s: dict[str, str]) -> int:
    """How many opponents an effect hits: 3 (each), 1 (a target), 0 (not them)."""
    defined = s.get("Defined", "")
    if defined.startswith(("Player.Opponent", "Opponent")) or defined in ("Player", "Player.All", "Player.nonYou"):
        return OPPONENTS
    target = step.target or ""
    first = target.split(",")[0].split(".")[0]
    if first in ("Any", "Player", "Opponent") or ",Player" in target or ",Opponent" in target:
        return 1
    return 0


def _effects(steps, svars: dict[str, str]) -> list[Effect]:
    """Effects of a chain of steps (resolve, trigger or ability)."""
    out: list[Effect] = []
    for step in steps:
        s, m = dict(step.shape), dict(step.magnitude)
        cond = _parse_condition(dict(step.conditions), svars)
        api = step.api
        if api in ("WinsGame", "LosesGame"):
            if cond is None:
                continue
            condition = None if cond is True else cond
            defined = s.get("Defined", "You")
            if api == "WinsGame" and defined in ("You", ""):
                out.append(Effect("win", condition=condition))
            elif api == "LosesGame" and (defined.startswith(("Player.Opponent", "Opponent")) or step.target):
                each = defined.startswith(("Player.Opponent", "Opponent"))
                out.append(Effect("opp_loses", amount=OPPONENTS if each else 1, condition=condition))
            continue
        if cond is not True:
            continue  # conditional non-terminal effects: not promised
        if api == "Branch" and "WinsGame" in s.get("TrueSubAbility", "") \
                and "Stack>Library" in s.get("FalseSubAbility", ""):
            out.append(Effect("win", scale="second_cast"))  # Approach of the Second Sun
        elif api in ("DealDamage", "LoseLife"):
            amount = m.get("NumDmg" if api == "DealDamage" else "LifeAmount", "1")
            n = _opponents(step, s)
            if not n:
                continue
            if amount.startswith(("var:TriggerCount$LifeAmount", "var:TriggerCount$DamageAmount")):
                out.append(Effect("drain", amount=n, scale="trigger"))
            elif _int(amount):
                out.append(Effect("drain", amount=_int(amount) * n))
            elif amount.startswith("var:Count$"):
                out.append(Effect("drain", amount=n, expr=amount[4:], svars=tuple(sorted(svars.items()))))
        elif api == "GainLife" and s.get("Defined", "You") in ("", "You"):
            amount = m.get("LifeAmount", "")
            if amount.startswith("var:TriggerCount$LifeAmount"):
                out.append(Effect("gain", amount=1, scale="trigger"))
            elif _int(amount):
                out.append(Effect("gain", amount=_int(amount)))
        elif api == "Mill":
            raw = m.get("NumCards", "1")
            half = "CardsInLibrary/Half" in raw
            if not (half or _int(raw)):
                continue  # a count the clock cannot read
            n, scale = (1, "half") if half else (_int(raw), "")
            if half and step.target:
                out.append(Effect("mill_choice", amount=1, scale="half"))
                continue
            if half:
                continue
            defined = s.get("Defined", "")
            if defined == "You":
                out.append(Effect("mill_self", amount=n))
            elif defined.startswith(("Player.Opponent", "Opponent")):
                out.append(Effect("mill_opp", amount=n * OPPONENTS))
            elif defined in ("Player", "Player.All"):
                out.append(Effect("mill_opp", amount=n * OPPONENTS))
                out.append(Effect("mill_self", amount=n))
            elif step.target:
                out.append(Effect("mill_choice", amount=n))
        elif api == "Draw" and s.get("Defined", "You") in ("", "You"):
            out.append(Effect("draw", amount=_int(m.get("NumCards"), 1)))
        elif api == "Token" and s.get("TokenOwner", "You") in ("", "You"):
            n = _int(m.get("TokenAmount"), 1)
            for script in s.get("TokenScript", "").split(","):
                tok = _token(script, n)
                if tok:
                    out.append(Effect("tokens", token=tok))
        elif api == "ChangeZone.Library>Battlefield":
            base = s.get("ChangeType", "").split(".")[0]
            if base and base not in ("Land", "Card", "Creature", "Permanent"):
                out.append(Effect("fetch", amount=_int(m.get("ChangeNum"), 1), fetch_type=base))
    return out


_NOT_CREATURES = {"Land", "Artifact", "Enchantment", "Planeswalker", "Card", "CARDNAME",
                  "Treasure", "Food", "Clue", "Blood"}


def _sac_fodder(cost: str) -> str:
    """Creature type a "Sac<N/Type>" cost eats ("" if it does not eat creatures)."""
    m = re.match(r"Sac<\d+/([^>/]+)", cost)
    if not m:
        return ""
    base = m.group(1).split(".")[0].split(";")[0]
    if base in _NOT_CREATURES or not base[:1].isupper():
        return ""
    return "Creature" if base == "Permanent" else base


def _trigger_event(op) -> str | None:
    shape = dict(op.shape)
    mode = op.kind
    if mode == "ChangesZone":
        origin, dest = shape.get("Origin", "Any"), shape.get("Destination", "")
        valid = shape.get("ValidCard", "")
        if origin == "Battlefield" and dest == "Graveyard" and "Creature" in valid:
            return "dies_yours" if ("YouCtrl" in valid or "YouOwn" in valid) else "dies_any"
        if dest == "Battlefield" and valid.startswith("Land") and "YouCtrl" in valid:
            return "landfall"
        if dest == "Battlefield" and valid.startswith("Creature"):
            return "etb_creature" if "YouCtrl" in valid else ("etb_any" if "Opp" not in valid else None)
        return None
    if mode in ("SpellCast", "SpellCastOrCopy"):
        if "Opponent" in shape.get("ValidActivatingPlayer", ""):
            return None
        valid = shape.get("ValidCard", "Card")
        if "Instant" in valid or "Sorcery" in valid:
            return "cast_spell"
        if valid.startswith("Card") or valid == "Spell":
            return "cast_any"
        return None
    if mode == "LifeGained" and shape.get("ValidPlayer", "You") in ("You", ""):
        return "lifegain"
    if mode == "Drawn" and shape.get("ValidCard", "") in ("Card.YouCtrl", "Card.YouOwn"):
        return "drawn"
    if mode == "LifeLost" and shape.get("ValidPlayer", "").startswith("Opponent"):
        return "opp_lifeloss"
    if mode == "TokenCreated" and shape.get("ValidPlayer", "You") in ("You", ""):
        return "token_created"
    if mode == "Sacrificed" and "YouCtrl" in shape.get("ValidCard", "") and "token" in shape.get("ValidCard", ""):
        return "sac_token"
    if mode == "Phase" and shape.get("Phase") == "Upkeep" and shape.get("ValidPlayer", "You") in ("You", ""):
        return "upkeep"
    return None


def clock_features(card: CardOps) -> ClockCard:
    """Read the clock's magnitudes and rules off a compiled card."""
    keywords = {op.kind for op in card.operators if op.source == "keyword"}
    svars = card.svars
    f = dict(
        name=card.name,
        cmc=card.mana_cost.mana_value,
        land="Land" in card.types,
        creature="Creature" in card.types,
        power=max(0, _int(card.power)),
        toughness=max(0, _int(card.toughness)),
        haste="Haste" in keywords,
        evasive=bool(keywords & EVASIVE),
        trample="Trample" in keywords,
        double_strike="Double Strike" in keywords,
        infect=bool(keywords & {"Infect", "Toxic"}),
        lifelink="Lifelink" in keywords,
        types=frozenset(card.types),
        subtypes=frozenset(card.subtypes),
        pips=tuple(card.mana_cost.pips),
    )
    acc = dict(mana=0, ramp_lands=0, draw=0, mill=0, anthem=0, overrun=0, burn=0, life_gain=0,
               self_mill=0, mill_choice=0, attach_power=0, equip_cost=0)
    tokens: list[tuple[int, int, int, str]] = []
    on_resolve: list[Effect] = []
    per_turn: list[tuple[int, int, int]] = []
    flags = dict(overrun_per_creature=False, overrun_trample=False, alt_win=False,
                 attach_per_land=False, tap_tokens_scale=False)
    tap_tokens = None
    triggers: list[Trigger] = []
    abilities: list[Ability] = []
    wins: list[WinRule] = []

    def absorb(effects: list[Effect]) -> None:
        for e in effects:
            if e.kind == "win" and e.scale == "second_cast":
                wins.append(WinRule("second_cast"))
            elif e.kind == "win" and e.condition is None:
                flags["alt_win"] = True
            elif e.kind in ("win", "opp_loses"):
                wins.append(WinRule("resolve", e.condition, opponents_lose=e.kind == "opp_loses"))
            elif e.expr or e.scale == "half":
                on_resolve.append(e)
            elif e.kind == "drain" and not e.scale:
                acc["burn"] += e.amount
            elif e.kind == "gain":
                acc["life_gain"] += e.amount
            elif e.kind == "mill_self":
                acc["self_mill"] += e.amount
            elif e.kind == "mill_opp":
                acc["mill"] += e.amount
            elif e.kind == "mill_choice":
                acc["mill_choice"] += e.amount
            elif e.kind == "draw":
                acc["draw"] += e.amount
            elif e.kind == "tokens":
                tokens.append(e.token)

    for op in card.operators:
        shape = dict(op.shape)
        etb = (op.source == "trigger" and op.kind == "ChangesZone" and shape.get("Destination") == "Battlefield"
               and shape.get("ValidCard") == "Card.Self")
        # Win/lose checks gated at the operator level (Felidar: life ≥ 40; Battle of Wits).
        if op.source == "trigger" and any(st.api in ("WinsGame", "LosesGame") for st in op.steps):
            magnitude = dict(op.magnitude)
            if "LifeAmount" in magnitude and shape.get("LifeTotal") == "You":
                m = _CMP.match(magnitude["LifeAmount"].removeprefix("var:"))
                gate = Condition("Count$YourLifeTotal", m.group(1), m.group(2)) if m else None
            else:
                gate = _parse_condition(dict(op.conditions), svars)
            when = "upkeep" if op.kind == "Phase" else ("resolve" if etb else None)
            if gate is not None and when:
                for e in _effects(op.steps, svars):
                    if e.kind in ("win", "opp_loses"):
                        wins.append(WinRule(when, e.condition if gate is True else gate,
                                            opponents_lose=e.kind == "opp_loses"))
            continue
        if op.source == "replacement" and op.kind == "Draw" and any(st.api == "WinsGame" for st in op.steps):
            cond = _parse_condition(dict(op.conditions), svars)
            if isinstance(cond, Condition) and cond.kind == "present" and cond.zone == "Library":
                wins.append(WinRule("draw_empty", cond))  # Laboratory Maniac, Jace
            continue
        if _conditional(op.conditions) or _comparison_gate(op):
            continue  # value the clock cannot promise (e.g. "if you control six lands")
        if op.source == "activated" and op.kind == "Mana" and "T" in op.cost_other and not f["land"]:
            acc["mana"] += _int(dict(op.steps[0].magnitude).get("Amount"), 1)
            continue
        if op.source == "activated" and op.kind == "Token" and op.cost_other == ("T",) \
                and op.cost is not None and op.cost.mana_value == 0:
            step = op.steps[0]
            s, m = dict(step.shape), dict(step.magnitude)
            pt = _TOKEN_PT.match(s.get("TokenScript", "").split(",")[0].strip())
            if pt and not _conditional(step.conditions):
                amount = m.get("TokenAmount", "1")
                flags["tap_tokens_scale"] = bool(_COUNT_YOURS.search(amount))
                tap_tokens = (_int(amount, 1), int(pt.group(2)), int(pt.group(3)))
            continue
        if op.source == "activated":
            effects = _effects(op.steps, svars)
            costs = op.cost_other
            self_sac = any(c.startswith("Sac<") and "CARDNAME" in c for c in costs)
            sac_type = next((t for t in map(_sac_fodder, costs) if t), "")
            if (effects or sac_type) and not self_sac:  # an outlet counts even if its effect does not
                abilities.append(Ability(
                    mana=op.cost.mana_value if op.cost else 0,
                    tap="T" in costs,
                    sac=bool(sac_type),
                    return_self=any("Return<1/CARDNAME>" in c for c in costs),
                    effects=tuple(effects),
                    sac_type=sac_type,
                ))
            continue
        if op.source == "static" and op.kind == "Continuous":
            affected = shape.get("Affected", "")
            add = dict(op.magnitude).get("AddPower", "")
            if affected.startswith("Creature.YouCtrl"):
                acc["anthem"] += _int(add)
            elif affected in ("Creature.EquippedBy", "Creature.EnchantedBy", "Creature.AttachedBy"):
                if "Count$Valid Land.YouCtrl" in add:
                    flags["attach_per_land"] = True
                else:
                    acc["attach_power"] += _int(add)
            continue
        if op.source == "keyword" and op.kind == "Equip" and shape.get("arg"):
            value = sum(int(t) if t.isdigit() else 1 for t in shape["arg"].split(":")[0].split())
            acc["equip_cost"] = value if not acc["equip_cost"] else min(acc["equip_cost"], value)
            continue
        if op.source == "trigger" and not etb:
            event = _trigger_event(op)
            effects = _effects(op.steps, svars)
            if event == "upkeep":
                per_turn += [e.token[:3] for e in effects if e.kind == "tokens"]
                effects = [e for e in effects if e.kind != "tokens"]
            if event and effects:
                triggers.append(Trigger(event, tuple(effects)))
            continue
        if not (op.source == "spell" or etb):
            continue
        for step in op.steps:
            if _conditional(step.conditions):
                continue
            s, m = dict(step.shape), dict(step.magnitude)
            if step.api == "ChangeZone.Library>Battlefield" and any(
                t in s.get("ChangeType", "") for t in ("Land", *BASIC_TYPES)
            ):
                acc["ramp_lands"] += _int(m.get("ChangeNum"), 1)
            elif step.api == "PumpAll" and s.get("ValidCards", "").startswith("Creature.YouCtrl"):
                att = m.get("NumAtt", "")
                if _COUNT_CREATURES in att:
                    flags["overrun_per_creature"] = True
                else:
                    acc["overrun"] += _int(att)
                flags["overrun_trample"] = flags["overrun_trample"] or "Trample" in s.get("KW", "")
        absorb([e for e in _effects(op.steps, svars) if e.kind != "fetch"])
    return ClockCard(**f, **acc, **flags, tokens=tuple(tokens), tokens_per_turn=tuple(per_turn),
                     on_resolve=tuple(on_resolve),
                     tap_tokens=tap_tokens, triggers=tuple(triggers), abilities=tuple(abilities),
                     wins=tuple(wins))


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

@dataclass
class ClockParams:
    horizon: int = 10                  # T
    samples: int = 300
    seed: int = 7
    p_evasive: float = 0.85            # P_conectar of an evasive attacker the table would block
    # Toughness of the blocker a creature meets: a trampler pushes the excess through,
    # and a blocker dies (chump) when the attacker's power reaches its toughness.
    blocker_toughness: tuple[tuple[int, float], ...] = ((1, 0.25), (2, 0.35), (3, 0.25), (4, 0.15))
    # b_t: the defenders' pool of blockers. Base rate until Fase 8: from turn
    # `blockers_start` the table adds `blocker_rate` blockers per turn; a chump
    # block (attacker power ≥ blocker toughness) spends the blocker.
    blockers_start: int = 2
    blocker_rate: float = 0.75
    blockers_cap: int = 6
    mulligan_lands: tuple[int, int] = (2, 5)
    # Our life also moves (base rate until Fase 8): the table deals `incoming_damage`
    # to us per turn from turn `blockers_start` + 1. It only matters for conditions
    # on our life (Felidar Sovereign: ≥ 40 at upkeep, and we start at 40).
    incoming_damage: float = 3.0


@dataclass
class _Body:
    card: ClockCard
    sick: bool
    commander: bool = False
    bonus: int = 0
    token: bool = False

    @property
    def power(self) -> int:
        return self.card.power


@dataclass
class ClockResult:
    turn: float                 # mean turn of the fastest terminal (censored at T + 1)
    turn_var: float
    censored: float             # fraction of samples with no win by T
    damage_by: dict[int, float]  # expected cumulative life removed by turn (all sources)
    wasted_mana: float          # mean unspent mana per turn
    terminals: dict[str, float]  # share of samples won by each terminal
    life_sources: dict[str, float] = field(default_factory=dict)  # life removed by T: combat vs direct
    routes: dict[str, float] = field(default_factory=dict)  # mean fraction of each terminal reached by T
    turns: list[int] = field(default_factory=list, repr=False)


_EVENT_ALIASES = {("cast_any", "cast_spell"), ("dies_any", "dies_yours"), ("etb_any", "etb_creature")}


def _token_card(power: int, toughness: int, subtype: str = "") -> ClockCard:
    return ClockCard(name=f"token {subtype}".strip(), creature=True, power=power, toughness=toughness,
                     types=frozenset({"Creature"}), subtypes=frozenset({subtype}) if subtype else frozenset())


def _is_permanent(card: ClockCard) -> bool:
    return not ({"Instant", "Sorcery"} & card.types) and bool(
        card.land or card.creature or {"Artifact", "Enchantment", "Planeswalker", "Battle"} & card.types
        or card.mana or card.anthem or card.attach_power or card.attach_per_land or card.triggers
        or card.abilities or card.tokens_per_turn or any(r.when in ("upkeep", "draw_empty") for r in card.wins)
    )


class _Game:
    def __init__(self, library: list[ClockCard], commander: ClockCard | None, p: ClockParams,
                 rates: dict[str, float], rho: float, self_mill_plan: bool):
        self.p, self.rates, self.rho, self.self_mill_plan = p, rates, rho, self_mill_plan
        self.hand = library[:7]
        self.library = library[7:]
        self.commander = commander
        self.commander_cast = False
        self.lands: list[ClockCard] = []
        self.pending_lands = 0
        self.rocks = 0
        self.others: list[ClockCard] = []  # noncreature, nonland permanents
        self.bodies: list[_Body] = []
        self.anthem = 0
        self.opp_life = float(LIFE_TOTAL)
        self.poison = self.cmd_damage = 0.0
        self.milled = 0
        self.combat_damage = self.direct_damage = 0.0
        self.our_life = OUR_LIFE
        self.graveyard = 0
        self.win: tuple[str, int] | None = None
        self.lost = False
        self.unattached: list[ClockCard] = []
        self.per_turn_makers: list[ClockCard] = []
        self.pool = 0.0
        self.casts: dict[str, int] = {}
        self.overrun = 0
        self.overrun_trample = False
        self.drawn = 0
        self.depth = 0
        self.looped = False
        self._deaths = 0.0  # expected chump-blocker deaths not yet emitted
        self.wasted = 0.0
        self.t = 0

    # -- state queries -----------------------------------------------------------
    def permanents(self) -> list[ClockCard]:
        return self.lands + self.others + [b.card for b in self.bodies]

    @staticmethod
    def _matches(card: ClockCard, filt: str) -> bool | None:
        base, _, quals = filt.partition(".")
        for q in quals.split("+"):
            if q and q not in ("YouCtrl", "YouOwn", "Other", "nonToken"):
                return None
        if base in ("Card", "Permanent"):
            return True
        return base in card.types or base in card.subtypes

    def _matching(self, filters: str) -> list[ClockCard] | None:
        out = []
        for card in self.permanents():
            hit = False
            for filt in filters.split(","):
                m = self._matches(card, filt)
                if m is None:
                    return None
                hit = hit or m
            if hit:
                out.append(card)
        return out

    def _eval(self, expr: str, svars: dict[str, str], ctx: dict, depth: int = 0) -> float | None:
        expr = _strip(expr)
        if depth > 6:
            return None
        if re.fullmatch(r"-?\d+", expr):
            return int(expr)
        if expr in svars:
            return self._eval(svars[expr], svars, ctx, depth + 1)
        if expr.startswith("Number$"):
            return _int(expr[7:])
        if expr.startswith("SVar$"):
            m = re.fullmatch(r"SVar\$(\w+)(?:/(Plus|Minus|Times)\.(\w+)|/Twice)?", expr)
            if not m:
                return None
            a = self._eval(m.group(1), svars, ctx, depth + 1)
            if a is None:
                return None
            if expr.endswith("/Twice"):
                return 2 * a
            if not m.group(2):
                return a
            b = self._eval(m.group(3), svars, ctx, depth + 1)
            return None if b is None else {"Plus": a + b, "Minus": a - b, "Times": a * b}[m.group(2)]
        if expr.startswith("Count$Valid "):
            filters, _, suffix = expr[12:].partition("$")
            cards = self._matching(filters)
            if cards is None:
                return None
            if not suffix:
                return len(cards)
            if suffix == "DifferentCardNames":
                return len({c.name for c in cards})
            if suffix == "Colors":
                return len({color for c in cards for color, _ in c.pips})
            return None
        if expr.startswith("Count$ValidLibrary"):
            return len(self.library)
        if expr.startswith("Count$ValidGraveyard"):
            return self.graveyard
        if expr.startswith("Count$Devotion."):
            color = _COLOR_WORDS.get(expr.split(".", 1)[1])
            return None if color is None else sum(dict(c.pips).get(color, 0) for c in self.permanents())
        if expr == "Count$YourLifeTotal":
            return self.our_life
        if expr == "Count$Domain":
            return len({t for c in self.lands for t in c.subtypes if t in BASIC_TYPES})
        if expr in ("Count$InYourHand", "Count$CardsInYourHand"):
            return len(self.hand)
        m = re.fullmatch(r"Count\$Kicked\.(\d+)\.(\d+)", expr)
        if m:
            return int(m.group(2))  # the clock never pays kicker
        if expr.startswith("TriggerCount$"):
            return ctx.get("amount")
        return None

    def holds(self, cond: Condition | None, ctx: dict | None = None) -> bool:
        if cond is None:
            return True
        svars = dict(cond.svars)
        ctx = ctx or {}
        if cond.kind == "present":
            lhs = {"Library": len(self.library), "Hand": len(self.hand), "Graveyard": self.graveyard}.get(cond.zone)
            if lhs is None:
                cards = self._matching(cond.lhs)
                lhs = None if cards is None else len(cards)
        else:
            lhs = self._eval(cond.lhs, svars, ctx)
        rhs = self._eval(cond.rhs, svars, ctx)
        return lhs is not None and rhs is not None and _compare(lhs, cond.op, rhs)

    # -- effects -------------------------------------------------------------------
    def _set_win(self, how: str) -> None:
        if self.win is None and not self.lost:
            self.win = (how, self.t)

    def drain(self, amount: float, combat: bool = False) -> None:
        amount = min(amount, max(0.0, self.opp_life))  # the table cannot lose more than it has
        if amount <= 0:
            return
        self.opp_life -= amount
        if combat:
            self.combat_damage += amount
        else:
            self.direct_damage += amount
        self.emit("opp_lifeloss", amount=amount)

    def gain(self, amount: float) -> None:
        if amount > 0:
            self.our_life += amount
            self.emit("lifegain", amount=amount)

    def draw_cards(self, n: int) -> None:
        for _ in range(n):
            if not self.library:
                if any(r.when == "draw_empty" for c in self.permanents() for r in c.wins):
                    self._set_win("alt_win")
                else:
                    self.lost = True  # decked yourself
                return
            self.hand.append(self.library.pop(0))
            self.drawn += 1
            self.emit("drawn")

    def mill_self(self, n: int) -> None:
        k = min(n, len(self.library))
        del self.library[:k]
        self.graveyard += k

    def apply(self, effects, ctx: dict | None = None) -> None:
        ctx = ctx or {}
        for e in effects:
            if self.win or self.lost:
                return
            amount = e.amount * (ctx.get("amount", 0) if e.scale == "trigger" else 1)
            if e.expr:
                amount *= self._eval(e.expr, dict(e.svars), ctx) or 0
            if e.kind == "drain":
                self.drain(amount)
            elif e.kind == "gain":
                self.gain(amount)
            elif e.kind == "mill_opp":
                self.milled += amount
            elif e.kind == "mill_self":
                self.mill_self(amount)
            elif e.kind == "mill_choice" and e.scale == "half":
                if self.self_mill_plan:
                    self.mill_self(len(self.library) // 2)
                else:
                    self.milled += (OPP_LIBRARY_TOTAL - self.milled) / OPPONENTS / 2
            elif e.kind == "mill_choice":
                if self.self_mill_plan:
                    self.mill_self(amount)
                else:
                    self.milled += amount
            elif e.kind == "draw":
                self.draw_cards(amount)
            elif e.kind == "tokens":
                n, pw, tg, sub = e.token
                for _ in range(n):
                    self.add_body(_token_card(pw, tg, sub), token=True)
            elif e.kind == "fetch":
                for _ in range(e.amount):
                    hit = next((c for c in self.library
                                if e.fetch_type in c.subtypes or e.fetch_type in c.types), None)
                    if hit:
                        self.library.remove(hit)
                        self.enter(hit)
            elif e.kind == "win" and e.scale != "second_cast" and self.holds(e.condition, ctx):
                self._set_win("alt_win")
            elif e.kind == "opp_loses" and self.holds(e.condition, ctx):
                if e.amount >= OPPONENTS:
                    self._set_win("opponents_lose")
                else:
                    self.drain(LIFE_TOTAL / OPPONENTS)

    def emit(self, event: str, **ctx) -> None:
        if self.depth >= MAX_TRIGGER_DEPTH:
            self.looped = True
            return
        top = self.depth == 0
        if top:
            self.looped, life_before = False, self.opp_life
        self.depth += 1
        try:
            for card in self.permanents():
                for trig in card.triggers:
                    if trig.event == event or (trig.event, event) in _EVENT_ALIASES:
                        self.apply(trig.effects, ctx)
        finally:
            self.depth -= 1
        if top and self.looped and self.opp_life < life_before and not (self.win or self.lost):
            # A chain that only the depth guard stopped and that drains each lap is an
            # unbounded loop (Sanguine Bond + Exquisite Blood): it drains the table.
            self.direct_damage += self.opp_life
            self.opp_life = 0.0

    def add_body(self, card: ClockCard, token: bool = False, commander: bool = False) -> None:
        self.bodies.append(_Body(card, sick=not card.haste, commander=commander, token=token))
        self.emit("etb_creature")
        if token:
            self.emit("token_created")

    def enter(self, card: ClockCard, commander: bool = False) -> None:
        """A permanent enters the battlefield (lands too, via fetch)."""
        if card.land:
            self.lands.append(card)
            self.emit("landfall")
        elif card.creature:
            self.add_body(card, commander=commander)
        else:
            self.others.append(card)
            self.rocks += card.mana
        self.anthem += card.anthem
        if card.attach_power or card.attach_per_land:
            self.unattached.append(card)
        if card.tokens_per_turn:
            self.per_turn_makers.append(card)

    def kill(self, body: _Body, sacrificed: bool = False) -> None:
        self.bodies.remove(body)
        if not body.token:
            self.graveyard += 1
        self.emit("dies_yours")
        if sacrificed and body.token:
            self.emit("sac_token")

    # -- casting -------------------------------------------------------------------
    def resolve(self, card: ClockCard) -> None:
        board = len(self.bodies)
        if card is self.commander:
            self.commander_cast = True
            self.enter(card, commander=True)
        elif _is_permanent(card):
            self.enter(card)
        else:
            self.graveyard += 1
        self.emit("cast_spell" if {"Instant", "Sorcery"} & card.types else "cast_any")
        self.pending_lands += card.ramp_lands
        for n, pw, tg, sub in card.tokens:
            for _ in range(n):
                self.add_body(_token_card(pw, tg, sub), token=True)
        self.apply(card.on_resolve)
        if card.overrun or card.overrun_per_creature:
            self.overrun += card.overrun + (board if card.overrun_per_creature else 0)
            self.overrun_trample = self.overrun_trample or card.overrun_trample
        if card.burn:
            self.drain(card.burn)
        self.milled += card.mill
        if card.self_mill:
            self.mill_self(card.self_mill)
        if card.mill_choice:
            self.apply([Effect("mill_choice", amount=card.mill_choice)])
        self.gain(card.life_gain)
        if card.draw:
            self.draw_cards(card.draw)
        if card.alt_win:
            self._set_win("alt_win")
        for rule in card.wins:
            if rule.when == "second_cast":
                self.casts[card.name] = self.casts.get(card.name, 0) + 1
                if self.casts[card.name] >= 2:
                    self._set_win("alt_win")
                else:
                    self.gain(7)
                    self.graveyard -= 1
                    self.library.insert(min(6, len(self.library)), card)
            elif rule.when == "resolve" and self.holds(rule.condition):
                self._set_win("opponents_lose" if rule.opponents_lose else "alt_win")

    def _progress(self, e: Effect) -> float:
        """Damage-equivalent progress of one effect (terminals priced by their totals)."""
        if e.kind == "drain":
            if e.expr:
                return e.amount * (self._eval(e.expr, dict(e.svars), {}) or 0)
            return e.amount * (3 if e.scale == "trigger" else 1)
        if e.scale == "half":
            return 40.0 if self.self_mill_plan else (OPP_LIBRARY_TOTAL - self.milled) / 6 * LIFE_TOTAL / OPP_LIBRARY_TOTAL
        if e.kind == "mill_opp" or (e.kind == "mill_choice" and not self.self_mill_plan):
            return e.amount * LIFE_TOTAL / OPP_LIBRARY_TOTAL
        if e.kind in ("mill_self", "mill_choice") and self.self_mill_plan:
            return e.amount * LIFE_TOTAL / max(20, len(self.library))
        if e.kind == "tokens":
            return e.token[0] * e.token[1] * 0.5
        if e.kind == "draw":
            return self.rho * 2
        if e.kind == "gain":  # one step through "whenever you gain life" drains (Sanguine Bond)
            per_life = sum(x.amount for c in self.permanents() for t in c.triggers if t.event == "lifegain"
                           for x in t.effects if x.kind == "drain" and x.scale == "trigger")
            return e.amount * (3 if e.scale == "trigger" else 1) * per_life
        return 0.0

    def impact(self, c: ClockCard) -> float:
        """Expected future damage-equivalent of casting c now (policy only, not V)."""
        p = self.p
        remaining = max(0, p.horizon - self.t)
        if any(r.when == "resolve" and not self._would_win(c, r) for r in c.wins):
            return 0.0  # hold a conditional win until it wins (Thassa's Oracle, Coalition Victory)
        value = 0.0
        if c.creature:
            conn = p.p_evasive if c.evasive else 1.0
            value += c.power * conn * (2 if c.double_strike else 1) * (remaining + (1 if c.haste else 0))
        for n, pw, *_ in c.tokens:
            value += n * pw * remaining
        with self._pretend(c):
            value += sum(self._progress(e) for e in c.on_resolve)
            value += sum(sum(self._progress(e) for e in trig.effects) * self.rates.get(trig.event, 0.5) * remaining
                         for trig in c.triggers)
        for n, pw, _ in c.tokens_per_turn:
            value += n * pw * remaining * max(0, remaining - 1) / 2
        if c.tap_tokens:
            n, pw, _ = c.tap_tokens
            value += (4 if c.tap_tokens_scale else n) * pw * max(0, remaining - 1) * max(0, remaining - 2) / 2
        value += (c.mana + c.ramp_lands) * remaining * self.rho
        value += c.draw * self.rho * 2.5 * max(0, remaining - 1) * 0.5
        value += c.anthem * remaining * 3
        value += (c.attach_power + (4 if c.attach_per_land else 0)) * max(0, remaining - 1)
        value += c.burn + c.mill * LIFE_TOTAL / OPP_LIBRARY_TOTAL
        value += self._progress(Effect("mill_self", amount=c.self_mill)) if self.self_mill_plan else 0
        value += self._progress(Effect("mill_choice", amount=c.mill_choice))
        for ab in c.abilities:
            per_use = sum(self._progress(e) for e in ab.effects if e.kind != "draw")
            value += per_use * (self.rates.get("sac_fodder", 1.0) if ab.sac else 1.0) * remaining
            if any(e.kind in ("win", "opp_loses", "fetch") for e in ab.effects):
                value += 40
        if c.overrun or c.overrun_per_creature:
            n = len(self.bodies)
            value += (c.overrun + (n if c.overrun_per_creature else 0)) * n
        if c.alt_win:
            value += 1000
        for rule in c.wins:
            if rule.when == "resolve":
                value += 1000  # only reached when it wins now (see the hold above)
            elif rule.when == "second_cast":
                value += 60 if self.casts.get(c.name, 0) else 20
            else:
                value += 40
        return value

    @contextmanager
    def _pretend(self, c: ClockCard):
        """The board as if c had just entered (for policy look-ahead)."""
        if not _is_permanent(c):
            yield
            return
        body = _Body(c, sick=True) if c.creature else None
        (self.bodies.append(body) if body else self.others.append(c))
        try:
            yield
        finally:
            (self.bodies.remove(body) if body else self.others.remove(c))

    def _would_win(self, c: ClockCard, rule: WinRule) -> bool:
        """Would a resolve-time condition hold right after c enters?"""
        with self._pretend(c):
            return self.holds(rule.condition)

    def choose(self, castable: list[ClockCard], mana: int) -> list[int]:
        items = []
        for i, c in enumerate(castable):
            if c.land or c.cmc > mana:
                continue
            v = self.impact(c)
            if v > 0:
                items.append((i, c.cmc, v))
        best = {0: (0.0, [])}
        for i, cost, v in items:
            for spent, (val, picks) in sorted(best.items(), reverse=True):
                s = spent + cost
                if s <= mana and (s not in best or best[s][0] < val + v):
                    best[s] = (val + v, picks + [i])
        return max(best.values(), key=lambda x: x[0])[1]

    # -- a turn ----------------------------------------------------------------------
    def turn(self, t: int) -> None:
        self.t = t
        for _ in range(self.pending_lands):
            basic = next((c for c in self.library if c.land and c.subtypes & set(BASIC_TYPES)), None)
            if basic:
                self.library.remove(basic)
                self.enter(basic)
            else:
                self.enter(ClockCard(name="Basic", land=True, types=frozenset({"Land"})))
        self.pending_lands = 0
        for b in self.bodies:
            b.sick = False
        for maker in self.per_turn_makers:
            for n, pw, tg in maker.tokens_per_turn:
                for _ in range(n):
                    self.add_body(_token_card(pw, tg), token=True)
        if t > self.p.blockers_start:
            self.our_life -= self.p.incoming_damage
            if self.our_life <= 0:
                self.lost = True
                return
        self.emit("upkeep")
        for card in self.permanents():
            for rule in card.wins:
                if rule.when == "upkeep" and self.holds(rule.condition):
                    self._set_win("opponents_lose" if rule.opponents_lose else "alt_win")
        if self.win or self.lost:
            return
        self.draw_cards(1)  # house rule: everyone draws on turn 1
        if self.win or self.lost:
            return
        in_play = {c.name for c in self.lands}
        land = max((c for c in self.hand if c.land), default=None,
                   key=lambda c: (bool(c.abilities), c.name not in in_play))  # Maze's End, new Gate names
        if land:
            self.hand.remove(land)
            self.enter(land)
        mana = len(self.lands) + self.rocks + sum(b.card.mana for b in self.bodies if not b.sick)
        while not (self.win or self.lost):
            castable = self.hand + ([self.commander] if self.commander and not self.commander_cast else [])
            picks = self.choose(castable, mana)
            if not picks:
                break
            drawn = self.drawn
            for i in sorted(picks, reverse=True):
                c = castable[i]
                if c is not self.commander:
                    self.hand.remove(c)
                mana -= c.cmc
                if c.mana and not c.creature:
                    mana += c.mana  # rocks tap the turn they arrive
                self.resolve(c)
                if self.win or self.lost:
                    break
            if self.drawn == drawn:
                break  # nothing new to cast
        if self.win or self.lost:
            return
        mana = self._attach(mana)
        mana = self._activate(mana)
        if not (self.win or self.lost):
            self._combat()
            mana = self._sacrifice(mana)
        self.wasted += max(0, mana)

    def _attach(self, mana: int) -> int:
        for gear in list(self.unattached):
            if not self.bodies or gear.equip_cost > mana:
                continue
            target = max(self.bodies, key=lambda b: (b.commander, b.card.evasive, b.power + b.bonus))
            target.bonus += gear.attach_power + (len(self.lands) if gear.attach_per_land else 0)
            mana -= gear.equip_cost
            self.unattached.remove(gear)
        return mana

    def _activate(self, mana: int) -> int:
        """Abilities with a tap or mana cost (sac outlets: see _sacrifice)."""
        ready = self.lands + self.others + [b.card for b in self.bodies if not b.sick]
        for source in list(ready):
            for ab in source.abilities:
                if ab.sac or ab.mana > mana:
                    continue
                useful = any(
                    e.kind in ("drain", "mill_opp", "mill_choice")
                    or (e.kind == "mill_self" and self.self_mill_plan)
                    or (e.kind == "fetch" and any(e.fetch_type in c.subtypes or e.fetch_type in c.types
                                                  for c in self.library))
                    or (e.kind in ("win", "opp_loses") and self.holds(e.condition))
                    for e in ab.effects
                )
                if not useful:
                    continue
                mana -= ab.mana
                self.apply(ab.effects)
                if ab.return_self and source in self.lands:
                    self.lands.remove(source)
                    self.hand.append(source)
                if self.win or self.lost:
                    return mana
                if ab.tap:
                    break  # one tap ability per permanent per turn
        return mana

    def _combat(self) -> None:
        p = self.p
        overrun, overrun_trample = self.overrun, self.overrun_trample
        self.overrun, self.overrun_trample = 0, False
        tapped = set()
        for b in list(self.bodies):
            if b.card.tap_tokens and not b.sick:
                n, pw, tg = b.card.tap_tokens
                count = len(self.bodies) if b.card.tap_tokens_scale else n
                for _ in range(count):
                    self.add_body(_token_card(pw, tg), token=True)
                tapped.add(id(b))
        if self.t >= p.blockers_start:
            self.pool = min(p.blockers_cap, self.pool + p.blocker_rate)

        def power(b: _Body) -> int:
            return b.power + b.bonus + self.anthem + overrun

        attackers = [b for b in self.bodies if not b.sick and id(b) not in tapped and power(b) > 0]
        # Blockers are assigned as if every attacker were on the ground (the biggest are
        # chump-blocked first), so a keyword never changes who is blocked or how many
        # blockers the table spends: adding Flying, Trample, Double Strike or Lifelink to
        # a creature can only add damage (monotone in every keyword).
        blocked_ids = {id(b) for b in sorted(attackers, key=lambda b: -power(b))[:int(self.pool)]}
        for b in attackers:
            if id(b) in blocked_ids:
                dies = sum(w for t, w in p.blocker_toughness if power(b) >= t)
                self.pool -= dies
                self._deaths += dies
                while self._deaths >= 1:
                    self._deaths -= 1
                    self.emit("dies_any")  # a chump blocker died
        for b in attackers:
            hit = power(b) * (2 if b.card.double_strike else 1)
            if id(b) in blocked_ids:
                through = sum(w * max(0, hit - t) for t, w in p.blocker_toughness)                     if (b.card.trample or overrun_trample) else 0.0
                # an evasive creature is blocked only by the blockers that can block it
                hit = p.p_evasive * hit + (1 - p.p_evasive) * through if b.card.evasive else through
            if b.card.lifelink and hit > 0:
                self.gain(hit)
            if b.card.infect:
                self.poison += hit
            else:
                self.drain(hit, combat=True)
                if b.commander:
                    self.cmd_damage += hit

    def _sacrifice(self, mana: int) -> int:
        """Sac outlets eat tokens after combat when a death is worth more than the body."""
        outlets = [ab for c in self.permanents() for ab in c.abilities if ab.sac]
        if not outlets:
            return mana
        per_death = sum(self._progress(e) for c in self.permanents() for t in c.triggers
                        if t.event in ("dies_yours", "dies_any", "sac_token") for e in t.effects)
        remaining = self.p.horizon - self.t
        for outlet in sorted(outlets, key=lambda ab: (ab.mana, -sum(self._progress(e) for e in ab.effects))):
            gain = per_death + sum(self._progress(e) for e in outlet.effects)
            for body in [b for b in self.bodies if b.token and not b.commander
                         and (outlet.sac_type == "Creature" or outlet.sac_type in b.card.subtypes)]:
                keep = (body.power + body.bonus + self.anthem) * min(remaining, 3) * 0.5
                if self.win or self.lost or outlet.mana > mana or gain <= keep:
                    break
                mana -= outlet.mana
                self.kill(body, sacrificed=True)
                self.apply(outlet.effects)
        return mana

    def _check_totals(self) -> None:
        if self.win or self.lost:
            return
        for name, value, total in (("damage", LIFE_TOTAL - self.opp_life, LIFE_TOTAL),
                                   ("poison", self.poison, POISON_TOTAL),
                                   ("commander", self.cmd_damage, COMMANDER_TOTAL),
                                   ("mill", self.milled, OPP_LIBRARY_TOTAL)):
            if value >= total:
                self._set_win(name)
                return

    def play(self) -> dict:
        dmg_by: dict[int, float] = {}
        for t in range(1, self.p.horizon + 1):
            if not (self.win or self.lost):
                self.turn(t)
                self._check_totals()
            dmg_by[t] = LIFE_TOTAL - self.opp_life
        return {"win": None if self.lost else self.win, "damage_by": dmg_by,
                "wasted": self.wasted / self.p.horizon, "combat": self.combat_damage,
                "direct": self.direct_damage, "lost": self.lost,
                "routes": {"damage": (LIFE_TOTAL - self.opp_life) / LIFE_TOTAL, "poison": self.poison / POISON_TOTAL,
                           "commander": self.cmd_damage / COMMANDER_TOTAL,
                           "mill": min(1.0, self.milled / OPP_LIBRARY_TOTAL)}}


def _shuffle_with_mulligan(deck: list[ClockCard], rng: random.Random, p: ClockParams) -> list[ClockCard]:
    order = deck[:]
    rng.shuffle(order)
    lo, hi = p.mulligan_lands
    if not lo <= sum(c.land for c in order[:7]) <= hi:
        rng.shuffle(order)  # one free mulligan (Commander)
    return order


def _deck_context(cards: list[ClockCard], p: ClockParams) -> tuple[dict[str, float], float, bool]:
    """Event rates for the policy, damage per mana, and whether the plan is self-mill."""
    n = max(1, len(cards))
    spells = sum(1 for c in cards if {"Instant", "Sorcery"} & c.types) / n
    creatures = sum(1 for c in cards if c.creature) / n
    token_makers = sum(1 for c in cards if c.tokens or c.tokens_per_turn or c.tap_tokens
                       or any(e.kind == "tokens" for t in c.triggers for e in t.effects)) / n
    outlets = any(ab.sac for c in cards for ab in c.abilities)
    deaths = (2.0 if outlets else 0.2) * (1 + 3 * token_makers)
    lifegain = any(c.life_gain or any(e.kind == "gain" for t in c.triggers for e in t.effects) for c in cards)
    rates = {
        "cast_spell": 1.3 * spells,
        "cast_any": 1.3 * (1 - sum(c.land for c in cards) / n),
        "landfall": 0.9,
        "upkeep": 1.0,
        "etb_creature": 1.3 * creatures + 2 * token_makers,
        "dies_yours": deaths,
        "dies_any": deaths + 0.6 * p.blocker_rate,
        "lifegain": 1.0 if lifegain else 0.2,
        "sac_fodder": 1 + 3 * token_makers,
        "opp_lifeloss": 1.5,
        "drawn": 1.2,
    }
    threats = [c.power * (p.p_evasive if c.evasive else 1.0) / max(1, c.cmc)
               for c in cards if c.creature and c.power > 0]
    rho = mean(threats) if threats else 0.3
    self_mill_plan = any(
        r.when == "draw_empty" or (r.condition is not None and "ValidLibrary" in r.condition.lhs)
        for c in cards for r in c.wins
    )
    return rates, rho, self_mill_plan


def simulate(deck: list[ClockCard], commander: ClockCard | None = None,
             params: ClockParams | None = None, order: list[ClockCard] | None = None) -> ClockResult:
    """Clock of a deck; `order` fixes the library (deterministic tests)."""
    p = params or ClockParams()
    pool = deck + ([commander] if commander else []) + (order or [])
    rates, rho, self_mill_plan = _deck_context(pool, p)
    rng = random.Random(p.seed)
    libraries = [order[:]] if order is not None else [_shuffle_with_mulligan(deck, rng, p) for _ in range(p.samples)]
    runs = [_Game(lib, commander, p, rates, rho, self_mill_plan).play() for lib in libraries]
    turns = [r["win"][1] if r["win"] else p.horizon + 1 for r in runs]
    terminals: dict[str, float] = {}
    for r in runs:
        key = r["win"][0] if r["win"] else ("decked" if r["lost"] else "none")
        terminals[key] = terminals.get(key, 0) + 1 / len(runs)
    return ClockResult(
        turn=mean(turns),
        turn_var=pvariance(turns) if len(turns) > 1 else 0.0,
        censored=sum(1 for r in runs if not r["win"]) / len(runs),
        damage_by={t: mean(r["damage_by"].get(t, 0) for r in runs) for t in range(1, p.horizon + 1)},
        wasted_mana=mean(r["wasted"] for r in runs),
        terminals=terminals,
        life_sources={"combat": mean(r["combat"] for r in runs), "direct": mean(r["direct"] for r in runs)},
        routes={k: mean(r["routes"][k] for r in runs) for k in runs[0]["routes"]},
        turns=turns,
    )


def vanilla(name: str, cmc: int, power: int = 0, **kw) -> ClockCard:
    """Convenience for tests and examples."""
    creature = power > 0 or kw.pop("creature", False)
    types = kw.pop("types", frozenset({"Creature"}) if creature else frozenset())
    return ClockCard(name=name, cmc=cmc, creature=creature, power=power,
                     toughness=kw.pop("toughness", power), types=types, **kw)


def basic_land(name: str = "Forest") -> ClockCard:
    return ClockCard(name=name, land=True, types=frozenset({"Land"}), subtypes=frozenset({name}))


def with_(card: ClockCard, **kw) -> ClockCard:
    return replace(card, **kw)
