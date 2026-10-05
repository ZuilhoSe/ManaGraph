"""Fase 2.5: relógio de dano (sem o Forge: cartas sintéticas e scripts embutidos)."""

import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from operators.clock import (  # noqa: E402
    ClockCard, ClockParams, basic_land, clock_features, simulate, vanilla,
)
from operators.compile import compile_script  # noqa: E402

FOREST = basic_land()
NOTHING = ClockCard(name="(sem efeito)", cmc=3)
NO_BLOCKS = ClockParams(horizon=10, blockers_cap=0)


def features(script):
    return clock_features(compile_script(script))


class FeatureTests(unittest.TestCase):
    def test_reads_magnitudes_off_operators(self):
        rock = features("Name:Rock\nManaCost:1\nTypes:Artifact\nA:AB$ Mana | Cost$ T | Produced$ C | Amount$ 2")
        self.assertEqual(rock.mana, 2)
        ramp = features("Name:Ramp\nManaCost:1 G\nTypes:Sorcery\n"
                        "A:SP$ ChangeZone | Origin$ Library | Destination$ Battlefield | ChangeType$ Land.Basic | ChangeNum$ 1 | Tapped$ True")
        self.assertEqual(ramp.ramp_lands, 1)
        overrun = features("Name:Run\nManaCost:2 G G G\nTypes:Sorcery\n"
                           "A:SP$ PumpAll | ValidCards$ Creature.YouCtrl | NumAtt$ +3 | NumDef$ +3 | KW$ Trample")
        self.assertEqual((overrun.overrun, overrun.overrun_trample), (3, True))
        hoof = features("Name:Hoof\nManaCost:5 G G G\nTypes:Creature Beast\nPT:5/5\nK:Haste\n"
                        "T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ P\n"
                        "SVar:P:DB$ PumpAll | ValidCards$ Creature.YouCtrl | NumAtt$ +X | NumDef$ +X | KW$ Trample\n"
                        "SVar:X:Count$Valid Creature.YouCtrl")
        self.assertTrue(hoof.overrun_per_creature and hoof.haste)

    def test_conditional_effects_are_not_promised(self):
        outcast = features("Name:Outcast\nManaCost:R\nTypes:Creature Human\nPT:1/1\n"
                           "T:Mode$ Phase | Phase$ Upkeep | ValidPlayer$ You | IsPresent$ Land.YouCtrl | PresentCompare$ GE6 | Execute$ T\n"
                           "SVar:T:DB$ Token | TokenScript$ r_5_5_dragon_flying | TokenOwner$ You")
        self.assertEqual(outcast.tokens_per_turn, ())
        endurance = features("Name:Endurance\nManaCost:2 W W\nTypes:Enchantment\n"
                             "T:Mode$ Phase | Phase$ Upkeep | ValidPlayer$ You | LifeTotal$ You | LifeAmount$ GE50 | Execute$ W\n"
                             "SVar:W:DB$ WinsGame | Defined$ You")
        self.assertFalse(endurance.alt_win)

    def test_tap_token_factory_and_equipment(self):
        krenko = features("Name:Boss\nManaCost:2 R R\nTypes:Legendary Creature Goblin\nPT:3/3\n"
                          "A:AB$ Token | Cost$ T | TokenAmount$ X | TokenScript$ r_1_1_goblin\nSVar:X:Count$Valid Goblin.YouCtrl")
        self.assertEqual(krenko.tap_tokens, (1, 1, 1))
        self.assertTrue(krenko.tap_tokens_scale)
        sword = features("Name:Sword\nManaCost:2\nTypes:Artifact Equipment\nK:Equip:2\n"
                         "S:Mode$ Continuous | Affected$ Creature.EquippedBy | AddPower$ 1 | AddToughness$ 1")
        self.assertEqual((sword.attach_power, sword.equip_cost), (1, 2))


class ClockTests(unittest.TestCase):
    def test_ramp_brings_the_top_of_the_curve_two_attacks_earlier(self):
        big, ramp = vanilla("Big", 7, 7), vanilla("Ramp", 2, ramp_lands=1)
        dmg = {
            label: simulate([], params=NO_BLOCKS, order=[FOREST] * 5 + top + [FOREST] * 12).damage_by[10]
            for label, top in (("none", [NOTHING] * 3), ("big", [big, NOTHING, NOTHING]),
                               ("ramp", [ramp, ramp, NOTHING]), ("both", [big, ramp, ramp]))
        }
        synergy = dmg["both"] - dmg["big"] - dmg["ramp"] + dmg["none"]
        self.assertEqual(synergy, 14)  # two more attacks with a 7/7

    def test_haste_attacks_the_turn_it_arrives(self):
        order = [FOREST, FOREST, vanilla("Rager", 2, 2, haste=True)] + [FOREST] * 10
        slow = [FOREST, FOREST, vanilla("Bear", 2, 2)] + [FOREST] * 10
        self.assertGreater(simulate([], params=NO_BLOCKS, order=order).damage_by[2],
                           simulate([], params=NO_BLOCKS, order=slow).damage_by[2])

    def test_blockers_stop_ground_but_not_evasion_or_trample(self):
        p = ClockParams(horizon=8, blocker_rate=1.0)
        ground = [FOREST] * 4 + [vanilla("G", 4, 4)] + [FOREST] * 10
        flyer = [FOREST] * 4 + [vanilla("F", 4, 4, evasive=True)] + [FOREST] * 10
        trampler = [FOREST] * 4 + [vanilla("T", 4, 4, trample=True)] + [FOREST] * 10
        g = simulate([], params=p, order=ground).damage_by[8]
        f = simulate([], params=p, order=flyer).damage_by[8]
        t = simulate([], params=p, order=trampler).damage_by[8]
        self.assertGreater(f, t)
        self.assertGreater(t, g)

    def test_overrun_grows_with_the_board(self):
        p = ClockParams(horizon=10, samples=150)
        bear, run = vanilla("Bear", 2, 2), vanilla("Run", 5, overrun=3, overrun_trample=True)
        gains = []
        for k in (5, 25):
            base = [FOREST] * 37 + [bear] * k + [NOTHING] * (62 - k)
            gains.append(simulate(base[:-1] + [run], params=p).damage_by[8] - simulate(base, params=p).damage_by[8])
        self.assertLess(gains[0], gains[1])

    def test_commander_stays_available_and_counts_commander_damage(self):
        p = ClockParams(horizon=12, blockers_cap=0)
        boss = vanilla("Boss", 3, 10, evasive=True)  # 9 attacks × 8.5 ≥ 63 = 3 × 21
        result = simulate([], commander=boss, params=p, order=[FOREST] * 20)
        self.assertGreater(result.damage_by[12], 0)
        self.assertIn("commander", result.terminals)

    def test_infect_goes_to_poison(self):
        p = ClockParams(horizon=12, blockers_cap=0)
        order = [FOREST] * 3 + [vanilla("Infector", 3, 5, evasive=True, infect=True)] + [FOREST] * 15
        result = simulate([], params=p, order=order)
        self.assertEqual(result.damage_by[12], 0)
        self.assertIn("poison", result.terminals)


if __name__ == "__main__":
    unittest.main()
