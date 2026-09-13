"""Offline evaluation primitives for the archived weather studies.

Scores concern probability forecasts, not trading returns. See docs/experiments.md
for sampling, source substitution and parameter-selection limitations.
"""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Mapping, Sequence
import zlib

import numpy as np


def brier_score(probabilities: Sequence[float], outcome: int) -> float:
    """Multiclass sum of squared errors (range 0..2), without a 1/K factor."""
    p = np.asarray(probabilities, dtype=float)
    if p.ndim != 1 or len(p) == 0 or not np.isfinite(p).all():
        raise ValueError("probabilities must be a finite nonempty vector")
    if np.any(p < 0) or np.any(p > 1) or not np.isclose(p.sum(), 1):
        raise ValueError("probabilities must be normalized")
    if not isinstance(outcome, (int, np.integer)) or not 0 <= outcome < len(p):
        raise ValueError("outcome must index the probability vector")
    actual = np.zeros(len(p))
    actual[outcome] = 1
    return float(np.sum((p - actual) ** 2))


def ranked_probability_score(probabilities: Sequence[float], outcome: int) -> float:
    """RPS on one common ordered support; normalized by K-1."""
    brier_score(probabilities, outcome)  # validate the same probability contract
    p = np.asarray(probabilities, dtype=float)
    if len(p) < 2:
        return 0.0
    observed_cdf = np.arange(len(p)) >= outcome
    return float(np.sum((np.cumsum(p) - observed_cdf) ** 2) / (len(p) - 1))


def score_bucket_snapshots(rows: Sequence[Mapping]) -> list[dict]:
    """Recompute the historical quoted-support score, retaining its eligibility rule.

    Rows need cluster, model, ts, bucket, model_p, market_p and realized. Missing
    resolution rows are excluded before grouping, as in the historical scorer.
    Eligible snapshots require model mass >= .80, positive market mass and exactly
    one resolved YES bucket. Both distributions are renormalized on these buckets.
    """
    groups = defaultdict(list)
    for row in rows:
        if row.get("realized") in (None, ""):
            continue
        groups[(str(row["cluster"]), str(row["model"]), str(row["ts"]))].append(row)
    scored = []
    for (cluster, model, ts), group in sorted(groups.items()):
        buckets = [str(row["bucket"]) for row in group]
        if len(set(buckets)) != len(buckets):
            raise ValueError("duplicate bucket in one snapshot")
        p = np.array([float(row["model_p"]) for row in group])
        m = np.array([float(row["market_p"]) for row in group])
        y = np.array([int(row["realized"]) for row in group])
        if not np.isfinite(p).all() or not np.isfinite(m).all():
            raise ValueError("nonfinite probability")
        if np.any(p < 0) or np.any(p > 1) or np.any(m < 0) or np.any(m > 1):
            raise ValueError("bucket probabilities must lie in [0, 1]")
        if not np.isin(y, [0, 1]).all():
            raise ValueError("realized must be 0 or 1")
        model_mass, market_mass = float(p.sum()), float(m.sum())
        if model_mass < .80 or market_mass <= 0 or y.sum() != 1:
            continue
        outcome = int(np.argmax(y))
        model_brier = brier_score(p / model_mass, outcome)
        market_brier = brier_score(m / market_mass, outcome)
        scored.append(dict(cluster=cluster, model=model, ts=ts,
                           model_brier=model_brier, market_brier=market_brier,
                           brier_difference=model_brier - market_brier,
                           model_mass=model_mass, market_mass=market_mass,
                           quoted_buckets=len(group)))
    return scored


def cluster_score_totals(scored: Sequence[Mapping]) -> list[dict]:
    """City-day sufficient statistics for snapshot-weighted paired score means."""
    groups = defaultdict(list)
    for row in scored:
        groups[(str(row["model"]), str(row["cluster"]))].append(row)
    return [dict(model=model, cluster=cluster, n=len(group),
                 model_brier_sum=math.fsum(float(r["model_brier"]) for r in group),
                 market_brier_sum=math.fsum(float(r["market_brier"]) for r in group))
            for (model, cluster), group in sorted(groups.items())]


def summarize_cluster_scores(totals: Sequence[Mapping], reps: int = 1000) -> list[dict]:
    """Historical percentile bootstrap: sample city-days, retain all their rows.

    This is a snapshot-weighted statistic, not an equally weighted mean of days.
    Compact counts and sums reproduce that statistic without treating bucket rows
    or repeated intraday forecasts as independent observations.
    """
    if reps < 0:
        raise ValueError("reps cannot be negative")
    groups = defaultdict(list)
    for row in totals:
        groups[str(row["model"])].append(row)
    results = []
    for model, group in sorted(groups.items()):
        group = sorted(group, key=lambda r: str(r["cluster"]))
        if len({str(r["cluster"]) for r in group}) != len(group):
            raise ValueError("one total per model/city-day is required")
        n = np.array([float(r["n"]) for r in group])
        a = np.array([float(r["model_brier_sum"]) for r in group])
        b = np.array([float(r["market_brier_sum"]) for r in group])
        if (not np.isfinite(n).all() or np.any(n <= 0) or np.any(n != np.floor(n))
                or not np.isfinite(a).all() or not np.isfinite(b).all()
                or np.any(a < 0) or np.any(b < 0) or np.any(a > 2*n) or np.any(b > 2*n)):
            raise ValueError("invalid counts or Brier totals")
        low = high = p_nonpositive = None
        if len(group) >= 2 and reps:
            rng = np.random.default_rng(3000 + zlib.crc32(model.encode("utf-8")))
            values = []
            for _ in range(reps):
                index = rng.choice(len(group), size=len(group), replace=True)
                values.append(float(np.sum(a[index] - b[index]) / np.sum(n[index])))
            low, high = (float(v) for v in np.quantile(values, [.025, .975]))
            p_nonpositive = float(np.mean(np.asarray(values) <= 0))
        count = int(n.sum())
        results.append(dict(model=model, n_snapshots=count, clusters=len(group),
                            model_brier_mean=float(a.sum()/count),
                            market_brier_mean=float(b.sum()/count),
                            model_minus_market_brier_mean=float((a-b).sum()/count),
                            difference_ci95_cluster_bootstrap=[low, high],
                            bootstrap_probability_difference_nonpositive=p_nonpositive))
    return results


def loo_ridge_errors(features: Sequence, target: Sequence, penalty: float = 1.0) -> np.ndarray:
    """Leave one day out; standardize and center using training rows in each fold."""
    x, y = np.asarray(features, dtype=float), np.asarray(target, dtype=float)
    if x.ndim != 2 or y.shape != (len(x),) or len(y) < 3 or penalty <= 0:
        raise ValueError("need >=3 paired rows, a feature matrix and positive penalty")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("features and target must be finite")
    errors = []
    for i in range(len(y)):
        train = np.arange(len(y)) != i
        xtr, ytr = x[train], y[train]
        mu, sd = xtr.mean(0), xtr.std(0)
        sd = np.where(sd < 1e-9, 1.0, sd)
        standardized = (xtr - mu) / sd
        ymean = ytr.mean()
        beta = np.linalg.solve(standardized.T @ standardized + penalty*np.eye(x.shape[1]),
                               standardized.T @ (ytr - ymean))
        prediction = (x[i] - mu) / sd @ beta + ymean
        errors.append(abs(float(prediction) - y[i]))
    return np.asarray(errors)


def feature_incremental(rows: Sequence[Mapping], feature: str = "wd_cos") -> dict:
    """Paired LOO delta MAE and residual association for one fixed candidate.

    This function does not choose a feature or declare it validated. The same
    complete-case day set is used by both arms. Units are degrees Celsius.
    """
    baseline = ["temp_now", "solar_now", "day_index"]
    if feature in baseline:
        raise ValueError("candidate is already in the baseline")
    columns = baseline + [feature]
    keep = [r for r in rows if all(r.get(k) not in (None, "") for k in columns + ["target_c"])]
    if len(keep) < 3:
        raise ValueError("at least three complete-case days are required")
    x = np.asarray([[float(r[k]) for k in columns] for r in keep])
    y = np.asarray([float(r["target_c"]) for r in keep])
    base_errors = loo_ridge_errors(x[:, :-1], y)
    augmented_errors = loo_ridge_errors(x, y)
    design = np.column_stack([np.ones(len(y)), x[:, :-1]])
    ry = y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
    rx = x[:, -1] - design @ np.linalg.lstsq(design, x[:, -1], rcond=None)[0]
    denom = float(np.linalg.norm(rx) * np.linalg.norm(ry))
    total_variation = float(np.sum((x[:, -1] - x[:, -1].mean()) ** 2))
    vif = total_variation / float(rx @ rx) if float(rx @ rx) > 1e-15 else None
    return dict(feature=feature, n_days=len(y),
                baseline_loo_mae_c=float(base_errors.mean()),
                augmented_loo_mae_c=float(augmented_errors.mean()),
                delta_loo_mae_c=float((augmented_errors-base_errors).mean()),
                partial_correlation=float(rx @ ry / denom) if denom > 1e-15 else None,
                vif=vif,
                # Preserve and name the original descriptive comparator honestly.
                in_sample_median_mae_c=float(np.mean(np.abs(y-np.median(y)))))
