"""Selection benchmark for full-deck scenario fixtures.

This measures the deterministic Solver/Validator layer after retrieval:
the fixture is copied, solved without an LLM, and the final deck is checked
for legal size plus the curated good/bad labels from its matching archetype.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
sys.path.insert(0, SRC_DIR)

from catalog import get_oracle_card
from deck_state import DeckState
from mana import diagnose_deck, strategy_from_name
from roles import role_counts
from rules_validator import CommanderValidator
from solver import DeckSolver


def _labels(scenario_path: str) -> tuple[list[str], list[str]]:
    filename = os.path.basename(scenario_path).replace("_full_deck.json", ".json")
    path = os.path.join(BASE_DIR, "eval", "archetypes", filename)
    if not os.path.isfile(path):
        return [], []
    with open(path, encoding="utf-8") as handle:
        spec = json.load(handle)
    return list(spec.get("good") or []), list(spec.get("bad") or [])


def run(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        scenario = json.load(handle)
    deck = DeckState.from_dict(scenario.get("deck") or {})
    if deck.commander and not deck.identity:
        info = get_oracle_card(deck.commander)
        if info:
            deck.identity = list(info.get("color_identity") or [])
    deck.baseline_cards = dict(deck.cards)
    before = set(name.lower() for name in deck.card_list())

    report = DeckSolver().solve(
        deck,
        query=scenario.get("query") or "",
        fill_to_99=bool(deck.require_complete and deck.commander),
    )
    validation = CommanderValidator().validate_deck_state(deck)
    final = set(name.lower() for name in deck.card_list())
    good, bad = _labels(path)
    diagnosis = diagnose_deck(
        deck,
        strategy=strategy_from_name(deck.mana_strategy),
    )
    rejected = sum(
        1
        for item in (deck.last_delta or {}).get("failed_substitutions") or []
        if item
    )
    good_present = [card for card in good if card.lower() in final]
    bad_present = [card for card in bad if card.lower() in final]
    good_recall = len(good_present) / len(good) if good else None
    bad_hit_rate = len(bad_present) / len(bad) if bad else None
    role_counts_report = diagnosis.get("roles") or role_counts([], deck.archetype)
    role_quotas = {"land": (34, 40), "ramp": (8, 14), "draw": (8, 18), "interaction": (8, 18)}
    roles_meeting_floor = sum(
        int(role_counts_report.get(role, 0)) >= low
        for role, (low, _high) in role_quotas.items()
    )
    return {
        "scenario": os.path.basename(path),
        "before_slots": sum((scenario.get("deck") or {}).get("cards", {}).values()),
        "after_slots": deck.slot_count(),
        "changed_cards": len(before ^ final),
        "good_present": good_present,
        "good_missing": [card for card in good if card.lower() not in final],
        "bad_present": bad_present,
        "metrics": {
            "good_recall": good_recall,
            "bad_hit_rate": bad_hit_rate,
            "roles_meeting_floor": roles_meeting_floor,
            "role_floor_count": len(role_quotas),
        },
        "role_counts": role_counts_report,
        "curve": diagnosis.get("curve") or {},
        "land_alert": diagnosis.get("land_alert") or {},
        "mana_sources": diagnosis.get("sources") or {},
        "rejected_substitutions": rejected,
        "validation_valid": bool(validation.get("valid")),
        "validation_errors": {
            key: value
            for key, value in validation.items()
            if key.endswith("_errors") and value
        },
        "solver": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        nargs="?",
        default="eval/scenarios/aminatou_esper_enchantments_full_deck.json",
    )
    args = parser.parse_args()
    path = args.scenario
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    result = run(path)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["validation_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
