"""Pinned-Oracle tests for the Tier 2 template grammar."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from catalog import ensure_schema  # noqa: E402
from ontology.patterns import extract_oracle_predicates, signatures  # noqa: E402
from ontology.search import rebuild_predicate_index, search_ontology  # noqa: E402

# Frozen Oracle strings: templates under test, not a live Scryfall dump.
SOL_RING = "{T}: Add {C}{C}."
COUNTERSPELL = "Counter target spell."
WRATH = "Destroy all creatures. They can't be regenerated."
VISCERA_SEER = "Sacrifice a creature: Scry 1."
SAKURA_TRIBE_ELDER = (
    "Sacrifice Sakura-Tribe Elder: Search your library for a basic land card, "
    "put that card onto the battlefield tapped, then shuffle."
)
TIME_WARP = "Target player takes an extra turn after this one."
COMBAT_CELEBRANT = (
    "If Combat Celebrant hasn't been exerted this turn, you may exert it as it "
    "attacks. When you do, untap all other creatures you control and after this "
    "phase, there is an additional combat phase."
)
AGGRAVATED_ASSAULT = (
    "{3}{R}{R}: Untap all creatures you control. After this main phase, there is "
    "an additional combat phase followed by an additional main phase. Activate "
    "only as a sorcery."
)
SWIFTFOOT_BOOTS = "Equipped creature has hexproof and haste.\nEquip {1}"
LIGHTNING_GREAVES = "Equipped creature has haste and shroud.\nEquip {0}"
SOUL_WARDEN = "Whenever another creature enters, you gain 1 life."
DOCKSIDE = (
    "When Dockside Extortionist enters, create X Treasure tokens, where X is the "
    "number of artifacts and enchantments your opponents control."
)
FOOD = "Create a Food token."
CULTIVATE = (
    "Search your library for up to two basic land cards, reveal those cards, "
    "put one onto the battlefield tapped and the other into your hand, then shuffle."
)
REANIMATE = (
    "Put target creature card from a graveyard onto the battlefield under your "
    "control. You lose life equal to its mana value."
)
IMPACT_TREMORS = (
    "Whenever a creature you control enters, Impact Tremors deals 1 damage to "
    "each opponent."
)
PHYREXIAN_ALTAR = "Sacrifice a creature: Add one mana of any color."
CONVOKE_SPELL = "Convoke (Your creatures can help cast this spell.)\nDraw a card."


def _sig(oracle: str, **kwargs) -> set[tuple[str, str, str]]:
    return signatures(extract_oracle_predicates(oracle, **kwargs))


class OraclePatternTests(unittest.TestCase):
    def test_sol_ring_produces_colorless_mana(self):
        sig = _sig(SOL_RING, name="Sol Ring", type_line="Artifact")
        self.assertIn(("produces", "object", "mana"), sig)
        self.assertIn(("produces", "color", "c"), sig)
        self.assertIn(("produces", "rate", "2"), sig)

    def test_counterspell_answers_stack(self):
        self.assertIn(
            ("answers", "threat_class", "stack"),
            _sig(COUNTERSPELL, name="Counterspell"),
        )

    def test_wrath_answers_board_not_single_creature(self):
        sig = _sig(WRATH, name="Wrath of God")
        self.assertIn(("answers", "threat_class", "board"), sig)
        self.assertNotIn(("answers", "threat_class", "creature"), sig)

    def test_viscera_seer_is_sac_outlet(self):
        sig = _sig(VISCERA_SEER, name="Viscera Seer")
        self.assertIn(("enables", "capability", "sac_outlet"), sig)
        self.assertIn(("consumes", "object", "creature"), sig)
        self.assertIn(("emits", "event", "sacrifice"), sig)

    def test_self_sacrifice_is_not_sac_outlet(self):
        sig = _sig(SAKURA_TRIBE_ELDER, name="Sakura-Tribe Elder")
        self.assertNotIn(("enables", "capability", "sac_outlet"), sig)
        self.assertIn(("tutors", "selector", "land"), sig)

    def test_extra_turn_is_not_extra_combat(self):
        sig = _sig(TIME_WARP, name="Time Warp")
        self.assertNotIn(("enables", "capability", "extra_combat"), sig)

    def test_combat_celebrant_and_aggravated_assault_are_extra_combat(self):
        for name, oracle in (
            ("Combat Celebrant", COMBAT_CELEBRANT),
            ("Aggravated Assault", AGGRAVATED_ASSAULT),
        ):
            sig = _sig(oracle, name=name)
            self.assertIn(("enables", "capability", "extra_combat"), sig, name)

    def test_equipment_protects_commander_and_grants_haste(self):
        boots = _sig(SWIFTFOOT_BOOTS, name="Swiftfoot Boots", type_line="Artifact — Equipment")
        self.assertIn(("protects", "target_class", "commander"), boots)
        self.assertIn(("enables", "capability", "haste_grant"), boots)
        greaves = _sig(
            LIGHTNING_GREAVES, name="Lightning Greaves", type_line="Artifact — Equipment"
        )
        self.assertIn(("protects", "target_class", "commander"), greaves)

    def test_soul_warden_rewards_etb_dockside_does_not(self):
        warden = _sig(SOUL_WARDEN, name="Soul Warden")
        self.assertIn(("rewards", "event", "etb"), warden)
        self.assertIn(("produces", "object", "life"), warden)
        dockside = _sig(DOCKSIDE, name="Dockside Extortionist")
        self.assertNotIn(("rewards", "event", "etb"), dockside)
        self.assertIn(("produces", "object", "treasure"), dockside)
        self.assertIn(("emits", "event", "token_created"), dockside)
        self.assertIn(("emits", "event", "etb"), dockside)

    def test_food_is_token_not_treasure(self):
        sig = _sig(FOOD, name="Gilded Goose")
        self.assertIn(("produces", "object", "token"), sig)
        self.assertNotIn(("produces", "object", "treasure"), sig)

    def test_cultivate_tutors_land(self):
        self.assertIn(("tutors", "selector", "land"), _sig(CULTIVATE, name="Cultivate"))

    def test_reanimate_recurs_to_battlefield(self):
        sig = _sig(REANIMATE, name="Reanimate")
        self.assertIn(("recurs", "zone_from", "graveyard"), sig)
        self.assertIn(("recurs", "zone_to", "battlefield"), sig)
        self.assertIn(("emits", "event", "etb"), sig)

    def test_own_etb_is_not_rewards_etb(self):
        sig = _sig(
            "When this creature enters, draw a card.",
            name="Mulldrifter",
        )
        self.assertNotIn(("rewards", "event", "etb"), sig)
        self.assertIn(("produces", "object", "card_in_hand"), sig)

    def test_phyrexian_altar_outlet_and_any_mana(self):
        sig = _sig(PHYREXIAN_ALTAR, name="Phyrexian Altar")
        self.assertIn(("enables", "capability", "sac_outlet"), sig)
        self.assertIn(("produces", "object", "mana"), sig)
        self.assertIn(("produces", "color", "any"), sig)

    def test_convoke_keyword_is_tier1_and_reminder_stripped(self):
        sig = _sig(
            CONVOKE_SPELL,
            name="Chord of Calling",
            keywords=["Convoke"],
        )
        self.assertIn(("enables", "capability", "convoke_like"), sig)
        self.assertIn(("produces", "object", "card_in_hand"), sig)


class OracleIndexTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        ensure_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO cards (
                id, name, mana_cost, cmc, oracle_text, color_identity,
                type_line, legalities, keywords
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "boots",
                "Swiftfoot Boots",
                "{2}",
                2.0,
                SWIFTFOOT_BOOTS,
                '["R"]',
                "Artifact — Equipment",
                json.dumps({"commander": "legal"}),
                "[]",
            ),
        )
        rebuild_predicate_index(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_rebuild_indexes_oracle_without_forge_row(self):
        hits = search_ontology(
            self.conn, "protects:commander", allowed_colors=["R"]
        )
        names = [hit["name"] for hit in hits]
        self.assertIn("Swiftfoot Boots", names)


if __name__ == "__main__":
    unittest.main()
