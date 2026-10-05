"""Fase 2: compilação em operadores e dominância.

Cada caso de "não comparável" abaixo é um erro real encontrado na conferência
manual da amostra de relações; o teste impede que ele volte.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from operators.compile import compile_script  # noqa: E402
from operators.dominance import dominance, ge  # noqa: E402

S = {
    "Lightning Bolt": "Name:Lightning Bolt\nManaCost:R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3",
    "Shock": "Name:Shock\nManaCost:R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 2",
    "Counterspell": "Name:Counterspell\nManaCost:U U\nTypes:Instant\nA:SP$ Counter | TargetType$ Spell | ValidTgts$ Card",
    "Cancel": "Name:Cancel\nManaCost:1 U U\nTypes:Instant\nA:SP$ Counter | TargetType$ Spell | TgtPrompt$ Select target spell | ValidTgts$ Card",
    "Murder": "Name:Murder\nManaCost:1 B B\nTypes:Instant\nA:SP$ Destroy | ValidTgts$ Creature",
    "Doom Blade": "Name:Doom Blade\nManaCost:1 B\nTypes:Instant\nA:SP$ Destroy | ValidTgts$ Creature.nonBlack",
    "Goblin Grenade": "Name:Goblin Grenade\nManaCost:R\nTypes:Sorcery\nA:SP$ DealDamage | Cost$ R Sac<1/Goblin> | ValidTgts$ Any | NumDmg$ 5",
    "Burning Fields": "Name:Burning Fields\nManaCost:4 R\nTypes:Sorcery\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 5",
    "Tangled Islet": "Name:Tangled Islet\nManaCost:no cost\nTypes:Land Forest Island\n"
                     "R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped\n"
                     "SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True",
    "Radiant Summit": "Name:Radiant Summit\nManaCost:no cost\nTypes:Land Mountain Plains\n"
                      "R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped\n"
                      "SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True",
    "Guildgate": "Name:Guildgate\nManaCost:no cost\nTypes:Land Gate\n"
                 "R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped\n"
                 "SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True\nA:AB$ Mana | Cost$ T | Produced$ Combo U R",
    "Fastland": "Name:Fastland\nManaCost:no cost\nTypes:Land\n"
                "R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped | IsPresent$ Land.YouCtrl+Other | PresentCompare$ GT2\n"
                "SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True\nA:AB$ Mana | Cost$ T | Produced$ Combo U R",
    "Clone A": "Name:Clone A\nManaCost:3 U\nTypes:Creature Shapeshifter\nPT:0/0\n"
               "K:ETBReplacement:Copy:DBCopy:Optional\nSVar:DBCopy:DB$ Clone | Choices$ Creature.Other",
    "Clone B": "Name:Clone B\nManaCost:4 U\nTypes:Creature Shapeshifter\nPT:0/0\n"
               "K:ETBReplacement:Copy:DBCopy:Optional\nSVar:DBCopy:DB$ Clone | Choices$ Creature.YouCtrl+Other | AddTriggers$ X",
    "Lash": "Name:Lash\nManaCost:B\nTypes:Instant\nA:SP$ Pump | ValidTgts$ Creature | NumAtt$ +2 | NumDef$ -2",
    "Stab": "Name:Stab\nManaCost:B\nTypes:Instant\nA:SP$ Pump | ValidTgts$ Creature | NumAtt$ -2 | NumDef$ -2",
    "Chain to Memory": "Name:Chain to Memory\nManaCost:U\nTypes:Instant\nA:SP$ Pump | ValidTgts$ Creature | NumAtt$ -4",
    "Labyrinth": "Name:Labyrinth\nManaCost:U\nTypes:Instant\nA:SP$ Pump | ValidTgts$ Creature | NumAtt$ -3",
    "Comet Crawler": "Name:Comet Crawler\nManaCost:2 B\nTypes:Creature Insect\nPT:2/3\nK:Lifelink\n"
                     "T:Mode$ Attacks | ValidCard$ Card.Self | Execute$ TrigPump\n"
                     "SVar:TrigPump:AB$ Pump | Cost$ Sac<1/Creature.Other> | Defined$ Self | NumAtt$ +2",
    "Vicious Kavu": "Name:Vicious Kavu\nManaCost:1 B R\nTypes:Creature Kavu\nPT:2/3\n"
                    "T:Mode$ Attacks | ValidCard$ Card.Self | Execute$ TrigPump\n"
                    "SVar:TrigPump:DB$ Pump | Defined$ Self | NumAtt$ +2",
    "Deception": "Name:Deception\nManaCost:2 B\nTypes:Sorcery\nA:SP$ Discard | ValidTgts$ Opponent | NumCards$ 2 | Mode$ TgtChoose",
    "Extortion": "Name:Extortion\nManaCost:3 B B\nTypes:Sorcery\nA:SP$ Discard | ValidTgts$ Opponent | NumCards$ 2 | Mode$ LookYouChoose",
    "Bear": "Name:Bear\nManaCost:1 G\nTypes:Creature Bear\nPT:2/2",
    "Adventurer": "Name:Adventurer\nManaCost:1 G\nTypes:Creature Human\nPT:2/2\nALTERNATE\n"
                  "Name:Go\nManaCost:G\nTypes:Instant Adventure\nA:SP$ Pump | ValidTgts$ Creature | NumAtt$ +2 | NumDef$ +2",
    "X Bolt": "Name:X Bolt\nManaCost:2 R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ X\nSVar:X:Count$Valid Artifact.YouCtrl",
    "Y Bolt": "Name:Y Bolt\nManaCost:2 R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ X\nSVar:X:Count$InYourYard",
}


def card(name):
    return compile_script(S[name])


class CompileTests(unittest.TestCase):
    def test_magnitude_target_and_speed(self):
        bolt = card("Lightning Bolt")
        (op,) = bolt.operators
        self.assertEqual((op.source, op.speed, op.frequency), ("spell", "instant", "once"))
        self.assertEqual(op.steps[0].magnitude, (("NumDmg", "3"),))
        self.assertEqual(op.steps[0].target, "Any")

    def test_spell_additional_cost_is_kept(self):
        self.assertIn("Sac<1/Goblin>", card("Goblin Grenade").operators[0].cost_other)

    def test_svar_variables_resolve_to_their_definition(self):
        x = card("X Bolt").operators[0].steps[0].magnitude
        y = card("Y Bolt").operators[0].steps[0].magnitude
        self.assertNotEqual(x, y)

    def test_other_faces_are_compiled(self):
        self.assertEqual(len(card("Adventurer").faces), 1)


class DominanceTests(unittest.TestCase):
    def assertDominates(self, a, b):
        self.assertTrue(ge(card(a), card(b)), f"{a} ≥ {b}")
        self.assertFalse(ge(card(b), card(a)), f"not {b} ≥ {a}")

    def assertIncomparable(self, a, b):
        self.assertFalse(ge(card(a), card(b)), f"{a} ≥ {b}")
        self.assertFalse(ge(card(b), card(a)), f"{b} ≥ {a}")

    def test_canonical_pairs(self):
        self.assertDominates("Lightning Bolt", "Shock")
        self.assertDominates("Counterspell", "Cancel")

    def test_trade_offs_are_incomparable(self):
        self.assertIncomparable("Murder", "Doom Blade")

    def test_additional_costs_count(self):
        self.assertIncomparable("Goblin Grenade", "Burning Fields")

    def test_basic_land_types_are_mana_abilities(self):
        self.assertIncomparable("Tangled Islet", "Radiant Summit")

    def test_conditions_on_a_drawback_are_good(self):
        self.assertDominates("Fastland", "Guildgate")

    def test_clone_content_is_read_through_svars(self):
        self.assertIncomparable("Clone A", "Clone B")

    def test_pump_signs(self):
        self.assertIncomparable("Lash", "Stab")
        self.assertDominates("Chain to Memory", "Labyrinth")

    def test_cost_inside_a_trigger(self):
        self.assertIncomparable("Comet Crawler", "Vicious Kavu")

    def test_discard_mode_is_shape(self):
        self.assertIncomparable("Deception", "Extortion")

    def test_symbolic_amounts_only_match_same_count(self):
        self.assertIncomparable("X Bolt", "Y Bolt")

    def test_adventure_is_not_ignored(self):
        self.assertIncomparable("Bear", "Adventurer")

    def test_dominance_relations_and_equivalents(self):
        cards = [card(n) for n in ("Lightning Bolt", "Shock", "Counterspell", "Cancel")]
        cards.append(compile_script(S["Shock"].replace("Name:Shock", "Name:Shock Reprint")))
        relations, equivalents = dominance(cards)
        pairs = {(r.dominator, r.dominated) for r in relations}
        self.assertEqual(pairs, {
            ("Lightning Bolt", "Shock"), ("Lightning Bolt", "Shock Reprint"),
            ("Counterspell", "Cancel"),
        })
        self.assertIn(["Shock", "Shock Reprint"], equivalents)


class SolverOrderingTests(unittest.TestCase):
    """Singleton: B waits while its dominator A is addable; once A is in, B may follow."""

    def setUp(self):
        sys.path.insert(0, SRC_DIR)
        from catalog import ensure_schema

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self.db = tmp.name
        conn = sqlite3.connect(self.db)
        ensure_schema(conn)
        for cid, name, tl, ident, ot, cmc, cost in [
            ("c", "Red Captain", "Legendary Creature — Human", ["R"], "", 2.0, "{1}{R}"),
            ("b", "Lightning Bolt", "Instant", ["R"], "Lightning Bolt deals 3 damage to any target.", 1.0, "{R}"),
            ("s", "Shock", "Instant", ["R"], "Shock deals 2 damage to any target.", 1.0, "{R}"),
            ("m", "Mountain", "Basic Land — Mountain", [], "({T}: Add {R}.)", 0.0, ""),
        ]:
            conn.execute(
                "INSERT INTO cards (id, name, mana_cost, cmc, oracle_text, color_identity, "
                "type_line, legalities, price_usd, price_eur) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, name, cost, cmc, ot, json.dumps(ident), tl, json.dumps({"commander": "legal"}), 1.0, 1.0),
            )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db)

    def test_dominated_card_waits_for_its_dominator(self):
        from deck_state import DeckState
        from solver import DeckSolver

        solver = DeckSolver(self.db)
        solver._view_store = None
        deck = DeckState(commander="Red Captain", identity=["R"], cards={"Mountain": 30})
        relations = {"shock": (("Lightning Bolt", False),)}
        with patch("solver.load_dominators", return_value=relations):
            solver._rebuild_context(deck, "")
            shock = solver._info("Shock")
            self.assertEqual(solver._dominated_reason(shock, deck), "dominated by Lightning Bolt")
            deck.add_card("Lightning Bolt", 1)
            solver._rebuild_context(deck, "")
            self.assertIsNone(solver._dominated_reason(shock, deck))
            names = solver._gather_names(DeckState(commander="Red Captain", identity=["R"],
                                                   candidate_pool={"Shock": 1}), "", None, False)
            self.assertIn("Lightning Bolt", names)


if __name__ == "__main__":
    unittest.main()
