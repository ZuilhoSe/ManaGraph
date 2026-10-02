"""Fase 1: auditoria de cobertura do Forge contra a álgebra de operadores."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_forge_coverage import audit  # noqa: E402
from operators.algebra import MASK, MODIFIER, OUT, RESOURCE, UNKNOWN, classify, static_value  # noqa: E402

SCRIPTS = {
    "l/lotus_field.txt": """Name:Lotus Field
ManaCost:no cost
Types:Land
R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplacementResult$ Updated | ReplaceWith$ ETBTapped | Description$ CARDNAME enters tapped.
SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True
K:Hexproof
T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ TrigSac | TriggerDescription$ When CARDNAME enters, sacrifice two lands.
SVar:TrigSac:DB$ Sacrifice | Amount$ 2 | Defined$ You | SacValid$ Land
A:AB$ Mana | Cost$ T | Produced$ Any | Amount$ 3 | SpellDescription$ Add three mana of any one color.
Oracle:Hexproof\\nLotus Field enters tapped.\\nWhen Lotus Field enters, sacrifice two lands.\\n{T}: Add three mana of any one color.
""",
    "c/clone.txt": """Name:Clone
ManaCost:3 U
Types:Creature Shapeshifter
PT:0/0
K:ETBReplacement:Copy:DBCopy:Optional
SVar:DBCopy:DB$ Clone | Choices$ Creature.Other | SpellDescription$ You may have CARDNAME enter as a copy of any creature on the battlefield.
Oracle:You may have Clone enter as a copy of any creature on the battlefield.
""",
    "h/humility_test.txt": """Name:Humility Test
ManaCost:2 W W
Types:Enchantment
S:Mode$ Continuous | Affected$ Creature | RemoveAllAbilities$ True | SetPower$ 1 | SetToughness$ 1 | Description$ All creatures lose all abilities and have base power and toughness 1/1.
Oracle:All creatures lose all abilities and have base power and toughness 1/1.
""",
    "p/plane_test.txt": """Name:Plane Test
ManaCost:no cost
Types:Plane Somewhere
Oracle:Nothing.
""",
    "rebalanced/a-lotus.txt": """Name:A-Lotus
ManaCost:no cost
Types:Land
A:AB$ Mana | Cost$ T | Produced$ Any | SpellDescription$ Add one mana of any color.
Oracle:{T}: Add one mana of any color.
""",
}


class AlgebraTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(classify("api", "Draw"), RESOURCE)
        self.assertEqual(classify("api", "Clone"), OUT)
        self.assertEqual(classify("api", "NotARealApi"), UNKNOWN)
        self.assertEqual(classify("static", "ReduceCost"), MODIFIER)
        self.assertEqual(classify("static", "CantAttack,CantBlock"), MASK)
        self.assertEqual(classify("replacement", "GameLoss"), MASK)
        self.assertEqual(classify("keyword", "Flying"), MODIFIER)
        self.assertEqual(classify("keyword", "Mutate"), OUT)

    def test_layer_rewrites_are_out(self):
        label = static_value("Continuous", {"RemoveAllAbilities": "True"})
        self.assertEqual(classify("static", label), OUT)
        self.assertEqual(classify("static", static_value("Continuous", {"AddKeyword": "Flying"})), MODIFIER)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for rel, text in SCRIPTS.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_coverage_counts(self):
        result = audit(self.root)
        by_name = {c["name"]: c for c in result["cards"]}
        self.assertNotIn("Plane Test", by_name)  # non-traditional type excluded
        self.assertNotIn("A-Lotus", by_name)  # rebalanced/ skipped by default
        self.assertTrue(by_name["Lotus Field"]["abstractable"])
        self.assertFalse(by_name["Clone"]["abstractable"])
        self.assertFalse(by_name["Humility Test"]["abstractable"])
        self.assertAlmostEqual(result["summary"]["coverage"], 1 / 3, places=3)
        self.assertEqual(result["summary"]["vocabulary_unknown"], [])

    def test_include_all(self):
        names = {c["name"] for c in audit(self.root, include_all=True)["cards"]}
        self.assertIn("A-Lotus", names)


if __name__ == "__main__":
    unittest.main()
