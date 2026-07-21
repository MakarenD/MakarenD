from __future__ import annotations

import hashlib
import unittest
from datetime import date, timedelta
from unittest import mock

from profile import github_data
from profile.github_data import (
    ContributionAccessError,
    ContributionDataError,
    ContributionDay,
    aggregate_weeks,
    compute_metrics,
    compute_signal_id,
    fetch_contributions,
    normalize_days,
)


class ContributionDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.end = date(2026, 7, 21)
        self.start = self.end - timedelta(days=364)

    def test_normalization_returns_exact_calendar_and_fills_missing_days(self) -> None:
        raw = [
            {
                "date": self.end.isoformat(),
                "contributionCount": 4,
                "contributionLevel": "SECOND_QUARTILE",
            },
            {
                "date": self.start.isoformat(),
                "contributionCount": 1,
                "contributionLevel": "FIRST_QUARTILE",
            },
        ]
        days = normalize_days(reversed(raw), self.end)
        self.assertEqual(365, len(days))
        self.assertEqual(self.start, days[0].date)
        self.assertEqual(self.end, days[-1].date)
        self.assertEqual(
            list(sorted(day.date for day in days)), [day.date for day in days]
        )
        self.assertEqual(
            (0, "NONE"), (days[1].contribution_count, days[1].contribution_level)
        )

    def test_normalization_handles_leap_day(self) -> None:
        leap_end = date(2024, 3, 1)
        days = normalize_days([], leap_end)
        self.assertIn(date(2024, 2, 29), [day.date for day in days])
        self.assertEqual(365, len(days))

    def test_unknown_level_and_negative_count_are_rejected(self) -> None:
        with self.assertRaises(ContributionDataError):
            normalize_days(
                [
                    {
                        "date": self.end.isoformat(),
                        "contributionCount": 1,
                        "contributionLevel": "MAXIMUM",
                    }
                ],
                self.end,
            )
        with self.assertRaises(ContributionDataError):
            normalize_days(
                [
                    {
                        "date": self.end.isoformat(),
                        "contributionCount": -1,
                        "contributionLevel": "NONE",
                    }
                ],
                self.end,
            )

    def test_week_aggregation_covers_all_days_in_52_buckets(self) -> None:
        days = [
            ContributionDay(self.start + timedelta(days=index), index % 5, "NONE")
            for index in range(365)
        ]
        weeks = aggregate_weeks(days)
        self.assertEqual(52, len(weeks))
        lengths = [(week.end - week.start).days + 1 for week in weeks]
        self.assertEqual([7] * 52, lengths)
        self.assertEqual(
            sum(day.contribution_count for day in days[1:]),
            sum(week.total_contributions for week in weeks),
        )
        self.assertEqual(days[1].date, weeks[0].start)
        self.assertEqual(days[-1].date, weeks[-1].end)

    def test_metrics_and_signal_id_are_deterministic(self) -> None:
        days = [
            ContributionDay(
                self.start + timedelta(days=index),
                3 if index in (0, 200, 364) else 0,
                "NONE",
            )
            for index in range(365)
        ]
        weeks = aggregate_weeks(days)
        metrics = compute_metrics(days, weeks)
        self.assertEqual(9, metrics.total_contributions)
        self.assertEqual(3, metrics.active_days)
        expected_digest = (
            hashlib.sha256(
                ",".join(str(week.total_contributions) for week in weeks).encode(
                    "ascii"
                )
            )
            .hexdigest()
            .upper()[:12]
        )
        expected = "-".join(
            expected_digest[index : index + 4] for index in range(0, 12, 4)
        )
        self.assertEqual(expected, compute_signal_id(weeks))
        self.assertRegex(metrics.signal_id, r"^[0-9A-F]{4}(?:-[0-9A-F]{4}){2}$")

    def test_github_token_precedes_metrics_token_and_falls_back(self) -> None:
        raw = [
            {
                "date": self.end.isoformat(),
                "contributionCount": 2,
                "contributionLevel": "FIRST_QUARTILE",
            }
        ]
        with mock.patch.object(
            github_data,
            "_fetch_with_token",
            side_effect=[ContributionAccessError("denied"), raw],
        ) as fetch:
            days = fetch_contributions(
                "MakarenD",
                self.end,
                {"GITHUB_TOKEN": "built-in", "METRICS_TOKEN": "fallback"},
            )
        self.assertEqual(
            ["built-in", "fallback"], [call.args[3] for call in fetch.call_args_list]
        )
        self.assertEqual(2, days[-1].contribution_count)

    def test_non_access_failure_does_not_retry_with_fallback_token(self) -> None:
        with mock.patch.object(
            github_data,
            "_fetch_with_token",
            side_effect=ContributionDataError("invalid schema"),
        ) as fetch:
            with self.assertRaisesRegex(ContributionDataError, "invalid schema"):
                fetch_contributions(
                    "MakarenD",
                    self.end,
                    {"GITHUB_TOKEN": "built-in", "METRICS_TOKEN": "fallback"},
                )
        self.assertEqual(1, fetch.call_count)

    def test_missing_tokens_fail_without_network_request(self) -> None:
        with mock.patch.object(github_data, "_fetch_with_token") as fetch:
            with self.assertRaises(ContributionDataError):
                fetch_contributions("MakarenD", self.end, {})
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
