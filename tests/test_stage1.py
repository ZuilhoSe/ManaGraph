import json
import os
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema, mana_cost_from_scryfall, oracle_text_from_scryfall
from deck_state import DeckState, extract_json, infer_intent, infer_task, proposal_has_work
from rules_validator import CommanderValidator
from supervisor_agent import deterministic_gate


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
    _insert_card(conn, "krenko", "Krenko, Mob Boss", "Legendary Creature — Goblin Warrior", ["R"], cmc=4)
    _insert_card(conn, "sol", "Sol Ring", "Artifact", [], usd=2.0, cmc=1)
    _insert_card(conn, "mtn", "Mountain", "Basic Land — Mountain", [], usd=0.01, cmc=0)
    _insert_card(conn, "bolt", "Lightning Bolt", "Instant", ["R"], usd=1.0, cmc=1)
    _insert_card(
        conn, "swarm", "Test Swarm", "Creature — Goblin", ["R"],
        oracle="A deck can have any number of cards named Test Swarm.", usd=0.5, cmc=2
    )
    _insert_card(conn, "counter", "Counterspell", "Instant", ["U"], usd=1.0, cmc=2)
    _insert_card(conn, "lotus", "Black Lotus", "Artifact", [], legal="banned", usd=10000.0, cmc=0)
    _insert_card(conn, "bear", "Grizzly Bears", "Creature — Bear", ["G"], usd=0.2, cmc=2)
    _insert_card(conn, "elf", "Llanowar Elves", "Creature — Elf Druid", ["G"], usd=0.3, cmc=1)
    _insert_card(conn, "split", "Fire // Ice", "Instant", ["U", "R"], usd=1.5, cmc=2)
    conn.execute(
        "INSERT INTO inventory VALUES (?, ?, ?)",
        ("Sol Ring", 1, json.dumps({"free_pool": 1})),
    )
    conn.commit()
    conn.close()


class Stage1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _seed(self.db)
        self.validator = CommanderValidator(self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_extract_json_fenced(self):
        text = "Sure.\n```json\n{\"delta\": {\"add\": [{\"name\": \"Sol Ring\"}]}}\n```"
        parsed = extract_json(text)
        self.assertEqual(parsed["delta"]["add"][0]["name"], "Sol Ring")

    def test_delta_add_remove_and_strip_commander(self):
        deck = DeckState(commander="Krenko, Mob Boss")
        deck.apply_delta(
            {
                "identity": ["R"],
                "add": [{"name": "Sol Ring", "quantity": 1}, {"name": "Krenko, Mob Boss", "quantity": 1}],
            }
        )
        self.assertNotIn("Krenko, Mob Boss", deck.cards)
        deck.apply_delta({"remove": [{"name": "sol ring", "quantity": 1}]})
        self.assertEqual(deck.slot_count(), 0)

    def test_substitute_keeps_slot_count(self):
        deck = DeckState(commander="Krenko, Mob Boss", cards={"Sol Ring": 1, "Mountain": 35})
        deck.apply_delta(
            {
                "intent": "substitute",
                "delta": {
                    "substitute": [
                        {
                            "out": "Sol Ring",
                            "in": "Lightning Bolt",
                            "quantity": 1,
                            "reason": "same slot, more goblin synergy",
                        }
                    ]
                },
            }
        )
        self.assertEqual(deck.slot_count(), 36)
        self.assertNotIn("Sol Ring", deck.cards)
        self.assertEqual(deck.cards["Lightning Bolt"], 1)
        self.assertEqual(deck.intent, "substitute")
        self.assertEqual(len(deck.last_delta["substituted"]), 1)

    def test_substitute_missing_out_does_not_add(self):
        deck = DeckState(commander="Krenko, Mob Boss", cards={"Mountain": 10})
        deck.apply_delta(
            {"delta": {"substitute": [{"out": "Sol Ring", "in": "Lightning Bolt"}]}}
        )
        self.assertNotIn("Lightning Bolt", deck.cards)
        self.assertEqual(deck.slot_count(), 10)
        self.assertEqual(len(deck.last_delta["failed_substitutions"]), 1)

    def test_infer_improve_and_substitute_intents(self):
        self.assertEqual(infer_intent("upgrade the worst cards in this list"), "improve")
        self.assertEqual(infer_intent("replace Sol Ring with something cheaper"), "substitute")
        self.assertEqual(infer_intent("build a deck from scratch"), "build")
        self.assertEqual(infer_intent("add interaction", has_cards=True), "improve")
        task = infer_task("suggest better cards for my Krenko list")
        self.assertEqual(task["intent"], "improve")
        self.assertFalse(task["require_complete"])
        self.assertTrue(proposal_has_work({"buy_list": [{"name": "Goblin Bombardment"}]}))
        self.assertTrue(
            proposal_has_work({"delta": {"substitute": [{"out": "A", "in": "B"}]}})
        )
        self.assertFalse(proposal_has_work({"notes": "looks fine"}))

    def test_add_never_exceeds_99_overflow_goes_to_pool(self):
        deck = DeckState(commander="Krenko, Mob Boss")
        deck.apply_delta({"add": [{"name": "Mountain", "quantity": 120}]})
        self.assertEqual(deck.slot_count(), 99)
        self.assertEqual(deck.cards["Mountain"], 99)
        self.assertEqual(deck.candidate_pool["Mountain"], 21)
        self.assertEqual(deck.last_delta["overflow_to_pool"][0]["quantity"], 21)

    def test_remaining_slots_limit_new_uniques(self):
        deck = DeckState(commander="Krenko, Mob Boss", cards={"Mountain": 97})
        deck.apply_delta(
            {
                "add": [
                    {"name": "Sol Ring", "quantity": 1},
                    {"name": "Lightning Bolt", "quantity": 1},
                    {"name": "Test Swarm", "quantity": 1},
                ]
            }
        )
        self.assertEqual(deck.slot_count(), 99)
        self.assertIn("Sol Ring", deck.cards)
        self.assertIn("Lightning Bolt", deck.cards)
        self.assertNotIn("Test Swarm", deck.cards)
        self.assertEqual(deck.candidate_pool.get("Test Swarm"), 1)

    def test_color_identity_and_singleton(self):
        report = self.validator.validate_deck(
            "Krenko, Mob Boss",
            {"Counterspell": 1, "Lightning Bolt": 2},
        )
        self.assertFalse(report["valid"])
        self.assertTrue(report["color_errors"])
        self.assertTrue(report["singleton_errors"])

    def test_basic_land_and_any_number(self):
        report = self.validator.validate_deck(
            "Krenko, Mob Boss",
            {"Mountain": 35, "Test Swarm": 4, "Sol Ring": 1},
        )
        self.assertFalse(report["singleton_errors"])
        self.assertTrue(report["valid"])
        self.assertIn("Incomplete", report["warnings"][0])

    def test_size_over_99(self):
        report = self.validator.validate_deck("Krenko, Mob Boss", {"Mountain": 100})
        self.assertFalse(report["valid"])
        self.assertTrue(report["size_errors"])

    def test_require_complete(self):
        report = self.validator.validate_deck(
            "Krenko, Mob Boss",
            {"Mountain": 10},
            require_complete=True,
        )
        self.assertFalse(report["valid"])
        self.assertTrue(report["size_errors"])

    def test_banned_and_illegal_commander(self):
        banned = self.validator.validate_deck("Krenko, Mob Boss", {"Black Lotus": 1})
        self.assertTrue(banned["format_errors"])
        illegal = self.validator.validate_deck("Llanowar Elves", {"Sol Ring": 1})
        self.assertTrue(illegal["commander_errors"])

    def test_budget_caps(self):
        over_pmax = self.validator.validate_deck(
            "Krenko, Mob Boss",
            {"Lightning Bolt": 1},
            max_card_price=0.25,
        )
        self.assertFalse(over_pmax["valid"])
        self.assertTrue(over_pmax["price_errors"])

        over_budget = self.validator.validate_deck(
            "Krenko, Mob Boss",
            {"Lightning Bolt": 1},
            budget_cap=0.05,
            owned_cost_zero=True,
        )
        self.assertFalse(over_budget["valid"])
        self.assertTrue(over_budget["price_errors"])

    def test_owned_zero_cost_skips_pmax(self):
        report = self.validator.validate_deck(
            "Krenko, Mob Boss",
            {"Sol Ring": 1},
            max_card_price=0.1,
            owned_cost_zero=True,
        )
        self.assertFalse(report["price_errors"])
        self.assertTrue(report["valid"])

    def test_owned_only(self):
        report = self.validator.validate_deck(
            "Krenko, Mob Boss",
            {"Lightning Bolt": 1},
            owned_only=True,
        )
        self.assertTrue(report["owned_errors"])

    def test_scryfall_flattens_dfc_faces(self):
        card = {
            "oracle_text": "",
            "mana_cost": "",
            "card_faces": [
                {
                    "mana_cost": "{R}",
                    "oracle_text": "I — Kumano Faces Kakkazan deals 1 damage.",
                },
                {"oracle_text": "+1/+1 counters. Whenever this deals combat damage, exile the top card."},
            ],
        }
        text = oracle_text_from_scryfall(card)
        self.assertIn("deals 1 damage", text)
        self.assertIn("+1/+1", text)
        self.assertEqual(mana_cost_from_scryfall(card), "{R}")

    def test_split_card_lookup(self):
        info = self.validator.get_card_info("Fire")
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "Fire // Ice")

    def test_gate_rejects_without_llm(self):
        rejected = deterministic_gate({"valid": False, "color_errors": ["Counterspell"]})
        self.assertEqual(rejected["decision"], "REJECTED")
        approved = deterministic_gate({"valid": True})
        self.assertEqual(approved["decision"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
