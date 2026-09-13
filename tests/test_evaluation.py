import csv
from pathlib import Path
import unittest

import numpy as np

from weather_research.evaluation import (
    brier_score, ranked_probability_score, score_bucket_snapshots,
    cluster_score_totals, summarize_cluster_scores, loo_ridge_errors,
    feature_incremental,
)


class EvaluationTests(unittest.TestCase):
    def test_multiclass_brier_units(self):
        self.assertEqual(brier_score([1, 0], 0), 0)
        self.assertEqual(brier_score([1, 0], 1), 2)
        self.assertEqual(brier_score([.5, .5], 1), .5)

    def test_score_validation(self):
        for p, outcome in [([.4, .4], 0), ([float("nan"), 1], 0), ([1, 0], 2), ([-1, 2], 0)]:
            with self.assertRaises(ValueError):
                brier_score(p, outcome)

    def test_rps_respects_order(self):
        self.assertEqual(ranked_probability_score([1, 0, 0], 1), .5)
        self.assertEqual(ranked_probability_score([1, 0, 0], 2), 1)
        self.assertEqual(ranked_probability_score([1], 0), 0)

    @staticmethod
    def snapshot(mass=.8):
        return [dict(cluster="day", model="test", ts="t", bucket=str(i),
                     model_p=p, market_p=q, realized=y)
                for i, (p, q, y) in enumerate([(mass*.75, .2, 1), (mass*.25, .2, 0)])]

    def test_conditional_quoted_support_normalization(self):
        score = score_bucket_snapshots(self.snapshot())[0]
        self.assertAlmostEqual(score["model_brier"], .125)
        self.assertAlmostEqual(score["market_brier"], .5)
        self.assertAlmostEqual(score["model_mass"], .8)

    def test_ineligible_mass_or_resolution_is_not_scored(self):
        self.assertEqual(score_bucket_snapshots(self.snapshot(.79)), [])
        rows = self.snapshot()
        rows[0]["realized"] = 0
        self.assertEqual(score_bucket_snapshots(rows), [])

    def test_duplicate_bucket_rejected(self):
        rows = self.snapshot()
        rows[1]["bucket"] = rows[0]["bucket"]
        with self.assertRaises(ValueError):
            score_bucket_snapshots(rows)

    def test_snapshot_weighting_not_equal_day_weighting(self):
        totals = [dict(model="m", cluster="a", n=1, model_brier_sum=1, market_brier_sum=0),
                  dict(model="m", cluster="b", n=9, model_brier_sum=0, market_brier_sum=0)]
        result = summarize_cluster_scores(totals)[0]
        self.assertEqual(result["model_minus_market_brier_mean"], .1)
        self.assertEqual(result["difference_ci95_cluster_bootstrap"], [0, 1])

    def test_single_cluster_has_no_interval(self):
        totals = cluster_score_totals(score_bucket_snapshots(self.snapshot()))
        result = summarize_cluster_scores(totals)[0]
        self.assertEqual(result["difference_ci95_cluster_bootstrap"], [None, None])

    def test_loo_standardization_uses_training_rows(self):
        # With two training observations, standardized x is [-1,+1]. The ridge
        # coefficient for y=[0,2] is 2/3, intercept is 1; held-out x=4 -> yhat=3.
        errors = loo_ridge_errors([[0], [2], [4]], [0, 2, 4])
        np.testing.assert_allclose(errors, [1, 0, 1])

    def test_loo_feature_scale_and_target_translation_invariance(self):
        x = np.array([[0, 2], [2, 5], [3, 8], [7, 12]], dtype=float)
        a = loo_ridge_errors(x, [1, 3, 2, 6])
        b = loo_ridge_errors(x*3+10, np.array([1, 3, 2, 6])+100)
        np.testing.assert_allclose(a, b)

    def test_feature_arms_use_same_complete_cases(self):
        rows = [dict(temp_now=i, solar_now=i*i, day_index=i, wd_cos=i%2,
                     target_c=i*.1) for i in range(6)]
        rows[-1]["wd_cos"] = None
        self.assertEqual(feature_incremental(rows)["n_days"], 5)

    def test_archived_pre_metar_denominators(self):
        path = Path(__file__).resolve().parents[1]/"data/research/pre_metar_buckets.csv"
        with path.open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 604)
        self.assertEqual(len({r["event"] for r in rows}), 156)
        self.assertEqual(len(score_bucket_snapshots(rows)), 126)
        filtered = [r for r in rows if r["consistency_residual"] and float(r["consistency_residual"]) <= .1]
        self.assertEqual(len({r["event"] for r in filtered}), 89)
        self.assertEqual(len(score_bucket_snapshots(filtered)), 77)


if __name__ == "__main__":
    unittest.main()
