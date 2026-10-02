"""Stage 3.6: the commander's plan decides fill vs cut for unmatched producers.

A producer of fuel nobody subscribes to is junk to cut, not a hole to fill, and
`protects` is a live claim even with no matching payoff inside the 99.
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
from mana import diagnose_deck
from ontology.ablation import dead_card_names, has_live_pair, ontology_eval_metrics
from ontology.graph import (
    commander_plan_events,
    commander_plan_objects,
    flow_repair_records,
    flow_snapshot,
    offplan_hit_count,
    search_queries_from_records,
    serves_plan,
)
from ontology.search import rebuild_predicate_index
from solver import DeckSolver

VOLTRON = "Voltron Boss"
TOKEN_LORD = "Token Boss"
GUARDIAN = "Guard Plating"
BEATSTICK = "Combat Trinket"
COINERS = ("Coin Widget", "Coin Lens", "Coin Rod")
ORACLE = "A test card."


def _insert_card(conn, card_id, name, type_line, oracle=ORACLE, cmc=2.0):
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
            json.dumps(["R"]),
            type_line,
            json.dumps({"commander": "legal"}),
            0.5,
            0.5,
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


def _seed(path, commander, commander_candidates):
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    _insert_card(conn, "cmd", commander, "Legendary Creature — Human", oracle="", cmc=3.0)
    _index_card(conn, "cmd", commander, commander_candidates)
    for i, name in enumerate(COINERS, start=1):
        _insert_card(conn, f"coin{i}", name, "Artifact")
        _index_card(conn, f"coin{i}", name, [_candidate("produces", {"object": "treasure"})])
    _insert_card(conn, "guard", GUARDIAN, "Artifact — Equipment")
    _index_card(
        conn,
        "guard",
        GUARDIAN,
        [_candidate("protects", {"target_class": "commander"})],
    )
    _insert_card(conn, "stick", BEATSTICK, "Artifact — Equipment")
    _index_card(
        conn,
        "stick",
        BEATSTICK,
        [
            _candidate("produces", {"object": "treasure"}),
            _candidate("rewards", {"event": "deal_combat_damage"}),
        ],
    )
    rebuild_predicate_index(conn)
    conn.commit()
    conn.close()


class CommanderPlanTests(unittest.TestCase):
    def test_plan_is_none_without_a_commander_claim(self):
        self.assertIsNone(commander_plan_objects(None))
        self.assertIsNone(commander_plan_objects({}))
        self.assertIsNone(commander_plan_objects({"protects": {"commander"}}))

    def test_plan_reads_produces_consumes_and_event_twins(self):
        self.assertEqual(
            commander_plan_objects({"produces": {"token"}, "emits": {"etb"}}),
            frozenset({"token"}),
        )
        self.assertEqual(
            commander_plan_objects({"consumes": {"treasure"}}),
            frozenset({"treasure"}),
        )
        # rewards(token_created) subscribes to the token object.
        self.assertEqual(
            commander_plan_objects({"rewards": {"token_created"}}),
            frozenset({"token"}),
        )

    def test_offplan_fuel_asks_for_a_cut_not_a_consumer(self):
        counts = {"produces:treasure": 4}
        records = flow_repair_records(counts, frozenset())
        self.assertEqual([row["kind"] for row in records], ["offplan"])
        self.assertEqual(records[0]["repairs"], [])
        self.assertEqual(
            records[0]["cuts"],
            [{"predicate": "produces", "arg_key": "object", "arg_value": "treasure"}],
        )
        # Nothing should enter the deck to feed an off-plan producer.
        self.assertEqual(search_queries_from_records(records), [])
        self.assertEqual(offplan_hit_count({"produces": {"treasure"}}, records), 1)
        self.assertEqual(offplan_hit_count({"produces": {"token"}}, records), 0)

    def test_on_plan_fuel_still_asks_for_a_consumer(self):
        records = flow_repair_records({"produces:treasure": 4}, frozenset({"treasure"}))
        self.assertEqual([row["kind"] for row in records], ["unmatched"])
        self.assertEqual(search_queries_from_records(records), ["consumes:treasure"])
        self.assertEqual(offplan_hit_count({"produces": {"treasure"}}, records), 0)

    def test_unknown_commander_keeps_the_old_fill_behaviour(self):
        records = flow_repair_records({"produces:treasure": 4})
        self.assertEqual([row["kind"] for row in records], ["unmatched"])
        self.assertEqual(search_queries_from_records(records), ["consumes:treasure"])

    def test_permanents_are_never_idle_producers(self):
        snap = flow_snapshot({"produces:enchantment_permanent": 4}, frozenset())
        self.assertEqual(snap["unmatched_producers"], [])
        self.assertEqual(snap["offplan_producers"], [])


class ServesPlanTests(unittest.TestCase):
    def test_plan_events_come_from_emits_and_rewards(self):
        self.assertEqual(
            commander_plan_events({"rewards": {"deal_combat_damage"}, "produces": {"x"}}),
            frozenset({"deal_combat_damage"}),
        )
        self.assertEqual(commander_plan_events(None), frozenset())

    def test_a_card_on_the_plan_keeps_its_slot_despite_the_fuel(self):
        events = frozenset({"deal_combat_damage"})
        # Equipment that rewards the commander's own event, treasure on the side.
        self.assertTrue(
            serves_plan(
                {"produces": {"treasure"}, "rewards": {"deal_combat_damage"}}, events
            )
        )
        # Emitting the event the commander rewards counts too.
        self.assertTrue(serves_plan({"emits": {"deal_combat_damage"}}, events))
        self.assertTrue(serves_plan({"protects": {"commander"}}, events))
        self.assertTrue(serves_plan({"answers": {"creature"}}, frozenset()))
        self.assertFalse(serves_plan({"produces": {"treasure"}}, events))
        self.assertFalse(serves_plan({"rewards": {"etb"}}, events))


class ProtectsIsLiveTests(unittest.TestCase):
    def test_protects_pairs_with_the_opponent_not_the_deck(self):
        self.assertTrue(has_live_pair({"protects": {"commander"}}, {}))
        self.assertTrue(has_live_pair({"answers": {"board"}}, {}))
        self.assertFalse(has_live_pair({"rewards": {"etb"}}, {"produces": {"token"}}))

    def test_voltron_protection_is_not_a_dead_card(self):
        by_name = {
            GUARDIAN: {"protects": {"commander"}},
            "orphan payoff": {"rewards": {"landfall"}},
        }
        self.assertEqual(dead_card_names(by_name), ["orphan payoff"])
        metrics = ontology_eval_metrics({}, by_name)
        self.assertNotIn(GUARDIAN, metrics["dead_cards"])


class VoltronCutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db = tmp.name
        _seed(
            self.db,
            VOLTRON,
            [
                _candidate("rewards", {"event": "deal_combat_damage"}),
                _candidate("protects", {"target_class": "commander"}),
            ],
        )

    def tearDown(self):
        os.unlink(self.db)

    def _deck(self, commander=VOLTRON) -> DeckState:
        cards = {name: 1 for name in COINERS}
        cards[GUARDIAN] = 1
        cards[BEATSTICK] = 1
        return DeckState(
            commander=commander,
            identity=["R"],
            cards=cards,
            intent="improve",
            require_complete=False,
        )

    def test_diagnose_stops_asking_for_a_sac_outlet(self):
        report = diagnose_deck(self._deck(), db_path=self.db)
        flow = report.get("ontology_flow") or {}
        self.assertEqual(flow.get("plan_objects"), [])
        self.assertTrue(
            any(row.get("object") == "treasure" for row in flow.get("offplan_producers") or []),
            flow,
        )
        self.assertEqual(flow.get("unmatched_producers"), [])
        queries = report.get("ontology_queries") or []
        self.assertNotIn("enables:sac_outlet", queries)
        self.assertNotIn("consumes:treasure", queries)
        suggested = " ".join(
            str(row.get("query") or "") for row in report.get("suggested_searches") or []
        )
        self.assertNotIn("sac_outlet", suggested)
        self.assertTrue(
            any("off the commander's plan" in text for text in report.get("ontology_deficits") or []),
            report.get("ontology_deficits"),
        )

    def test_cut_prefers_the_treasure_producer_over_protection(self):
        deck = self._deck()
        solver = DeckSolver(self.db)
        solver._rebuild_context(deck, "")
        self.assertEqual(solver._ctx.get("ontology_plan"), frozenset())
        guard = solver.score_breakdown(deck, GUARDIAN, "")
        self.assertEqual(guard["ontology_offplan"], 0.0)
        # Same treasure, but this one pays the commander's event off.
        stick = solver.score_breakdown(deck, BEATSTICK, "")
        self.assertEqual(stick["ontology_offplan"], 0.0)
        for name in COINERS:
            coin = solver.score_breakdown(deck, name, "")
            self.assertGreater(coin["ontology_offplan"], 0.0)
            self.assertLess(coin["total"], guard["total"])
            self.assertLess(coin["total"], stick["total"])
        self.assertIn(solver._worst_cut(deck, "", False), set(COINERS))

    def test_a_subscribed_commander_protects_its_own_fuel(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            _seed(tmp.name, TOKEN_LORD, [_candidate("consumes", {"object": "treasure"})])
            deck = self._deck(commander=TOKEN_LORD)
            solver = DeckSolver(tmp.name)
            solver._rebuild_context(deck, "")
            self.assertEqual(solver._ctx.get("ontology_plan"), frozenset({"treasure"}))
            for name in COINERS:
                self.assertEqual(
                    solver.score_breakdown(deck, name, "")["ontology_offplan"], 0.0
                )
        finally:
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main()
