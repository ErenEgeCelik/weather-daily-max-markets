# Temperature estimation and outcome probabilities

The modeling problem has two horizons: estimate the resolving station's temperature
now, then translate that estimate into the next official report and the final daily
maximum. The public engine implements a scalar Kalman filter pulled toward an hourly
forecast, with source-specific observation uncertainty and an intraday PWS correction.
It computes three quantities from that state:

| Output | Question | Implementation |
| --- | --- | --- |
| `P_now[k]` | What is the probability of final maximum bucket `k`? | `_daily_max_distribution`: sampled remaining-hour paths, official maximum floor |
| `Q_next[m]` | What might the next integer METAR report be? | `_next_metar_distribution`: forecast advance and Gaussian interval probabilities |
| `D_after[m][k]` | How would the maximum distribution change after report `m`? | `distribution_given`: temporary observation update and another path simulation |

This decomposition gives a decision layer both current bucket values and a map of
how a future information event could change them. Prices, fill assumptions and
position sizing belong to the decision layer, rather than the temperature filter.

## Read and run

- [Derivation and implementation details](model-derivation.md): every equation,
  units, bias sign, initialization, event ordering and numerical approximation.
- [Probability walkthrough](../examples/probability_walkthrough.py): deterministic
  synthetic observations through posterior, `P`, `Q`, conditional `D`, and the
  measured mixture residual. It runs without network access or credentials.
- [Engine](../kalman_engine.py): the actual code used by the public replay.
- [Recorded replay](../replay.py): arrival-ordered input from the included Istanbul
  sample, using the parameter set listed below.

```sh
python examples/probability_walkthrough.py
python -m unittest discover -s tests -p test_probability_model.py -v
python replay.py --out output/replay.png
```

## Model choices visible in code

Forecast values set the mean-reversion target; they are not treated as repeated
independent measurements. PWS observations update the posterior more frequently,
with a rolling official-minus-PWS correction and lower confidence in high solar
radiation. METAR reports provide the official integer anchor and a running floor
for the day's maximum. Future paths start from a distribution over temperature,
instead of treating the most recent sensor reading as certain.

The included Istanbul replay uses forecast-pull rate `0.5 / hour`, process diffusion
`0.8 °C / sqrt(hour)`, PWS noise `1.2 °C`, and fallback correction `+0.3 °C`.
Default METAR noise is `0.29 °C`, approximating rounding uncertainty. These are
the supplied example's parameters, not universal station or seasonal calibration.

## Version lineage

| Model family | Main design | Status in this repository |
| --- | --- | --- |
| Earlier weather model | Precision-weighted sensor/official-report blend for next-METAR probabilities; a separate AR(1) component for daily maximum, plus heuristic regime flags | Historical approach documented in the derivation; not the engine behind the public replay |
| Scalar Kalman model | Shared temperature posterior, forecast pull, observation updates and `P/Q/D` interfaces | Runnable public implementation, with documented publication corrections |
| Ensemble prototype | Parallel forecast/parameter hypotheses weighted by accumulated observation likelihood | Research prototype in reviewed source; no integration imports were found in the reviewed application source, and it is not used by the public replay |

The production forecast provider supported cached hourly WU/Meteoblue curves and
hour-aligned blending. The public engine accepts the curve supplied in each recorded
forecast event; it does not reconstruct an unpublished historical provider response.

`P_now(k) = sum_m Q_next(m) D_after(k|m)` is the intended probabilistic relationship.
The implementation reports the maximum absolute bucket residual. Shared state alone
does not prove the identity: rounding, Gaussian pseudo-observations, hourly path
sampling and guard rules produce different approximations. The walkthrough prints
the residual rather than declaring an invariant pass. This is a model demonstration;
calibration and strategy results need their own datasets and evaluation protocols.
