"""Predict a card's resource Δ from its compiled operators (Fase 3).

The prediction uses only the abstract operators (`operators.compile`), never the
Forge engine, and is compared against what Forge measures for the same state
(`tools/forge_oracle/ForgeOracle.java`). Keys follow the oracle's vector:
`p0.*` is the opponent, `p1.*` the acting player.

Scope is first-order effects of a single spell or ability resolving once
(costs excluded). Anything the predictor cannot model returns None ("outside
the predictor"), reported separately from a wrong prediction.

The experiment state is built so the prediction knows what the oracle will
target: one test permanent per target class (on the actor's side when the
ability says "you control"), and targeting mirrors ForgeOracle.chooseTarget —
the opponent if a player can be targeted, else the opponent's permanent, else
the actor's.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from operators.compile import CardOps, Step


@dataclass(frozen=True)
class TestPermanent:
    name: str
    kind: str  # creature | artifact | enchantment | land
    types: frozenset[str]
    colors: frozenset[str]
    cmc: int
    power: int = 0
    toughness: int = 0
    supertypes: frozenset[str] = frozenset()


PERMANENTS = {
    "creature": TestPermanent("Grizzly Bears", "creature", frozenset({"Creature"}), frozenset({"Green"}), 2, 2, 2),
    "artifact": TestPermanent("Sol Ring", "artifact", frozenset({"Artifact"}), frozenset(), 1),
    "enchantment": TestPermanent("Glorious Anthem", "enchantment", frozenset({"Enchantment"}), frozenset({"White"}), 3),
    "land": TestPermanent("Island", "land", frozenset({"Land"}), frozenset(), 0, supertypes=frozenset({"Basic"})),
}
LIBRARY_SIZE = 10  # Islands in each library
# The opponent's hand: one land, one creature, one instant — so "nonland card",
# "creature card" and plain discards all have something to hit.
OPP_HAND = ("Island", "Grizzly Bears", "Lightning Bolt")
_HAND_FACTS = {
    "Island": {"types": {"Land"}, "cmc": 0},
    "Grizzly Bears": {"types": {"Creature"}, "cmc": 2},
    "Lightning Bolt": {"types": {"Instant"}, "cmc": 1},
}

SUPPORTED = {
    "DealDamage", "Draw", "GainLife", "LoseLife", "Destroy", "Token", "PutCounter", "Pump",
    "Discard", "Mill", "Mana", "Tap", "Scry",
    "ChangeZone.Battlefield>Hand", "ChangeZone.Battlefield>Exile",
    "ChangeZone.Battlefield>Library", "ChangeZone.Library>Hand", "ChangeZone.Library>Battlefield",
}
# Keywords that add activated abilities the oracle might resolve instead.
ACTIVATED_KEYWORDS = {
    "Cycling", "TypeCycling", "Equip", "Crew", "Ninjutsu", "Reconfigure", "Level up", "Outlast",
    "Scavenge", "Unearth", "Transmute", "Forecast", "Channel", "Saddle", "Station", "Fortify",
    "Embalm", "Eternalize", "Encore", "Boast", "Adapt", "Monstrosity",
}
# Who a Defined$/TokenOwner$ means, given that the target is the opponent's.
TARGET_CONTROLLER = ("TargetedController", "TargetedOwner", "TargetedPlayer", "ParentTarget",
                     "Targeted", "TargetedCard.Controller")
_TOKEN_PT = re.compile(r"^[wubrgc]+_(\d+)_(\d+)_")
_TOKEN_ARTIFACT = ("treasure", "food", "clue", "blood", "map", "powerstone", "gold", "junk")
_CMP = re.compile(r"^(power|toughness|cmc)(LT|LE|EQ|GE|GT|NE)(\d+)$")
_COLORS = ("White", "Blue", "Black", "Red", "Green", "Colorless")


@dataclass
class Setup:
    target_class: str | None  # which test permanent exists, if any
    target_side: str = "p0"   # who controls it
    state: list[str] = field(default_factory=list)


def _num(value: str | None, default: int = 1) -> int | None:
    if value is None or value == "":
        return default
    return int(value) if re.fullmatch(r"-?\d+", value) else None


def _qualifier_holds(q: str, perm: TestPermanent) -> bool | None:
    """Does the test permanent satisfy one Forge qualifier? None: unknown."""
    if q in ("OppCtrl", "YouDontCtrl", "YouCtrl", "YouOwn", "OppOwn", "Other", "untapped",
             "nonToken", "nonLegendary", "inZoneBattlefield"):
        return True
    m = _CMP.match(q)
    if m:
        value = {"power": perm.power, "toughness": perm.toughness, "cmc": perm.cmc}[m.group(1)]
        n = int(m.group(3))
        return {"LT": value < n, "LE": value <= n, "EQ": value == n,
                "GE": value >= n, "GT": value > n, "NE": value != n}[m.group(2)]
    neg = q.startswith("non")
    word = q[3:] if neg else q
    if word in _COLORS:
        has = word in perm.colors or (word == "Colorless" and not perm.colors)
        return has != neg
    if word in ("Creature", "Artifact", "Enchantment", "Land", "Planeswalker", "Instant", "Sorcery"):
        return (word in perm.types) != neg
    if word in ("Basic", "Legendary", "Snow"):
        return (word in perm.supertypes) != neg
    if neg:  # nonWall, nonHuman, nonDragon ...: the test permanents have no subtypes
        return True
    return None


def _target_class(target: str) -> tuple[str | None, str] | None:
    """(class, side) of the permanent the oracle will target, or None if out of scope.

    class None means the opponent player is targetable (the oracle prefers it).
    """
    alternatives = target.split(",")
    if any(a.split(".")[0] in ("Any", "Player", "Opponent") for a in alternatives):
        return None, "p0"
    for cls in ("creature", "artifact", "enchantment", "land"):
        perm = PERMANENTS[cls]
        for alt in alternatives:
            base, _, quals = alt.partition(".")
            if base not in ("Creature", "Artifact", "Enchantment", "Land", "Permanent", "Card"):
                continue
            if base not in ("Permanent", "Card") and base.lower() != cls:
                continue
            ok = True
            for q in [x for x in quals.split("+") if x]:
                held = _qualifier_holds(q, perm)
                if held is None or not held:
                    ok = False
                    break
            if ok:
                side = "p1" if ("YouCtrl" in quals or "YouOwn" in quals) else "p0"
                return cls, side
    return "unsupported", "p0"


def _the_operator(card: CardOps):
    """The operator the oracle resolves: the spell, or the first activated ability."""
    if card.faces:
        return None
    if any(op.source == "keyword" and op.kind in ACTIVATED_KEYWORDS for op in card.operators):
        return None
    spells = [op for op in card.operators if op.source == "spell"]
    if spells:
        return spells[0] if len(spells) == 1 else None
    activated = [op for op in card.operators if op.source == "activated"]
    non_mana = [op for op in activated if op.kind != "Mana"]
    if non_mana:
        return non_mana[0]
    return activated[0] if activated else None


def setup_for(card: CardOps) -> Setup | None:
    """State for the experiment, or None when the card is outside the predictor."""
    op = _the_operator(card)
    if op is None:
        return None
    on_battlefield = op.source != "spell"
    if on_battlefield and ("Aura" in card.subtypes or card.toughness in ("0", "*")):
        return None  # cannot sit on the battlefield unattached / dies to 0 toughness
    chosen: set[tuple] = set()
    for step in op.steps:
        if step.api not in SUPPORTED or step.conditions:
            return None
        if any(_num(v, 0) is None for _, v in step.magnitude):
            return None  # symbolic amount (X, counts)
        shape = dict(step.shape)
        if any("Kicked" in v or "Condition" in k for k, v in step.shape):
            return None
        if int(shape.get("TargetMax", "1") or 1) > 1 or "TargetMin" in shape:
            return None
        if step.target:
            got = _target_class(step.target)
            if got is not None and got[0] == "unsupported":
                return None
            if got is not None and got[0] is not None:
                chosen.add(got)
    if len(chosen) > 1:
        return None
    target_class, side = next(iter(chosen), (None, "p0"))
    state = ["p0life=40", "p1life=40",
             "p0library=" + ";".join(["Island"] * LIBRARY_SIZE),
             "p1library=" + ";".join(["Island"] * LIBRARY_SIZE),
             "p0hand=" + ";".join(OPP_HAND)]
    p1_battlefield = [card.name] if on_battlefield else []
    if target_class:
        name = PERMANENTS[target_class].name
        if side == "p1":
            p1_battlefield.insert(0, name)  # the oracle targets the first match it finds
        else:
            state.append(f"p0battlefield={name}")
    if p1_battlefield:
        state.append("p1battlefield=" + ";".join(p1_battlefield))
    if not on_battlefield:
        state.append(f"p1hand={card.name}")
    return Setup(target_class, side, state)


def _valid_in_hand(valid: str) -> int | None:
    """How many of the opponent's hand cards match a DiscardValid$ filter."""
    if not valid or valid == "Card":
        return len(OPP_HAND)
    count = 0
    for name in OPP_HAND:
        facts = _HAND_FACTS[name]
        perm = TestPermanent(name, "", frozenset(facts["types"]), frozenset(), facts["cmc"])
        matches_any = False
        for alt in valid.split(","):
            base, _, quals = alt.partition(".")
            if base not in ("Card", "Creature", "Land", "Instant", "Sorcery", "Artifact", "Enchantment"):
                return None
            if base != "Card" and base not in facts["types"]:
                continue
            held = [_qualifier_holds(q, perm) for q in quals.split("+") if q]
            if any(h is None for h in held):
                return None
            if all(held):
                matches_any = True
        count += matches_any
    return count


def _leave(delta: Counter, side: str, perm: TestPermanent, to: str) -> None:
    delta[f"{side}.permanents"] -= 1
    delta[f"{side}.{to}"] += 1
    if perm.kind == "creature":
        delta[f"{side}.creatures"] -= 1
        delta[f"{side}.power"] -= perm.power
        delta[f"{side}.toughness"] -= perm.toughness
    elif perm.kind in ("artifact", "enchantment"):
        delta[f"{side}.{perm.kind}s"] -= 1
    elif perm.kind == "land":
        delta[f"{side}.lands"] -= 1


def _players(defined: str, on_player: bool, target_side: str) -> list[str] | None:
    """Which players an effect hits, from its Defined$ (or its player target)."""
    if on_player:
        return ["p0"]
    if not defined or defined == "You":
        return ["p1"]
    if defined in ("Player", "Player.All"):
        return ["p0", "p1"]
    if defined.startswith(("Opponent", "Player.Opponent")) or defined in ("Opponent",):
        return ["p0"]
    if defined.startswith(TARGET_CONTROLLER):
        return [target_side]
    return None


def predict(card: CardOps, setup: Setup) -> dict[str, int] | None:
    """Expected Δ of the oracle's resource vector (only non-zero keys)."""
    op = _the_operator(card)
    delta: Counter = Counter()
    perm = PERMANENTS.get(setup.target_class) if setup.target_class else None
    side = setup.target_side
    alive = perm is not None
    power = perm.power if perm else 0
    toughness = perm.toughness if perm else 0
    opp_hand = list(OPP_HAND)
    library = {"p0": LIBRARY_SIZE, "p1": LIBRARY_SIZE}
    for step in op.steps:
        shape = dict(step.shape)
        mag = dict(step.magnitude)
        on_player = bool(step.target) and _target_class(step.target) == (None, "p0")
        on_permanent = bool(step.target) and not on_player
        api = step.api
        players = _players(shape.get("Defined", ""), on_player, side)
        if api == "DealDamage":
            n = _num(mag.get("NumDmg"))
            if on_permanent:
                if alive and perm.kind == "creature" and n >= toughness:
                    to = "exile" if any(k.startswith("ReplaceDying") for k in shape) else "graveyard"
                    _leave(delta, side, perm, to)
                    alive = False
            else:
                if players is None:
                    return None
                for p in players:
                    delta[f"{p}.life"] -= n
        elif api in ("Draw", "Mill"):
            n = _num(mag.get("NumCards"))
            if players is None:
                return None
            for p in players:
                k = min(n, library[p])
                library[p] -= k
                delta[f"{p}.library"] -= k
                delta[f"{p}.{'hand' if api == 'Draw' else 'graveyard'}"] += k
        elif api in ("GainLife", "LoseLife"):
            if players is None:
                return None
            n = _num(mag.get("LifeAmount"))
            for p in players:
                delta[f"{p}.life"] += n if api == "GainLife" else -n
        elif api == "Destroy":
            if not (on_permanent and alive):
                return None
            _leave(delta, side, perm, "graveyard")
            alive = False
        elif api == "Token":
            owner_field = shape.get("TokenOwner", "")
            owners = _players(owner_field, False, side) if owner_field else ["p1"]
            if owners is None:
                return None
            n = _num(mag.get("TokenAmount"))
            scripts = [s.strip() for s in shape.get("TokenScript", "").split(",") if s.strip()]
            if not scripts:
                return None
            for owner in owners:
                for script in scripts:
                    delta[f"{owner}.tokens"] += n
                    delta[f"{owner}.permanents"] += n
                    if shape.get("TokenTapped") == "True":
                        delta[f"{owner}.tapped"] += n
                    m = _TOKEN_PT.match(script)
                    if m:
                        delta[f"{owner}.creatures"] += n
                        delta[f"{owner}.power"] += n * int(m.group(1))
                        delta[f"{owner}.toughness"] += n * int(m.group(2))
                        if "_a_" in script:
                            delta[f"{owner}.artifacts"] += n
                    elif any(k in script for k in _TOKEN_ARTIFACT):
                        delta[f"{owner}.artifacts"] += n
                    else:
                        return None
        elif api == "PutCounter":
            if shape.get("CounterType") != "P1P1" or not (on_permanent and alive and perm.kind == "creature"):
                return None
            n = _num(mag.get("CounterNum"))
            delta[f"{side}.p1p1"] += n
            delta[f"{side}.power"] += n
            delta[f"{side}.toughness"] += n
            power, toughness = power + n, toughness + n
        elif api == "Pump":
            if not (on_permanent and alive and perm.kind == "creature"):
                return None
            a, d = _num(mag.get("NumAtt"), 0), _num(mag.get("NumDef"), 0)
            if toughness + d <= 0:
                _leave(delta, side, perm, "graveyard")
                alive = False
            else:
                delta[f"{side}.power"] += a
                delta[f"{side}.toughness"] += d
                power, toughness = power + a, toughness + d
        elif api == "Discard":
            if players != ["p0"]:
                return None
            mode = shape.get("Mode", "TgtChoose")
            n = _num(mag.get("NumCards"))
            if mode in ("RevealYouChoose", "LookYouChoose", "RevealOppChoose"):
                valid = _valid_in_hand(shape.get("DiscardValid", "Card"))
                if valid is None:
                    return None
                n = min(n, valid)
            elif mode == "Hand":
                n = len(opp_hand)
            elif mode not in ("TgtChoose", "Random"):
                return None
            n = min(n, len(opp_hand))
            opp_hand = opp_hand[n:]
            delta["p0.hand"] -= n
            delta["p0.graveyard"] += n
        elif api == "Mana":
            n = _num(mag.get("Amount"))
            produced = shape.get("Produced", "")
            delta["p1.mana"] += n if ("Combo" in produced or "Any" in produced) else n * max(1, len(produced.split()))
        elif api == "Tap":
            if not (on_permanent and alive):
                return None
            delta[f"{side}.tapped"] += 1
        elif api == "Scry":
            pass
        elif api.startswith("ChangeZone.Battlefield>"):
            if not (on_permanent and alive):
                return None
            _leave(delta, side, perm, api.rsplit(">", 1)[1].lower())
            alive = False
        elif api in ("ChangeZone.Library>Hand", "ChangeZone.Library>Battlefield"):
            change_type = shape.get("ChangeType", "Card")
            if change_type not in ("Card", "Land", "Land.Basic", "Card.Land", "Basic", "Land.Basic+YouOwn"):
                return None
            if "DifferentNames" in shape:
                return None
            n = min(_num(mag.get("ChangeNum")), library["p1"])
            library["p1"] -= n
            delta["p1.library"] -= n
            if api.endswith("Hand"):
                delta["p1.hand"] += n
            else:
                delta["p1.lands"] += n
                delta["p1.permanents"] += n
                if shape.get("Tapped") == "True":
                    delta["p1.tapped"] += n
        else:
            return None
    return {k: v for k, v in delta.items() if v}


def compare(predicted: dict[str, int], measured: dict[str, int], tolerance: float = 0.10) -> tuple[bool, dict]:
    """Pass when every key (predicted or measured to change) is within tolerance."""
    misses = {}
    for key in set(predicted) | {k for k, v in measured.items() if v}:
        p, m = predicted.get(key, 0), measured.get(key, 0)
        if abs(p - m) > tolerance * max(abs(m), 1):
            misses[key] = (p, m)
    return not misses, misses
