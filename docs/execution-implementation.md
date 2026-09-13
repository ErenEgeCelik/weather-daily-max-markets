# Execution implementation and source mapping

The weather execution work separates **ARM**, when metadata and an order can be
prepared, from **FIRE**, when an incoming observation triggers submission. The
public extraction makes this separation inspectable: its FIRE method consumes
previously built bytes and calls the injected transport once. Metadata lookup,
balance lookup and signing happen earlier.

Read [the implementation](../weather_research/execution.py), then run:

```bash
python -B examples/execution_walkthrough.py
python -B -m unittest discover -s tests -p test_execution.py -v
```

Both commands use only the Python standard library. The walkthrough uses a
synthetic provider, a marked JSON payload in place of a cryptographic signature,
and an in-memory transport. It cannot contact an exchange.

## What was engineered

The earlier wrapper warmed tick-size, negative-risk and fee metadata before
building a capped BUY order. Its pre-sign method returned an order for later
submission. The later wrapper added explicit metadata dictionaries, a version
warm-up and a prefetched collateral balance. Supplying an explicit price cap and
cached order options made those inputs available during preparation without
waiting for an order-book lookup at the trigger.

The asynchronous ARM path submitted independent metadata/balance reads through
`asyncio.gather` and moved signing to an executor. A trigger-level scheduler also
armed multiple triggers concurrently. Near an expected METAR publication window,
it refreshed fee and balance inputs and prepared the still-armed orders again.
The trigger's FIRE path removed the saved order from its preparation dictionary
before submitting it.

The source also maintained an HTTP keep-alive task between infrequent weather
events. This addresses connection establishment separately from metadata and
signing work. The public extraction demonstrates the latter two boundaries;
it does not contain the source's connection pool, keep-alive loop or HTTP client.

## Source-to-public mapping

These IDs identify historical source snapshots, not additional public modules.
The publication's provenance index records their snapshot identity. The public
code is a source-derived adaptation with new interfaces and tests, not a claim
that the historical wrappers already contained these exact classes or guards.

| Source ID | Historical symbols | Public treatment |
|---|---|---|
| `W-EXEC-V1` | `ClobOrderClient.warm_cache`, `pre_sign_market_buy`, `post_pre_signed` | Preserve preparation/submission separation; omit SDK and authentication wiring. |
| `W-EXEC-V2` | `ClobClient.warm_cache`, `warm_cache_async`, `_options_for`, `_build_signed_market`, `pre_sign_buy`, `fire` | Re-express cached preparation inputs and concurrent cold reads with `PreparationProvider`, `Signer` and `Transport`. |
| `W-EXEC-V2` | `ClobClient.start_keepalive`, `_keepalive_loop` | Document the connection-lifetime decision; do not simulate network performance. |
| `W-EXEC-TRIGGER` | `_arm_trigger`, `metar_window_repre_sign`, `fire_trigger` | Preserve refresh-before-event and consume-before-submit structure. The synthetic example uses explicit calls instead of a deployed scheduler. |
| `W-EXEC-V2`, `W-EXEC-TRIGGER` | `_raw_to_result`, `_adapt_response`, `_recheck_delayed_fill` | Expose a source inconsistency and replace inferred fills with separate order-scoped execution confirmation. |

The source's v1 and v2 weather pre-sign paths included historical FAK BUY orders;
the trigger also contained a v1 pre-signed GTC limit path. These are distinct from
the BTC project's maker-only execution policy. The public abstraction represents
a capped BUY intention but implements neither exchange order type.

## Cache and preparation lifetime

`PreparedExecutor` is scoped to one token. The first `warm_cache()` reads tick
size, negative-risk configuration, fee rate, API version and balance concurrently.
Repeating it reuses the first four values but reads balance again, following the
historical v2 method. `force=True` refreshes fees as well. `invalidate()` drops all
metadata when configuration changes; the public code does not assume that tick
size or risk configuration can never change.

Every warm-up advances a generation and invalidates older preparations. A
preparation must also be younger than the configured monotonic-clock lifetime.
The example's 30-second lifetime is an illustrative publication guard, not a
measured historical optimum or an exchange signature-expiry rule. Refresh and
re-sign occur explicitly in ARM; FIRE rejects stale input without attempting a
slower fallback.

The public version additionally requires every preparation fetch to succeed,
checks positive finite amounts and tick-aligned price caps, detects refreshes
during signing, and prevents a submitted decision ID from being reused. These
are stronger boundaries than the historical wrappers' partial-cache handling and
fallback paths. A balance snapshot is an advisory preflight input: this extraction
does not reserve collateral across multiple decisions, processes or wallets.

## Acknowledgement and execution are separate events

The reviewed wrapper treated `delayed` as `FILLED` in one typed result path and as
`filled_pending` in another compatibility path. The trigger added a later position
check for some delayed responses. A positive token balance alone is not sufficient
attribution when a position may already exist or another strategy shares a wallet.

The public lifecycle is explicit:

```text
prepared -> pending -> acknowledged -> confirmed (separate execution report)
                    -> rejected
                    -> unknown (timeout, cancellation or unrecognized reply)
```

Even a `matched` POST reply is only acknowledged here. `confirm_fill()` requires
a separately supplied order ID, trade ID and positive executed quantity. The
quantity may be a partial fill; `confirmed` does not assert complete order fill
or blockchain finality. The walkthrough supplies synthetic evidence explicitly.
An HTTP timeout consumes the local preparation and leaves its outcome unknown:
the module does not automatically retry an order that may already exist. A real
integration would need durable order tracking, exchange reconciliation and
portfolio reservations; those are outside this offline extraction.

## What the walkthrough verifies

The output counts actual calls to injected fixture methods. The scheduler yields
cooperatively during reads so the test can observe that all five cold reads are
in flight together. It does not assign a simulated duration to a request.

| Operation | Metadata reads | Balance reads | Build/sign calls | Submissions |
|---|---:|---:|---:|---:|
| First warm-up | 4 | 1 | 0 | 0 |
| Repeat warm-up | 0 | 1 | 0 | 0 |
| Prepare an intention | 0 | 0 | 1 | 0 |
| Repeat the same preparation | 0 | 0 | 0 | 0 |
| FIRE a valid preparation | 0 | 0 | 0 | 1 |
| FIRE an expired preparation | 0 | 0 | 0 | 0 |
| Force refresh and re-prepare | 1 (fee) | 1 | 1 | 0 |

This verifies which work has been removed from the critical trigger path. It
does not establish exchange latency, fill rate, profitability, or a speedup in
milliseconds. Historical timing comments require matched logs and measurement
boundaries before being used as performance claims. Useful separate boundaries
are ARM cache time, ARM signing time, decision-to-submit time, POST round trip,
and the later execution-confirmation time.
