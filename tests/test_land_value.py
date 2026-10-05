import json
import os
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema
from deck_analysis.land_value import land_delta, land_profile
from deck_state import DeckState
from solver import DeckSolver, _ontology_pair_score, _pred_counts

BOROS = ["R", "W"]
SHORT = ({"R": 0, "W": 0}, {"R": 20, "W": 15})
MET = ({"R": 30, "W": 30}, {"R": 20, "W": 15})

LANDS = {
    "Rainbow Vale": (
        "Land",
        "{T}: Add one mana of any color. An opponent gains control of this land at the "
        "beginning of the next end step.",
    ),
    "Forbidden Orchard": (
        "Land",
        "{T}: Add one mana of any color.\nWhenever you tap this land for mana, target "
        "opponent creates a 1/1 colorless Spirit creature token.",
    ),
    "Sliver Hive": (
        "Land",
        "{T}: Add {C}.\n{T}: Add one mana of any color. Spend this mana only to cast a "
        "Sliver spell.\n{5}, {T}: Create a 1/1 colorless Sliver creature token. Activate "
        "only if you control a Sliver.",
    ),
    "Lotus Field": (
        "Land",
        "Hexproof\nThis land enters tapped.\nWhen this land enters, sacrifice two lands.\n"
        "{T}: Add three mana of any one color.",
    ),
    "Hall of Tagsin": (
        "Land",
        "{T}: Add {C}.\n{1}, {T}: Add one mana of any color.\n{4}, {T}: Create a tapped "
        "Powerstone token. (It's an artifact with \"{T}: Add {C}. This mana can't be "
        "spent to cast a nonartifact spell.\")",
    ),
    "Command Tower": (
        "Land",
        "{T}: Add one mana of any color in your commander's color identity.",
    ),
    "Plateau": ("Land — Mountain Plains", "({T}: Add {R} or {W}.)"),
    "Wind-Scarred Crag": (
        "Land",
        "This land enters tapped.\nWhen this land enters, you gain 1 life.\n"
        "{T}: Add {R} or {W}.",
    ),
    "Arid Mesa": (
        "Land",
        "{T}, Pay 1 life, Sacrifice this land: Search your library for a Mountain or "
        "Plains card, put it onto the battlefield, then shuffle.",
    ),
}


def delta(name, state=SHORT, identity=BOROS):
    tl, ot = LANDS[name]
    return land_delta(tl, ot, identity, *state)["delta"]


class LandValueTests(unittest.TestCase):
    def test_lands_that_feed_opponents_never_beat_a_basic(self):
        for name in ("Rainbow Vale", "Forbidden Orchard"):
            for state in (SHORT, MET):
                self.assertLess(delta(name, state), 0, name)

    def test_tribal_and_filter_lands_lose_to_a_basic_when_colours_are_short(self):
        for name in ("Sliver Hive", "Hall of Tagsin"):
            self.assertLess(delta(name), 0, name)
        self.assertIn("spend_restricted", land_profile(*LANDS["Sliver Hive"], BOROS)["drawbacks"])

    def test_lotus_field_pays_for_its_ramp_with_two_lands(self):
        profile = land_profile(*LANDS["Lotus Field"], BOROS)
        self.assertIn("sacrifices_lands", profile["drawbacks"])
        self.assertLess(delta("Lotus Field", MET), 0)

    def test_on_colour_duals_beat_a_basic(self):
        self.assertGreater(delta("Command Tower"), 0)
        self.assertGreater(delta("Plateau"), 0)
        self.assertGreater(delta("Arid Mesa"), 0)

    def test_tapped_dual_only_worth_it_while_colours_are_short(self):
        self.assertGreater(delta("Wind-Scarred Crag", SHORT), 0)
        self.assertLess(delta("Wind-Scarred Crag", MET), 0)

    def test_fixing_is_worthless_in_mono_colour(self):
        self.assertLessEqual(delta("Command Tower", ({"R": 0}, {"R": 14}), ["R"]), 0)


class OntologyPairScoreTests(unittest.TestCase):
    """ONTOLOGY.md Layer 3: Σ min(supply, capacity · demand), so stacking saturates."""

    PRODUCER = {"produces": {"treasure"}}
    OUTLET = {"consumes": {"treasure"}}

    def _counts(self, producers, outlets):
        by_name = {f"p{i}": self.PRODUCER for i in range(producers)}
        by_name.update({f"o{i}": self.OUTLET for i in range(outlets)})
        return _pred_counts(by_name, {name: 1 for name in by_name})

    def test_producers_without_an_outlet_score_nothing(self):
        self.assertEqual(_ontology_pair_score(self.PRODUCER, self._counts(5, 0)), 0.0)

    def test_producers_saturate_against_outlet_capacity(self):
        first = _ontology_pair_score(self.PRODUCER, self._counts(0, 1))
        saturated = _ontology_pair_score(self.PRODUCER, self._counts(10, 1))
        self.assertGreater(first, 0.0)
        self.assertEqual(saturated, 0.0)

    def test_outlet_value_grows_with_supply(self):
        thin = _ontology_pair_score(self.OUTLET, self._counts(1, 0))
        deep = _ontology_pair_score(self.OUTLET, self._counts(6, 0))
        self.assertGreater(deep, thin)

    def test_in_deck_marginal_matches_add_marginal(self):
        counts = self._counts(2, 1)
        added = _ontology_pair_score(self.PRODUCER, self._counts(1, 1))
        self.assertEqual(_ontology_pair_score(self.PRODUCER, counts, in_deck=True), added)


class SolverLandSwapTests(unittest.TestCase):
    """Basics are the default; nonbasics enter or stay only with Δ > 0 (plan Fase 0)."""

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db = tmp.name
        conn = sqlite3.connect(self.db)
        ensure_schema(conn)
        rows = [
            ("cmd", "Boros Captain", "Legendary Creature — Human Soldier", ["R", "W"],
             "Whenever Boros Captain attacks, it gets +1/+1.", 2.0, "{R}{W}"),
            ("mtn", "Mountain", "Basic Land — Mountain", [], "({T}: Add {R}.)", 0.0, ""),
            ("pln", "Plains", "Basic Land — Plains", [], "({T}: Add {W}.)", 0.0, ""),
            ("bolt", "Lightning Bolt", "Instant", ["R"],
             "Lightning Bolt deals 3 damage to any target.", 1.0, "{R}"),
            ("helix", "Lightning Helix", "Instant", ["R", "W"],
             "Lightning Helix deals 3 damage to any target and you gain 3 life.", 2.0, "{R}{W}"),
        ] + [(f"lid{k}", name, tl, [], ot, 0.0, "") for k, (name, (tl, ot)) in enumerate(LANDS.items())]
        for card_id, name, tl, ident, ot, cmc, cost in rows:
            conn.execute(
                "INSERT INTO cards (id, name, mana_cost, cmc, oracle_text, color_identity, "
                "type_line, legalities, price_usd, price_eur) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (card_id, name, cost, cmc, ot, json.dumps(ident), tl,
                 json.dumps({"commander": "legal"}), 1.0, 1.0),
            )
        conn.commit()
        conn.close()
        self.solver = DeckSolver(self.db)
        self.solver._view_store = None

    def tearDown(self):
        os.unlink(self.db)

    def _deck(self, cards):
        deck = DeckState(commander="Boros Captain", identity=["R", "W"], cards=cards)
        deck.intent = "build"
        return deck

    def test_harmful_and_tribal_lands_are_swapped_for_basics(self):
        deck = self._deck({
            "Rainbow Vale": 1, "Forbidden Orchard": 1, "Sliver Hive": 1,
            "Mountain": 10, "Plains": 10, "Lightning Bolt": 1,
        })
        swaps = self.solver._upgrade_lands(deck, "")
        for name in ("Rainbow Vale", "Forbidden Orchard", "Sliver Hive"):
            self.assertNotIn(name, deck.cards)
        self.assertTrue(all(s["reason"] in ("nonbasic_below_basic", "nonbasic_upgrade") for s in swaps))
        self.assertEqual(deck.slot_count(), 24)

    def test_skip_reason_blocks_nonbasics_that_lose_to_a_basic(self):
        # Both colours short of their floors: fixing is worth a slot.
        deck = self._deck({"Lightning Helix": 1, "Lightning Bolt": 1})
        self.solver._rebuild_context(deck, "")
        vale = self.solver._info("Rainbow Vale")
        self.assertIsNotNone(self.solver._skip_reason(vale, deck))
        self.assertIsNone(self.solver._skip_reason(self.solver._info("Command Tower"), deck))

    def test_land_synergy_is_not_text_similarity(self):
        deck = self._deck({"Mountain": 5})
        parts = self.solver.score_breakdown(deck, "Command Tower", "")
        self.assertEqual(parts["synergy"], 0.0)


if __name__ == "__main__":
    unittest.main()
