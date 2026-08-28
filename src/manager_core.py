"""Deterministic state transitions owned by the Manager."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Callable

from catalog import get_oracle_card
from catalog_filters import identity_ok, is_commander_legal
from contracts import (
    ArchitectPlan,
    Constraints,
    IntentSpec,
    PlanOperation,
    PlanResult,
    RejectedOperation,
)
from deck_state import DeckState, _normalize_key, infer_task
from symbolic_cards import requirement_families

CardResolver = Callable[[str], dict | None]
_transition_lock = RLock()


def build_intent_spec(query: str, deck: DeckState) -> IntentSpec:
    """Translate request/configuration into a closed Manager input contract."""

    flags = infer_task(query, deck.slot_count() > 0)
    return IntentSpec(
        query=query,
        intent=flags["intent"],
        archetype=flags.get("archetype") or deck.archetype or "generic",
        commander=deck.commander or None,
        constraints=Constraints(
            owned_only=deck.owned_only,
            require_complete=deck.require_complete,
            currency=deck.currency,
            max_card_price=deck.max_card_price,
            budget_cap=deck.budget_cap,
            pool_only=deck.pool_only,
            price_cap_new_only=deck.price_cap_new_only,
        ),
        priorities=requirement_families(query),
        requirements=requirement_families(query),
        allow_commander_change=any(
            phrase in query.lower()
            for phrase in ("change commander", "switch commander", "set commander")
        ),
    )


def _normalize(name: str) -> str:
    return _normalize_key(name or "")


def _canonical_name(name: str, resolver: CardResolver) -> str | None:
    info = resolver(name)
    return str(info["name"]) if info and info.get("name") else None


def _reject(
    rejected: list[RejectedOperation],
    operation: PlanOperation,
    code: str,
    message: str,
) -> None:
    rejected.append(RejectedOperation(operation=operation, code=code, message=message))


def _check_card_allowed(deck: DeckState, info: dict, name: str) -> tuple[str, str] | None:
    if deck.pool_only and deck.card_pool:
        allowed = {_normalize_key(card) for card in deck.card_pool}
        if _normalize_key(name) not in allowed:
            return (
                "outside_card_pool",
                f"'{name}' is not in the selected physical card pool.",
            )
    if deck.identity and info.get("color_identity") is not None:
        if not identity_ok(info.get("color_identity"), deck.identity):
            return "color_identity", f"'{name}' is outside the commander's color identity."
    legalities = info.get("legalities")
    if legalities and not is_commander_legal(legalities):
        return "not_commander_legal", f"'{name}' is not legal in Commander."
    return None


def apply_plan(
    deck: DeckState,
    plan: ArchitectPlan,
    *,
    resolver: CardResolver | None = None,
    allow_commander_change: bool = False,
) -> PlanResult:
    """Apply one plan as a serialized compare-and-swap transition."""

    with _transition_lock:
        return _apply_plan_unlocked(
            deck,
            plan,
            resolver=resolver,
            allow_commander_change=allow_commander_change,
        )


def _apply_plan_unlocked(
    deck: DeckState,
    plan: ArchitectPlan,
    *,
    resolver: CardResolver | None = None,
    allow_commander_change: bool = False,
) -> PlanResult:
    """Validate and apply a plan atomically.

    Unknown cards and stale revisions are rejected without changing the deck.
    Card facts are resolved from the catalog, never trusted from model output.
    """

    resolver = resolver or get_oracle_card
    base_revision = deck.revision
    if plan.base_revision != base_revision:
        return PlanResult(
            base_revision=plan.base_revision,
            revision=base_revision,
            rejected=[
                RejectedOperation(
                    operation=PlanOperation(kind="candidate", card="revision"),
                    code="stale_revision",
                    message=(
                        f"Plan targets revision {plan.base_revision}, "
                        f"but current state is {base_revision}."
                    ),
                )
            ],
        )

    working = deepcopy(deck)
    applied: list[PlanOperation] = []
    rejected: list[RejectedOperation] = []

    if working.commander:
        current_info = resolver(working.commander)
        current_name = _canonical_name(working.commander, resolver)
        if current_info and current_name:
            working.commander = current_name
            working.identity = list(current_info.get("color_identity") or [])

    if plan.commander:
        info = resolver(plan.commander)
        commander = _canonical_name(plan.commander, resolver)
        same_commander = commander and _normalize(commander) == _normalize(working.commander)
        if same_commander:
            # The manager prompt asks the model to echo the current commander.
            # Echoing it is not a commander-change operation.
            pass
        elif not allow_commander_change:
            _reject(
                rejected,
                PlanOperation(kind="candidate", card=plan.commander),
                "commander_change_forbidden",
                "Commander changes require explicit Manager authorization.",
            )
        else:
            if not commander or not info:
                _reject(
                    rejected,
                    PlanOperation(kind="candidate", card=plan.commander),
                    "unknown_commander",
                    f"Commander '{plan.commander}' was not found in the catalog.",
                )
            elif "Creature" not in (info.get("type_line") or ""):
                _reject(
                    rejected,
                    PlanOperation(kind="candidate", card=plan.commander),
                    "commander_not_creature",
                    f"'{commander}' is not a Commander-eligible creature.",
                )
            else:
                working.set_commander(commander)
                working.identity = list(info.get("color_identity") or [])
                applied.append(
                    PlanOperation(kind="candidate", card=commander, reason="commander")
                )

    for operation in plan.operations:
        if operation.kind == "candidate":
            name = _canonical_name(operation.card or "", resolver)
            if not name:
                _reject(
                    rejected,
                    operation,
                    "unknown_card",
                    f"'{operation.card}' was not found in the catalog.",
                )
                continue
            info = resolver(operation.card or "")
            violation = _check_card_allowed(working, info or {}, name)
            if violation:
                _reject(rejected, operation, violation[0], violation[1])
                continue
            working.add_to_pool(name, operation.quantity)
            applied.append(operation.model_copy(update={"card": name}))
            continue

        if operation.kind in ("add", "remove"):
            name = _canonical_name(operation.card or "", resolver)
            if not name:
                _reject(
                    rejected,
                    operation,
                    "unknown_card",
                    f"'{operation.card}' was not found in the catalog.",
                )
                continue
            info = resolver(operation.card or "")
            violation = _check_card_allowed(working, info or {}, name)
            if operation.kind == "add" and violation:
                _reject(rejected, operation, violation[0], violation[1])
                continue
            canonical_operation = operation.model_copy(update={"card": name})
            if operation.kind == "add":
                working.add_card(name, operation.quantity)
            else:
                key = working._key(name)
                if not key or working.cards[key] < operation.quantity:
                    _reject(
                        rejected,
                        operation,
                        "insufficient_quantity",
                        f"Deck does not contain {operation.quantity} copy/copies of '{name}'.",
                    )
                    continue
                working.remove_card(name, operation.quantity)
            applied.append(canonical_operation)
            continue

        out_name = _canonical_name(operation.out or "", resolver)
        in_name = _canonical_name(operation.in_ or "", resolver)
        if not out_name or not in_name:
            _reject(
                rejected,
                operation,
                "unknown_card",
                "Both cards in a substitute operation must exist in the catalog.",
            )
            continue
        in_info = resolver(operation.in_ or "")
        violation = _check_card_allowed(working, in_info or {}, in_name)
        if violation:
            _reject(rejected, operation, violation[0], violation[1])
            continue
        key = working._key(out_name)
        if not key or working.cards[key] < operation.quantity:
            _reject(
                rejected,
                operation,
                "insufficient_quantity",
                f"Deck does not contain enough copies of '{out_name}'.",
            )
            continue
        working.substitute(out_name, in_name, operation.quantity)
        applied.append(
            operation.model_copy(update={"out": out_name, "in": in_name})
        )

    # A plan is atomic: any rejected operation leaves the original unchanged.
    if rejected:
        return PlanResult(
            base_revision=base_revision,
            revision=base_revision,
            applied=[],
            rejected=rejected,
            state_changed=False,
        )

    changed = (
        working.commander != deck.commander
        or working.identity != deck.identity
        or working.cards != deck.cards
        or working.candidate_pool != deck.candidate_pool
    )
    if not changed:
        return PlanResult(
            base_revision=base_revision,
            revision=base_revision,
            applied=[],
            state_changed=False,
        )

    next_revision = base_revision + 1
    working.revision = next_revision
    working.last_delta = {"added": [], "removed": [], "substituted": []}
    for operation in applied:
        if operation.kind == "add":
            working.last_delta["added"].append(
                {"name": operation.card, "quantity": operation.quantity}
            )
        elif operation.kind == "remove":
            working.last_delta["removed"].append(
                {"name": operation.card, "quantity": operation.quantity}
            )
        elif operation.kind == "substitute":
            working.last_delta["substituted"].append(
                {
                    "out": operation.out,
                    "in": operation.in_,
                    "quantity": operation.quantity,
                    "reason": operation.reason,
                }
            )
    working.last_delta["operations"] = [
        operation.model_dump(by_alias=True) for operation in applied
    ]
    deck.__dict__.update(working.__dict__)
    return PlanResult(
        base_revision=base_revision,
        revision=next_revision,
        applied=applied,
        state_changed=True,
    )
