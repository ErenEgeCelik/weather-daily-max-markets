"""Offline R1/R2/R3 weather trigger preparation, adapted from the sidecar rules.

These are candidate conditions, not predictions of fills or network order code.
All temperatures are already integer observations in the contract's settlement unit.
"""

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from weather_research.decisions import Bucket, BucketBook


def known_yes_payoff(bucket: Bucket, confirmed_max: int) -> float | None:
    """Monotonic maximum facts; cooling/model confidence is not a logical lock."""
    if bucket.upper is not None and confirmed_max > bucket.upper:
        return 0.0
    if bucket.upper is None and bucket.lower is not None and confirmed_max >= bucket.lower:
        return 1.0
    return None


@dataclass(frozen=True)
class RuleConfig:
    r3_min_ask: float = 0.005
    r3_max_ask: float = 0.65
    r3_min_below_ask_sum: float = 0.30
    r3_max_above_ask: float = 0.15
    r3_max_spread: float = 0.25
    r3_max_cascade: int = 2
    r3_spike_threshold: float = 0.85
    r3_price_cap: float = 0.85
    min_depth_notional: float = 0.05

    def __post_init__(self):
        values = (self.r3_min_ask, self.r3_max_ask, self.r3_min_below_ask_sum,
                  self.r3_max_above_ask, self.r3_max_spread, self.r3_spike_threshold,
                  self.r3_price_cap, self.min_depth_notional)
        if (any(not math.isfinite(v) or v < 0 for v in values)
                or not self.r3_min_ask < self.r3_max_ask <= 1
                or self.r3_spike_threshold > 1 or self.r3_price_cap > 1
                or self.r3_max_cascade < 1):
            raise ValueError("invalid rule configuration")


@dataclass(frozen=True)
class RuleCandidate:
    rule: str
    bucket: Bucket
    side: str
    metar_min: int
    metar_max: int | None
    reference_ask: float | None
    price_cap: float | None = None
    watch_buckets: tuple[Bucket, ...] = ()
    spike_threshold: float | None = None

    def observation_matches(self, observed_integer: int) -> bool:
        return (observed_integer >= self.metar_min
                and (self.metar_max is None or observed_integer <= self.metar_max))


def build_triggers(books: Sequence[BucketBook], confirmed_max: int | None,
                   config: RuleConfig = RuleConfig()) -> tuple[RuleCandidate, ...]:
    """Prepare sticky R1/R2 conditions and book-filtered R3 candidates.

    R3 depth uses best-ask notional in this compact interface. The source summed
    five ask levels. No available depth at a future trigger time is assumed.
    """
    ordered = sorted(books, key=lambda book: -math.inf if book.bucket.lower is None else book.bucket.lower)
    for left, right in zip(ordered, ordered[1:]):
        if left.bucket.upper is None or right.bucket.lower is None or left.bucket.upper >= right.bucket.lower:
            raise ValueError("rules require distinct non-overlapping settlement buckets")
    out = []
    for index, book in enumerate(ordered):
        bucket = book.bucket
        closed_middle = bucket.lower is not None and bucket.upper is not None
        # Historical R1 emits closed middle buckets; the lower tail is not included.
        if closed_middle and (confirmed_max is None or confirmed_max <= bucket.upper):
            out.append(RuleCandidate("R1", bucket, "NO", bucket.upper + 1, None, book.no_ask))
        if bucket.upper is None:
            out.append(RuleCandidate("R2", bucket, "YES", bucket.lower, None, book.yes_ask))
        if not closed_middle or book.yes_ask is None or book.yes_bid is None:
            continue
        if (not config.r3_min_ask <= book.yes_ask < config.r3_max_ask
                or book.yes_ask - book.yes_bid > config.r3_max_spread
                or book.yes_ask * book.yes_ask_depth < config.min_depth_notional):
            continue
        if confirmed_max is not None and not confirmed_max < bucket.lower <= confirmed_max + config.r3_max_cascade:
            continue
        below_sum = sum(previous.yes_ask or 0.0 for previous in ordered[:index])
        above_ask = ordered[index + 1].yes_ask if index + 1 < len(ordered) else None
        if (below_sum < config.r3_min_below_ask_sum or above_ask is None
                or above_ask > config.r3_max_above_ask):
            continue
        watches = ()
        if confirmed_max is not None:
            candidates = tuple(previous.bucket for previous in ordered[:index]
                               if previous.bucket.lower is not None and previous.bucket.upper is not None
                               and previous.bucket.upper >= confirmed_max)
            # Publication guard: a missing intermediate contract cannot silently
            # shorten the AND condition to an easier, incomplete watch list.
            covered = {value for candidate in candidates
                       for value in range(candidate.lower, candidate.upper + 1)}
            if set(range(confirmed_max, bucket.lower)).issubset(covered):
                watches = candidates
        out.append(RuleCandidate("R3", bucket, "YES", bucket.lower, bucket.upper,
                                 book.yes_ask, config.r3_price_cap, watches,
                                 config.r3_spike_threshold if watches else None))
    return tuple(out)


def cascade_matches(candidate: RuleCandidate, no_bids: Mapping[Bucket, float]) -> bool:
    """Inspect the all-lower-buckets threshold; freshness/delta gates live elsewhere."""
    if not candidate.watch_buckets or candidate.spike_threshold is None:
        return False
    return all(bucket in no_bids and math.isfinite(no_bids[bucket])
               and candidate.spike_threshold <= no_bids[bucket] <= 1
               for bucket in candidate.watch_buckets)
