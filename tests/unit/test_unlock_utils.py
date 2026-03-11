from __future__ import annotations

from datetime import date
import unittest

import pandas as pd

from tests import _path_setup  # noqa: F401
from services.data_inserter.collectors.unlock_support.utils import (
    ParsedUnlockEvent,
    compute_ups_raw,
    normalize_bucket_label,
    percent_rank_average,
)


class UnlockUtilsTest(unittest.TestCase):
    def test_normalize_bucket_label_maps_canonical_buckets(self) -> None:
        self.assertEqual(normalize_bucket_label("core contributors"), "Team/Founders")
        self.assertEqual(normalize_bucket_label("seed sale"), "VC/Investors")
        self.assertEqual(normalize_bucket_label("foundation reserve"), "Ecosystem")
        self.assertEqual(normalize_bucket_label("public sale"), "Airdrop/Public")
        self.assertEqual(normalize_bucket_label("mystery tranche"), "Other")

    def test_compute_ups_raw_applies_30d_window_and_bucket_weights(self) -> None:
        as_of_date = date(2024, 1, 1)
        events = [
            ParsedUnlockEvent(date(2024, 1, 10), "team", "Team/Founders", 10.0),
            ParsedUnlockEvent(date(2024, 1, 15), "seed", "VC/Investors", 5.0),
            ParsedUnlockEvent(date(2024, 1, 20), "airdrop", "Airdrop/Public", 4.0),
            ParsedUnlockEvent(date(2024, 1, 25), "unknown", "Other", 50.0),
            ParsedUnlockEvent(date(2024, 2, 15), "team", "Team/Founders", 100.0),
            ParsedUnlockEvent(date(2024, 1, 1), "team", "Team/Founders", 100.0),
        ]

        raw = compute_ups_raw(events, as_of_date=as_of_date, circ=100.0)
        expected = 1.5 * (10.0 / 100.0) + 1.2 * (5.0 / 100.0) + 0.6 * (4.0 / 100.0)

        self.assertEqual(raw, expected)

    def test_compute_ups_raw_regression_includes_day_30_and_excludes_day_31(self) -> None:
        as_of_date = date(2024, 1, 1)
        events = [
            ParsedUnlockEvent(date(2024, 1, 1), "team", "Team/Founders", 10.0),
            ParsedUnlockEvent(date(2024, 1, 31), "team", "Team/Founders", 10.0),
            ParsedUnlockEvent(date(2024, 2, 1), "team", "Team/Founders", 10.0),
        ]

        raw = compute_ups_raw(events, as_of_date=as_of_date, circ=100.0)

        self.assertEqual(raw, 1.5 * (10.0 / 100.0))

    def test_percent_rank_average_respects_ties_and_nan(self) -> None:
        ranked = percent_rank_average(pd.Series([10.0, 10.0, 30.0, None], index=["a", "b", "c", "d"]))

        self.assertEqual(ranked["a"], 0.25)
        self.assertEqual(ranked["b"], 0.25)
        self.assertEqual(ranked["c"], 1.0)
        self.assertTrue(pd.isna(ranked["d"]))

    def test_percent_rank_average_handles_massive_zero_ties_without_ranking_nan(self) -> None:
        ranked = percent_rank_average(pd.Series([0.0, 0.0, 0.0, 0.0, 1.0, None], index=list("abcdef")))

        self.assertEqual(ranked["a"], 0.375)
        self.assertEqual(ranked["b"], 0.375)
        self.assertEqual(ranked["c"], 0.375)
        self.assertEqual(ranked["d"], 0.375)
        self.assertEqual(ranked["e"], 1.0)
        self.assertTrue(pd.isna(ranked["f"]))

    def test_percent_rank_average_single_observation_returns_half(self) -> None:
        ranked = percent_rank_average(pd.Series([7.0], index=["only"]))

        self.assertEqual(ranked["only"], 0.5)


if __name__ == "__main__":
    unittest.main()
