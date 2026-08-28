"""Closed contracts at the boundary between the LLM manager and the core.

The models in this module are deliberately boring. They validate shape and
types, while catalog.py, DeckState, the solver, and the Commander validator
remain authoritative for card facts and game rules.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1"

Intent = Literal["build", "improve", "substitute", "cut"]
OperationKind = Literal["add", "remove", "substitute", "candidate"]
GateStatus = Literal["APPROVED", "REJECTED", "NEEDS_REVIEW"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)

    @field_validator("schema_version", check_fields=False)
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {value}")
        return value


class CardRef(StrictModel):
    """A user/model card reference; it is resolved against the catalog later."""

    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=99)
    instead_of: str | None = Field(default=None, max_length=200)


class SubstituteRef(StrictModel):
    out: str = Field(min_length=1, max_length=200)
    in_: str = Field(alias="in", min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=99)
    reason: str = Field(default="", max_length=1000)


class PlanOperation(StrictModel):
    kind: OperationKind
    card: str | None = Field(default=None, min_length=1, max_length=200)
    out: str | None = Field(default=None, min_length=1, max_length=200)
    in_: str | None = Field(default=None, alias="in", min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=99)
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_fields(self) -> "PlanOperation":
        if self.kind in ("add", "remove", "candidate") and not self.card:
            raise ValueError(f"{self.kind} operation requires card")
        if self.kind == "substitute" and (not self.out or not self.in_):
            raise ValueError("substitute operation requires out and in")
        return self


class Constraints(StrictModel):
    owned_only: bool = False
    require_complete: bool = False
    currency: Literal["usd", "eur"] = "usd"
    max_card_price: float | None = Field(default=None, ge=0)
    budget_cap: float | None = Field(default=None, ge=0)
    pool_only: bool = False
    price_cap_new_only: bool = True


class IntentSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    query: str = Field(min_length=1, max_length=10000)
    intent: Intent
    archetype: str = Field(default="generic", min_length=1, max_length=40)
    commander: str | None = Field(default=None, max_length=200)
    constraints: Constraints = Field(default_factory=Constraints)
    priorities: list[str] = Field(default_factory=list, max_length=20)
    requirements: list[str] = Field(default_factory=list, max_length=50)
    allow_commander_change: bool = False


class ArchitectPlan(StrictModel):
    """Only this object may cross from the Architect into the Manager."""

    schema_version: str = SCHEMA_VERSION
    base_revision: int = Field(default=0, ge=0)
    intent: Intent | None = None
    archetype: str | None = Field(default=None, max_length=40)
    commander: str | None = Field(default=None, max_length=200)
    operations: list[PlanOperation] = Field(default_factory=list, max_length=200)
    candidates: list[CardRef] = Field(default_factory=list, max_length=200)
    buy_list: list[CardRef] = Field(default_factory=list, max_length=200)
    rationale: str = Field(default="", max_length=4000)


class RejectedOperation(StrictModel):
    operation: PlanOperation
    code: str
    message: str


class PlanResult(StrictModel):
    schema_version: str = SCHEMA_VERSION
    base_revision: int
    revision: int
    applied: list[PlanOperation] = Field(default_factory=list)
    rejected: list[RejectedOperation] = Field(default_factory=list)
    state_changed: bool = False


class GateDecision(StrictModel):
    schema_version: str = SCHEMA_VERSION
    decision: GateStatus
    valid: bool
    reason_codes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_action: Literal["finish", "repair", "clarify", "confirm"] = "finish"


class AllocationCommand(StrictModel):
    schema_version: str = SCHEMA_VERSION
    card: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=100)
    destination: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=99)
    confirmation_id: str = Field(min_length=1, max_length=200)


class RunEvent(StrictModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    sequence: int = Field(ge=0)
    node: str
    state_revision: int = Field(default=0, ge=0)
    event_type: Literal[
        "run_started",
        "node_started",
        "node_finished",
        "log",
        "state_changed",
        "validation",
        "error",
        "done",
        "cancelled",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


def parse_architect_plan(data: dict[str, Any], *, base_revision: int = 0) -> ArchitectPlan:
    """Parse the new contract and a small compatibility subset of legacy JSON."""

    raw = dict(data or {})
    if "operations" not in raw:
        delta = raw.get("delta") if isinstance(raw.get("delta"), dict) else raw
        operations: list[dict[str, Any]] = []
        for item in delta.get("add") or []:
            if isinstance(item, str):
                item = {"name": item}
            operations.append(
                {
                    "kind": "add",
                    "card": item.get("name") or item.get("card"),
                    "quantity": item.get("quantity", 1),
                }
            )
        for item in delta.get("remove") or []:
            if isinstance(item, str):
                item = {"name": item}
            operations.append(
                {
                    "kind": "remove",
                    "card": item.get("name") or item.get("card"),
                    "quantity": item.get("quantity", 1),
                }
            )
        for item in delta.get("substitute") or delta.get("substitutions") or []:
            if isinstance(item, dict):
                operations.append(
                    {
                        "kind": "substitute",
                        "out": item.get("out") or item.get("remove"),
                        "in": item.get("in") or item.get("add"),
                        "quantity": item.get("quantity", 1),
                        "reason": item.get("reason", ""),
                    }
                )
        for item in raw.get("candidate_pool") or raw.get("pool") or []:
            if isinstance(item, str):
                item = {"name": item}
            operations.append(
                {
                    "kind": "candidate",
                    "card": item.get("name") or item.get("card"),
                    "quantity": item.get("quantity", 1),
                }
            )
        raw["operations"] = operations
        raw.pop("delta", None)
        raw.pop("candidate_pool", None)
        raw.pop("pool", None)
    if "rationale" not in raw and raw.get("notes"):
        raw["rationale"] = raw["notes"]
    for legacy_key in (
        "identity",
        "preferred_land_types",
        "theme_types",
        "land_types_strict",
        "notes",
    ):
        raw.pop(legacy_key, None)
    raw.setdefault("base_revision", base_revision)
    if raw.get("candidates"):
        raw.setdefault("operations", [])
        raw["operations"] = list(raw["operations"]) + [
            {
                "kind": "candidate",
                "card": item.get("name"),
                "quantity": item.get("quantity", 1),
            }
            for item in raw["candidates"]
        ]
    return ArchitectPlan.model_validate(raw)
