"""Fase 0 (zuilho_plans/economia-de-operadores.md): land quality and flow saturation.

Oracle texts below are the real cards' text; the Boros cases reproduce the
failure seen in data/deck_lightning_army_of_one.json (Rainbow Vale, Lotus
Field, Sliver Hive, Heap Gate... with one Mountain and one Plains).
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema
from deck_analysis.land_value import land_delta, parse_land
from deck_state import DeckState
from solver import DeckSolver, _flow_counts, _ontology_pair_score, ONTOLOGY_PAIR_WEIGHT

BOROS = ["R", "W"]

LANDS = {
    "Rainbow Vale": "{T}: Add one mana of any color. An opponent gains control of Rainbow Vale at the beginning of the next end step.",
    "Lotus Field": "Hexproof\nLotus Field enters tapped.\nWhen Lotus Field enters, sacrifice two lands.\n{T}: Add three mana of any one color.",
    "Sliver Hive": "{T}: Add {C}.\n{T}: Add one mana of any color. Spend this mana only to cast a Sliver spell.\n{5}, {T}: Create a 1/1 colorless Sliver creature token. Activate only if you control a Sliver.",
    "Forbidden Orchard": "{T}: Add one mana of any color.\nWhenever you tap Forbidden Orchard for mana, target opponent creates a 1/1 colorless Spirit creature token.",
    "Heap Gate": "{T}: Add {C}.\n{1}, {T}: Add one mana of any color.\n{1}, {T}, Tap an untapped Gate you control: Create a Treasure token.",
    "Hall of Tagsin": "{T}: Add {C}.\n{1}, {T}: Add one mana of any color.\n{4}, {T}: Create a tapped Powerstone token.",
    "Undiscovered Paradise": "{T}: Add one mana of any color. During your next untap step, as you untap your permanents, return Undiscovered Paradise to its owner's hand.",
    "Command Tower": "{T}: Add one mana of any color in your commander's color identity.",
    "Sacred Foundry": "({T}: Add {R} or {W}.)\nAs Sacred Foundry enters, you may pay 2 life. If you don't, it enters tapped.",
    "Battlefield Forge": "{T}: Add {C}.\n{T}: Add {R} or {W}. Battlefield Forge deals 1 damage to you.",
    "Boros Garrison": "Boros Garrison enters tapped.\nWhen Boros Garrison enters, return a land you control to its owner's hand.\n{T}: Add {R}{W}.",
    "Arid Mesa": "{T}, Pay 1 life, Sacrifice Arid Mesa: Search your library for a Mountain or Plains card, put it onto the battlefield, then shuffle.",
}
TYPE_LINES = {"Sacred Foundry": "Land — Mountain Plains"}
BAD_FOR_BOROS = [
    "Rainbow Vale", "Lotus Field", "Sliver Hive", "Forbidden Orchard",
    "Heap Gate", "Hall of Tagsin", "Undiscovered Paradise",
]
GOOD_FOR_BOROS = ["Command Tower", "Sacred Foundry", "Battlefield Forge", "Boros Garrison", "Arid Mesa"]


def _info(name):
    return {"name": name, "type_line": TYPE_LINES.get(name, "Land"), "oracle_text": LANDS[name]}


class LandValueTests(unittest.TestCase):
    def test_bad_lands_lose_to_a_basic(self):
        for name in BAD_FOR_BOROS:
            with self.subTest(name=name):
                self.assertLessEqual(land_delta(_info(name), BOROS)["delta"], 0.0)

    def test_fixing_lands_beat_a_basic(self):
        for name in GOOD_FOR_BOROS:
            with self.subTest(name=name):
                self.assertGreater(land_delta(_info(name), BOROS)["delta"], 0.0)

    def test_untapped_dual_beats_tapped_bounce_dual(self):
        foundry = land_delta(_info("Sacred Foundry"), BOROS)["delta"]
        tower = land_delta(_info("Command Tower"), BOROS)["delta"]
        self.assertGreaterEqual(tower, foundry)

    def test_fixing_is_worthless_in_mono_color(self):
        self.assertLessEqual(land_delta(_info("Command Tower"), ["R"])["delta"], 0.0)

    def test_basic_is_the_reference(self):
        out = land_delta({"name": "Mountain", "type_line": "Basic Land — Mountain"}, BOROS)
        self.assertTrue(out["basic"])
        self.assertEqual(out["delta"], 0.0)

    def test_parser_reads_drawbacks(self):
        self.assertTrue(parse_land("Rainbow Vale", "Land", LANDS["Rainbow Vale"], BOROS).control_loss)
        self.assertEqual(parse_land("Lotus Field", "Land", LANDS["Lotus Field"], BOROS).sac_lands, 2)
        self.assertTrue(parse_land("Boros Garrison", "Land", LANDS["Boros Garrison"], BOROS).bounce)
        self.assertTrue(parse_land("Arid Mesa", "Land", LANDS["Arid Mesa"], BOROS).is_fetch)
        hive = parse_land("Sliver Hive", "Land", LANDS["Sliver Hive"], BOROS)
        self.assertEqual(hive.colors, set())

    def test_generic_share_scales_fixing(self):
        heavy_colored = {"R": 30, "W": 30, "colored": 60, "generic": 0}
        heavy_generic = {"R": 10, "W": 10, "colored": 20, "generic": 80}
        a = land_delta(_info("Command Tower"), BOROS, heavy_colored)["delta"]
        b = land_delta(_info("Command Tower"), BOROS, heavy_generic)["delta"]
        self.assertGreater(a, b)


def _insert(conn, card_id, name, type_line, identity, oracle="", cmc=0.0, cost="", usd=0.5):
    conn.execute(
        """
        INSERT INTO cards (
            id, name, mana_cost, cmc, oracle_text, color_identity,
            type_line, legalities, price_usd, price_eur
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (card_id, name, cost, cmc, oracle, json.dumps(identity), type_line,
         json.dumps({"commander": "legal"}), usd, usd),
    )


def _seed(path):
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS inventory (card_name TEXT PRIMARY KEY, total_quantity INTEGER, allocations TEXT)"
    )
    _insert(conn, "cmd", "Test Boros Commander", "Legendary Creature — Human Soldier", BOROS,
            oracle="Equipped creatures you control have double strike.", cmc=3, cost="{1}{R}{W}")
    _insert(conn, "mtn", "Mountain", "Basic Land — Mountain", [], usd=0.01)
    _insert(conn, "pln", "Plains", "Basic Land — Plains", [], usd=0.01)
    for name, text in LANDS.items():
        _insert(conn, name.lower().replace(" ", "_"), name, TYPE_LINES.get(name, "Land"), [], oracle=text)
    for i in range(70):
        color = "R" if i % 2 else "W"
        _insert(conn, f"spell{i}", f"Test Spell {i}", "Creature — Human Soldier", [color],
                oracle="Equipped creature gets +1/+1.", cmc=2, cost=f"{{1}}{{{color}}}")
    conn.commit()
    conn.close()


class SolverLandQualityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db = tmp.name
        _seed(self.db)
        self.solver = DeckSolver(self.db)
        self.solver._view_store = None

    def tearDown(self):
        os.unlink(self.db)

    def test_fill_prefers_basics_over_bad_nonbasics(self):
        pool = {f"Test Spell {i}": 1 for i in range(70)}
        pool.update({name: 1 for name in LANDS})
        deck = DeckState(
            commander="Test Boros Commander",
            identity=BOROS,
            candidate_pool=pool,
            intent="build",
        )
        self.solver.fill(deck, retrieve=False, complete_fallback=True)
        names = set(deck.cards)
        for bad in BAD_FOR_BOROS:
            self.assertNotIn(bad, names)
        basics = deck.cards.get("Mountain", 0) + deck.cards.get("Plains", 0)
        self.assertGreaterEqual(basics, 20)
        self.assertGreater(deck.cards.get("Mountain", 0), 5)
        self.assertGreater(deck.cards.get("Plains", 0), 5)
        for good in GOOD_FOR_BOROS:
            self.assertIn(good, names)

    def test_solve_swaps_bad_lands_already_in_the_deck(self):
        cards = {f"Test Spell {i}": 1 for i in range(62)}
        cards.update({name: 1 for name in BAD_FOR_BOROS})
        cards["Mountain"] = 15
        cards["Plains"] = 15
        deck = DeckState(
            commander="Test Boros Commander",
            identity=BOROS,
            cards=cards,
            intent="build",
        )
        report = self.solver.solve(deck, query="", fill_to_99=False)
        swaps = (report.get("fill") or {}).get("land_basic_swaps") or []
        self.assertEqual({s["out"] for s in swaps}, set(BAD_FOR_BOROS))
        for bad in BAD_FOR_BOROS:
            self.assertNotIn(bad, deck.cards)
        self.assertEqual(deck.slot_count(), 99)

    def test_text_synergy_never_prices_a_land(self):
        deck = DeckState(commander="Test Boros Commander", identity=BOROS)
        br = self.solver.score_breakdown(deck, "Rainbow Vale", "")
        self.assertLessEqual(br["land_delta"], 0.0)
        info = self.solver._info("Rainbow Vale")
        self.assertIn("land worse than a basic", self.solver._skip_reason(info, deck) or "")


class FlowSaturationTests(unittest.TestCase):
    def test_event_flow_is_bilinear_with_diminishing_returns(self):
        payoff = {"rewards": {"etb"}}
        emitter = {"emits": {"etb"}}
        one = _flow_counts({"payoff": payoff})
        two = _flow_counts({"payoff": payoff, "e1": emitter})
        three = _flow_counts({"payoff": payoff, "e1": emitter, "e2": emitter})
        first = _ontology_pair_score(emitter, one)
        second = _ontology_pair_score(emitter, two)
        third = _ontology_pair_score(emitter, three)
        self.assertGreater(first, second)
        self.assertGreater(second, third)
        self.assertGreater(third, 0.0)
        # A second payoff on an existing emitter is a real interaction (broadcast).
        self.assertGreater(_ontology_pair_score(payoff, two), 0.0)

    def test_object_flow_saturates(self):
        maker = {"produces": {"treasure"}}
        outlet = {"consumes": {"treasure"}}
        flow = _flow_counts({"outlet": outlet, "m1": maker})
        # One Treasure is spent once: a second maker or a second outlet adds nothing.
        self.assertEqual(_ontology_pair_score(maker, flow), 0.0)
        self.assertEqual(_ontology_pair_score(outlet, flow), 0.0)
        # With a spare Treasure maker, the outlet is matched again.
        spare = _flow_counts({"outlet": outlet, "m1": maker, "m2": maker})
        self.assertEqual(_ontology_pair_score(outlet, spare), ONTOLOGY_PAIR_WEIGHT)

    def test_predicate_count_is_not_rewarded_without_demand(self):
        wordy = {"emits": {"etb", "death", "attack", "draw"}}
        flow = _flow_counts({"payoff": {"rewards": {"etb"}}})
        self.assertAlmostEqual(_ontology_pair_score(wordy, flow), ONTOLOGY_PAIR_WEIGHT)
        self.assertEqual(_ontology_pair_score(wordy, _flow_counts({})), 0.0)

    def test_skip_self_removes_own_contribution(self):
        emitter = {"emits": {"etb"}}
        flow = _flow_counts({"payoff": {"rewards": {"etb"}}, "e1": emitter})
        self.assertAlmostEqual(_ontology_pair_score(emitter, flow, skip_self=True), ONTOLOGY_PAIR_WEIGHT)


if __name__ == "__main__":
    unittest.main()
