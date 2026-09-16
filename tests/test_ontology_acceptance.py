"""Stage 3.6 acceptance: typed diagnosis and a cut that follows it.

ONTOLOGY.md: (1) diagnose_deck reports a deficit Stage 3.5 cannot express;
(2) cut picks different cards because of that deficit, not cosine.
"""

from __future__ import annotations

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
from mana import diagnose, diagnose_deck
from ontology.search import rebuild_predicate_index
from solver import DeckSolver

OUTLET = "Unrelated Widget"
VICTIM = "Treasure Chip"
TREASURES = (
    "Treasure Coil",
    "Treasure Lens",
    "Treasure Rod",
    "Treasure Disk",
)
COMMANDER = "Treasure Boss"


def _insert_card(conn, card_id, name, type_line, identity, oracle="", usd=0.5, cmc=2.0):
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
            json.dumps({"commander": "legal"}),
            usd,
            usd,
        ),
    )


def _candidate(predicate, arguments):
    return {
        "kind": "predicate",
        "predicate": predicate,
        "arguments": arguments,
        "mapping_id": "test",
        "validation_only": False,
        "evidence": {},
    }


def _index_card(conn, card_id, name, candidates):
    conn.execute(
        """
        INSERT INTO ontology_cards
          (card_id, scryfall_name, forge_match_status, forge_candidates_json, enriched_at)
        VALUES (?, ?, 'matched', ?, '2026-01-01T00:00:00Z')
        """,
        (card_id, name, json.dumps(candidates)),
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
    oracle = "A test card."
    _insert_card(
        conn,
        "cmd",
        COMMANDER,
        "Legendary Creature — Human",
        ["R"],
        oracle="",
        cmc=3.0,
    )
    for i, name in enumerate(TREASURES, start=1):
        _insert_card(conn, f"tr{i}", name, "Artifact", ["R"], oracle=oracle)
        _index_card(
            conn,
            f"tr{i}",
            name,
            [_candidate("produces", {"object": "treasure"})],
        )
    _insert_card(conn, "glue", VICTIM, "Artifact", ["R"], oracle=oracle, cmc=2.0)
    _insert_card(conn, "out", OUTLET, "Artifact", ["R"], oracle=oracle, cmc=6.0)
    _index_card(
        conn,
        "out",
        OUTLET,
        [
            _candidate("enables", {"capability": "sac_outlet"}),
            _candidate("consumes", {"object": "treasure"}),
        ],
    )
    rebuild_predicate_index(conn)
    conn.commit()
    conn.close()


class OntologyAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _seed(self.db)
        self._score_flag = os.environ.get("MANAGRAPH_ONTOLOGY_SCORE")

    def tearDown(self):
        if self._score_flag is None:
            os.environ.pop("MANAGRAPH_ONTOLOGY_SCORE", None)
        else:
            os.environ["MANAGRAPH_ONTOLOGY_SCORE"] = self._score_flag
        os.unlink(self.db)

    def _deck(self) -> DeckState:
        cards = {name: 1 for name in TREASURES}
        cards[VICTIM] = 1
        return DeckState(
            commander=COMMANDER,
            identity=["R"],
            cards=cards,
            candidate_pool={OUTLET: 1},
            intent="improve",
            require_complete=False,
        )

    def test_diagnose_reports_typed_deficit_stage35_cannot_express(self):
        deck = self._deck()
        stage35 = diagnose(
            [
                {
                    "name": name,
                    "quantity": 1,
                    "type_line": "Artifact",
                    "oracle_text": "A test card.",
                    "mana_cost": "{R}",
                    "cmc": 2.0,
                }
                for name in (*TREASURES, VICTIM)
            ],
            commander={
                "name": COMMANDER,
                "type_line": "Legendary Creature — Human",
                "oracle_text": "",
                "mana_cost": "{R}",
                "color_identity": ["R"],
            },
            identity=["R"],
            slot_count=deck.slot_count(),
            remaining_slots=deck.remaining_slots(),
        )
        stage35_text = " ".join(stage35.get("deficits") or []).lower()
        self.assertNotIn("sacrifice outlet", stage35_text)
        self.assertNotIn("treasure source", stage35_text)

        report = diagnose_deck(deck, db_path=self.db)
        typed = report.get("ontology_deficits") or []
        self.assertTrue(
            any("treasure" in item and "0 sacrifice outlets" in item for item in typed),
            typed,
        )
        self.assertTrue(any(item in (report.get("deficits") or []) for item in typed))
        records = report.get("ontology_deficit_records") or []
        self.assertTrue(records)
        predicates = {
            repair["arg_value"]
            for record in records
            for repair in record.get("repairs") or []
        }
        self.assertIn("sac_outlet", predicates)
        self.assertIn("enables:sac_outlet", report.get("ontology_queries") or [])
        suggested = report.get("suggested_searches") or []
        self.assertTrue(
            any("enables=sac_outlet" in (row.get("query") or "") for row in suggested),
            suggested,
        )
        flow = report.get("ontology_flow") or {}
        unmatched = flow.get("unmatched_producers") or []
        self.assertTrue(any(row.get("object") == "treasure" for row in unmatched), flow)

    def test_retrieve_searches_deficit_predicates(self):
        class _CaptureSearcher:
            def __init__(self):
                self.queries = []

            def search_cards_batch(self, queries=None, **_kwargs):
                self.queries.extend(queries or [])
                return []

        searcher = _CaptureSearcher()
        solver = DeckSolver(self.db, searcher=searcher)
        solver._retrieve(self._deck(), query="build a list")
        self.assertIn("enables:sac_outlet", searcher.queries)
        os.environ["MANAGRAPH_ONTOLOGY_SCORE"] = "0"
        off_solver = DeckSolver(self.db)
        off_deck = self._deck()
        off_solver._rebuild_context(off_deck, "")
        off_outlet = off_solver.score_candidate(off_deck, OUTLET, "")
        off_victim = off_solver.score_candidate(off_deck, VICTIM, "")
        self.assertLessEqual(
            off_outlet,
            off_victim,
            (off_outlet, off_victim),
        )
        off_report = off_solver.cut(self._deck(), query="")
        self.assertTrue(off_report["ok"])
        off_ins = {row["in"] for row in off_report.get("swapped") or []}
        self.assertNotIn(OUTLET, off_ins)

        os.environ["MANAGRAPH_ONTOLOGY_SCORE"] = "1"
        on_solver = DeckSolver(self.db)
        on_deck = self._deck()
        on_solver._rebuild_context(on_deck, "")
        incoming = on_solver.score_breakdown(on_deck, OUTLET, query="", skip_self=True)
        outgoing = on_solver.score_breakdown(on_deck, VICTIM, query="")
        self.assertGreater(incoming["ontology_repair"], 0.0, incoming)
        self.assertEqual(outgoing["ontology_repair"], 0.0, outgoing)
        self.assertGreater(incoming["total"], outgoing["total"])

        on_report = on_solver.cut(self._deck(), query="")
        self.assertTrue(on_report["ok"])
        swapped = on_report.get("swapped") or []
        self.assertTrue(swapped, on_report)
        self.assertEqual(swapped[0]["in"], OUTLET)
        self.assertNotEqual(off_report.get("swapped"), swapped)


WIPE_A = "Quiet Field"
WIPE_B = "Silent Horizon"
CLONE_GLUE = "Nameless Widget"
UNIQUE = "Combat Coil"


def _seed_clones(path):
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
        conn, "cmd2", COMMANDER, "Legendary Creature — Human", ["R"], oracle="", cmc=3.0
    )
    for card_id, name in (("wa", WIPE_A), ("wb", WIPE_B)):
        _insert_card(conn, card_id, name, "Artifact", ["R"], oracle="", cmc=4.0)
        _index_card(
            conn,
            card_id,
            name,
            [_candidate("answers", {"threat_class": "board"})],
        )
    _insert_card(conn, "glue2", CLONE_GLUE, "Artifact", ["R"], oracle="", cmc=4.0)
    _insert_card(conn, "uniq", UNIQUE, "Artifact", ["R"], oracle="", cmc=4.0)
    _index_card(
        conn,
        "uniq",
        UNIQUE,
        [_candidate("protects", {"target_class": "commander"})],
    )
    rebuild_predicate_index(conn)
    conn.commit()
    conn.close()


class OntologySignatureCutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _seed_clones(self.db)
        self._score_flag = os.environ.get("MANAGRAPH_ONTOLOGY_SCORE")

    def tearDown(self):
        if self._score_flag is None:
            os.environ.pop("MANAGRAPH_ONTOLOGY_SCORE", None)
        else:
            os.environ["MANAGRAPH_ONTOLOGY_SCORE"] = self._score_flag
        os.unlink(self.db)

    def _deck(self) -> DeckState:
        return DeckState(
            commander=COMMANDER,
            identity=["R"],
            cards={WIPE_A: 1, WIPE_B: 1, CLONE_GLUE: 1},
            candidate_pool={UNIQUE: 1},
            intent="improve",
            require_complete=False,
        )

    def test_cut_swaps_duplicate_signature_for_distinct_one(self):
        os.environ["MANAGRAPH_ONTOLOGY_SCORE"] = "1"
        solver = DeckSolver(self.db)
        deck = self._deck()
        solver._rebuild_context(deck, "")
        clone = solver.score_breakdown(deck, WIPE_A, query="")
        unique = solver.score_breakdown(deck, UNIQUE, query="", skip_self=True)
        self.assertEqual(clone["redundancy_kind"], "ontology")
        self.assertEqual(clone["redundancy"], 1.0)
        self.assertEqual(clone["redundancy_with"], WIPE_B)
        self.assertLess(unique["redundancy"], clone["redundancy"])
        self.assertGreater(unique["total"], clone["total"])

        os.environ["MANAGRAPH_ONTOLOGY_SCORE"] = "0"
        off = DeckSolver(self.db)
        off_deck = self._deck()
        off._rebuild_context(off_deck, "")
        off_clone = off.score_breakdown(off_deck, WIPE_A, query="")
        self.assertNotEqual(off_clone["redundancy_kind"], "ontology")
        self.assertLess(off_clone["redundancy"], 1.0)

        os.environ["MANAGRAPH_ONTOLOGY_SCORE"] = "1"
        report = solver.cut(self._deck(), query="")
        self.assertTrue(report["ok"])
        swapped = report.get("swapped") or []
        self.assertTrue(swapped, report)
        self.assertEqual(swapped[0]["in"], UNIQUE)
        self.assertIn(swapped[0]["out"], {WIPE_A, WIPE_B})


if __name__ == "__main__":
    unittest.main()
