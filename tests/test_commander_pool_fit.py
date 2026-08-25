import json
import os
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema  # noqa: E402
from deck_state import DeckState  # noqa: E402
from rules_validator import legal_commanders_in_pool, rank_commanders_by_pool_fit  # noqa: E402


def _insert_card(conn, card_id, name, type_line, identity, legal="legal"):
    conn.execute(
        """
        INSERT INTO cards (
            id, name, mana_cost, cmc, oracle_text, color_identity,
            type_line, legalities, price_usd, price_eur
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (card_id, name, "", 1.0, "", json.dumps(identity), type_line,
         json.dumps({"commander": legal}), 1.0, 1.0),
    )


def _seed(path):
    conn = sqlite3.connect(path)
    ensure_schema(conn)

    # Three mono-color legendary creatures, one per color -- eligible commanders.
    _insert_card(conn, "cmdr_g", "Green General", "Legendary Creature — Elf", ["G"])
    _insert_card(conn, "cmdr_b", "Black General", "Legendary Creature — Zombie", ["B"])
    _insert_card(conn, "cmdr_u", "Blue General", "Legendary Creature — Wizard", ["U"])
    # Two-color and three-color commanders, no gold cards backing them yet.
    _insert_card(conn, "cmdr_gb", "Golgari General", "Legendary Creature — Elf Zombie", ["G", "B"])
    _insert_card(conn, "cmdr_gbu", "Sultai General", "Legendary Creature — Elf Zombie Wizard", ["B", "G", "U"])

    # 30 mono-colored non-commander cards per color, matching the chat's toy example.
    for i in range(30):
        _insert_card(conn, f"g{i}", f"Green Spell {i}", "Sorcery", ["G"])
        _insert_card(conn, f"b{i}", f"Black Spell {i}", "Sorcery", ["B"])
        _insert_card(conn, f"u{i}", f"Blue Spell {i}", "Sorcery", ["U"])

    # A couple of real gold cards, only usable once a commander actually spans
    # both/all of their colors -- this is what should legitimately move the
    # needle for wider identities, not the raw union of mono piles above.
    _insert_card(conn, "gold_gb", "Golgari Charm", "Instant", ["G", "B"])
    _insert_card(conn, "gold_gbu", "Sultai Ascendancy", "Enchantment", ["B", "G", "U"])

    conn.commit()
    conn.close()


class CommanderPoolFitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _seed(self.db)

    def tearDown(self):
        os.unlink(self.db)

    def _pool(self, names):
        return {name: 1 for name in names}

    def _rank(self, pool):
        return rank_commanders_by_pool_fit(pool, db_path=self.db)

    def _legal(self, pool):
        return legal_commanders_in_pool(pool, db_path=self.db)

    def test_even_mono_pool_ties_regardless_of_commander_color_count(self):
        """The exact scenario from the design discussion: 30 mono-G/B/U cards
        and no gold cards at all. Raw subset-counting would score mono=30,
        2-color=60, 3-color=90 -- always rewarding more colors for free.
        Weighted-by-dilution should tie every candidate at 30.

        Each commander gets its own pool (just its on-color spells + itself)
        instead of one shared pool with all five generals in it -- otherwise
        the generals count as ordinary on-color cards for each other (they
        physically are legendary creatures within color identity) and that
        cross-contribution, not the metric, would throw off the expected tie."""
        greens = [f"Green Spell {i}" for i in range(30)]
        blacks = [f"Black Spell {i}" for i in range(30)]
        blues = [f"Blue Spell {i}" for i in range(30)]
        cases = {
            "Green General": greens,
            "Black General": blacks,
            "Blue General": blues,
            "Golgari General": greens + blacks,
            "Sultai General": greens + blacks + blues,
        }
        for name, spells in cases.items():
            pool = self._pool(spells + [name])
            ranked = self._rank(pool)
            row = next(r for r in ranked if r["name"] == name)
            self.assertAlmostEqual(row["weighted_score"], 30.0, places=1, msg=name)

    def test_real_gold_cards_break_the_tie_in_favor_of_wider_identity(self):
        """Add the two real gold cards from the pool. Golgari (GB) should now
        score strictly above the mono generals, and Sultai (GBU) strictly
        above Golgari -- a genuine, data-backed reason to prefer more colors,
        not the free-lunch artifact from the previous test."""
        greens = [f"Green Spell {i}" for i in range(30)]
        blacks = [f"Black Spell {i}" for i in range(30)]
        blues = [f"Blue Spell {i}" for i in range(30)]
        pool = self._pool(
            greens + blacks + blues + ["Golgari Charm", "Sultai Ascendancy"]
            + ["Green General", "Black General", "Blue General", "Golgari General", "Sultai General"]
        )
        ranked = self._rank(pool)
        scores = {row["name"]: row["weighted_score"] for row in ranked}
        self.assertGreater(scores["Golgari General"], scores["Green General"])
        self.assertGreater(scores["Sultai General"], scores["Golgari General"])

    def test_quantity_scales_the_score(self):
        pool = {"Green General": 1, "Green Spell 0": 4}
        ranked = self._rank(pool)
        row = next(r for r in ranked if r["name"] == "Green General")
        self.assertEqual(row["raw_count"], 4)
        self.assertAlmostEqual(row["weighted_score"], 4.0, places=1)

    def test_commander_itself_is_not_counted_against_its_own_score(self):
        pool = {"Green General": 1}
        ranked = self._rank(pool)
        row = next(r for r in ranked if r["name"] == "Green General")
        self.assertEqual(row["raw_count"], 0)
        self.assertEqual(row["weighted_score"], 0.0)

    def test_empty_pool_returns_no_candidates(self):
        self.assertEqual(self._rank({}), [])

    def test_unresolved_names_do_not_inflate_raw_count(self):
        """A pool name with no catalog match (typo, not yet in the bulk data)
        must count toward neither raw_count nor weighted_score -- an empty
        color_identity default would otherwise pass the subset check for free
        (the empty set is a subset of everything) and inflate raw_count while
        contributing nothing to weighted_score, decoupling the two numbers."""
        pool = {"Green General": 1, "Green Spell 0": 1, "Green Spell Typo": 3}
        ranked = self._rank(pool)
        row = next(r for r in ranked if r["name"] == "Green General")
        self.assertEqual(row["raw_count"], 1)
        self.assertAlmostEqual(row["weighted_score"], 1.0, places=1)

    def test_ranking_matches_legal_commanders_in_pool_membership(self):
        pool = self._pool(["Green General", "Golgari General", "Green Spell 0"])
        ranked_names = {row["name"] for row in self._rank(pool)}
        self.assertEqual(ranked_names, set(self._legal(pool)))

    def test_deck_state_round_trips_the_advanced_option(self):
        deck = DeckState.from_dict({"pool_only": True, "commander_by_pool_fit": True})
        self.assertTrue(deck.commander_by_pool_fit)
        self.assertTrue(deck.to_dict()["commander_by_pool_fit"])
        self.assertTrue(deck.summary()["commander_by_pool_fit"])

        default_deck = DeckState.from_dict({})
        self.assertFalse(default_deck.commander_by_pool_fit)


if __name__ == "__main__":
    unittest.main()
