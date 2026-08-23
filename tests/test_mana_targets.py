"""Characterization tests for the mana-target contract (land + color-source floors).

These pin down the StaticQuotaStrategy's exact numbers -- every value here was
computed from the real code, not hand-derived, so swapping in a different
strategy (e.g. hypergeometric) later can be checked against these same numbers
to prove the propagation to shape_bonus()/solver still "sees" the same signal.
"""

import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from deck_analysis.diagnose import diagnose
from deck_analysis.mana_base import MIN_SOURCES, color_floors, land_alert, min_sources_for
from deck_analysis.shape_bonus import shape_bonus


class TestMinSourcesFor(unittest.TestCase):
    def test_matches_min_sources_table_by_identity_size(self):
        rainbow = ["W", "U", "B", "R", "G"]
        for n_colors, expected in MIN_SOURCES.items():
            self.assertEqual(min_sources_for(rainbow[:n_colors]), expected)

    def test_empty_identity_treated_as_one_color(self):
        self.assertEqual(min_sources_for([]), MIN_SOURCES[1])

    def test_floor_depends_on_color_count_not_which_colors(self):
        self.assertEqual(min_sources_for(["R", "G"]), min_sources_for(["W", "U"]))


class TestLandAlert(unittest.TestCase):
    def test_ok_inside_quota(self):
        alert = land_alert(36)
        self.assertEqual(alert, {
            "status": "ok", "count": 36, "quota": [34, 38], "delta": 0, "pct": 0.0, "severity": "none",
        })

    def test_mild_low(self):
        alert = land_alert(33)
        self.assertEqual(alert, {
            "status": "low", "count": 33, "quota": [34, 38], "delta": 1, "pct": 0.029, "severity": "mild",
        })

    def test_severe_low(self):
        alert = land_alert(10)
        self.assertEqual(alert, {
            "status": "low", "count": 10, "quota": [34, 38], "delta": 24, "pct": 0.706, "severity": "severe",
        })

    def test_severe_high(self):
        alert = land_alert(60)
        self.assertEqual(alert, {
            "status": "high", "count": 60, "quota": [34, 38], "delta": 22, "pct": 0.579, "severity": "severe",
        })


class TestDiagnoseCurveAndPipsBaseline(unittest.TestCase):
    """report["curve"] (string-bucketed, "0".."7+") and report["pips"] (summed
    per-color floats) -- collected in the same per-card loop as curve_by_cmc/
    pip_reqs_by_color (see TestDiagnoseUniversalInput) but independent of them.
    """

    def _report(self):
        commander = {"mana_cost": "{2}{R}{R}", "color_identity": ["R", "G"]}
        cards = [
            {"name": "Mountain", "quantity": 10, "type_line": "Basic Land — Mountain",
             "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0},
            {"name": "Ornithopter", "quantity": 2, "type_line": "Artifact Creature",
             "oracle_text": "", "mana_cost": "{0}", "cmc": 0},
            {"name": "Bolt", "quantity": 3, "type_line": "Instant",
             "oracle_text": "", "mana_cost": "{R}", "cmc": 1},
            {"name": "Twin", "quantity": 1, "type_line": "Creature",
             "oracle_text": "", "mana_cost": "{R/G}{R/G}", "cmc": 2},
            {"name": "Elder", "quantity": 1, "type_line": "Creature",
             "oracle_text": "", "mana_cost": "{4}{G}{G}{G}", "cmc": 7},
            {"name": "Huge", "quantity": 1, "type_line": "Creature",
             "oracle_text": "", "mana_cost": "{8}{R}", "cmc": 9},
        ]
        return diagnose(cards, commander=commander, identity=["R", "G"])

    def test_curve_bucket_counts(self):
        report = self._report()
        self.assertEqual(report["curve"], {
            "0": 2, "1": 3, "2": 1, "3": 0, "4": 0, "5": 0, "6": 0, "7+": 2,
        })

    def test_pips_and_sources(self):
        report = self._report()
        self.assertEqual(report["pips"]["R"], 7.0)
        self.assertEqual(report["pips"]["G"], 4.0)
        self.assertEqual(report["sources"]["R"], 10.0)
        self.assertEqual(report["min_sources"], {"R": 10, "G": 10})


class TestDiagnoseUniversalInput(unittest.TestCase):
    """report["curve_by_cmc"] and report["pip_reqs_by_color"] -- the richer per-card
    data any mana-target strategy can now ask for, whether or not it uses it. A
    0-cost pip-bearing card lands in curve_by_cmc[0] (real CMC) but pip_reqs_by_color
    buckets it at CMC 1 (a free spell's color still can't be paid before turn 1).
    """

    def test_curve_by_cmc_keeps_real_zero(self):
        report = diagnose(
            [
                {"name": "Mountain", "quantity": 10, "type_line": "Basic Land — Mountain",
                 "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0},
                {"name": "FreeSpell", "quantity": 1, "type_line": "Instant",
                 "oracle_text": "", "mana_cost": "{R}", "cmc": 0},
                {"name": "Bolt", "quantity": 3, "type_line": "Instant",
                 "oracle_text": "", "mana_cost": "{R}", "cmc": 1},
                {"name": "Elder", "quantity": 1, "type_line": "Creature",
                 "oracle_text": "", "mana_cost": "{4}{G}{G}{G}", "cmc": 7},
            ],
            commander={"mana_cost": "{2}{R}{R}", "color_identity": ["R", "G"]},
            identity=["R", "G"],
        )
        self.assertEqual(report["curve_by_cmc"], {0: 1, 1: 3, 7: 1})

    def test_pip_reqs_by_color_pools_per_card_and_remaps_zero_cost(self):
        report = diagnose(
            [
                {"name": "Mountain", "quantity": 10, "type_line": "Basic Land — Mountain",
                 "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0},
                {"name": "FreeSpell", "quantity": 1, "type_line": "Instant",
                 "oracle_text": "", "mana_cost": "{R}", "cmc": 0},
                {"name": "Bolt", "quantity": 3, "type_line": "Instant",
                 "oracle_text": "", "mana_cost": "{R}", "cmc": 1},
                {"name": "Twin", "quantity": 1, "type_line": "Creature",
                 "oracle_text": "", "mana_cost": "{R/G}{R/G}", "cmc": 2},
                {"name": "Elder", "quantity": 1, "type_line": "Creature",
                 "oracle_text": "", "mana_cost": "{4}{G}{G}{G}", "cmc": 7},
            ],
            commander={"mana_cost": "{2}{R}{R}", "color_identity": ["R", "G"]},
            identity=["R", "G"],
        )
        self.assertEqual(
            sorted(report["pip_reqs_by_color"]["R"]),
            [(1, 1), (1, 1), (1, 1), (1, 1), (2, 1)],
        )
        self.assertEqual(sorted(report["pip_reqs_by_color"]["G"]), [(2, 1), (7, 3)])

    def test_land_alert_and_color_floors_accept_and_ignore_the_new_input(self):
        """StaticQuotaStrategy contract: the new params exist on the functions
        (universal input shape) but the static numbers don't move because of them.
        """
        report = diagnose(
            [{"name": "Mountain", "quantity": 5, "type_line": "Basic Land — Mountain",
              "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0}],
            commander={"mana_cost": "{2}{R}{R}", "color_identity": ["R"]},
            identity=["R"],
        )
        self.assertEqual(land_alert(5), land_alert(5, report["curve_by_cmc"]))
        self.assertEqual(
            color_floors(["R"]),
            color_floors(["R"], report["pip_reqs_by_color"]),
        )


class TestDiagnoseSourceFloor(unittest.TestCase):
    """diagnose()'s report["min_sources"] is a per-color dict, keyed by identity color --
    the shape a future per-color-varying strategy (e.g. hypergeometric) also needs.
    """

    def test_min_sources_is_the_per_color_floor_dict(self):
        report = diagnose(
            [{"name": "Mountain", "quantity": 5, "type_line": "Basic Land — Mountain",
              "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0}],
            commander={"mana_cost": "{2}{R}{R}", "color_identity": ["R"]},
            identity=["R"],
        )
        self.assertEqual(report["min_sources"], {"R": 14})


class TestShapeBonusManaSignal(unittest.TestCase):
    """Pins the exact mana_bonus/land_bonus numbers shape_bonus() derives from
    min_sources + pips_per_source, run through the real diagnose() -> shape_bonus()
    path (no mocks) -- this is the boundary solver.py actually consumes.
    """

    def setUp(self):
        self.commander = {"mana_cost": "{2}{R}{R}", "color_identity": ["R"]}

    def test_land_candidate_gets_needed_color_bump(self):
        report = diagnose(
            [
                {"name": "Mountain", "quantity": 5, "type_line": "Basic Land — Mountain",
                 "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0},
                {"name": "Test Burn", "quantity": 1, "type_line": "Instant",
                 "oracle_text": "", "mana_cost": "{R}", "cmc": 1},
            ],
            commander=self.commander,
            identity=["R"],
        )
        bonus = shape_bonus(
            {"type_line": "Basic Land — Mountain", "oracle_text": "{T}: Add {R}.",
             "mana_cost": "", "cmc": 0},
            report,
            ["R"],
        )
        self.assertEqual(bonus, {
            "total": 3.7, "curve_penalty": 0.0, "curve_bonus": 0.0,
            "land_bonus": 3.2, "mana_bonus": 0.5,
        })

    def test_nonland_fixer_gets_needed_color_bump(self):
        report = diagnose(
            [
                {"name": "Mountain", "quantity": 5, "type_line": "Basic Land — Mountain",
                 "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0},
                {"name": "Test Burn", "quantity": 1, "type_line": "Instant",
                 "oracle_text": "", "mana_cost": "{R}", "cmc": 1},
            ],
            commander=self.commander,
            identity=["R"],
        )
        rock = {"type_line": "Artifact", "oracle_text": "{T}: Add {R}.", "mana_cost": "{2}", "cmc": 2}
        bonus = shape_bonus(rock, report, ["R"])
        self.assertEqual(bonus["mana_bonus"], 0.6)

    def test_strained_pips_per_source_penalizes_heavy_candidate(self):
        report = diagnose(
            [
                {"name": "Mountain", "quantity": 2, "type_line": "Basic Land — Mountain",
                 "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0},
                {"name": "Pentaburn", "quantity": 1, "type_line": "Sorcery",
                 "oracle_text": "", "mana_cost": "{R}{R}{R}{R}{R}", "cmc": 5},
            ],
            commander=self.commander,
            identity=["R"],
        )
        self.assertGreater(report["pips_per_source"]["R"], 2.0)
        strained = {"type_line": "Sorcery", "oracle_text": "", "mana_cost": "{R}{R}", "cmc": 2}
        bonus = shape_bonus(strained, report, ["R"])
        self.assertEqual(bonus["mana_bonus"], -0.4)


if __name__ == "__main__":
    unittest.main()
