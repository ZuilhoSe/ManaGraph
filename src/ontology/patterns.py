"""Tier 2: deterministic Oracle-text templates for ontology predicates.

Oracle is templated, not free prose. These patterns cover high-precision
WotC frames. They do not invent predicates, do not treat extra turn as extra
combat, and do not flood `emits(etb)` onto every "when this enters".
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from deck_analysis.mana_symbols import ADD_ANY_RE, ADD_BRACES_RE, COLORS, SYMBOL_RE, WORD_NUM

_TREASURE_TOKEN = re.compile(r"(?i)\bcreates?\s+[^.]*?\btreasure\b")

PATTERN_VERSION = "1.0.0"
SOURCE_TIER1 = "tier1"
SOURCE_TIER2 = "tier2"

_REMINDER = re.compile(r"\([^)]*\)")
_EXTRA_TURN = re.compile(
    r"(?i)\b(?:an?\s+)?(?:extra|additional|another)\s+turns?\b"
)
_EXTRA_COMBAT = re.compile(
    r"(?i)(?:an?\s+)?(?:additional|another|extra)\s+combat\s+(?:phase|step)s?"
)
_SAC_OUTLET = re.compile(
    r"(?i)sacrifice (?:a|an|another) (creature|artifact|enchantment|permanent)s?\s*:"
)
_SELF_SAC = re.compile(
    r"(?i)sacrifice (?:this (?:creature|artifact|enchantment|permanent|land)|THIS)\s*:"
)
_SAC_SPELL_COST = re.compile(
    r"(?i)as an additional cost to cast this spell,\s*sacrifice (?:a|an|another) "
    r"(creature|artifact|enchantment|permanent)"
)
_CREATE_TOKEN = re.compile(r"(?i)\bcreates?\s+[^.]*?\btokens?\b")
_REWARDS_ETB = re.compile(
    r"(?i)whenever (?:a|another|one or more) "
    r"(?:creature|permanent|artifact|enchantment)s? (?:you control )?enters"
)
_REWARDS_TOKEN = re.compile(
    r"(?i)whenever (?:you create (?:a|an|one or more) |one or more tokens?\b|"
    r"a token (?:you control )?(?:enters|is created))"
)
_REWARDS_ATTACK = re.compile(
    r"(?i)whenever (?:this creature|THIS|equipped creature|enchanted creature|"
    r"a creature you control|you) attacks\b"
)
_REWARDS_COMBAT_DMG = re.compile(
    r"(?i)whenever (?:this creature|THIS|equipped creature|enchanted creature|"
    r"a creature you control) deals combat damage"
)
_REWARDS_DEATH = re.compile(
    r"(?i)whenever (?:a|another) creature (?:you control )?dies\b"
)
_REWARDS_SAC = re.compile(
    r"(?i)whenever you sacrifice (?:a|another) (?:creature|permanent|artifact)"
)
_REWARDS_DISCARD = re.compile(r"(?i)whenever (?:you|a player) discards?\b")
_LANDFALL = re.compile(
    r"(?i)\blandfall\b|whenever a land (?:you control )?enters"
)
_DRAW = re.compile(
    r"(?i)\bdraws? (?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+) cards?\b"
)
_DRAW_SKIP = re.compile(
    r"(?i)would draw|instead of drawing|don't draw|do not draw|skip (?:your |the )?draw"
)
_GAIN_LIFE = re.compile(r"(?i)(?:you )?gains? (?:a|an|one|two|\d+) life\b")
_MILL = re.compile(r"(?i)\bmills? (?:a|an|one|two|\d+) (creature )?cards?\b")
_P1P1 = re.compile(r"(?i)(?:put|place)s? (?:a|an|one|two|\d+) \+1/\+1 counters?")
_COUNTER_SPELL = re.compile(r"(?i)counter target (?:instant or sorcery )?spell")
_DESTROY_ALL = re.compile(
    r"(?i)(?:destroy|exile) all (creatures|permanents|artifacts|enchantments)"
)
_DESTROY_TARGET = re.compile(
    r"(?i)(?:destroy|exile) target ((?:artifact or enchantment)|creature|artifact|enchantment)s?"
)
_GY_HATE = re.compile(
    r"(?i)exile target (?:card|creature card) from (?:a |your |an )?graveyard"
)
_BOUNCE = re.compile(
    r"(?i)return target (?:creature |nontoken |nonland )?permanents? to "
    r"(?:its|their) owner's hand"
    r"|return target creatures? to (?:its|their) owner's hand"
)
_TUTOR = re.compile(
    r"(?i)search your library for (?:up to (?:two|three|four|\w+) )?(?:a |an )?"
    r"(basic land|land|creature|artifact|enchantment|instant|sorcery|card)s?"
)
_REANIMATE = re.compile(
    r"(?i)(?:return|put) target (?:creature )?cards? from "
    r"(?:a |your |an opponent's )?graveyard (?:to|onto) "
    r"(the battlefield|your hand|its owner's hand)"
)
_COST_RED = re.compile(
    r"(?i)(?:spells? you cast|this spell) costs? \{[^}]+\}\s*less"
)
_UNTAP = re.compile(
    r"(?i)untap (?:target|another|all) (?:other )?(?:creatures?|permanents?|lands?|artifacts?)"
)
_DISCARD_COST = re.compile(r"(?i)discard (?:a|an|one) cards?\s*:")
_PAY_LIFE = re.compile(r"(?i)pay (?:a|an|one|\d+) life\s*:")
_REQUIRE = re.compile(r"(?i)\b(delirium|threshold|hellbent|metalcraft)\b")
_PROTECT_WORDS = r"(?:hexproof|indestructible|shroud|ward|protection from)"
_EQUIPPED_PROTECT = re.compile(
    rf"(?i)(?:equipped|enchanted) creature (?:has|gains|gets) [^.]*?{_PROTECT_WORDS}"
)
_PREVENT_EQUIPPED = re.compile(
    r"(?i)prevent all (?:combat )?damage that would be dealt to "
    r"(?:equipped|enchanted) creature"
)
_BOARD_PROTECT = re.compile(
    rf"(?i)creatures you control (?:have|gain|get) [^.]*?{_PROTECT_WORDS}"
)
_HASTE_GRANT = re.compile(
    r"(?i)(?:equipped|enchanted) creature (?:has|gains|gets) [^.]*?\bhaste\b"
    r"|creatures you control (?:have|gain|get) [^.]*?\bhaste\b"
    r"|target creature [^.]*?\bgains haste\b"
)
_KEYWORD_GRANT = re.compile(
    r"(?i)(?:equipped|enchanted) creature (?:has|gains|gets) [^.]*?\b"
    r"(?:flying|trample|menace|double strike|first strike|deathtouch|lifelink|reach)\b"
)
_TUTOR_SELECTOR = {
    "basic land": "land",
    "land": "land",
    "creature": "creature",
    "artifact": "artifact",
    "enchantment": "enchantment",
    "instant": "instant",
    "sorcery": "sorcery",
    "card": "any",
}
_KEYWORD_ENABLES = {
    "convoke": ("enables", "capability", "convoke_like"),
    "delve": ("enables", "capability", "convoke_like"),
    "improvise": ("enables", "capability", "convoke_like"),
    "affinity": ("enables", "capability", "cost_reduction"),
    "cascade": ("enables", "capability", "cost_reduction"),
}


def _keyword_list(keywords: Any) -> list[str]:
    raw = keywords
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return [part.strip().lower() for part in text.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip().lower() for item in raw if str(item).strip()]
    return []


def _normalize_oracle(oracle_text: str, name: str = "") -> str:
    text = _REMINDER.sub(" ", oracle_text or "")
    text = text.replace("~", "THIS").replace("CARDNAME", "THIS")
    tokens: list[str] = []
    cleaned = name.strip()
    if cleaned:
        tokens.append(cleaned)
        for face in cleaned.split("//"):
            face = face.strip()
            if face and face.casefold() != cleaned.casefold():
                tokens.append(face)
    for token in sorted(set(tokens), key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(token)}\b", "THIS", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _candidate(
    predicate: str,
    arguments: dict[str, Any],
    pattern_id: str,
    *,
    source: str = SOURCE_TIER2,
    span: str = "",
) -> dict[str, Any]:
    evidence: dict[str, str] = {"pattern": pattern_id}
    if span:
        evidence["span"] = span[:180]
    return {
        "kind": "predicate",
        "predicate": predicate,
        "arguments": arguments,
        "mapping_id": f"oracle:{pattern_id}",
        "validation_only": False,
        "evidence": evidence,
        "source": source,
    }


def _unique(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    out: list[dict[str, Any]] = []
    for item in candidates:
        predicate = str(item.get("predicate") or "").strip()
        if not predicate:
            continue
        arguments = item.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            continue
        key = (
            predicate,
            tuple(sorted((str(k), str(v)) for k, v in arguments.items())),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(item))
    return out


def _object_for_sac(kind: str) -> str:
    lowered = kind.lower()
    if lowered.startswith("creature"):
        return "creature"
    if lowered.startswith("artifact"):
        return "artifact_permanent"
    if lowered.startswith("enchantment"):
        return "enchantment_permanent"
    return "creature"


def _mana_candidates(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for match in ADD_BRACES_RE.finditer(text):
        symbols = [token.upper() for token in SYMBOL_RE.findall(match.group(1))]
        colors = [token.lower() for token in symbols if token in COLORS or token == "C"]
        arguments: dict[str, Any] = {"object": "mana"}
        if len(set(colors)) == 1:
            arguments["color"] = colors[0]
        if colors:
            arguments["rate"] = len(colors)
        hits.append(
            _candidate("produces", arguments, "add_braces", span=match.group(0))
        )
    for match in ADD_ANY_RE.finditer(text):
        qty = WORD_NUM.get(match.group(1).lower(), 1)
        if str(match.group(1)).isdigit():
            qty = int(match.group(1))
        hits.append(
            _candidate(
                "produces",
                {"object": "mana", "color": "any", "rate": qty},
                "add_any",
                span=match.group(0),
            )
        )
    return hits


def _keyword_candidates(keywords: Any) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for keyword in _keyword_list(keywords):
        mapped = _KEYWORD_ENABLES.get(keyword)
        if mapped is None:
            continue
        predicate, arg_key, arg_value = mapped
        hits.append(
            _candidate(
                predicate,
                {arg_key: arg_value},
                f"keyword_{keyword}",
                source=SOURCE_TIER1,
                span=keyword,
            )
        )
    return hits


def extract_oracle_predicates(
    oracle_text: str,
    *,
    name: str = "",
    type_line: str = "",
    keywords: Any = None,
) -> list[dict[str, Any]]:
    """Return Forge-shaped candidates from Oracle text (and Scryfall keywords)."""
    _ = type_line
    hits: list[dict[str, Any]] = _keyword_candidates(keywords)
    text = _normalize_oracle(oracle_text, name)
    if not text:
        return _unique(hits)

    hits.extend(_mana_candidates(text))

    if _TREASURE_TOKEN.search(text):
        hits.append(_candidate("produces", {"object": "treasure"}, "treasure"))
        hits.append(_candidate("produces", {"object": "token"}, "treasure_token"))
        hits.append(_candidate("emits", {"event": "token_created"}, "treasure_token"))
        hits.append(_candidate("emits", {"event": "etb"}, "treasure_token"))
    elif _CREATE_TOKEN.search(text):
        hits.append(_candidate("produces", {"object": "token"}, "create_token"))
        hits.append(_candidate("emits", {"event": "token_created"}, "create_token"))
        hits.append(_candidate("emits", {"event": "etb"}, "create_token"))

    if _EXTRA_COMBAT.search(text):
        hits.append(
            _candidate("enables", {"capability": "extra_combat"}, "extra_combat")
        )
    elif _EXTRA_TURN.search(text):
        pass

    if _SELF_SAC.search(text):
        pass
    elif _SAC_OUTLET.search(text):
        kind = _SAC_OUTLET.search(text).group(1)
        hits.append(_candidate("enables", {"capability": "sac_outlet"}, "sac_outlet"))
        hits.append(
            _candidate("consumes", {"object": _object_for_sac(kind)}, "sac_outlet")
        )
        hits.append(_candidate("emits", {"event": "sacrifice"}, "sac_outlet"))

    extra_cost = _SAC_SPELL_COST.search(text)
    if extra_cost:
        hits.append(
            _candidate(
                "consumes",
                {"object": _object_for_sac(extra_cost.group(1))},
                "spell_sac_cost",
            )
        )
        hits.append(_candidate("emits", {"event": "sacrifice"}, "spell_sac_cost"))

    if _REWARDS_ETB.search(text):
        hits.append(_candidate("rewards", {"event": "etb"}, "rewards_etb"))
    if _REWARDS_TOKEN.search(text):
        hits.append(_candidate("rewards", {"event": "token_created"}, "rewards_token"))
    if _REWARDS_ATTACK.search(text):
        hits.append(_candidate("rewards", {"event": "attack"}, "rewards_attack"))
    if _REWARDS_COMBAT_DMG.search(text):
        hits.append(
            _candidate(
                "rewards", {"event": "deal_combat_damage"}, "rewards_combat_damage"
            )
        )
    if _REWARDS_DEATH.search(text):
        hits.append(_candidate("rewards", {"event": "death"}, "rewards_death"))
    if _REWARDS_SAC.search(text):
        hits.append(_candidate("rewards", {"event": "sacrifice"}, "rewards_sacrifice"))
    if _REWARDS_DISCARD.search(text):
        hits.append(_candidate("rewards", {"event": "discard"}, "rewards_discard"))
    if _LANDFALL.search(text):
        hits.append(_candidate("rewards", {"event": "landfall"}, "landfall"))

    if _DRAW.search(text) and not _DRAW_SKIP.search(text):
        hits.append(_candidate("produces", {"object": "card_in_hand"}, "draw"))
        hits.append(_candidate("emits", {"event": "draw"}, "draw"))

    if _GAIN_LIFE.search(text):
        hits.append(_candidate("produces", {"object": "life"}, "gain_life"))
        hits.append(_candidate("emits", {"event": "lifegain"}, "gain_life"))

    mill = _MILL.search(text)
    if mill:
        if mill.group(1):
            hits.append(
                _candidate(
                    "produces", {"object": "creature_in_graveyard"}, "mill_creature"
                )
            )
        else:
            hits.append(
                _candidate("produces", {"object": "card_in_graveyard"}, "mill")
            )

    if _P1P1.search(text):
        hits.append(_candidate("produces", {"object": "p1p1_counter"}, "p1p1"))
        hits.append(_candidate("emits", {"event": "counter_placed"}, "p1p1"))

    if _COUNTER_SPELL.search(text):
        hits.append(_candidate("answers", {"threat_class": "stack"}, "counterspell"))

    destroy_all = _DESTROY_ALL.search(text)
    if destroy_all:
        kind = destroy_all.group(1).lower()
        threat = "board" if kind in {"creatures", "permanents"} else kind.rstrip("s")
        if threat == "artifact":
            threat = "artifact"
        elif threat == "enchantment":
            threat = "enchantment"
        hits.append(_candidate("answers", {"threat_class": threat}, "destroy_all"))

    for match in _DESTROY_TARGET.finditer(text):
        kind = match.group(1).lower()
        if kind == "artifact or enchantment":
            hits.append(
                _candidate("answers", {"threat_class": "artifact"}, "destroy_target")
            )
            hits.append(
                _candidate(
                    "answers", {"threat_class": "enchantment"}, "destroy_target"
                )
            )
        else:
            hits.append(
                _candidate("answers", {"threat_class": kind}, "destroy_target")
            )

    if _GY_HATE.search(text):
        hits.append(_candidate("answers", {"threat_class": "graveyard"}, "gy_hate"))

    if _BOUNCE.search(text):
        hits.append(_candidate("emits", {"event": "bounce"}, "bounce"))
        hits.append(_candidate("answers", {"threat_class": "creature"}, "bounce"))

    tutor = _TUTOR.search(text)
    if tutor:
        selector = _TUTOR_SELECTOR.get(tutor.group(1).lower(), "any")
        hits.append(_candidate("tutors", {"selector": selector}, "tutor"))

    reanimate = _REANIMATE.search(text)
    if reanimate:
        destination = reanimate.group(1).lower()
        if "battlefield" in destination:
            hits.append(
                _candidate(
                    "recurs",
                    {"zone_from": "graveyard", "zone_to": "battlefield"},
                    "reanimate",
                )
            )
            hits.append(_candidate("emits", {"event": "etb"}, "reanimate"))
        else:
            hits.append(
                _candidate(
                    "recurs",
                    {"zone_from": "graveyard", "zone_to": "hand"},
                    "regrowth",
                )
            )

    if _COST_RED.search(text):
        hits.append(
            _candidate("enables", {"capability": "cost_reduction"}, "cost_reduction")
        )
    if _UNTAP.search(text):
        hits.append(_candidate("enables", {"capability": "untapper"}, "untapper"))
        hits.append(_candidate("emits", {"event": "untap"}, "untapper"))
    if _DISCARD_COST.search(text):
        hits.append(
            _candidate("consumes", {"object": "card_in_hand"}, "discard_cost")
        )
        hits.append(_candidate("emits", {"event": "discard"}, "discard_cost"))
    if _PAY_LIFE.search(text):
        hits.append(_candidate("consumes", {"object": "life"}, "pay_life"))

    require = _REQUIRE.search(text)
    if require:
        hits.append(
            _candidate(
                "requires",
                {"precondition": require.group(1).lower()},
                "ability_word",
            )
        )

    if _EQUIPPED_PROTECT.search(text) or _PREVENT_EQUIPPED.search(text):
        hits.append(
            _candidate("protects", {"target_class": "commander"}, "equip_protect")
        )
    if _BOARD_PROTECT.search(text):
        hits.append(_candidate("protects", {"target_class": "board"}, "board_protect"))
    if _HASTE_GRANT.search(text):
        hits.append(_candidate("enables", {"capability": "haste_grant"}, "haste_grant"))
    if _KEYWORD_GRANT.search(text):
        hits.append(
            _candidate("enables", {"capability": "keyword_grant"}, "keyword_grant")
        )

    return _unique(hits)


def extract_card_predicates(
    oracle_text: str,
    *,
    name: str = "",
    type_line: str = "",
    keywords: Any = None,
) -> list[dict[str, Any]]:
    """Public alias used by the predicate index rebuild."""
    return extract_oracle_predicates(
        oracle_text,
        name=name,
        type_line=type_line,
        keywords=keywords,
    )


def signatures(candidates: Iterable[Mapping[str, Any]]) -> set[tuple[str, str, str]]:
    """(predicate, arg_key, arg_value) triples, matching the SQLite index."""
    rows: set[tuple[str, str, str]] = set()
    for item in candidates:
        predicate = str(item.get("predicate") or "").strip()
        arguments = item.get("arguments") or {}
        if not predicate or not isinstance(arguments, Mapping) or not arguments:
            continue
        for arg_key, arg_value in arguments.items():
            value = str(arg_value).strip()
            if value:
                rows.add((predicate, str(arg_key), value))
    return rows
