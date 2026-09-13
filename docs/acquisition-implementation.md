# Acquisition: from source payload to a timed observation

The historical weather system moved official observations through a source-side
collector, a relay, and consumer-side adapters. Collectors used a persistent
`httpx.AsyncClient` and HTTP/1.1 connection pools. The engineering contribution
was source-specific scheduling and removal of repeated setup from polling, with
explicit duplicate suppression before downstream work. This extraction makes
the parsing and scheduling decisions inspectable without a deployed service.

Run the synthetic example:

```sh
python examples/acquisition_walkthrough.py
python -m unittest discover -s tests -p test_acquisition.py -v
```

## Source-derived components

| Historical source identifier and symbol | Published implementation | What is retained |
| --- | --- | --- |
| `collector-noaa`, `_parse_time`, `_parse_temp`, `poll_station` | `parse_metar`, `parse_noaa_text` | Official `DDHHMMZ` report time, integer `TT/TdTd` temperature with `M` for negative, two-line TXT envelope |
| `collector-mgm`, `poll_mgm_station` | `parse_mgm_json` | First JSON row, `rasatMetar` as the official report, separate `sicaklik` and `veriZamani` fields |
| `consumer-noaa`, `_entry_to_obs`, `NOAABatchPoller.fetch_all`, `NOAABatchSource.fetch_latest` | `Observation` and `ObservationLedger` | Normalized consumer observation, station identity, raw-report suppression before repeated model work |
| `collector-noaa`, `scraper_loop` | `collect_noaa_batch` | One shared fetch dependency, multiple poll tasks, a complete-batch `gather` barrier, at most one changed report per station released per batch |
| `collector-mgm`, `mgm_loop`, `mgm_worker` | `collect_mgm_once` | Independent worker completion and immediate worker-local emission, without waiting for other workers |
| Both collectors, client initialization | `historical_pool_settings` | HTTP/1.1, 60-second keepalive expiry, and their different connection-pool sizing formulas |

All published functions are in [acquisition.py](../weather_research/acquisition.py).
Source identifiers label reviewed historical implementations, not release dates
or claims that all versions behaved identically.

The source NOAA pool size was `max(20, workers × 4)`. The MGM pool size was
`max(20, workers × stations × 2)`. One client lived outside the polling loop.
These are configuration choices, not measured throughput or latency results.
The code inspected here uses HTTP through `httpx`; it does not establish a
separate hand-written raw TCP transport or parser.

## Completion and delivery are different events

The NOAA collector parsed responses as individual requests completed, but its
caller waited for the entire `asyncio.gather` batch before broadcasting. It then
selected at most one result per station in task-list order. A slow request could
therefore delay delivery of an already completed observation. A revision found
within the same batch could also be omitted by the per-station delivery gate.
`collect_noaa_batch` retains that gate and barrier so this distinction is visible.

MGM workers instead called broadcast immediately after a changed poll result.
`collect_mgm_once` extracts one such iteration; callers may run several iterations
concurrently with the same fetch dependency. Tests use controlled completion
events to establish the different scheduling behavior, not elapsed-time targets.

The historical MGM worker also used expected report windows, reducing polling
after finding a report in a window. That deployed scheduling policy is documented
here but not reproduced in this finite offline example. Relay authentication,
reconnect loops, production retry policy, source URLs, and HTTP request headers
are outside this extract. Network errors propagate here; the original collectors
caught and logged request errors.

## Observation semantics and publication changes

The original parsing helpers supplied a day/time token; consumer records often
used the current time as their timestamp. The public `Observation` separates:

- `observed_at`: the date inferred from the official report's `DDHHMMZ` token;
- `arrived_at`: the explicit timestamp when this replay receives the payload;
- `source_recorded_at`: the NOAA TXT header or timezone-aware MGM metadata field;
- `metar_temperature_c`: the official report's integer temperature;
- `mgm_temperature_c`: MGM's separate decimal field, retained for inspection.

The decimal temperature never silently replaces the official METAR temperature.
An unchanged official report with a changed decimal field remains an official
report duplicate; this extract is not a continuous decimal-temperature feed.
Missing or malformed temperature groups remain missing. No fallback temperature
is taken from remarks. MGM's missing-value sentinel `-9999`, nonfinite numbers,
and booleans are rejected as decimal observations.

Month and year inference is deliberately bounded. Only a unique timestamp in
the previous 36 hours, including the arrival instant, is accepted. Future,
older, invalid, and ambiguous report times fail explicitly. Timezones are
mandatory; a naive MGM metadata timestamp is left unresolved. An archive loader
must supply a trustworthy arrival date. The NOAA header is retained independently
and is not substituted for arrival time or asserted to be exact publication time.

`observation_age_seconds = arrived_at − observed_at` includes the reporting
interval, upstream publication delays, and transport effects. It is **not** HTTP
fetch latency, one-way network latency, or time available to trade.

The dedup implementation is an explicit **publication refactor**:

- Historical NOAA compared each station's last raw report. Historical MGM
  compared the `DDHHMMZ` token, which could suppress corrections at the same time.
- The public ledger identifies a report by station, full inferred UTC observation
  timestamp, and normalized report text, independent of the source venue.
- An identical cross-source report is a duplicate. A changed report at the same
  observation timestamp is a `revision`; the label does not by itself certify
  which source or revision is authoritative.
- A newly seen older observation is `late`, retained as an event but not promoted
  to current state. Input must follow nondecreasing arrival timestamps. Equal
  arrival times are accepted in caller order; no finer ordering is inferred.
- Storage is intended for finite offline runs. A production ledger would need a
  defined retention policy, durable state and source-authority handling.

This is improved public behavior, not a claim that historical deployments already
handled all these cases. The scheduling skeleton preserves source behavior while
the parser and dedup rules deliberately make the edge cases explicit.

## What the example establishes

[The fixtures](../examples/fixtures/acquisition/README.md) are hand-authored. They
show a report across a UTC month boundary, a cross-source duplicate, a same-time
correction and a newer report. The fake transport records that the same dependency
services multiple polls. It owns no connections, so it cannot establish actual
socket reuse, deploy-time latency, reliable source availability, or strategy P&L.

[Tests](../tests/test_acquisition.py) cover negative and missing temperatures,
malformed responses, UTC date resolution, metadata separation, duplicate and
late observations, and the batch-versus-worker delivery distinction. These are
software-behavior checks; strategy and model evidence belong to their separate
research sections.
