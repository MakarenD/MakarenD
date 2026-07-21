"""Command-line entry point for Makaren Signal asset generation."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import date
from html import escape
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from .geometry import DEFAULT_SEED, generate_m_core
from .github_data import (
    ContributionDataError,
    aggregate_weeks,
    compute_metrics,
    dump_contributions,
    fetch_contributions,
    load_contributions,
)
from .render import render_all, system_asset_stem


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).with_name("config.json")
README = ROOT / "README.md"
SYSTEMS_MARKERS = ("<!-- CONNECTED_SYSTEMS:START -->", "<!-- CONNECTED_SYSTEMS:END -->")
FOOTER_MARKERS = ("<!-- IDENTITY_FOOTER:START -->", "<!-- IDENTITY_FOOTER:END -->")
SAFE_SYSTEM_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*$")


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
        "site_url",
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
    if not all(
        isinstance(identity[field], str) and identity[field]
        for field in required_identity
    ):
        raise ContributionDataError("Profile identity fields must be non-empty strings")
    _validate_https_url(str(identity["site_url"]), "Site")
    if not isinstance(systems, list):
        raise ContributionDataError("connected_systems must be a list")
    ids: set[str] = set()
    for system in systems:
        if (
            not isinstance(system, dict)
            or not {"id", "kind", "name", "url", "description"}.issubset(system)
            or set(system) - {"id", "kind", "name", "url", "description", "status"}
        ):
            raise ContributionDataError(
                "Every connected system needs id, kind, name, url, and description"
            )
        for field in ("id", "kind", "name", "url", "description"):
            if not isinstance(system[field], str) or not system[field]:
                raise ContributionDataError(
                    f"Connected system {field} must be a non-empty string"
                )
        if "status" in system and (
            not isinstance(system["status"], str) or not system["status"]
        ):
            raise ContributionDataError(
                "Connected system status must be a non-empty string when provided"
            )
        system_id = str(system["id"])
        if not SAFE_SYSTEM_ID.fullmatch(system_id):
            raise ContributionDataError(
                "Connected system id must be a lowercase kebab-case slug"
            )
        if system_id in ids:
            raise ContributionDataError("Connected system ids must be unique")
        ids.add(system_id)
        _validate_https_url(str(system["url"]), "Connected system")
    return config


def _validate_https_url(value: str, label: str) -> None:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ContributionDataError(f"{label} URLs must use HTTPS")


def _picture(asset_stem: str, alt: str, version: int = 2) -> str:
    base = "https://raw.githubusercontent.com/MakarenD/MakarenD/output/"
    return "\n".join(
        [
            "<picture>",
            f'  <source media="(max-width: 480px) and (prefers-color-scheme: dark)" srcset="{base}{asset_stem}-dark-mobile.svg?v={version}" />',
            f'  <source media="(max-width: 480px) and (prefers-color-scheme: light)" srcset="{base}{asset_stem}-light-mobile.svg?v={version}" />',
            f'  <source media="(prefers-color-scheme: dark)" srcset="{base}{asset_stem}-dark.svg?v={version}" />',
            f'  <source media="(prefers-color-scheme: light)" srcset="{base}{asset_stem}-light.svg?v={version}" />',
            f'  <img src="{base}{asset_stem}-light.svg?v={version}" width="100%" alt="{escape(alt, quote=True)}" />',
            "</picture>",
        ]
    )


def connected_systems_block(config: Mapping[str, object]) -> str:
    systems = list(config["connected_systems"])
    blocks = [_picture("signal-systems-header", "Makaren connected systems")]
    for system in systems:
        name = str(system["name"])
        url = escape(str(system["url"]), quote=True)
        picture = _picture(system_asset_stem(str(system["id"])), name)
        blocks.append(
            f'<a href="{url}" aria-label="Open {escape(name, quote=True)}" title="Open {escape(name, quote=True)}">\n{picture}\n</a>'
        )
    return "\n\n".join(blocks)


def identity_footer_block(config: Mapping[str, object]) -> str:
    identity = config["identity"]
    site = escape(str(identity["site"]).upper())
    site_url = escape(str(identity["site_url"]), quote=True)
    location = escape(str(identity["location"]).upper())
    return f'<div align="center">\n  <sub>\n    <a href="{site_url}">{site}</a>\n    · SOFTWARE ENGINEERING · {location}\n  </sub>\n</div>'


def _replace_marker_block(source: str, markers: tuple[str, str], content: str) -> str:
    start, end = markers
    pattern = re.compile(f"({re.escape(start)})(.*?)(\\n{re.escape(end)})", re.DOTALL)
    result, count = pattern.subn(
        lambda match: f"{match.group(1)}\n{content}{match.group(3)}", source
    )
    if count != 1:
        raise ContributionDataError(
            f"README must contain exactly one {start} marker block"
        )
    return result


def sync_readme(
    config_path: Path = DEFAULT_CONFIG, readme_path: Path = README, check: bool = False
) -> bool:
    config = load_config(config_path)
    current = readme_path.read_text(encoding="utf-8")
    updated = _replace_marker_block(
        current, SYSTEMS_MARKERS, connected_systems_block(config)
    )
    updated = _replace_marker_block(
        updated, FOOTER_MARKERS, identity_footer_block(config)
    )
    if check:
        return current == updated
    if current != updated:
        readme_path.write_text(updated, encoding="utf-8")
    return True


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
        "assets": len(list(output_dir.glob("signal-*.svg"))),
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
    parser.add_argument("--sync-readme", action="store_true")
    parser.add_argument("--check-readme", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.sync_readme or args.check_readme:
            synced = sync_readme(args.config, check=args.check_readme)
            if args.check_readme and not synced:
                print(
                    "error: README is out of sync; run python -m profile.generate --sync-readme",
                    file=os.sys.stderr,
                )
                return 1
            if args.sync_readme:
                print(json.dumps({"readme": "synchronized"}))
            return 0
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
