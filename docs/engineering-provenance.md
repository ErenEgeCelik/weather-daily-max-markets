# Engineering source and version record

This release extracts selected engineering ideas and logic from the author's historical weather
system. The public modules are portable research components: network clients, live account configuration,
service management and venue-specific signing adapters have been replaced by explicit dependencies.

The original inference replay remains unchanged. The new acquisition schema is separate from the three
recorded journal formats it consumes.

## Historical components and public artifacts

| Source family | Original responsibilities | Public artifacts |
|---|---|---|
| `collector-noaa`, `collector-mgm`, `consumer-noaa` | Station fetching, parsing, deduplication and event delivery | [Acquisition](../weather_research/acquisition.py), [symbol mapping](acquisition-implementation.md) |
| `W-EXEC-V1`, `W-EXEC-V2`, `W-EXEC-TRIGGER` | Metadata warmup, order preparation, submission and trigger handling | [Execution](../weather_research/execution.py), [symbol mapping](execution-implementation.md) |
| `W-LATENCY-PROBE` | Cold connection decomposition and warm request loops | [Read-only probe](../benchmarks/connection_probe.py) |
| `W-LATENCY-REPORT` | Historical cross-region timing report | [Transcribed evidence](../benchmarks/historical_report.json) |

Source snapshot hashes are in [source_snapshots.json](../evidence/source_snapshots.json). They identify
the private source bytes reviewed during extraction; they do not give the public reader access to the
upstream repository or independently authenticate its measurements. The published implementation,
fixtures and tests can be inspected directly.

## Preserved behavior and deliberate changes

- The acquisition routines retain NOAA's complete-batch barrier and MGM's independent worker emission.
  Their shared public ledger adds bounded UTC-date resolution, cross-source deduplication and explicit
  same-time revisions. These are documented changes, not claims about all historical deployments.
- The execution extraction preserves the preparation/submission separation, cache inputs and parallel
  warmup structure. Dependency injection, explicit preparation lifetime and stricter lifecycle guards
  make the public behavior testable; they are not a byte-for-byte export of a deployed SDK wrapper.
- Historical response adapters differed on delayed orders. The public component requires a separate
  order-scoped execution report before recording confirmed quantity. Its examples use fictional IDs.
- The connection probe preserves the original timing stages, with clearer field names and bounded
  response size. It removes order submission, authentication and machine-specific deployment code.
  Summary statistics report sample size, median, mean and range rather than calling a small sample's
  maximum a p99.
- The existing public Kalman engine includes a prior bias correction. Adding the engineering layer
  does not replace that engine with an older private snapshot.

## What each kind of evidence supports

| Evidence | Supported conclusion |
|---|---|
| Source-mapped implementation | A particular engineering design existed and can be inspected through the extraction |
| Synthetic example and unit tests | The public component behaves as specified on the exercised inputs |
| Recorded Istanbul replay | The model can process that recorded observation sequence and reproduce its outputs |
| Archived timing report | The author recorded those historical request measurements under the described protocol |

The latency raw samples were not recovered in this extraction. Historical measurements therefore remain
reported values, while the public probe and engineering examples are independently inspectable code.
No new network or live-order benchmark was run for this release.
