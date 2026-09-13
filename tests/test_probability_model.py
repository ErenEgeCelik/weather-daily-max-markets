import math
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np

from kalman_engine import (
    KalmanWeatherModel, gaussian_round_pmf, kalman_update, rolling_bias,
    solar_regime_adjustment,
)

NOW = datetime(2026, 6, 15, 12, 10, tzinfo=timezone.utc)


def make_model(**kwargs):
    return KalmanWeatherModel("synthetic-test", tz_hours=0,
                              target_local_date=date(2026, 6, 15), **kwargs)


def initialized_model():
    model = make_model(sigma_process_per_hour=0.8, mean_reversion_per_hour=0.5)
    model.set_now(NOW)
    model.set_forecast(["12:00", "16:00", "23:00"], [21, 23, 18], 23)
    model.add_pws(NOW, 21.0, 0)
    model.add_metar(NOW - timedelta(minutes=20), 20)
    model.refresh()
    return model


class AnalyticalUpdateTests(unittest.TestCase):
    def test_offset_kalman_update_matches_closed_form(self):
        mean, variance = kalman_update(20, 4, obs=23, obs_var=1, obs_offset=1)
        self.assertAlmostEqual(mean, 21.6)
        self.assertAlmostEqual(variance, 0.8)

    def test_process_time_is_hours_and_noise_is_variance_per_hour(self):
        model = make_model(sigma_process_per_hour=0.8, mean_reversion_per_hour=0.5)
        model.set_forecast(["12:00", "23:00"], [24, 24], 24)
        model._T_mean, model._T_var, model._last_update_time = 20, 1, NOW
        model._process_advance(NOW + timedelta(minutes=30))
        self.assertAlmostEqual(model._T_mean, 21)
        self.assertAlmostEqual(model._T_var, 0.75 ** 2 + 0.8 ** 2 * 0.5)

    def test_missing_forecast_uses_random_walk_variance(self):
        model = make_model(sigma_process_per_hour=0.8, mean_reversion_per_hour=0.5)
        model._T_mean, model._T_var, model._last_update_time = 20, 1, NOW
        model._process_advance(NOW + timedelta(minutes=30))
        self.assertEqual(model._T_mean, 20)
        self.assertAlmostEqual(model._T_var, 1 + 0.8 ** 2 * 0.5)

    def test_positive_official_minus_pws_correction_adds_to_reading(self):
        model = make_model(fallback_pws_bias=0.3, sigma_pws=0.1)
        model.set_now(NOW)
        model.add_pws(NOW, 20, 0)
        snap = model.refresh()
        self.assertAlmostEqual(snap.T_mean, 20.3)
        self.assertAlmostEqual(snap.T_sigma, 0.5)  # Constructor noise floor.
        sigma, offset = solar_regime_adjustment(800, 0.5, -0.3)
        self.assertAlmostEqual(sigma, 1)
        self.assertAlmostEqual(offset, 0.2)

    def test_rolling_bias_uses_two_past_pairs_and_not_current_metar(self):
        earlier = [NOW - timedelta(minutes=60), NOW - timedelta(minutes=30)]
        pws = [(earlier[0], 20.5), (earlier[1], 21.5), (NOW, 99)]
        metars = [(earlier[0], 20), (earlier[1], 21), (NOW, 0)]
        self.assertAlmostEqual(rolling_bias(metars, pws, NOW), -0.5)
        self.assertIsNone(rolling_bias(metars[1:], pws, NOW))

    def test_rounded_gaussian_uses_interval_mass_and_renormalizes_support(self):
        pmf = gaussian_round_pmf(0, 1, (-8, 8))
        raw_zero_mass = math.erf(0.5 / math.sqrt(2))
        self.assertAlmostEqual(sum(pmf.values()), 1)
        self.assertAlmostEqual(pmf[-1], pmf[1])
        self.assertAlmostEqual(pmf[0], raw_zero_mass, places=5)
        self.assertEqual(gaussian_round_pmf(0, 1, (0, 0)), {0: 1.0})

    def test_cold_pws_window_and_timestamp_correction_limit(self):
        model = make_model()
        model.set_now(NOW)
        model.add_pws(NOW - timedelta(hours=4), -10, 0)
        model.add_pws(NOW, 20, 0)
        model.refresh()
        self.assertEqual(model._T_mean, 20)
        model.add_metar(NOW, 20)
        model.add_metar(NOW, 21)
        self.assertEqual(model._metars, [(NOW, 20)])


class OutcomeTests(unittest.TestCase):
    def test_probability_normalization_floor_and_seed_repeatability(self):
        model = initialized_model()
        snap = model.snapshot()
        self.assertAlmostEqual(sum(snap.P_now.values()), 1)
        self.assertAlmostEqual(sum(snap.Q_next.values()), 1)
        self.assertGreaterEqual(min(snap.P_now), snap.confirmed_max)
        paths_a = model._sample_remaining_paths(n_paths=32)
        paths_b = model._sample_remaining_paths(n_paths=32)
        np.testing.assert_array_equal(paths_a, paths_b)
        for m in snap.Q_next:
            conditional = model.distribution_given(m)
            self.assertAlmostEqual(sum(conditional.values()), 1)
            self.assertGreaterEqual(min(conditional), max(snap.confirmed_max, m))

    def test_conditional_clock_and_full_state_restore(self):
        model = initialized_model()
        attrs = ("_T_mean", "_T_var", "_last_update_time", "_last_update_kind", "_replay_now")
        before = tuple(getattr(model, name) for name in attrs)
        expected_target = NOW + timedelta(minutes=10)
        original = model._daily_max_distribution

        def inspect_clock(floor):
            self.assertEqual(model._now(), expected_target)
            self.assertEqual(model._last_update_time, expected_target)
            self.assertEqual(model._last_update_kind, "metar_hypothetical")
            return original(floor)

        with patch.object(model, "_daily_max_distribution", side_effect=inspect_clock):
            model.distribution_given(22)
        self.assertEqual(tuple(getattr(model, name) for name in attrs), before)
        with patch.object(model, "_daily_max_distribution", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                model.distribution_given(22)
        self.assertEqual(tuple(getattr(model, name) for name in attrs), before)

    def test_next_report_query_restores_state_when_advance_fails(self):
        model = initialized_model()
        attrs = ("_T_mean", "_T_var", "_last_update_time")
        before = tuple(getattr(model, name) for name in attrs)
        original = model._process_advance

        def failing_advance(target):
            original(target)
            raise RuntimeError("synthetic failure after advance")

        with patch.object(model, "_process_advance", side_effect=failing_advance):
            with self.assertRaises(RuntimeError):
                model._next_metar_distribution()
        self.assertEqual(tuple(getattr(model, name) for name in attrs), before)

    def test_consistency_residual_is_max_bucket_error_not_an_asserted_identity(self):
        model = initialized_model()
        snap = model.snapshot()
        mixture = {}
        for m, q in snap.Q_next.items():
            for k, p in model.distribution_given(m).items():
                mixture[k] = mixture.get(k, 0) + q * p
        keys = set(mixture) | set(snap.P_now)
        expected = max(abs(mixture.get(k, 0) - snap.P_now.get(k, 0)) for k in keys)
        self.assertAlmostEqual(model.consistency_residual(), expected)
        self.assertTrue(math.isfinite(expected))


if __name__ == "__main__":
    unittest.main()
