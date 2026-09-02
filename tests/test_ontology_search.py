import json
import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from catalog import ensure_schema  # noqa: E402
from ontology.diagnose import typed_deficits  # noqa: E402
from ontology.search import (  # noqa: E402
    compile_search_intent,
    flatten_candidates,
    parse_ontology_query,
    rebuild_predicate_index,
    search_ontology,
    search_ontology_clauses,
    search_route,
)
from retrieval_text import merge_hit_maps  # noqa: E402


def _insert_card(conn, card_id, name, identity, oracle="", type_line="Creature"):
    conn.execute(
        """
        INSERT INTO cards (
            id, name, mana_cost, cmc, oracle_text, color_identity,
            type_line, legalities, keywords
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            name,
            "{R}",
            3.0,
            oracle,
            json.dumps(identity),
            type_line,
            json.dumps({"commander": "legal"}),
            "[]",
        ),
    )


def _candidate(predicate, arguments, validation_only=False):
    return {
        "kind": "validation" if validation_only else "predicate",
        "predicate": predicate,
        "arguments": arguments,
        "mapping_id": "test",
        "validation_only": validation_only,
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


class OntologySearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        ensure_schema(self.conn)
        _insert_card(
            self.conn,
            "combat-red",
            "Anonymous Combat Engine",
            ["R"],
            "Whenever you attack, untap attacking creatures.",
        )
        _insert_card(
            self.conn,
            "combat-blue",
            "Blue Combat Trick",
            ["U"],
            "Create a 1/1 blue Soldier token.",
        )
        _insert_card(
            self.conn,
            "outlet-black",
            "Anonymous Outlet",
            ["B"],
            "Pay 1 life: draw a card.",
        )
        _index_card(
            self.conn,
            "combat-red",
            "Anonymous Combat Engine",
            [
                _candidate("enables", {"capability": "extra_combat"}),
                _candidate("emits", {"event": "attack"}),
                _candidate(None, {"validation": "deck_has"}, validation_only=True),
            ],
        )
        _index_card(
            self.conn,
            "combat-blue",
            "Blue Combat Trick",
            [_candidate("enables", {"capability": "extra_combat"})],
        )
        _index_card(
            self.conn,
            "outlet-black",
            "Anonymous Outlet",
            [_candidate("enables", {"capability": "sac_outlet"})],
        )
        rebuild_predicate_index(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_parse_explicit_and_nl_families(self):
        extra = parse_ontology_query("enables:extra_combat")
        self.assertEqual(extra[0].predicate, "enables")
        self.assertEqual(extra[0].arg_value, "extra_combat")
        nl = parse_ontology_query("I want extra combat phases")
        self.assertTrue(any(c.arg_value == "extra_combat" for c in nl))
        sac = parse_ontology_query("enables:sac_outlet")
        self.assertEqual(sac[0].arg_value, "sac_outlet")

    def test_compile_extra_combat_and_protection(self):
        intent = compile_search_intent("I want extra combat and protection")
        self.assertTrue(any(c.arg_value == "extra_combat" for c in intent.clauses))
        self.assertTrue(any(c.predicate == "protects" for c in intent.clauses))
        self.assertIn("extra_combat", intent.families)
        self.assertIn("protection", intent.families)

    def test_compile_sac_outlet(self):
        intent = compile_search_intent("sac outlet")
        self.assertTrue(
            any(
                c.predicate == "enables" and c.arg_value == "sac_outlet"
                for c in intent.clauses
            )
        )

    def test_compile_draw_a_card_has_oracle_harness(self):
        intent = compile_search_intent("draw a card")
        self.assertTrue(
            any(
                c.predicate == "produces" and c.arg_value == "card_in_hand"
                for c in intent.clauses
            )
        )
        phrases = [phrase.lower() for phrase in intent.oracle_phrases]
        self.assertIn("draw a card", phrases)

    def test_compile_extra_turn_is_not_extra_combat(self):
        intent = compile_search_intent("I want an extra turn")
        self.assertFalse(any(c.arg_value == "extra_combat" for c in intent.clauses))
        self.assertNotIn("extra_combat", intent.families)

    def test_compile_grant_haste_includes_haste_grant(self):
        intent = compile_search_intent("grant haste")
        self.assertTrue(
            any(
                c.predicate == "enables" and c.arg_value == "haste_grant"
                for c in intent.clauses
            )
        )

    def test_compile_convoke_is_convoke_like(self):
        intent = compile_search_intent("convoke")
        self.assertTrue(
            any(
                c.predicate == "enables" and c.arg_value == "convoke_like"
                for c in intent.clauses
            )
        )

    def test_compile_delirium_requires_delirium(self):
        intent = compile_search_intent("delirium")
        self.assertTrue(
            any(
                c.predicate == "requires"
                and c.arg_key == "precondition"
                and c.arg_value == "delirium"
                for c in intent.clauses
            )
        )

    def test_compile_bounce_emits_bounce(self):
        intent = compile_search_intent("bounce")
        self.assertTrue(
            any(
                c.predicate == "emits" and c.arg_value == "bounce"
                for c in intent.clauses
            )
        )
        self.assertTrue(
            any(
                c.predicate == "answers" and c.arg_value == "creature"
                for c in intent.clauses
            )
        )

    def test_compile_removal_is_not_bounce(self):
        intent = compile_search_intent("removal")
        self.assertFalse(any(c.arg_value == "bounce" for c in intent.clauses))
        self.assertTrue(
            any(
                c.predicate == "answers" and c.arg_value == "creature"
                for c in intent.clauses
            )
        )

    def test_compile_hexproof_still_protects(self):
        intent = compile_search_intent("hexproof")
        self.assertTrue(any(c.predicate == "protects" for c in intent.clauses))

    def test_extra_combat_search_finds_mapped_card_without_naming_it(self):
        hits = search_ontology(self.conn, "extra combat", allowed_colors=["W", "U", "B", "R", "G"])
        names = [hit["name"] for hit in hits]
        self.assertIn("Anonymous Combat Engine", names)
        self.assertNotIn("Anonymous Outlet", names)
        self.assertTrue(all(hit["source"] == "ontology" for hit in hits))

    def test_explicit_sac_outlet_search_and_color_filter(self):
        hits = search_ontology(self.conn, "enables:sac_outlet", allowed_colors=["B"])
        names = [hit["name"] for hit in hits]
        self.assertIn("Anonymous Outlet", names)
        self.assertNotIn("Anonymous Combat Engine", names)

        red_only = search_ontology(self.conn, "extra combat", allowed_colors=["R"])
        red_names = [hit["name"] for hit in red_only]
        self.assertIn("Anonymous Combat Engine", red_names)
        self.assertNotIn("Blue Combat Trick", red_names)

    def test_validation_only_candidates_are_not_indexed(self):
        count = self.conn.execute(
            "SELECT COUNT(*) FROM ontology_predicates WHERE predicate IS NULL OR predicate = ''"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_batched_clause_search_finds_both_families(self):
        clauses = parse_ontology_query("enables:extra_combat") + parse_ontology_query(
            "enables:sac_outlet"
        )
        hits = search_ontology_clauses(
            self.conn, clauses, allowed_colors=["W", "U", "B", "R", "G"]
        )
        names = [hit["name"] for hit in hits]
        self.assertIn("Anonymous Combat Engine", names)
        self.assertIn("Anonymous Outlet", names)
        self.assertTrue(all(hit["source"] == "ontology" for hit in hits))

    def test_explicit_predicate_is_ontology_route(self):
        self.assertEqual(search_route("enables:extra_combat"), "ontology")
        self.assertEqual(search_route("enables:capability:extra_combat"), "ontology")
        self.assertEqual(search_route("enables:extra_combat enables:sac_outlet"), "ontology")
        self.assertEqual(search_route("I want extra combat"), "hybrid")
        self.assertEqual(search_route("draw a card"), "hybrid")
        self.assertEqual(search_route("enables:extra_combat and ramp"), "hybrid")

    def test_flatten_drops_leaked_color_rate_and_token_script(self):
        rows = flatten_candidates(
            "c1",
            "Leak Card",
            [
                _candidate(
                    "produces",
                    {
                        "object": "mana",
                        "color": "Combo R G",
                        "rate": "Count$Valid Artifact.YouCtrl",
                        "token_script": "c_a_treasure_sac",
                    },
                ),
                _candidate("produces", {"object": "mana", "color": "R", "rate": 2}),
            ],
        )
        keyed = {(row[2], row[3], row[4]) for row in rows}
        self.assertIn(("produces", "object", "mana"), keyed)
        self.assertIn(("produces", "color", "R"), keyed)
        self.assertIn(("produces", "rate", "2"), keyed)
        self.assertNotIn(("produces", "color", "Combo R G"), keyed)
        self.assertFalse(any(row[3] == "token_script" for row in rows))
        self.assertFalse(any(row[3] == "rate" and "Count$" in row[4] for row in rows))

    def test_typed_deficits_treasures_without_outlet(self):
        deficits = typed_deficits(
            {"produces:treasure": 9, "enables:sac_outlet": 0, "produces:token": 4}
        )
        self.assertTrue(any("treasure" in item and "0 sacrifice" in item for item in deficits))
        self.assertTrue(any("token" in item and "0 token-created" in item for item in deficits))

    def test_ontology_only_hits_survive_hybrid_merge(self):
        embedding = [{"name": "Unrelated Bear", "distance": 0.4, "source": "embedding"}]
        lexical = []
        ontology = search_ontology(self.conn, "extra combat", allowed_colors=["R"])
        merged = merge_hit_maps(merge_hit_maps(embedding, lexical), ontology)
        by_name = {hit["name"]: hit for hit in merged}
        self.assertIn("Anonymous Combat Engine", by_name)
        self.assertEqual(by_name["Anonymous Combat Engine"]["source"], "ontology")
        self.assertIn("Unrelated Bear", by_name)


if __name__ == "__main__":
    unittest.main()
