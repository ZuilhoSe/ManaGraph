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
MAPPING = ROOT / "data" / "ontology" / "forge_mapping.yaml"


def _predicates(row):
    return {
        item["predicate"]
        for item in row["candidates"]
        if item["predicate"]
    }


def _mapped(text, filename="fixture.txt"):
    return apply_mapping(
        parse_forge_text(text, filename),
        _load_mapping(MAPPING),
        load_schema(),
    )


def _arg_values(row, predicate, key):
    return [
        item["arguments"].get(key)
        for item in row["candidates"]
        if item["predicate"] == predicate
    ]


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
            _load_mapping(MAPPING),
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

    def test_mapping_add_phase_extra_combat(self):
        row = parse_forge_text(
            "\n".join(
                [
                    "Name: Extra Combat Fixture",
                    "A:SP$ AddPhase | ExtraPhase$ Combat",
                ]
            ),
            "extra_combat.txt",
        )
        mapped = apply_mapping(
            row,
            _load_mapping(MAPPING),
            load_schema(),
        )
        self.assertTrue(
            any(
                item["predicate"] == "enables"
                and item["arguments"].get("capability") == "extra_combat"
                for item in mapped["candidates"]
            )
        )
        self.assertTrue(
            any(
                item["predicate"] == "emits"
                and item["arguments"].get("event") == "attack"
                for item in mapped["candidates"]
            )
        )

    def test_pump_hexproof_protects_but_self_keyword_does_not(self):
        granted = _mapped(
            "\n".join(
                [
                    "Name: Protection Grant Fixture",
                    "A:AB$ Pump | Cost$ 1 | ValidTgts$ Creature | KW$ Hexproof",
                ]
            )
        )
        self.assertIn("commander", _arg_values(granted, "protects", "target_class"))
        self.assertIn("protection", _arg_values(granted, "enables", "capability"))

        self_kw = _mapped("Name: Self Hexproof Fixture\nK:Hexproof\n")
        self.assertNotIn("protects", _predicates(self_kw))
        self.assertNotIn("protection", _arg_values(self_kw, "enables", "capability"))

        equipped = _mapped(
            "\n".join(
                [
                    "Name: Equipment Protection Fixture",
                    "S:Mode$ Continuous | Affected$ Creature.EquippedBy | AddKeyword$ Hexproof",
                ]
            )
        )
        self.assertIn("commander", _arg_values(equipped, "protects", "target_class"))

    def test_add_keyword_haste_grants_but_self_haste_does_not(self):
        granted = _mapped(
            "\n".join(
                [
                    "Name: Haste Grant Fixture",
                    "S:Mode$ Continuous | Affected$ Creature.YouCtrl | AddKeyword$ Haste",
                ]
            )
        )
        self.assertIn("haste_grant", _arg_values(granted, "enables", "capability"))

        self_kw = _mapped("Name: Self Haste Fixture\nK:Haste\n")
        self.assertNotIn("haste_grant", _arg_values(self_kw, "enables", "capability"))

    def test_keyword_convoke_enables_convoke_like(self):
        mapped = _mapped("Name: Convoke Fixture\nK:Convoke\n")
        self.assertIn("convoke_like", _arg_values(mapped, "enables", "capability"))

        svar = _mapped("Name: Convoke SVar Fixture\nSVar:Convoke:Count$YouCtrl\n")
        self.assertNotIn("convoke_like", _arg_values(svar, "enables", "capability"))

    def test_reduce_cost_enables_cost_reduction(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Cost Reduction Fixture",
                    "S:Mode$ ReduceCost | Type$ Instant",
                ]
            )
        )
        self.assertIn("cost_reduction", _arg_values(mapped, "enables", "capability"))

    def test_deal_damage_answers_creature_not_player(self):
        creature = _mapped(
            "\n".join(
                [
                    "Name: Damage Creature Fixture",
                    "A:SP$ DealDamage | ValidTgts$ Creature | NumDmg$ 3",
                ]
            )
        )
        self.assertIn("creature", _arg_values(creature, "answers", "threat_class"))

        player = _mapped(
            "\n".join(
                [
                    "Name: Damage Player Fixture",
                    "A:SP$ DealDamage | ValidTgts$ Player | NumDmg$ 3",
                ]
            )
        )
        self.assertNotIn("answers", _predicates(player))

    def test_library_search_change_type_card_tutors_any(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Tutor Any Fixture",
                    "A:SP$ ChangeZone | Origin$ Library | Destination$ Hand | ChangeType$ Card",
                ]
            )
        )
        self.assertIn("any", _arg_values(mapped, "tutors", "selector"))

        reorder = _mapped(
            "\n".join(
                [
                    "Name: Library Reorder Fixture",
                    "A:SP$ ChangeZone | Origin$ Library | Destination$ Library | ChangeType$ Card",
                ]
            )
        )
        self.assertNotIn("tutors", _predicates(reorder))

    def test_spell_cast_valid_card_does_not_leak_forge_dsl(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Spell Reward Fixture",
                    "T:Mode$ SpellCast | ValidCard$ Instant.Blue",
                ]
            )
        )
        rewards = [
            item["arguments"]
            for item in mapped["candidates"]
            if item["predicate"] == "rewards"
        ]
        self.assertTrue(rewards)
        self.assertTrue(all(item.get("event") == "cast_spell" for item in rewards))
        types = [item.get("type") for item in rewards]
        self.assertNotIn("Instant.Blue", types)
        self.assertTrue(all(value in {None, "any", "creature", "instant", "sorcery"} for value in types))
        self.assertIn("instant", types)

        mixed = _mapped(
            "\n".join(
                [
                    "Name: Mixed Spell Reward Fixture",
                    "T:Mode$ SpellCast | ValidCard$ Instant,Sorcery",
                ]
            )
        )
        mixed_rewards = [
            item["arguments"]
            for item in mixed["candidates"]
            if item["predicate"] == "rewards"
        ]
        self.assertTrue(all(item.get("event") == "cast_spell" for item in mixed_rewards))
        self.assertTrue(all("type" not in item for item in mixed_rewards))

    def test_condition_threshold_requires_precondition(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Threshold Fixture",
                    "A:AB$ Pump | Cost$ T | NumAtt$ +3 | Condition$ Threshold",
                ]
            )
        )
        self.assertIn("threshold", _arg_values(mapped, "requires", "precondition"))

    def test_svar_subability_db_draw_maps_like_ability_line(self):
        svar = _mapped(
            "\n".join(
                [
                    "Name: SVar Draw Fixture",
                    "A:AB$ Token | Cost$ T | TokenScript$ c_1_1_goblin | SubAbility$ DBDraw",
                    "SVar:DBDraw:DB$ Draw | NumCards$ 1",
                ]
            )
        )
        self.assertIn("card_in_hand", _arg_values(svar, "produces", "object"))
        self.assertIn("draw", _arg_values(svar, "emits", "event"))
        self.assertIn("token_created", _arg_values(svar, "emits", "event"))

        chained = _mapped(
            "\n".join(
                [
                    "Name: SubAbility Draw Fixture",
                    "A:AB$ Token | Cost$ T | TokenScript$ c_1_1_goblin | SubAbility$ DBDraw",
                    "A:DB$ Draw | NumCards$ 1",
                ]
            )
        )
        self.assertIn("card_in_hand", _arg_values(chained, "produces", "object"))
        self.assertIn("draw", _arg_values(chained, "emits", "event"))

    def test_mill_produces_card_in_graveyard(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Mill Fixture",
                    "A:SP$ Mill | NumCards$ 3 | Defined$ You",
                ]
            )
        )
        self.assertIn("card_in_graveyard", _arg_values(mapped, "produces", "object"))

    def test_fight_answers_creature(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Fight Fixture",
                    "A:SP$ Fight | ValidTgts$ Creature | Defined$ Self",
                ]
            )
        )
        self.assertIn("creature", _arg_values(mapped, "answers", "threat_class"))

    def test_damage_all_answers_board(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: DamageAll Fixture",
                    "A:SP$ DamageAll | ValidCards$ Creature | NumDmg$ 2",
                ]
            )
        )
        self.assertIn("board", _arg_values(mapped, "answers", "threat_class"))
        self.assertIn("creature", _arg_values(mapped, "answers", "threat_class"))

    def test_play_land_and_adjust_land_plays_produce_land_in_play(self):
        play = _mapped(
            "\n".join(
                [
                    "Name: Play Land Fixture",
                    "A:SP$ Play | Valid$ Land | ExtraLand$ True",
                ]
            )
        )
        self.assertIn("land_in_play", _arg_values(play, "produces", "object"))
        self.assertIn("landfall", _arg_values(play, "emits", "event"))

        extra = _mapped(
            "\n".join(
                [
                    "Name: Extra Land Fixture",
                    "S:Mode$ Continuous | Affected$ You | AdjustLandPlays$ 1",
                ]
            )
        )
        self.assertIn("land_in_play", _arg_values(extra, "produces", "object"))
        self.assertIn("landfall", _arg_values(extra, "emits", "event"))

    def test_change_zone_all_bounce(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: ChangeZoneAll Bounce Fixture",
                    "A:SP$ ChangeZoneAll | Origin$ Battlefield | Destination$ Hand | ChangeType$ Creature",
                ]
            )
        )
        self.assertIn("bounce", _arg_values(mapped, "emits", "event"))
        self.assertIn("creature", _arg_values(mapped, "answers", "threat_class"))

    def test_mana_combo_color_is_not_an_argument(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Combo Mana Fixture",
                    "A:AB$ Mana | Cost$ T | Produced$ Combo R G",
                ]
            )
        )
        colors = _arg_values(mapped, "produces", "color")
        self.assertNotIn("Combo R G", colors)
        self.assertTrue(
            any(
                item["predicate"] == "produces"
                and item["arguments"].get("object") == "mana"
                for item in mapped["candidates"]
            )
        )
        self.assertTrue(
            any(
                "Combo R G" in str(item.get("evidence"))
                for item in mapped["candidates"]
                if item["predicate"] == "produces"
            )
        )

    def test_add_turn_is_not_extra_combat(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Extra Turn Fixture",
                    "A:SP$ AddTurn | NumTurns$ 1 | Defined$ You",
                ]
            )
        )
        self.assertNotIn("extra_combat", _arg_values(mapped, "enables", "capability"))
        self.assertNotIn("enables", _predicates(mapped))

    def test_token_script_stays_in_evidence_not_arguments(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Token Script Fixture",
                    "A:AB$ Token | Cost$ T | TokenScript$ c_a_treasure_sac",
                ]
            )
        )
        self.assertIn("treasure", _arg_values(mapped, "produces", "object"))
        scripts = [
            item["arguments"].get("token_script")
            for item in mapped["candidates"]
            if item["predicate"] == "produces"
        ]
        self.assertTrue(all(value is None for value in scripts))

    def test_battlefield_to_hand_emits_bounce(self):
        mapped = _mapped(
            "\n".join(
                [
                    "Name: Bounce Fixture",
                    "A:SP$ ChangeZone | Origin$ Battlefield | Destination$ Hand | ValidTgts$ Creature",
                ]
            )
        )
        self.assertIn("bounce", _arg_values(mapped, "emits", "event"))
        self.assertIn("creature", _arg_values(mapped, "answers", "threat_class"))


if __name__ == "__main__":
    unittest.main()
