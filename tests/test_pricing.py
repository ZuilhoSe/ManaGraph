"""Fase 4: preços latentes π e força de carta (scripts embutidos, sem o Forge)."""

import os
import sys
import unittest

import numpy as np

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from operators.compile import compile_script  # noqa: E402
from operators.pricing import (  # noqa: E402
    CardVector, card_vector, commander_features, era_group, fit_prices, load_prices, save_prices,
)


def vector(script):
    return card_vector(compile_script(script))


BOLT = "Name:Bolt\nManaCost:R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3"
HORROR = ("Name:Horror\nManaCost:B B\nTypes:Creature Horror\nPT:7/7\nK:Trample\n"
          "T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ T\n"
          "SVar:T:DB$ Token | ValidTgts$ Opponent | TokenAmount$ 2 | TokenOwner$ Targeted | TokenScript$ g_3_3_centaur")
WALL = ("Name:Wall\nManaCost:1 W\nTypes:Creature Wall\nPT:0/4\nK:Defender\n"
        "T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ D\n"
        "SVar:D:DB$ Draw | Defined$ You | NumCards$ 1")
EYE = ("Name:Eye\nManaCost:4\nTypes:Artifact\n"
       "T:Mode$ Drawn | ValidCard$ Card.OppOwn | TriggerZones$ Battlefield | Execute$ D\n"
       "SVar:D:DB$ Draw | Defined$ You | NumCards$ 1 | Cost$ 1")
GRAY = ("Name:Gray\nManaCost:3 B B\nTypes:Creature Zombie\nPT:2/4\n"
        "T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ValidCard$ Card.Self | Execute$ L\n"
        "SVar:L:DB$ LoseLife | Defined$ Player.Opponent | LifeAmount$ 2")


class FeatureTests(unittest.TestCase):
    def test_effects_carry_magnitude_and_back_off_keys(self):
        bolt = vector(BOLT)
        self.assertEqual(bolt.features["once|DealDamage|tgt_Any"], 3)
        self.assertEqual(bolt.features["once|DealDamage"], 3)
        chain = next(c for c, _ in bolt.groups if c[0] == "once|DealDamage|tgt_Any")
        # weaker variants: conditional, and "creature" (any target can hit one)
        self.assertEqual(set(chain), {"once|DealDamage|tgt_Any", "once|DealDamage|tgt_Any|?", "once|DealDamage|tgt_Creature",
                                      "once|DealDamage|tgt_Creature|?", "once|DealDamage"})
        self.assertEqual(bolt.features["speed|instant"], 1)
        self.assertEqual(bolt.excluded, "")

    def test_chain_holds_every_weaker_variant(self):
        downfall = vector("Name:Downfall\nManaCost:1 B B\nTypes:Instant\n"
                          "A:SP$ Destroy | ValidTgts$ Creature,Planeswalker")
        murder = vector("Name:Murder\nManaCost:1 B B\nTypes:Instant\nA:SP$ Destroy | ValidTgts$ Creature")
        broad = next(chain for chain, _ in downfall.groups if chain[0].startswith("once|Destroy"))
        narrow = next(chain for chain, _ in murder.groups if chain[0].startswith("once|Destroy"))
        self.assertTrue(set(narrow) < set(broad))  # broad = narrow + non-negative increments

    def test_drawbacks_go_to_the_cost_side(self):
        horror = vector(HORROR)
        self.assertEqual(horror.signs["once|Token-gift|creature"], -1)
        self.assertEqual(horror.features["once|Token-gift|pt"], 12)  # 2 × (3 + 3)
        wall = vector(WALL)
        self.assertEqual(wall.signs["kw|Defender"], -1)
        self.assertEqual(wall.signs["once|Draw|you"], 1)

    def test_tokens_are_priced_by_kind(self):
        treasure = vector("Name:T\nManaCost:1\nTypes:Sorcery\n"
                          "A:SP$ Token | TokenScript$ c_a_treasure_sac | TokenOwner$ You")
        self.assertIn("once|Token|treasure", treasure.features)
        self.assertNotIn("once|Token|pt", treasure.features)

    def test_instant_speed_has_a_floor(self):
        from operators.pricing import FLOORS
        bolt = vector(BOLT)
        self.assertEqual(bolt.signs["speed|instant"], 1)
        self.assertGreater(FLOORS["speed|instant"], 0)

    def test_payment_inside_the_effect_discounts_it(self):
        eye = vector(EYE)
        self.assertEqual(eye.features["event_opp:Drawn|Draw|you"], 0.5)  # 1 card for {1}

    def test_alternative_costs_and_lands_do_not_anchor_the_fit(self):
        self.assertEqual(vector("Name:Plains\nManaCost:no cost\nTypes:Basic Land Plains").excluded, "terreno")
        self.assertTrue(vector("Name:Fling\nManaCost:X R\nTypes:Instant\nA:SP$ DealDamage | ValidTgts$ Any | NumDmg$ X")
                        .excluded.startswith("custo X"))
        self.assertTrue(vector("Name:Hasty\nManaCost:2 R\nTypes:Creature\nPT:2/2\nK:Dash:R").excluded
                        .startswith("custo alternativo"))

    def test_commander_rules_scale_each_opponent_and_life(self):
        gray = vector(GRAY)
        edh = commander_features(gray)
        # LoseLife on each opponent: × 3 opponents × ½ (40 life) on the whole chain
        for key in ("once|LoseLife|each_opp", "once|LoseLife"):
            self.assertAlmostEqual(edh[key], gray.features[key] * 1.5)
        eye = commander_features(vector(EYE))
        self.assertAlmostEqual(eye["rep|Draw|you"], 1.5)

    def test_era_groups(self):
        self.assertEqual(era_group("1994-04-01", "Expansion"), "era|1993–96|set")
        self.assertEqual(era_group("2021-04-23", "Commander"), "era|2020–22|commander")


def synthetic(n=600, seed=0):
    """Cards priced by a known π: damage 0.5, draw 0.8, power 0.4, defender −1."""
    rng = np.random.default_rng(seed)
    vecs, groups = [], []
    for i in range(n):
        v = CardVector(f"c{i}", 0)
        dmg, draw, power = rng.integers(0, 5), rng.integers(0, 4), rng.integers(0, 6)
        defender = rng.random() < 0.3
        v.add("once|DealDamage", float(dmg))
        v.add("once|Draw", float(draw))
        v.add("body|power", float(power))
        if defender:
            v.add("kw|Defender", 1.0, -1)
        old = rng.random() < 0.5
        cost = 1 + 0.5 * dmg + 0.8 * draw + 0.4 * power - 1.0 * defender + (0.5 if old else 0) + rng.normal(0, 0.2)
        v.mana_value = max(0, int(round(cost)))
        vecs.append(v)
        groups.append("era|old" if old else "era|new")
    return vecs, groups


class FitTests(unittest.TestCase):
    def test_recovers_known_prices_and_power_creep(self):
        vecs, groups = synthetic()
        prices = fit_prices(vecs, groups, min_support=5, l2=0.1)
        pi = dict(zip(prices.features, prices.weights))
        self.assertAlmostEqual(pi["once|DealDamage"], 0.5, delta=0.1)
        self.assertAlmostEqual(pi["once|Draw"], 0.8, delta=0.1)
        self.assertAlmostEqual(pi["kw|Defender"], 1.0, delta=0.2)
        self.assertGreater(prices.intercepts["era|old"] - prices.intercepts["era|new"], 0.3)

    def test_strength_orders_same_effect_at_different_costs(self):
        vecs, groups = synthetic()
        prices = fit_prices(vecs, groups, min_support=5, l2=0.1)
        cheap, dear = CardVector("cheap", 2), CardVector("dear", 3)
        for v in (cheap, dear):
            v.add("once|DealDamage", 3.0)
        self.assertGreater(prices.strength(cheap.features, 2), prices.strength(dear.features, 3))

    def test_strength_has_no_bias_by_cost(self):
        vecs, groups = synthetic()
        prices = fit_prices(vecs, groups, min_support=5, l2=0.1)
        by_mv = {}
        for v in vecs:
            by_mv.setdefault(v.mana_value, []).append(prices.strength(v.features, v.mana_value))
        for mv, values in by_mv.items():
            if len(values) >= 30:
                self.assertAlmostEqual(float(np.mean(values)), 0.0, delta=0.15)

    def test_round_trip(self):
        import tempfile
        from pathlib import Path
        vecs, groups = synthetic(200)
        prices = fit_prices(vecs, groups, min_support=5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            save_prices(prices, path)
            loaded = load_prices(path)
        v = vecs[0]
        self.assertAlmostEqual(loaded.strength(v.features, v.mana_value), prices.strength(v.features, v.mana_value),
                               places=4)


if __name__ == "__main__":
    unittest.main()


class FittedPositivityTests(unittest.TestCase):
    """On the fitted prices (data/ontology/prices_v1.json): every benefit strictly raises a
    card's strength at the same mana cost and body, every cost strictly lowers it."""

    KEYWORDS = ("Flying", "Trample", "Haste", "Vigilance", "Deathtouch", "Lifelink", "First Strike",
                "Double Strike", "Menace", "Reach", "Hexproof", "Indestructible", "Ward:2", "Flash", "Prowess",
                "Shroud", "Fear", "Intimidate", "Shadow", "Horsemanship", "Exalted")

    @classmethod
    def setUpClass(cls):
        cls.prices = load_prices()
        if cls.prices is None:
            raise unittest.SkipTest("prices_v1.json not built (scripts/fit_prices.py)")

    def strength(self, script):
        vec = vector(script)
        return self.prices.strength(vec.features, vec.mana_value)

    def test_every_keyword_beats_the_same_body_without_it(self):
        for p in range(0, 7):
            base = f"Name:C\nManaCost:{max(1, p)}\nTypes:Creature Beast\nPT:{p}/{max(1, p)}"
            plain = self.strength(base)
            for kw in self.KEYWORDS:
                with self.subTest(keyword=kw, body=p):
                    self.assertGreater(self.strength(base + f"\nK:{kw}"), plain)

    def test_combat_keywords_grow_with_the_body(self):
        def gain(kw, p):
            base = f"Name:C\nManaCost:{p}\nTypes:Creature Beast\nPT:{p}/{p}"
            return self.strength(base + f"\nK:{kw}") - self.strength(base)
        for kw in ("Trample", "Haste", "Flying", "Double Strike"):
            self.assertGreater(gain(kw, 6), gain(kw, 1), kw)

    def test_drawbacks_strictly_lower_strength(self):
        base = "Name:C\nManaCost:3\nTypes:Creature Beast\nPT:3/3"
        for kw in ("Defender", "Echo:3", "Cumulative upkeep:1"):
            with self.subTest(keyword=kw):
                self.assertLess(self.strength(base + f"\nK:{kw}"), self.strength(base))

    def test_instant_beats_the_same_sorcery(self):
        for body in ("A:SP$ Draw | Defined$ You | NumCards$ 2", "A:SP$ Destroy | ValidTgts$ Creature",
                     "A:SP$ DealDamage | ValidTgts$ Any | NumDmg$ 3"):
            sorcery = self.strength(f"Name:S\nManaCost:2 B\nTypes:Sorcery\n{body}")
            instant = self.strength(f"Name:I\nManaCost:2 B\nTypes:Instant\n{body}")
            self.assertGreater(instant, sorcery, body)

    def test_every_canonical_effect_is_worth_something(self):
        scripts_dir = os.path.join(os.path.dirname(SRC_DIR), "scripts")
        sys.path.insert(0, scripts_dir)
        import fit_prices
        for row in fit_prices.effect_prices(self.prices):
            if row.startswith("| **"):
                continue
            label, design, edh = [c.strip() for c in row.strip("|").split("|")]
            with self.subTest(effect=label):
                if "custo" in label:
                    self.assertLess(float(design), 0)
                else:
                    self.assertGreater(float(design), 0)
                    self.assertGreater(float(edh), 0)
