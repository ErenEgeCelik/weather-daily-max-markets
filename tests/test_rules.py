from dataclasses import replace
import unittest

from weather_research.decisions import Bucket, BucketBook
from weather_research.rules import build_triggers, cascade_matches, known_yes_payoff


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.b24 = Bucket("24", 24, 24)
        self.b25 = Bucket("25", 25, 25)
        self.b26 = Bucket("26", 26, 26)
        self.top = Bucket("27+", 27, None)

    def books(self):
        return (
            BucketBook(self.b24, yes_bid=0.25, yes_ask=0.3, no_bid=0.6, no_ask=0.65),
            BucketBook(self.b25, yes_bid=0.15, yes_ask=0.2, no_bid=0.7, no_ask=0.8),
            BucketBook(self.b26, yes_bid=0.1, yes_ask=0.15, no_bid=0.8, no_ask=0.85, yes_ask_depth=10),
            BucketBook(self.top, yes_bid=0.05, yes_ask=0.1),
        )

    def test_confirmed_max_kills_only_below_buckets_and_locks_top(self):
        self.assertIsNone(known_yes_payoff(self.b24, 24))  # Maximum can still rise.
        self.assertEqual(known_yes_payoff(self.b24, 25), 0)
        self.assertEqual(known_yes_payoff(Bucket("23-", None, 23), 24), 0)
        self.assertIsNone(known_yes_payoff(self.top, 26))
        self.assertEqual(known_yes_payoff(self.top, 27), 1)

    def test_sticky_r1_r2_do_not_depend_on_an_emission_time_book(self):
        candidates = build_triggers((BucketBook(self.b24), BucketBook(self.top)), 24)
        self.assertEqual({c.rule for c in candidates}, {"R1", "R2"})
        r1 = next(c for c in candidates if c.rule == "R1")
        self.assertFalse(r1.observation_matches(24))
        self.assertTrue(r1.observation_matches(25))
        self.assertIsNone(r1.reference_ask)
        self.assertFalse(any(c.rule == "R1" for c in build_triggers((BucketBook(self.b24),), 25)))

    def test_r3_all_intermediate_no_buckets_must_match(self):
        r3 = next(c for c in build_triggers(self.books(), 24) if c.rule == "R3")
        self.assertEqual(r3.bucket, self.b26)
        self.assertEqual(r3.watch_buckets, (self.b24, self.b25))
        self.assertFalse(cascade_matches(r3, {self.b25: 0.99}))
        self.assertFalse(cascade_matches(r3, {self.b24: 0.8, self.b25: 0.99}))
        self.assertTrue(cascade_matches(r3, {self.b24: 0.85, self.b25: 0.99}))
        self.assertTrue(r3.observation_matches(26))
        self.assertFalse(r3.observation_matches(25))

    def test_r3_distance_and_liquidity_filters(self):
        self.assertFalse(any(c.rule == "R3" for c in build_triggers(self.books(), 23)))
        no_depth = tuple(replace(book, yes_ask_depth=0) for book in self.books())
        self.assertFalse(any(c.rule == "R3" for c in build_triggers(no_depth, 24)))

    def test_missing_intermediate_bucket_disables_spike_condition(self):
        books = tuple(book for book in self.books() if book.bucket != self.b25)
        r3 = next(c for c in build_triggers(books, 24) if c.rule == "R3")
        self.assertEqual(r3.watch_buckets, ())
        self.assertFalse(cascade_matches(r3, {self.b24: 0.99}))
        self.assertTrue(r3.observation_matches(26))

    def test_range_observation_condition_uses_all_inclusive_bounds(self):
        broad = Bucket("25-28", 25, 28)
        books = (BucketBook(self.b24, yes_ask=0.5),
                 BucketBook(broad, yes_bid=0.2, yes_ask=0.3, yes_ask_depth=10),
                 BucketBook(Bucket("29+", 29, None), yes_ask=0.1))
        r3 = next(c for c in build_triggers(books, 24) if c.rule == "R3")
        for value in (25, 26, 27, 28):
            self.assertTrue(r3.observation_matches(value))
        self.assertFalse(r3.observation_matches(29))

    def test_overlapping_buckets_are_rejected(self):
        with self.assertRaises(ValueError):
            build_triggers((BucketBook(self.b24), BucketBook(Bucket("24+", 24, None))), 24)


if __name__ == "__main__":
    unittest.main()
