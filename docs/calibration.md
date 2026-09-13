# Sensor information and model calibration

The research separates three questions: which sensor fields help forecast the
remaining temperature rise, how much a PWS temperature stream adds to METAR plus
forecast, and which process/observation parameters fit each market. They require
different baselines and scores. Market-relative evaluation comes separately in
[Experiments](experiments.md).

Run `python examples/research_audit.py --json` to inspect the public recheck.
[Data and provenance](../data/research/README.md) distinguish probability rows,
derived daily features and archived fit summaries.

## 1. Does an auxiliary sensor field add information?

The original March 12–June 11, 2026 study covered Istanbul, Chicago and London.
At a fixed local hour, the target was the realized METAR daily maximum minus the
current PWS temperature:

$$
y_d=\max_{t\in d}T^{METAR}_t-T^{PWS}_{d,h}.
$$

The baseline contained current temperature, solar radiation and a day index.
Twenty candidate columns included temperature/solar/humidity changes, dewpoint,
wind speed and gust, pressure, precipitation, UV, heat index, wind chill, and
wind direction represented by sine and cosine. A raw correlation was only a
screen. The analysis then checked redundancy through VIF, association after
regressing both candidate and target on the baseline, and incremental error on
the **same complete-case days**.

For each left-out day, ridge regression standardizes predictors using the other
days, centers the training target, and uses penalty 1 with an unpenalized
intercept. The comparison is

$$
\Delta MAE=MAE_{LOO}(B+x)-MAE_{LOO}(B).
$$

Negative values favor the added field. The public
[`feature_incremental`](../weather_research/evaluation.py) function reproduces
that fixed-candidate calculation, including partial correlation and VIF. It does
not automatically select or enable a sensor feature.

The frozen public extraction contains 1,374 city/hour/day rows across five local
hours (08, 10, 12, 13, 15). These repeat the same calendar days; they are not 1,374
independent weather episodes. All feature-study temperature errors are **Celsius**,
including Chicago.

| Frozen extraction | Complete days | Baseline LOO MAE, C | With wind cosine, C | Delta MAE, C |
|---|---:|---:|---:|---:|
| Istanbul 08:00 | 92 | 1.9179 | 1.4042 | -0.5137 |
| Istanbul 10:00 | 92 | 1.4673 | 1.0628 | -0.4045 |
| Istanbul 12:00 | 92 | 1.2226 | 1.0052 | -0.2174 |
| Istanbul 13:00 | 92 | 1.2287 | 1.0950 | -0.1337 |
| Istanbul 15:00 | 92 | 0.9998 | 0.9050 | -0.0948 |
| Chicago 10:00 | 92 | 2.5315 | 2.2417 | -0.2898 |
| Chicago 12:00 | 92 | 1.8286 | 1.8268 | -0.0018 |
| London 10:00 | 92 | 1.4801 | 1.4933 | +0.0132 |
| London 12:00 | 91 | 1.2251 | 1.2323 | +0.0072 |

The station-specific result matters for model design: a field useful in one
location or part of the day need not transfer elsewhere. In this extraction,
wind cosine helps at all five Istanbul hours, loses its Chicago advantage around
midday, and contributes little in London. The original broader screening also
flagged derived weather fields as strongly collinear (reported VIF values about
27–60,000). Those broader rankings are historical reports; the default public
command rechecks the fixed wind-cosine comparison. Other candidates can be passed
to `feature_incremental` using the same published feature table.

### What the extraction preserves

The June narrative's Chicago 10:00/12:00 numbers agree with the current fixed
cache extraction to its printed precision. **The Istanbul numbers do not:**
for example, the old narrative gives -0.131 C at 10:00, while the frozen input
here gives -0.4045 C. The original loader selected the largest matching METAR
cache and did not store a report-level input manifest. The exact historical input
behind that prose has not been recovered. The table above describes the released
fixture, with hashes; it does not silently certify the older table.

The original feature builder used nearest observations within about 12 minutes,
which can include an observation after the nominal decision time. Its `*_slp`
fields are endpoint changes over the named window, not per-minute derivatives.
It excluded days with realized remaining rise below -3 C and used fixed UTC
offsets. The historical "naive median" comparison uses the full-sample median,
so it is descriptive rather than held out. The baseline excludes forecasts.

LOO trains on both earlier and later days. Neighboring weather days are correlated,
many candidates/hours were inspected, and the original combined-feature selection
was not nested inside the folds. These results show a research method and a
location-specific association, not chronological out-of-sample trading evidence.

## 2. What does the temperature stream add?

The sensor ablation refits two arms on the same day set:

- A: METAR plus forecast.
- B: METAR plus forecast plus PWS temperature, with the solar-dependent noise/bias rule.

Both arms use the same Monte Carlo seed for a day and are scored on a common
contiguous integer support containing both distributions and the outcome.
This measures the difference between fitted **pipelines**, rather than removing
a sensor while leaving all other parameters fixed.

For ordered temperature outcomes the scorer uses normalized ranked probability
score, alongside multiclass Brier and entropy:

$$
RPS=\frac{1}{K-1}\sum_{k=1}^{K}(F_k-\mathbf{1}\{y\leq k\})^2,
\qquad BS=\sum_{k=1}^{K}(p_k-\mathbf{1}\{y=k\})^2.
$$

The published Warsaw aggregates cover 92 days, six hours (08–18 every two hours),
and 1,500 scoring paths per snapshot: **552 paired snapshots per variant**.

| Archived variant | Mean RPS without PWS | Mean RPS with PWS | Relative reduction |
|---|---:|---:|---:|
| Hourly path maxima | 0.068779 | 0.057076 | 17.02% |
| Brownian-bridge maxima | 0.090609 | 0.076250 | 15.85% |

The timing profile explains more than the pooled number. In the hourly-grid
variant, PWS changes RPS from 0.06579 to 0.06512 at 08:00, versus 0.07351 to
0.05108 at 16:00. By 18:00 the realized daily maximum had already been observed
in all 92 days according to the artifact's METAR-floor diagnostic. Much of the
late-day difference concerns narrowing uncertainty once the maximum is known.

The bridge variant tests a separate approximation: hourly grid points can miss
a path's within-hour maximum. Each arm was refitted for that variant, so the two
rows are not an isolated fixed-parameter bridge experiment. RPS's normalizing
support can also differ between variants, even though each A/B pair shares its
support. Neither row is a held-out result; the public audit recomputes the
weighted aggregate from the hourly score table, not the historical simulation.

## 3. Forecast-aware parameter fitting across 28 markets

The fitter replayed observations in time order and selected parameters by mean
multiclass Brier over local hours 08, 10, 12, 14 and 16. The observed source uses
400 Monte Carlo paths by default and resets the scoring seed to 7 for each
candidate. It then scores the production-default parameter vector on the same
days. Candidate selection and the displayed improvement share their data.

| Input regime | Observed search grid in Celsius units | Candidates |
|---|---|---:|
| PWS present | beta/h: 0, .25, .5; process SD: .4, .8, 1.4; PWS SD: .5, .8, 1.2; PWS bias: -.3, 0, .3 | 81 |
| METAR only | beta/h: 0, .25, .5, .7; process SD: .4, .8, 1.4; METAR SD: .29, .5 | 24 |

Standard deviations and biases are multiplied by 1.8 in Fahrenheit markets.
The PWS default is beta=.5, process SD=1.5, PWS SD=.6, bias=0; the METAR-only
default is beta=.25, process SD=.4, METAR SD=.29. Process noise enters the
variance as sigma_process squared times hours. The separately implemented sensor
ablation has a 108-candidate PWS grid because it also includes beta=.7.

The archived 28 fit records have 36–93 fit-days per market. Recomputing
`100 * (baseline_brier - best_brier) / baseline_brier` from those records gives an
**equal-market mean reduction of 24.3531%, median 25.7841%, and 17 markets above
20%**. This corrects the older prose's count of 16; the machine-readable fit
artifacts are the basis of the public count. It is not a pooled snapshot-weighted
score, a comparison with market prices, or an out-of-sample gain.

Istanbul illustrates the fitted change: the default Brier 0.614728 becomes
0.477053 (22.40% reduction), with process SD .8 and PWS SD 1.2 instead of 1.5
and .6. The archive therefore records a quieter process and less sensor trust
in that fit. The selected grid is not proof these parameters are optimal outside
the fitted window.

## 4. Separate forecast and sensor diagnostics

The forecast diagnostic compares **rounded** forecast daily maxima with rounded
METAR daily maxima. Its 28-market table includes MAE, signed forecast-minus-METAR
bias, RMSE and fraction within one market unit. For example, archived London MAE
is .35 C, Istanbul .77 C and Seoul 5.28 C, with Seoul bias -5.28 C. Fahrenheit
markets retain Fahrenheit errors; they must not be ranked against Celsius errors
without conversion. These are stored aggregates, not recomputed from full curves
by the public command.

The original fit used the Open-Meteo historical-forecast series while production
used Wunderground. Provider-specific bias should not be copied across them. The
later as-issued ECMWF run replay is a separate study with explicit run-availability
assumptions. Locating both does not turn the earlier calibration into a frozen
chronological holdout.

The solar/wind divergence tool asked a different question from future-rise
prediction: does `PWS - METAR` grow with radiation, and is that discrepancy damped
by ventilation? It matched near-simultaneous readings, summarized radiation bands,
compared wind terciles above 400 W/m2, and regressed discrepancy on radiation
overall and within wind groups. This supports a noise/bias modeling hypothesis;
it does not identify sensor bias causally because geography and local weather can
also separate the stations. No numerical divergence result is claimed as newly
reproduced here.

See [Model derivation](model-derivation.md) for equations and unit choices, and
[Experiments](experiments.md) for market-relative checks and their denominators.
