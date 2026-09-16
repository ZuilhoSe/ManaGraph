import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from ontology.graph import (  # noqa: E402
    add_predicate_to_sets,
    empty_pred_sets,
    flow_repair_records,
    flow_snapshot,
    merge_deficit_records,
    pred_sets_from_id_rows,
    predicate_signature,
    repair_hit_count,
    search_queries_from_records,
    signature_redundancy,
    signature_tokens,
)


class OntologyGraphTests(unittest.TestCase):
    def test_orphan_payoff_without_emitter(self):
        snap = flow_snapshot({"rewards:etb": 3, "emits:etb": 0, "produces:mana": 8})
        self.assertEqual(snap["orphans"], [{"event": "etb", "rewards": 3, "emits": 0}])
        self.assertEqual(snap["matched_events"], [])
        records = flow_repair_records({"rewards:etb": 3})
        self.assertEqual(records[0]["kind"], "orphan")
        self.assertEqual(
            records[0]["repairs"][0],
            {"predicate": "emits", "arg_key": "event", "arg_value": "etb"},
        )
        self.assertEqual(search_queries_from_records(records), ["emits:etb"])

    def test_starved_consumer_skips_mana(self):
        snap = flow_snapshot(
            {"consumes:treasure": 2, "produces:treasure": 0, "consumes:mana": 4}
        )
        self.assertEqual(
            snap["starved"],
            [{"object": "treasure", "consumes": 2, "produces": 0}],
        )
        self.assertFalse(any(row["object"] == "mana" for row in snap["starved"]))

    def test_merge_drops_flow_row_already_covered_by_curated(self):
        curated = [
            {
                "text": "4 treasure sources, 0 sacrifice outlets",
                "repairs": [
                    {"predicate": "enables", "arg_key": "capability", "arg_value": "sac_outlet"},
                    {"predicate": "consumes", "arg_key": "object", "arg_value": "treasure"},
                ],
            }
        ]
        extra = flow_repair_records({"produces:treasure": 4, "rewards:etb": 2})
        merged = merge_deficit_records(curated, extra)
        texts = [row["text"] for row in merged]
        self.assertIn("4 treasure sources, 0 sacrifice outlets", texts)
        self.assertFalse(any("treasure sources, 0 treasure consumers" in t for t in texts))
        self.assertTrue(any("etb payoffs" in t for t in texts))

    def test_repair_hit_count_one_per_record(self):
        records = [
            {
                "repairs": [
                    {"predicate": "enables", "arg_value": "sac_outlet"},
                    {"predicate": "consumes", "arg_value": "treasure"},
                ]
            }
        ]
        card = {"enables": {"sac_outlet"}, "consumes": {"treasure"}}
        self.assertEqual(repair_hit_count(card, records), 1)

    def test_signature_redundancy_same_wipe_same_band(self):
        wipe = {"answers": {"board"}}
        self.assertEqual(predicate_signature(wipe), frozenset({("answers", "board")}))
        self.assertEqual(signature_redundancy(wipe, wipe, 4, 4), 1.0)
        self.assertEqual(signature_redundancy(wipe, wipe, 2, 6), 0.0)

    def test_producer_and_payoff_are_not_redundant(self):
        producer = {"produces": {"token"}, "emits": {"etb", "token_created"}}
        payoff = {"rewards": {"etb"}}
        self.assertEqual(signature_redundancy(producer, payoff, 3, 3), 0.0)

    def test_mana_rocks_are_not_an_ontology_signature(self):
        rock = {"produces": {"mana"}}
        self.assertEqual(predicate_signature(rock), frozenset())
        self.assertEqual(signature_redundancy(rock, rock, 2, 2), 0.0)
        self.assertEqual(signature_tokens(rock), [])

    def test_signature_tokens_skip_rate_and_color(self):
        sets = empty_pred_sets()
        add_predicate_to_sets(sets, "produces", "object", "mana")
        add_predicate_to_sets(sets, "produces", "color", "R")
        add_predicate_to_sets(sets, "produces", "rate", "2")
        add_predicate_to_sets(sets, "emits", "event", "etb")
        self.assertEqual(signature_tokens(sets), ["emits:etb"])

    def test_pred_sets_from_id_rows_aligns_to_catalog_id(self):
        by_id = pred_sets_from_id_rows(
            [
                ("card-1", "answers", "threat_class", "board"),
                ("card-1", "produces", "object", "mana"),
            ]
        )
        self.assertEqual(by_id["card-1"]["answers"], {"board"})
        self.assertEqual(by_id["card-1"]["produces"], {"mana"})
        self.assertEqual(signature_tokens(by_id["card-1"]), ["answers:board"])


if __name__ == "__main__":
    unittest.main()
