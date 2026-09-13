# Compact historical research inputs

These inputs support the public sensor/calibration and market-score chapters.
They are derived from recorded observations, stored forecasts and archived study
outputs. **They are not synthetic examples and are not an executable order tape.**
The source archive remains separate from this compact publication.

Run from the repository root:

```bash
python examples/research_audit.py
python examples/research_audit.py --json
python examples/plot_research_scores.py
```

## Files and reproduction boundaries

| File | Contents | What the public audit recomputes |
|---|---|---|
| `daily_sensor_features.csv` | 1,374 real derived city/hour/day rows, March 12–June 11, 2026 | Paired leave-one-day-out ridge MAE, partial correlation and VIF for wind cosine at 15 city/hour combinations |
| `feature_recheck.json` | Results of this frozen-cache extraction | Same calculations from the feature CSV; not certification of every number in the older narrative |
| `proxy_city_day_scores.csv` | 177 city-day counts and paired score sums covering 5,104 snapshots | Full archived proxy mean Brier difference and 95% city-day bootstrap interval |
| `proxy_one_day_buckets.csv` | All 126 archived bucket rows from Istanbul, May 12, 2026 | Probability-to-score-to-city-day transformation on one complete day |
| `journal_buckets.csv` | All 432 matched journal bucket rows | Eligibility, normalized scores, paired means and city-day intervals |
| `pre_metar_buckets.csv` | All 604 event bucket rows, including stored residuals | Unfiltered and residual <= .10 eligibility, scores, means, intervals and counts |
| `pre_metar_q_scores.csv` | All 167 archived Q event-score rows | Event counts and average Q/persistence scores; full Q distributions are absent |
| `market_fits.json` | Selected/default parameters and scores, top five candidates, 28 markets | Relative score-improvement arithmetic and equal-market aggregation; no grid refit |
| `infogain_warsaw*.json` | Two six-hour sensor ablation tables, 92 days each | Snapshot-weighted RPS summaries; no path simulation rerun |
| `forecast_skill.json` | 28 archived rounded forecast-error summaries | Published as a historical report; full curves are absent |
| `historical_replay_summaries.json` | Original score/count/response report sections | Reference values for numerical comparisons; regression sections remain source-only |
| `feature_cache_manifest.json` | Three groups of 92 weather-cache file hashes | Pins the weather inputs behind the derived feature table |
| `provenance.json` | SHA-256 of source methods, archived inputs and published data files | Identifies exactly which source artifacts and transformations were used |

The entire data folder is approximately 0.64 MB. No personal station-owner
information, account identifiers, API keys, order IDs or operational endpoints
are included. City names and public airport codes identify the research setting.

## Schemas

Bucket files use `(cluster, model, ts, bucket)` as the unique row key.
`cluster` is city/local-event-date; `model_p` and `market_p` are probabilities
in [0,1]; `realized` is 0/1 or empty when unresolved. `ts` preserves the recorded
snapshot timestamp, except in the pre-METAR file where it identifies the event
as in the original audit. These files contain neither bid/ask history nor fill
observations. Public score eligibility and normalization are specified in
[Experiments](../../docs/experiments.md).

The compact proxy table contains `model`, `cluster`, `n`, `model_brier_sum` and
`market_brier_sum`. Each sum covers the same n eligible snapshots. Resampling a
city-day resamples its count and both sums together. This is sufficient for the
paired snapshot-weighted mean and its city-day bootstrap, but not medians or
arbitrary intraday slices. All 52,918 source bucket rows were processed to derive
these totals; the 126-row example is not used to approximate the full study.

Feature rows contain `city`, `local_hour`, `local_date`, `target_c`,
`daily_max_c` and the original numeric predictors. The target is daily METAR
maximum minus current PWS temperature. Temperatures are Celsius even for the
Chicago feature study. Solar radiation is W/m2, humidity percent, wind direction
sine/cosine dimensionless, pressure hPa, and wind speed/gust km/h. `*_slp60` and
`*_slp120` are endpoint differences over the named window, not rates per minute.
Empty numeric values denote missing features. Fahrenheit units in the separate
market-fit/forecast-skill JSONs remain explicitly labeled by market.

## Transformation and provenance

The feature extraction executes only the historical pure `_nearest`, `_val_at`,
`_slope` and `build_rows` functions, with a fixed March–June file window. It selects
the named March 12–June 11 METAR cache explicitly, rather than the original
loader's largest-file heuristic. Fixed offsets are Istanbul +3, Chicago -5 and
London +1. The original nearest-observation behavior and target-dependent
remaining-rise >= -3 C filter are retained and described in
[Calibration](../../docs/calibration.md). Current feature values do not by
themselves establish decision-time availability.

The old Istanbul narrative does not match this frozen extraction (10:00 delta
MAE -0.131 C in the narrative versus -0.4045 C here). Its exact report-level input
manifest was not recovered. Chicago's printed 10:00/12:00 values do reproduce.
The new input/result pair is published transparently without presenting it as an
exact regeneration of every older prose result.

Journal and pre-METAR tables retain every original row but use a field allowlist.
The proxy totals are newly derived from the archived probabilities using the
public scorer. Other JSONs preserve historical aggregates with operational paths
removed. The code/data hashes permit comparison with the original archive but
do not make unavailable raw provider data public or establish its licensing.

The audit checks historical score means, counts and city-day intervals to numerical
tolerance. It reruns fixed-candidate LOO from the frozen feature table and checks
fit percentage arithmetic. It does not recreate full forecast ingestion, training
searches, historical live states, forward-price regression estimates, or trading
returns. Source methods and each claim's status are indexed in
[`evidence/experiments.json`](../../evidence/experiments.json).
