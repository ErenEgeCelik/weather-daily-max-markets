# Connection and submission measurements

The execution research separated connection setup, warm requests, preparation and signed submission.
That decomposition guided moving work before the trigger and choosing the execution location.

## Historical case: cross-region execution

The archived engineering report dated 8 May 2026 records the following measurements. These are
**reported historical values**: the original request-level output has not been matched locally, so
the table is not presented as a reproduced benchmark. A machine-readable transcription is in
[historical_report.json](historical_report.json).

| Reported interval | Local machine in Turkey | Ireland | Virginia |
|---|---:|---:|---:|
| Cold GET, total | 330 ms | 101 ms | 173 ms |
| Warm GET | 105 ms | 25 ms | 93 ms |
| Warm unauthenticated POST, rejected with 401 | 106 ms | 20 ms | — |
| POST after idle period | 215 ms | 33 ms | — |

The report gives a general sample count of 30, while the inspected idle-POST branch uses 10 attempts.
The table does not explicitly label its summary statistic. The original tool's small-sample `p99`
was a maximum; those labels are not reused here. Rejected POSTs measure a request/rejection path, not
an accepted order or a fill. These POST probes are not included in the runnable public tool.

A separate single paired trial reports signed-request response times of **134.9 ms from the local
machine and 30.4 ms from Ireland**. Both responses were `delayed`. The trial used different outcome
tokens and different machines, so it is not a controlled estimate of geography alone or a latency
distribution. It is a concrete historical example of the instrumentation and deployment decision.

## Inspect the measurement implementation

[connection_probe.py](connection_probe.py) adapts the original decomposition routine. It measures:

1. DNS lookup.
2. TCP connection.
3. TLS context creation and handshake.
4. HTTP request construction/send through receipt of the first response byte.
5. Remaining response reception.

Durations use one local monotonic clock. The warm-burst helper reuses a caller-supplied async client.
The original cold probe used raw sockets and TLS; the weather production parsers used persistent HTTP
clients. These are different components of the engineering work.

The CLI requires an explicit HTTPS URL and performs GET requests only:

```bash
python -B benchmarks/connection_probe.py --help
```

Supplying a URL runs a new network measurement. The public release checks the instrument with mocked
connections and does not claim a new cross-region measurement. The instrument forces HTTP/1.1;
it should not be treated as the deployed SDK's complete HTTP/2 path.

## Timing boundaries

| Quantity | Start -> end | What it establishes |
|---|---|---|
| Observation age at receipt | Station observation -> local arrival | Combined observation/publication/transport age, subject to clock interpretation |
| Fetch request | Local request start -> response read | Source request duration |
| Cold HTTPS | DNS start -> response read | Connection setup plus request path |
| Prepared POST response | Local submission -> HTTP response | Request/acknowledgement duration |
| Fill notification | Submission -> matched order-specific update | A separate execution event; absent from the reported case |

The historical trigger field named `total_e2e` was function-entry to response. It did not begin at
source publication. An estimated sum of fetch/relay/POST durations is not a measured end-to-end sample.

NOAA notes contain different RTT headlines from different dates and protocols, so this release does not
select a single NOAA speed figure. One MGM machine was named "Istanbul" in notes but had conflicting
hosting-location documentation; the architecture labels its role rather than assuming a physical location.

See [execution engineering](../docs/execution-engineering.md) for the optimization and
[provenance](../docs/engineering-provenance.md) for the historical-source/public-extraction boundary.
