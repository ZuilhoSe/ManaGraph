"""Versioned, dependency-light contract for the mechanics-first ontology.

This module only loads and validates the YAML contract.  It deliberately does
not import Forge, an RDF/OWL library, or any runtime annotation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised by installation errors
    raise ImportError(
        "Ontology schema loading requires PyYAML; install project requirements."
    ) from exc


class SchemaValidationError(ValueError):
    """Raised when a schema file is missing or violates the v1 contract."""


class ObjectName(str, Enum):
    MANA = "mana"
    TREASURE = "treasure"
    TOKEN = "token"
    CREATURE = "creature"
    P1P1_COUNTER = "p1p1_counter"
    CARD_IN_HAND = "card_in_hand"
    LIFE = "life"
    CREATURE_IN_GRAVEYARD = "creature_in_graveyard"
    CARD_IN_GRAVEYARD = "card_in_graveyard"
    ARTIFACT_PERMANENT = "artifact_permanent"
    ENCHANTMENT_PERMANENT = "enchantment_permanent"
    LAND_IN_PLAY = "land_in_play"
    CARD_IN_EXILE = "card_in_exile"


class EventName(str, Enum):
    ETB = "etb"
    DEATH = "death"
    SACRIFICE = "sacrifice"
    CAST_SPELL = "cast_spell"
    ATTACK = "attack"
    DEAL_COMBAT_DAMAGE = "deal_combat_damage"
    LANDFALL = "landfall"
    DRAW = "draw"
    DISCARD = "discard"
    LIFEGAIN = "lifegain"
    UNTAP = "untap"
    END_STEP = "end_step"
    COUNTER_PLACED = "counter_placed"
    MANA_PRODUCED = "mana_produced"
    TOKEN_CREATED = "token_created"
    BOUNCE = "bounce"


class PredicateName(str, Enum):
    PRODUCES = "produces"
    CONSUMES = "consumes"
    EMITS = "emits"
    REWARDS = "rewards"
    ENABLES = "enables"
    ANSWERS = "answers"
    TUTORS = "tutors"
    RECURS = "recurs"
    PROTECTS = "protects"
    REQUIRES = "requires"


class Capability(str, Enum):
    SAC_OUTLET = "sac_outlet"
    COST_REDUCTION = "cost_reduction"
    HASTE_GRANT = "haste_grant"
    UNTAPPER = "untapper"
    CONVOKE_LIKE = "convoke_like"
    KEYWORD_GRANT = "keyword_grant"
    PROTECTION = "protection"


class ThreatClass(str, Enum):
    CREATURE = "creature"
    ARTIFACT = "artifact"
    ENCHANTMENT = "enchantment"
    BOARD = "board"
    STACK = "stack"
    GRAVEYARD = "graveyard"


class Selector(str, Enum):
    CREATURE = "creature"
    LAND = "land"
    ANY = "any"


class TargetClass(str, Enum):
    COMMANDER = "commander"
    BOARD = "board"


class Zone(str, Enum):
    LIBRARY = "library"
    HAND = "hand"
    BATTLEFIELD = "battlefield"
    GRAVEYARD = "graveyard"
    EXILE = "exile"


@dataclass(frozen=True)
class ObjectDefinition:
    name: ObjectName
    parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventDefinition:
    name: EventName
    parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class PredicateDefinition:
    name: PredicateName
    arguments: tuple[str, ...]
    consumer: str
    description: str = ""


@dataclass(frozen=True)
class OntologySchema:
    schema_version: str
    labels_version: str
    objects: dict[str, ObjectDefinition]
    events: dict[str, EventDefinition]
    capabilities: tuple[Capability, ...]
    threat_classes: tuple[ThreatClass, ...]
    selectors: tuple[Selector, ...]
    target_classes: tuple[TargetClass, ...]
    predicates: dict[str, PredicateDefinition]
    provenance: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> "OntologySchema":
        """Validate cross-references and return this schema for fluent use."""
        if not self.schema_version or not self.labels_version:
            raise SchemaValidationError("schema_version and labels_version are required")
        if not self.objects or not self.events or not self.predicates:
            raise SchemaValidationError("objects, events, and predicates cannot be empty")
        for predicate in self.predicates.values():
            if not predicate.arguments:
                raise SchemaValidationError(
                    f"predicate {predicate.name.value} must declare arguments"
                )
            if not predicate.consumer:
                raise SchemaValidationError(
                    f"predicate {predicate.name.value} must declare a consumer"
                )
        forge = self.provenance.get("forge", {})
        if forge and not isinstance(forge, Mapping):
            raise SchemaValidationError("provenance.forge must be a mapping")
        if forge and forge.get("runtime_dependency", False):
            raise SchemaValidationError("Forge cannot be a schema runtime dependency")
        return self

    def has_object(self, value: str | ObjectName) -> bool:
        key = value.value if isinstance(value, Enum) else str(value)
        return key in self.objects

    def has_event(self, value: str | EventName) -> bool:
        key = value.value if isinstance(value, Enum) else str(value)
        return key in self.events

    def predicate(self, value: str | PredicateName) -> PredicateDefinition:
        key = value.value if isinstance(value, Enum) else str(value)
        try:
            return self.predicates[key]
        except KeyError as exc:
            raise SchemaValidationError(f"unknown predicate: {key}") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{label} must be a mapping")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise SchemaValidationError(f"{label} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _enum_tuple(enum_type: type[Enum], value: Any, label: str) -> tuple[Enum, ...]:
    values = _string_tuple(value, label)
    try:
        return tuple(enum_type(item) for item in values)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise SchemaValidationError(
            f"{label} contains an unknown value ({exc}); allowed: {allowed}"
        ) from exc


def _definitions(
    raw: Any,
    enum_type: type[Enum],
    definition_type: type[ObjectDefinition] | type[EventDefinition],
    label: str,
) -> dict[str, ObjectDefinition | EventDefinition]:
    if not isinstance(raw, list):
        raise SchemaValidationError(f"{label} must be a list")
    result: dict[str, ObjectDefinition | EventDefinition] = {}
    for index, item in enumerate(raw):
        data = _mapping(item, f"{label}[{index}]")
        try:
            name = enum_type(_string(data.get("name"), f"{label}[{index}].name"))
        except ValueError as exc:
            raise SchemaValidationError(
                f"{label}[{index}].name is not in the v1 enum: {exc}"
            ) from exc
        if name.value in result:
            raise SchemaValidationError(f"duplicate {label} name: {name.value}")
        result[name.value] = definition_type(
            name=name,
            parameters=_string_tuple(data.get("parameters"), f"{label}[{index}].parameters"),
        )
    return result


def _load_data(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise SchemaValidationError(f"cannot read ontology schema {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SchemaValidationError(f"invalid YAML in ontology schema {path}: {exc}") from exc
    return _mapping(data, "schema")


def schema_from_mapping(data: Mapping[str, Any]) -> OntologySchema:
    """Build and validate a schema from a decoded YAML mapping."""
    objects = _definitions(
        data.get("objects"), ObjectName, ObjectDefinition, "objects"
    )
    events = _definitions(data.get("events"), EventName, EventDefinition, "events")

    raw_predicates = data.get("predicates")
    if not isinstance(raw_predicates, list):
        raise SchemaValidationError("predicates must be a list")
    predicates: dict[str, PredicateDefinition] = {}
    for index, item in enumerate(raw_predicates):
        row = _mapping(item, f"predicates[{index}]")
        try:
            name = PredicateName(_string(row.get("name"), f"predicates[{index}].name"))
        except ValueError as exc:
            raise SchemaValidationError(
                f"predicates[{index}].name is not in the v1 enum: {exc}"
            ) from exc
        if name.value in predicates:
            raise SchemaValidationError(f"duplicate predicate name: {name.value}")
        predicates[name.value] = PredicateDefinition(
            name=name,
            arguments=_string_tuple(row.get("arguments"), f"predicates[{index}].arguments"),
            consumer=_string(row.get("consumer"), f"predicates[{index}].consumer"),
            description=str(row.get("description") or "").strip(),
        )

    schema = OntologySchema(
        schema_version=_string(data.get("schema_version"), "schema_version"),
        labels_version=_string(data.get("labels_version"), "labels_version"),
        objects=objects,  # type: ignore[arg-type]
        events=events,  # type: ignore[arg-type]
        capabilities=_enum_tuple(Capability, data.get("capabilities"), "capabilities"),  # type: ignore[arg-type]
        threat_classes=_enum_tuple(
            ThreatClass, data.get("threat_classes"), "threat_classes"
        ),  # type: ignore[arg-type]
        selectors=_enum_tuple(Selector, data.get("selectors"), "selectors"),  # type: ignore[arg-type]
        target_classes=_enum_tuple(
            TargetClass, data.get("target_classes"), "target_classes"
        ),  # type: ignore[arg-type]
        predicates=predicates,
        provenance=dict(_mapping(data.get("provenance") or {}, "provenance")),
    )
    return schema.validate()


def load_schema(path: str | Path | None = None) -> OntologySchema:
    """Load ``data/ontology/schema_v1.yaml`` or an explicitly supplied path."""
    schema_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[2] / "data" / "ontology" / "schema_v1.yaml"
    )
    return schema_from_mapping(_load_data(schema_path))

