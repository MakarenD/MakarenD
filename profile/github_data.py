"""GitHub contribution loading and deterministic signal aggregation."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence


GRAPHQL_URL = "https://api.github.com/graphql"
KNOWN_LEVELS = {
    "NONE",
    "FIRST_QUARTILE",
    "SECOND_QUARTILE",
    "THIRD_QUARTILE",
    "FOURTH_QUARTILE",
}


class ContributionDataError(RuntimeError):
    """Raised when contribution data cannot be fetched or validated."""


class ContributionAccessError(ContributionDataError):
    """Raised when the current token cannot access contribution data."""


@dataclass(frozen=True)
class ContributionDay:
    date: date
    contribution_count: int
    contribution_level: str

    def to_json(self) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "contributionCount": self.contribution_count,
            "contributionLevel": self.contribution_level,
        }


@dataclass(frozen=True)
class ContributionWeek:
    start: date
    end: date
    total_contributions: int
    active_days: int


@dataclass(frozen=True)
class SignalMetrics:
    total_contributions: int
    active_days: int
    peak_week: ContributionWeek
    signal_id: str
    start: date
    end: date


def normalize_level(value: object) -> str:
    level = str(value or "NONE").upper()
    if level not in KNOWN_LEVELS:
        raise ContributionDataError(f"Unknown contribution level: {level}")
    return level


def normalize_days(
    raw_days: Iterable[Mapping[str, object]], end_date: date | None = None
) -> list[ContributionDay]:
    """Return exactly 365 chronological calendar days, filling gaps with zeroes."""

    end = end_date or datetime.now(timezone.utc).date()
    start = end - timedelta(days=364)
    indexed: dict[date, ContributionDay] = {}

    for raw in raw_days:
        try:
            day_date = date.fromisoformat(str(raw["date"]))
            count = int(raw.get("contributionCount", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ContributionDataError("Invalid contribution day payload") from exc
        if count < 0:
            raise ContributionDataError("Contribution counts cannot be negative")
        if start <= day_date <= end:
            indexed[day_date] = ContributionDay(
                day_date, count, normalize_level(raw.get("contributionLevel"))
            )

    return [
        indexed.get(
            start + timedelta(days=offset),
            ContributionDay(start + timedelta(days=offset), 0, "NONE"),
        )
        for offset in range(365)
    ]


def aggregate_weeks(days: Sequence[ContributionDay]) -> list[ContributionWeek]:
    """Aggregate the latest 364 days into 52 complete trailing weeks."""

    if len(days) != 365:
        raise ContributionDataError("Weekly aggregation requires exactly 365 days")

    weekly_days = days[1:]
    weeks: list[ContributionWeek] = []
    for index in range(52):
        start_index = index * 7
        bucket = weekly_days[start_index : start_index + 7]
        weeks.append(
            ContributionWeek(
                start=bucket[0].date,
                end=bucket[-1].date,
                total_contributions=sum(day.contribution_count for day in bucket),
                active_days=sum(day.contribution_count > 0 for day in bucket),
            )
        )
    return weeks


def compute_signal_id(weeks: Sequence[ContributionWeek]) -> str:
    serialized = ",".join(str(week.total_contributions) for week in weeks)
    digest = hashlib.sha256(serialized.encode("ascii")).hexdigest().upper()[:12]
    return "-".join(digest[offset : offset + 4] for offset in range(0, 12, 4))


def compute_metrics(
    days: Sequence[ContributionDay], weeks: Sequence[ContributionWeek]
) -> SignalMetrics:
    if len(days) != 365 or len(weeks) != 52:
        raise ContributionDataError("Signal metrics require 365 days and 52 weeks")
    peak = max(weeks, key=lambda week: week.total_contributions)
    return SignalMetrics(
        total_contributions=sum(day.contribution_count for day in days),
        active_days=sum(day.contribution_count > 0 for day in days),
        peak_week=peak,
        signal_id=compute_signal_id(weeks),
        start=days[0].date,
        end=days[-1].date,
    )


def _graphql_payload(username: str, start: date, end: date) -> bytes:
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
                contributionLevel
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "login": username,
        "from": f"{start.isoformat()}T00:00:00Z",
        "to": f"{end.isoformat()}T23:59:59Z",
    }
    return json.dumps({"query": query, "variables": variables}).encode("utf-8")


def _fetch_with_token(username: str, start: date, end: date, token: str) -> list[dict]:
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=_graphql_payload(username, start, end),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "makaren-signal-profile",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ContributionAccessError(
                f"GitHub GraphQL access failed with HTTP {exc.code}"
            ) from exc
        raise ContributionDataError(f"GitHub GraphQL request failed: {exc}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ContributionDataError(f"GitHub GraphQL request failed: {exc}") from exc

    if payload.get("errors"):
        messages = "; ".join(
            str(item.get("message", "GraphQL error")) for item in payload["errors"]
        )
        error_type = (
            ContributionAccessError
            if any(
                marker in messages.lower()
                for marker in (
                    "forbidden",
                    "not accessible",
                    "permission",
                    "authentication",
                )
            )
            else ContributionDataError
        )
        raise error_type(f"GitHub GraphQL returned errors: {messages}")
    try:
        weeks = payload["data"]["user"]["contributionsCollection"][
            "contributionCalendar"
        ]["weeks"]
        return [day for week in weeks for day in week["contributionDays"]]
    except (KeyError, TypeError) as exc:
        raise ContributionDataError(
            "GitHub GraphQL response has an unexpected shape"
        ) from exc


def fetch_contributions(
    username: str,
    end_date: date | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[ContributionDay]:
    """Fetch contributions using GITHUB_TOKEN, with METRICS_TOKEN as fallback."""

    env = environ or os.environ
    end = end_date or datetime.now(timezone.utc).date()
    start = end - timedelta(days=364)
    tokens: list[str] = []
    for name in ("GITHUB_TOKEN", "METRICS_TOKEN"):
        value = env.get(name, "").strip()
        if value and value not in tokens:
            tokens.append(value)
    if not tokens:
        raise ContributionDataError("GITHUB_TOKEN or METRICS_TOKEN is required")

    failures: list[str] = []
    for token in tokens:
        try:
            return normalize_days(_fetch_with_token(username, start, end, token), end)
        except ContributionAccessError as exc:
            failures.append(str(exc))
    raise ContributionDataError(
        "Unable to load GitHub contributions: " + " | ".join(failures)
    )


def load_contributions(
    path: str, end_date: date | None = None
) -> list[ContributionDay]:
    with open(path, encoding="utf-8") as source:
        payload = json.load(source)
    raw_days = payload.get("days", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_days, list):
        raise ContributionDataError("Contribution fixture must contain a list of days")
    if end_date is None and raw_days:
        try:
            end_date = max(date.fromisoformat(str(item["date"])) for item in raw_days)
        except (KeyError, TypeError, ValueError) as exc:
            raise ContributionDataError(
                "Contribution fixture contains an invalid date"
            ) from exc
    return normalize_days(raw_days, end_date)


def dump_contributions(days: Sequence[ContributionDay]) -> str:
    return json.dumps({"days": [day.to_json() for day in days]}, indent=2) + "\n"
