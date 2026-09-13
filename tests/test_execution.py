"""Boundaries that make the offline execution example auditable."""

import asyncio
from dataclasses import replace
import math
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from execution_walkthrough import SyntheticVenue
from weather_research.execution import (
    FillConfirmation, Lifecycle, OrderIntent, PreparationError,
    PreparedExecutor, SubmissionReply,
)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.venue = SyntheticVenue()
        self.executor = PreparedExecutor("synthetic-token", self.venue, self.venue,
                                         self.venue, ttl_s=30, clock=lambda: self.venue.now)
        self.intent = OrderIntent("observation-001", 10.0, 0.60)

    async def arm(self):
        await self.executor.warm_cache()
        return await self.executor.prepare(self.intent)

    async def test_parallel_cold_fetches_and_repeat_balance_refresh(self):
        await self.executor.warm_cache()
        self.assertEqual(self.venue.max_parallel, 5)
        await self.executor.warm_cache()
        self.assertEqual(self.venue.calls["balance"], 2)
        for name in ("tick_size", "neg_risk", "fee_bps", "version"):
            self.assertEqual(self.venue.calls[name], 1)

    async def test_force_refresh_and_full_invalidation(self):
        await self.arm()
        await self.executor.warm_cache(force=True)
        self.assertEqual(self.venue.calls["fee_bps"], 2)
        self.assertEqual(self.venue.calls["tick_size"], 1)
        with self.assertRaises(PreparationError):
            await self.executor.fire(self.intent.decision_id)
        await self.executor.prepare(self.intent)
        self.executor.invalidate()
        await self.executor.warm_cache()
        self.assertEqual(self.venue.calls["tick_size"], 2)
        self.assertEqual(self.venue.calls["version"], 2)

    async def test_fire_has_no_fetch_or_sign_and_delayed_is_not_fill(self):
        await self.arm()
        before = self.venue.calls.copy()
        result = await self.executor.fire(self.intent.decision_id)
        self.assertEqual(result.state, Lifecycle.ACKNOWLEDGED)
        self.assertIsNone(result.confirmation)
        self.assertEqual(dict(self.venue.calls - before), {"submit": 1})

    async def test_concurrent_preparation_is_one_sign(self):
        await self.executor.warm_cache()
        await asyncio.gather(self.executor.prepare(self.intent), self.executor.prepare(self.intent))
        self.assertEqual(self.venue.calls["build_and_sign"], 1)
        with self.assertRaises(PreparationError):
            await self.executor.prepare(replace(self.intent, spend=20.0))

    async def test_concurrent_fire_and_later_reuse_submit_once(self):
        await self.arm()
        results = await asyncio.gather(self.executor.fire(self.intent.decision_id),
                                       self.executor.fire(self.intent.decision_id), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, PreparationError) for r in results), 1)
        self.assertEqual(self.venue.calls["submit"], 1)
        await self.executor.warm_cache()
        with self.assertRaises(PreparationError):
            await self.executor.prepare(self.intent)

    async def test_missing_and_expired_preparation_cannot_submit(self):
        with self.assertRaises(PreparationError):
            await self.executor.fire(self.intent.decision_id)
        await self.arm()
        self.venue.now = 30.0  # Expiry boundary itself is excluded.
        with self.assertRaises(PreparationError):
            await self.executor.fire(self.intent.decision_id)
        self.assertEqual(self.venue.calls["submit"], 0)
        await self.executor.warm_cache(force=True)
        await self.executor.prepare(self.intent)
        await self.executor.fire(self.intent.decision_id)
        self.assertEqual(self.venue.calls["submit"], 1)

    async def test_failed_warmup_invalidates_previous_preparation(self):
        await self.arm()

        async def failing_balance():
            raise OSError("fixture failure")

        self.venue.balance = failing_balance
        with self.assertRaises(PreparationError):
            await self.executor.warm_cache()
        with self.assertRaises(PreparationError):
            await self.executor.fire(self.intent.decision_id)
        self.assertEqual(self.venue.calls["submit"], 0)

    async def test_invalid_metadata_and_intents_do_not_sign(self):
        await self.executor.warm_cache()
        for intent in (replace(self.intent, spend=101), replace(self.intent, spend=math.nan),
                       replace(self.intent, price_cap=0.605), replace(self.intent, price_cap=1.0)):
            with self.subTest(intent=intent), self.assertRaises(PreparationError):
                await self.executor.prepare(intent)
        self.assertEqual(self.venue.calls["build_and_sign"], 0)

        async def invalid_balance(): return float("nan")
        self.venue.balance = invalid_balance
        with self.assertRaises(PreparationError):
            await self.executor.warm_cache()

    async def test_refresh_while_signing_cannot_publish_old_preparation(self):
        await self.executor.warm_cache()
        started, release = threading.Event(), threading.Event()
        original_sign = self.venue.build_and_sign

        def paused_sign(intent, snapshot):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test signer was not released")
            return original_sign(intent, snapshot)

        self.venue.build_and_sign = paused_sign
        task = asyncio.create_task(self.executor.prepare(self.intent))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 5))
            await self.executor.warm_cache(force=True)
        finally:
            release.set()
        with self.assertRaises(PreparationError):
            await task
        with self.assertRaises(PreparationError):
            await self.executor.fire(self.intent.decision_id)

    async def test_signing_failure_does_not_create_a_fire_fallback(self):
        await self.executor.warm_cache()

        def failing_sign(intent, snapshot): raise ValueError("fixture signing failure")
        self.venue.build_and_sign = failing_sign
        with self.assertRaises(ValueError):
            await self.executor.prepare(self.intent)
        with self.assertRaises(PreparationError):
            await self.executor.fire(self.intent.decision_id)
        self.assertEqual(self.venue.calls["submit"], 0)

    async def test_timeout_is_unknown_and_not_retried(self):
        await self.arm()

        async def timed_out(payload):
            self.venue.calls["submit"] += 1
            raise TimeoutError("outcome unknown")

        self.venue.submit = timed_out
        result = await self.executor.fire(self.intent.decision_id)
        self.assertEqual(result.state, Lifecycle.UNKNOWN)
        with self.assertRaises(PreparationError):
            await self.executor.fire(self.intent.decision_id)
        self.assertEqual(self.venue.calls["submit"], 1)

    async def test_pending_and_cancelled_submission_do_not_reuse_payload(self):
        await self.arm()
        started = asyncio.Event()

        async def paused_submit(payload):
            self.venue.calls["submit"] += 1
            started.set()
            await asyncio.Event().wait()

        self.venue.submit = paused_submit
        task = asyncio.create_task(self.executor.fire(self.intent.decision_id))
        await started.wait()
        self.assertEqual(self.executor.submission(self.intent.decision_id).state, Lifecycle.PENDING)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.executor.submission(self.intent.decision_id).state, Lifecycle.UNKNOWN)
        with self.assertRaises(PreparationError):
            await self.executor.fire(self.intent.decision_id)

    async def test_reply_classification_never_confirms_a_fill(self):
        for status, success, expected in (
            ("matched", True, Lifecycle.ACKNOWLEDGED),
            ("delayed", True, Lifecycle.ACKNOWLEDGED),
            ("live", True, Lifecycle.ACKNOWLEDGED),
            ("unmatched", True, Lifecycle.REJECTED),
            ("matched", False, Lifecycle.REJECTED),
            ("unrecognized", True, Lifecycle.UNKNOWN),
        ):
            with self.subTest(status=status, success=success):
                executor = PreparedExecutor("synthetic-token", self.venue, self.venue, self.venue)
                async def reply(payload): return SubmissionReply("synthetic-order", status, success)
                self.venue.submit = reply
                await executor.warm_cache()
                await executor.prepare(self.intent)
                result = await executor.fire(self.intent.decision_id)
                self.assertEqual(result.state, expected)
                self.assertIsNone(result.confirmation)

    async def test_confirmation_requires_order_scoped_positive_execution(self):
        await self.arm()
        result = await self.executor.fire(self.intent.decision_id)
        valid = FillConfirmation(result.order_id, "synthetic-trade", 4.0)
        for evidence in (replace(valid, order_id="different-order"),
                         replace(valid, quantity=0), replace(valid, quantity=math.nan),
                         replace(valid, trade_id="")):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.executor.confirm_fill(self.intent.decision_id, evidence)
        confirmed = self.executor.confirm_fill(self.intent.decision_id, valid)
        self.assertEqual(confirmed.state, Lifecycle.CONFIRMED)
        self.assertEqual(confirmed.confirmation.quantity, 4.0)
        self.assertEqual(self.executor.confirm_fill(self.intent.decision_id, valid), confirmed)


if __name__ == "__main__":
    unittest.main()
