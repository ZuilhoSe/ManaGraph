import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from mine_forge import _load_mapping, apply_mapping, mine_cardsfolder, parse_forge_text  # noqa: E402
from ontology.schema import load_schema  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "forge"


def _predicates(row):
    return {
        item["predicate"]
        for item in row["candidates"]
        if item["predicate"]
    }


class ForgeMiningTests(unittest.TestCase):
    def test_parser_preserves_alternate_oracle_and_follows_subability(self):
        text = (FIXTURES / "complex_card.txt").read_text(encoding="utf-8")
        row = parse_forge_text(text, "complex_card.txt")

        self.assertEqual(row["card"]["name"], "Forge Test Engine")
        self.assertEqual(len(row["card"]["faces"]), 2)
        self.assertTrue(row["card"]["faces"][1]["is_alternate"])
        self.assertIn("graveyard", row["card"]["faces"][1]["oracle"].lower())
        self.assertTrue(any(chain["resolved"] for chain in row["subabilities"]))
        self.assertTrue(
            any(
                effect["value"].startswith("DB$ Draw")
                and effect["reachable_via_subability"]
                for effect in row["effects"]
            )
        )
        facts = row["card"]["facts"]["front"]
        self.assertEqual(facts["mana"]["symbols"], ["2", "R"])
        self.assertEqual(facts["mana"]["colors"], ["R"])
        self.assertEqual(facts["mana"]["color_identity_source"], "scryfall_pending")
        self.assertEqual(facts["types"]["card_types"], ["Artifact"])
        self.assertEqual(facts["keywords"], ["Flash"])
        self.assertIn("ability", facts["ability_kinds"])
        self.assertIn("trigger", facts["ability_kinds"])

    def test_directory_mining_deduplicates_identical_cards_and_maps_core_dsl(self):
        rows = mine_cardsfolder(FIXTURES, release="forge-test", commit="abc123")
        by_name = {row["card"]["name"]: row for row in rows}

        self.assertEqual(len(rows), 2)
        row = by_name["Forge Test Engine"]
        self.assertEqual(row["release"], {"tag": "forge-test", "commit": "abc123"})
        self.assertEqual(len(row["card"]["source_files"]), 2)
        self.assertEqual(row["deck_has"], ["Ability$Token"])
        self.assertIn("produces", _predicates(row))
        self.assertIn("emits", _predicates(row))
        self.assertIn("answers", _predicates(row))
        self.assertEqual(row["card"]["facts"]["front"]["types"]["card_types"], ["Artifact"])
        self.assertTrue(
            any(
                item["arguments"].get("object") == "card_in_hand"
                for item in row["candidates"]
            )
        )
        self.assertTrue(any(item["validation_only"] for item in row["candidates"]))

    def test_costs_are_separate_and_do_not_confuse_self_sacrifice(self):
        text = (FIXTURES / "sacrifice_outlet.txt").read_text(encoding="utf-8")
        row = parse_forge_text(text, "sacrifice_outlet.txt")
        self.assertEqual(row["costs"][0]["value"], "Sac<1/Creature>")

        outlet = {
            item["card"]["name"]: item for item in mine_cardsfolder(FIXTURES)
        }["Forge Test Outlet"]
        self.assertIn("enables", _predicates(outlet))

        with tempfile.TemporaryDirectory() as directory:
            self_sac = Path(directory) / "self.txt"
            self_sac.write_text(
                "Name: Self Sac\nA:AB$ Draw | Cost$ Sac<1/CARDNAME>\n",
                encoding="utf-8",
            )
            self_row = mine_cardsfolder(self_sac.parent)[0]
            self.assertNotIn("enables", _predicates(self_row))

    def test_semantic_facts_parse_colors_types_and_variable_power(self):
        row = parse_forge_text(
            "\n".join(
                [
                    "Name: Semantic Facts",
                    "ManaCost: {2}{W/U}{G/P}{X}",
                    "Types: Legendary Creature — Elf Druid",
                    "PT: 2+* / 3",
                    "K: Flying",
                    "R: Event$ Damage",
                    "S: Mode$ Continuous",
                    "T: Mode$ Attacks",
                ]
            ),
            "semantic_facts.txt",
        )
        facts = row["card"]["facts"]["front"]
        self.assertEqual(facts["mana"]["generic_pips"], 2)
        self.assertEqual(facts["mana"]["colors"], ["W", "U", "G"])
        self.assertTrue(facts["mana"]["has_variable_symbol"])
        self.assertIsNone(facts["mana"]["color_identity"])
        self.assertEqual(facts["types"]["supertypes"], ["Legendary"])
        self.assertEqual(facts["types"]["card_types"], ["Creature"])
        self.assertEqual(facts["types"]["subtypes"], ["Elf", "Druid"])
        self.assertEqual(
            facts["power_toughness"],
            {"raw": "2+* / 3", "power": "2+*", "toughness": "3"},
        )
        self.assertEqual(facts["keywords"], ["Flying"])
        self.assertEqual(set(facts["ability_kinds"]), {"keyword", "replacement", "static", "trigger"})

    def test_zip_input_emits_json_serializable_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "cardsfolder.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.write(FIXTURES / "sacrifice_outlet.txt", "cards/sacrifice.txt")
            rows = mine_cardsfolder(archive_path)
            self.assertEqual(len(rows), 1)
            self.assertIn("consumes", _predicates(rows[0]))
            json.dumps(rows)

    def test_mapping_destroy_counter_gainlife_and_untap(self):
        row = parse_forge_text(
            "\n".join(
                [
                    "Name: Interaction Suite",
                    "A:SP$ Destroy | ValidTgts$ Creature",
                    "A:SP$ Counter | ValidTgts$ Card",
                    "A:SP$ GainLife | LifeAmount$ 3",
                    "A:AB$ Untap | Cost$ T | ValidTgts$ Creature",
                    "A:SP$ DestroyAll | ValidCards$ Creature",
                    "T:Mode$ Untaps | ValidCard$ Creature",
                ]
            ),
            "interaction.txt",
        )
        mapped = apply_mapping(
            row,
            _load_mapping(ROOT / "data" / "ontology" / "forge_mapping.yaml"),
            load_schema(),
        )
        answers = {
            item["arguments"].get("threat_class")
            for item in mapped["candidates"]
            if item["predicate"] == "answers"
        }
        self.assertIn("creature", answers)
        self.assertIn("stack", answers)
        self.assertIn("board", answers)
        self.assertTrue(
            any(
                item["predicate"] == "produces" and item["arguments"].get("object") == "life"
                for item in mapped["candidates"]
            )
        )
        self.assertTrue(
            any(
                item["predicate"] == "emits" and item["arguments"].get("event") == "lifegain"
                for item in mapped["candidates"]
            )
        )
        self.assertTrue(
            any(
                item["predicate"] == "enables"
                and item["arguments"].get("capability") == "untapper"
                for item in mapped["candidates"]
            )
        )
        self.assertTrue(
            any(
                item["predicate"] == "rewards" and item["arguments"].get("event") == "untap"
                for item in mapped["candidates"]
            )
        )

    def test_split_alternate_mode_preserves_face_names_for_join(self):
        row = parse_forge_text(
            "\n".join(
                [
                    "Name: Claim",
                    "AlternateMode: Split",
                    "Types: Sorcery",
                    "A:SP$ ChangeZone | Origin$ Graveyard | Destination$ Battlefield",
                    "ALTERNATE",
                    "Name: Fame",
                    "Types: Sorcery",
                    "K:Aftermath",
                ]
            ),
            "claim.txt",
        )
        self.assertEqual(row["card"]["name"], "Claim")
        self.assertEqual([face["name"] for face in row["card"]["faces"]], ["Claim", "Fame"])
        self.assertEqual(row["card"]["faces"][0]["metadata"].get("AlternateMode"), "Split")


if __name__ == "__main__":
    unittest.main()
