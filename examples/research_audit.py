"""Recheck compact historical research artifacts; no network or model refitting."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from weather_research.evaluation import (  # noqa: E402
    cluster_score_totals, feature_incremental, score_bucket_snapshots,
    summarize_cluster_scores,
)

DATA = ROOT / "data" / "research"


def csv_rows(name):
    with (DATA / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def run_audit():
    historical = json.loads((DATA / "historical_replay_summaries.json").read_text())
    reports = {}
    proxy_totals = csv_rows("proxy_city_day_scores.csv")
    reports["source_substituted"] = summarize_cluster_scores(proxy_totals)
    # One complete city-day checks the probability-to-total transformation.
    one_day = cluster_score_totals(score_bucket_snapshots(csv_rows("proxy_one_day_buckets.csv")))
    for recomputed in one_day:
        archived = next(r for r in proxy_totals if r["cluster"] == recomputed["cluster"])
        for key in ("n", "model_brier_sum", "market_brier_sum"):
            if abs(float(archived[key]) - recomputed[key]) > 1e-10:
                raise AssertionError(f"one-day transformation differs: {key}")
    reports["production_journal"] = summarize_cluster_scores(
        cluster_score_totals(score_bucket_snapshots(csv_rows("journal_buckets.csv"))))
    pre = csv_rows("pre_metar_buckets.csv")
    reports["pre_metar_all"] = summarize_cluster_scores(cluster_score_totals(score_bucket_snapshots(pre)))
    threshold = historical["pre_metar"]["parameters"]["max_consistency_residual"]
    consistent = [r for r in pre if r["consistency_residual"] != ""
                  and float(r["consistency_residual"]) <= threshold]
    reports["pre_metar_consistent"] = summarize_cluster_scores(
        cluster_score_totals(score_bucket_snapshots(consistent)))
    targets = {
        "source_substituted": historical["source_substituted"]["outcome_scores"],
        "production_journal": historical["production_journal"]["outcome_scores"],
        "pre_metar_all": historical["pre_metar"]["pre_metar_fair_scores"],
        "pre_metar_consistent": historical["pre_metar"]["pre_metar_fair_scores_consistent_states"],
    }
    for name, expected in targets.items():
        if {r["model"] for r in reports[name]} != {r["model"] for r in expected}:
            raise AssertionError(f"model coverage differs: {name}")
        for result in reports[name]:
            target = next(r for r in expected if r["model"] == result["model"])
            for key, value in result.items():
                if key == "model":
                    continue
                pairs = zip(value, target[key]) if isinstance(value, list) else [(value, target[key])]
                for actual, original in pairs:
                    if actual is None or original is None:
                        if actual != original:
                            raise AssertionError(f"missing interval differs: {name}/{key}")
                    elif abs(actual - original) > 1e-10:
                        raise AssertionError(f"historical comparison differs: {name}/{key}")
    features = defaultdict(list)
    for row in csv_rows("daily_sensor_features.csv"):
        features[(row["city"], int(row["local_hour"]))].append(row)
    feature_results = [dict(city=city, local_hour=hour, **feature_incremental(rows))
                       for (city, hour), rows in sorted(features.items())]
    fits = json.loads((DATA / "market_fits.json").read_text())
    improvements = [100*(r["baseline_brier"]-r["best_brier"])/r["baseline_brier"] for r in fits]
    for fit, value in zip(fits, improvements):
        if abs(round(value, 1)-fit["improve_brier_pct"]) > 1e-10:
            raise AssertionError("historical improvement field is inconsistent")
    ablations = []
    for name in ("infogain_warsaw.json", "infogain_warsaw_bridge.json"):
        artifact = json.loads((DATA / name).read_text())
        hours = list(artifact["by_hour"].values())
        n = sum(r["n"] for r in hours)
        a = sum(r["rps_without"]*r["n"] for r in hours)/n
        b = sum(r["rps_with"]*r["n"] for r in hours)/n
        ablations.append(dict(bridge=artifact["bridge"], n_snapshots=n,
                              rps_without=a, rps_with=b, relative_reduction_pct=100*(a-b)/a))
    q_rows = csv_rows("pre_metar_q_scores.csv")
    q_results = []
    for model in sorted({r["model"] for r in q_rows}):
        group = [r for r in q_rows if r["model"] == model]
        result = dict(model=model, n_events=len(group),
                      clusters=len({r["cluster"] for r in group}),
                      changed_events=sum(r["metar_changed"] == "True" for r in group),
                      q_brier_mean=statistics.fmean(float(r["q_brier"]) for r in group),
                      persistence_brier_mean=statistics.fmean(float(r["persistence_brier"]) for r in group))
        target = next(r for r in historical["pre_metar"]["q_next_scores"]
                      if r["model"] == model and r["subset"] == "all")
        for key in ("n_events", "clusters", "changed_events", "q_brier_mean", "persistence_brier_mean"):
            if abs(result[key]-target[key]) > 1e-10:
                raise AssertionError(f"Q aggregate differs: {model}/{key}")
        q_results.append(result)
    return dict(
        status="archived score means, city-day intervals and counts reproduced",
        rerun_scope="offline scoring/aggregation and fixed-candidate LOO; no full model replay or new holdout",
        score_rechecks=reports,
        pre_metar_counts=dict(bucket_rows=len(pre),
                              events=len({r['event'] for r in pre}),
                              consistent_events=len({r['event'] for r in consistent})),
        q_event_aggregate_recheck=q_results,
        calibration_arithmetic=dict(markets=len(fits), weighting="equal market",
                                    mean_relative_improvement_pct=statistics.fmean(improvements),
                                    median_relative_improvement_pct=statistics.median(improvements),
                                    markets_above_20_pct=sum(x > 20 for x in improvements),
                                    status="same-data selected-vs-default fit artifacts; fits not rerun"),
        sensor_ablation_arithmetic=ablations,
        feature_rechecks=feature_results,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full recheck report")
    args = parser.parse_args()
    report = run_audit()
    if args.json:
        print(json.dumps(report, indent=2, allow_nan=False))
    else:
        print(report["status"])
        for name, rows in report["score_rechecks"].items():
            for r in rows:
                print(f"{name:24} {r['model']:20} snapshots={r['n_snapshots']:4} "
                      f"days={r['clusters']:3} model-market Brier={r['model_minus_market_brier_mean']:+.6f} "
                      f"CI95={r['difference_ci95_cluster_bootstrap']}")
        print("Feature check: 15 city/hour wind-direction comparisons; LOO, not chronological.")
        print("28-market fit arithmetic and both Warsaw ablation tables checked; models not refit.")
