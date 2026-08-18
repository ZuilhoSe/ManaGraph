import json
import os
import sqlite3
import sys
import tempfile
import unittest

import numpy as np

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema
from deck_state import DeckState
from geometry import chroma_metadata, identity_where, knn_indices
from solver import DeckSolver


def _insert_card(conn, card_id, name, type_line, identity, oracle="", legal="legal", usd=1.0, cmc=1.0):
    conn.execute(
        """
        INSERT INTO cards (
            id, name, mana_cost, cmc, oracle_text, color_identity,
            type_line, legalities, price_usd, price_eur
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            name,
            "{R}",
            cmc,
            oracle,
            json.dumps(identity),
            type_line,
            json.dumps({"commander": legal}),
            usd,
            usd,
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
    _insert_card(
        conn, "krenko", "Krenko, Mob Boss",
        "Legendary Creature — Goblin Warrior", ["R"],
        oracle="Create X 1/1 red Goblin creature tokens.", cmc=4,
    )
    _insert_card(
        conn, "warchief", "Goblin Warchief", "Creature — Goblin", ["R"],
        oracle="Goblin creatures you control get +1/+1 and have haste.", cmc=3,
    )
    _insert_card(
        conn, "kumano", "Kumano Faces Kakkazan // Etching of Kumano",
        "Enchantment — Saga // Enchantment Creature — Human Shaman", ["R"],
        oracle="This Saga deals 1 damage to each opponent.", cmc=1,
    )
    _insert_card(
        conn, "counter", "Counterspell", "Instant", ["U"],
        oracle="Counter target spell.", cmc=2,
    )
    conn.commit()
    conn.close()


class Stage3Tests(unittest.TestCase):
    def test_identity_where_red_excludes_blue(self):
        where = identity_where(["R"])
        self.assertIn("$and", where)
        keys = {list(c.keys())[0] for c in where["$and"]}
        self.assertEqual(keys, {"ci_w", "ci_u", "ci_b", "ci_g"})
        self.assertNotIn("ci_r", keys)

    def test_identity_where_five_color_is_open(self):
        self.assertIsNone(identity_where(["W", "U", "B", "R", "G"]))

    def test_chroma_metadata_bits(self):
        meta = chroma_metadata("Lightning Bolt", ["R"], 1, "Instant")
        self.assertEqual(meta["ci_r"], 1)
        self.assertEqual(meta["ci_u"], 0)
        self.assertEqual(meta["is_creature"], 0)
        self.assertEqual(meta["cmc"], 1.0)

    def test_knn_excludes_self(self):
        vecs = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]])
        idx = knn_indices(vecs, k=1)
        self.assertEqual(idx.shape, (3, 1))
        self.assertNotEqual(idx[0, 0], 0)
        self.assertEqual(int(idx[0, 0]), 1)

    def test_geometry_outranks_name_proximity(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            _seed(tmp.name)
            solver = DeckSolver(tmp.name)
            cmd = np.array([1.0, 0.0, 0.0])
            solver._emb = {
                "krenko": cmd,
                "warchief": np.array([0.95, 0.05, 0.0]),
                "kumano": np.array([0.1, 0.0, 0.99]),
            }
            deck = DeckState(commander="Krenko, Mob Boss", identity=["R"])
            solver._rebuild_context(deck, "goblin tokens")
            goblin = solver.score_breakdown(deck, "Goblin Warchief", "goblin tokens")
            saga = solver.score_breakdown(
                deck, "Kumano Faces Kakkazan // Etching of Kumano", "goblin tokens"
            )
            self.assertGreater(goblin["geometry"], saga["geometry"])
            self.assertGreater(goblin["synergy"], saga["synergy"])
            self.assertGreater(goblin["geometry"], 0.9)
            self.assertLess(saga["geometry"], 0.2)
        finally:
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
