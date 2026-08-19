import json
import os
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import DB_NAME, ensure_schema
from deck_state import DeckState
from mana import (
    CURVE_PROFILES,
    diagnose,
    diagnose_deck,
    parse_mana_cost,
    produced_mana,
    shape_bonus,
)
from solver import DeckSolver


def _insert_card(
    conn,
    card_id,
    name,
    type_line,
    identity,
    oracle="",
    legal="legal",
    usd=1.0,
    cmc=1.0,
    mana_cost="{R}",
):
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
            mana_cost,
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
        oracle="Create X 1/1 red Goblin creature tokens.",
        cmc=4, mana_cost="{2}{R}{R}",
    )
    _insert_card(
        conn, "mtn", "Mountain", "Basic Land — Mountain", [],
        oracle="{T}: Add {R}.", cmc=0, mana_cost="",
    )
    _insert_card(
        conn, "sol", "Sol Ring", "Artifact", [],
        oracle="{T}: Add {C}{C}.", usd=2.0, cmc=1, mana_cost="{1}",
    )
    _insert_card(
        conn, "lotus", "Gilded Lotus", "Artifact", [],
        oracle="Add three mana of any one color.", usd=8.0, cmc=5, mana_cost="{5}",
    )
    _insert_card(
        conn, "whelp", "Goblin Whelp", "Creature — Goblin", ["R"],
        oracle="Goblin creature tokens.", cmc=2, mana_cost="{1}{R}",
    )
    _insert_card(
        conn, "ogre", "Goblin Ogre", "Creature — Goblin", ["R"],
        oracle="Goblin creature tokens.", cmc=7, mana_cost="{5}{R}{R}",
    )
    _insert_card(
        conn, "bolt", "Lightning Bolt", "Instant", ["R"],
        oracle="Lightning Bolt deals 3 damage to any target.", cmc=1, mana_cost="{R}",
    )
    _insert_card(
        conn, "shock", "Steam Vents", "Land — Island Mountain", ["U", "R"],
        oracle="{T}: Add {U} or {R}.", cmc=0, mana_cost="",
    )
    conn.commit()
    conn.close()


class Stage35Tests(unittest.TestCase):
    def test_parse_two_generic_two_red(self):
        pips = parse_mana_cost("{2}{R}{R}")
        self.assertEqual(pips["generic"], 2)
        self.assertEqual(pips["R"], 2)
        self.assertEqual(pips["colored"], 2)

    def test_parse_hybrid_and_phyrexian_and_x(self):
        hybrid = parse_mana_cost("{R/G}")
        self.assertAlmostEqual(hybrid["R"], 0.5)
        self.assertAlmostEqual(hybrid["G"], 0.5)
        self.assertEqual(hybrid["hybrid"], 1)
        phy = parse_mana_cost("{B/P}")
        self.assertEqual(phy["B"], 1)
        self.assertEqual(phy["phyrexian"], 1)
        twobrid = parse_mana_cost("{2/R}")
        self.assertAlmostEqual(twobrid["R"], 0.5)
        self.assertAlmostEqual(twobrid["generic"], 0.5)
        x_cost = parse_mana_cost("{X}{R}")
        self.assertEqual(x_cost["x"], 1)
        self.assertEqual(x_cost["R"], 1)

    def test_mountain_uses_type_line_not_double_count(self):
        src = produced_mana("Basic Land — Mountain", "{T}: Add {R}.")
        self.assertEqual(src["R"], 1)
        self.assertEqual(src["any"], 0)

    def test_sol_ring_and_gilded_lotus_and_dual(self):
        sol = produced_mana("Artifact", "{T}: Add {C}{C}.")
        self.assertEqual(sol["C"], 2)
        lotus = produced_mana("Artifact", "Add three mana of any one color.")
        self.assertEqual(lotus["any"], 3)
        dual = produced_mana("Land — Island Mountain", "{T}: Add {U} or {R}.")
        self.assertEqual(dual["U"], 1)
        self.assertEqual(dual["R"], 1)
        self.assertEqual(dual["any"], 0)
        bazaar = produced_mana("Land", "{T}: Draw two cards, then discard three cards.")
        self.assertEqual(bazaar["R"], 0)
        self.assertEqual(bazaar["C"], 0)
        self.assertEqual(bazaar["any"], 0)

    def test_yavimaya_and_urborg_count_as_mana_lands(self):
        from mana import produces_mana

        yavi = produced_mana(
            "Legendary Land",
            "Each land is a Forest in addition to its other land types.",
        )
        self.assertEqual(yavi["G"], 1)
        self.assertTrue(
            produces_mana(
                "Legendary Land",
                "Each land is a Forest in addition to its other land types.",
            )
        )
        urborg = produced_mana(
            "Legendary Land",
            "Each land is a Swamp in addition to its other land types.",
        )
        self.assertEqual(urborg["B"], 1)
        bazaar = produced_mana("Land", "{T}: Draw two cards, then discard three cards.")
        self.assertFalse(produces_mana("Land", "{T}: Draw two cards, then discard three cards."))
        self.assertEqual(bazaar["G"], 0)

    def test_diagnose_land_and_source_gaps(self):
        commander = {
            "name": "Krenko, Mob Boss",
            "mana_cost": "{2}{R}{R}",
            "color_identity": ["R"],
        }
        cards = [
            {
                "name": "Mountain",
                "quantity": 2,
                "type_line": "Basic Land — Mountain",
                "oracle_text": "{T}: Add {R}.",
                "mana_cost": "",
                "cmc": 0,
            },
            {
                "name": "Lightning Bolt",
                "quantity": 1,
                "type_line": "Instant",
                "oracle_text": "Lightning Bolt deals 3 damage to any target.",
                "mana_cost": "{R}",
                "cmc": 1,
            },
        ]
        report = diagnose(cards, commander=commander, identity=["R"], slot_count=3)
        self.assertEqual(report["land_count"], 2)
        self.assertEqual(report["pips"]["R"], 3)
        self.assertEqual(report["sources"]["R"], 2)
        self.assertEqual(report["land_alert"]["status"], "low")
        self.assertTrue(any(d.startswith("LAND") for d in report["deficits"]))
        self.assertTrue(any("sources R" in d for d in report["deficits"]))
        self.assertFalse(any(d.startswith("curve") for d in report["deficits"]))

    def test_shape_prefers_two_drop_when_curve_is_top_heavy(self):
        report = diagnose(
            [
                {
                    "name": "Goblin Ogre",
                    "quantity": 12,
                    "type_line": "Creature — Goblin",
                    "oracle_text": "Goblin creature tokens.",
                    "mana_cost": "{5}{R}{R}",
                    "cmc": 7,
                }
            ],
            commander={"mana_cost": "{2}{R}{R}", "color_identity": ["R"]},
            identity=["R"],
            slot_count=12,
        )
        two = shape_bonus(
            {
                "type_line": "Creature — Goblin",
                "oracle_text": "",
                "mana_cost": "{1}{R}",
                "cmc": 2,
            },
            report,
            ["R"],
        )
        seven = shape_bonus(
            {
                "type_line": "Creature — Goblin",
                "oracle_text": "",
                "mana_cost": "{5}{R}{R}",
                "cmc": 7,
            },
            report,
            ["R"],
        )
        self.assertGreater(two["curve_bonus"], 0)
        self.assertGreater(seven["curve_penalty"], 0)
        self.assertGreater(two["total"], seven["total"])

    def test_fast_commander_wants_low_curve(self):
        cmd = {
            "name": "Krenko, Mob Boss",
            "type_line": "Legendary Creature — Goblin Warrior",
            "oracle_text": "Create X 1/1 red Goblin creature tokens.",
            "mana_cost": "{2}{R}{R}",
            "cmc": 4,
            "color_identity": ["R"],
        }
        report = diagnose([], commander=cmd, identity=["R"])
        self.assertEqual(report["curve_profile"], "fast")
        self.assertLess(report["avg_cmc_band"][1], CURVE_PROFILES["high"]["avg"][1])

    def test_ramp_and_cheat_allow_top_end(self):
        cmd = {
            "type_line": "Legendary Creature — Elf Druid",
            "oracle_text": "You may play an additional land on each of your turns.",
            "mana_cost": "{3}{G}{G}",
            "cmc": 5,
            "color_identity": ["G"],
        }
        cards = [
            {
                "name": "Rock",
                "quantity": 12,
                "type_line": "Artifact",
                "oracle_text": "Add {C}{C}.",
                "mana_cost": "{2}",
                "cmc": 2,
            },
            {
                "name": "Big",
                "quantity": 6,
                "type_line": "Creature — Beast",
                "oracle_text": "",
                "mana_cost": "{5}{G}{G}",
                "cmc": 7,
            },
            {
                "name": "Sneak",
                "quantity": 2,
                "type_line": "Enchantment",
                "oracle_text": "You may put a creature card from your hand onto the battlefield.",
                "mana_cost": "{3}{R}",
                "cmc": 4,
            },
        ]
        report = diagnose(cards, commander=cmd, identity=["G"])
        self.assertEqual(report["curve_profile"], "high")
        seven = shape_bonus(
            {
                "type_line": "Creature — Beast",
                "oracle_text": "",
                "mana_cost": "{5}{G}{G}",
                "cmc": 7,
            },
            report,
            ["G"],
        )
        self.assertEqual(seven["curve_penalty"], 0.0)

    def test_lots_of_ramp_overrides_fast_commander(self):
        cmd = {
            "type_line": "Legendary Creature — Goblin Warrior",
            "oracle_text": "Create X 1/1 red Goblin creature tokens.",
            "mana_cost": "{2}{R}{R}",
            "cmc": 4,
        }
        cards = [
            {
                "name": "Rock",
                "quantity": 12,
                "type_line": "Artifact",
                "oracle_text": "Add {C}{C}.",
                "mana_cost": "{2}",
                "cmc": 2,
            }
        ]
        report = diagnose(cards, commander=cmd, identity=["R"])
        self.assertEqual(report["curve_profile"], "high")

    def test_solver_uses_shape_on_curve_gap(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            _seed(tmp.name)
            solver = DeckSolver(tmp.name)
            solver._view_store = None
            deck = DeckState(
                commander="Krenko, Mob Boss",
                identity=["R"],
                cards={"Goblin Ogre": 12},
            )
            two = solver.score_breakdown(deck, "Goblin Whelp", "goblin tokens")
            seven = solver.score_breakdown(deck, "Goblin Ogre", "goblin tokens")
            self.assertGreater(two["curve_bonus"], seven["curve_bonus"])
            self.assertGreater(two["shape"], seven["shape"])
        finally:
            os.unlink(tmp.name)

    def test_diagnose_deck_json_roundtrip(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            _seed(tmp.name)
            deck = DeckState(
                commander="Krenko, Mob Boss",
                identity=["R"],
                cards={"Mountain": 2, "Lightning Bolt": 1},
            )
            report = diagnose_deck(deck, tmp.name)
            self.assertGreaterEqual(report["pips"]["R"], 2)
            self.assertEqual(report["sources"]["R"], 2)
            self.assertTrue(report["deficits"])
        finally:
            os.unlink(tmp.name)


@unittest.skipUnless(
    os.path.exists(DB_NAME)
    and os.path.exists(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "solved_deck.txt")
    ),
    "needs local catalog and solved_deck.txt",
)
class Krenko99Diagnosis(unittest.TestCase):
    def test_solved_krenko_has_mana_report(self):
        from demo_solver import _parse_list

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "solved_deck.txt",
        )
        parsed = _parse_list(path)
        commander = "Krenko, Mob Boss"
        parsed.pop(commander, None)
        deck = DeckState(commander=commander, identity=["R"], cards=parsed)
        report = diagnose_deck(deck)
        self.assertGreater(report["land_count"], 0)
        self.assertGreater(report["sources"]["R"], 0)
        self.assertIsInstance(report["deficits"], list)


if __name__ == "__main__":
    unittest.main()
