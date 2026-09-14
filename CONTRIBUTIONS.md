# Contributions and reading paths

I built weather-market acquisition and execution components and investigated how observations and
forecasts should change contract probabilities. This index connects those contributions to code and
evidence. The programme included live trading in daily-maximum temperature markets, alongside
probability and decision research with distinct deployment states. It is also a source for preparing
trading, research and engineering applications.

| ID | Contribution | Inspect | Evidence |
|---|---|---|---|
| W-MODEL-01 | Temperature-state estimation and Monte Carlo daily-maximum probabilities | [Equations and model versions](docs/model.md), [recorded replay](replay.py), [engine](kalman_engine.py) | A recorded Istanbul sequence, with input provenance and a reproducible figure |
| W-MODEL-02 | Current, next-report and conditional outcome distributions from a temperature posterior | [Derivation](docs/model-derivation.md), [probability walkthrough](examples/probability_walkthrough.py) | Explicit P/Q/D construction and residual; analytical update and state-restoration tests |
| W-RESEARCH-01 | Sensor feature design, incremental-information analysis and parameter selection | [Calibration](docs/calibration.md), [experiment index](docs/experiments.md) | Distinct daily-feature, ablation and 28-market fitting studies; their evaluation units and selection procedures are stated |
| W-RESEARCH-02 | Paired historical replay scoring and event/journal diagnostics | [Audit code](weather_research/evaluation.py), [runnable research audit](examples/research_audit.py), [experiment records](docs/experiments.md) | Compact historical extracts support recomputation of selected scores; model versions and source substitution remain explicit |
| W-DECISION-01 | Scenario-based inventory payoff, variance penalty and one-step action evaluation | [Strategies and equations](docs/strategies.md), [decision code](weather_research/decisions.py) | BUY/SELL/HOLD calculations and conditional-report playbooks, with correctness tests and labeled publication fixes |
| W-STRATEGY-01 | Deterministic impossible-bucket rules and market-mass repricing heuristics | [Rule implementation](weather_research/rules.py), [decision walkthrough](examples/decision_walkthrough.py) | Source-derived rule transitions; book-price heuristics distinguished from calibrated probabilities |
| W-ENG-01 | Source-specific NOAA/MGM collection using persistent clients, concurrent polling and explicit observation identity | [Architecture](docs/system-architecture.md), [acquisition code](weather_research/acquisition.py) | Source-derived parsing/scheduling; synthetic event walkthrough and controlled scheduling tests |
| W-ENG-02 | Separation of order preparation from submission: cached metadata, parallel warmup and pre-signing | [Execution design](docs/execution-engineering.md), [implementation](weather_research/execution.py) | Offline call trace establishes which work is outside submission; historical client mapping |
| W-ENG-03 | Instrumentation of DNS/TCP/TLS/request stages and cross-region request comparisons | [Measurement case](benchmarks/README.md), [GET probe](benchmarks/connection_probe.py) | Archived report transcribed with protocol and scope; probe behavior checked with mocked connections |
| W-ENG-04 | Explicit observation and order lifecycles, including duplicate/corrected reports and pending submission | [Observation semantics](docs/observations.md), [adaptation record](docs/engineering-provenance.md) | Historical design plus identified implementation differences; stronger public guards labeled separately |

## A three-minute review

1. Read the [model overview](docs/model.md) and [decision equations](docs/strategies.md): how information
   changes probabilities, how an outcome pays, and how inventory changes an action's value.
2. Inspect the [experiment index](docs/experiments.md): what was compared, which dataset was used and
   what can be recalculated from the public inputs.
3. Follow the [architecture](docs/system-architecture.md) into the
   [preparation/submission example](examples/execution_walkthrough.py) and [measurement case](benchmarks/README.md).

For a quantitative research review, follow the derivations and audit calculations. For a trading review,
inspect portfolio scenario payoffs, conditional actions and entry/exit assumptions. For an engineering
review, follow scheduling, cache invalidation and order-state handling into the source and tests.

## Application wording grounded in this release

- Developed a forecast-pulled Bayesian temperature model and Monte Carlo outcome distributions, linking
  sensor uncertainty and official-report timing to daily-maximum contracts.
- Investigated sensor information value and model calibration through daily-feature comparisons,
  sensor ablations, parameter fitting and historical forecast/replay diagnostics.
- Implemented a one-step portfolio decision evaluator using scenario payoff and variance, with
  conditional playbooks for the next weather report and explicit market-reaction assumptions.
- Developed an event-driven weather-market acquisition and execution system, using persistent HTTP
  clients, concurrent collection and source-aware observation handling.
- Separated metadata retrieval and signing from event-triggered submission, with asynchronous cache
  warmup and pre-signed order preparation in historical CLOB clients.
- Instrumented connection setup and request latency to inform execution placement; published the
  historical measurement protocol and an inspectable read-only probe.

These sentences describe engineering and research contributions. Attach the relevant links when using
them. Do not convert the single signed-request case into a production p99, a fill-time guarantee or a
causal geography benchmark. The examples demonstrate software behavior; they do not establish returns.

The machine-readable contribution record is [evidence/claim_ledger.json](evidence/claim_ledger.json).
Historical numerical findings have their own [experiment records](evidence/experiments.json).
