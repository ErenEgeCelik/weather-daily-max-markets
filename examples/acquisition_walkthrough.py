"""Run from any directory: python examples/acquisition_walkthrough.py."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from weather_research.acquisition import (  # noqa: E402
    ObservationLedger, collect_mgm_once, collect_noaa_batch,
    historical_pool_settings, parse_mgm_json, parse_noaa_text,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "acquisition"


def timestamp(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


async def main():
    print("SYNTHETIC: parser and arrival-order demonstration; no network or orders.")
    ledger = ObservationLedger()
    rows = [
        ("noaa_first.txt", "2026-06-01T00:00:00+00:00", parse_noaa_text),
        ("mgm_duplicate.json", "2026-06-01T00:00:01+00:00", parse_mgm_json),
        ("mgm_corrected.json", "2026-06-01T00:01:01+00:00", parse_mgm_json),
        ("noaa_later.txt", "2026-06-01T00:20:02+00:00", parse_noaa_text),
    ]
    for filename, arrival, parser in rows:
        payload = (FIXTURES / filename).read_text(encoding="utf-8")
        if filename.endswith(".json"):
            payload = json.loads(payload)
        obs = parser(payload, arrived_at=timestamp(arrival), expected_station="LTZZ")
        event = ledger.ingest(obs)
        print(json.dumps({"status": event.status, "source": obs.source,
                          "observed_at": obs.observed_at.isoformat(),
                          "arrived_at": obs.arrived_at.isoformat(),
                          "age_seconds": obs.observation_age_seconds,
                          "official_metar_c": obs.metar_temperature_c,
                          "mgm_decimal_c": obs.mgm_temperature_c,
                          "updates_current": event.updates_current}))

    # The same fake transport object services all polls. It owns no sockets;
    # actual reuse of a TCP connection cannot be inferred from this example.
    class FakeTransport:
        def __init__(self):
            self.calls = []

        async def noaa(self, station):
            self.calls.append(("noaa", station))
            await asyncio.sleep(0)
            return (FIXTURES / "noaa_first.txt").read_text(encoding="utf-8")

        async def mgm(self, station):
            self.calls.append(("mgm", station))
            await asyncio.sleep(0)
            return json.loads((FIXTURES / "mgm_duplicate.json").read_text(encoding="utf-8"))

    fake = FakeTransport()
    clock = lambda: timestamp("2026-06-01T00:00:01+00:00")
    batch = await collect_noaa_batch(["LTZZ"], fetch=fake.noaa, clock=clock,
                                     ledger=ObservationLedger(), workers=3)
    emitted = []

    async def emit(delivery):
        emitted.append(delivery)

    shared_ledger = ObservationLedger()
    await asyncio.gather(*(collect_mgm_once("LTZZ", fetch=fake.mgm, clock=clock,
                          ledger=shared_ledger, emit=emit) for _ in range(3)))
    print(json.dumps({"fake_transport_calls": fake.calls,
                      "noaa_batch_deliveries": len(batch), "mgm_worker_deliveries": len(emitted),
                      "historical_noaa_pool": historical_pool_settings("noaa", 3, 1)}))


if __name__ == "__main__":
    asyncio.run(main())
