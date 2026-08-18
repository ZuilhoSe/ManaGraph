"""Class-level fill rules. Synthetic oracle only — no printed card names."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema
from deck_state import DeckState
from mana import produces_mana
from roles import classify_roles, token_classes
from solver import DeckSolver


def _insert(
    conn,
    card_id,
    name,
    type_line,
    identity,
    oracle="",
    cmc=1.0,
    mana_cost="{R}",
    keywords=None,
):
    conn.execute(
        """
        INSERT INTO cards (
            id, name, mana_cost, cmc, oracle_text, color_identity,
            type_line, legalities, price_usd, price_eur, keywords
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            name,
            mana_cost,
            cmc,
            oracle,
            json.dumps(identity),
            type_line,
            json.dumps({"commander": "legal"}),
            1.0,
            1.0,
            json.dumps(keywords or []),
        ),
    )


def _seed(path):
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            card_name TEXT PRIMARY KEY,
            total_quantity INTEGER,
            allocations TEXT
        )
        """
    )
    _insert(
        conn, "cmd", "Alpha Leader",
        "Legendary Creature — Warrior", ["R"],
        oracle="Create two 1/1 red creature tokens.",
        cmc=3, mana_cost="{1}{R}{R}",
    )
    _insert(
        conn, "typed", "Typed Source",
        "Basic Land — Mountain", [],
        oracle="", cmc=0, mana_cost="",
    )
    _insert(
        conn, "addline", "Add Source",
        "Land", [],
        oracle="{T}: Add {R}.", cmc=0, mana_cost="",
    )
    _insert(
        conn, "grant", "Type Grant",
        "Legendary Land", [],
        oracle="Each land is a Forest in addition to its other land types.",
        cmc=0, mana_cost="",
    )
    _insert(
        conn, "loot", "Loot Land",
        "Land", [],
        oracle="{T}: Draw two cards, then discard three cards.",
        cmc=0, mana_cost="",
    )
    _insert(
        conn, "drawland", "Draw Source",
        "Land", ["R"],
        oracle="{T}: Add {R}. Draw a card.",
        cmc=0, mana_cost="",
    )
    _insert(
        conn, "payoff", "Token Payoff",
        "Enchantment", ["R"],
        oracle="Creature tokens you control get +1/+1.",
        cmc=2, mana_cost="{1}{R}",
    )
    conn.commit()
    conn.close()


class InvariantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _seed(self.db)
        self.solver = DeckSolver(self.db)
        self.solver._view_store = None

    def tearDown(self):
        os.unlink(self.db)

    def test_retrieval_distance_is_not_synergy(self):
        deck = DeckState(commander="Alpha Leader", identity=["R"])
        self.solver._rebuild_context(deck, "draw a card")
        info = self.solver._info("Loot Land")
        info["_distance"] = 0.2
        info["_distance_query"] = "draw a card"
        br = self.solver.score_breakdown(deck, "Loot Land", "draw a card")
        self.assertIsNotNone(br["chroma_synergy"])
        self.assertLess(br["synergy"], br["chroma_synergy"])
        self.assertAlmostEqual(br["synergy"], br["jaccard"], places=3)

    def test_land_in_99_must_produce_mana(self):
        self.assertTrue(produces_mana("Basic Land — Mountain", ""))
        self.assertTrue(produces_mana("Land", "{T}: Add {R}."))
        self.assertTrue(
            produces_mana(
                "Legendary Land",
                "Each land is a Forest in addition to its other land types.",
            )
        )
        self.assertFalse(
            produces_mana("Land", "{T}: Draw two cards, then discard three cards.")
        )
        deck = DeckState(
            commander="Alpha Leader",
            identity=["R"],
            candidate_pool={"Loot Land": 1, "Typed Source": 40},
        )
        self.solver.fill(deck, retrieve=False, max_adds=3)
        self.assertNotIn("Loot Land", deck.cards)
        self.assertIn("Typed Source", deck.cards)

    def test_role_is_net_effect(self):
        self.assertIn(
            "draw",
            classify_roles("Sorcery", "Draw two cards, then discard two cards."),
        )
        self.assertNotIn(
            "draw",
            classify_roles("Land", "{T}: Draw two cards, then discard three cards."),
        )

    def test_quotas_add_not_max(self):
        deck = DeckState(
            commander="Alpha Leader",
            identity=["R"],
            cards={"Typed Source": 38},
        )
        br = self.solver.score_breakdown(deck, "Draw Source", "")
        self.assertIn("land", br["roles"])
        self.assertIn("draw", br["roles"])
        self.assertAlmostEqual(br["role_score"], 1.0, places=3)
        self.assertLess(br["role_bonuses"]["land"], 0)
        self.assertGreater(br["role_bonuses"]["draw"], 0)

    def test_token_producer_aligns_with_payoff_not_creature_type(self):
        self.assertIn(
            "token_producer",
            token_classes("Legendary Creature — Warrior", "Create two 1/1 red creature tokens."),
        )
        self.assertIn(
            "token_payoff",
            token_classes("Enchantment", "Creature tokens you control get +1/+1."),
        )
        self.assertNotIn(
            "token_payoff",
            token_classes("Creature — Warrior", "This creature gets +1/+1."),
        )
        deck = DeckState(commander="Alpha Leader", identity=["R"])
        br = self.solver.score_breakdown(deck, "Token Payoff", "")
        self.assertGreater(br["token_align"], 0)

    def test_keyword_maps_to_role_class(self):
        self.assertIn("threat", classify_roles("Artifact", "Equip {1}.", keywords=["Haste"]))
        self.assertIn("ramp", classify_roles("Enchantment", "At the beginning of your upkeep.", keywords=["Treasure"]))


if __name__ == "__main__":
    unittest.main()
