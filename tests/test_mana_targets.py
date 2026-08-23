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

from deck_analysis.diagnose import diagnose, diagnose_deck
from deck_analysis.mana_base import MIN_SOURCES, color_floors, land_alert, min_sources_for
from deck_analysis.shape_bonus import shape_bonus
from deck_analysis.strategies import HypergeometricStrategy, StaticQuotaStrategy, strategy_from_name
from deck_state import DeckState
from hypergeometric import color_pip_share, recommended_color_sources, recommended_land_count


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
    Pinned against StaticQuotaStrategy explicitly since min_sources below is a
    strategy-dependent number, not the curve/pips collection this class is about.
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
        return diagnose(cards, commander=commander, identity=["R", "G"], strategy=StaticQuotaStrategy())

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


class TestStaticQuotaStrategy(unittest.TestCase):
    """StaticQuotaStrategy is a thin wrapper -- must match the free functions exactly."""

    def test_land_target_matches_land_alert(self):
        self.assertEqual(
            StaticQuotaStrategy().land_target(33, {1: 4, 2: 3}),
            land_alert(33, {1: 4, 2: 3}),
        )

    def test_color_floors_matches_color_floors(self):
        pip_reqs = {"R": [(1, 1), (2, 1)], "G": [(7, 3)]}
        self.assertEqual(
            StaticQuotaStrategy().color_floors(["R", "G"], pip_reqs),
            color_floors(["R", "G"], pip_reqs),
        )


class TestHypergeometricStrategy(unittest.TestCase):
    """Adapter correctness: same numbers hypergeometric.py itself would produce,
    wrapped into the land_alert/color_floors output shapes.
    """

    def test_land_target_bands_around_recommended_land_count_by_one(self):
        curve = {1: 8, 2: 10, 3: 6, 4: 4, 7: 1}
        target = recommended_land_count(curve)
        alert = HypergeometricStrategy().land_target(target, curve)
        self.assertEqual(alert, {
            "status": "ok", "count": target, "quota": [target - 1, target + 1],
            "delta": 0, "pct": 0.0, "severity": "none",
        })
        low_alert = HypergeometricStrategy().land_target(target - 3, curve)
        self.assertEqual(low_alert["status"], "low")
        self.assertEqual(low_alert["delta"], 2)

    def test_color_floors_matches_recommended_color_sources_per_color(self):
        pip_reqs_by_color = {
            "R": [(1, 1), (1, 1), (2, 1)],
            "G": [(7, 3)],
            "W": [], "U": [], "B": [],
        }
        shares = color_pip_share(pip_reqs_by_color)
        expected = {
            "R": recommended_color_sources(pip_reqs_by_color["R"], shares["R"]),
            "G": recommended_color_sources(pip_reqs_by_color["G"], shares["G"]),
        }
        self.assertEqual(
            HypergeometricStrategy().color_floors(["R", "G"], pip_reqs_by_color),
            expected,
        )

    def test_diagnose_with_hypergeometric_strategy_uses_its_own_numbers(self):
        cards = [
            {"name": "Mountain", "quantity": 10, "type_line": "Basic Land — Mountain",
             "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0},
            {"name": "Bolt", "quantity": 3, "type_line": "Instant",
             "oracle_text": "", "mana_cost": "{R}", "cmc": 1},
            {"name": "Elder", "quantity": 1, "type_line": "Creature",
             "oracle_text": "", "mana_cost": "{4}{R}{R}{R}", "cmc": 7},
        ]
        commander = {"mana_cost": "{2}{R}{R}", "color_identity": ["R"]}
        static_report = diagnose(cards, commander=commander, identity=["R"], strategy=StaticQuotaStrategy())
        hyper_report = diagnose(cards, commander=commander, identity=["R"], strategy=HypergeometricStrategy())
        self.assertNotEqual(static_report["land_alert"]["quota"], hyper_report["land_alert"]["quota"])
        self.assertNotEqual(static_report["min_sources"], hyper_report["min_sources"])
        target = recommended_land_count(hyper_report["curve_by_cmc"])
        self.assertEqual(hyper_report["land_alert"]["quota"], [target - 1, target + 1])

    def test_diagnose_default_strategy_is_now_hypergeometric(self):
        """diagnose()'s default flipped from StaticQuotaStrategy to
        HypergeometricStrategy -- land count is now curve-weighted per deck
        instead of a single fixed ROLE_QUOTAS band for every deck.
        """
        cards = [
            {"name": "Mountain", "quantity": 10, "type_line": "Basic Land — Mountain",
             "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0},
            {"name": "Bolt", "quantity": 3, "type_line": "Instant",
             "oracle_text": "", "mana_cost": "{R}", "cmc": 1},
        ]
        commander = {"mana_cost": "{2}{R}{R}", "color_identity": ["R"]}
        default_report = diagnose(cards, commander=commander, identity=["R"])
        hyper_report = diagnose(cards, commander=commander, identity=["R"], strategy=HypergeometricStrategy())
        self.assertEqual(default_report, hyper_report)


class TestDiagnoseSourceFloor(unittest.TestCase):
    """diagnose()'s report["min_sources"] is a per-color dict, keyed by identity color --
    the shape a future per-color-varying strategy (e.g. hypergeometric) also needs.
    Pinned against StaticQuotaStrategy explicitly, since the shape (not the default
    strategy's own numbers) is what this class is characterizing.
    """

    def test_min_sources_is_the_per_color_floor_dict(self):
        report = diagnose(
            [{"name": "Mountain", "quantity": 5, "type_line": "Basic Land — Mountain",
              "oracle_text": "{T}: Add {R}.", "mana_cost": "", "cmc": 0}],
            commander={"mana_cost": "{2}{R}{R}", "color_identity": ["R"]},
            identity=["R"],
            strategy=StaticQuotaStrategy(),
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


class TestStrategyFromName(unittest.TestCase):
    """strategy_from_name() is how a DeckState's mana_strategy string (set by the
    web UI's advanced-options dropdown) becomes a real strategy instance.
    """

    def test_known_names_map_to_the_matching_class(self):
        self.assertIsInstance(strategy_from_name("static"), StaticQuotaStrategy)
        self.assertIsInstance(strategy_from_name("hypergeometric"), HypergeometricStrategy)

    def test_unknown_or_missing_name_falls_back_to_hypergeometric(self):
        self.assertIsInstance(strategy_from_name(None), HypergeometricStrategy)
        self.assertIsInstance(strategy_from_name(""), HypergeometricStrategy)
        self.assertIsInstance(strategy_from_name("bogus"), HypergeometricStrategy)


class TestDeckStateManaStrategy(unittest.TestCase):
    """DeckState.mana_strategy: validated on from_dict, round-trips through
    to_dict, and actually changes diagnose_deck()'s output when set.
    """

    def test_from_dict_validates_and_defaults(self):
        self.assertEqual(DeckState.from_dict({"mana_strategy": "static"}).mana_strategy, "static")
        self.assertEqual(DeckState.from_dict({"mana_strategy": "bogus"}).mana_strategy, "hypergeometric")
        self.assertEqual(DeckState.from_dict({}).mana_strategy, "hypergeometric")

    def test_round_trips_through_to_dict(self):
        deck = DeckState.from_dict({"mana_strategy": "static"})
        self.assertEqual(DeckState.from_dict(deck.to_dict()).mana_strategy, "static")

    @unittest.skipUnless(
        os.path.exists(os.path.join(os.path.dirname(SRC_DIR), "data", "managraph.db")),
        "needs local catalog",
    )
    def test_diagnose_deck_honors_the_deck_s_mana_strategy(self):
        """diagnose_deck() itself has no idea about DeckState.mana_strategy -- every
        real call site (solver.py, main_agent.py, tools.py, demo_solver.py) has to
        translate it via strategy_from_name() explicitly, same as here.
        """
        deck = DeckState.from_dict({
            "commander": "Krenko, Mob Boss",
            "identity": ["R"],
            "cards": {"Mountain": 10, "Lightning Bolt": 3},
            "mana_strategy": "static",
        })
        report = diagnose_deck(deck, strategy=strategy_from_name(deck.mana_strategy))
        self.assertEqual(report["land_alert"], land_alert(report["land_count"]))


if __name__ == "__main__":
    unittest.main()
