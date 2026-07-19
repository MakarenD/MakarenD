#!/usr/bin/env python3
"""Generate the profile hero and contribution activity SVG assets."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageEnhance, ImageOps


PROFILE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROFILE_DIR / "config.json"
DEFAULT_OUTPUT = PROFILE_DIR.parent / "dist"
LOCAL_AVATAR = PROFILE_DIR / "avatar-source.png"
GRAPHQL_URL = "https://api.github.com/graphql"
ASCII_PALETTE = "@#%*+=-:.{}[]<>/\\01 "
LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    panel: str
    border: str
    text: str
    muted: str
    accent: str
    activity: tuple[str, str, str, str, str]


THEMES = {
    "dark": Theme(
        name="dark",
        background="#0d1117",
        panel="#161b22",
        border="#30363d",
        text="#e6edf3",
        muted="#8b949e",
        accent="#39c5cf",
        activity=("#21262d", "#0e4429", "#006d32", "#26a641", "#39d353"),
    ),
    "light": Theme(
        name="light",
        background="#ffffff",
        panel="#f6f8fa",
        border="#d0d7de",
        text="#1f2328",
        muted="#656d76",
        accent="#087f8c",
        activity=("#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"),
    ),
}


class GenerationError(RuntimeError):
    """Raised when a required profile asset cannot be generated safely."""


def xml_escape(value: object) -> str:
    """Escape text for safe insertion into XML content and attributes."""

    return html.escape(str(value), quote=True)


def load_config(path: Path) -> dict[str, str]:
    """Read and validate the user-editable profile configuration."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Cannot read profile configuration: {path}") from exc

    if not isinstance(raw, dict):
        raise GenerationError("Profile configuration must be a JSON object")

    required = ("username", "host", "role", "focus", "location")
    for field in required:
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise GenerationError(
                f"Configuration field '{field}' must be a non-empty string"
            )

    status = raw.get("status", "")
    if not isinstance(status, str):
        raise GenerationError("Configuration field 'status' must be a string")

    github_username = raw.get("github_username", raw["username"])
    if not isinstance(github_username, str) or not github_username.strip():
        raise GenerationError(
            "Configuration field 'github_username' must be a non-empty string"
        )

    return {
        "username": raw["username"].strip(),
        "github_username": github_username.strip(),
        "host": raw["host"].strip(),
        "role": raw["role"].strip(),
        "focus": raw["focus"].strip(),
        "status": status.strip(),
        "location": raw["location"].strip(),
    }


def brightness_to_ascii(brightness: int | float, palette: str = ASCII_PALETTE) -> str:
    """Map an 8-bit brightness value to a character from dark to light."""

    if not palette:
        raise ValueError("ASCII palette cannot be empty")
    value = max(0.0, min(255.0, float(brightness)))
    index = round((value / 255.0) * (len(palette) - 1))
    return palette[index]


def portrait_lines(image: Image.Image, columns: int = 47) -> list[str]:
    """Convert an avatar into a contrast-balanced, aspect-correct ASCII portrait."""

    if columns < 8:
        raise ValueError("Portrait must use at least eight columns")

    rgb = image.convert("RGB")
    square = ImageOps.fit(
        rgb, (460, 460), method=Image.Resampling.LANCZOS, centering=(0.5, 0.45)
    )
    grayscale = ImageOps.autocontrast(ImageOps.grayscale(square), cutoff=1)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(1.18)
    rows = max(8, round(columns * 0.62))
    reduced = grayscale.resize((columns, rows), Image.Resampling.LANCZOS)

    pixels = (
        list(reduced.get_flattened_data())
        if hasattr(reduced, "get_flattened_data")
        else list(reduced.getdata())
    )
    average = sum(pixels) / len(pixels)
    gamma = 0.88 if average < 112 else 1.08 if average > 176 else 1.0

    result: list[str] = []
    for row in range(rows):
        chars = []
        for column in range(columns):
            value = pixels[row * columns + column]
            adjusted = 255 * ((value / 255) ** gamma)
            chars.append(brightness_to_ascii(adjusted))
        result.append("".join(chars).rstrip())
    return result


def _request(url: str, *, data: bytes | None = None, token: str = "") -> bytes:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MakarenD-profile-generator",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise GenerationError(f"GitHub request failed for {url}") from exc


def load_avatar(
    github_username: str, local_path: Path = LOCAL_AVATAR
) -> tuple[Image.Image, str]:
    """Load a local avatar override or download the current GitHub avatar."""

    try:
        if local_path.exists():
            with Image.open(local_path) as image:
                return image.copy(), "local avatar-source.png"

        url = f"https://github.com/{github_username}.png?size=460"
        with Image.open(BytesIO(_request(url))) as image:
            return image.copy(), "GitHub avatar"
    except (OSError, ValueError) as exc:
        raise GenerationError("Avatar is not a readable image") from exc


def _token_from_environment() -> str:
    return next(
        (
            os.environ[name]
            for name in ("PROFILE_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
            if os.environ.get(name)
        ),
        "",
    )


def extract_calendar(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract a contribution calendar from a GitHub GraphQL response."""

    if payload.get("errors"):
        messages = "; ".join(
            str(error.get("message", "GraphQL error")) for error in payload["errors"]
        )
        raise GenerationError(f"GitHub GraphQL returned an error: {messages}")
    try:
        calendar = payload["data"]["user"]["contributionsCollection"][
            "contributionCalendar"
        ]
    except (KeyError, TypeError) as exc:
        raise GenerationError(
            "GitHub response did not include a contribution calendar"
        ) from exc
    if not isinstance(calendar.get("weeks"), list) or not calendar["weeks"]:
        raise GenerationError("Contribution calendar contains no weeks")
    return calendar


def fetch_contributions(github_username: str, token: str) -> dict[str, Any]:
    """Fetch the real contribution calendar for the configured GitHub account."""

    if not token:
        raise GenerationError(
            "A GitHub token is required for contribution data; set PROFILE_GITHUB_TOKEN, GITHUB_TOKEN, or GH_TOKEN"
        )
    query = """
      query($login: String!) {
        user(login: $login) {
          contributionsCollection {
            contributionCalendar {
              totalContributions
              weeks {
                contributionDays {
                  contributionCount
                  contributionLevel
                  date
                  weekday
                }
              }
            }
          }
        }
      }
    """
    body = json.dumps({"query": query, "variables": {"login": github_username}}).encode(
        "utf-8"
    )
    return extract_calendar(json.loads(_request(GRAPHQL_URL, data=body, token=token)))


def load_contributions_file(path: Path) -> dict[str, Any]:
    """Load a recorded GraphQL response or a normalized calendar for deterministic checks."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Cannot read contribution data: {path}") from exc
    return (
        extract_calendar(payload)
        if "data" in payload or "errors" in payload
        else payload
    )


def contribution_level(level: str, count: int = 0) -> int:
    """Convert GitHub's contribution level enum into a stable 0-4 intensity."""

    if level in LEVELS:
        return LEVELS[level]
    if count <= 0:
        return 0
    raise GenerationError(f"Unknown contribution level: {level}")


def _svg_prelude(width: int, height: int, title: str, description: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {width} {height}" '
        f'role="img" aria-labelledby="title desc">\n'
        f'  <title id="title">{xml_escape(title)}</title>\n'
        f'  <desc id="desc">{xml_escape(description)}</desc>\n'
    )


def render_hero(theme: Theme, config: dict[str, str], lines: Iterable[str]) -> str:
    """Render the terminal-style system identity card."""

    width, height = 1000, 420
    line_list = list(lines)
    svg = [
        _svg_prelude(
            width,
            height,
            "Makaren system profile",
            "ASCII portrait and software engineering profile",
        )
    ]
    svg.append(
        "  <style>\n"
        "    .portrait-line,.info-line,.command{opacity:1;animation:reveal .28s ease-out both}\n"
        "    @keyframes reveal{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}\n"
        "    @media (prefers-reduced-motion: reduce){.portrait-line,.info-line,.command{opacity:1;animation:none;transform:none}}\n"
        "  </style>\n"
    )
    svg.extend(
        [
            f'  <rect width="{width}" height="{height}" rx="22" fill="{theme.background}"/>\n',
            f'  <rect x="1" y="1" width="998" height="418" rx="21" fill="none" stroke="{theme.border}"/>\n',
            f'  <line x1="0" y1="48" x2="1000" y2="48" stroke="{theme.border}"/>\n',
            f'  <circle cx="24" cy="24" r="5" fill="{theme.border}"/>\n',
            f'  <circle cx="42" cy="24" r="5" fill="{theme.border}"/>\n',
            f'  <circle cx="60" cy="24" r="5" fill="{theme.accent}"/>\n',
            f'  <text x="82" y="29" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13" letter-spacing="1.5">MAKAREN // SYSTEM PROFILE</text>\n',
            f'  <text x="950" y="29" text-anchor="end" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="12">identity.sys</text>\n',
            f'  <rect x="32" y="70" width="452" height="318" rx="14" fill="{theme.panel}" stroke="{theme.border}"/>\n',
            f'  <text x="52" y="91" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11" letter-spacing="1">AVATAR.ASCII</text>\n',
        ]
    )

    portrait_y = 111
    portrait_step = 9.1
    for index, line in enumerate(line_list):
        delay = index * 18
        svg.append(
            f'  <text class="portrait-line" x="55" y="{portrait_y + index * portrait_step:.1f}" '
            f'style="animation-delay:{delay}ms" fill="{theme.accent}" '
            'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="8.8" '
            f'letter-spacing="0.35">{xml_escape(line)}</text>\n'
        )

    svg.append(
        f'  <text class="info-line" x="535" y="103" style="animation-delay:260ms" '
        f'fill="{theme.text}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="22" font-weight="600">'
        f'<tspan fill="{theme.accent}">{xml_escape(config["username"])}</tspan>'
        f'<tspan fill="{theme.muted}">@</tspan>{xml_escape(config["host"])}</text>\n'
    )
    svg.append(
        f'  <line x1="535" y1="122" x2="958" y2="122" stroke="{theme.border}"/>\n'
    )

    fields = [("role", config["role"]), ("focus", config["focus"])]
    if config.get("status"):
        fields.append(("status", config["status"]))
    fields.append(("location", config["location"]))

    y = 164
    for index, (label, value) in enumerate(fields):
        delay = 380 + index * 105
        svg.append(
            f'  <text class="info-line" x="535" y="{y}" style="animation-delay:{delay}ms" '
            f'fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="14">'
            f"{xml_escape(label)}</text>\n"
        )
        value_lines = [value]
        if len(value) > 32 and " · " in value:
            value_lines = [part.strip() for part in value.split("·")]
        for line_index, value_line in enumerate(value_lines):
            line_y = y + line_index * 21
            svg.append(
                f'  <text class="info-line" x="648" y="{line_y}" style="animation-delay:{delay}ms" '
                f'fill="{theme.text}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="15">'
                f"{xml_escape(value_line)}</text>\n"
            )
        y += 44 + (len(value_lines) - 1) * 21

    command_y = min(y + 30, 374)
    svg.append(
        f'  <text class="command" x="535" y="{command_y}" style="animation-delay:820ms" '
        f'fill="{theme.text}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="16">'
        f'<tspan fill="{theme.accent}">$</tspan> ./build_future</text>\n'
    )
    svg.append("</svg>\n")
    return "".join(svg)


def _calendar_days(calendar: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    days: list[tuple[int, dict[str, Any]]] = []
    for week_index, week in enumerate(calendar.get("weeks", [])):
        contribution_days = week.get("contributionDays", [])
        if not isinstance(contribution_days, list):
            raise GenerationError("Contribution week must contain a list of days")
        for day in contribution_days:
            if not isinstance(day, dict):
                raise GenerationError("Contribution day must be an object")
            days.append((week_index, day))
    if not days:
        raise GenerationError("Contribution calendar contains no days")
    return days


def render_activity(theme: Theme, calendar: dict[str, Any]) -> str:
    """Render a compact terminal activity log from real contribution data."""

    width, height = 1000, 220
    weeks = calendar.get("weeks", [])
    days = _calendar_days(calendar)
    cell, gap = 10, 3
    grid_width = len(weeks) * (cell + gap) - gap
    grid_x = max(116, (width - grid_width) // 2)
    grid_y = 72
    total = int(
        calendar.get(
            "totalContributions",
            sum(int(day.get("contributionCount", 0)) for _, day in days),
        )
    )
    dates = sorted(str(day.get("date", "")) for _, day in days if day.get("date"))
    date_range = f"{dates[0]} → {dates[-1]}" if dates else "rolling year"

    svg = [
        _svg_prelude(
            width,
            height,
            "GitHub contribution activity",
            f"{total} contributions from {date_range}",
        )
    ]
    svg.append(
        "  <style>\n"
        "    .activity-cell{opacity:1;animation:fill .32s ease-out both;animation-delay:var(--delay)}\n"
        "    @keyframes fill{from{opacity:0;transform:scale(.55)}to{opacity:1;transform:scale(1)}}\n"
        "    @media (prefers-reduced-motion: reduce){.activity-cell{opacity:1;animation:none;transform:none}}\n"
        "  </style>\n"
    )
    svg.extend(
        [
            f'  <rect width="{width}" height="{height}" rx="18" fill="{theme.background}"/>\n',
            f'  <rect x="1" y="1" width="998" height="218" rx="17" fill="none" stroke="{theme.border}"/>\n',
            f'  <text x="32" y="37" fill="{theme.accent}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="15" font-weight="600">activity.log</text>\n',
            f'  <text x="968" y="37" text-anchor="end" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13">{total} contributions</text>\n',
            f'  <line x1="32" y1="51" x2="968" y2="51" stroke="{theme.border}"/>\n',
        ]
    )

    for weekday, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        y = grid_y + weekday * (cell + gap) + 8
        svg.append(
            f'  <text x="{grid_x - 14}" y="{y}" text-anchor="end" fill="{theme.muted}" '
            f'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="10">{label}</text>\n'
        )

    denominator = max(1, len(weeks) - 1)
    for week_index, day in days:
        weekday = int(day.get("weekday", 0))
        if not 0 <= weekday <= 6:
            raise GenerationError(f"Invalid contribution weekday: {weekday}")
        count = int(day.get("contributionCount", 0))
        level = contribution_level(str(day.get("contributionLevel", "NONE")), count)
        x = grid_x + week_index * (cell + gap)
        y = grid_y + weekday * (cell + gap)
        delay = round(week_index / denominator * 1250 + weekday * 12)
        svg.append(
            f'  <rect class="activity-cell" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" '
            f'fill="{theme.activity[level]}" style="--delay:{delay}ms;transform-origin:{x + cell / 2}px {y + cell / 2}px">'
            f"<title>{xml_escape(day.get('date', 'unknown date'))}: {count} contributions</title></rect>\n"
        )

    legend_y = 190
    svg.append(
        f'  <text x="32" y="{legend_y}" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11">{xml_escape(date_range)}</text>\n'
    )
    legend_x = 836
    svg.append(
        f'  <text x="{legend_x - 12}" y="{legend_y}" text-anchor="end" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="10">less</text>\n'
    )
    for level, color in enumerate(theme.activity):
        svg.append(
            f'  <rect x="{legend_x + level * 17}" y="{legend_y - 10}" width="10" height="10" rx="2" fill="{color}"/>\n'
        )
    svg.append(
        f'  <text x="{legend_x + 92}" y="{legend_y}" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="10">more</text>\n'
    )
    svg.append("</svg>\n")
    return "".join(svg)


def generate_assets(
    config_path: Path,
    output_dir: Path,
    contributions_file: Path | None = None,
    avatar_path: Path = LOCAL_AVATAR,
) -> list[Path]:
    """Generate all four public SVG assets and return their paths."""

    print(f"config: {config_path}")
    config = load_config(config_path)
    avatar, avatar_source = load_avatar(config["github_username"], avatar_path)
    print(f"avatar: {avatar_source}")
    lines = portrait_lines(avatar)

    if contributions_file:
        calendar = load_contributions_file(contributions_file)
        print(f"contributions: {contributions_file}")
    else:
        calendar = fetch_contributions(
            config["github_username"], _token_from_environment()
        )
        print("contributions: GitHub GraphQL")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for theme_name, theme in THEMES.items():
        hero_path = output_dir / f"hero-{theme_name}.svg"
        activity_path = output_dir / f"activity-{theme_name}.svg"
        hero_path.write_text(render_hero(theme, config, lines), encoding="utf-8")
        activity_path.write_text(render_activity(theme, calendar), encoding="utf-8")
        outputs.extend((hero_path, activity_path))
        print(f"wrote: {hero_path}")
        print(f"wrote: {activity_path}")
    return outputs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--contributions-file", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        generate_assets(args.config, args.output, args.contributions_file)
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
