import os
import sys
import unittest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from hypergeometric import (
    TARGET_MAX,
    TARGET_MIN,
    average_cmc,
    hand_confidence_by_cmc,
    hand_size,
    hypergeom_at_least,
    mana_base_report,
    recommended_color_sources,
    recommended_land_count,
    target_confidence,
)

# Real decklists the user was happy with at their real land count, resolved against the
# local Scryfall catalog. See conversation history / feedback_mana_calc_scope for the
# source. teval and Karrthus are intentionally excluded from calibration checks: teval's
# commander has a land-reanimation mechanic (wants more lands than curve alone
# predicts), Karrthus is heavily ramp-supported (wants fewer) -- both are Level 2
# (ramp/draw-aware) concerns, not curve-only ones.
REAL_DECKS = {
    "kotis":      (36, {1: 17, 2: 10, 3: 12, 4: 7, 5: 9, 6: 6, 7: 2}),
    "livaan":     (37, {0: 2, 1: 2, 2: 14, 3: 11, 4: 12, 5: 10, 6: 7, 7: 3}),
    "saheeli":    (36, {1: 4, 2: 16, 3: 17, 4: 12, 5: 6, 6: 5, 7: 3}),
    "hope":       (36, {1: 9, 2: 21, 3: 14, 4: 15, 5: 2, 6: 1, 7: 2}),
    "Ivy":        (33, {0: 1, 1: 27, 2: 18, 3: 8, 4: 5, 5: 5, 6: 2, 7: 1}),
    "lightining": (36, {1: 9, 2: 25, 3: 18, 4: 6, 5: 3, 6: 1, 7: 2}),
}


class TestHypergeomAtLeast(unittest.TestCase):
    def test_single_draw_matches_simple_ratio(self):
        self.assertAlmostEqual(hypergeom_at_least(10, 5, 1, 1), 0.5)

    def test_need_two_of_two_successes_in_two_draws(self):
        # population=4 (2 successes, 2 failures), draw 2, need both successes: 1/6.
        self.assertAlmostEqual(hypergeom_at_least(4, 2, 2, 2), 1 / 6)

    def test_need_one_is_complement_of_above(self):
        self.assertAlmostEqual(hypergeom_at_least(4, 2, 2, 1), 5 / 6)

    def test_need_le_zero_is_certain(self):
        self.assertEqual(hypergeom_at_least(99, 10, 10, 0), 1.0)

    def test_no_successes_is_impossible(self):
        self.assertEqual(hypergeom_at_least(99, 0, 10, 1), 0.0)

    def test_all_successes_is_certain_within_draw_size(self):
        self.assertEqual(hypergeom_at_least(99, 99, 10, 10), 1.0)


class TestHandSize(unittest.TestCase):
    def test_draws_turn_one_too(self):
        self.assertEqual(hand_size(0), 7)
        self.assertEqual(hand_size(1), 8)
        self.assertEqual(hand_size(4), 11)


class TestAverageCmc(unittest.TestCase):
    def test_weighted_average(self):
        # 2 cards at CMC1, 1 at CMC4 -> (1*2 + 4*1) / 3 = 2.0
        self.assertAlmostEqual(average_cmc({1: 2, 4: 1}), 2.0)

    def test_empty_curve_is_zero(self):
        self.assertEqual(average_cmc({}), 0.0)

    def test_zero_count_buckets_are_ignored(self):
        self.assertAlmostEqual(average_cmc({1: 2, 4: 1, 7: 0}), average_cmc({1: 2, 4: 1}))


class TestTargetConfidence(unittest.TestCase):
    def test_decreases_with_avg_cmc(self):
        # Real-deck finding: a higher-curve deck settles for *less* achieved confidence
        # at its real land count, not more.
        self.assertGreater(target_confidence(2.0), target_confidence(4.0))

    def test_clamped_to_bounds_for_extreme_avg_cmc(self):
        self.assertEqual(target_confidence(-5.0), TARGET_MAX)
        self.assertEqual(target_confidence(50.0), TARGET_MIN)


class TestRecommendedLandCount(unittest.TestCase):
    def test_higher_curve_needs_more_lands(self):
        # Curve shape must move the result, not just avg_cmc in isolation.
        low = {1: 15, 2: 15, 3: 5}
        high = {4: 5, 5: 15, 6: 15}
        self.assertLess(recommended_land_count(low), recommended_land_count(high))

    def test_empty_cmc_buckets_are_free(self):
        aggro = recommended_land_count({1: 10, 2: 10, 3: 10})
        aggro_with_phantom_zero = recommended_land_count({1: 10, 2: 10, 3: 10, 7: 0})
        self.assertEqual(aggro, aggro_with_phantom_zero)

    def test_empty_curve_needs_no_lands(self):
        self.assertEqual(recommended_land_count({}), 0)

    def test_rare_high_cmc_tail_does_not_dominate_the_deck_total(self):
        without_tail = recommended_land_count({1: 8, 2: 14, 3: 14, 4: 10, 5: 6})
        with_tail = recommended_land_count({1: 8, 2: 14, 3: 14, 4: 10, 5: 6, 6: 3})
        self.assertLessEqual(with_tail - without_tail, 5)

    def test_within_a_couple_lands_of_the_real_decks_this_was_calibrated_on(self):
        for name, (real_lands, curve) in REAL_DECKS.items():
            with self.subTest(deck=name):
                predicted = recommended_land_count(curve)
                self.assertLessEqual(abs(predicted - real_lands), 2)


class TestRecommendedColorSources(unittest.TestCase):
    def test_empty_requirements_needs_no_sources(self):
        self.assertEqual(recommended_color_sources([], avg_cmc=3.0), 0)

    def test_demanding_pip_count_needs_far_more_sources_than_a_single_pip(self):
        easy = recommended_color_sources([(1, 1)], avg_cmc=3.0)
        hard = recommended_color_sources([(2, 5)], avg_cmc=3.0, max_sources=60)
        self.assertGreater(hard, easy)

    def test_extreme_early_multi_pip_saturates_at_max_sources(self):
        result = recommended_color_sources([(2, 5)], avg_cmc=3.0, max_sources=30)
        self.assertEqual(result, 30)

    def test_rare_double_pip_does_not_dominate_the_color_target(self):
        without_outlier = recommended_color_sources([(2, 1)] * 18, avg_cmc=3.0, max_sources=60)
        with_outlier = recommended_color_sources(
            [(2, 1)] * 18 + [(3, 2)] * 2, avg_cmc=3.0, max_sources=60
        )
        self.assertLessEqual(with_outlier - without_outlier, 5)

    def test_duplicate_requirements_collapse_to_one_bucket(self):
        single = recommended_color_sources([(3, 1)], avg_cmc=3.0)
        pooled = recommended_color_sources([(3, 1)] * 10, avg_cmc=3.0)
        self.assertEqual(single, pooled)


class TestHandConfidenceByCmc(unittest.TestCase):
    def test_reports_one_value_per_populated_cmc(self):
        result = hand_confidence_by_cmc({1: 8, 2: 14, 3: 0})
        self.assertEqual(set(result), {1, 2})
        self.assertTrue(0.0 < result[1] < 1.0)
        self.assertTrue(0.0 < result[2] < 1.0)


class TestManaBaseReport(unittest.TestCase):
    def test_report_shape(self):
        report = mana_base_report(
            curve={1: 8, 2: 10, 3: 8, 4: 4},
            pip_requirements_by_color={"R": [(1, 1), (2, 1), (4, 2)], "U": [(3, 1)]},
        )
        self.assertIn("land_count", report)
        self.assertIn("sources", report)
        self.assertIn("avg_cmc", report)
        self.assertIn("target_confidence", report)
        self.assertEqual(set(report["sources"]), {"R", "U"})
        self.assertGreater(report["land_count"], 0)
        self.assertGreater(report["sources"]["R"], 0)


if __name__ == "__main__":
    unittest.main()
