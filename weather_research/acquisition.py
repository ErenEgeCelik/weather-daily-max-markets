"""Offline extracts of METAR acquisition; see docs/acquisition-implementation.md.

No transport is supplied. Fetch callables and clocks are explicit dependencies.
The parsing/dedup changes are publication refactors, not historical fixes.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def historical_pool_settings(source: str, workers: int, stations: int) -> dict:
    """Source settings only; this does not open or benchmark a connection pool."""
    if workers < 1 or stations < 1:
        raise ValueError("workers and stations must be positive")
    if source == "noaa":
        pool = max(20, workers * 4)
    elif source == "mgm":
        pool = max(20, workers * stations * 2)
    else:
        raise ValueError("source must be noaa or mgm")
    return {"http2": False, "max_connections": pool,
            "max_keepalive_connections": pool, "keepalive_expiry": 60.0}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def resolve_metar_time(token: str, arrived_at: datetime,
                       max_age: timedelta = timedelta(hours=36)) -> datetime:
    """Resolve DDHHMMZ to the unique nonfuture date within a bounded age window."""
    arrived_at = _utc(arrived_at)
    if not re.fullmatch(r"\d{6}Z", token) or max_age <= timedelta(0):
        raise ValueError("invalid METAR time or age window")
    day, hour, minute = int(token[:2]), int(token[2:4]), int(token[4:6])
    candidates = []
    for delta in (-1, 0, 1):
        month_index = arrived_at.year * 12 + arrived_at.month - 1 + delta
        year, month_zero = divmod(month_index, 12)
        try:
            candidate = datetime(year, month_zero + 1, day, hour, minute, tzinfo=UTC)
        except ValueError:
            continue
        if timedelta(0) <= arrived_at - candidate <= max_age:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise ValueError("METAR time is invalid, future, stale, or ambiguous")
    return candidates[0]


@dataclass(frozen=True)
class Observation:
    station: str
    source: str
    observed_at: datetime
    arrived_at: datetime
    metar_temperature_c: float | None
    raw_metar: str
    mgm_temperature_c: float | None = None
    source_recorded_at: datetime | None = None

    @property
    def observation_age_seconds(self) -> float:
        """Observation-to-arrival age, not fetch time or actionable latency."""
        return (self.arrived_at - self.observed_at).total_seconds()


def parse_metar(raw: str, *, source: str, arrived_at: datetime,
                expected_station: str | None = None) -> Observation:
    if not isinstance(raw, str) or not source:
        raise ValueError("METAR text and source are required")
    normalized = " ".join(raw.strip().rstrip("=").split())
    tokens = normalized.split()
    if tokens and tokens[0] in {"METAR", "SPECI"}:
        tokens = tokens[1:]
    if len(tokens) < 2 or not re.fullmatch(r"[A-Z]{4}", tokens[0]):
        raise ValueError("missing or invalid ICAO station")
    station = tokens[0]
    if expected_station is not None and station != expected_station.upper():
        raise ValueError("station does not match the requested station")
    # COR can appear before or after the report time.
    time_position = 2 if tokens[1] == "COR" else 1
    if len(tokens) <= time_position:
        raise ValueError("missing METAR time")
    observed_at = resolve_metar_time(tokens[time_position], arrived_at)
    main = tokens[:tokens.index("RMK")] if "RMK" in tokens else tokens
    temperature = None
    for token in main[time_position + 1:]:
        match = re.fullmatch(r"(M?\d{2})/(?:M?\d{2}|//)?", token)
        if match:
            value = match.group(1)
            temperature = float(-int(value[1:]) if value.startswith("M") else int(value))
            break
    # Ignore an optional METAR envelope in cross-source identity; keep SPECI/COR.
    identity_raw = normalized.removeprefix("METAR ")
    return Observation(station, source, observed_at, _utc(arrived_at),
                       temperature, identity_raw)


def parse_noaa_text(text: str, *, arrived_at: datetime,
                    expected_station: str | None = None) -> Observation:
    """Read the date header and report from the historical two-line TXT shape."""
    if not isinstance(text, str):
        raise ValueError("NOAA response must be text")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 2:
        raise ValueError("expected a NOAA date header and one METAR line")
    try:
        recorded = datetime.strptime(lines[0], "%Y/%m/%d %H:%M").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("invalid NOAA date header") from error
    observation = parse_metar(lines[1], source="noaa_txt", arrived_at=arrived_at,
                              expected_station=expected_station)
    return Observation(**{**observation.__dict__, "source_recorded_at": recorded})


def parse_mgm_json(payload: Mapping | Sequence, *, arrived_at: datetime,
                   expected_station: str | None = None) -> Observation:
    """Keep the official report temperature separate from MGM's decimal field."""
    if isinstance(payload, (list, tuple)):
        if not payload:
            raise ValueError("empty MGM response")
        row = payload[0]
    else:
        row = payload
    if not isinstance(row, Mapping):
        raise ValueError("MGM response must contain a JSON object")
    observation = parse_metar(row.get("rasatMetar", ""), source="mgm",
                              arrived_at=arrived_at, expected_station=expected_station)
    decimal = row.get("sicaklik")
    try:
        decimal = None if decimal is None or isinstance(decimal, bool) else float(decimal)
    except (TypeError, ValueError):
        decimal = None
    if decimal is not None and (not math.isfinite(decimal) or decimal == -9999):
        decimal = None
    recorded = None
    try:
        recorded = _utc(datetime.fromisoformat(row.get("veriZamani", "").replace("Z", "+00:00")))
    except (AttributeError, TypeError, ValueError):
        pass  # An absent/naive field cannot establish a UTC source timestamp.
    return Observation(**{**observation.__dict__, "mgm_temperature_c": decimal,
                           "source_recorded_at": recorded})


@dataclass(frozen=True)
class ObservationEvent:
    observation: Observation
    status: str  # new, revision, duplicate, late
    first_arrived_at: datetime

    @property
    def updates_current(self) -> bool:
        return self.status in {"new", "revision"}


class ObservationLedger:
    """Finite-replay dedup across sources, preserving same-time report revisions.

    Ingest in arrival order. Late observation times are recorded but do not
    replace current state. Storage is unbounded: construct one ledger per replay.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple, datetime] = {}
        self._latest: dict[str, datetime] = {}
        self._last_arrival: datetime | None = None

    def ingest(self, observation: Observation) -> ObservationEvent:
        arrived = _utc(observation.arrived_at)
        if self._last_arrival is not None and arrived < self._last_arrival:
            raise ValueError("ingest must follow nondecreasing arrival timestamps")
        self._last_arrival = arrived
        key = (observation.station, observation.observed_at, observation.raw_metar)
        if key in self._seen:
            return ObservationEvent(observation, "duplicate", self._seen[key])
        self._seen[key] = arrived
        latest = self._latest.get(observation.station)
        if latest is not None and observation.observed_at < latest:
            status = "late"
        elif latest == observation.observed_at:
            status = "revision"
        else:
            status = "new"
        if status != "late":
            self._latest[observation.station] = observation.observed_at
        return ObservationEvent(observation, status, arrived)


@dataclass(frozen=True)
class Delivery:
    event: ObservationEvent
    released_at: datetime


async def collect_noaa_batch(stations: Sequence[str], *,
                             fetch: Callable[[str], Awaitable[str]],
                             clock: Callable[[], datetime], ledger: ObservationLedger,
                             workers: int = 1) -> list[Delivery]:
    """Historical scheduling: shared fetch dependency, gather barrier, station gate.

    Parsing/dedup happens on completion. Delivery waits for all polls and selects
    at most one changed report per station in task-list order, as in the source.
    Exceptions propagate in this extract; retries/logging belong to transport.
    """
    if workers < 1:
        raise ValueError("workers must be positive")

    async def poll(station: str) -> ObservationEvent:
        text = await fetch(station)
        return ledger.ingest(parse_noaa_text(text, arrived_at=clock(), expected_station=station))

    results = await asyncio.gather(*(poll(station) for station in stations for _ in range(workers)))
    seen = set()
    deliveries = []
    for event in results:
        station = event.observation.station
        if event.updates_current and station not in seen:
            deliveries.append(Delivery(event, _utc(clock())))
            seen.add(station)
    return deliveries


async def collect_mgm_once(station: str, *, fetch: Callable[[str], Awaitable[Mapping | Sequence]],
                           clock: Callable[[], datetime], ledger: ObservationLedger,
                           emit: Callable[[Delivery], Awaitable[None]]) -> ObservationEvent:
    """One worker iteration: emit after this poll, without a cross-worker barrier."""
    payload = await fetch(station)
    event = ledger.ingest(parse_mgm_json(payload, arrived_at=clock(), expected_station=station))
    if event.updates_current:
        await emit(Delivery(event, _utc(clock())))
    return event
