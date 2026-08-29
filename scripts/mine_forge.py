#!/usr/bin/env python3
"""Mine a local Forge ``cardsfolder`` into an intermediate JSONL artifact.

Forge is intentionally an input-only bootstrap here.  This script never
downloads or executes Forge, and its output is not a runtime card label set.
Use ``--release`` and ``--commit`` to record the exact local corpus provenance.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ontology.schema import OntologySchema, load_schema  # noqa: E402


class ForgeMiningError(ValueError):
    """Raised for an invalid local cardsfolder or mapping contract."""


_FIELD_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_]*):(?:\s?)(?P<value>.*)$")
_DSL_RE = re.compile(r"^(?P<prefix>A|T|R|S|K|SVar):\s*(?P<value>.*)$")
_METADATA_KEYS = {
    "Name",
    "ManaCost",
    "Types",
    "PT",
    "Oracle",
    "DeckHas",
    "DeckNeeds",
    "DeckHints",
    "AI",
    "AlternateMode",
}
_EFFECT_PREFIXES = {"A", "R", "S", "K", "SVar"}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _normalise_reference(value: str) -> str:
    return re.sub(r"\s+", "", value.strip()).lower()


def _parse_params(value: str) -> dict[str, Any]:
    """Parse Forge's pipe-separated ``Key$Value`` segments without guessing."""
    params: dict[str, Any] = {}
    for segment in value.split("|"):
        segment = segment.strip()
        if "$" not in segment:
            continue
        key, item = segment.split("$", 1)
        key = key.strip()
        item = item.strip()
        if not key:
            continue
        if key in params:
            if not isinstance(params[key], list):
                params[key] = [params[key]]
            params[key].append(item)
        else:
            params[key] = item
    return params


def _param(record: Mapping[str, Any], key: str, default: str = "") -> str:
    value = record.get("params", {}).get(key, default)
    values = _as_list(value)
    return values[0] if values else default


def _number(value: str, default: int | str = 1) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value or default


_MANA_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")
_COLOURS = frozenset("WUBRG")
_CARD_TYPES = frozenset(
    {
        "Artifact",
        "Battle",
        "Conspiracy",
        "Creature",
        "Enchantment",
        "Instant",
        "Kindred",
        "Land",
        "Phenomenon",
        "Plane",
        "Planeswalker",
        "Scheme",
        "Sorcery",
        "Tribal",
        "Vanguard",
    }
)
_SUPERTYPES = frozenset({"Basic", "Legendary", "Ongoing", "Snow", "World"})


def _mana_facts(raw_cost: Any) -> dict[str, Any]:
    """Parse the structural part of a Forge ManaCost without guessing identity."""
    raw = _as_list(raw_cost)[0] if _as_list(raw_cost) else ""
    symbols = _MANA_SYMBOL_RE.findall(raw)
    if not symbols and raw:
        # Forge releases use both ``{2}{R}`` and ``2 R`` representations.
        symbols = raw.split()
    color_hints: list[str] = []
    colored_pips: dict[str, int] = {}
    generic_pips = 0
    has_variable = False

    for symbol in symbols:
        token = symbol.strip().upper()
        if token.isdigit():
            generic_pips += int(token)
        elif token == "X" or token.startswith("X"):
            has_variable = True
        for colour in _COLOURS:
            if colour in token:
                if colour not in color_hints:
                    color_hints.append(colour)
                if token == colour:
                    colored_pips[colour] = colored_pips.get(colour, 0) + 1

    return {
        "raw": raw,
        "symbols": symbols,
        "generic_pips": generic_pips,
        "colored_pips": colored_pips,
        "colors": sorted(color_hints, key="WUBRG".index),
        "has_variable_symbol": has_variable,
        # Forge's cost is not enough to determine Commander color identity.
        "color_identity": None,
        "color_identity_source": "scryfall_pending",
    }


def _type_facts(raw_types: Any) -> dict[str, Any]:
    """Split Forge's Types field while retaining the original type line."""
    raw = _as_list(raw_types)[0] if _as_list(raw_types) else ""
    parts = re.split(r"\s+[—-]\s+", raw, maxsplit=1)
    left = parts[0].strip()
    right = parts[1].strip() if len(parts) == 2 else ""
    words = left.split()
    supertypes = [word for word in words if word in _SUPERTYPES]
    card_types = [word for word in words if word in _CARD_TYPES]
    subtypes = right.split() if right else [
        word for word in words if word not in _SUPERTYPES and word not in _CARD_TYPES
    ]
    return {
        "raw": raw,
        "supertypes": supertypes,
        "card_types": card_types,
        "subtypes": subtypes,
    }


def _power_toughness_facts(raw_pt: Any) -> dict[str, Any]:
    raw = _as_list(raw_pt)[0] if _as_list(raw_pt) else ""
    if "/" not in raw:
        return {"raw": raw, "power": None, "toughness": None}
    power, toughness = (part.strip() for part in raw.split("/", 1))
    return {"raw": raw, "power": power or None, "toughness": toughness or None}


def _keyword_facts(effects: Iterable[Mapping[str, Any]]) -> list[str]:
    keywords: list[str] = []
    for effect in effects:
        if effect.get("kind") != "keyword":
            continue
        value = str(effect.get("value") or "").split("|", 1)[0].strip()
        if value and value not in keywords:
            keywords.append(value)
    return keywords


def _face_facts(
    metadata: Mapping[str, Any],
    effects: list[dict[str, Any]],
    triggers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return normalized Forge facts without promoting them to ontology labels."""
    by_kind: dict[str, int] = {}
    for record in effects + triggers:
        kind = str(record.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "source": "forge",
        "mana": _mana_facts(metadata.get("ManaCost")),
        "types": _type_facts(metadata.get("Types")),
        "power_toughness": _power_toughness_facts(metadata.get("PT")),
        "keywords": _keyword_facts(effects),
        "ability_kinds": sorted(by_kind),
        "ability_counts": by_kind,
        "effect_count": len(effects),
        "trigger_count": len(triggers),
    }


def _unique(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _record(prefix: str, value: str, line_number: int, face_index: int) -> dict[str, Any]:
    first_segment = value.split("|", 1)[0].strip()
    return {
        "raw": f"{prefix}: {value}".rstrip(),
        "value": value,
        "line": line_number,
        "face": face_index,
        "prefix": prefix,
        "kind": (
            "ability"
            if prefix == "A"
            else "trigger"
            if prefix == "T"
            else "replacement"
            if prefix == "R"
            else "static"
            if prefix == "S"
            else "keyword"
            if prefix == "K"
            else "svar"
        ),
        "ability": first_segment if prefix == "A" else "",
        "params": _parse_params(value),
    }


def _metadata_put(metadata: dict[str, Any], key: str, value: str) -> None:
    if key in {"DeckHas", "DeckNeeds", "DeckHints", "AI"}:
        metadata.setdefault(key, []).append(value)
        return
    if key == "Oracle" and key in metadata and metadata[key]:
        metadata[key] = f"{metadata[key]}\n{value}"
        return
    # Repeated metadata is retained rather than silently overwritten.
    if key in metadata and metadata[key] != value:
        old = metadata[key]
        metadata[key] = _as_list(old) + [value]
        return
    metadata[key] = value


def _parse_face(lines: list[str], face_index: int, is_alternate: bool) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    effects: list[dict[str, Any]] = []
    triggers: list[dict[str, Any]] = []
    current_key: str | None = None

    for line_number, raw_line in enumerate(lines, 1):
        raw_line = raw_line.rstrip("\r\n")
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        dsl_match = _DSL_RE.match(raw_line)
        field_match = _FIELD_RE.match(raw_line)
        if dsl_match:
            prefix = dsl_match.group("prefix")
            record = _record(prefix, dsl_match.group("value"), line_number, face_index)
            if prefix == "T":
                triggers.append(record)
            elif prefix in _EFFECT_PREFIXES:
                effects.append(record)
            current_key = None
            continue
        if field_match and field_match.group("key") in _METADATA_KEYS:
            current_key = field_match.group("key")
            _metadata_put(metadata, current_key, field_match.group("value"))
            continue
        # Some local exports wrap Oracle text onto indented continuation lines.
        # Never treat an unindented unknown line as Oracle: it may be a DSL
        # extension from a newer Forge release.
        if raw_line[:1].isspace() and current_key == "Oracle":
            metadata["Oracle"] = f"{metadata.get('Oracle', '')}\n{stripped}".strip()
            continue
        if field_match:
            # Forge releases add metadata over time. Keep unknown fields in
            # the raw row so a later mapping can inspect them without making
            # this parser invent their semantics.
            _metadata_put(
                metadata, field_match.group("key"), field_match.group("value")
            )
            current_key = field_match.group("key")
            continue
        current_key = None

    costs: list[dict[str, Any]] = []
    for effect in effects:
        for cost in _as_list(effect.get("params", {}).get("Cost")):
            costs.append(
                {
                    "raw": f"Cost$ {cost}".rstrip(),
                    "value": cost,
                    "line": effect["line"],
                    "face": face_index,
                    "parent_effect_line": effect["line"],
                }
            )

    facts = _face_facts(metadata, effects, triggers)
    face = {
        "index": face_index,
        "is_alternate": is_alternate,
        "name": metadata.get("Name", ""),
        "oracle": metadata.get("Oracle", ""),
        "metadata": metadata,
        "facts": facts,
        "effects": effects,
        "costs": costs,
        "triggers": triggers,
        "subability_chain": [],
        "warnings": [],
    }
    _follow_subabilities(face)
    return face


def _follow_subabilities(face: dict[str, Any]) -> None:
    """Mark the transitive SubAbility closure while preserving raw DSL lines."""
    effects = face["effects"]
    by_reference: dict[str, list[dict[str, Any]]] = {}
    for effect in effects:
        if effect["prefix"] != "A":
            continue
        reference = _normalise_reference(effect["ability"])
        if reference:
            by_reference.setdefault(reference, []).append(effect)

    chains: list[dict[str, Any]] = []
    reachable: dict[int, int] = {effect["line"]: 0 for effect in effects}
    visiting: set[int] = set()

    def visit(effect: dict[str, Any], depth: int) -> None:
        if effect["line"] in visiting:
            face["warnings"].append(
                f"SubAbility cycle at line {effect['line']}; traversal stopped"
            )
            return
        visiting.add(effect["line"])
        for reference in _as_list(effect.get("params", {}).get("SubAbility")):
            candidates = by_reference.get(_normalise_reference(reference), [])
            if not candidates:
                chains.append(
                    {
                        "from_line": effect["line"],
                        "reference": reference,
                        "resolved": False,
                        "depth": depth + 1,
                    }
                )
                continue
            if len(candidates) > 1:
                face["warnings"].append(
                    f"ambiguous SubAbility {reference!r} at line {effect['line']}"
                )
            child = sorted(candidates, key=lambda item: item["line"])[0]
            reachable[child["line"]] = min(
                reachable.get(child["line"], depth + 1), depth + 1
            )
            child["reachable_via_subability"] = True
            child["subability_root_line"] = effect["line"]
            chains.append(
                {
                    "from_line": effect["line"],
                    "to_line": child["line"],
                    "reference": reference,
                    "resolved": True,
                    "depth": depth + 1,
                }
            )
            visit(child, depth + 1)
        visiting.remove(effect["line"])

    for effect in effects:
        if effect["prefix"] == "A":
            visit(effect, 0)
    for effect in effects:
        effect["reachable"] = reachable.get(effect["line"], 0) >= 0
    face["subability_chain"] = chains


def _split_faces(lines: list[str]) -> list[tuple[list[str], bool]]:
    faces: list[tuple[list[str], bool]] = []
    current: list[str] = []
    alternate = False
    for line in lines:
        marker = line.strip().upper()
        if marker == "ALTERNATE" or marker.startswith("ALTERNATE:"):
            if current:
                faces.append((current, alternate))
            current = []
            alternate = True
        else:
            current.append(line)
    if current:
        faces.append((current, alternate))
    return faces or [([], False)]


def parse_forge_text(text: str, filename: str = "<memory>") -> dict[str, Any]:
    """Parse one Forge text file into a raw, face-preserving intermediate row."""
    faces = [
        _parse_face(face_lines, index, is_alternate)
        for index, (face_lines, is_alternate) in enumerate(_split_faces(text.splitlines()))
    ]
    front = faces[0]
    name = front["name"] or Path(filename).stem
    if not front["name"]:
        front["warnings"].append("missing Name field; filename stem used as join key")

    effects = [item for face in faces for item in face["effects"]]
    costs = [item for face in faces for item in face["costs"]]
    triggers = [item for face in faces for item in face["triggers"]]
    deck_has = [
        item
        for face in faces
        for item in _as_list(face["metadata"].get("DeckHas"))
    ]
    deck_needs = [
        item
        for face in faces
        for item in _as_list(face["metadata"].get("DeckNeeds"))
    ]
    deck_hints = [
        item
        for face in faces
        for item in _as_list(face["metadata"].get("DeckHints"))
    ]
    return {
        "source": "forge.cardsfolder",
        "release": {"tag": None, "commit": None},
        "card": {
            "name": name,
            "filename": filename,
            "oracle": front["oracle"],
            "facts": {
                "source": "forge",
                "front": front["facts"],
                "faces": [face["facts"] for face in faces],
            },
            "faces": [
                {
                    "index": face["index"],
                    "name": face["name"],
                    "is_alternate": face["is_alternate"],
                    "oracle": face["oracle"],
                    "metadata": face["metadata"],
                    "facts": face["facts"],
                }
                for face in faces
            ],
            "source_files": [filename],
        },
        "effects": effects,
        "costs": costs,
        "triggers": triggers,
        "deck_has": deck_has,
        "deck_needs": deck_needs,
        "deck_hints": deck_hints,
        "subabilities": [
            chain
            for face in faces
            for chain in face["subability_chain"]
        ],
        "candidates": [],
        "warnings": [
            warning
            for face in faces
            for warning in face["warnings"]
        ],
    }


def _load_mapping(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ForgeMiningError(f"cannot load Forge mapping {path}: {exc}") from exc
    if not isinstance(data, Mapping) or not isinstance(data.get("mappings"), list):
        raise ForgeMiningError("Forge mapping must contain a mappings list")
    result: list[dict[str, Any]] = []
    for index, rule in enumerate(data["mappings"]):
        if not isinstance(rule, Mapping):
            raise ForgeMiningError(f"mapping {index} must be a mapping")
        required = ("id", "input", "match", "action")
        if any(not isinstance(rule.get(key), str) or not rule[key] for key in required):
            raise ForgeMiningError(f"mapping {index} requires id/input/match/action")
        try:
            re.compile(rule["match"], re.IGNORECASE)
        except re.error as exc:
            raise ForgeMiningError(f"invalid regex in mapping {rule['id']}: {exc}") from exc
        result.append(dict(rule))
    return result


def _threat_classes(record: Mapping[str, Any]) -> list[str]:
    """Read ValidTgts / ValidCards / Defined into existing threat_class values."""
    params = record.get("params") if isinstance(record, Mapping) else {}
    if not isinstance(params, Mapping):
        params = {}
    raw = " ".join(
        _as_list(params.get("ValidTgts"))
        + _as_list(params.get("ValidCards"))
        + _as_list(params.get("Defined"))
    ).lower()
    classes: list[str] = []
    if "creature" in raw:
        classes.append("creature")
    if "artifact" in raw:
        classes.append("artifact")
    if "enchantment" in raw:
        classes.append("enchantment")
    return classes


def _defined_self(record: Mapping[str, Any]) -> bool:
    params = record.get("params") if isinstance(record, Mapping) else {}
    if not isinstance(params, Mapping):
        return False
    defined = " ".join(_as_list(params.get("Defined"))).lower()
    return "self" in defined or "cardname" in defined


def _candidate(
    predicate: str | None,
    arguments: dict[str, Any],
    rule: Mapping[str, Any],
    evidence: Mapping[str, Any] | str,
    *,
    validation_only: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "validation" if validation_only else "predicate",
        "predicate": predicate,
        "arguments": arguments,
        "mapping_id": rule["id"],
        "validation_only": validation_only,
        "evidence": evidence,
    }


def _action_candidates(
    action: str,
    rule: Mapping[str, Any],
    record: Mapping[str, Any] | str,
) -> list[dict[str, Any]]:
    if isinstance(record, str):
        raw = record
        params: dict[str, Any] = {}
        line_record: Mapping[str, Any] = {"raw": raw}
    else:
        raw = str(record.get("raw", ""))
        params = dict(record.get("params", {}))
        line_record = record
    evidence = line_record
    get = lambda key, default="": _as_list(params.get(key, default))[0]
    if action == "mana":
        produced = get("Produced", "any")
        return [
            _candidate(
                "produces",
                {
                    "object": "mana",
                    "color": produced if produced.lower() != "any" else "any",
                    "rate": _number(get("Amount", get("NumMana", "1"))),
                    "cost": get("Cost") or None,
                },
                rule,
                evidence,
            )
        ]
    if action == "token":
        script = get("TokenScript")
        token_type = get("TokenType", get("Types"))
        object_name = "treasure" if "treasure" in script.lower() else "token"
        arguments: dict[str, Any] = {"object": object_name}
        if script:
            arguments["token_script"] = script
        if token_type:
            arguments["subtype"] = token_type
        candidates = [
            _candidate("produces", arguments, rule, evidence),
            _candidate("emits", {"event": "etb"}, rule, evidence),
            _candidate("emits", {"event": "token_created"}, rule, evidence),
        ]
        return candidates
    if action == "draw":
        return [
            _candidate(
                "produces",
                {"object": "card_in_hand", "rate": _number(get("NumCards", "1"))},
                rule,
                evidence,
            )
        ]
    if action == "change_zone":
        origin = get("Origin").lower()
        destination = get("Destination").lower()
        change_type = get("ChangeType").lower()
        candidates: list[dict[str, Any]] = []
        if destination == "battlefield":
            candidates.append(_candidate("emits", {"event": "etb"}, rule, evidence))
        if origin == "graveyard" and destination == "battlefield":
            candidates.append(
                _candidate("recurs", {"zone_from": "graveyard", "zone_to": "battlefield"}, rule, evidence)
            )
        if origin == "graveyard" and destination == "hand":
            candidates.append(
                _candidate("recurs", {"zone_from": "graveyard", "zone_to": "hand"}, rule, evidence)
            )
        if origin == "library" and change_type in {"creature", "land"}:
            candidates.append(
                _candidate("tutors", {"selector": change_type}, rule, evidence)
            )
        if origin == "graveyard" and destination == "exile":
            candidates.append(
                _candidate("answers", {"threat_class": "graveyard"}, rule, evidence)
            )
        return candidates
    if action == "sacrifice":
        return [
            _candidate("emits", {"event": "sacrifice"}, rule, evidence),
            _candidate("emits", {"event": "death"}, rule, evidence),
        ]
    if action == "sacrifice_cost":
        return [
            _candidate("consumes", {"object": "creature"}, rule, evidence),
            _candidate("enables", {"capability": "sac_outlet"}, rule, evidence),
            _candidate("emits", {"event": "sacrifice"}, rule, evidence),
        ]
    if action == "sacrifice_cost_artifact":
        return [
            _candidate("consumes", {"object": "artifact_permanent"}, rule, evidence),
            _candidate("enables", {"capability": "sac_outlet"}, rule, evidence),
            _candidate("emits", {"event": "sacrifice"}, rule, evidence),
        ]
    if action == "discard_cost":
        return [
            _candidate("consumes", {"object": "card_in_hand"}, rule, evidence),
            _candidate("emits", {"event": "discard"}, rule, evidence),
        ]
    if action == "exile_graveyard_cost":
        return [
            _candidate("consumes", {"object": "card_in_graveyard"}, rule, evidence)
        ]
    if action == "ignore_self_sacrifice":
        return []
    if action == "destroy":
        if _defined_self(line_record):
            return [_candidate("emits", {"event": "death"}, rule, evidence)]
        return [
            _candidate("answers", {"threat_class": threat}, rule, evidence)
            for threat in _threat_classes(line_record)
        ]
    if action == "destroy_all":
        return [
            _candidate("answers", {"threat_class": "board"}, rule, evidence),
            _candidate("emits", {"event": "death"}, rule, evidence),
        ]
    if action == "counter_spell":
        return [_candidate("answers", {"threat_class": "stack"}, rule, evidence)]
    if action == "gain_life":
        return [
            _candidate(
                "produces",
                {"object": "life", "rate": _number(get("LifeAmount", get("Life", "1")))},
                rule,
                evidence,
            ),
            _candidate("emits", {"event": "lifegain"}, rule, evidence),
        ]
    if action == "lose_life":
        return [_candidate("consumes", {"object": "life"}, rule, evidence)]
    if action == "untap":
        candidates = [_candidate("emits", {"event": "untap"}, rule, evidence)]
        if not _defined_self(line_record):
            candidates.append(
                _candidate("enables", {"capability": "untapper"}, rule, evidence)
            )
        return candidates
    if action == "put_counter":
        candidates = [_candidate("emits", {"event": "counter_placed"}, rule, evidence)]
        counter_type = get("CounterType").lower().replace("+", "").replace("/", "")
        if counter_type in {"p1p1", "11"}:
            candidates.append(
                _candidate("produces", {"object": "p1p1_counter"}, rule, evidence)
            )
        return candidates
    if action == "deal_damage":
        return [
            _candidate("answers", {"threat_class": threat}, rule, evidence)
            for threat in _threat_classes(line_record)
        ]
    reward_events = {
        "reward_etb": ("etb", {}),
        "reward_death": ("death", {}),
        "reward_sacrifice": ("sacrifice", {}),
        "reward_attack": ("attack", {}),
        "reward_combat_damage": ("deal_combat_damage", {}),
        "reward_spell_cast": ("cast_spell", {"type": get("ValidCard", "any")}),
        "reward_cast_spell": ("cast_spell", {"type": get("ValidCard", "any")}),
        "reward_landfall": ("landfall", {}),
        "reward_draw": ("draw", {}),
        "reward_discard": ("discard", {}),
        "reward_counter_placed": ("counter_placed", {}),
        "reward_lifegain": ("lifegain", {}),
        "reward_mana_produced": ("mana_produced", {}),
        "reward_end_step": ("end_step", {}),
        "reward_untap": ("untap", {}),
    }
    if action in reward_events:
        event, extra = reward_events[action]
        return [_candidate("rewards", {"event": event, **extra}, rule, evidence)]
    if action in {"validate_deck_has", "validate_deck_needs", "validate_deck_hints"}:
        direction = (
            "deck_has"
            if action.endswith("has")
            else "deck_hints"
            if action.endswith("hints")
            else "deck_needs"
        )
        return [
            _candidate(
                None,
                {"validation": direction, "tag": raw},
                rule,
                evidence,
                validation_only=True,
            )
        ]
    return []


def apply_mapping(
    row: dict[str, Any],
    mapping: list[dict[str, Any]],
    schema: OntologySchema | None = None,
) -> dict[str, Any]:
    """Apply declarative mapping actions and reject unknown schema predicates."""
    inputs: dict[str, list[Any]] = {
        "effects": row["effects"],
        "costs": row["costs"],
        "triggers": row["triggers"],
        "deck_has": row["deck_has"],
        "deck_needs": row["deck_needs"],
        "deck_hints": row.get("deck_hints") or [],
    }
    candidates: list[dict[str, Any]] = []
    for rule in mapping:
        for record in inputs.get(rule["input"], []):
            if isinstance(record, str):
                raw = record
            elif rule["input"] == "effects":
                # Effect mapping patterns intentionally start at AB$/SP$/DB$,
                # not at the physical ``A:`` line prefix.
                raw = record.get("value", record.get("raw", ""))
            else:
                raw = record.get("raw", "")
            if re.search(rule["match"], str(raw), re.IGNORECASE):
                candidates.extend(_action_candidates(rule["action"], rule, record))
    for candidate in candidates:
        predicate = candidate["predicate"]
        if predicate and schema is not None:
            schema.predicate(predicate)
    row["candidates"] = _unique(candidates)
    return row


def _read_source(path: Path) -> Iterator[tuple[str, str]]:
    if path.is_dir():
        for file_path in sorted(path.rglob("*.txt")):
            yield str(file_path.relative_to(path)), file_path.read_text(
                encoding="utf-8", errors="replace"
            )
        return
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise ForgeMiningError(
            f"cardsfolder must be an existing directory or zip file: {path}"
        )
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir() or not info.filename.lower().endswith(".txt"):
                continue
            yield info.filename, archive.read(info).decode("utf-8", errors="replace")


def _card_key(row: Mapping[str, Any]) -> str:
    return " ".join(str(row["card"]["name"]).lower().split())


def _merge_rows(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate identical files while retaining conflicting face content."""
    existing["card"]["source_files"] = _unique(
        existing["card"].get("source_files", []) + incoming["card"].get("source_files", [])
    )
    for field in (
        "effects",
        "costs",
        "triggers",
        "deck_has",
        "deck_needs",
        "deck_hints",
        "subabilities",
        "candidates",
        "warnings",
    ):
        existing[field] = _unique(existing.get(field, []) + incoming.get(field, []))
    old_faces = existing["card"].get("faces", [])
    new_faces = incoming["card"].get("faces", [])
    existing["card"]["faces"] = _unique(old_faces + new_faces)
    if existing["card"]["faces"]:
        existing["card"]["facts"] = {
            "source": "forge",
            "front": existing["card"]["faces"][0].get("facts", {}),
            "faces": [face.get("facts", {}) for face in existing["card"]["faces"]],
        }
    if len(existing["card"]["faces"]) > len(old_faces):
        existing["warnings"].append(
            f"conflicting duplicate card content retained from {incoming['card']['filename']}"
        )
    return existing


def mine_cardsfolder(
    cardsfolder: str | Path,
    *,
    release: str | None = None,
    commit: str | None = None,
    mapping_path: str | Path | None = None,
    schema: OntologySchema | None = None,
) -> list[dict[str, Any]]:
    """Mine and deterministically merge a local directory or zip cardsfolder."""
    path = Path(cardsfolder)
    mapping_file = (
        Path(mapping_path)
        if mapping_path is not None
        else PROJECT_ROOT / "data" / "ontology" / "forge_mapping.yaml"
    )
    mapping = _load_mapping(mapping_file)
    schema = schema or load_schema()
    merged: dict[str, dict[str, Any]] = {}
    for filename, text in _read_source(path):
        row = apply_mapping(parse_forge_text(text, filename), mapping, schema)
        row["release"] = {
            "tag": release if release is not None else os.environ.get("FORGE_RELEASE"),
            "commit": commit if commit is not None else os.environ.get("FORGE_COMMIT"),
        }
        key = _card_key(row)
        if key in merged:
            merged[key] = _merge_rows(merged[key], row)
        else:
            merged[key] = row
    return [merged[key] for key in sorted(merged)]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mine a local Forge cardsfolder directory or zip into JSONL."
    )
    parser.add_argument("cardsfolder", type=Path)
    parser.add_argument(
        "--release",
        default=os.environ.get("FORGE_RELEASE"),
        help="Forge tag/release identifier; no network lookup is performed",
    )
    parser.add_argument(
        "--commit",
        default=os.environ.get("FORGE_COMMIT"),
        help="Forge commit identifier for local corpus provenance",
    )
    parser.add_argument(
        "--mapping",
        type=Path,
        default=PROJECT_ROOT / "data" / "ontology" / "forge_mapping.yaml",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "data" / "ontology" / "schema_v1.yaml",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("-"),
        help="JSONL destination, or '-' for stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        schema = load_schema(args.schema)
        rows = mine_cardsfolder(
            args.cardsfolder,
            release=args.release,
            commit=args.commit,
            mapping_path=args.mapping,
            schema=schema,
        )
        lines = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        )
        if str(args.output) == "-":
            sys.stdout.write(lines)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(lines, encoding="utf-8")
    except (ForgeMiningError, OSError, ValueError) as exc:
        print(f"mine_forge: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
