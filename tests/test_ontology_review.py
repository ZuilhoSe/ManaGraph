import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from catalog import ensure_schema  # noqa: E402
from enrich_ontology import (  # noqa: E402
    assign_forge_matches,
    enrich,
    join_name_keys,
    match_forge_key,
    rebuild_ontology_model,
)
from mine_forge import mine_cardsfolder  # noqa: E402
from ontology.model_config import build_model_facts  # noqa: E402
from service.handlers.ontology import (  # noqa: E402
    export_gold_set,
    get_ontology_card,
    list_ontology_cards,
    ontology_stats,
    save_ontology_review,
)


FIXTURES = ROOT / "tests" / "fixtures" / "forge"


class OntologyReviewTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db = handle.name
        conn = sqlite3.connect(self.db)
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO cards
              (id, name, mana_cost, cmc, oracle_text, color_identity, type_line,
               legalities, keywords, scryfall_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "engine",
                "Forge Test Engine",
                "{2}{R}",
                3,
                "Create a Treasure token.",
                '["R"]',
                "Artifact",
                '{"commander":"legal"}',
                "[]",
                json.dumps(
                    {
                        "id": "engine",
                        "name": "Forge Test Engine",
                        "mana_cost": "{2}{R}",
                        "type_line": "Artifact",
                        "color_identity": ["R"],
                "colors": ["R"],
                "keywords": ["Menace"],
                        "oracle_text": "Create a Treasure token.",
                    }
                ),
            ),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db)

    def test_enrichment_and_review_export(self):
        with tempfile.TemporaryDirectory() as directory:
            forge_path = Path(directory) / "forge.jsonl"
            rows = mine_cardsfolder(FIXTURES)
            forge_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            result = enrich(forge_path, db_path=self.db)
            self.assertEqual(result["scryfall_cards"], 1)
            self.assertEqual(result["matched"], 1)
            detail = get_ontology_card("engine", self.db)
            self.assertEqual(detail["model_facts"]["source"], "configured-model")
            self.assertEqual(detail["model_facts"]["card"]["keywords"], ["Menace"])
            self.assertIn("effects", detail["model_facts"]["mechanics"])

            stats = ontology_stats(self.db)
            self.assertEqual(stats["forge_matched"], 1)
            cards = list_ontology_cards(query="treasure", db_path=self.db)
            self.assertEqual(cards["total"], 1)
            card_id = cards["cards"][0]["id"]

            reviewed = save_ontology_review(
                card_id,
                "accepted",
                ["produces(treasure)"],
                "Forge and Oracle agree.",
                "resolved",
                {"keywords": True, "color_identity": True},
                self.db,
            )
            self.assertEqual(reviewed["review"]["status"], "accepted")
            self.assertEqual(reviewed["review"]["selected_source"], "resolved")
            self.assertTrue(reviewed["review"]["field_checks"]["keywords"])
            self.assertEqual(
                reviewed["resolved_facts"]["card"]["color_identity"], ["R"]
            )
            self.assertEqual(
                reviewed["resolved_facts"]["card"]["keywords"], ["Menace"]
            )
            self.assertNotIn("front", reviewed["resolved_facts"])
            self.assertEqual(reviewed["resolved_facts"]["source"], "scryfall+forge")
            self.assertEqual(get_ontology_card(card_id, self.db)["review"]["labels"], ["produces(treasure)"])

            output = Path(directory) / "gold_set.jsonl"
            exported = export_gold_set(self.db, output)
            self.assertEqual(exported["count"], 1)
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "accepted")
            self.assertEqual(record["field_checks"]["keywords"], True)
            self.assertEqual(record["scryfall"]["id"], "engine")


def _insert_card(db_path: str, card_id: str, name: str, scryfall: dict, **columns):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO cards
          (id, name, mana_cost, cmc, oracle_text, color_identity, type_line,
           legalities, keywords, scryfall_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            name,
            columns.get("mana_cost", scryfall.get("mana_cost") or ""),
            columns.get("cmc", scryfall.get("cmc", 0)),
            columns.get("oracle_text", scryfall.get("oracle_text") or ""),
            json.dumps(scryfall.get("color_identity") or []),
            scryfall.get("type_line") or "",
            json.dumps(scryfall.get("legalities") or {}),
            json.dumps(scryfall.get("keywords") or []),
            json.dumps(scryfall),
        ),
    )
    conn.commit()
    conn.close()


class FinalModelPromotionTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db = handle.name
        conn = sqlite3.connect(self.db)
        ensure_schema(conn)
        conn.close()

    def tearDown(self):
        os.unlink(self.db)

    def test_dfc_split_oracle_faces_layout_and_ids_in_final(self):
        _insert_card(
            self.db,
            "claim-fame",
            "Claim // Fame",
            {
                "id": "claim-fame",
                "oracle_id": "oracle-claim-fame",
                "name": "Claim // Fame",
                "layout": "split",
                "mana_cost": "",
                "cmc": 4,
                "oracle_text": "",
                "color_identity": ["B", "R"],
                "colors": ["B", "R"],
                "type_line": "Sorcery // Sorcery",
                "keywords": [],
                "card_faces": [
                    {
                        "name": "Claim",
                        "mana_cost": "{B}",
                        "oracle_text": "Return target creature card with mana value 2 or less from your graveyard to the battlefield.",
                        "type_line": "Sorcery",
                        "colors": ["B"],
                    },
                    {
                        "name": "Fame",
                        "mana_cost": "{1}{R}",
                        "oracle_text": "Aftermath\nTarget creature gets +2/+0 and gains haste until end of turn.",
                        "type_line": "Sorcery",
                        "keywords": ["Aftermath"],
                        "colors": ["R"],
                    },
                ],
            },
        )
        forge_text = "\n".join(
            [
                "Name: Claim",
                "ManaCost: B",
                "Types: Sorcery",
                "AlternateMode: Split",
                "Oracle: Return target creature card with mana value 2 or less from your graveyard to the battlefield.",
                "A:SP$ ChangeZone | Origin$ Graveyard | Destination$ Battlefield",
                "SVar:Picture:ignore",
                "ALTERNATE",
                "Name: Fame",
                "ManaCost: 1 R",
                "Types: Sorcery",
                "Oracle: Aftermath. Target creature gets +2/+0 and gains haste until end of turn.",
                "K:Aftermath",
                "A:SP$ Pump | ValidTgts$ Creature",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            cardsfolder = Path(directory) / "cards"
            cardsfolder.mkdir()
            (cardsfolder / "claim.txt").write_text(forge_text, encoding="utf-8")
            forge_path = Path(directory) / "forge.jsonl"
            rows = mine_cardsfolder(cardsfolder)
            forge_path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            result = enrich(forge_path, db_path=self.db)
            self.assertEqual(result["matched"], 1)
            detail = get_ontology_card("claim-fame", self.db)
            card = detail["model_facts"]["card"]
            mechanics = detail["model_facts"]["mechanics"]
            self.assertEqual(card["id"], "claim-fame")
            self.assertEqual(card["oracle_id"], "oracle-claim-fame")
            self.assertEqual(card["layout"], "split")
            self.assertIn("Return target creature", card["oracle_text"])
            self.assertIn("Aftermath", card["oracle_text"])
            self.assertEqual([face["name"] for face in card["faces"]], ["Claim", "Fame"])
            self.assertEqual(card["faces"][0]["mana_cost"], "{B}")
            self.assertEqual(card["faces"][1]["mana_cost"], "{1}{R}")
            self.assertEqual(card["types"]["card_types"], ["Sorcery"])
            self.assertTrue(card["symbols"])
            self.assertIn("deck_has", mechanics)
            self.assertIn("deck_needs", mechanics)
            self.assertIn("deck_hints", mechanics)
            self.assertFalse(
                any(
                    str(effect.get("kind") or "").lower() == "svar"
                    or str(effect.get("prefix") or "") == "SVar"
                    for effect in mechanics["effects"]
                )
            )
            self.assertIn("ability_kinds", mechanics)
            self.assertNotIn("svar", mechanics["ability_kinds"])
            self.assertEqual(detail["forge_match_status"], "matched")
            self.assertEqual(detail["forge"]["card"]["name"], "Claim")

    def test_loyalty_defense_and_all_parts_in_final(self):
        _insert_card(
            self.db,
            "walker",
            "Test Walker",
            {
                "id": "walker",
                "oracle_id": "oracle-walker",
                "name": "Test Walker",
                "layout": "normal",
                "loyalty": "4",
                "type_line": "Legendary Planeswalker — Test",
                "mana_cost": "{2}{U}",
                "cmc": 3,
                "oracle_text": "+1: Draw a card.",
                "color_identity": ["U"],
                "colors": ["U"],
                "all_parts": [
                    {
                        "id": "emblem-1",
                        "component": "token",
                        "name": "Test Walker Emblem",
                        "type_line": "Emblem — Test",
                        "uri": "https://example.test/emblem",
                    }
                ],
            },
        )
        _insert_card(
            self.db,
            "battle",
            "Test Battle",
            {
                "id": "battle",
                "oracle_id": "oracle-battle",
                "name": "Test Battle",
                "layout": "saga",
                "defense": "5",
                "type_line": "Battle — Siege",
                "mana_cost": "{1}{W}",
                "cmc": 2,
                "oracle_text": "When this Battle enters, draw a card.",
                "color_identity": ["W"],
                "colors": ["W"],
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            forge_path = Path(directory) / "forge.jsonl"
            forge_path.write_text("", encoding="utf-8")
            enrich(forge_path, db_path=self.db)
            walker = get_ontology_card("walker", self.db)["model_facts"]["card"]
            battle = get_ontology_card("battle", self.db)["model_facts"]["card"]
            self.assertEqual(walker["loyalty"], "4")
            self.assertEqual(walker["all_parts"], [
                {
                    "id": "emblem-1",
                    "component": "token",
                    "name": "Test Walker Emblem",
                    "type_line": "Emblem — Test",
                }
            ])
            self.assertEqual(walker["types"]["card_types"], ["Planeswalker"])
            self.assertEqual(walker["types"]["supertypes"], ["Legendary"])
            self.assertEqual(walker["types"]["subtypes"], ["Test"])
            self.assertEqual(walker["symbols"], ["2", "U"])
            self.assertEqual(walker["generic_pips"], 2)
            self.assertEqual(walker["colored_pips"], {"U": 1})
            self.assertEqual(battle["defense"], "5")

    def test_svar_filtered_and_rebuild_promotes_from_existing_layers(self):
        canonical = {
            "source": "scryfall",
            "id": "engine",
            "oracle_id": "oracle-engine",
            "name": "Forge Test Engine",
            "layout": "transform",
            "mana_cost": "{2}{R}",
            "type_line": "Legendary Artifact Creature — Construct",
            "oracle_text": "",
            "faces": [
                {"name": "Front", "oracle_text": "Create a Treasure token.", "power": "2", "toughness": "2"},
                {"name": "Back", "oracle_text": "Exile target card from a graveyard.", "loyalty": "5"},
            ],
        }
        forge = {
            "effects": [
                {"kind": "ability", "prefix": "A", "raw": "A: AB$ Token", "value": "AB$ Token"},
                {"kind": "svar", "prefix": "SVar", "raw": "SVar:Picture:x", "value": "Picture:x"},
            ],
            "triggers": [],
            "card": {
                "name": "Forge Test Engine",
                "facts": {
                    "front": {
                        "mana": {"raw": "{2}{R}", "symbols": ["2", "R"], "colored_pips": {"R": 1}, "generic_pips": 2},
                        "types": {"card_types": ["Artifact", "Creature"], "supertypes": ["Legendary"], "subtypes": ["Construct"]},
                        "keywords": ["Flash"],
                    },
                    "faces": [{"keywords": ["Flash"]}],
                },
            },
        }
        resolved = {
            "card": {"name": "Forge Test Engine"},
            "mechanics": {
                "ability_kinds": ["ability", "svar"],
                "effect_count": 2,
                "trigger_count": 0,
                "forge_keywords_not_in_scryfall": ["Flash"],
            },
        }
        model = build_model_facts(canonical, forge, resolved)
        card = model["card"]
        mechanics = model["mechanics"]
        self.assertIn("Create a Treasure token.", card["oracle_text"])
        self.assertEqual(card["layout"], "transform")
        self.assertEqual(card["id"], "engine")
        self.assertEqual(card["oracle_id"], "oracle-engine")
        self.assertEqual(len(card["faces"]), 2)
        self.assertEqual(card["types"]["card_types"], ["Artifact", "Creature"])
        self.assertEqual(card["symbols"], ["2", "R"])
        self.assertEqual(mechanics["forge_keywords_not_in_scryfall"], ["Flash"])
        self.assertEqual(mechanics["effect_count"], 1)
        self.assertEqual([effect["kind"] for effect in mechanics["effects"]], ["ability"])
        self.assertNotIn("svar", mechanics["ability_kinds"])

    def test_art_series_stays_unmatched_and_split_alias_joins(self):
        self.assertEqual(join_name_keys("Claim // Fame"), ["claim // fame", "claim", "fame"])
        exact = {"claim": ["claim"]}
        alias = {"claim": ["claim"], "fame": ["claim"]}
        split = match_forge_key(
            "Claim // Fame",
            {"layout": "split", "faces": [{"name": "Claim"}, {"name": "Fame"}]},
            exact,
            alias,
            set(),
        )
        self.assertEqual(split, "claim")
        skipped = match_forge_key(
            "Claim",
            {"layout": "art_series", "faces": []},
            exact,
            alias,
            set(),
        )
        self.assertIsNone(skipped)
        assigned = assign_forge_matches(
            [{"id": "art", "name": "Claim"}, {"id": "real", "name": "Claim // Fame"}],
            {
                "art": {"layout": "art_series", "faces": []},
                "real": {"layout": "split", "faces": [{"name": "Claim"}, {"name": "Fame"}]},
            },
            {"claim": {"card": {"name": "Claim", "faces": [{"name": "Claim"}, {"name": "Fame"}]}}},
        )
        self.assertEqual(assigned, {"real": "claim"})

    def test_rebuild_refreshes_canonical_without_re_enrich(self):
        _insert_card(
            self.db,
            "valki",
            "Valki, God of Lies // Tibalt, Cosmic Impostor",
            {
                "id": "valki",
                "oracle_id": "oracle-valki",
                "name": "Valki, God of Lies // Tibalt, Cosmic Impostor",
                "layout": "modal_dfc",
                "oracle_text": "",
                "type_line": "Legendary Creature — God // Legendary Planeswalker — Tibalt",
                "mana_cost": "",
                "cmc": 3,
                "color_identity": ["B"],
                "card_faces": [
                    {
                        "name": "Valki, God of Lies",
                        "mana_cost": "{1}{B}",
                        "oracle_text": "When Valki enters, each opponent reveals their hand.",
                        "power": "2",
                        "toughness": "1",
                        "type_line": "Legendary Creature — God",
                    },
                    {
                        "name": "Tibalt, Cosmic Impostor",
                        "mana_cost": "{5}{B}{R}",
                        "oracle_text": "+2: Exile the top card of each player's library.",
                        "loyalty": "5",
                        "type_line": "Legendary Planeswalker — Tibalt",
                    },
                ],
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            cardsfolder = Path(directory) / "cards"
            cardsfolder.mkdir()
            (cardsfolder / "valki.txt").write_text(
                "\n".join(
                    [
                        "Name: Valki, God of Lies",
                        "ManaCost: 1 B",
                        "Types: Legendary Creature — God",
                        "AlternateMode: Modal",
                        "PT: 2/1",
                        "Oracle: When Valki enters, each opponent reveals their hand.",
                        "A:SP$ Reveal",
                        "ALTERNATE",
                        "Name: Tibalt, Cosmic Impostor",
                        "Types: Legendary Planeswalker — Tibalt",
                        "Oracle: +2: Exile the top card of each player's library.",
                    ]
                ),
                encoding="utf-8",
            )
            forge_path = Path(directory) / "forge.jsonl"
            forge_path.write_text(
                "\n".join(json.dumps(row) for row in mine_cardsfolder(cardsfolder)) + "\n",
                encoding="utf-8",
            )
            enrich(forge_path, db_path=self.db)
            rebuilt = rebuild_ontology_model(self.db, rematch=True)
            self.assertEqual(rebuilt["cards_rebuilt"], 1)
            detail = get_ontology_card("valki", self.db)
            card = detail["model_facts"]["card"]
            self.assertEqual(detail["forge_match_status"], "matched")
            self.assertIn("each opponent reveals", card["oracle_text"])
            self.assertEqual(card["layout"], "modal_dfc")
            self.assertEqual(card["faces"][1]["loyalty"], "5")
            self.assertEqual(card["power"], "2")
            self.assertEqual(card["toughness"], "1")


if __name__ == "__main__":
    unittest.main()
