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
from roles import classify_roles
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
    _insert_card(conn, "krenko", "Krenko, Mob Boss", "Legendary Creature — Goblin Warrior", ["R"],
                 oracle="Goblins you control get +1/+1. Create goblin tokens.", cmc=4)
    _insert_card(conn, "sol", "Sol Ring", "Artifact", [], oracle="Add {C}{C}.", usd=2.0, cmc=1)
    _insert_card(conn, "mtn", "Mountain", "Basic Land — Mountain", [], usd=0.01, cmc=0)
    _insert_card(conn, "bolt", "Lightning Bolt", "Instant", ["R"],
                 oracle="Lightning Bolt deals 3 damage to any target.", usd=1.0, cmc=1)
    _insert_card(
        conn, "swarm", "Test Swarm", "Creature — Goblin", ["R"],
        oracle="A deck can have any number of cards named Test Swarm. Goblin tokens.", usd=0.5, cmc=2
    )
    _insert_card(conn, "counter", "Counterspell", "Instant", ["U"],
                 oracle="Counter target spell.", usd=1.0, cmc=2)
    _insert_card(conn, "lotus", "Black Lotus", "Artifact", [], legal="banned", usd=10000.0, cmc=0)
    _insert_card(conn, "bear", "Grizzly Bears", "Creature — Bear", ["G"], usd=0.2, cmc=2)
    _insert_card(conn, "gilded", "Gilded Lotus", "Artifact", [],
                 oracle="Add three mana of any one color.", usd=8.0, cmc=5)
    _insert_card(conn, "draw", "Goblin Recruiter", "Creature — Goblin", ["R"],
                 oracle="Search your library for any number of Goblin cards.", usd=0.4, cmc=2)
    _insert_card(
        conn, "kumano", "Kumano Faces Kakkazan // Etching of Kumano",
        "Enchantment — Saga // Enchantment Creature — Human Shaman", ["R"],
        oracle="This Saga deals 1 damage to each opponent. Exile this Saga, then return it transformed.",
        usd=0.22, cmc=1,
    )
    _insert_card(
        conn, "bazaar", "Bazaar of Baghdad", "Land", [],
        oracle="{T}: Draw two cards, then discard three cards.", usd=0.5, cmc=0,
    )
    # A commander that IS a creature type but never mentions it in its own
    # oracle text -- e.g. the real Aminatou, Veil Piercer (Human Wizard, but
    # her text is about surveil/miracle, never says "human" or "wizard").
    # self_referential_types() should read this as "not a tribal commander".
    _insert_card(
        conn, "nontribal_cmd", "Test Nontribal Commander", "Legendary Creature — Human Wizard", ["U"],
        oracle="Draw a card at the beginning of your upkeep.", usd=1.0, cmc=3,
    )
    _insert_card(
        conn, "human_wizard", "Test Human Wizard", "Creature — Human Wizard", ["U"],
        oracle="", usd=0.5, cmc=2,
    )
    _insert_card(
        conn, "unrelated_creature", "Test Unrelated Creature", "Creature — Elf", ["U"],
        oracle="", usd=0.5, cmc=2,
    )
    conn.execute(
        "INSERT INTO inventory VALUES (?, ?, ?)",
        ("Sol Ring", 1, json.dumps({"free_pool": 1})),
    )
    conn.execute(
        "INSERT INTO inventory VALUES (?, ?, ?)",
        ("Mountain", 99, json.dumps({"free_pool": 99})),
    )
    conn.commit()
    conn.close()


class Stage2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _seed(self.db)
        self.solver = DeckSolver(self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_classify_roles(self):
        self.assertIn("land", classify_roles("Basic Land — Mountain", ""))
        self.assertIn("ramp", classify_roles("Artifact", "Add {C}{C}."))
        self.assertIn("interaction", classify_roles("Instant", "Lightning Bolt deals 3 damage to any target."))
        self.assertIn("threat", classify_roles("Creature — Goblin", "Create goblin tokens."))
        self.assertIn("draw", classify_roles("Sorcery", "Draw two cards, then discard two cards."))
        self.assertNotIn(
            "draw",
            classify_roles("Land", "{T}: Draw two cards, then discard three cards."),
        )

    def test_fill_skips_land_that_makes_no_mana(self):
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            candidate_pool={
                "Bazaar of Baghdad": 1,
                "Goblin Recruiter": 1,
                "Mountain": 99,
            },
        )
        self.solver.fill(deck, query="goblin tokens damage", retrieve=False, max_adds=5)
        self.assertNotIn("Bazaar of Baghdad", deck.cards)
        self.assertIn("Goblin Recruiter", deck.cards)

    def test_fill_reaches_99_and_not_more(self):
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            require_complete=True,
            candidate_pool={"Mountain": 200, "Sol Ring": 1, "Lightning Bolt": 1, "Counterspell": 1},
        )
        report = self.solver.fill(deck, query="goblin tokens", retrieve=False)
        self.assertTrue(report["ok"])
        self.assertEqual(deck.slot_count(), 99)
        self.assertLessEqual(deck.slot_count(), 99)
        self.assertIn("Sol Ring", deck.cards)
        self.assertIn("Lightning Bolt", deck.cards)
        self.assertNotIn("Counterspell", deck.cards)

    def test_fill_skips_banned_and_pmax(self):
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            max_card_price=3.0,
            candidate_pool={"Black Lotus": 1, "Gilded Lotus": 1, "Mountain": 99},
        )
        self.solver.fill(deck, retrieve=False)
        self.assertNotIn("Black Lotus", deck.cards)
        self.assertNotIn("Gilded Lotus", deck.cards)
        self.assertIn("Mountain", deck.cards)

    def test_owned_only(self):
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            owned_only=True,
            candidate_pool={"Lightning Bolt": 1, "Mountain": 99, "Sol Ring": 1},
        )
        self.solver.fill(deck, retrieve=False)
        self.assertNotIn("Lightning Bolt", deck.cards)
        self.assertIn("Sol Ring", deck.cards)

    def test_budget_blocks_expensive(self):
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            budget_cap=1.5,
            owned_cost_zero=True,
            candidate_pool={"Gilded Lotus": 1, "Mountain": 99},
        )
        self.solver.fill(deck, retrieve=False)
        self.assertNotIn("Gilded Lotus", deck.cards)

    def test_cut_swaps_pool_into_land_heavy_99(self):
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            cards={"Mountain": 99},
            candidate_pool={"Goblin Recruiter": 1, "Lightning Bolt": 1},
        )
        report = self.solver.cut(deck, query="goblin")
        self.assertTrue(report["ok"])
        self.assertEqual(deck.slot_count(), 99)
        self.assertTrue(deck.cards.get("Goblin Recruiter") or deck.cards.get("Lightning Bolt"))
        self.assertLess(deck.cards["Mountain"], 99)

    def test_can_add_rejects_identity(self):
        deck = DeckState(commander="Krenko, Mob Boss", identity=["R"])
        ok, reason = self.solver.can_add(deck, "Counterspell")
        self.assertFalse(ok)
        self.assertIn("identity", reason)

    def test_off_tribe_creature_does_not_fill(self):
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            candidate_pool={
                "Kumano Faces Kakkazan // Etching of Kumano": 1,
                "Goblin Recruiter": 1,
                "Mountain": 99,
            },
        )
        self.solver.fill(deck, query="goblin tokens damage", retrieve=False, max_adds=2)
        self.assertIn("Goblin Recruiter", deck.cards)
        self.assertNotIn("Kumano Faces Kakkazan // Etching of Kumano", deck.cards)

    def test_theme_gate_ignores_non_self_referential_commander(self):
        """Inverse of test_off_tribe_creature_does_not_fill: a commander whose
        own oracle text never mentions its own creature type (like the real
        Aminatou, Veil Piercer -- Human Wizard, but her text is about
        surveil/miracle) must not apply any tribal bonus or off-tribe filter,
        not even to a card that shares its exact creature type."""
        from solver import self_referential_types

        deck = DeckState(commander="Test Nontribal Commander", identity=["U"])
        self.solver._rebuild_context(deck, "")
        cmd_info = self.solver._info("Test Nontribal Commander")
        self.assertEqual(self_referential_types(cmd_info), set())

        same_type = self.solver._info("Test Human Wizard")
        unrelated = self.solver._info("Test Unrelated Creature")
        self.assertEqual(self.solver._tribe_adj(same_type), 0.0)
        self.assertEqual(self.solver._tribe_adj(unrelated), 0.0)
        self.assertFalse(self.solver._off_tribe_creature(same_type))
        self.assertFalse(self.solver._off_tribe_creature(unrelated))

    def test_fill_allows_off_type_creature_for_non_tribal_commander(self):
        deck = DeckState(
            commander="Test Nontribal Commander",
            identity=["U"],
            candidate_pool={"Test Unrelated Creature": 1, "Test Human Wizard": 1},
        )
        self.solver.fill(deck, query="", retrieve=False, max_adds=2)
        self.assertIn("Test Unrelated Creature", deck.cards)
        self.assertIn("Test Human Wizard", deck.cards)

    def test_chroma_distance_does_not_fake_synergy(self):
        deck = DeckState(commander="Krenko, Mob Boss", identity=["R"])
        self.solver._rebuild_context(deck, "goblin tokens damage")
        kumano = self.solver._info("Kumano Faces Kakkazan // Etching of Kumano")
        kumano["_distance"] = 0.2
        goblin = self.solver.score_candidate(deck, "Goblin Recruiter", "goblin tokens damage")
        fake = self.solver.score_candidate(
            deck, "Kumano Faces Kakkazan // Etching of Kumano", "goblin tokens damage"
        )
        br = self.solver.score_breakdown(
            deck, "Kumano Faces Kakkazan // Etching of Kumano", "goblin tokens damage"
        )
        self.assertFalse(br["theme_match"])
        self.assertLess(br["synergy"], br["chroma_synergy"])
        self.assertGreater(goblin, fake)

    def test_solve_strips_off_identity_and_refills_from_pool(self):
        class _SilentSearcher:
            def search_cards(self, **_kwargs):
                return []

        self.solver.searcher = _SilentSearcher()
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            cards={"Lightning Bolt": 1, "Grizzly Bears": 1, "Mountain": 10},
            candidate_pool={"Sol Ring": 1, "Mountain": 20},
            # Editing an existing partial list, not a from-scratch build -- intent
            # must not default to "build" here, or the low land count (10, well
            # under quota) triggers a full rebuild-to-99 instead of just replacing
            # the one stripped card, which isn't what this scenario is testing.
            intent="improve",
        )
        started = deck.slot_count()
        report = self.solver.solve(deck, query="goblin tokens", fill_to_99=False)
        stripped_names = [item["name"] for item in report["stripped"]["removed"]]
        self.assertIn("Grizzly Bears", stripped_names)
        self.assertNotIn("Grizzly Bears", deck.cards)
        self.assertEqual(deck.slot_count(), started)
        self.assertTrue(report["fill"] and report["fill"]["added"])

    def test_solve_cuts_when_99_and_pool(self):
        class _SilentSearcher:
            def search_cards(self, **_kwargs):
                return []

        self.solver.searcher = _SilentSearcher()
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            cards={"Mountain": 98, "Lightning Bolt": 1},
            candidate_pool={"Goblin Recruiter": 1},
            require_complete=True,
        )
        report = self.solver.solve(deck, query="goblin tokens", fill_to_99=True)
        self.assertEqual(deck.slot_count(), 99)
        self.assertTrue((report.get("cut") or {}).get("swapped") or "Goblin Recruiter" in deck.cards)

    def test_solve_reflows_land_flood_with_no_stripped_cards_or_pool(self):
        """A legal-but-land-flooded 99 (the '67 lands' bug) must still get seeded and cut."""

        class _StubSearcher:
            def search_cards(self, **_kwargs):
                return [{"name": "Goblin Recruiter", "distance": 0.1}]

        self.solver.searcher = _StubSearcher()
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            cards={"Mountain": 67, "Test Swarm": 31, "Lightning Bolt": 1},
        )
        self.assertEqual(deck.slot_count(), 99)
        report = self.solver.solve(deck, query="goblin tokens", fill_to_99=False)
        self.assertEqual(report["stripped"]["removed"], [])
        self.assertEqual(report["land_alert"]["severity"], "severe")
        self.assertIsNotNone(report["cut"])
        self.assertLess(deck.cards.get("Mountain", 0), 67)
        self.assertIn("Goblin Recruiter", deck.cards)
        self.assertEqual(deck.slot_count(), 99)

    def test_solve_does_not_auto_cut_land_flood_on_improve_intent(self):
        """A land-heavy 99 that's intentional (Azusa, extra-land-drop shells) must survive
        an unrelated 'improve'/'substitute' request untouched by the land-shape auto-cut."""

        class _StubSearcher:
            def search_cards(self, **_kwargs):
                return [{"name": "Goblin Recruiter", "distance": 0.1}]

        self.solver.searcher = _StubSearcher()
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            cards={"Mountain": 67, "Test Swarm": 31, "Lightning Bolt": 1},
            intent="improve",
        )
        report = self.solver.solve(deck, query="goblin tokens", fill_to_99=False)
        self.assertEqual(report["stripped"]["removed"], [])
        self.assertEqual(report["land_alert"]["severity"], "severe")
        self.assertIsNone(report["cut"])
        self.assertEqual(deck.cards.get("Mountain"), 67)

    def test_solve_fills_incomplete_build_with_severe_land_shortage(self):
        """A near-empty 'build' request must actually reach 99, not stop early.

        Regression for a real run: a from-scratch build landed at 12/99 cards
        because should_fix_shape seeded the candidate_pool (0 lands is a severe
        shortage) but need_fill's condition didn't know about should_fix_shape,
        so fill() was never called to spend that pool down.
        """

        class _StubSearcher:
            def search_cards(self, **_kwargs):
                return [{"name": "Goblin Recruiter", "distance": 0.1}]

        self.solver.searcher = _StubSearcher()
        deck = DeckState(
            commander="Krenko, Mob Boss",
            identity=["R"],
            cards={"Sol Ring": 1},
            intent="build",
        )
        report = self.solver.solve(deck, query="goblin tokens", fill_to_99=False)
        self.assertEqual(report["land_alert"]["severity"], "severe")
        self.assertIsNotNone(report["fill"])
        self.assertTrue(report["fill"]["added"])
        self.assertEqual(deck.slot_count(), 99)


if __name__ == "__main__":
    unittest.main()
