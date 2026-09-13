# Observations and clocks

A useful weather event needs both its physical observation time and its availability to the process
making a decision. These answer different questions: what was measured, and what could the system know?

| Clock | Meaning | Use |
|---|---|---|
| Observation time | Time encoded by the station report | Identify and order physical observations |
| Source publication time | Time the source made that observation available, when recorded | Separate publication delay from transport |
| Local arrival time | Time this process received the event | Causal replay and decision availability |
| Monotonic request timestamps | Start/end from one process's monotonic clock | Duration measurement without wall-clock adjustments |

Wall-clock differences between machines require clock alignment. A timestamp in a weather report is
not proof of when an endpoint first published it. End-to-end latency needs matched records across the path.

## The recorded probability replay

In the original journals, `ts` orders events by arrival. PWS `obs_time_utc` and METAR `valid_utc` are
observation timestamps. Forecast records contain curve timestamps and temperatures known at their arrival.
Using a revised forecast before it was available would introduce lookahead.

The existing Istanbul replay retains these input formats. The new acquisition example is a separate,
explicit normalization exercise; it does not silently rewrite the recorded model inputs.

Arrival ordering governs when a record becomes available at each replay-grid step. Within a refresh,
the historical model ingests PWS first and METAR second, each sorted within its source. A delayed report
updates the current posterior without rolling the state back to the report's observation time. This is
an approximation to delayed-observation filtering, not a globally chronological assimilation engine.
The model also deduplicates METARs by observation time; the acquisition ledger's correction support
does not imply that same-time revisions are assimilated by the model.

## Two sources, one observation

NOAA provides station text containing the raw METAR. MGM provides JSON with a METAR and other sensor
fields. The historical trigger rules used the METAR temperature, not MGM's separate decimal temperature.
The public parser retains that distinction.

An observation may be delivered repeatedly or by both sources. Deduplication must consider station,
observation time and report content. A corrected report can share a timestamp with an earlier report;
the public correction policy and its differences from the historical collectors are documented in
[acquisition implementation](acquisition-implementation.md).

```bash
python -B examples/acquisition_walkthrough.py
```

The fixture is synthetic. It illustrates parsing, multiple deliveries and clock interpretation rather
than measuring a real information lead. Contractual settlement still depends on the individual market rule.

See [system architecture](system-architecture.md) for the acquisition and relay paths and
[measurement definitions](../benchmarks/README.md) for latency boundaries.
