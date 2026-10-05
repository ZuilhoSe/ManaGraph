"""Relógio de dano analítico (plano economia-de-operadores, Fase 2.5 / seção 2.6).

Mede quão rápido um deck chega a um estado terminal das regras, a partir só das
magnitudes dos operadores compilados (poder, mana value, mana produzida, ...):

    m_t      = terrenos_t + ramp disponível em t
    L_t      = o que se lança em t: mochila sobre a mão, maior impacto que cabe em m_t
    dano_t   = Σ poder · P_conectar dos corpos sem enjoo (+ overrun, anthem, trample)
    relógio  = min{t : Σ dano ≥ 120}   (3 oponentes × 40), e relógios paralelos
               para veneno (3 × 10), dano de comandante (3 × 21), biblioteca
               vazia e vitória alternativa

A esperança sobre a ordem de compra é estimada por amostragem de embaralhamentos
(goldfish abstrato: sem oponente jogando, sem Forge). Os oponentes entram só
como capacidade de bloqueio b_t por turno — uma taxa-base, até a Fase 8.

O impacto usado pela política de lançamento é medido no próprio relógio (dano
futuro esperado em equivalente de poder), não em V: evita circularidade.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, replace
from statistics import mean, pvariance

from operators.compile import CardOps

LIFE_TOTAL = 120        # 3 opponents × 40
POISON_TOTAL = 30       # 3 × 10
COMMANDER_TOTAL = 63    # 3 × 21
OPP_LIBRARY_TOTAL = 3 * 85
EVASIVE = {"Flying", "Menace", "Shadow", "Horsemanship", "Fear", "Intimidate", "Skulk",
           "Landwalk", "Islandwalk", "Swampwalk", "Forestwalk", "Mountainwalk", "Plainswalk"}
_TOKEN_PT = re.compile(r"^[wubrgc]+_(\d+)_(\d+)_")
_COUNT_CREATURES = "Count$Valid Creature.YouCtrl"
_COUNT_YOURS = re.compile(r"Count\$Valid \w+\.YouCtrl")
# "You may" is not a condition on the effect happening.
_BENIGN_CONDITIONS = {"Optional", "OptionalDecider"}


def _conditional(conditions) -> bool:
    return any(k not in _BENIGN_CONDITIONS for k, _ in conditions)


def _comparison_gate(op) -> bool:
    """Trigger gated by a comparison kept as magnitude (LifeAmount$ GE50, ...)."""
    return op.source == "trigger" and any(v.startswith("var:") for _, v in op.magnitude)


@dataclass(frozen=True)
class ClockCard:
    """The magnitudes the clock reads off one card's operators."""

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
    mana: int = 0              # mana per turn from a permanent (rock, dork)
    ramp_lands: int = 0        # lands put onto the battlefield when it resolves
    draw: int = 0              # cards drawn when it resolves
    tokens: tuple[tuple[int, int, int], ...] = ()  # (count, power, toughness) on resolve
    tokens_per_turn: tuple[tuple[int, int, int], ...] = ()  # each upkeep
    tap_tokens: tuple[int, int, int] | None = None  # {T}: create tokens (count, power, toughness)
    tap_tokens_scale: bool = False  # count = creatures you control (Krenko)
    attach_power: int = 0      # equipment/aura: +power to the creature it is attached to
    attach_per_land: bool = False  # +X where X = your lands (Blackblade Reforged)
    equip_cost: int = 0        # mana to attach (0 for auras: cast onto the creature)
    anthem: int = 0            # +power to your creatures while it stays
    overrun: int = 0           # +power to your creatures this turn (one-shot)
    overrun_per_creature: bool = False  # Craterhoof: +X where X = your creatures
    overrun_trample: bool = False
    mill: int = 0              # opponent cards milled on resolve
    alt_win: bool = False

    @property
    def permanent(self) -> bool:
        return self.creature or self.mana > 0 or self.anthem > 0 or bool(self.tokens_per_turn)


def _int(v: str | None, default: int = 0) -> int:
    return int(v) if v and re.fullmatch(r"-?\d+", v) else default


def clock_features(card: CardOps) -> ClockCard:
    """Read the clock's magnitudes off a compiled card. Unknown effects add nothing."""
    keywords = {op.kind for op in card.operators if op.source == "keyword"}
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
    )
    mana = ramp = draw = mill = anthem = overrun = 0
    tokens: list[tuple[int, int, int]] = []
    per_turn: list[tuple[int, int, int]] = []
    per_creature = overrun_trample = alt_win = False
    tap_tokens = None
    tap_scale = False
    attach_power = equip_cost = 0
    attach_per_land = False
    for op in card.operators:
        shape = dict(op.shape)
        if _conditional(op.conditions) or _comparison_gate(op):
            continue  # value the clock cannot promise (e.g. "if you control six lands")
        if op.source == "activated" and op.kind == "Token" and op.cost_other == ("T",) and op.cost is not None                 and op.cost.mana_value == 0:
            step = op.steps[0]
            s, m = dict(step.shape), dict(step.magnitude)
            pt = _TOKEN_PT.match(s.get("TokenScript", "").split(",")[0].strip())
            if pt and not _conditional(step.conditions):
                amount = m.get("TokenAmount", "1")
                tap_scale = bool(_COUNT_YOURS.search(amount))
                tap_tokens = (_int(amount, 1), int(pt.group(1)), int(pt.group(2)))
        etb = op.source == "trigger" and op.kind == "ChangesZone" and shape.get("Destination") == "Battlefield" \
            and shape.get("ValidCard") == "Card.Self"
        upkeep = op.source == "trigger" and op.kind == "Phase" and shape.get("Phase") == "Upkeep"
        resolves = op.source == "spell" or etb
        if op.source == "activated" and op.kind == "Mana" and "T" in op.cost_other and not f["land"]:
            step = op.steps[0]
            mana += _int(dict(step.magnitude).get("Amount"), 1)
        if op.source == "static" and op.kind == "Continuous" and shape.get("Affected", "").startswith("Creature.YouCtrl"):
            anthem += _int(dict(op.magnitude).get("AddPower"))
        if op.source == "static" and op.kind == "Continuous" and shape.get("Affected", "") in (
            "Creature.EquippedBy", "Creature.EnchantedBy", "Creature.AttachedBy"
        ):
            add = dict(op.magnitude).get("AddPower", "")
            if "Count$Valid Land.YouCtrl" in add:
                attach_per_land = True
            else:
                attach_power += _int(add)
        if op.source == "keyword" and op.kind == "Equip" and shape.get("arg"):
            cost = shape["arg"].split(":")[0].split()
            value = sum(int(t) if t.isdigit() else 1 for t in cost)
            equip_cost = value if not equip_cost else min(equip_cost, value)
        if not (resolves or upkeep):
            continue
        for step in op.steps:
            if _conditional(step.conditions):
                continue
            s, m = dict(step.shape), dict(step.magnitude)
            if step.api == "ChangeZone.Library>Battlefield" and any(
                t in s.get("ChangeType", "") for t in ("Land", "Forest", "Plains", "Island", "Swamp", "Mountain")
            ):
                ramp += _int(m.get("ChangeNum"), 1)
            elif step.api == "Draw" and s.get("Defined", "You") in ("", "You"):
                draw += _int(m.get("NumCards"), 1)
            elif step.api == "Token" and s.get("TokenOwner", "You") in ("", "You"):
                n = _int(m.get("TokenAmount"), 1)
                for script in s.get("TokenScript", "").split(","):
                    pt = _TOKEN_PT.match(script.strip())
                    if pt:
                        (per_turn if upkeep else tokens).append((n, int(pt.group(1)), int(pt.group(2))))
            elif step.api == "PumpAll" and s.get("ValidCards", "").startswith("Creature.YouCtrl"):
                att = m.get("NumAtt", "")
                if _COUNT_CREATURES in att:
                    per_creature = True
                else:
                    overrun += _int(att)
                overrun_trample = overrun_trample or "Trample" in s.get("KW", "")
            elif step.api == "Mill" and (step.target or "Opponent" in s.get("Defined", "")):
                mill += _int(m.get("NumCards"))
            elif step.api == "WinsGame":
                alt_win = True
    return ClockCard(**f, mana=mana, ramp_lands=ramp, draw=draw, tokens=tuple(tokens),
                     tokens_per_turn=tuple(per_turn), anthem=anthem, overrun=overrun,
                     overrun_per_creature=per_creature, overrun_trample=overrun_trample,
                     mill=mill, alt_win=alt_win, tap_tokens=tap_tokens, tap_tokens_scale=tap_scale,
                     attach_power=attach_power, attach_per_land=attach_per_land, equip_cost=equip_cost)


@dataclass
class ClockParams:
    horizon: int = 10                  # T
    samples: int = 300
    seed: int = 7
    p_evasive: float = 0.85            # P_conectar of an evasive attacker
    blocker_toughness: int = 2         # what a trampler pushes through
    # b_t: the defenders' pool of blockers. Base rate until Fase 8: from turn
    # `blockers_start` the table adds `blocker_rate` blockers per turn; a chump
    # block (attacker power ≥ blocker toughness) spends the blocker.
    blockers_start: int = 2
    blocker_rate: float = 0.75
    blockers_cap: int = 6
    mulligan_lands: tuple[int, int] = (2, 5)


@dataclass
class _Body:
    power: int
    toughness: int
    evasive: bool
    trample: bool
    double_strike: bool
    infect: bool
    sick: bool
    commander: bool = False
    dork: int = 0  # mana it taps for once it can tap
    tap_tokens: tuple[int, int, int] | None = None
    tap_tokens_scale: bool = False
    bonus: int = 0  # from equipment/auras attached to it


@dataclass
class ClockResult:
    turn: float                 # mean turn of the fastest terminal (censored at T + 1)
    turn_var: float
    censored: float             # fraction of samples with no win by T
    damage_by: dict[int, float]  # expected cumulative damage by turn
    wasted_mana: float          # mean unspent mana per turn
    terminals: dict[str, float]  # share of samples won by each terminal
    turns: list[int] = field(default_factory=list, repr=False)


def _impact(c: ClockCard, t: int, p: ClockParams, rho: float) -> float:
    """Expected future damage-equivalent of casting c on turn t (policy only)."""
    remaining = max(0, p.horizon - t)
    attacks = remaining + (1 if c.haste else 0)
    value = 0.0
    if c.creature:
        conn = p.p_evasive if c.evasive else 1.0
        value += c.power * conn * (2 if c.double_strike else 1) * attacks
    for n, pw, _ in c.tokens:
        value += n * pw * remaining
    for n, pw, _ in c.tokens_per_turn:
        value += n * pw * remaining * max(0, remaining - 1) / 2
    if c.tap_tokens:
        n, pw, _ = c.tap_tokens
        value += (4 if c.tap_tokens_scale else n) * pw * max(0, remaining - 1) * max(0, remaining - 2) / 2
    value += (c.mana + c.ramp_lands) * remaining * rho
    value += c.draw * rho * 2.5 * max(0, remaining - 1) * 0.5
    value += c.anthem * remaining * 3
    value += (c.attach_power + (4 if c.attach_per_land else 0)) * max(0, remaining - 1)
    value += c.mill * 0.1
    if c.alt_win:
        value += 1000
    return value


def _choose(hand: list[ClockCard], mana: int, t: int, p: ClockParams, rho: float,
            bodies_now: int) -> list[int]:
    """0/1 knapsack over the hand: indices to cast this turn."""
    items = []
    for i, c in enumerate(hand):
        if c.land or c.cmc > mana:
            continue
        v = _impact(c, t, p, rho)
        if c.overrun or c.overrun_per_creature:
            v = (c.overrun + (bodies_now if c.overrun_per_creature else 0)) * bodies_now
        if v > 0:
            items.append((i, c.cmc, v))
    best = {0: (0.0, [])}
    for i, cost, v in items:
        for spent, (val, picks) in sorted(best.items(), reverse=True):
            s = spent + cost
            if s <= mana and (s not in best or best[s][0] < val + v):
                best[s] = (val + v, picks + [i])
    return max(best.values(), key=lambda x: x[0])[1]


def _rho(deck: list[ClockCard], p: ClockParams) -> float:
    """Deck's damage per mana among its threats: converts ramp/draw into damage."""
    rates = [c.power * (p.p_evasive if c.evasive else 1.0) / max(1, c.cmc)
             for c in deck if c.creature and c.power > 0]
    return mean(rates) if rates else 0.3


def _game(library: list[ClockCard], commander: ClockCard | None, p: ClockParams, rho: float) -> dict:
    hand = library[:7]
    library = library[7:]
    lands = rocks = 0
    pending_lands = 0
    bodies: list[_Body] = []
    anthem = 0
    commander_cast = False
    damage = poison = cmd_damage = milled = 0
    dmg_by: dict[int, int] = {}
    wasted = 0
    win = None
    per_turn_makers: list[ClockCard] = []
    pool = 0.0  # defenders' blockers
    unattached: list[ClockCard] = []  # equipment waiting for equip mana / a body
    for t in range(1, p.horizon + 1):
        # Untap / upkeep: lands from ramp arrive, bodies lose summoning sickness.
        lands += pending_lands
        pending_lands = 0
        for b in bodies:
            b.sick = False
        for maker in per_turn_makers:
            for n, pw, tg in maker.tokens_per_turn:
                bodies += [_Body(pw, tg, False, False, False, False, True) for _ in range(n)]
        if library:
            hand.append(library.pop(0))  # house rule: everyone draws on turn 1
        land = next((c for c in hand if c.land), None)
        if land:
            hand.remove(land)
            lands += 1
        mana = lands + rocks + sum(b.dork for b in bodies if not b.sick)
        overrun_now = 0
        overrun_trample = False
        castable = list(hand)
        if commander and not commander_cast:
            castable.append(commander)
        while True:
            picks = _choose(castable, mana, t, p, rho, len(bodies))
            if not picks:
                break
            drew = False
            for i in sorted(picks, reverse=True):
                c = castable.pop(i)
                if c is commander:
                    commander_cast = True
                else:
                    hand.remove(c)
                mana -= c.cmc
                if c.creature:
                    bodies.append(_Body(c.power, c.toughness, c.evasive, c.trample, c.double_strike,
                                        c.infect, not c.haste, commander=c is commander, dork=c.mana,
                                        tap_tokens=c.tap_tokens, tap_tokens_scale=c.tap_tokens_scale))
                elif c.mana:
                    rocks += c.mana
                    mana += c.mana  # rocks tap the turn they arrive
                pending_lands += c.ramp_lands
                anthem += c.anthem
                if c.attach_power or c.attach_per_land:
                    unattached.append(c)
                if c.tokens_per_turn:
                    per_turn_makers.append(c)
                for n, pw, tg in c.tokens:
                    bodies += [_Body(pw, tg, False, False, False, False, True) for _ in range(n)]
                if c.overrun or c.overrun_per_creature:
                    overrun_now += c.overrun + (len(bodies) if c.overrun_per_creature else 0)
                    overrun_trample = overrun_trample or c.overrun_trample
                milled += c.mill
                if c.draw:
                    for _ in range(min(c.draw, len(library))):
                        hand.append(library.pop(0))
                    drew = True
                if c.alt_win and win is None:
                    win = ("alt_win", t)
            castable = list(hand) + ([commander] if commander and not commander_cast else [])
            if not drew:
                break
        # Attach equipment/auras to the best attacker (commander first, then evasive, then biggest).
        for gear in list(unattached):
            cost = gear.equip_cost
            if not bodies or cost > mana:
                continue
            target = max(bodies, key=lambda b: (b.commander, b.evasive, b.power + b.bonus))
            target.bonus += gear.attach_power + (lands if gear.attach_per_land else 0)
            mana -= cost
            unattached.remove(gear)
        wasted += max(0, mana)
        # Combat: blockers soak the biggest non-evasive attackers.
        # Token factories tap for their ability instead of attacking.
        tapped = set()
        for b in list(bodies):
            if b.tap_tokens and not b.sick:
                n, pw, tg = b.tap_tokens
                count = len(bodies) if b.tap_tokens_scale else n
                bodies += [_Body(pw, tg, False, False, False, False, True) for _ in range(count)]
                tapped.add(id(b))
        if t >= p.blockers_start:
            pool = min(p.blockers_cap, pool + p.blocker_rate)
        attackers = [b for b in bodies if not b.sick and id(b) not in tapped and b.power + b.bonus + anthem + overrun_now > 0]
        ground = sorted((b for b in attackers if not b.evasive), key=lambda b: -(b.power + b.bonus + anthem + overrun_now))
        blocked = set(map(id, ground[:int(pool)]))
        # Chump blocks cost the defenders their blocker.
        pool -= sum(1 for b in ground[:int(pool)] if b.power + b.bonus + anthem + overrun_now >= p.blocker_toughness)
        for b in attackers:
            power = (b.power + b.bonus + anthem + overrun_now) * (2 if b.double_strike else 1)
            if id(b) in blocked:
                tramples = b.trample or overrun_trample
                hit = max(0, power - p.blocker_toughness) if tramples else 0
            else:
                hit = power * (p.p_evasive if b.evasive else 1.0)
            if b.infect:
                poison += hit
            else:
                damage += hit
                if b.commander:
                    cmd_damage += hit
        dmg_by[t] = damage
        if win is None:
            for name, value, total in (("damage", damage, LIFE_TOTAL), ("poison", poison, POISON_TOTAL),
                                       ("commander", cmd_damage, COMMANDER_TOTAL),
                                       ("mill", milled, OPP_LIBRARY_TOTAL)):
                if value >= total:
                    win = (name, t)
                    break
    return {"win": win, "damage_by": dmg_by, "wasted": wasted / p.horizon}


def _shuffle_with_mulligan(deck: list[ClockCard], rng: random.Random, p: ClockParams) -> list[ClockCard]:
    order = deck[:]
    rng.shuffle(order)
    lo, hi = p.mulligan_lands
    if not lo <= sum(c.land for c in order[:7]) <= hi:
        rng.shuffle(order)  # one free mulligan (Commander)
    return order


def simulate(deck: list[ClockCard], commander: ClockCard | None = None,
             params: ClockParams | None = None, order: list[ClockCard] | None = None) -> ClockResult:
    """Clock of a deck; `order` fixes the library (deterministic tests)."""
    p = params or ClockParams()
    rho = _rho(deck + ([commander] if commander else []), p)
    rng = random.Random(p.seed)
    runs = [_game(order[:], commander, p, rho)] if order is not None else [
        _game(_shuffle_with_mulligan(deck, rng, p), commander, p, rho) for _ in range(p.samples)
    ]
    turns = [r["win"][1] if r["win"] else p.horizon + 1 for r in runs]
    terminals: dict[str, float] = {}
    for r in runs:
        key = r["win"][0] if r["win"] else "none"
        terminals[key] = terminals.get(key, 0) + 1 / len(runs)
    return ClockResult(
        turn=mean(turns),
        turn_var=pvariance(turns) if len(turns) > 1 else 0.0,
        censored=sum(1 for r in runs if not r["win"]) / len(runs),
        damage_by={t: mean(r["damage_by"].get(t, 0) for r in runs) for t in range(1, p.horizon + 1)},
        wasted_mana=mean(r["wasted"] for r in runs),
        terminals=terminals,
        turns=turns,
    )


def vanilla(name: str, cmc: int, power: int = 0, **kw) -> ClockCard:
    """Convenience for tests and examples."""
    return ClockCard(name=name, cmc=cmc, creature=power > 0 or kw.pop("creature", False),
                     power=power, toughness=kw.pop("toughness", power), **kw)


def basic_land(name: str = "Forest") -> ClockCard:
    return ClockCard(name=name, land=True)


def with_(card: ClockCard, **kw) -> ClockCard:
    return replace(card, **kw)
