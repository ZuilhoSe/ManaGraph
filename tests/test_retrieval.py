"""Retrieval text + hybrid lexical search (no Chroma required)."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from catalog import ensure_schema
from retrieval_text import (
    DOCUMENT_FORMAT,
    card_document,
    is_searchable_card,
    lexical_phrases,
    lexical_search_sqlite,
    merge_hit_maps,
)


def _insert(conn, card_id, name, type_line, identity, oracle="", legal="legal"):
    conn.execute(
        """
        INSERT INTO cards (
            id, name, mana_cost, cmc, oracle_text, color_identity,
            type_line, legalities, price_usd, price_eur, keywords
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            name,
            "{B}",
            2.0,
            oracle,
            json.dumps(identity),
            type_line,
            json.dumps({"commander": legal}),
            1.0,
            1.0,
            "[]",
        ),
    )


class RetrievalTextTests(unittest.TestCase):
    def test_document_excludes_name(self):
        doc = card_document(
            "Enchantment",
            "At the beginning of your upkeep, you draw a card and you lose 1 life.",
        )
        self.assertNotIn("Phyrexian Arena", doc)
        self.assertTrue(doc.startswith("Enchantment"))
        self.assertIn("draw a card", doc)
        self.assertEqual(DOCUMENT_FORMAT, "type_oracle_v1")

    def test_junk_filter(self):
        self.assertFalse(is_searchable_card("Card Draw", "Card", {"commander": "not_legal"}))
        self.assertFalse(
            is_searchable_card(
                "Careful Study (minigame)",
                "Card",
                {"commander": "not_legal"},
            )
        )
        self.assertTrue(
            is_searchable_card(
                "Phyrexian Arena",
                "Enchantment",
                {"commander": "legal"},
            )
        )

    def test_draw_family_expands_phrases(self):
        phrases = lexical_phrases("draw a card")
        self.assertIn("draw a card", phrases)
        self.assertIn("exile the top card of your library", phrases)
        nl = lexical_phrases(
            "I want repeatable draw engines or cards that give me card advantage."
        )
        self.assertTrue(any("draw" in p for p in nl))

    def test_lexical_finds_arena_and_necro(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            conn = sqlite3.connect(tmp.name)
            ensure_schema(conn)
            _insert(
                conn,
                "arena",
                "Phyrexian Arena",
                "Enchantment",
                ["B"],
                "At the beginning of your upkeep, you draw a card and you lose 1 life.",
            )
            _insert(
                conn,
                "necro",
                "Necropotence",
                "Enchantment",
                ["B"],
                "Skip your draw step. Pay 1 life: Exile the top card of your library face down. "
                "Put that card into your hand at the beginning of your next end step.",
            )
            _insert(
                conn,
                "coroner",
                "Agency Coroner",
                "Creature — Ogre Cleric",
                ["B"],
                "{2}{B}, Sacrifice another creature: Draw a card. "
                "If the sacrificed creature was suspected, draw two cards instead.",
            )
            _insert(
                conn,
                "rites",
                "Village Rites",
                "Instant",
                ["B"],
                "As an additional cost to cast this spell, sacrifice a creature.\nDraw two cards.",
            )
            _insert(
                conn,
                "ring",
                "The One Ring",
                "Legendary Artifact",
                [],
                "Indestructible\n"
                "{T}: Put a burden counter on The One Ring, then draw a card for each "
                "burden counter on The One Ring.",
            )
            _insert(
                conn,
                "bmc",
                "Black Market Connections",
                "Enchantment",
                ["B"],
                "At the beginning of your first main phase, choose one or more —\n"
                "• Create a Treasure token. You lose 1 life.\n"
                "• Draw a card. You lose 2 life.",
            )
            _insert(
                conn,
                "junk",
                "Card Draw",
                "Card",
                [],
                "(Theme color: {U})",
                legal="not_legal",
            )
            _insert(
                conn,
                "bolt",
                "Lightning Bolt",
                "Instant",
                ["R"],
                "Lightning Bolt deals 3 damage to any target.",
            )
            conn.commit()
            hits = lexical_search_sqlite(conn, "draw a card", ["B"], limit=20)
            names = [h["name"] for h in hits]
            self.assertIn("Phyrexian Arena", names)
            self.assertIn("Necropotence", names)
            self.assertIn("Village Rites", names)
            self.assertIn("Black Market Connections", names)
            self.assertIn("The One Ring", names)
            self.assertNotIn("Card Draw", names)
            self.assertNotIn("Lightning Bolt", names)
            # Engines and burst draw outrank activated creature cantrips.
            self.assertLess(names.index("Phyrexian Arena"), names.index("Agency Coroner"))
            self.assertLess(names.index("Village Rites"), names.index("Agency Coroner"))
            conn.close()
        finally:
            os.unlink(tmp.name)

    def test_counter_family_covers_negate_variants(self):
        phrases = lexical_phrases("counter target spell")
        self.assertIn("counter target noncreature spell", phrases)
        self.assertIn("counter target instant or sorcery spell", phrases)
        # Do not flood a counter query with unrelated removal templates.
        self.assertNotIn("destroy target creature", phrases)

    def test_lexical_finds_counters_and_enchantress(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            conn = sqlite3.connect(tmp.name)
            ensure_schema(conn)
            _insert(
                conn,
                "cs",
                "Counterspell",
                "Instant",
                ["U"],
                "Counter target spell.",
            )
            _insert(
                conn,
                "veto",
                "Dovin's Veto",
                "Instant",
                ["W", "U"],
                "This spell can't be countered.\nCounter target noncreature spell.",
            )
            _insert(
                conn,
                "negate",
                "Negate",
                "Instant",
                ["U"],
                "Counter target noncreature spell.",
            )
            _insert(
                conn,
                "muddle",
                "Muddle the Mixture",
                "Instant",
                ["U"],
                "Counter target instant or sorcery spell.\nTransmute {1}{U}{U}.",
            )
            _insert(
                conn,
                "mesa",
                "Mesa Enchantress",
                "Creature — Human Druid",
                ["W"],
                "Whenever you cast an enchantment spell, you may draw a card.",
            )
            _insert(
                conn,
                "tracker",
                "Entity Tracker",
                "Creature — Human Scout",
                ["U"],
                "Flash\n"
                "Whenever an enchantment you control enters and whenever you fully "
                "unlock a Room, draw a card.",
            )
            _insert(
                conn,
                "reaper",
                "Ashiok's Reaper",
                "Creature — Nightmare",
                ["B"],
                "Whenever an enchantment you control is put into a graveyard from "
                "the battlefield, draw a card.",
            )
            _insert(
                conn,
                "bolt",
                "Lightning Bolt",
                "Instant",
                ["R"],
                "Lightning Bolt deals 3 damage to any target.",
            )
            conn.commit()

            counters = lexical_search_sqlite(
                conn, "counter target spell", ["W", "U", "B"], limit=20
            )
            cnames = [h["name"] for h in counters]
            self.assertIn("Counterspell", cnames)
            self.assertIn("Dovin's Veto", cnames)
            self.assertIn("Negate", cnames)
            self.assertIn("Muddle the Mixture", cnames)
            self.assertNotIn("Lightning Bolt", cnames)

            enchant = lexical_search_sqlite(
                conn, "whenever you cast an enchantment spell", ["W", "U", "B"], limit=20
            )
            enames = [h["name"] for h in enchant]
            self.assertIn("Mesa Enchantress", enames)

            enters = lexical_search_sqlite(
                conn, "enchantment you control enters", ["W", "U", "B"], limit=20
            )
            self.assertIn("Entity Tracker", [h["name"] for h in enters])

            dies = lexical_search_sqlite(
                conn, "whenever an enchantment you control", ["W", "U", "B"], limit=20
            )
            self.assertIn("Ashiok's Reaper", [h["name"] for h in dies])
            conn.close()
        finally:
            os.unlink(tmp.name)

    def test_merge_prefers_hybrid_blend(self):
        emb = [{"name": "Phyrexian Arena", "distance": 0.55, "text": "emb"}]
        lex = [{"name": "Phyrexian Arena", "distance": 0.08, "text": "lex", "matched_phrase": "draw a card"}]
        only_lex = [{"name": "Night's Whisper", "distance": 0.10, "text": "lex2"}]
        merged = merge_hit_maps(emb, lex + only_lex)
        by = {h["name"]: h for h in merged}
        self.assertEqual(by["Phyrexian Arena"]["source"], "hybrid")
        self.assertLessEqual(by["Phyrexian Arena"]["distance"], 0.08)
        self.assertEqual(by["Night's Whisper"]["source"], "lexical")
        # Strong lexical can outrank a weak embedding blend.
        self.assertIn(merged[0]["name"], {"Night's Whisper", "Phyrexian Arena"})


if __name__ == "__main__":
    unittest.main()
