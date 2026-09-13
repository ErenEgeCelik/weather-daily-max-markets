# Contributions and reading paths

I built weather-market acquisition and execution components and investigated how observations and
forecasts should change contract probabilities. This index connects those contributions to code and
evidence. It is also a source for preparing research and engineering applications.

| ID | Contribution | Inspect | Evidence |
|---|---|---|---|
| W-ENG-01 | Source-specific NOAA/MGM collection using persistent clients, concurrent polling and explicit observation identity | [Architecture](docs/system-architecture.md), [acquisition code](weather_research/acquisition.py) | Source-derived parsing/scheduling; synthetic event walkthrough and controlled scheduling tests |
| W-ENG-02 | Separation of order preparation from submission: cached metadata, parallel warmup and pre-signing | [Execution design](docs/execution-engineering.md), [implementation](weather_research/execution.py) | Offline call trace establishes which work is outside submission; historical client mapping |
| W-ENG-03 | Instrumentation of DNS/TCP/TLS/request stages and cross-region request comparisons | [Measurement case](benchmarks/README.md), [GET probe](benchmarks/connection_probe.py) | Archived report transcribed with protocol and scope; probe behavior checked with mocked connections |
| W-ENG-04 | Explicit observation and order lifecycles, including duplicate/corrected reports and pending submission | [Observation semantics](docs/observations.md), [adaptation record](docs/engineering-provenance.md) | Historical design plus identified implementation differences; stronger public guards labeled separately |
| W-MODEL-01 | Temperature-state estimation and Monte Carlo daily-maximum probabilities | [Model](docs/model.md), [recorded replay](replay.py), [engine](kalman_engine.py) | A recorded Istanbul sequence, with input provenance and a reproducible figure |

## A three-minute review

1. Read [the architecture](docs/system-architecture.md): where information enters, how it is normalized,
   and what happens before an observation-triggered action.
2. Inspect [the preparation/submission example](examples/execution_walkthrough.py): look at the call
   trace and cache refresh, rather than treating an SDK call as the entire execution system.
3. Read [the recorded measurement case](benchmarks/README.md) and run one offline example.

For a deeper engineering review, follow the extraction notes into scheduling tests, cache invalidation
and order-state handling. For a modeling review, start from the recorded replay and the probability
model. The wider sensor, calibration and decision research is summarized in the existing method pages;
its full public pipeline is a separate expansion, not implied by the engineering examples.

## Application wording grounded in this release

- Developed an event-driven weather-market acquisition and execution system, using persistent HTTP
  clients, concurrent collection and source-aware observation handling.
- Separated metadata retrieval and signing from event-triggered submission, with asynchronous cache
  warmup and pre-signed order preparation in historical CLOB clients.
- Instrumented connection setup and request latency to inform execution placement; published the
  historical measurement protocol and an inspectable read-only probe.
- Built an observation-driven temperature and daily-maximum model, with a public recorded-data replay.

These sentences describe engineering and research contributions. Attach the relevant links when using
them. Do not convert the single signed-request case into a production p99, a fill-time guarantee or a
causal geography benchmark. The examples demonstrate software behavior; they do not establish returns.

The machine-readable record is [evidence/claim_ledger.json](evidence/claim_ledger.json).
