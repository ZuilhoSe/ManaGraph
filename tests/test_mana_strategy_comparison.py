"""Comparative regression: a real generated deck run through both mana-target
strategies (StaticQuotaStrategy vs HypergeometricStrategy).

Uses data/deck_lightning_army_of_one.json -- a real Boros Commander deck the
solver actually produced -- as fixture, per the calibrate-with-real-data
preference (real decklists over hand-typed synthetic curves). The deck is
reloaded through DeckState + diagnose_deck() so curve_by_cmc/pip_reqs_by_color
come from the local catalog (data/managraph.db), not from the JSON's own
cached (bucketed, static-only) report fields.
"""

import json
import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

from deck_state import DeckState
from deck_analysis.diagnose import diagnose_deck
from deck_analysis.strategies import HypergeometricStrategy, StaticQuotaStrategy
from hypergeometric import recommended_land_count


def _load_lightning_deck() -> DeckState:
    path = os.path.join(DATA_DIR, "deck_lightning_army_of_one.json")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return DeckState.from_dict(payload["deck"])


class TestLightningArmyOfOneManaTargets(unittest.TestCase):
    """Lightning, Army of One (R/W, real 99-card build, 25 lands) -- a concrete
    case where the two strategies agree on one axis and sharply disagree on
    another.
    """

    @classmethod
    def setUpClass(cls):
        cls.deck = _load_lightning_deck()
        cls.static_report = diagnose_deck(cls.deck, strategy=StaticQuotaStrategy())
        cls.hyper_report = diagnose_deck(cls.deck, strategy=HypergeometricStrategy())

    def test_land_target_is_close_between_strategies(self):
        """Both agree the deck is severely land-short; the hypergeometric target
        sits near the top of the static band, not wildly off it -- this axis
        looks trustworthy.
        """
        self.assertEqual(self.static_report["land_alert"], {
            "status": "low", "count": 25, "quota": [34, 38],
            "delta": 9, "pct": 0.265, "severity": "severe",
        })
        self.assertEqual(self.hyper_report["land_alert"], {
            "status": "low", "count": 25, "quota": [36, 38],
            "delta": 11, "pct": 0.306, "severity": "severe",
        })

    def test_color_floors_diverge_sharply_on_two_color_decks(self):
        """Known limitation (see project_hypergeometric_land_calc memory): color_share
        isn't normalized by the deck's color count, so a 2-color deck pushes both
        colors' shares close to 0.5 each, driving target_confidence_color near its
        ceiling. Here the static floor is a flat 10/10, but the hypergeometric one
        asks for more combined color sources (18 R + 22 W) than its own land target
        -- not trustworthy yet for 2-color decks without further calibration.
        """
        self.assertEqual(self.static_report["min_sources"], {"R": 10, "W": 10})
        self.assertEqual(self.hyper_report["min_sources"], {"R": 18, "W": 22})
        combined_color_floor = sum(self.hyper_report["min_sources"].values())
        land_target = recommended_land_count(self.hyper_report["curve_by_cmc"])
        self.assertGreater(combined_color_floor, land_target)


if __name__ == "__main__":
    unittest.main()
