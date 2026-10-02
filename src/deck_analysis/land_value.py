"""Value of a land relative to the basic it would replace (Fase 0).

A land slot is a mana source first. A basic land is the reference point: it
enters untapped, taps for one mana of one identity color, and has no drawback.
Every non-basic has to *earn* its slot against that basic:

    delta = fixing_gain + extra_mana + utility
            - tapped_cost - drawback_cost

All terms are in units of "one basic land drop". A non-basic enters the deck
only when ``delta > 0``. This replaces the old behaviour where any land under
quota got ``+3.0`` and the tie between non-basics was broken by text cosine
with the commander (which is how Rainbow Vale beat Mountain).

The parser reads Oracle text only, so it works on the SQLite catalog as it is
today. It is deliberately conservative: when a mana ability is restricted
("Spend this mana only ..."), conditional or needs extra mana, it is
discounted, not trusted. Phase 2 replaces this parser with operators compiled
from Forge scripts; the cost model below stays.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

COLORS = ("W", "U", "B", "R", "G")
BASIC_TYPES = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G"}
BASIC_NAMES = {
    "plains", "island", "swamp", "mountain", "forest", "wastes",
    "snow-covered plains", "snow-covered island", "snow-covered swamp",
    "snow-covered mountain", "snow-covered forest", "snow-covered wastes",
}
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "x": 1}

# --- cost model (units: one basic land drop) --------------------------------
TAPPED_COST = 0.2             # unconditional "enters tapped": one turn of tempo
COND_TAPPED_COST = 0.08       # "enters tapped unless ..."
SHOCK_COST = 0.05             # "you may pay 2 life ... If you don't, it enters tapped"
PAIN_COST = 0.05              # per life lost per tap ("deals N damage to you")
EXTRA_MANA_VALUE = 0.5        # per mana beyond one per tap (ramp every turn)
SAC_LAND_COST = 0.6           # per other land sacrificed on ETB (card disadvantage)
BOUNCE_COST = 0.3             # bounce lands: re-play a land (tempo)
SELF_BOUNCE_COST = 0.6        # land returns itself to hand: costs a land drop every turn
SELF_SAC_COST = 0.3           # one-shot lands that sacrifice themselves for mana
CONTROL_LOSS_COST = 1.5       # an opponent gains control of the land
OPP_GIFT_COST = 0.6           # repeated gift to an opponent per tap (tokens, cards, mana)
UTILITY_VALUE = 0.15          # per non-mana activated ability, capped
UTILITY_CAP = 0.3
FILTER_VALUE = 0.1            # "{1}, {T}: Add one mana of any color" style filtering
CONDITIONAL_WEIGHT = 0.6      # "Activate only if ..." / opponent-dependent colors
FETCH_TAPPED_COST = 0.2       # fetch puts the land in tapped (Evolving Wilds)
DEFAULT_GENERIC_SHARE = 0.5   # share of mana demand payable by any mana (no pips yet)

ADD_CLAUSE_RE = re.compile(r"(?i)\badd\b([^.\n]*)")
SYMBOL_RE = re.compile(r"\{([^}]+)\}")
ANY_COLOR_RE = re.compile(
    r"(?i)\b(one|two|three|four|five|x)?\s*mana of any (one )?(color|type)\b"
)
FETCH_RE = re.compile(
    r"search your library for (a|an|up to \w+) [^.]*?(land|plains|island|swamp|mountain|forest)"
    r"[^.]*?cards?,? put (it|them) onto the battlefield"
)
SAC_LANDS_RE = re.compile(
    r"sacrifice (a|an|one|two|three|\d+) (untapped )?(lands?|plains|islands?|swamps?|mountains?|forests?)\b"
)
NONTAP_ACTIVATED_RE = re.compile(r"^[^\"]*\{[^}]+\}[^:\"]*:\s")
ACTIVATED_RE = re.compile(r"^\s*([^:\n]*\{T\}[^:\n]*):\s*(.+)$", re.MULTILINE)


@dataclass
class LandProfile:
    name: str
    colors: set[str] = field(default_factory=set)       # unconditional colored output
    weak_colors: set[str] = field(default_factory=set)  # conditional / opponent-dependent
    colorless: bool = False
    amount: int = 0                 # max mana per tap from an unconditional ability
    is_fetch: bool = False
    fetch_tapped: bool = False
    enters_tapped: str = "no"       # no | always | conditional | shock
    pain: int = 0                   # life lost per tap
    sac_lands: int = 0
    bounce: bool = False
    self_bounce: bool = False       # returns itself to hand: eats a land drop each turn
    self_sac: bool = False
    control_loss: bool = False
    opp_gift: bool = False
    filters: int = 0
    utility: int = 0
    restricted_only: bool = False   # every colored ability is "spend only to ..."

    def produces_mana(self) -> bool:
        return bool(self.colors or self.weak_colors or self.colorless or self.is_fetch or self.filters)


def _norm(text: str | None) -> str:
    return (text or "").replace("\\n", "\n")


def is_basic(name: str, type_line: str = "") -> bool:
    if (name or "").strip().lower() in BASIC_NAMES:
        return True
    return "basic" in (type_line or "").lower() and "land" in (type_line or "").lower()


def _clause_output(clause: str, identity: list[str]) -> tuple[set[str], bool, int]:
    """Colors, colorless flag and amount produced by one 'Add ...' clause."""
    colors: set[str] = set()
    colorless = False
    amount = 0
    low = clause.lower()
    any_match = ANY_COLOR_RE.search(clause)
    if any_match:
        qty = WORD_NUM.get((any_match.group(1) or "one").lower(), 1)
        if "commander's color identity" in low or "color identity" in low:
            colors |= set(identity)
        else:
            colors |= set(COLORS)
        return colors, colorless, qty
    symbols = [s.upper() for s in SYMBOL_RE.findall(clause)]
    if not symbols:
        return colors, colorless, 0
    choice = bool(re.search(r"\bor\b", low))
    for sym in symbols:
        if sym in COLORS:
            colors.add(sym)
        elif sym == "C":
            colorless = True
        elif sym.isdigit():
            colorless = True
    if choice:
        amount = 1
    else:
        amount = sum(int(s) if s.isdigit() else 1 for s in symbols)
    return colors, colorless, amount


def parse_land(name: str, type_line: str, oracle_text: str, identity: list[str] | None = None) -> LandProfile:
    identity = [c for c in (identity or []) if c in COLORS]
    text = _norm(oracle_text)
    low = text.lower()
    me = (name or "").lower()
    prof = LandProfile(name=name)

    # Basic land types on the type line give intrinsic mana abilities.
    tl = (type_line or "").lower()
    for word, color in BASIC_TYPES.items():
        if re.search(rf"\b{word}\b", tl):
            prof.colors.add(color)
            prof.amount = max(prof.amount, 1)

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.lower().startswith("whenever") and "opponent" not in stripped.lower():
            prof.utility += 1
        elif "{T}" not in stripped and NONTAP_ACTIVATED_RE.match(stripped):
            prof.utility += 1
        elif stripped.lower().startswith("channel"):
            prof.utility += 1

    restricted_hits = 0
    colored_hits = 0
    for line in text.split("\n"):
        line_l = line.lower()
        for m in ACTIVATED_RE.finditer(line):
            cost, effect = m.group(1), m.group(2)
            effect_l = effect.lower()
            if not re.search(r"\badd\b", effect_l):
                if not FETCH_RE.search(effect_l):
                    prof.utility += 1
                continue
            clause_m = ADD_CLAUSE_RE.search(effect)
            colors, colorless, amount = _clause_output(clause_m.group(1) if clause_m else effect, identity)
            extra_mana_cost = bool(re.search(r"\{\d+\}|\{[WUBRGC]\}", cost))
            granted = '"' in line[: m.start(1)] or line.strip().startswith('"')
            cost_l = cost.lower()
            other_sac = "sacrifice" in cost_l and not re.search(
                rf"sacrifice (this land|it|{re.escape(me)})\b", cost_l
            )
            conditional = (
                granted
                or other_sac
                or "remove" in cost_l
                or "counter" in cost_l
                or "activate only if" in effect_l
                or "activate only as" in effect_l
                or " among " in effect_l
            )
            restricted = "spend this mana only" in effect_l or "can't be spent" in effect_l
            if colors:
                colored_hits += 1
            if restricted:
                restricted_hits += 1 if colors else 0
                if colorless and not colors:
                    prof.colorless = True
                # Creature-only mana still casts most of a creature deck: weak, not zero.
                if colors and "creature spell" in effect_l:
                    prof.weak_colors |= colors
                continue
            if extra_mana_cost:
                if colors:
                    prof.filters += 1
                continue
            opponent_dependent = "opponent controls could produce" in effect_l or "could produce" in effect_l
            if conditional or opponent_dependent:
                prof.weak_colors |= colors
            else:
                prof.colors |= colors
                prof.amount = max(prof.amount, amount)
            if colorless:
                prof.colorless = True
            dmg = re.search(r"deals (\d+) damage to you", effect_l)
            if dmg:
                prof.pain = max(prof.pain, int(dmg.group(1)))
            elif "pay 1 life" in effect_l or "lose 1 life" in effect_l:
                prof.pain = max(prof.pain, 1)
            if re.search(rf"\bsacrifice (this land|it|{re.escape(me)})\b", cost.lower() + " " + effect_l):
                prof.self_sac = True

        # Fetch lands: search for a land and put it onto the battlefield.
        if FETCH_RE.search(line_l):
            prof.is_fetch = True
            named = {c for w, c in BASIC_TYPES.items() if re.search(rf"\b{w}\b", line_l)}
            if named:
                prof.colors |= named & (set(identity) or set(COLORS))
            elif "basic land" in line_l:
                prof.colors |= set(identity)
            prof.amount = max(prof.amount, 1)
            if "tapped" in line_l:
                prof.fetch_tapped = True

    prof.restricted_only = colored_hits > 0 and restricted_hits == colored_hits and not prof.colors

    # Enters tapped.
    if re.search(r"enters (the battlefield )?tapped", low):
        if re.search(r"you may pay \d+ life", low):
            prof.enters_tapped = "shock"
        elif re.search(r"enters (the battlefield )?tapped unless", low) or "if you don't" in low or "if you control" in low.split("enters")[0]:
            prof.enters_tapped = "conditional"
        else:
            prof.enters_tapped = "always"

    # ETB costs and drawbacks.
    # Lands sacrificed to put this one into play (ETB trigger or replacement).
    for line in text.split("\n"):
        if ACTIVATED_RE.match(line) or NONTAP_ACTIVATED_RE.match(line.strip()):
            continue
        sac = SAC_LANDS_RE.search(line.lower())
        if sac:
            word = sac.group(1)
            n = 1 if word == "a" else (int(word) if word.isdigit() else WORD_NUM.get(word, 1))
            prof.sac_lands = max(prof.sac_lands, n)
    if re.search(r"return (a|an) (untapped )?(non-lair )?(lands?|plains|islands?|swamps?|mountains?|forests?) you control to its owner's hand", low):
        prof.bounce = True
    if me and re.search(rf"return {re.escape(me)} to its owner's hand", low):
        prof.self_bounce = True
    if re.search(r"(an|target) opponent gains control of", low):
        prof.control_loss = True
    if re.search(r"whenever you tap [^.]* for mana, (target|each|an) opponent", low) or re.search(
        r"(target|an|each) opponent (creates|draws|adds|gains)", low
    ):
        prof.opp_gift = True
    if re.search(r"\bcycling\b", low):
        prof.utility += 1
    return prof


def generic_share(pips: dict | None) -> float:
    """Fraction of the deck's mana symbols that any mana can pay."""
    pips = pips or {}
    generic = float(pips.get("generic") or 0)
    colored = float(pips.get("colored") or 0)
    if generic + colored <= 0:
        return DEFAULT_GENERIC_SHARE
    return min(0.8, max(0.2, generic / (generic + colored)))


def color_shares(identity: list[str], pips: dict | None = None) -> dict[str, float]:
    """Share of colored demand per identity color (equal split when no pips yet)."""
    colors = [c for c in identity if c in COLORS]
    if not colors:
        return {}
    pips = pips or {}
    total = sum(float(pips.get(c) or 0) for c in colors)
    if total <= 0:
        return {c: 1.0 / len(colors) for c in colors}
    return {c: float(pips.get(c) or 0) / total for c in colors}


def land_delta(
    info: dict,
    identity: list[str] | None,
    pips: dict | None = None,
) -> dict:
    """Value of ``info`` (a catalog row) over the best basic it would replace."""
    name = info.get("name") or ""
    type_line = info.get("type_line") or ""
    identity = [c for c in (identity or []) if c in COLORS]
    if is_basic(name, type_line):
        return {"delta": 0.0, "basic": True, "parts": {}, "profile": None}
    prof = parse_land(name, type_line, info.get("oracle_text") or "", identity)
    shares = color_shares(identity, pips)

    if shares:
        basic_cov = max(shares.values())
        cov = sum(s for c, s in shares.items() if c in prof.colors)
        cov += CONDITIONAL_WEIGHT * sum(
            s for c, s in shares.items() if c in prof.weak_colors and c not in prof.colors
        )
    else:  # colorless commander: a colorless source is a basic (Wastes)
        basic_cov = 1.0
        cov = 1.0 if (prof.colorless or prof.colors) else 0.0
    # A land pays the generic part of demand (g) plus the colored share it
    # covers; a basic pays g plus its own color. Fixing is the difference.
    fixing = (1.0 - generic_share(pips)) * (cov - basic_cov) if shares else cov - basic_cov
    extra = EXTRA_MANA_VALUE * max(0, prof.amount - 1)
    utility = min(UTILITY_CAP, UTILITY_VALUE * prof.utility) + (FILTER_VALUE if prof.filters else 0.0)

    tapped = {
        "always": TAPPED_COST,
        "conditional": COND_TAPPED_COST,
        "shock": SHOCK_COST,
        "no": 0.0,
    }[prof.enters_tapped]
    if prof.is_fetch and prof.fetch_tapped:
        tapped = max(tapped, FETCH_TAPPED_COST)
    drawback = 0.0
    drawback += PAIN_COST * prof.pain
    drawback += SELF_BOUNCE_COST if prof.self_bounce else 0.0
    drawback += SAC_LAND_COST * prof.sac_lands
    drawback += BOUNCE_COST if prof.bounce else 0.0
    drawback += SELF_SAC_COST if prof.self_sac else 0.0
    drawback += CONTROL_LOSS_COST if prof.control_loss else 0.0
    drawback += OPP_GIFT_COST if prof.opp_gift else 0.0

    delta = fixing + extra + utility - tapped - drawback
    if not prof.produces_mana():
        delta = min(delta, -1.0)
    parts = {
        "fixing": round(fixing, 3),
        "extra_mana": round(extra, 3),
        "utility": round(utility, 3),
        "tapped": round(-tapped, 3),
        "drawback": round(-drawback, 3),
    }
    return {
        "delta": round(delta, 3),
        "basic": False,
        "parts": parts,
        "profile": asdict(prof) | {"colors": sorted(prof.colors), "weak_colors": sorted(prof.weak_colors)},
    }
