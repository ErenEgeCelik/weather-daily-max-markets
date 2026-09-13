# From temperature observations to market probabilities

This document explains the runnable [public engine](../kalman_engine.py), then
separates its historical predecessors and publication corrections. Its output is a
distribution over integer daily-maximum temperatures, which can be aggregated into
the event buckets defined by an individual market.

## 1. State, units and forecast

At time `t`, the hidden station temperature is represented by

$$T_t\mid\mathcal I_t\sim\mathcal N(\mu_t,V_t).$$

`mu` and all temperatures are in °C; variance is in °C². Elapsed process time
`delta` is measured in hours. The forecast-pull coefficient `beta` has units
hour⁻¹, and the diffusion coefficient `sigma_proc` has units °C / sqrt(hour).
Consequently `sigma_proc² × delta` is a variance, not a standard deviation.

`set_forecast` stores temperatures by local integer hour. `_forecast_at` linearly
interpolates between neighboring hours and holds the first/last value outside the
available range. It does not fit a new curve. `forecast_peak` is descriptive
metadata; changing only that field does not change the process target. The public
timezone is a fixed UTC offset, without automatic daylight-saving rules.

The scalar process with an available forecast `f(t + delta)` is

$$a=1-\beta\delta,\qquad
T_{t+\delta}=aT_t+(1-a)f(t+\delta)+\sigma_{proc}\sqrt\delta\,Z,
\quad Z\sim\mathcal N(0,1).$$

Independence of the new process increment gives the propagated moments:

$$\mu^-=a\mu_t+(1-a)f(t+\delta),\qquad
V^-=\max(10^{-6},a^2V_t+\sigma_{proc}^2\delta).$$

These equations map directly to `_process_advance`. If the forecast is unavailable
or `beta <= 0`, the public process keeps the mean and adds diffusion variance.
Nonpositive time differences do nothing. This is an Euler-style discrete process,
not an exact continuous Ornstein–Uhlenbeck transition. The scalar implementation
does not clamp `beta × delta`; unusually large gaps can overshoot the forecast.

The recorded replay uses `beta=0.5`, `sigma_proc=0.8`, `sigma_pws=1.2`, fallback
correction `+0.3`, and `tz_hours=3`. Constructor defaults are respectively `0.25`,
`0.4`, `0.6`, `0.0` and a caller-supplied offset. Regime-dependent process variance
appeared in design notes; the public scalar engine uses one process coefficient
per model instance.

## 2. Observation update and robust innovation handling

For an observation `y = T + b + epsilon`, with `epsilon ~ N(0,R)`, the update is

$$K=\frac{V^-}{V^-+R},\qquad
\mu^+=\mu^-+K(y-b-\mu^-),\qquad
V^+=\max(10^{-6},(1-K)V^-).$$

`kalman_update` implements this scalar calculation. `b` is the sensor's additive
offset relative to the station, so it is **subtracted** from the reading.
`_observation_update` first checks innovation `r = y - b - mu` against
`k × sqrt(V + R)`. When that threshold is exceeded, it replaces the prior variance
with

$$\widetilde V=\min\left(V_{cap},\;V\left[\frac{|r|}{k\sqrt{V+R}}\right]^2\right),$$

then performs the Kalman update. Defaults are `k=2` and `V_cap=5 °C²`.
Despite the parameter name `innovation_inflation_max`, the code caps the resulting
variance, not the multiplier. The guard is a heuristic response to a posterior
that has become too tight; it does not make the complete filter a linear Gaussian
model with an exact analytical likelihood.

For the first accepted observation, the implementation sets `mu = y - b` and
`V = R` directly. The constructor's initial `V=5` is not combined with that first
measurement as a separate prior observation.

## 3. PWS bias, solar effects and official rounding

`rolling_bias` pairs official temperatures with interpolated PWS values during
the previous 150 minutes, using official times strictly before the replay clock:

$$c_t=\frac{1}{n}\sum_i\left(M_i-\operatorname{PWS}(t_i)\right).$$

At least two usable pairs are required; otherwise the configured fallback
correction is used. Thus `c=+0.3` means **add 0.3 °C to the PWS reading** to align
it with the official station. The observation offset is `b = -c`, not `+c`.
The public engine preserves this corrected sign.

For solar radiation `s` in W/m², `solar_regime_adjustment` calculates

$$u=\operatorname{clip}\left(\frac{s-200}{800-200},0,1\right),\qquad
\sigma_{pws,eff}=\sigma_{pws}(1+\alpha u),\qquad
b_{eff}=-c+\gamma u.$$

Defaults are dimensionless `alpha=1` and `gamma=0.5 °C`. High solar readings
therefore reduce the corrected observation and increase its noise. Missing or
nonpositive solar radiation applies no adjustment. Base `sigma_pws` is floored at
`0.5 °C` before this calculation.

`interp_pws` uses linear interpolation across neighboring points. Its 12-minute
guard rejects a bracket only when **both** endpoints are farther than 12 minutes;
it can still interpolate across a long span if one endpoint is close. Without
a bracket, a single endpoint must be within 12 minutes. The helper assumes sorted
PWS input. The current public single-stream model does not estimate independent
biases or correlations for multiple PWS stations.

METAR reports are integer observations. In the filtering update, the code uses
`R_metar = 0.29² °C²`, close to the variance `1/12 °C²` of uniform rounding error.
This is a Gaussian pseudo-observation approximation to an interval-valued
measurement. Exact conditioning on a rounded report would instead condition the
latent temperature on the corresponding half-degree interval.

## 4. Cold start, warm refresh and delayed inputs

The replay driver sorts records by their **arrival timestamp** and injects only
records already received by the current replay step. Each injected observation
also retains its own observation/valid time. Forecast revisions become available
on arrival and replace the stored hourly curve for subsequent processing.

Within `refresh`, `_ingest_pws` runs before `_ingest_metars`; each source sorts
its own new rows by observation time. This is not a globally merged observation
stream. If a METAR is older than the current state time, `_process_advance` does
not rewind: the observation is applied to the current posterior. The code is not
a delayed-measurement smoother.

On cold start, PWS initialization retains only the latest three hours by default.
That warm-up cutoff does not apply to the separate METAR ingestion pass. Warm
refreshes process only observation timestamps beyond each source's watermark.
An older late arrival can therefore be omitted from filter updates even though
its official value contributes to the observed maximum. `add_metar` also keeps
only the first value at a given timestamp, so same-time correction support in the
acquisition ledger is **not** yet end-to-end correction support in this engine.

The paired bias is computed once per PWS ingestion batch from the available
buffers, then used for all new PWS rows in that refresh. Preloading data that has
not yet arrived would invalidate causal interpretation. `set_now` changes the
clock but does not itself refresh the cached snapshot.

Without any usable temperature observation, snapshot temperature `0`, uncertainty
`999` and the fallback maximum distribution are placeholders. A downstream
consumer must not treat that uninitialized state as a forecast.

## 5. Daily maximum: posterior to `P_now`

`_sample_remaining_paths` draws `N=10,000` starting temperatures from
`N(mu_t,V_t)`, then applies the process equation at the remaining integer-hour
grid points through the target local-day end. Each call defaults to a new NumPy
generator with seed `0`, giving reproducible draws for the same inputs.

For each path `j`, `_daily_max_distribution` forms

$$Y_j=\max\left(M_t,\left\lfloor\max_{h\in H_t}T_h^{(j)}+0.5\right\rfloor\right),
\qquad P_{now}(k)=\frac1N\sum_j\mathbf1\{Y_j=k\},$$

where `M_t` is the largest available official report for the target day. The
floor is omitted when no report exists. Sampling the initial posterior carries
state uncertainty into future outcomes rather than treating the latest reading
as exact.

The grid maximum is an approximation: it excludes intermediate peaks and does
not include the starting draw itself as a maximum candidate. The implementation
includes the exact next-midnight boundary, and ingestion similarly uses an
inclusive day end. It does not reconstruct a resolving station's complete
subhourly observation schedule.

If one future bucket above the confirmed maximum has probability at least 98.5%,
the degeneracy guard moves four percentage points out of it: 2.4 points to the
lower neighbor and 1.6 to the upper. This is an explicit heuristic, not a posterior
derivation. If no grid points remain, the function returns the official maximum
as certain when available; otherwise it rounds the current mean.

## 6. Next report: `Q_next`

`_next_metar_eta_min` chooses the next configured publication minute strictly
after the current local minute/second; defaults are `:20` and `:50`. It does not
estimate publication delay from the collector's arrival data. `_next_metar_distribution`
temporarily propagates the posterior to that time, then restores state and uses

$$Q(m)=\Phi\left(\frac{m+0.5-\mu_{next}}{\sigma_{next}}\right)
-\Phi\left(\frac{m-0.5-\mu_{next}}{\sigma_{next}}\right).$$

`gaussian_round_pmf` evaluates those half-degree intervals. Support is selected
around four standard deviations, probabilities below `1e-5` are omitted, and
the retained mass is normalized. `sigma_metar` is not added to the variance for
this calculation: `Q` is based on rounding the predicted latent temperature.
The helper assumes positive standard deviation. Publication scheduling can select
a report beyond the target day near midnight; callers must interpret the target
horizon accordingly.

## 7. Hypothetical report: `D_after` and consistency

For each next-report value `m`, `distribution_given(m)` advances to the report
time, applies the Gaussian pseudo-observation update, raises the official floor
to `max(M_t,m)`, and samples the remaining maximum. The public implementation
temporarily advances its replay clock for this conditional simulation and restores
the original clock, mean, variance, process timestamp and update label afterward.

An internally consistent joint model would satisfy total probability:

$$P_{now}(k)=\sum_m Q(m)D(k\mid m).$$

Here `consistency_residual` computes

$$r_\infty=\max_k\left|\sum_m Q(m)D(k\mid m)-P_{now}(k)\right|.$$

This maximum absolute bucket error is different from the L1 error, which sums
absolute bucket differences. The walkthrough prints both for clarity. It does
not assert that either is below a historical target.

The identity need not hold exactly in this implementation. `Q` uses a rounded
Gaussian interval, whereas `D` uses a Gaussian pseudo-observation with robustness
inflation. `P` uses an hourly maximum grid, while `D` introduces a next-report
floor at another time. Truncated supports, finite Monte Carlo samples and the
degeneracy guard add further differences. Common seeds improve reproducibility;
they do not remove construction error or prove calibration.

## 8. Model lineage and publication corrections

| Source identifier / symbols | Role in the research | Relation to this release |
| --- | --- | --- |
| `weather-legacy-model`, `WeatherMarketModel.refresh`, `distribution_given`, `_fallback_distribution` | Combines corrected PWS and recent official reports by inverse-variance weighting for `Q`; a separate AR(1) engine provides `P` and conditional outputs. Fallback maximum uses powers of a one-report CDF, effectively an independent repeated-report approximation. | Historical predecessor; its precision blending and heuristics are not silently attributed to the public scalar filter. |
| `weather-scalar-model`, `KalmanWeatherModel._process_advance`, `_observation_update`, `_ingest_pws_observations`, `distribution_given` | Forecast-pulled scalar posterior, observation updates, solar adjustment and shared output interface; production version supports multiple source adapters. | Source lineage for the reduced, offline, single-PWS-stream public engine. |
| `weather-forecast-provider`, `_blend_curves`, `ForecastCurveProvider.fetch_curve` | Caches hourly provider curves, averages WU and Meteoblue at matched UTC hours, then selects the local-day window and converts units. If both are present, WU's hour grid drives the blend; unmatched WU hours retain WU. | Public replay injects the recorded curve and does not call providers. |
| `weather-ensemble-prototype`, `HypothesisState`, `EnsembleKalmanModel.weights` | Tracks several forecast/parameter hypotheses. Scores are `(log prior + accumulated Gaussian log likelihood) / temperature`; softmax gives hypothesis weights. | Dormant research prototype: reviewed application source has no integration imports. Not used by the published replay. |

The ensemble prototype's reported total variance includes both within-hypothesis
variance and between-hypothesis mean dispersion. Its outcome methods call separate
research helpers through a reduced state wrapper; that prototype does not establish
a deployed, calibrated mixture of the complete scalar model's forecast paths.

Publication corrections are separate from historical results:

- The public PWS correction uses `offset = -mean(METAR - PWS)`, preserving the
  corrected bias sign. The reviewed private predecessor passed the opposite sign.
- Conditional simulation now starts its clock at the hypothetical report time and
  restores all mutated state, including after an exception. The earlier public
  implementation advanced the posterior but kept the earlier simulation clock and
  leaked the hypothetical update label.
- Without a forecast, scalar moment propagation now uses random-walk variance,
  matching the path sampler. The earlier code applied variance decay without a
  corresponding forecast pull.

These fixes improve the published model's mechanics. Previously stored residuals,
calibration metrics and strategy outcomes retain their original version labels;
they are not retrospectively upgraded. [Tests](../tests/test_probability_model.py)
check analytical updates, units, corrected bias direction, probability normalization,
state restoration, seeded repeatability and the exact residual definition. They
do not establish out-of-sample forecasting skill or trading profitability.
