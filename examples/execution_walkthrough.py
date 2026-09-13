"""Run the historical ARM/FIRE separation using synthetic, counted operations.

    python -B examples/execution_walkthrough.py

No network, credentials, exchange SDK or real signing is involved.
"""

from __future__ import annotations

import asyncio
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from weather_research.execution import (
    CacheSnapshot, FillConfirmation, OrderIntent, PreparationError,
    PreparedExecutor, SubmissionReply,
)


class SyntheticVenue:
    """Count injected calls, yielding cooperatively to expose concurrency."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.active = 0
        self.max_parallel = 0
        self.now = 0.0

    async def _read(self, name: str, value):
        self.calls[name] += 1
        self.active += 1
        self.max_parallel = max(self.max_parallel, self.active)
        try:
            await asyncio.sleep(0)  # Scheduler hand-off, not a latency simulation.
            return value
        finally:
            self.active -= 1

    async def tick_size(self, token): return await self._read("tick_size", 0.01)
    async def neg_risk(self, token): return await self._read("neg_risk", False)
    async def fee_bps(self, token): return await self._read("fee_bps", 0.0)
    async def version(self): return await self._read("version", "synthetic-v2")
    async def balance(self): return await self._read("balance", 100.0)

    def build_and_sign(self, intent: OrderIntent, snapshot: CacheSnapshot) -> bytes:
        self.calls["build_and_sign"] += 1
        # A marked JSON fixture, not a cryptographic signature or exchange payload.
        return json.dumps({"synthetic": True, "token": snapshot.token,
                           "decision": intent.decision_id, "spend": intent.spend,
                           "price_cap": intent.price_cap}).encode()

    async def submit(self, payload: bytes) -> SubmissionReply:
        self.calls["submit"] += 1
        body = json.loads(payload)
        assert body["synthetic"] is True
        return SubmissionReply("synthetic-" + body["decision"], "delayed")


def changes(after: Counter, before: Counter) -> dict[str, int]:
    names = ("tick_size", "neg_risk", "fee_bps", "version", "balance",
             "build_and_sign", "submit")
    return {name: after[name] - before[name] for name in names}


async def walkthrough() -> dict:
    venue = SyntheticVenue()
    executor = PreparedExecutor("synthetic-weather-token", venue, venue, venue,
                                ttl_s=30, clock=lambda: venue.now)
    before = venue.calls.copy()
    await executor.warm_cache()
    cold = changes(venue.calls, before)
    cold_parallel = venue.max_parallel

    before = venue.calls.copy()
    await executor.warm_cache()
    repeat = changes(venue.calls, before)

    intent = OrderIntent("observation-001", spend=10.0, price_cap=0.60)
    await executor.prepare(intent)
    before = venue.calls.copy()
    await executor.prepare(intent)
    duplicate_preparation = changes(venue.calls, before)
    before = venue.calls.copy()
    submitted = await executor.fire(intent.decision_id)
    fire_calls = changes(venue.calls, before)
    confirmed = executor.confirm_fill(intent.decision_id, FillConfirmation(
        "synthetic-observation-001", "synthetic-trade-001", quantity=4.0,
    ))

    next_intent = OrderIntent("observation-002", spend=5.0, price_cap=0.60)
    await executor.prepare(next_intent)
    venue.now = 31.0
    before = venue.calls.copy()
    try:
        await executor.fire(next_intent.decision_id)
    except PreparationError:
        stale_rejected = True
    else:
        stale_rejected = False
    stale_calls = changes(venue.calls, before)
    before = venue.calls.copy()
    await executor.warm_cache(force=True)
    await executor.prepare(next_intent)
    refresh = changes(venue.calls, before)
    refreshed_submission = await executor.fire(next_intent.decision_id)

    return {
        "fixture": "synthetic; operation counts, not latency or fill-performance data",
        "cold_arm_fetches": cold,
        "max_concurrent_cold_fetches": cold_parallel,
        "repeat_warm_fetches": repeat,
        "repeat_preparation": duplicate_preparation,
        "fire_path_calls": fire_calls,
        "delayed_reply_state": submitted.state.value,
        "separate_execution_report_state": confirmed.state.value,
        "synthetic_reported_quantity": confirmed.confirmation.quantity,
        "stale_preparation_rejected": stale_rejected,
        "stale_fire_calls": stale_calls,
        "force_refresh_and_reprepare": refresh,
        "refreshed_submission_state": refreshed_submission.state.value,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(walkthrough()), indent=2))
