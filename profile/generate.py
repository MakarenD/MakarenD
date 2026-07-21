"""Command-line entry point for Makaren Signal asset generation."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Mapping

from .geometry import DEFAULT_SEED, generate_m_core
from .github_data import (
    ContributionDataError,
    aggregate_weeks,
    compute_metrics,
    dump_contributions,
    fetch_contributions,
    load_contributions,
)
from .render import render_all


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).with_name("config.json")


def load_config(path: Path) -> dict[str, object]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContributionDataError(f"Unable to read profile config: {exc}") from exc

    if not isinstance(config, dict):
        raise ContributionDataError("Profile config must be a JSON object")
    identity = config.get("identity")
    capabilities = config.get("capabilities")
    systems = config.get("connected_systems")
    required_identity = {
        "handle",
        "github_username",
        "role",
        "focus",
        "location",
        "site",
    }
    if not isinstance(identity, dict) or not required_identity.issubset(identity):
        raise ContributionDataError("Profile identity is incomplete")
    if not isinstance(capabilities, dict) or list(capabilities) != [
        "backend",
        "frontend",
        "data",
        "platform",
    ]:
        raise ContributionDataError(
            "Capabilities must define backend, frontend, data, and platform in order"
        )
    if not all(
        isinstance(items, list) and all(isinstance(item, str) for item in items)
        for items in capabilities.values()
    ):
        raise ContributionDataError(
            "Every capability must be a list of technology names"
        )
    if not isinstance(systems, list):
        raise ContributionDataError("connected_systems must be a list")
    for system in systems:
        if not isinstance(system, dict) or set(system) != {
            "name",
            "url",
            "description",
        }:
            raise ContributionDataError(
                "Every connected system needs name, url, and description"
            )
        if not str(system["url"]).startswith("https://"):
            raise ContributionDataError("Connected system URLs must use HTTPS")
    return config


def generate(
    config_path: Path,
    output_dir: Path,
    data_file: Path | None = None,
    end_date: date | None = None,
    snapshot_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    config = load_config(config_path)
    username = str(config["identity"]["github_username"])
    days = (
        load_contributions(str(data_file), end_date)
        if data_file
        else fetch_contributions(username, end_date, environ)
    )
    weeks = aggregate_weeks(days)
    metrics = compute_metrics(days, weeks)
    geometry = generate_m_core(365, DEFAULT_SEED)

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="makaren-signal-") as temporary:
        staged = render_all(Path(temporary), config, days, weeks, metrics, geometry)
        for source in staged:
            os.replace(source, output_dir / source.name)

    if snapshot_path:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(dump_contributions(days), encoding="utf-8")

    return {
        "days": len(days),
        "weeks": len(weeks),
        "total_contributions": metrics.total_contributions,
        "active_days": metrics.active_days,
        "peak_week": metrics.peak_week.total_contributions,
        "signal_id": metrics.signal_id,
        "date_range": f"{metrics.start.isoformat()}..{metrics.end.isoformat()}",
        "assets": 32,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Makaren Signal profile SVG assets"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--data-file", type=Path)
    parser.add_argument("--today", type=date.fromisoformat)
    parser.add_argument("--snapshot", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = generate(
            args.config,
            args.output_dir,
            args.data_file,
            args.today,
            args.snapshot,
        )
    except ContributionDataError as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
