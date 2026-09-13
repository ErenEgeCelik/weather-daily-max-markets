"""Offline belief-to-action extraction; a one-step comparator, not a Bellman solver.

Source mapping and publication fixes: docs/decision-implementation.md.
Prices are dollars/share, quantities shares, PnL dollars, variance dollars squared.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping, Sequence


def validate_distribution(probabilities: Mapping[int, float]) -> None:
    if (not probabilities or any(not isinstance(k, int) for k in probabilities)
            or any(not math.isfinite(p) or p < 0 for p in probabilities.values())
            or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-9)):
        raise ValueError("an integer-valued, finite, nonnegative normalized distribution is required")


@dataclass(frozen=True)
class Bucket:
    """Inclusive integer settlement bounds; None denotes an open tail."""

    label: str
    lower: int | None
    upper: int | None

    def __post_init__(self):
        if (not self.label or (self.lower is None and self.upper is None)
                or any(v is not None and not isinstance(v, int) for v in (self.lower, self.upper))
                or (self.lower is not None and self.upper is not None and self.lower > self.upper)):
            raise ValueError("invalid settlement bucket")

    def contains(self, value: int) -> bool:
        return ((self.lower is None or value >= self.lower)
                and (self.upper is None or value <= self.upper))


def payoff(bucket: Bucket, side: str, outcome: int) -> float:
    if side not in {"YES", "NO"}:
        raise ValueError("side must be YES or NO")
    yes = float(bucket.contains(outcome))
    return yes if side == "YES" else 1.0 - yes


def contract_probability(probabilities: Mapping[int, float], bucket: Bucket, side="YES") -> float:
    validate_distribution(probabilities)
    return sum(p * payoff(bucket, side, outcome) for outcome, p in probabilities.items())


@dataclass(frozen=True)
class Position:
    bucket: Bucket
    side: str
    entry_price: float
    qty: float

    def __post_init__(self):
        if (self.side not in {"YES", "NO"} or not math.isfinite(self.entry_price)
                or self.entry_price < 0 or not math.isfinite(self.qty) or self.qty <= 0):
            raise ValueError("invalid position; entry price includes any paid fees")

    def pnl(self, outcome: int) -> float:
        return (payoff(self.bucket, self.side, outcome) - self.entry_price) * self.qty


@dataclass(frozen=True)
class PortfolioValue:
    mean: float
    variance: float
    utility: float


def multi_position_utility(probabilities: Mapping[int, float], positions: Sequence[Position],
                           lam: float = 1.0, realized: float = 0.0) -> PortfolioValue:
    """Enumerate shared settlement Y: cross-position covariance is included."""
    validate_distribution(probabilities)
    if not math.isfinite(lam) or lam < 0 or not math.isfinite(realized):
        raise ValueError("risk penalty must be finite and nonnegative")
    pnls = {y: realized + sum(position.pnl(y) for position in positions) for y in probabilities}
    mean = sum(probabilities[y] * pnl for y, pnl in pnls.items())
    variance = sum(probabilities[y] * (pnl - mean) ** 2 for y, pnl in pnls.items())
    return PortfolioValue(mean, variance, mean - lam * variance)


@dataclass(frozen=True)
class BucketBook:
    bucket: Bucket
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    yes_ask_depth: float = 0.0
    no_ask_depth: float = 0.0
    yes_bid_depth: float = 0.0
    no_bid_depth: float = 0.0

    def __post_init__(self):
        for side in ("yes", "no"):
            bid, ask = getattr(self, side + "_bid"), getattr(self, side + "_ask")
            if any(v is not None and (not math.isfinite(v) or not 0 <= v <= 1) for v in (bid, ask)):
                raise ValueError("book prices must be in [0, 1]")
            if bid is not None and ask is not None and bid > ask:
                raise ValueError("crossed snapshot cannot be scored")
            for level in ("bid", "ask"):
                depth = getattr(self, side + "_" + level + "_depth")
                if not math.isfinite(depth) or depth < 0:
                    raise ValueError("book depth must be finite and nonnegative")

    def quote(self, side: str) -> tuple[float | None, float | None, float, float]:
        prefix = side.lower()
        return (getattr(self, prefix + "_bid"), getattr(self, prefix + "_ask"),
                getattr(self, prefix + "_bid_depth"), getattr(self, prefix + "_ask_depth"))


@dataclass(frozen=True)
class MarketState:
    P_Y: Mapping[int, float]
    books: tuple[BucketBook, ...] = ()
    positions: tuple[Position, ...] = ()
    fee_rate: float = 0.0
    cash: float = math.inf
    M_floor: int | None = None
    seconds_to_next_event: float = 60.0

    def __post_init__(self):
        validate_distribution(self.P_Y)
        if (not math.isfinite(self.fee_rate) or not 0 <= self.fee_rate < 1
                or math.isnan(self.cash) or self.cash < 0
                or not math.isfinite(self.seconds_to_next_event) or self.seconds_to_next_event < 0):
            raise ValueError("invalid economic inputs")
        if self.M_floor is not None and any(y < self.M_floor and p > 1e-12 for y, p in self.P_Y.items()):
            raise ValueError("daily maximum belief assigns mass below the confirmed floor")
        if len({book.bucket for book in self.books}) != len(self.books):
            raise ValueError("duplicate bucket books")


@dataclass(frozen=True)
class Action:
    kind: str
    bucket: Bucket | None = None
    side: str | None = None
    price: float = 0.0
    qty: float = 0.0


def apply_action(state: MarketState, action: Action) -> tuple[tuple[Position, ...], float]:
    """Return remaining positions and newly realized PnL; never mutate the input.

    BUY adds paid fees to basis. SELL reduces matching lots FIFO and retains
    their settlement bounds. The caller must enforce displayed depth and cash.
    """
    if action.kind == "HOLD":
        return state.positions, 0.0
    if (action.kind not in {"BUY", "SELL"} or action.bucket is None
            or action.side not in {"YES", "NO"} or not math.isfinite(action.qty)
            or action.qty <= 0 or not math.isfinite(action.price) or not 0 < action.price <= 1):
        raise ValueError("invalid action")
    if action.kind == "BUY":
        position = Position(action.bucket, action.side, action.price * (1 + state.fee_rate), action.qty)
        return state.positions + (position,), 0.0
    remaining, realized, positions = action.qty, 0.0, []
    for position in state.positions:
        if position.bucket == action.bucket and position.side == action.side and remaining > 0:
            sold = min(position.qty, remaining)
            realized += (action.price * (1 - state.fee_rate) - position.entry_price) * sold
            remaining -= sold
            if sold < position.qty:
                positions.append(replace(position, qty=position.qty - sold))
        else:
            positions.append(position)
    if remaining > 1e-9:
        raise ValueError("cannot sell more shares than held")
    return tuple(positions), realized


def reaction_mean(current_contract_price: float, target_contract_price: float,
                  gamma: float, seconds: float) -> float:
    """Source exponential reaction prior; both prices refer to the SAME contract."""
    if (any(not math.isfinite(v) for v in (current_contract_price, target_contract_price, gamma, seconds))
            or not 0 <= current_contract_price <= 1 or not 0 <= target_contract_price <= 1
            or gamma < 0 or seconds < 0):
        raise ValueError("invalid reaction inputs")
    decay = math.exp(-gamma * seconds)
    return current_contract_price * decay + target_contract_price * (1 - decay)


@dataclass(frozen=True)
class ActionScore:
    action: Action
    value: PortfolioValue
    improvement: float
    edge_per_share: float
    gate_horizon: str
    eligible: bool


@dataclass(frozen=True)
class Decision:
    action: Action
    outcome: str  # candidate / review / hold; never an instruction to send an order
    selected: ActionScore
    confidence: float
    alternatives: tuple[ActionScore, ...]


class OneStepDecisionEngine:
    """Enumerate BUY/SELL/HOLD and compare terminal portfolio utility.

    Optional reaction pricing affects the BUY edge screen only, preserving the
    historical separation from terminal-utility ranking. No future policy is solved.
    """

    def __init__(self, *, lam=1.0, min_edge=0.03, confidence_threshold=0.70,
                 default_qty=50.0, max_position_per_contract=500.0,
                 min_bid_price=0.03, max_spread=0.05, min_ask_price=0.04,
                 liquidity_blend_alpha=0.6, reaction_gamma: float | None = None):
        values = (lam, min_edge, confidence_threshold, default_qty, max_position_per_contract,
                  min_bid_price, max_spread, min_ask_price, liquidity_blend_alpha)
        if (any(not math.isfinite(v) or v < 0 for v in values) or default_qty <= 0
                or max_position_per_contract <= 0 or confidence_threshold > 1
                or liquidity_blend_alpha > 1
                or (reaction_gamma is not None and (not math.isfinite(reaction_gamma) or reaction_gamma < 0))):
            raise ValueError("invalid decision parameters")
        self.lam, self.min_edge, self.confidence_threshold = lam, min_edge, confidence_threshold
        self.default_qty, self.max_position = default_qty, max_position_per_contract
        self.min_bid, self.max_spread, self.min_ask = min_bid_price, max_spread, min_ask_price
        self.alpha, self.gamma = liquidity_blend_alpha, reaction_gamma

    def candidates(self, state: MarketState) -> tuple[Action, ...]:
        actions = [Action("HOLD")]
        for book in state.books:
            for side in ("YES", "NO"):
                bid, ask, bid_depth, ask_depth = book.quote(side)
                exposure = sum(p.qty for p in state.positions if p.bucket == book.bucket and p.side == side)
                if (bid is not None and ask is not None and ask > 0 and ask >= self.min_ask
                        and bid >= self.min_bid and ask - bid <= self.max_spread + 1e-12):
                    quantity = min(self.default_qty, self.max_position - exposure, ask_depth,
                                   state.cash / (ask * (1 + state.fee_rate)))
                    if quantity > 0:
                        actions.append(Action("BUY", book.bucket, side, ask, quantity))
                if bid is not None and bid > 0 and bid_depth > 0 and exposure > 0:
                    actions.append(Action("SELL", book.bucket, side, bid, min(exposure, bid_depth)))
        return tuple(actions)

    def choose(self, state: MarketState) -> Decision:
        baseline = multi_position_utility(state.P_Y, state.positions, self.lam)
        scores = []
        books = {book.bucket: book for book in state.books}
        for action in self.candidates(state):
            positions, realized = apply_action(state, action)
            value = multi_position_utility(state.P_Y, positions, self.lam, realized)
            improvement = value.utility - baseline.utility
            edge, horizon, eligible = 0.0, "hold", True
            if action.kind == "BUY":
                fair = contract_probability(state.P_Y, action.bucket, action.side)
                bid = books[action.bucket].quote(action.side)[0]
                cost = action.price * (1 + state.fee_rate)
                immediate = bid * (1 - state.fee_rate) - cost
                edge = self.alpha * (fair - cost) + (1 - self.alpha) * immediate
                horizon = "resolution_gate"
                if self.gamma is not None and state.seconds_to_next_event > 0:
                    event_price = reaction_mean(action.price, fair, self.gamma, state.seconds_to_next_event)
                    event_edge = self.alpha * (event_price * (1 - state.fee_rate) - cost) + (1 - self.alpha) * immediate
                    if event_edge > edge:
                        edge, horizon = event_edge, "event_gate"
                eligible = edge >= self.min_edge
            elif action.kind == "SELL":
                edge, horizon = realized / action.qty, "sell"
                # A utility-improving loss reduction must not require positive entry PnL.
            scores.append(ActionScore(action, value, improvement, edge, horizon, eligible))
        best = scores[0]
        for score in scores[1:]:
            if score.eligible and score.improvement > best.improvement + 1e-12:
                best = score
        confidence = (contract_probability(state.P_Y, best.action.bucket, best.action.side)
                      if best.action.kind == "BUY" else max(state.P_Y.values()))
        outcome = "hold" if best.action.kind == "HOLD" else "candidate"
        if best.action.kind == "BUY" and confidence < self.confidence_threshold:
            outcome = "review"
        return Decision(best.action, outcome, best, confidence, tuple(scores))


@dataclass(frozen=True)
class ConditionalPlan:
    metar_value: int
    probability: float
    confirmed_floor: int
    posterior: Mapping[int, float]
    decision: Decision


def conditional_playbook(state: MarketState, Q_next: Mapping[int, float],
                         D_after: Mapping[int, Mapping[int, float]], engine: OneStepDecisionEngine,
                         *, top_k: int = 3, q_min: float = 0.05) -> tuple[ConditionalPlan, ...]:
    """Score top-Q observation branches using supplied conditional distributions.

    Current books are intentionally held fixed: these are contingent scenarios,
    not forecasts of executable post-event prices. Missing D is never replaced
    with silently truncated P. Original Q weights are retained after filtering.
    """
    validate_distribution(Q_next)
    if top_k < 1 or not 0 <= q_min <= 1:
        raise ValueError("invalid branch selection")
    plans = []
    for m, q in sorted(Q_next.items(), key=lambda item: (-item[1], item[0]))[:top_k]:
        if q < q_min or q == 0:
            continue
        if m not in D_after:
            raise ValueError("conditional distribution missing for selected observation")
        posterior = dict(D_after[m])
        floor = m if state.M_floor is None else max(state.M_floor, m)
        hypothetical = replace(state, P_Y=posterior, M_floor=floor,
                               seconds_to_next_event=state.seconds_to_next_event * 2)
        plans.append(ConditionalPlan(m, q, floor, posterior, engine.choose(hypothetical)))
    return tuple(plans)


def total_probability_residual(P_now: Mapping[int, float], Q_next: Mapping[int, float],
                               D_after: Mapping[int, Mapping[int, float]]) -> float:
    """L1 residual of P_now versus sum_m Q(m) D(Y|m); this is not a price edge."""
    validate_distribution(P_now)
    validate_distribution(Q_next)
    mixture: dict[int, float] = {}
    for m, q in Q_next.items():
        if q == 0:
            continue
        if m not in D_after:
            raise ValueError("cannot form mixture with a missing positive-probability branch")
        validate_distribution(D_after[m])
        for y, probability in D_after[m].items():
            mixture[y] = mixture.get(y, 0.0) + q * probability
    return sum(abs(P_now.get(y, 0.0) - mixture.get(y, 0.0)) for y in set(P_now) | set(mixture))


def exit_values(position: Position, P_Y: Mapping[int, float], bid: float | None,
                *, fee_rate=0.0, event_contract_price: float | None = None,
                stop_loss_per_share: float | None = None) -> dict:
    """Compare now/resolution/event per-share PnL in the source ExitEvaluator form."""
    if (not 0 <= fee_rate < 1 or not math.isfinite(fee_rate)
            or (stop_loss_per_share is not None and (not math.isfinite(stop_loss_per_share) or stop_loss_per_share < 0))):
        raise ValueError("invalid exit parameters")
    if any(p is not None and (not math.isfinite(p) or not 0 <= p <= 1) for p in (bid, event_contract_price)):
        raise ValueError("invalid exit price")
    values = {"resolution": contract_probability(P_Y, position.bucket, position.side) - position.entry_price}
    if event_contract_price is not None:
        values["event"] = event_contract_price * (1 - fee_rate) - position.entry_price
    if bid is not None and bid > 0:
        values["now"] = bid * (1 - fee_rate) - position.entry_price
    horizon = max(values, key=values.get)
    forced = ("now" in values and stop_loss_per_share is not None
              and -values["now"] > stop_loss_per_share)
    return {"pnl_per_share": values, "horizon": "now" if forced else horizon,
            "should_exit": forced or horizon == "now", "stop_loss": forced}
