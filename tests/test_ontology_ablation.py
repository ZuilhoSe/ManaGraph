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
from ontology.ablation import (
    geometry_mode,
    ontology_eval_metrics,
    ontology_score_enabled,
    resolve_ablation,
)
from solver import DeckSolver


def _insert_card(conn, card_id, name, type_line="Creature", identity=None, oracle="", cmc=2.0):
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
            json.dumps(identity or ["R"]),
            type_line,
            json.dumps({"commander": "legal"}),
            0.5,
            0.5,
        ),
    )


def _seed(path):
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    _insert_card(
        conn,
        "krenko",
        "Krenko, Mob Boss",
        "Legendary Creature — Goblin Warrior",
        ["R"],
        "Create X 1/1 red Goblin creature tokens.",
        4,
    )
    _insert_card(conn, "warchief", "Goblin Warchief", "Creature — Goblin", ["R"], cmc=3)
    _insert_card(
        conn,
        "kumano",
        "Kumano Faces Kakkazan // Etching of Kumano",
        "Enchantment — Saga // Enchantment Creature — Human Shaman",
        ["R"],
        cmc=1,
    )
    conn.commit()
    conn.close()


class OntologyAblationTests(unittest.TestCase):
    def setUp(self):
        for key in (
            "MANAGRAPH_ONTOLOGY_SCORE",
            "MANAGRAPH_ONTOLOGY_GEOMETRY",
            "MANAGRAPH_ABLATION",
        ):
            os.environ.pop(key, None)

    def test_rows_switch_retrieve_score_cut(self):
        a = resolve_ablation("A")
        d = resolve_ablation("D")
        e = resolve_ablation("E")
        self.assertEqual(a.retrieval, "minilm")
        self.assertEqual(a.cut, "knn")
        self.assertEqual(a.geometry, "concat")
        self.assertFalse(ontology_score_enabled(a))
        self.assertEqual(d.retrieval, "hybrid")
        self.assertEqual(d.cut, "signature")
        self.assertTrue(ontology_score_enabled(d))
        self.assertEqual(e.retrieval, "ontology")
        self.assertEqual(e.geometry, "predicates")
        self.assertEqual(geometry_mode(e), "predicates")
        self.assertEqual(resolve_ablation(None).row, "D")

    def test_metrics_orphan_starved_dead_and_answers(self):
        diagnosis = {
            "ontology_counts": {
                "rewards:etb": 3,
                "emits:etb": 0,
                "consumes:treasure": 2,
                "produces:treasure": 0,
                "answers:board": 1,
            },
            "ontology_flow": {
                "orphans": [{"event": "etb", "rewards": 3, "emits": 0}],
                "starved": [{"object": "treasure", "consumes": 2, "produces": 0}],
                "matched_events": [],
                "answers": {"board": 1},
            },
        }
        by_name = {
            "payoff": {"rewards": {"etb"}, "emits": set()},
            "emitter": {"emits": set(), "rewards": set()},
        }
        metrics = ontology_eval_metrics(diagnosis, by_name)
        self.assertEqual(metrics["matched_pair_coverage"], 0.0)
        self.assertEqual(metrics["orphan_rate"], 1.0)
        self.assertEqual(metrics["starved_rate"], 1.0)
        self.assertGreater(metrics["dead_card_rate"], 0.0)
        self.assertIn("payoff", metrics["dead_cards"])
        self.assertAlmostEqual(metrics["answer_class_coverage"], 1 / 6)
        self.assertEqual(metrics["answer_classes"]["board"], 1)

    def test_row_a_uses_concat_and_knn_not_multiview(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            _seed(tmp.name)
            solver = DeckSolver(tmp.name, ablation="A")
            solver._view_store = None
            solver._views = {
                "krenko": {
                    "oracle": np.array([1.0, 0.0]),
                    "type": np.array([1.0, 0.0]),
                },
                "warchief": {
                    "oracle": np.array([0.9, 0.1]),
                    "type": np.array([0.95, 0.05]),
                },
                "kumano": {
                    "oracle": np.array([0.2, 0.8]),
                    "type": np.array([0.0, 1.0]),
                },
            }
            solver._emb = {
                "krenko": np.array([1.0, 0.0]),
                "warchief": np.array([0.2, 0.8]),
                "kumano": np.array([0.99, 0.01]),
            }
            deck = DeckState(
                commander="Krenko, Mob Boss",
                identity=["R"],
                cards={"Goblin Warchief": 1, "Kumano Faces Kakkazan // Etching of Kumano": 1},
            )
            solver._rebuild_context(deck, "goblin tokens")
            goblin = solver.score_breakdown(deck, "Goblin Warchief", "goblin tokens")
            saga = solver.score_breakdown(
                deck, "Kumano Faces Kakkazan // Etching of Kumano", "goblin tokens"
            )
            self.assertGreater(saga["geometry"], goblin["geometry"])
            self.assertEqual(goblin["redundancy_kind"], "knn")
            self.assertEqual(goblin["ontology_pair"], 0.0)
            self.assertEqual(goblin["ontology_repair"], 0.0)
        finally:
            os.unlink(tmp.name)

    def test_row_e_retrieve_is_ontology_filters_only(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            _seed(tmp.name)

            class _Capture:
                def __init__(self):
                    self.calls = []

                def search_cards_batch(self, queries=None, **kwargs):
                    self.calls.append({"queries": list(queries or []), **kwargs})
                    return []

            repairs = [
                {
                    "repairs": [
                        {
                            "predicate": "enables",
                            "arg_key": "capability",
                            "arg_value": "sac_outlet",
                        }
                    ]
                }
            ]
            deck = DeckState(commander="Krenko, Mob Boss", identity=["R"])
            capture_e = _Capture()
            solver_e = DeckSolver(tmp.name, searcher=capture_e, ablation="E")
            solver_e._ctx = {"ontology_repairs": repairs}
            solver_e._retrieve(deck, "draw a card")
            e_call = capture_e.calls[0]
            self.assertTrue(e_call.get("ontology_only"))
            self.assertIn("enables:sac_outlet", e_call["queries"])
            self.assertFalse(any("draw a card" == q.lower() for q in e_call["queries"]))

            capture_a = _Capture()
            solver_a = DeckSolver(tmp.name, searcher=capture_a, ablation="A")
            solver_a._ctx = {"ontology_repairs": repairs}
            solver_a._retrieve(deck, "draw a card")
            a_call = capture_a.calls[0]
            self.assertFalse(a_call.get("hybrid"))
            self.assertNotIn("enables:sac_outlet", a_call["queries"])
            self.assertIn("draw a card", a_call["queries"])
        finally:
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
