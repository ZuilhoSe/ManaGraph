"""Unit tests for catalog filters, infer_build_filters, and solver land/theme seeding."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema, get_oracle_card
from catalog_filters import (
    cards_by_type_fragment,
    identity_ok,
    is_commander_legal,
    type_line_has_fragment,
)
from deck_state import DeckState, infer_build_filters, infer_task
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
    cmc=0.0,
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
            "",
            cmc,
            oracle,
            json.dumps(identity),
            type_line,
            json.dumps({"commander": legal}),
            usd,
            usd,
        ),
    )


def _seed_filter_db(path: str):
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    _insert_card(
        conn, "cmd", "Test Commander", "Legendary Creature — Human",
        ["W", "U", "B", "R", "G"], cmc=5,
    )
    _insert_card(
        conn, "g1", "Azorius Guildgate", "Land — Gate",
        ["W", "U"], oracle="{T}: Add {W} or {U}.",
    )
    _insert_card(
        conn, "g2", "Simic Guildgate", "Land — Gate",
        ["G", "U"], oracle="{T}: Add {G} or {U}.",
    )
    _insert_card(
        conn, "g_illegal", "Alchemy Gate", "Land — Gate",
        ["W"], oracle="{T}: Add {W}.", legal="not_legal",
    )
    _insert_card(
        conn, "tower", "Command Tower", "Land",
        [], oracle="{T}: Add one mana of any color in your commander's color identity.",
    )
    _insert_card(
        conn, "plains", "Plains", "Basic Land — Plains",
        ["W"], oracle="{T}: Add {W}.",
    )
    _insert_card(
        conn, "forest", "Forest", "Basic Land — Forest",
        ["G"], oracle="{T}: Add {G}.",
    )
    _insert_card(
        conn, "r1", "Abandoned Campground", "Enchantment — Room",
        ["W"], oracle="(You may cast either half.)", cmc=2,
    )
    _insert_card(
        conn, "r2", "Mirror Room // Fractured Realm", "Enchantment — Room",
        ["U"], oracle="Unlock...", cmc=4,
    )
    _insert_card(
        conn, "bear", "Grizzly Bears", "Creature — Bear",
        ["G"], oracle="", cmc=2,
    )
    _insert_card(
        conn, "cave", "Hidden Cave", "Land — Cave",
        ["G"], oracle="{T}: Add {G}.",
    )
    for i in range(40):
        _insert_card(
            conn,
            f"gx{i}",
            f"Test Gate {i}",
            "Land — Gate",
            ["W", "U", "B", "R", "G"],
            oracle="{T}: Add one mana of any color.",
        )
    for i in range(12):
        _insert_card(
            conn,
            f"rx{i}",
            f"Test Room {i}",
            "Enchantment — Room",
            ["W", "U", "B", "R", "G"],
            oracle="Unlock door.",
            cmc=2,
        )
    conn.commit()
    conn.close()


class TypeFragmentTests(unittest.TestCase):
    def test_gate_vs_room_tokens(self):
        self.assertTrue(type_line_has_fragment("Land — Gate", "Gate"))
        self.assertFalse(type_line_has_fragment("Land — Gate", "Room"))
        self.assertTrue(type_line_has_fragment("Enchantment — Room", "Room"))
        self.assertTrue(
            type_line_has_fragment("Enchantment — Room // Enchantment — Room", "Room")
        )
        self.assertFalse(type_line_has_fragment("Creature — Human Wizard", "Gate"))

    def test_identity_and_legality_helpers(self):
        self.assertTrue(identity_ok(["W", "U"], ["W", "U", "B"]))
        self.assertFalse(identity_ok(["W", "B"], ["W", "U"]))
        self.assertTrue(is_commander_legal({"commander": "legal"}))
        self.assertFalse(is_commander_legal({"commander": "not_legal"}))
        self.assertTrue(is_commander_legal(json.dumps({"commander": "legal"})))


class CatalogFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _seed_filter_db(self.db)

    def tearDown(self):
        try:
            os.unlink(self.db)
        except OSError:
            pass

    def test_gates_exclude_illegal_and_respect_identity(self):
        wubrg = ["W", "U", "B", "R", "G"]
        gates = cards_by_type_fragment("Gate", wubrg, lands_only=True, db_path=self.db)
        names = {c["name"] for c in gates}
        self.assertIn("Azorius Guildgate", names)
        self.assertIn("Simic Guildgate", names)
        self.assertNotIn("Alchemy Gate", names)

        wu_only = cards_by_type_fragment(
            "Gate", ["W", "U"], lands_only=True, db_path=self.db
        )
        wu_names = {c["name"] for c in wu_only}
        self.assertIn("Azorius Guildgate", wu_names)
        self.assertNotIn("Simic Guildgate", wu_names)

    def test_rooms_are_non_lands(self):
        rooms = cards_by_type_fragment(
            "Room", ["W", "U", "B", "R", "G"], lands_only=False, db_path=self.db
        )
        names = {c["name"] for c in rooms}
        self.assertIn("Abandoned Campground", names)
        self.assertTrue(all("land" not in (c["type_line"] or "").lower() for c in rooms))


class InferBuildFiltersTests(unittest.TestCase):
    def test_gate_and_room_phrases(self):
        soft = infer_build_filters("gate mana base with rooms")
        self.assertEqual(soft["preferred_land_types"], ["Gate"])
        self.assertEqual(soft["theme_types"], ["Room"])
        self.assertFalse(soft["land_types_strict"])

        strict = infer_build_filters("only gates for the mana base")
        self.assertEqual(strict["preferred_land_types"], ["Gate"])
        self.assertTrue(strict["land_types_strict"])

        snow = infer_build_filters("snow lands and caves")
        self.assertIn("Snow", snow["preferred_land_types"])
        self.assertIn("Cave", snow["preferred_land_types"])

    def test_infer_task_merges_filters(self):
        task = infer_task(
            "build Marina Vendrell rooms enchantress with a gate mana base and ramp"
        )
        self.assertEqual(task["intent"], "build")
        self.assertEqual(task["preferred_land_types"], ["Gate"])
        self.assertEqual(task["theme_types"], ["Room"])


class SolverFilterSeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _seed_filter_db(self.db)

    def tearDown(self):
        try:
            os.unlink(self.db)
        except OSError:
            pass

    def test_seed_theme_and_preferred_lands(self):
        deck = DeckState(
            commander="Test Commander",
            identity=["W", "U", "B", "R", "G"],
            intent="build",
            require_complete=True,
            preferred_land_types=["Gate"],
            theme_types=["Room"],
        )
        solver = DeckSolver(db_path=self.db)
        report = solver._seed_catalog_filters(deck)
        self.assertGreater(report["theme_pool"], 0)
        self.assertGreater(report["land_pool"] + report["land_committed"], 0)

        roomish = [
            n
            for n in deck.candidate_pool
            if "Room" in ((get_oracle_card(n, self.db) or {}).get("type_line") or "")
        ]
        self.assertTrue(roomish)

        gate_in_deck = 0
        for name, qty in deck.card_list().items():
            tl = (get_oracle_card(name, self.db) or {}).get("type_line") or ""
            if "Gate" in tl:
                gate_in_deck += qty
        self.assertGreater(gate_in_deck, 5)

    def test_fill_prefers_gates_in_land_slots(self):
        deck = DeckState(
            commander="Test Commander",
            identity=["W", "U", "B", "R", "G"],
            intent="build",
            require_complete=True,
            preferred_land_types=["Gate"],
            theme_types=["Room"],
        )
        # Seed non-gate utility + rooms into pool so soft fill can use them
        deck.add_to_pool("Command Tower", 1)
        for i in range(12):
            deck.add_to_pool(f"Test Room {i}", 1)
        for i in range(40):
            deck.add_to_pool(f"Test Gate {i}", 1)

        solver = DeckSolver(db_path=self.db)
        solver.fill(deck, query="rooms gates", retrieve=False, max_adds=50)

        land_n = gate_n = room_n = swamp_n = 0
        for name, qty in deck.card_list().items():
            info = get_oracle_card(name, self.db) or {}
            tl = info.get("type_line") or ""
            if "land" in tl.lower():
                land_n += qty
            if "Gate" in tl:
                gate_n += qty
            if "Room" in tl:
                room_n += qty
            if name == "Swamp":
                swamp_n += qty
        self.assertGreater(land_n, 10)
        self.assertLessEqual(land_n, int(__import__("roles").ROLE_QUOTAS["land"][1]) + 2)
        self.assertGreaterEqual(gate_n, land_n // 2)
        self.assertGreater(room_n, 0)
        self.assertLessEqual(room_n, 16)
        self.assertLessEqual(swamp_n, 8)

    def test_theme_pool_is_capped(self):
        deck = DeckState(
            commander="Test Commander",
            identity=["W", "U", "B", "R", "G"],
            intent="build",
            preferred_land_types=["Gate"],
            theme_types=["Room"],
        )
        solver = DeckSolver(db_path=self.db)
        report = solver._seed_catalog_filters(deck)
        self.assertLessEqual(report["theme_pool"], 14)
        self.assertLessEqual(report["land_committed"], 28)

    def test_no_basic_pad_when_lands_ok(self):
        solver = DeckSolver(db_path=self.db)
        deck = DeckState(
            commander="Test Commander",
            identity=["W", "U", "B", "R", "G"],
            intent="build",
            preferred_land_types=["Gate"],
        )
        # Pretend we already hit land quota high with gates.
        for i in range(38):
            deck.add_card(f"Test Gate {i}", 1)
        self.assertIsNone(solver._best_basic(deck))
        self.assertEqual(
            solver._skip_reason(
                {"name": "Test Gate 39", "type_line": "Land — Gate",
                 "oracle_text": "{T}: Add one mana of any color."},
                deck,
            ),
            "land quota full",
        )

    def test_strict_rejects_non_preferred_nonbasic_land(self):
        solver = DeckSolver(db_path=self.db)
        deck = DeckState(
            commander="Test Commander",
            identity=["W", "U", "B", "R", "G"],
            preferred_land_types=["Gate"],
            land_types_strict=True,
        )
        tower = {
            "name": "Command Tower",
            "type_line": "Land",
            "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
        }
        gate = {
            "name": "Azorius Guildgate",
            "type_line": "Land — Gate",
            "oracle_text": "{T}: Add {W} or {U}.",
        }
        plains = {
            "name": "Plains",
            "type_line": "Basic Land — Plains",
            "oracle_text": "{T}: Add {W}.",
        }
        self.assertEqual(
            solver._skip_reason(tower, deck),
            "non-preferred land (land_types_strict)",
        )
        self.assertIsNone(solver._skip_reason(gate, deck))
        self.assertIsNone(solver._skip_reason(plains, deck))

    def test_soft_allows_command_tower(self):
        solver = DeckSolver(db_path=self.db)
        deck = DeckState(
            commander="Test Commander",
            preferred_land_types=["Gate"],
            land_types_strict=False,
        )
        tower = {
            "name": "Command Tower",
            "type_line": "Land",
            "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
        }
        self.assertIsNone(solver._skip_reason(tower, deck))

    def test_deckstate_round_trip_filters(self):
        deck = DeckState(
            preferred_land_types=["Gate"],
            theme_types=["Room"],
            land_types_strict=True,
        )
        again = DeckState.from_dict(deck.to_dict())
        self.assertEqual(again.preferred_land_types, ["Gate"])
        self.assertEqual(again.theme_types, ["Room"])
        self.assertTrue(again.land_types_strict)
        self.assertEqual(again.summary()["preferred_land_types"], ["Gate"])


if __name__ == "__main__":
    unittest.main()
