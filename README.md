# weather-kalman-replay

**Replay a Bayesian probability engine over a real recorded trading day.**

This is the state-estimation core of a system that traded Polymarket's daily maximum-temperature markets, extracted from production and replayed offline against real data recorded on **2026-06-11** for the Istanbul (LTFM) market: 1,097 private-weather-station readings, 26 METAR observations, and 42 forecast revisions, fed to the engine in the exact order the live system received them.

```
python replay.py
```

![replay](output/replay.png)

**Top panel:** the hidden-state posterior T(t) (blue, ±1σ) threading between the private weather station (gray dots — reads ~0.3 °C cool, bias-corrected online) and the official METAR observations (red squares), with the forecast curve and its intraday revisions (green dashed).

**Bottom panel:** the daily-maximum distribution `P(daily max = k)` evolving as evidence arrives. The market's question — does the day end at 24 °C or reach 25 °C? — is a live race until ~17:30 local: P(25) surges to 0.75 during the 15:00 warm spell, collapses when the readings recede, twitches again at 16:30, and dies as the remaining daylight runs out. The engine converges to the true outcome (24 °C) before the market's final observations.

## The model

One scalar hidden state per market — the instantaneous temperature at the resolving station:

- **Process:** AR(1) random walk pulled toward the hourly forecast curve: `T(t+Δ) = (1−βΔ)·T(t) + βΔ·forecast(t+Δ) + ε`, `ε ~ N(0, σ_proc²·Δ)`
- **Observations** (each a scalar Kalman update): METAR as `round(T) + noise` (σ ≈ 0.29, uniform rounding noise), PWS as `T + bias + noise` with the bias re-estimated online from a rolling METAR−PWS window and a solar-regime adjustment (sun-baked stations over-read, so under high solar radiation the reading is trusted less and pushed down)
- **Outputs**, all descending from the same posterior:
  - `P_now(k)` — daily-max distribution, via 10,000-path Monte Carlo over the remaining hours
  - `Q_next(m)` — next-METAR distribution, analytic Gaussian round-PMF at the publish minute
  - `D_after(k|m)` — daily-max conditional on a hypothetical next observation

Because the three outputs share one posterior, they satisfy `Σ_m Q(m)·D_after(k|m) ≈ P_now(k)`. The replay checks this **consistency identity** hourly — it holds to 0.5–3.6% (Monte Carlo noise), and it was used the same way in production as a built-in correctness alarm.

The engine also carries three robustness guards, each added after a documented live failure: innovation-based variance inflation (a tight overnight posterior must not reject genuine daytime warming), a cold-start warm-up window, and a degeneracy guard against over-confident calls on a *future* maximum (one noisy station reading once collapsed the posterior to 100% certainty).

Model parameters (β, σ_proc, σ_pws) are per-market, fitted by a forecast-aware replay against archived historical forecasts — deliberately not reanalysis, which would be look-ahead leakage. Across 28 markets the fit improved mean Brier score ~24% with no market regressing.

## Files

| File | Role |
|---|---|
| `kalman_engine.py` | The engine — production filter math, offline-adapted (replay clock, injected observations, no network) |
| `replay.py` | Loads the journals, drives the engine in arrival order, writes the plot and the consistency-check table |
| `data/istanbul_2026-06-11/` | Real recorded journals: `pws_raw.jsonl`, `metar_raw.jsonl`, `forecast_raw.jsonl` |

Requires Python 3.9+, `numpy`, `matplotlib`.

## Context

This engine was one layer of a larger system — data collection across five machines, an MDP decision layer, and low-latency execution. The full research record, including how the edges this engine served were found, measured, and retired, is at **[prediction-market-research](https://github.com/ErenEgeCelik/prediction-market-research)** ("Weather Markets: A Succession of Edges").

*© 2026 Eren Ege Çelik*
