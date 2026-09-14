# Weather Daily-Max Markets

**Live trading, observation pipelines, probability models and execution engineering.**

I traded daily-maximum temperature prediction markets and built observation-collection and execution
components for that work. Alongside live trading, I researched how station observations and forecasts
change daily maximum-temperature probabilities. The work connects source timing, probability modeling,
market rules and the cost of acting through an order book.

The repository follows the complete research chain: observations -> temperature posterior -> contract
probabilities -> portfolio decisions -> prepared execution. It includes source-derived components,
a recorded-day replay, and compact historical research inputs for checking published scores.
Start with the [research map](docs/research-map.md) or the [contribution index](CONTRIBUTIONS.md).

## What I built

| Problem | Approach | Inspect |
|---|---|---|
| Continuous observations must become discrete contract probabilities | Forecast-pulled temperature filter, source-specific uncertainty, bias correction and remaining-day Monte Carlo | [Equations and model versions](docs/model.md), [engine](kalman_engine.py) |
| More sensor data may add redundant or misleading information | Daily features, leave-one-day-out comparisons, sensor ablations, parameter search and paired replay scores | [Calibration](docs/calibration.md), [experiment records](docs/experiments.md) |
| A probability change is not yet a trading decision | Scenario portfolio payoff, variance penalty, BUY/SELL/HOLD evaluation and conditional next-report playbooks | [Decision equations](docs/strategies.md), [implementation](weather_research/decisions.py) |
| New observations arrive through different sources and may repeat | Persistent HTTP clients, source-specific concurrent polling, parsing and observation identity | [Acquisition and event flow](docs/acquisition-implementation.md) |
| Setup work delays an event-triggered order | Fetch metadata/balance and build/sign before the trigger; reuse cached inputs | [Preparation and submission](docs/execution-engineering.md) |
| A single latency number hides the bottleneck | Separate DNS, TCP, TLS, warm requests and signed submission; compare historical locations | [Measurement case and probe](benchmarks/README.md) |

## Follow an observation into a decision

The temperature state evolves as
`T_next = (1 - beta * dt) * T + beta * dt * forecast_next + process_noise`.
A scalar Bayesian update incorporates each observation with its source noise and offset. The model
then estimates the daily maximum, the next reported temperature, and conditional daily-maximum
distributions. The decision layer evaluates the resulting portfolio payoff across mutually exclusive
outcomes and ranks feasible actions by `E[PnL] - lambda * Var(PnL)`.

The [derivation](docs/model-derivation.md) explains the approximations in the probability construction;
the [decision notes](docs/decision-implementation.md) connect the objective to actual action selection.
This is a one-step decision evaluator, with a separate heuristic for market reaction after a report.

```bash
python -m pip install -r requirements.txt
python -B examples/probability_walkthrough.py
python -B examples/decision_walkthrough.py
python -B examples/research_audit.py
python -B -m unittest discover -s tests -v
```

The first two commands use declared fixtures to expose calculations. The research audit uses compact
historical extracts; [experiment records](docs/experiments.md) specify which quantities can be recomputed.

## Selected research findings

| Study | Result and interpretation |
|---|---|
| Parameter search across 28 markets | Selected settings reduced Brier score by **24.35% on average relative to defaults**, equally weighting market-level percentage changes. Selection and comparison used the same data; this is fitting improvement. |
| Warsaw sensor ablation | Adding the sensor reduced pooled RPS by **17.02%** in the default grid and **15.85%** in the bridge variant, each over 552 scored snapshots. Both arms were refitted on the same 92 days. |
| Wider historical replay with a substituted forecast source | On 5,104 scored snapshots across 177 city-days, model-minus-market Brier was **+0.0579**, with a 95% city-day bootstrap interval **[+0.0332, +0.0825]**. The market scored better on that eligible sample. |

These answer different questions: whether tuning improves a model, whether a sensor adds information
inside a fitted setup, and whether a historical model matches market forecasting quality. The
[experiment chapter](docs/experiments.md) links each finding to its inputs, scoring code and version;
it also includes the daily-feature and pre-observation studies and a [score comparison figure](output/research-scores.png).

## Run the engineering examples

Python 3.10 or newer; standard library only:

```bash
python -B examples/acquisition_walkthrough.py
python -B examples/execution_walkthrough.py
```

The first example follows a synthetic report through source arrival, duplicate delivery and correction.
The second traces cache warmup, signing and submission through local providers and a fake transport.
Their purpose is to make the implementation inspectable; historical latency evidence is listed separately.

## Run the recorded probability example

```bash
python -m pip install -r requirements.txt
python -B replay.py --out output/replay.png
```

The Istanbul example covers 11 June 2026 and processes 1,165 recorded input events: 1,097 PWS
readings, 26 METAR observations and 42 forecast records. The driver admits records by their arrival
timestamps at each replay step. Within a refresh the historical engine processes PWS and METAR batches
separately; this is not a globally event-time-ordered filter.

![Temperature posterior and daily-maximum probabilities](output/replay.png)

This one-day example demonstrates the implementation. It is not an out-of-sample forecasting score
or a trading return. The confirmed maximum in the replay is an observation-derived quantity;
contractual settlement must be checked against the individual market rule.

## Research components

- [System architecture](docs/system-architecture.md): source collectors, relay, market state and execution.
- [Execution engineering](docs/execution-engineering.md): historical v1/v2 clients and preparation costs.
- [Model](docs/model.md): posterior, forecast pull, conditional distributions and version comparison.
- [Observations](docs/observations.md): arrival clocks and component boundaries.
- [Calibration](docs/calibration.md): feature design, ablations and parameter fitting.
- [Experiments](docs/experiments.md): sample units, stored results and reproducible score calculations.
- [Strategies](docs/strategies.md): deterministic bucket rules, portfolio risk and event-conditioned actions.
- [Market events](docs/market-events.md): bucket repricing and evidence limits.
- [Limitations](docs/limitations.md): consistency metrics, deployment and generalization.

| File | Purpose |
|---|---|
| `weather_research/` | Portable acquisition, execution, decision and evaluation components |
| `examples/` | Probability/decision walkthroughs, research audit and engineering fixtures |
| `benchmarks/` | Historical measurement record and read-only connection probe |
| `kalman_engine.py` | Offline model implementation |
| `replay.py` | Arrival-ordered replay and figure generation |
| `data/` | Recorded example, compact research extracts and provenance |
| `docs/` | Methods and research scope |
| `REPRODUCIBILITY.md` | Commands and what they establish |
| `CONTRIBUTIONS.md` | Contribution-to-code/evidence index for technical and application review |

The collection/execution system and inference research were parts of the same programme, but their
versions and deployment states differed. In particular, the final calibration report described
an offline configuration that had not been deployed.

The extracts preserve historical methods and identify publication corrections in the
[engineering](docs/engineering-provenance.md), [model](docs/model-derivation.md) and
[decision](docs/decision-implementation.md) notes. Historical experiment scores retain their original
version labels; they are not attributed to corrected public code. The compact audits reproduce specific
score calculations, not the entire collection and model-fitting history.

[Eren Ege Çelik](https://www.erenege.dev) ·
[Related crypto research](https://github.com/ErenEgeCelik/crypto-updown-prediction-market-research)

## License

Authored code and documentation: [MIT](LICENSE). Third-party data provenance is described in [data/README.md](data/README.md).
