"""Offline ARM/FIRE extraction from the historical weather CLOB wrappers.

Providers, signer and transport are injected. This module contains no SDK,
credentials, URLs or network adapter. See docs/execution-implementation.md for
the source mapping and the additional publication guards.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Protocol


class PreparationError(ValueError):
    """Preparation is absent, stale, invalid or already submitted."""


@dataclass(frozen=True)
class OrderIntent:
    decision_id: str
    spend: float
    price_cap: float


@dataclass(frozen=True)
class CacheSnapshot:
    token: str
    tick_size: float
    neg_risk: bool
    fee_bps: float
    version: str
    balance: float
    generation: int
    expires_at: float


class PreparationProvider(Protocol):
    async def tick_size(self, token: str) -> float: ...
    async def neg_risk(self, token: str) -> bool: ...
    async def fee_bps(self, token: str) -> float: ...
    async def version(self) -> str: ...
    async def balance(self) -> float: ...


class Signer(Protocol):
    def build_and_sign(self, intent: OrderIntent, snapshot: CacheSnapshot) -> bytes: ...


@dataclass(frozen=True)
class SubmissionReply:
    order_id: str | None
    status: str
    success: bool = True


class Transport(Protocol):
    async def submit(self, payload: bytes) -> SubmissionReply: ...


class Lifecycle(str, Enum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FillConfirmation:
    """An order-scoped execution report, supplied separately from POST status."""

    order_id: str
    trade_id: str
    quantity: float


@dataclass(frozen=True)
class Submission:
    decision_id: str
    state: Lifecycle
    order_id: str | None = None
    raw_status: str | None = None
    confirmation: FillConfirmation | None = None
    error: str | None = None


@dataclass(frozen=True)
class _Prepared:
    intent: OrderIntent
    generation: int
    expires_at: float
    payload: bytes = field(repr=False)


class PreparedExecutor:
    """One token's preparation cache and one-shot submission lifecycle.

    ARM: warm_cache(), then prepare(). FIRE: fire() submits only saved bytes.
    A cached balance is a preflight input, not a portfolio cash reservation.
    """

    def __init__(
        self, token: str, provider: PreparationProvider, signer: Signer,
        transport: Transport, *, ttl_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not token or not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("token and positive finite cache lifetime required")
        self.token = token
        self.provider, self.signer, self.transport = provider, signer, transport
        self.ttl_s, self.clock = ttl_s, clock
        self._metadata: dict[str, object] = {}
        self._generation = 0
        self._snapshot: CacheSnapshot | None = None
        self._prepared: dict[str, _Prepared] = {}
        self._submissions: dict[str, Submission] = {}
        self._warm_lock = asyncio.Lock()
        self._prepare_lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Drop all metadata after a configuration change or failed warm-up."""
        self._generation += 1
        self._metadata.clear()
        self._snapshot = None

    async def warm_cache(self, *, force: bool = False) -> CacheSnapshot:
        """Fetch missing metadata concurrently; always refresh the balance.

        force also refreshes fees, matching the historical v2 ARM path.
        invalidate() is the explicit path for changed tick/risk/version data.
        Every warm-up invalidates previously prepared payloads for this token.
        """
        async with self._warm_lock:
            self._generation += 1
            generation = self._generation
            self._snapshot = None
            metadata = dict(self._metadata)
            if force:
                metadata.pop("fee_bps", None)
            fetchers = {
                "tick_size": lambda: self.provider.tick_size(self.token),
                "neg_risk": lambda: self.provider.neg_risk(self.token),
                "fee_bps": lambda: self.provider.fee_bps(self.token),
                "version": self.provider.version,
            }
            names = [name for name in fetchers if name not in metadata] + ["balance"]
            calls = [fetchers[name]() for name in names if name != "balance"]
            calls.append(self.provider.balance())
            # Wait for every fetch to finish before returning or reporting failure.
            values = await asyncio.gather(*calls, return_exceptions=True)
            if any(isinstance(value, BaseException) for value in values):
                self.invalidate()
                raise PreparationError("cache warm-up failed")
            if generation != self._generation:
                raise PreparationError("cache invalidated during warm-up")
            metadata.update(zip(names, values))
            tick, fee, balance = (metadata[name] for name in ("tick_size", "fee_bps", "balance"))
            if (
                not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                        and math.isfinite(v) for v in (tick, fee, balance))
                or not 0 < tick < 1 or fee < 0 or balance < 0
                or not isinstance(metadata["neg_risk"], bool)
                or not isinstance(metadata["version"], str) or not metadata["version"]
            ):
                self.invalidate()
                raise PreparationError("invalid preparation metadata")
            self._snapshot = CacheSnapshot(
                token=self.token, tick_size=tick, neg_risk=metadata["neg_risk"],
                fee_bps=fee, version=metadata["version"], balance=balance,
                generation=generation, expires_at=self.clock() + self.ttl_s,
            )
            self._metadata = {key: metadata[key] for key in fetchers}
            return self._snapshot

    def _current(self) -> CacheSnapshot:
        snapshot = self._snapshot
        if snapshot is None or self.clock() >= snapshot.expires_at:
            raise PreparationError("cache missing or stale; warm before preparation")
        return snapshot

    async def prepare(self, intent: OrderIntent) -> str:
        """Build/sign outside FIRE; repeat preparation of the same intent is a hit."""
        async with self._prepare_lock:
            snapshot = self._current()
            if intent.decision_id in self._submissions:
                raise PreparationError("decision already submitted; reconcile its outcome")
            if (not intent.decision_id or not math.isfinite(intent.spend)
                    or not 0 < intent.spend <= snapshot.balance
                    or not math.isfinite(intent.price_cap) or not 0 < intent.price_cap < 1
                    or not math.isclose(intent.price_cap / snapshot.tick_size,
                                        round(intent.price_cap / snapshot.tick_size), abs_tol=1e-9)):
                raise PreparationError("invalid intent, insufficient cached balance or off-tick cap")
            previous = self._prepared.get(intent.decision_id)
            if previous is not None and previous.intent != intent:
                raise PreparationError("decision ID already belongs to a different intent")
            if previous is not None and previous.generation == snapshot.generation:
                return intent.decision_id
            # Mirrors run_in_executor in the historical trigger's ARM phase.
            payload = await asyncio.to_thread(self.signer.build_and_sign, intent, snapshot)
            if not isinstance(payload, bytes) or not payload:
                raise PreparationError("signer returned no prepared payload")
            if self._current().generation != snapshot.generation:
                raise PreparationError("preparation changed while signing; prepare again")
            self._prepared[intent.decision_id] = _Prepared(
                intent, snapshot.generation, snapshot.expires_at, payload,
            )
            return intent.decision_id

    async def fire(self, decision_id: str) -> Submission:
        """Submit once; no metadata calls, signing, refresh, or automatic retry."""
        if decision_id in self._submissions:
            raise PreparationError("decision already submitted; reconcile its outcome")
        prepared = self._prepared.get(decision_id)
        snapshot = self._current()
        if (prepared is None or prepared.generation != snapshot.generation
                or self.clock() >= prepared.expires_at):
            raise PreparationError("preparation missing or stale; prepare again")
        pending = Submission(decision_id, Lifecycle.PENDING)
        # Consume before the first await: concurrent triggers cannot double-submit.
        self._submissions[decision_id] = pending
        del self._prepared[decision_id]
        try:
            reply = await self.transport.submit(prepared.payload)
        except asyncio.CancelledError:
            self._submissions[decision_id] = replace(pending, state=Lifecycle.UNKNOWN,
                                                       error="submission cancelled; reconcile")
            raise
        except Exception as exc:
            result = replace(pending, state=Lifecycle.UNKNOWN, error=type(exc).__name__)
        else:
            if not isinstance(reply, SubmissionReply):
                result = replace(pending, state=Lifecycle.UNKNOWN, error="invalid reply")
            else:
                state = Lifecycle.UNKNOWN
                if not reply.success or reply.status in {"rejected", "unmatched"}:
                    state = Lifecycle.REJECTED
                elif reply.order_id and reply.status in {"matched", "delayed", "live", "accepted"}:
                    state = Lifecycle.ACKNOWLEDGED
                result = replace(pending, state=state, order_id=reply.order_id,
                                 raw_status=reply.status)
        self._submissions[decision_id] = result
        return result

    def submission(self, decision_id: str) -> Submission:
        return self._submissions[decision_id]

    def confirm_fill(self, decision_id: str, evidence: FillConfirmation) -> Submission:
        """Attach an attributable execution report, never infer it from POST status.

        CONFIRMED means reported executed quantity, possibly a partial fill; it
        does not mean full order completion or blockchain settlement finality.
        """
        current = self._submissions[decision_id]
        if current.confirmation == evidence:
            return current
        if (current.state != Lifecycle.ACKNOWLEDGED
                or not current.order_id or evidence.order_id != current.order_id
                or not evidence.trade_id or not math.isfinite(evidence.quantity)
                or evidence.quantity <= 0):
            raise ValueError("a positive order-scoped execution report is required")
        result = replace(current, state=Lifecycle.CONFIRMED, confirmation=evidence)
        self._submissions[decision_id] = result
        return result
