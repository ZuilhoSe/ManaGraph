"""Global contract for constructing the final card model.

This is deliberately separate from per-card review.  A model configuration is
selected once, saved, and then applied to every ontology card.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from catalog import mana_cost_from_scryfall, oracle_text_from_scryfall

MODEL_CONFIG_VERSION = "1.0.0"
MODEL_SOURCES = ("scryfall", "forge", "resolved", "exclude")

CARD_FIELDS = (
    ("name", "Name"),
    ("mana_cost", "Mana cost"),
    ("mana_value", "Mana value"),
    ("colors", "Colors"),
    ("color_identity", "Color identity"),
    ("type_line", "Type line"),
    ("keywords", "Keywords"),
    ("power", "Power"),
    ("toughness", "Toughness"),
    ("produced_mana", "Produced mana"),
    ("oracle_text", "Oracle text"),
    ("legalities", "Legalities"),
    ("id", "Scryfall id"),
    ("oracle_id", "Oracle id"),
    ("layout", "Layout"),
    ("faces", "Faces"),
    ("types", "Types"),
    ("symbols", "Mana symbols"),
    ("colored_pips", "Colored pips"),
    ("generic_pips", "Generic pips"),
    ("loyalty", "Loyalty"),
    ("defense", "Defense"),
    ("all_parts", "Related parts"),
)
MECHANIC_FIELDS = (
    ("effects", "Effects"),
    ("costs", "Costs"),
    ("triggers", "Triggers"),
    ("subabilities", "Subabilities"),
    ("deck_has", "DeckHas"),
    ("deck_needs", "DeckNeeds"),
    ("deck_hints", "DeckHints"),
    ("candidates", "Ontology candidates"),
    ("warnings", "Warnings"),
    ("ability_kinds", "Ability kinds"),
    ("effect_count", "Effect count"),
    ("trigger_count", "Trigger count"),
    ("forge_keywords_not_in_scryfall", "Forge-only keywords"),
)

_RESOLVED_CARD_FIELDS = frozenset(
    {"types", "symbols", "colored_pips", "generic_pips"}
)
_RESOLVED_MECHANIC_FIELDS = frozenset(
    {
        "ability_kinds",
        "effect_count",
        "trigger_count",
        "forge_keywords_not_in_scryfall",
    }
)
_OPTIONAL_CARD_FIELDS = frozenset(
    {"loyalty", "defense", "all_parts", "oracle_id", "faces"}
)

_FACE_KEYS = (
    "name",
    "mana_cost",
    "oracle_text",
    "colors",
    "color_indicator",
    "type_line",
    "keywords",
    "power",
    "toughness",
    "loyalty",
    "defense",
)
_ALL_PARTS_KEYS = ("id", "component", "name", "type_line")
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


def default_model_config() -> dict[str, Any]:
    field_sources = {
        **{field: "scryfall" for field, _ in CARD_FIELDS},
        **{field: "forge" for field, _ in MECHANIC_FIELDS},
    }
    field_sources.update({field: "resolved" for field in _RESOLVED_CARD_FIELDS})
    field_sources.update({field: "resolved" for field in _RESOLVED_MECHANIC_FIELDS})
    return {
        "config_version": MODEL_CONFIG_VERSION,
        "name": "scryfall-forge-final-v1",
        "source_enabled": {"scryfall": True, "forge": True, "resolved": True},
        "field_sources": field_sources,
        "field_enabled": {
            "scryfall": {field: True for field, _ in CARD_FIELDS},
            "forge": {field: True for field, _ in MECHANIC_FIELDS},
        },
        "value_filters": {"scryfall": {}, "forge": {}},
        "raw_enabled": {"scryfall": True, "forge": True},
        "raw_fields": {"scryfall": ["*"], "forge": ["*"]},
    }


def model_field_metadata() -> list[dict[str, Any]]:
    return [
        {
            "id": field,
            "label": label,
            "group": "card",
            "sources": list(MODEL_SOURCES),
        }
        for field, label in CARD_FIELDS
    ] + [
        {
            "id": field,
            "label": label,
            "group": "mechanics",
            "sources": list(MODEL_SOURCES),
        }
        for field, label in MECHANIC_FIELDS
    ]


def normalize_model_config(value: Mapping[str, Any] | None) -> dict[str, Any]:
    config = default_model_config()
    if value:
        config["name"] = str(value.get("name") or config["name"]).strip()
        for source in ("scryfall", "forge", "resolved"):
            if source in (value.get("source_enabled") or {}):
                config["source_enabled"][source] = bool(value["source_enabled"][source])
        requested = value.get("field_sources") or {}
        if not isinstance(requested, Mapping):
            raise ValueError("field_sources must be an object")
        for field in config["field_sources"]:
            source = requested.get(field, config["field_sources"][field])
            if source not in MODEL_SOURCES:
                raise ValueError(
                    f"source for {field} must be one of: {', '.join(MODEL_SOURCES)}"
                )
            config["field_sources"][field] = source
        for source in ("scryfall", "forge"):
            requested_fields = (value.get("field_enabled") or {}).get(source) or {}
            if not isinstance(requested_fields, Mapping):
                raise ValueError(f"field_enabled.{source} must be an object")
            for field in config["field_enabled"][source]:
                if field in requested_fields:
                    config["field_enabled"][source][field] = bool(requested_fields[field])
            config["raw_enabled"][source] = bool(
                (value.get("raw_enabled") or {}).get(source, config["raw_enabled"][source])
            )
            raw_fields = (value.get("raw_fields") or {}).get(source)
            if isinstance(raw_fields, (list, tuple)):
                config["raw_fields"][source] = sorted({str(item) for item in raw_fields})
        requested_filters = value.get("value_filters") or {}
        if not isinstance(requested_filters, Mapping):
            raise ValueError("value_filters must be an object")
        for source in ("scryfall", "forge"):
            source_filters = requested_filters.get(source) or {}
            if not isinstance(source_filters, Mapping):
                raise ValueError(f"value_filters.{source} must be an object")
            config["value_filters"][source] = {
                str(field): [str(item) for item in values]
                for field, values in source_filters.items()
                if isinstance(values, (list, tuple))
            }
    return config


def _parse_one_type_line(text: str) -> dict[str, Any]:
    parts = re.split(r"\s+[—–-]\s+", text, maxsplit=1)
    left = parts[0].strip()
    right = parts[1].strip() if len(parts) == 2 else ""
    words = left.split()
    supertypes = [word for word in words if word in _SUPERTYPES]
    card_types = [word for word in words if word in _CARD_TYPES]
    subtypes = (
        right.split()
        if right
        else [word for word in words if word not in _SUPERTYPES and word not in _CARD_TYPES]
    )
    return {
        "card_types": card_types,
        "supertypes": supertypes,
        "subtypes": subtypes,
    }


def parse_type_line(raw: str | None) -> dict[str, Any]:
    """Split a Scryfall or Forge type line into card types, supertypes, subtypes."""
    text = str(raw or "").strip()
    if " // " in text:
        combined = {"card_types": [], "supertypes": [], "subtypes": []}
        seen = {key: set() for key in combined}
        for part in text.split(" // "):
            parsed = _parse_one_type_line(part.strip())
            for key in combined:
                for item in parsed[key]:
                    if item not in seen[key]:
                        seen[key].add(item)
                        combined[key].append(item)
        return combined
    return _parse_one_type_line(text)


def parse_mana_cost(raw: str | None) -> dict[str, Any]:
    """Parse `{2}{R}` or Forge `2 R` into symbols and pip counts."""
    text = str(raw or "").strip()
    symbols = _MANA_SYMBOL_RE.findall(text)
    if not symbols and text:
        symbols = text.split()
    colored_pips: dict[str, int] = {}
    generic_pips = 0
    for symbol in symbols:
        token = symbol.strip().upper()
        if token.isdigit():
            generic_pips += int(token)
        elif token in _COLOURS:
            colored_pips[token] = colored_pips.get(token, 0) + 1
        else:
            for colour in _COLOURS:
                if colour in token and token == colour:
                    colored_pips[colour] = colored_pips.get(colour, 0) + 1
    return {
        "symbols": symbols,
        "colored_pips": colored_pips,
        "generic_pips": generic_pips,
    }


def slim_all_parts(parts: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in parts or []:
        if not isinstance(part, Mapping):
            continue
        item = {key: part.get(key) for key in _ALL_PARTS_KEYS if part.get(key) is not None}
        if item.get("name") or item.get("id"):
            result.append(item)
    return result


def slim_faces(card_faces: Any) -> list[dict[str, Any]]:
    faces: list[dict[str, Any]] = []
    for face in card_faces or []:
        if not isinstance(face, Mapping):
            continue
        item = {key: face.get(key) for key in _FACE_KEYS if face.get(key) is not None}
        if item:
            faces.append(item)
    return faces


def merged_oracle_text(canonical: Mapping[str, Any]) -> str:
    text = str(canonical.get("oracle_text") or "").strip()
    if text:
        return text
    return "\n\n".join(
        str(face.get("oracle_text") or "").strip()
        for face in (canonical.get("faces") or [])
        if str(face.get("oracle_text") or "").strip()
    )


def is_svar_effect(effect: Any) -> bool:
    if not isinstance(effect, Mapping):
        return str(effect).startswith("SVar:")
    return (
        str(effect.get("kind") or "").lower() == "svar"
        or str(effect.get("prefix") or "") == "SVar"
        or str(effect.get("raw") or "").startswith("SVar:")
    )


def _row_get(row: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def canonical_facts_from_scryfall(
    card: Mapping[str, Any],
    row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalized Scryfall facts, including DFC/split faces and identifiers."""
    faces = slim_faces(card.get("card_faces"))
    keywords = list(card.get("keywords") or [])
    seen = {str(item) for item in keywords}
    for face in faces:
        for keyword in face.get("keywords") or []:
            if str(keyword) not in seen:
                seen.add(str(keyword))
                keywords.append(keyword)
    power = card.get("power")
    toughness = card.get("toughness")
    if power is None and faces:
        power = faces[0].get("power")
    if toughness is None and faces:
        toughness = faces[0].get("toughness")
    loyalty = card.get("loyalty")
    defense = card.get("defense")
    if loyalty is None:
        loyalty = next((face.get("loyalty") for face in faces if face.get("loyalty") is not None), None)
    if defense is None:
        defense = next((face.get("defense") for face in faces if face.get("defense") is not None), None)
    oracle = oracle_text_from_scryfall(dict(card))
    if not oracle:
        oracle = str(_row_get(row, "oracle_text", "") or "")
    mana_cost = mana_cost_from_scryfall(dict(card)) or card.get("mana_cost") or _row_get(row, "mana_cost", "") or ""
    color_identity = card.get("color_identity")
    if not color_identity:
        raw_identity = _row_get(row, "color_identity")
        if isinstance(raw_identity, str):
            try:
                color_identity = json.loads(raw_identity)
            except json.JSONDecodeError:
                color_identity = []
        else:
            color_identity = raw_identity or []
    return {
        "source": "scryfall",
        "id": card.get("id") or _row_get(row, "id"),
        "oracle_id": card.get("oracle_id"),
        "name": card.get("name") or _row_get(row, "name"),
        "layout": card.get("layout") or "normal",
        "mana_cost": mana_cost,
        "mana_value": card.get("cmc", card.get("mana_value", _row_get(row, "cmc"))),
        "colors": card.get("colors") or [],
        "color_identity": color_identity or [],
        "type_line": card.get("type_line") or _row_get(row, "type_line") or "",
        "keywords": keywords,
        "power": power,
        "toughness": toughness,
        "loyalty": loyalty,
        "defense": defense,
        "produced_mana": card.get("produced_mana") or [],
        "oracle_text": oracle,
        "legalities": card.get("legalities") or {},
        "faces": faces,
        "all_parts": slim_all_parts(card.get("all_parts")),
    }


def _types_from_layers(
    canonical: Mapping[str, Any],
    forge_row: Mapping[str, Any] | None,
    resolved_card: Mapping[str, Any] | None,
) -> dict[str, Any]:
    existing = (resolved_card or {}).get("types")
    if isinstance(existing, Mapping) and (
        existing.get("card_types") or existing.get("supertypes") or existing.get("subtypes")
    ):
        return {
            "card_types": list(existing.get("card_types") or []),
            "supertypes": list(existing.get("supertypes") or []),
            "subtypes": list(existing.get("subtypes") or []),
        }
    front = (((forge_row or {}).get("card") or {}).get("facts") or {}).get("front") or {}
    forge_types = front.get("types") if isinstance(front, Mapping) else None
    if isinstance(forge_types, Mapping) and (
        forge_types.get("card_types") or forge_types.get("supertypes") or forge_types.get("subtypes")
    ):
        return {
            "card_types": list(forge_types.get("card_types") or []),
            "supertypes": list(forge_types.get("supertypes") or []),
            "subtypes": list(forge_types.get("subtypes") or []),
        }
    return parse_type_line(str(canonical.get("type_line") or ""))


def _mana_from_layers(
    canonical: Mapping[str, Any],
    forge_row: Mapping[str, Any] | None,
    resolved_card: Mapping[str, Any] | None,
) -> dict[str, Any]:
    existing_symbols = (resolved_card or {}).get("symbols")
    if isinstance(existing_symbols, list) and existing_symbols:
        return {
            "symbols": list(existing_symbols),
            "colored_pips": dict((resolved_card or {}).get("colored_pips") or {}),
            "generic_pips": (resolved_card or {}).get("generic_pips") or 0,
        }
    front = (((forge_row or {}).get("card") or {}).get("facts") or {}).get("front") or {}
    forge_mana = front.get("mana") if isinstance(front, Mapping) else None
    if isinstance(forge_mana, Mapping) and (
        forge_mana.get("symbols") or forge_mana.get("raw") or forge_mana.get("colored_pips")
    ):
        return {
            "symbols": list(forge_mana.get("symbols") or []),
            "colored_pips": dict(forge_mana.get("colored_pips") or {}),
            "generic_pips": forge_mana.get("generic_pips") or 0,
        }
    return parse_mana_cost(str(canonical.get("mana_cost") or ""))


def _forge_values(forge_row: Mapping[str, Any] | None) -> dict[str, Any]:
    row = forge_row or {}
    card = row.get("card") or {}
    facts = card.get("facts") or {}
    front = facts.get("front") or {}
    mana = front.get("mana") or {}
    types = front.get("types") or {}
    pt = front.get("power_toughness") or {}
    faces = facts.get("faces") or []
    keywords = sorted(
        {
            keyword
            for face in faces
            for keyword in face.get("keywords") or []
        }
        | set(front.get("keywords") or [])
    )
    parsed_types = {
        "card_types": list(types.get("card_types") or []),
        "supertypes": list(types.get("supertypes") or []),
        "subtypes": list(types.get("subtypes") or []),
    }
    return {
        "name": card.get("name"),
        "mana_cost": mana.get("raw") or None,
        "colors": mana.get("colors") or [],
        "type_line": types.get("raw") or None,
        "keywords": keywords,
        "power": pt.get("power"),
        "toughness": pt.get("toughness"),
        "oracle_text": card.get("oracle") or None,
        "types": parsed_types,
        "symbols": list(mana.get("symbols") or []),
        "colored_pips": dict(mana.get("colored_pips") or {}),
        "generic_pips": mana.get("generic_pips") or 0,
    }


def _filter_value(value: Any, allowed: list[str] | None) -> Any:
    """Filter categorical/list values; an empty filter means keep everything."""
    if not allowed or not isinstance(value, list):
        return value
    allowed_set = set(allowed)
    return [item for item in value if str(item) in allowed_set]


def _field_wanted(normalized: Mapping[str, Any], field: str) -> bool:
    source = normalized["field_sources"].get(field)
    if source == "exclude":
        return False
    if source is None:
        return True
    return bool(normalized["source_enabled"].get(source, False))


def _promote_computed_fields(
    result: dict[str, Any],
    canonical: Mapping[str, Any],
    forge_row: Mapping[str, Any] | None,
    resolved: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> None:
    """Fill Final fields from existing layers so a model rebuild does not need re-enrich."""
    card = result["card"]
    mechanics = result["mechanics"]
    resolved_card = resolved.get("card") or {}
    resolved_mechanics = resolved.get("mechanics") or {}

    if _field_wanted(normalized, "oracle_text"):
        merged = merged_oracle_text(card) or merged_oracle_text(canonical)
        if merged:
            card["oracle_text"] = merged

    if _field_wanted(normalized, "faces"):
        faces = card.get("faces") or canonical.get("faces") or resolved.get("faces") or []
        if faces:
            card["faces"] = faces
        else:
            card.pop("faces", None)

    for key in ("id", "oracle_id", "layout", "loyalty", "defense", "all_parts"):
        if not _field_wanted(normalized, key):
            continue
        value = card.get(key)
        if value in (None, "", []):
            value = canonical.get(key)
        if value in (None, "", []):
            value = resolved_card.get(key)
        if value in (None, "", []) and key in _OPTIONAL_CARD_FIELDS:
            card.pop(key, None)
        elif value not in (None, ""):
            card[key] = value

    if _field_wanted(normalized, "types"):
        card["types"] = _types_from_layers(canonical, forge_row, resolved_card)

    if any(_field_wanted(normalized, field) for field in ("symbols", "colored_pips", "generic_pips")):
        mana = _mana_from_layers(canonical, forge_row, resolved_card)
        if _field_wanted(normalized, "symbols"):
            card["symbols"] = mana["symbols"]
        if _field_wanted(normalized, "colored_pips"):
            card["colored_pips"] = mana["colored_pips"]
        if _field_wanted(normalized, "generic_pips"):
            card["generic_pips"] = mana["generic_pips"]

    for key in _RESOLVED_MECHANIC_FIELDS:
        if _field_wanted(normalized, key) and key not in mechanics and key in resolved_mechanics:
            mechanics[key] = resolved_mechanics[key]

    if "effects" in mechanics:
        mechanics["effects"] = [
            effect for effect in mechanics["effects"] if not is_svar_effect(effect)
        ]
        if _field_wanted(normalized, "effect_count"):
            mechanics["effect_count"] = len(mechanics["effects"])
        kinds = mechanics.get("ability_kinds")
        if isinstance(kinds, list):
            mechanics["ability_kinds"] = [kind for kind in kinds if str(kind).lower() != "svar"]


def build_model_facts(
    canonical: Mapping[str, Any],
    forge_row: Mapping[str, Any] | None,
    resolved: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one global source-selection policy to one card."""
    normalized = normalize_model_config(config)
    forge = _forge_values(forge_row)
    forge_mechanics = {
        field: (forge_row or {}).get(field) or []
        for field, _ in MECHANIC_FIELDS
        if field != "candidates" and field not in _RESOLVED_MECHANIC_FIELDS
    }
    forge_mechanics["candidates"] = (forge_row or {}).get("candidates") or []
    forge_mechanics["warnings"] = (forge_row or {}).get("warnings") or []
    resolved = resolved or {}
    resolved_card = dict(resolved.get("card") or {})
    if "faces" not in resolved_card and resolved.get("faces"):
        resolved_card["faces"] = resolved["faces"]
    resolved_mechanics = dict(resolved.get("mechanics") or {})
    sources = {
        "scryfall": dict(canonical),
        "forge": forge,
        "resolved": resolved_card,
    }
    mechanic_sources = {
        "scryfall": {},
        "forge": forge_mechanics,
        "resolved": resolved_mechanics,
    }
    result = {
        "source": "configured-model",
        "config_version": normalized["config_version"],
        "config_name": normalized["name"],
        "source_selection": dict(normalized["field_sources"]),
        "raw_selection": {
            source: {
                "enabled": normalized["raw_enabled"][source],
                "fields": normalized["raw_fields"][source],
            }
            for source in ("scryfall", "forge")
        },
        "card": {},
        "mechanics": {},
    }
    for field, _ in CARD_FIELDS:
        source = normalized["field_sources"][field]
        if (
            source != "exclude"
            and normalized["source_enabled"].get(source, False)
            and normalized["field_enabled"].get(source, {}).get(field, True)
            and field in sources[source]
        ):
            value = sources[source][field]
            if field in _OPTIONAL_CARD_FIELDS and value in (None, "", []):
                continue
            result["card"][field] = _filter_value(
                value, normalized["value_filters"].get(source, {}).get(field)
            )
    for field, _ in MECHANIC_FIELDS:
        source = normalized["field_sources"][field]
        if (
            source != "exclude"
            and normalized["source_enabled"].get(source, False)
            and normalized["field_enabled"].get(source, {}).get(field, True)
            and field in mechanic_sources[source]
        ):
            result["mechanics"][field] = _filter_value(
                mechanic_sources[source][field],
                normalized["value_filters"].get(source, {}).get(field),
            )
    _promote_computed_fields(result, canonical, forge_row, resolved, normalized)
    return result
