"""Script do Forge → operadores com magnitude, frequência e condição (Fase 2).

Cada linha de habilidade (A:, T:, S:, R:, K:) vira um `Operator`; as SVars
encadeadas (SubAbility$, Execute$) viram a sequência de passos do operador.
Os parâmetros do Forge são separados em grupos com papéis diferentes na
comparação entre cartas:

  magnitude  números do efeito (NumDmg$ 3, NumCards$ 2) — ordenáveis
  target     escopo do alvo (ValidTgts$ Any ⊇ Creature ⊇ Creature.nonBlack)
  condition  restrições (Condition*, IsPresent$, ActivationLimit$, ...) — menos é melhor
  shape      todo o resto que muda o que a habilidade faz — tem de ser igual
  cosmetic   descrições, prompts, dicas de IA, contabilidade interna — ignorado

Um parâmetro desconhecido cai em `shape`: na dúvida, duas cartas deixam de ser
comparáveis em vez de uma "dominar" a outra por um detalhe não lido.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

COLORS = ("W", "U", "B", "R", "G")
CARD_TYPES = {
    "Artifact", "Battle", "Creature", "Enchantment", "Instant", "Kindred", "Land",
    "Planeswalker", "Sorcery", "Tribal", "Dungeon",
}
SUPERTYPES = {"Basic", "Legendary", "Snow", "World", "Ongoing"}

MAGNITUDE_KEYS = {
    "NumDmg", "NumCards", "LifeAmount", "CounterNum", "NumAtt", "NumDef", "Amount",
    "TokenAmount", "ChangeNum", "DigNum", "ScryNum", "Num", "TargetMax", "CharmNum",
    "UnlessCost", "AddPower", "AddToughness", "Power", "Toughness", "SurveilNum",
}
TARGET_KEYS = {"ValidTgts"}
CONDITION_KEYS = {
    "Condition", "ConditionPresent", "ConditionDefined", "ConditionCheckSVar",
    "ConditionCompare", "ConditionSVarCompare", "ConditionZone", "ConditionPlayerTurn",
    "ConditionPhases", "ConditionLifeTotal", "ConditionLifeAmount", "ConditionTargetValidTargeting",
    "IsPresent", "PresentCompare", "PresentZone", "PresentPlayer", "CheckSVar",
    "SVarCompare", "CheckSecondSVar", "SecondSVarCompare", "ActivationLimit",
    "ActivationPhases", "SorcerySpeed", "PlayerTurn", "OpponentTurn", "ActivationZone",
    "ActivationCardsInHand", "GameActivationLimit", "Threshold", "Metalcraft",
    "Hellbent", "Delirium", "Revolt", "Ferocious", "Morbid", "Raid", "Spectacle",
    "IsPresent2", "PresentCompare2", "Planeswalker", "Ultimate", "OptionalDecider",
    "Optional", "UnlessPayer", "UnlessSwitched", "AnyPlayer", "RequiresMetalcraft",
    "AdditionalCost", "MayChooseTarget", "TargetMin",
}
COSMETIC_PREFIXES = ("AI", "Remember", "Forget", "Clear", "Imprint")
COSMETIC_KEYS = {
    "SpellDescription", "StackDescription", "TgtPrompt", "TriggerDescription",
    "Description", "ChoiceTitle", "SelectPrompt", "ValidTgtsDesc", "ChangeTypeDesc",
    "ValidDescription", "PrecostDesc", "CostDesc", "Secondary", "IsCurse", "SubAbility",
    "Execute", "Picture", "ShowCurrentCard", "TriggerZones", "SpellName", "StackDesc",
    "SVar", "InfoMessage", "PrecostDescription", "AlternateText", "Stackable",
    "IsPresentDesc", "MayPlayText", "Hidden", "NoReveal", "Reveal", "Shuffle",
    "ShuffleNonMandatory", "NoLooking", "Mandatory", "DividedAsYouChoose",
}

_PARAM_RE = re.compile(r"(\w+)\$\s*([^|]*)")
_API_RE = re.compile(r"^(AB|SP|DB)\$\s*([A-Za-z]+)")


def param_role(key: str) -> str:
    if key in MAGNITUDE_KEYS:
        return "magnitude"
    if key in TARGET_KEYS:
        return "target"
    if key in CONDITION_KEYS:
        return "condition"
    if key in COSMETIC_KEYS or key.startswith(COSMETIC_PREFIXES):
        return "cosmetic"
    return "shape"


def parse_params(body: str) -> dict[str, str]:
    return {key: value.strip() for key, value in _PARAM_RE.findall(body)}


@dataclass(frozen=True)
class ManaCost:
    generic: int = 0
    pips: tuple[tuple[str, int], ...] = ()  # sorted (color, count)
    other: tuple[str, ...] = ()  # hybrid, phyrexian, X, snow — compared as text
    raw: str = ""

    @property
    def mana_value(self) -> int:
        hybrid = sum(1 if "2" not in tok else 2 for tok in self.other if tok not in ("X",))
        return self.generic + sum(n for _, n in self.pips) + hybrid

    def pip(self, color: str) -> int:
        return dict(self.pips).get(color, 0)


def parse_mana_cost(raw: str) -> ManaCost:
    generic = 0
    pips: dict[str, int] = {}
    other: list[str] = []
    for token in (raw or "").split():
        token = token.strip()
        if not token or token == "no" or token == "cost":
            continue
        if token.isdigit():
            generic += int(token)
        elif token in COLORS:
            pips[token] = pips.get(token, 0) + 1
        elif token == "C":
            other.append("C")
        else:
            other.append(token)
    return ManaCost(generic, tuple(sorted(pips.items())), tuple(sorted(other)), raw or "")


@dataclass(frozen=True)
class Step:
    """One effect in an operator's chain (A: plus its SubAbility$ SVars)."""

    api: str
    magnitude: tuple[tuple[str, str], ...] = ()
    target: str = ""
    conditions: tuple[tuple[str, str], ...] = ()
    shape: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Operator:
    source: str  # spell | activated | trigger | static | replacement | keyword
    kind: str  # api name, trigger/static mode, replacement event, keyword name
    cost: ManaCost | None = None
    cost_other: tuple[str, ...] = ()  # non-mana cost parts, e.g. "T", "Sac<1/CARDNAME>"
    steps: tuple[Step, ...] = ()
    magnitude: tuple[tuple[str, str], ...] = ()  # for statics/triggers/keywords
    target: str = ""
    conditions: tuple[tuple[str, str], ...] = ()
    shape: tuple[tuple[str, str], ...] = ()
    frequency: str = "once"  # once | repeatable | per_turn | per_event | continuous
    speed: str = ""  # instant | sorcery | "" (not cast/activated)


@dataclass
class CardOps:
    name: str
    mana_cost: ManaCost
    types: frozenset[str]
    supertypes: frozenset[str]
    subtypes: frozenset[str]
    power: str = ""
    toughness: str = ""
    operators: list[Operator] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # generic atoms whose key params are missing
    file: str = ""
    faces: list["CardOps"] = field(default_factory=list)  # adventure, split, transform, MDFC
    svars: dict[str, str] = field(default_factory=dict)  # raw SVar bodies, for evaluating Count$ expressions

    @property
    def is_spell(self) -> bool:
        return bool(self.types & {"Instant", "Sorcery"})


def _split(params: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {
        "magnitude": [], "target": [], "condition": [], "shape": []
    }
    for key, value in params.items():
        role = param_role(key)
        if role != "cosmetic":
            groups[role].append((key, value))
    return groups


def _resolve_number(value: str, svars: dict[str, str]) -> str:
    """'+3' → '3'; 'X' with SVar 'X:Number$2' → '2'; '-X' keeps its sign; anything
    else stays symbolic as the SVar's *definition*, so two cards' X only compare
    equal when they count the same thing."""
    v = value.strip().lstrip("+")
    if re.fullmatch(r"-?\d+", v):
        return v
    sign = ""
    if v.startswith("-"):
        sign, v = "-", v[1:]
    body = svars.get(v, "").strip()
    m = re.fullmatch(r"Number\$(-?\d+)", body)
    if m:
        n = int(m.group(1))
        return str(-n if sign else n)
    return f"var:{sign}{body or v}"


class _Compiler:
    """Compiles one face's ability lines, dereferencing SVars by content."""

    def __init__(self, svars: dict[str, str]):
        self.svars = svars

    # -- SVar dereference -------------------------------------------------------
    def canon(self, value: str, seen: frozenset[str] = frozenset()) -> str:
        """Replace SVar names inside a parameter value by what they define.

        Two cards that point to differently named SVars with the same content
        compare equal; same name with different content (Clone's DBCopy) differ.
        """
        parts = []
        for part in re.split(r"([,:&])", value):
            name = part.strip()
            body = self.svars.get(name)
            if body is None or name in seen:
                parts.append(part)
                continue
            inner = seen | {name}
            if _API_RE.match(body):
                parts.append("{" + repr(self.steps(body, inner)) + "}")
            elif body.startswith(("Mode$", "Event$")):
                params = {k: self.canon(v, inner) for k, v in parse_params(body).items()
                          if param_role(k) != "cosmetic"}
                parts.append("{" + repr(sorted(params.items())) + "}")
            else:
                parts.append("{" + body.strip() + "}")
        return "".join(parts)

    def _groups(self, params: dict[str, str], seen: frozenset[str], drop_cost: bool = False,
                operator_level: bool = False) -> dict[str, tuple]:
        """`drop_cost` only for an A: line, whose Cost$ is compared on its own; a
        Cost$ anywhere else (inside a trigger's SVar, an alternative cost) is shape."""
        groups = _split(params)
        # Mode$/Event$ name the operator on T:/S:/R: lines; inside a step Mode$ is
        # meaningful (Discard Mode$ TgtChoose vs LookYouChoose).
        dropped = {"AB", "SP", "DB", "ReplaceWith"} | ({"Cost"} if drop_cost else set())
        if operator_level:
            dropped |= {"Mode", "Event"}
        return {
            "magnitude": tuple(sorted((k, _resolve_number(v, self.svars)) for k, v in groups["magnitude"])),
            "target": "|".join(v for _, v in groups["target"]),
            "condition": tuple(sorted((k, self.canon(v, seen)) for k, v in groups["condition"])),
            "shape": tuple(sorted(
                (k, self.canon(v, seen)) for k, v in groups["shape"] if k not in dropped
            )),
        }

    def steps(self, first_body: str, seen: frozenset[str] = frozenset(),
              drop_first_cost: bool = False) -> list[Step]:
        steps: list[Step] = []
        body = first_body
        visited = set(seen)
        while body:
            match = _API_RE.match(body)
            params = parse_params(body)
            api = match.group(2) if match else "?"
            if api in ("ChangeZone", "ChangeZoneAll"):
                api = f"{api}.{params.get('Origin', 'Any')}>{params.get('Destination', 'Any')}"
            g = self._groups(params, frozenset(visited), drop_cost=drop_first_cost and not steps)
            steps.append(Step(api, g["magnitude"], g["target"], g["condition"], g["shape"]))
            nxt = params.get("SubAbility", "")
            if not nxt or nxt in visited:
                break
            visited.add(nxt)
            body = self.svars.get(nxt, "")
        return steps


def _cost(cost: str) -> tuple[ManaCost, tuple[str, ...]]:
    mana_tokens, other = [], []
    for token in re.findall(r"[A-Za-z]+<[^>]*>|\S+", cost or ""):
        if re.fullmatch(r"\d+|[WUBRGCX]|[WUBRGC2]/?[WUBRGP]", token):
            mana_tokens.append(token)
        else:
            other.append(token)
    return parse_mana_cost(" ".join(mana_tokens)), tuple(sorted(other))


GENERIC_KEYS = {
    "Animate": ("Power", "Toughness", "Types", "Keywords", "Abilities"),
    "AnimateAll": ("Power", "Toughness", "Types", "Keywords"),
    "Play": ("Valid", "Defined", "ValidSA", "WithoutManaCost"),
    "ReplaceEffect": ("VarName",),
    "AlterAttribute": ("Attributes",),
}


def _compile_face(lines: list[str], file: str) -> CardOps:
    meta: dict[str, str] = {}
    svars: dict[str, str] = {}
    for line in lines:
        key, _, value = line.partition(":")
        if key == "SVar":
            name, _, body = value.partition(":")
            svars[name] = body
        elif key in ("Name", "ManaCost", "Types", "PT") and key not in meta:
            meta[key] = value.strip()
    comp = _Compiler(svars)
    words = meta.get("Types", "").split()
    types = frozenset(w for w in words if w in CARD_TYPES)
    card = CardOps(
        name=meta.get("Name", ""),
        mana_cost=parse_mana_cost(meta.get("ManaCost", "")),
        types=types,
        supertypes=frozenset(w for w in words if w in SUPERTYPES),
        subtypes=frozenset(w for w in words if w not in CARD_TYPES | SUPERTYPES),
        file=file,
        svars=dict(svars),
    )
    if "/" in meta.get("PT", ""):
        card.power, card.toughness = meta["PT"].split("/", 1)
    flash = False
    for line in lines:
        key, _, value = line.partition(":")
        value = value.strip()
        if key != "K":
            continue
        name, _, arg = value.partition(":")
        flash = flash or name == "Flash"
        card.operators.append(Operator(
            source="keyword", kind=name.strip(),
            shape=(("arg", comp.canon(arg.strip())),) if arg.strip() else (),
            frequency="continuous",
        ))
    speed_card = "instant" if ("Instant" in types or flash) else "sorcery"
    for line in lines:
        key, _, value = line.partition(":")
        value = value.strip()
        if key == "A":
            match = _API_RE.match(value)
            if not match:
                continue
            params = parse_params(value)
            is_spell = match.group(1) == "SP"
            cost, other = _cost(params.get("Cost", ""))
            steps = comp.steps(value, drop_first_cost=True)
            for step in steps:
                base = step.api.split(".")[0]
                if base in GENERIC_KEYS and not any(
                    k in GENERIC_KEYS[base] for k, _ in step.shape + step.magnitude
                ):
                    card.unresolved.append(f"api:{base}")
            sorcery = any(k in params for k in ("SorcerySpeed", "Planeswalker"))
            card.operators.append(Operator(
                source="spell" if is_spell else "activated",
                kind=steps[0].api if steps else match.group(2),
                cost=None if is_spell else cost,
                # A spell's Cost$ holds additional costs (sacrifice a creature, ...).
                cost_other=other,
                steps=tuple(steps),
                frequency="once" if is_spell else (
                    "per_turn" if "ActivationLimit" in params or "Planeswalker" in params
                    else "repeatable"
                ),
                speed=speed_card if is_spell else ("sorcery" if sorcery else "instant"),
            ))
        elif key == "T":
            params = parse_params(value)
            g = comp._groups(params, frozenset(), operator_level=True)
            execute = params.get("Execute", "")
            card.operators.append(Operator(
                source="trigger", kind=params.get("Mode", "?"),
                steps=tuple(comp.steps(svars.get(execute, ""), frozenset({execute}))),
                magnitude=g["magnitude"],
                conditions=g["condition"],
                shape=g["shape"],
                frequency="per_event",
            ))
        elif key == "S":
            params = parse_params(value)
            g = comp._groups(params, frozenset(), operator_level=True)
            card.operators.append(Operator(
                source="static", kind=params.get("Mode", "?"),
                magnitude=g["magnitude"],
                conditions=g["condition"],
                shape=g["shape"],
                frequency="continuous",
            ))
            if "MayPlay" in params and not params.get("Affected"):
                card.unresolved.append("static:Continuous.MayPlay")
        elif key == "R":
            params = parse_params(value)
            g = comp._groups(params, frozenset(), operator_level=True)
            replace_with = params.get("ReplaceWith", "")
            body = svars.get(replace_with, "")
            steps = comp.steps(body, frozenset({replace_with})) if _API_RE.match(body) else []
            if params.get("Event") == "Moved" and not replace_with:
                card.unresolved.append("replacement:Moved")
            card.operators.append(Operator(
                source="replacement", kind=params.get("Event", "?"),
                steps=tuple(steps),
                conditions=g["condition"],
                shape=g["shape"] + ((("ReplaceWith", body.strip()),) if body and not steps else ()),
                frequency="continuous",
            ))
    return card


def compile_script(text: str, file: str = "") -> CardOps:
    """One Forge card script → CardOps of the front face, other faces attached."""
    faces: list[list[str]] = [[]]
    for raw in text.splitlines():
        if raw.startswith("ALTERNATE"):
            faces.append([])
            continue
        faces[-1].append(raw.rstrip())
    card = _compile_face(faces[0], file)
    card.faces = [_compile_face(face, file) for face in faces[1:] if face]
    card.unresolved += [a for face in card.faces for a in face.unresolved]
    return card


def compile_file(path: Path, root: Path | None = None) -> CardOps:
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    rel = str(path.relative_to(root)) if root else str(path)
    return compile_script(text, rel)
