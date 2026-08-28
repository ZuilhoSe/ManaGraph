import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from symbolic_cards import classify_card, requirement_families


class SymbolicCardTests(unittest.TestCase):
    def test_capabilities_are_derived_from_oracle(self):
        facts = classify_card(
            "Extra Combat",
            "Sorcery",
            "After this main phase, there is an additional combat phase followed by an additional main phase.",
        )
        self.assertTrue(facts.is_extra_combat)
        self.assertFalse(facts.is_counter)
        self.assertEqual(
            facts.attribute_evidence["is_extra_combat"]["origin"],
            "oracle_rule",
        )

    def test_multiple_capabilities_are_supported(self):
        facts = classify_card(
            "Protection Draw",
            "Instant",
            "Draw a card. Target creature gains hexproof and indestructible until end of turn.",
        )
        self.assertTrue(facts.is_draw)
        self.assertTrue(facts.is_protection)

    def test_requirement_families_are_generic(self):
        families = requirement_families(
            "multiple combat phases, protection for my commander, and card advantage"
        )
        self.assertEqual(families, ["draw", "protection", "extra_combat"])


if __name__ == "__main__":
    unittest.main()
