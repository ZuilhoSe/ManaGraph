"""Fase 2.5: relógio de dano (sem o Forge: cartas sintéticas e scripts embutidos)."""

import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from operators.clock import (  # noqa: E402
    ClockCard, ClockParams, basic_land, clock_features, simulate, vanilla, with_,
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


SCRIPTS = {
    "maze": "Name:Maze's End\nManaCost:no cost\nTypes:Land\n"
            "A:AB$ Mana | Cost$ T | Produced$ C\n"
            "A:AB$ ChangeZone | Cost$ 3 T Return<1/CARDNAME> | ChangeType$ Gate | ChangeNum$ 1 | Origin$ Library"
            " | Destination$ Battlefield | SubAbility$ DBWin\n"
            "SVar:DBWin:DB$ WinsGame | Defined$ You | ConditionCheckSVar$ MazeGate | ConditionSVarCompare$ GE10\n"
            "SVar:MazeGate:Count$Valid Gate.YouCtrl$DifferentCardNames",
    "artist": "Name:Blood Artist\nManaCost:1 B\nTypes:Creature Vampire\nPT:0/1\n"
              "T:Mode$ ChangesZone | Origin$ Battlefield | Destination$ Graveyard | ValidCard$ Card.Self,Creature.Other"
              " | TriggerZones$ Battlefield | Execute$ TrigLoseLife\n"
              "SVar:TrigLoseLife:DB$ LoseLife | ValidTgts$ Player | LifeAmount$ 1 | SubAbility$ DBGainLife\n"
              "SVar:DBGainLife:DB$ GainLife | Defined$ You | LifeAmount$ 1",
    "bombardment": "Name:Goblin Bombardment\nManaCost:1 R\nTypes:Enchantment\n"
                   "A:AB$ DealDamage | Cost$ Sac<1/Creature> | ValidTgts$ Any | NumDmg$ 1",
    "seer": "Name:Viscera Seer\nManaCost:B\nTypes:Creature Vampire Wizard\nPT:1/1\n"
            "A:AB$ Scry | Cost$ Sac<1/Creature> | ScryNum$ 1",
    "siege": "Name:Siege-Gang\nManaCost:3 R R\nTypes:Creature Goblin\nPT:2/2\n"
             "A:AB$ DealDamage | Cost$ 1 R Sac<1/Goblin> | ValidTgts$ Any | NumDmg$ 2",
    "bond": "Name:Sanguine Bond\nManaCost:3 B B\nTypes:Enchantment\n"
            "T:Mode$ LifeGained | ValidPlayer$ You | TriggerZones$ Battlefield | Execute$ TrigDrain\n"
            "SVar:TrigDrain:DB$ LoseLife | ValidTgts$ Opponent | LifeAmount$ X\nSVar:X:TriggerCount$LifeAmount",
    "blood": "Name:Exquisite Blood\nManaCost:4 B\nTypes:Enchantment\n"
             "T:Mode$ LifeLost | ValidPlayer$ Opponent | TriggerZones$ Battlefield | Execute$ TrigLifeGain\n"
             "SVar:TrigLifeGain:DB$ GainLife | Defined$ You | LifeAmount$ X\nSVar:X:TriggerCount$LifeAmount",
    "maniac": "Name:Laboratory Maniac\nManaCost:2 U\nTypes:Creature Human Wizard\nPT:2/2\n"
              "R:Event$ Draw | ActiveZones$ Battlefield | ValidPlayer$ You | IsPresent$ Card.YouOwn | PresentZone$ Library"
              " | PresentCompare$ EQ0 | ReplaceWith$ Win\nSVar:Win:DB$ WinsGame | Defined$ You",
    "oracle": "Name:Thassa's Oracle\nManaCost:U U\nTypes:Creature Merfolk Wizard\nPT:1/3\n"
              "T:Mode$ ChangesZone | ValidCard$ Card.Self | Origin$ Any | Destination$ Battlefield | Execute$ TrigDig\n"
              "SVar:TrigDig:DB$ Dig | DigNum$ X | ChangeNum$ 1 | DestinationZone$ Library | SubAbility$ DBWin\n"
              "SVar:DBWin:DB$ WinsGame | Defined$ You | ConditionCheckSVar$ Y | ConditionSVarCompare$ LEX\n"
              "SVar:X:Count$Devotion.Blue\nSVar:Y:Count$ValidLibrary Card.YouOwn",
    "approach": "Name:Approach of the Second Sun\nManaCost:6 W\nTypes:Sorcery\n"
                "A:SP$ Branch | BranchConditionSVar$ X | BranchConditionSVarCompare$ EQ3 | TrueSubAbility$ WinGame"
                " | FalseSubAbility$ GainLife\nSVar:WinGame:DB$ WinsGame | Defined$ You\n"
                "SVar:GainLife:DB$ GainLife | LifeAmount$ 7 | Defined$ You | SubAbility$ Reapproach\n"
                "SVar:Reapproach:DB$ ChangeZone | Origin$ Stack | Destination$ Library | LibraryPosition$ 6 | Defined$ Parent",
    "guttersnipe": "Name:Guttersnipe\nManaCost:2 R\nTypes:Creature Goblin Shaman\nPT:2/2\n"
                   "T:Mode$ SpellCast | ValidCard$ Instant,Sorcery | ValidActivatingPlayer$ You | TriggerZones$ Battlefield"
                   " | Execute$ TrigDamage\nSVar:TrigDamage:DB$ DealDamage | Defined$ Player.Opponent | NumDmg$ 2",
    "merchant": "Name:Gray Merchant\nManaCost:3 B B\nTypes:Creature Zombie\nPT:2/4\n"
                "T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ TrigLoseLife\n"
                "SVar:TrigLoseLife:DB$ LoseLife | Defined$ Player.Opponent | LifeAmount$ X\nSVar:X:Count$Devotion.Black",
}
ISLAND, SWAMP = basic_land("Island"), basic_land("Swamp")
LONG = ClockParams(horizon=30, blockers_cap=0)


def card(key):
    return features(SCRIPTS[key])


def gate(name):
    return ClockCard(name=name, land=True, types=frozenset({"Land"}), subtypes=frozenset({"Gate"}))


def instant(name="Opt", cmc=1, **kw):
    return ClockCard(name=name, cmc=cmc, types=frozenset({"Instant"}), **kw)


class KeywordMonotonicityTests(unittest.TestCase):
    """Adding a combat keyword to a creature never removes damage, on any body."""

    FLAGS = ("trample", "haste", "evasive", "double_strike", "lifelink")

    def deck(self, card):
        filler = [vanilla(f"Bear{i % 3}", 2, 2) for i in range(25)] + [vanilla("Ogre", 3, 3)] * 14
        return [FOREST] * 36 + filler + [card] * 24

    def test_every_keyword_on_every_body_is_at_least_as_fast(self):
        p = ClockParams(horizon=10, samples=60, blocker_rate=1.0)
        for power in range(1, 7):
            base = vanilla("T", power, power)
            d0 = simulate(self.deck(base), params=p).damage_by[10]
            for flag in self.FLAGS:
                d = simulate(self.deck(with_(base, **{flag: True})), params=p).damage_by[10]
                self.assertGreaterEqual(d, d0 - 1e-9, f"{flag} on {power}/{power}")

    def test_blocked_evasive_and_trampling_attackers_get_through(self):
        p = ClockParams(horizon=6, blocker_rate=3.0, blockers_start=1, blockers_cap=6)
        order = [FOREST] * 3 + [vanilla("X", 3, 3)] * 2 + [FOREST] * 10
        ground = simulate([], params=p, order=order).damage_by[6]
        for flag in ("evasive", "trample"):
            hit = simulate([], params=p, order=[with_(c, **{flag: True}) if c.creature else c for c in order])
            self.assertGreater(hit.damage_by[6], ground, flag)

    def test_lifelink_feeds_lifegain_payoffs(self):
        p = ClockParams(horizon=6, blockers_cap=0)
        bond = features("Name:Bond\nManaCost:2\nTypes:Enchantment\n"
                        "T:Mode$ LifeGained | ValidPlayer$ You | TriggerZones$ Battlefield | Execute$ D\n"
                        "SVar:D:DB$ LoseLife | ValidTgts$ Opponent | LifeAmount$ X\nSVar:X:TriggerCount$LifeAmount")
        order = [FOREST, FOREST, bond, vanilla("L", 2, 2), FOREST] + [FOREST] * 10
        plain = simulate([], params=p, order=order).life_sources["direct"]
        order[3] = vanilla("L", 2, 2, lifelink=True)
        self.assertGreater(simulate([], params=p, order=order).life_sources["direct"], plain)


class RouteFeatureTests(unittest.TestCase):
    def test_sac_outlets_count_even_without_a_clock_effect(self):
        self.assertEqual([(a.sac, a.sac_type, a.effects) for a in card("seer").abilities], [(True, "Creature", ())])
        siege = card("siege").abilities[0]
        self.assertEqual((siege.sac_type, siege.mana, siege.effects[0].amount), ("Goblin", 2, 2))

    def test_loop_pieces_read_the_event_amount(self):
        (bond,), (blood,) = card("bond").triggers, card("blood").triggers
        self.assertEqual((bond.event, bond.effects[0].kind, bond.effects[0].scale), ("lifegain", "drain", "trigger"))
        self.assertEqual((blood.event, blood.effects[0].kind), ("opp_lifeloss", "gain"))

    def test_variable_amount_is_evaluated_on_the_board(self):
        merchant = card("merchant")
        order = [SWAMP] * 5 + [merchant] + [SWAMP] * 10
        result = simulate([], params=ClockParams(horizon=5, blockers_cap=0), order=order)
        self.assertEqual(result.life_sources["direct"], 6)  # devotion 2 × 3 opponents


class RouteTests(unittest.TestCase):
    def test_mazes_end_needs_ten_gates_with_different_names(self):
        distinct = [gate(f"Gate {i}") for i in range(10)]
        same = [gate("Gate") for _ in range(10)]
        for gates, wins in ((distinct, True), (same, False)):
            order = [card("maze")] + [FOREST] * 6 + gates + [FOREST] * 30
            result = simulate([], params=LONG, order=order)
            self.assertEqual(result.terminals.get("alt_win", 0) == 1, wins)

    def test_aristocrats_drain_through_deaths(self):
        maker = ClockCard(name="Swarm", cmc=2, tokens=((3, 1, 1, ""),), types=frozenset({"Sorcery"}))
        base = [card("bombardment")] + [SWAMP] * 6 + [maker] * 6 + [SWAMP] * 20
        with_artist = base[:1] + [card("artist")] + base[2:]
        p = ClockParams(horizon=10, blockers_cap=0)
        plain, drained = simulate([], params=p, order=base), simulate([], params=p, order=with_artist)
        self.assertGreater(drained.life_sources["direct"], plain.life_sources["direct"])
        self.assertGreater(drained.life_sources["direct"], drained.life_sources["combat"])

    def test_unbounded_loop_drains_the_table(self):
        bolt = instant("Bolt", burn=3)
        order = [SWAMP] * 7 + [card("bond"), card("blood")] + [SWAMP] * 4 + [bolt] + [SWAMP] * 10
        result = simulate([], params=ClockParams(horizon=10, blockers_cap=0), order=order)
        self.assertEqual(result.terminals, {"damage": 1.0})
        self.assertEqual(result.life_sources["direct"], 120)

    def test_laboratory_maniac_wins_instead_of_decking(self):
        for hand, terminal in (([card("maniac")] + [ISLAND] * 6, "alt_win"), ([ISLAND] * 7, "decked")):
            result = simulate([], params=LONG, order=hand + [ISLAND] * 3)
            self.assertEqual(result.terminals, {terminal: 1.0})

    def test_thassas_oracle_waits_until_devotion_covers_the_library(self):
        order = [card("oracle")] + [ISLAND] * 6 + [ISLAND] * 6
        result = simulate([], params=LONG, order=order)
        self.assertEqual(result.terminals, {"alt_win": 1.0})
        self.assertEqual(result.turns, [4])  # library 6 → 2 cards = devotion UU

    def test_approach_wins_on_the_second_cast(self):
        order = [card("approach")] + [ISLAND] * 6 + [ISLAND] * 30
        result = simulate([], params=LONG, order=order)
        self.assertEqual(result.terminals, {"alt_win": 1.0})

    def test_guttersnipe_turns_spells_into_direct_damage(self):
        spells = [instant(f"Opt {i}", draw=1) for i in range(12)]
        order = [card("guttersnipe")] + [FOREST] * 6 + [x for pair in zip(spells, [FOREST] * 12) for x in pair]
        hit = simulate([], params=NO_BLOCKS, order=order)
        miss = simulate([], params=NO_BLOCKS, order=[FOREST] + order[1:])
        self.assertGreater(hit.life_sources["direct"], 0)
        self.assertEqual(miss.life_sources["direct"], 0)


if __name__ == "__main__":
    unittest.main()
