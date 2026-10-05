"""Fase 3: previsão do Δ de recursos a partir dos operadores (sem o Forge).

Os casos vêm dos padrões de erro que o oráculo do Forge revelou na amostra de
desenvolvimento; o teste trava a semântica corrigida.
"""

import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from operators.compile import compile_script  # noqa: E402
from operators.predict import compare, predict, setup_for  # noqa: E402


def run(script):
    card = compile_script(script)
    setup = setup_for(card)
    return setup, (predict(card, setup) if setup else None)


class PredictTests(unittest.TestCase):
    def test_damage_to_the_opponent(self):
        setup, delta = run("Name:Bolt\nManaCost:R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3")
        self.assertIsNone(setup.target_class)
        self.assertEqual(delta, {"p0.life": -3})

    def test_lethal_damage_to_a_creature(self):
        setup, delta = run("Name:Fry\nManaCost:R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Creature | NumDmg$ 3")
        self.assertIn("p0battlefield=Grizzly Bears", setup.state)
        self.assertEqual(delta, {"p0.permanents": -1, "p0.graveyard": 1, "p0.creatures": -1,
                                 "p0.power": -2, "p0.toughness": -2})

    def test_exile_instead_of_dying(self):
        _, delta = run("Name:Coil\nManaCost:1 R\nTypes:Sorcery\n"
                       "A:SP$ DealDamage | ValidTgts$ Creature | NumDmg$ 4 | ReplaceDyingDefined$ Targeted")
        self.assertEqual(delta["p0.exile"], 1)
        self.assertNotIn("p0.graveyard", delta)

    def test_target_controller_gets_the_life(self):
        _, delta = run("Name:Breath\nManaCost:1 W\nTypes:Instant\n"
                       "A:SP$ ChangeZone | ValidTgts$ Creature.powerLE2 | Origin$ Battlefield | Destination$ Exile | SubAbility$ L\n"
                       "SVar:L:DB$ GainLife | Defined$ TargetedController | LifeAmount$ 4")
        self.assertEqual(delta["p0.life"], 4)
        self.assertNotIn("p1.life", delta)

    def test_token_for_the_target_controller(self):
        _, delta = run("Name:Swap\nManaCost:2 W\nTypes:Instant\n"
                       "A:SP$ ChangeZone | ValidTgts$ Creature | Origin$ Battlefield | Destination$ Exile | SubAbility$ T\n"
                       "SVar:T:DB$ Token | TokenScript$ c_1_1_shapeshifter_changeling | TokenOwner$ TargetedController")
        self.assertEqual(delta["p0.tokens"], 1)
        # A 2/2 leaves, a 1/1 token arrives: same creature count, one less power.
        self.assertEqual(delta.get("p0.creatures", 0), 0)
        self.assertEqual(delta["p0.power"], -1)

    def test_unsatisfiable_target_is_out_of_scope(self):
        setup, _ = run("Name:Reproach\nManaCost:1 W\nTypes:Instant\n"
                       "A:SP$ DealDamage | ValidTgts$ Creature.attacking,Creature.blocking | NumDmg$ 4")
        self.assertIsNone(setup)

    def test_you_control_puts_the_target_on_your_side(self):
        setup, delta = run("Name:Boost\nManaCost:G\nTypes:Instant\nA:SP$ Pump | ValidTgts$ Creature.YouCtrl | NumAtt$ +2 | NumDef$ +2")
        self.assertEqual(setup.target_side, "p1")
        self.assertIn("p1battlefield=Grizzly Bears", setup.state)
        self.assertEqual(delta, {"p1.power": 2, "p1.toughness": 2})

    def test_discard_respects_the_filter(self):
        _, delta = run("Name:Inquisition\nManaCost:B\nTypes:Sorcery\n"
                       "A:SP$ Discard | ValidTgts$ Player | Mode$ RevealYouChoose | DiscardValid$ Card.nonLand+cmcLE3 | NumCards$ 1")
        self.assertEqual(delta, {"p0.hand": -1, "p0.graveyard": 1})
        _, delta = run("Name:Strip\nManaCost:B\nTypes:Sorcery\n"
                       "A:SP$ Discard | ValidTgts$ Player | Mode$ RevealYouChoose | DiscardValid$ Card.Creature | NumCards$ 2")
        self.assertEqual(delta, {"p0.hand": -1, "p0.graveyard": 1})

    def test_each_player_mills_and_libraries_cap(self):
        _, delta = run("Name:Stone\nManaCost:3\nTypes:Artifact\nA:AB$ Mill | Cost$ 3 | NumCards$ 2 | Defined$ Player")
        self.assertEqual(delta, {"p0.library": -2, "p0.graveyard": 2, "p1.library": -2, "p1.graveyard": 2})
        _, delta = run("Name:Sanity\nManaCost:U U U\nTypes:Sorcery\nA:SP$ Mill | ValidTgts$ Opponent | NumCards$ 14")
        self.assertEqual(delta["p0.library"], -10)

    def test_artifact_creature_and_tapped_tokens(self):
        _, delta = run("Name:Thopters\nManaCost:2 U\nTypes:Sorcery\n"
                       "A:SP$ Token | TokenAmount$ 2 | TokenScript$ c_1_1_a_thopter_flying | TokenTapped$ True")
        self.assertEqual(delta["p1.artifacts"], 2)
        self.assertEqual(delta["p1.creatures"], 2)
        self.assertEqual(delta["p1.tapped"], 2)

    def test_keyword_activated_abilities_are_out_of_scope(self):
        setup, _ = run("Name:Karst\nManaCost:no cost\nTypes:Land\nK:Cycling:2\nA:AB$ Mana | Cost$ T | Produced$ G")
        self.assertIsNone(setup)

    def test_compare_tolerance(self):
        self.assertTrue(compare({"p0.life": -3}, {"p0.life": -3})[0])
        ok, misses = compare({"p0.life": -3}, {"p0.life": -3, "p1.hand": 1})
        self.assertFalse(ok)
        self.assertEqual(misses, {"p1.hand": (0, 1)})
        self.assertTrue(compare({"p1.library": -20}, {"p1.library": -21})[0])  # within 10%


if __name__ == "__main__":
    unittest.main()
