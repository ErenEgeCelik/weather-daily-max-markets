import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from weather_research.acquisition import (
    ObservationLedger, collect_mgm_once, collect_noaa_batch,
    historical_pool_settings, parse_metar, parse_mgm_json, parse_noaa_text,
    resolve_metar_time,
)

UTC = timezone.utc
ARRIVAL = datetime(2026, 6, 1, 0, 5, tzinfo=UTC)
RAW = "LTZZ 312350Z 00000KT CAVOK 19/12 Q1015"


class ParsingTests(unittest.TestCase):
    def test_month_and_year_boundaries(self):
        self.assertEqual(resolve_metar_time("312350Z", ARRIVAL),
                         datetime(2026, 5, 31, 23, 50, tzinfo=UTC))
        arrival = datetime(2027, 1, 1, 0, 5, tzinfo=UTC)
        self.assertEqual(resolve_metar_time("312350Z", arrival).year, 2026)
        self.assertEqual(resolve_metar_time("282350Z", datetime(2026, 3, 1, tzinfo=UTC)).month, 2)

    def test_invalid_future_stale_and_ambiguous_times(self):
        for token in ("322350Z", "312460Z", "010600Z", "292350Z", "350Z"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                resolve_metar_time(token, ARRIVAL)
        with self.assertRaises(ValueError):
            resolve_metar_time("010000Z", ARRIVAL, max_age=timedelta(days=40))
        with self.assertRaises(ValueError):
            resolve_metar_time("010000Z", ARRIVAL.replace(tzinfo=None))

    def test_negative_temperature_and_missing_dewpoint(self):
        for group, expected in (("M02/M07", -2), ("M00/M01", 0), ("10///", 10), ("10/", 10)):
            with self.subTest(group=group):
                obs = parse_metar(RAW.replace("19/12", group), source="test", arrived_at=ARRIVAL)
                self.assertEqual(obs.metar_temperature_c, expected)

    def test_missing_or_malformed_temperature_cannot_match_remarks(self):
        for group in ("/////", "X19/12Y", "19/123", "M2/M07", ""):
            with self.subTest(group=group):
                obs = parse_metar(RAW.replace("19/12", group) + " RMK 22/12",
                                  source="test", arrived_at=ARRIVAL)
                self.assertIsNone(obs.metar_temperature_c)

    def test_station_validation_and_correction_position(self):
        for raw in ("", "<html>service unavailable</html>", "LTZZ COR"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_metar(raw, source="test", arrived_at=ARRIVAL)
        with self.assertRaises(ValueError):
            parse_metar(RAW, source="test", arrived_at=ARRIVAL, expected_station="KZZZ")
        obs = parse_metar(RAW.replace("LTZZ", "METAR LTZZ COR"), source="test", arrived_at=ARRIVAL)
        self.assertEqual(obs.station, "LTZZ")

    def test_noaa_header_is_not_arrival_or_report_time(self):
        obs = parse_noaa_text("2026/06/01 00:01\n" + RAW, arrived_at=ARRIVAL)
        self.assertEqual(obs.observation_age_seconds, 900)
        self.assertEqual(obs.source_recorded_at.minute, 1)
        self.assertEqual(obs.arrived_at.minute, 5)
        self.assertEqual(obs.observed_at.day, 31)
        for text in (RAW, "bad date\n" + RAW, "2026/06/01 00:01\n" + RAW + "\n" + RAW):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_noaa_text(text, arrived_at=ARRIVAL)

    def test_mgm_decimal_kept_separate_and_sentinel_rejected(self):
        row = {"rasatMetar": RAW, "sicaklik": 19.4, "veriZamani": "2026-06-01T00:01:00Z"}
        obs = parse_mgm_json([row], arrived_at=ARRIVAL)
        self.assertEqual((obs.metar_temperature_c, obs.mgm_temperature_c), (19, 19.4))
        self.assertEqual(obs.source_recorded_at.hour, 0)
        for invalid in (None, -9999, "NaN", float("inf"), True, "bad"):
            with self.subTest(invalid=invalid):
                self.assertIsNone(parse_mgm_json({**row, "sicaklik": invalid},
                                                 arrived_at=ARRIVAL).mgm_temperature_c)
        naive = parse_mgm_json({**row, "veriZamani": "2026-06-01T00:01:00"}, arrived_at=ARRIVAL)
        self.assertIsNone(naive.source_recorded_at)
        for invalid in ([], [1], "garbage", {"rasatMetar": None}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_mgm_json(invalid, arrived_at=ARRIVAL)


class DedupTests(unittest.TestCase):
    def observation(self, raw=RAW, source="noaa_txt", seconds=0):
        return parse_metar(raw, source=source, arrived_at=ARRIVAL + timedelta(seconds=seconds))

    def test_cross_source_duplicate_and_same_time_correction(self):
        ledger = ObservationLedger()
        first = ledger.ingest(self.observation())
        duplicate = ledger.ingest(self.observation("METAR " + RAW + "=", "mgm", 1))
        corrected = ledger.ingest(self.observation(RAW.replace("19/12", "20/12") + " COR", "mgm", 2))
        self.assertEqual([first.status, duplicate.status, corrected.status], ["new", "duplicate", "revision"])
        self.assertEqual(duplicate.first_arrived_at, first.observation.arrived_at)
        self.assertFalse(duplicate.updates_current)
        self.assertTrue(corrected.updates_current)
        self.assertEqual(ledger.ingest(self.observation(seconds=3)).status, "duplicate")

    def test_old_reports_do_not_replace_current_and_arrival_order_is_explicit(self):
        ledger = ObservationLedger()
        ledger.ingest(self.observation())
        old = ledger.ingest(self.observation(RAW.replace("312350Z", "312320Z"), seconds=1))
        self.assertEqual(old.status, "late")
        self.assertFalse(old.updates_current)
        with self.assertRaises(ValueError):
            ledger.ingest(self.observation(seconds=0))
        self.assertEqual(ledger.ingest(self.observation(RAW.replace("312350Z", "010000Z"), seconds=2)).status,
                         "new")

    def test_equal_arrivals_different_stations_and_next_month_identity(self):
        ledger = ObservationLedger()
        self.assertEqual(ledger.ingest(self.observation()).status, "new")
        self.assertEqual(ledger.ingest(self.observation(RAW.replace("LTZZ", "KZZZ"))).status, "new")
        next_month = parse_metar(RAW.replace("312350Z", "302350Z"), source="test",
                                 arrived_at=datetime(2026, 7, 1, tzinfo=UTC))
        self.assertEqual(ledger.ingest(next_month).status, "new")


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_noaa_batch_has_barrier_even_when_one_source_is_ready(self):
        started, fast_done, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def fetch(station):
            calls.append(station)
            if station == "KZZZ":
                started.set()
                await release.wait()
            else:
                fast_done.set()
            return "2026/05/31 23:50\n" + RAW.replace("LTZZ", station)

        task = asyncio.create_task(collect_noaa_batch(["LTZZ", "KZZZ"], fetch=fetch,
                                                     clock=lambda: ARRIVAL, ledger=ObservationLedger()))
        await started.wait()
        await fast_done.wait()
        self.assertFalse(task.done())
        release.set()
        deliveries = await task
        self.assertEqual(calls, ["LTZZ", "KZZZ"])
        self.assertEqual(len(deliveries), 2)

    async def test_mgm_fast_worker_emits_before_other_worker_finishes(self):
        slow_started, emitted_fast, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        delivered = []

        async def fetch(station):
            if station == "KZZZ":
                slow_started.set()
                await release.wait()
            return [{"rasatMetar": RAW.replace("LTZZ", station)}]

        async def emit(delivery):
            delivered.append(delivery.event.observation.station)
            if delivered[-1] == "LTZZ":
                emitted_fast.set()

        ledger = ObservationLedger()
        tasks = [asyncio.create_task(collect_mgm_once(station, fetch=fetch, clock=lambda: ARRIVAL,
                                                     ledger=ledger, emit=emit))
                 for station in ["KZZZ", "LTZZ"]]
        await slow_started.wait()
        await emitted_fast.wait()
        self.assertEqual(delivered, ["LTZZ"])
        self.assertFalse(tasks[0].done())
        release.set()
        await asyncio.gather(*tasks)
        self.assertEqual(delivered, ["LTZZ", "KZZZ"])

    async def test_duplicate_polls_use_same_fetch_dependency_and_emit_once(self):
        calls = []

        async def fetch(station):
            calls.append(station)
            return "2026/05/31 23:50\n" + RAW

        delivered = await collect_noaa_batch(["LTZZ"], fetch=fetch, clock=lambda: ARRIVAL,
                                             ledger=ObservationLedger(), workers=4)
        self.assertEqual(calls, ["LTZZ"] * 4)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(historical_pool_settings("noaa", 6, 4)["max_connections"], 24)
        self.assertEqual(historical_pool_settings("mgm", 6, 4)["max_connections"], 48)

    async def test_historical_noaa_gate_can_omit_same_batch_revision(self):
        reports = iter([RAW, RAW.replace("19/12", "20/12") + " COR"])

        async def fetch(station):
            return "2026/05/31 23:50\n" + next(reports)

        ledger = ObservationLedger()
        delivered = await collect_noaa_batch(["LTZZ"], fetch=fetch, clock=lambda: ARRIVAL,
                                             ledger=ledger, workers=2)
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].event.observation.metar_temperature_c, 19)
        # The report was parsed/remembered although the batch gate omitted delivery.
        corrected = parse_metar(RAW.replace("19/12", "20/12") + " COR", source="test", arrived_at=ARRIVAL)
        self.assertEqual(ledger.ingest(corrected).status, "duplicate")


if __name__ == "__main__":
    unittest.main()
