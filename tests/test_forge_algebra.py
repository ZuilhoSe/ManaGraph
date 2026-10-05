import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from forge_ability_inventory import card_atoms  # noqa: E402
from forge_algebra_coverage import classify_card, coverage, load_algebra  # noqa: E402

ALGEBRA = ROOT / "data" / "ontology" / "operator_algebra_v1.yaml"

SCRIPTS = {
    "Sol Ring": """Name:Sol Ring
ManaCost:1
Types:Artifact
A:AB$ Mana | Cost$ T | Produced$ C | Amount$ 2 | SpellDescription$ Add {C}{C}.
""",
    "Doubling Season": """Name:Doubling Season
ManaCost:4 G
Types:Enchantment
R:Event$ CreateToken | ActiveZones$ Battlefield | ValidToken$ Card.YouCtrl | ReplaceWith$ DoubleToken
SVar:DoubleToken:DB$ ReplaceToken | Type$ Amount
R:Event$ AddCounter | ActiveZones$ Battlefield | ValidCard$ Permanent.YouCtrl | ReplaceWith$ DoubleCounters
SVar:DoubleCounters:DB$ ReplaceCounter | Amount$ Y
SVar:Y:ReplaceCount$CounterNum/Twice
""",
    "Platinum Angel": """Name:Platinum Angel
ManaCost:7
Types:Artifact Creature Angel
PT:4/4
K:Flying
R:Event$ GameLoss | ActiveZones$ Battlefield | ValidPlayer$ You | Layer$ CantHappen
R:Event$ GameWin | ActiveZones$ Battlefield | ValidPlayer$ Opponent | Layer$ CantHappen
""",
    "Reanimate": """Name:Reanimate
ManaCost:B
Types:Sorcery
A:SP$ ChangeZone | Origin$ Graveyard | Destination$ Battlefield | ValidTgts$ Creature | GainControl$ True | SubAbility$ DBLoseLife
SVar:DBLoseLife:DB$ LoseLife | Defined$ You | LifeAmount$ X
""",
    "Glorious Anthem": """Name:Glorious Anthem
ManaCost:1 W W
Types:Enchantment
S:Mode$ Continuous | Affected$ Creature.YouCtrl | AddPower$ 1 | AddToughness$ 1
""",
    "Clone": """Name:Clone
ManaCost:3 U
Types:Creature Shapeshifter
PT:0/0
K:ETBReplacement:Copy:DBCopy:Optional
SVar:DBCopy:DB$ Clone | Choices$ Creature.Other
""",
    "Mogg Fanatic": """Name:Mogg Fanatic
ManaCost:R
Types:Creature Goblin
PT:1/1
A:AB$ DealDamage | Cost$ Sac<1/CARDNAME> | ValidTgts$ Any | NumDmg$ 1
""",
}


def atoms(name):
    return card_atoms(SCRIPTS[name].splitlines(keepends=True))[1]


class InventoryTests(unittest.TestCase):
    def test_api_trigger_replacement_and_cost_atoms(self):
        self.assertEqual(atoms("Sol Ring"), {"api:Mana", "cost:Tap"})
        self.assertEqual(
            atoms("Doubling Season"),
            {"replacement:CreateToken", "replacement:AddCounter",
             "api:ReplaceToken", "api:ReplaceCounter"},
        )
        self.assertIn("cost:Sac", atoms("Mogg Fanatic"))

    def test_change_zone_is_split_by_zones(self):
        self.assertIn("api:ChangeZone.Graveyard>Battlefield", atoms("Reanimate"))

    def test_continuous_static_is_split_by_effect(self):
        self.assertEqual(
            atoms("Glorious Anthem"),
            {"static:Continuous.AddPower", "static:Continuous.AddToughness"},
        )

    def test_front_face_name(self):
        name, _, meta = card_atoms(SCRIPTS["Sol Ring"].splitlines(keepends=True))
        self.assertEqual((name, meta["Types"]), ("Sol Ring", "Artifact"))


class AlgebraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.version, cls.mapping, cls.generic = load_algebra(ALGEBRA)

    def test_contract_loads_without_conflicts(self):
        self.assertTrue(self.version)
        self.assertGreater(len(self.mapping), 500)
        self.assertTrue(self.generic <= set(self.mapping))

    def test_second_order_and_masks_are_inside(self):
        for name in ("Sol Ring", "Doubling Season", "Platinum Angel", "Reanimate",
                     "Glorious Anthem", "Mogg Fanatic"):
            verdict, blocking = classify_card(sorted(atoms(name)), self.mapping)
            self.assertNotEqual(verdict, "out", (name, blocking))
        self.assertEqual(self.mapping["replacement:GameLoss"], "mask")
        self.assertEqual(self.mapping["api:ReplaceToken"], "modifier")

    def test_former_out_families_have_semantics(self):
        verdict, blocking = classify_card(sorted(atoms("Clone")), self.mapping)
        self.assertEqual((verdict, blocking), ("strict", set()))
        expected = {
            "api:Clone": "reference",
            "api:CopySpellAbility": "reference",
            "static:Continuous.SetPower": "layer",
            "static:Continuous.RemoveAllAbilities": "layer",
            "keyword:Morph": "face_down",
            "api:Manifest": "face_down",
            "api:AddTurn": "turn",
            "api:AddPhase": "turn",
            "api:WinsGame": "terminal",
            "api:LosesGame": "terminal",
            "api:TwoPiles": "adversarial",
            "api:Vote": "adversarial",
            "api:ChangeZone.Sideboard>Hand": "noop",
        }
        for atom, cls in expected.items():
            self.assertEqual(self.mapping[atom], cls, atom)

    def test_no_out_families_left_in_contract(self):
        self.assertFalse([a for a, cls in self.mapping.items() if cls.startswith("out:")])

    def test_layer_index_follows_cr_613(self):
        import yaml

        with open(ALGEBRA, encoding="utf-8") as handle:
            layers = yaml.safe_load(handle)["layer_index"]
        self.assertIn("api:Clone", layers["1"])
        self.assertIn("static:Continuous.AddType", layers["4"])
        self.assertIn("static:Continuous.SetPower", layers["7b"])
        self.assertIn("static:Continuous.AddPower", layers["7c"])
        indexed = [atom for atoms in layers.values() for atom in atoms]
        self.assertEqual(len(indexed), len(set(indexed)))

    def test_unmapped_atoms_count_as_out(self):
        self.assertEqual(classify_card(["api:NotARealApi"], self.mapping)[1], {"unmapped"})

    def test_coverage_counts_only_commander_legal(self):
        inventory = {"cards": [
            {"name": "A", "commander_legal": True, "atoms": ["api:Mana"]},
            {"name": "B", "commander_legal": True, "atoms": ["api:NotARealApi"]},
            {"name": "C", "commander_legal": False, "atoms": ["api:NotARealApi"]},
        ]}
        report = coverage(inventory, self.mapping)
        self.assertEqual(report["total"], 2)
        self.assertAlmostEqual(report["strict_pct"], 0.5)
        self.assertEqual(report["families"]["unmapped"], 1)


if __name__ == "__main__":
    unittest.main()
