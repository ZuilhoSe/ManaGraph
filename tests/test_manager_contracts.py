import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from contracts import ArchitectPlan, parse_architect_plan
from deck_state import DeckState
from manager_core import apply_plan, build_intent_spec


def resolver(name):
    cards = {
        "Test Commander": {
            "name": "Test Commander",
            "type_line": "Legendary Creature — Wizard",
            "color_identity": ["U"],
        },
        "Island": {
            "name": "Island",
            "type_line": "Basic Land — Island",
            "color_identity": [],
        },
        "Counterspell": {
            "name": "Counterspell",
            "type_line": "Instant",
            "color_identity": ["U"],
        },
    }
    return next(
        (card for key, card in cards.items() if key.lower() == name.lower()),
        None,
    )


class ManagerContractTests(unittest.TestCase):
    def test_legacy_proposal_is_normalized_to_operations(self):
        plan = parse_architect_plan(
            {
                "delta": {"add": [{"name": "Island", "quantity": 1}]},
                "candidate_pool": [{"name": "Counterspell"}],
            }
        )
        self.assertEqual([op.kind for op in plan.operations], ["add", "candidate"])

    def test_invalid_quantities_are_rejected_by_contract(self):
        with self.assertRaises(ValueError):
            ArchitectPlan.model_validate(
                {
                    "operations": [
                        {"kind": "add", "card": "Island", "quantity": 0}
                    ]
                }
            )

    def test_plan_is_atomic_when_one_card_is_unknown(self):
        deck = DeckState(commander="Test Commander", identity=["U"])
        plan = ArchitectPlan(
            base_revision=0,
            operations=[
                {"kind": "add", "card": "Island"},
                {"kind": "add", "card": "Not A Card"},
            ],
        )
        result = apply_plan(deck, plan, resolver=resolver)
        self.assertFalse(result.state_changed)
        self.assertEqual(deck.slot_count(), 0)
        self.assertEqual(deck.revision, 0)
        self.assertEqual(result.rejected[0].code, "unknown_card")

    def test_revision_prevents_stale_plan(self):
        deck = DeckState(commander="Test Commander", identity=["U"])
        first = apply_plan(
            deck,
            ArchitectPlan(
                base_revision=0,
                operations=[{"kind": "add", "card": "Island"}],
            ),
            resolver=resolver,
        )
        self.assertTrue(first.state_changed)
        self.assertEqual(deck.revision, 1)
        stale = apply_plan(
            deck,
            ArchitectPlan(
                base_revision=0,
                operations=[{"kind": "add", "card": "Counterspell"}],
            ),
            resolver=resolver,
        )
        self.assertFalse(stale.state_changed)
        self.assertEqual(stale.rejected[0].code, "stale_revision")
        self.assertNotIn("Counterspell", deck.cards)

    def test_identity_is_derived_when_commander_changes(self):
        deck = DeckState()
        plan = ArchitectPlan(base_revision=0, commander="Test Commander")
        result = apply_plan(
            deck,
            plan,
            resolver=resolver,
            allow_commander_change=True,
        )
        self.assertTrue(result.state_changed)
        self.assertEqual(deck.commander, "Test Commander")
        self.assertEqual(deck.identity, ["U"])

    def test_concurrent_same_revision_allows_only_one_transition(self):
        deck = DeckState(commander="Test Commander", identity=["U"])
        plan = ArchitectPlan(
            base_revision=0,
            operations=[{"kind": "add", "card": "Island"}],
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: apply_plan(deck, plan, resolver=resolver),
                    range(2),
                )
            )
        self.assertEqual(sum(result.state_changed for result in results), 1)
        self.assertEqual(deck.revision, 1)
        self.assertEqual(deck.cards, {"Island": 1})

    def test_intent_spec_contains_structured_requirements(self):
        spec = build_intent_spec(
            "Improve this deck with extra combat and protection",
            DeckState(commander="Test Commander", identity=["U"]),
        )
        self.assertEqual(spec.intent, "improve")
        self.assertIn("extra_combat", spec.requirements)
        self.assertIn("protection", spec.requirements)


if __name__ == "__main__":
    unittest.main()
