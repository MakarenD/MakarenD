"""Native SVG renderers for the Makaren Signal visual system."""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Mapping, Sequence

from .geometry import MCoreGeometry
from .github_data import ContributionDay, ContributionWeek, SignalMetrics


@dataclass(frozen=True)
class Theme:
    name: str
    background: str
    surface: str
    surface_alt: str
    line: str
    text: str
    muted: str
    signal: str
    signal_soft: str


THEMES = {
    "dark": Theme(
        "dark",
        "#080B10",
        "#0D1117",
        "#111720",
        "#26303B",
        "#E6EDF3",
        "#7D8590",
        "#32D6E2",
        "#1B7D86",
    ),
    "light": Theme(
        "light",
        "#F7F9FB",
        "#FFFFFF",
        "#EDF2F5",
        "#CBD5DD",
        "#17212B",
        "#5E6B78",
        "#087F8C",
        "#55B8C1",
    ),
}


def _css(theme: Theme, static: bool = False) -> str:
    static_rules = (
        """
      .motion { display: none !important; }
      .reveal { animation: none !important; opacity: var(--final-opacity, 1) !important; }
      .draw { animation: none !important; stroke-dashoffset: 0 !important; }
    """
        if static
        else ""
    )
    return f"""
      :root {{ color-scheme: {theme.name}; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
      .mono {{ font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace; }}
      .label {{ fill: {theme.muted}; font-size: 11px; letter-spacing: 2px; }}
      .copy {{ fill: {theme.text}; }}
      .muted {{ fill: {theme.muted}; }}
      .signal {{ fill: {theme.signal}; }}
      .line {{ stroke: {theme.line}; }}
      .final-opacity {{ opacity: var(--final-opacity, 1); }}
      .reveal {{ animation: reveal var(--reveal-duration, .32s) ease-out var(--reveal-delay, 0s) both; }}
      .draw {{ animation: draw var(--draw-duration, 1.5s) ease-out var(--draw-delay, 0s) both; }}
      .connection {{ vector-effect: non-scaling-stroke; }}
      @keyframes reveal {{ from {{ opacity: 0; }} to {{ opacity: var(--final-opacity, 1); }} }}
      @keyframes draw {{ from {{ stroke-dashoffset: 1; }} to {{ stroke-dashoffset: 0; }} }}
      @media (prefers-reduced-motion: reduce) {{
        .motion {{ display: none !important; }}
        .reveal {{ animation: none !important; opacity: var(--final-opacity, 1) !important; }}
        .draw {{ animation: none !important; stroke-dashoffset: 0 !important; }}
      }}
      {static_rules}
    """


def _svg_open(
    width: int,
    height: int,
    title: str,
    description: str,
    theme: Theme,
    static: bool = False,
) -> str:
    title_id = "signal-title"
    description_id = "signal-description"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{title_id} {description_id}">
  <title id="{title_id}">{escape(title)}</title>
  <desc id="{description_id}">{escape(description)}</desc>
  <style>{_css(theme, static)}</style>
  <rect width="{width}" height="{height}" fill="{theme.background}" rx="12"/>
"""


def _svg_close() -> str:
    return "</svg>\n"


def _percentile(values: Sequence[int], percentile: float) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _node_style(day: ContributionDay, clip: float) -> tuple[float, float]:
    level_radius = {
        "NONE": 1.15,
        "FIRST_QUARTILE": 1.65,
        "SECOND_QUARTILE": 2.05,
        "THIRD_QUARTILE": 2.45,
        "FOURTH_QUARTILE": 2.9,
    }
    radius = level_radius.get(day.contribution_level, 1.15)
    if day.contribution_count <= 0:
        return radius, 0.18
    denominator = math.log1p(max(1.0, clip))
    intensity = math.log1p(min(day.contribution_count, clip)) / denominator
    return radius + intensity * 0.42, 0.42 + intensity * 0.48


def _reveal_style(final_opacity: float, delay: float, duration: float = 0.32) -> str:
    return (
        f"--final-opacity:{final_opacity:.3f};--reveal-delay:{delay:.3f}s;"
        f"--reveal-duration:{duration:.2f}s"
    )


def _hero_network(
    days: Sequence[ContributionDay],
    geometry: MCoreGeometry,
    theme: Theme,
    transform: str,
) -> str:
    counts = [day.contribution_count for day in days if day.contribution_count > 0]
    clip = max(1.0, _percentile(counts, 0.9))
    halo_threshold = max(1.0, _percentile(counts, 0.94))
    cycle = 5.4

    edge_segments = []
    for left, right in geometry.edges:
        p1, p2 = geometry.nodes[left], geometry.nodes[right]
        edge_segments.append(f"M {p1.x:.2f} {p1.y:.2f} L {p2.x:.2f} {p2.y:.2f}")
    edge_path = " ".join(edge_segments)

    nodes = []
    for index, (day, point) in enumerate(zip(days, geometry.nodes)):
        radius, opacity = _node_style(day, clip)
        delay = 0.15 + 1.55 * index / 364
        if day.contribution_count >= halo_threshold and day.contribution_count > 0:
            nodes.append(
                f'<circle class="reveal" style="{_reveal_style(0.28, delay)}" cx="{point.x:.2f}" '
                f'cy="{point.y:.2f}" r="{radius + 1.8:.2f}" fill="none" stroke="{theme.signal}" '
                f'stroke-width="0.55" opacity="0.28"/>'
            )
        nodes.append(
            f'<circle class="data-node reveal" style="{_reveal_style(opacity, delay)}" '
            f'data-index="{index}" data-date="{day.date.isoformat()}" data-count="{day.contribution_count}" '
            f'data-level="{day.contribution_level}" cx="{point.x:.2f}" cy="{point.y:.2f}" r="{radius:.2f}" '
            f'fill="{theme.signal}" opacity="{opacity:.3f}"/>'
        )

    pulse_nodes = "".join(
        f'<circle cx="{point.x:.2f}" cy="{point.y:.2f}" r="{_node_style(day, clip)[0] + 0.75:.2f}" fill="{theme.signal}"/>'
        for day, point in zip(days, geometry.nodes)
    )
    pulse = f"""
      <defs>
        <mask id="hero-pulse-mask" maskUnits="userSpaceOnUse" x="360" y="50" width="490" height="320">
          <circle r="27" fill="white" opacity="0">
            <animateMotion id="hero-mask-motion" path="{geometry.route_path}" begin="1.95s;hero-mask-motion.end+4.25s" dur="1.15s" fill="freeze"/>
            <animate id="hero-mask-opacity" attributeName="opacity" values="0;1;1;0" keyTimes="0;0.08;0.82;1"
              begin="1.95s;hero-mask-opacity.end+4.25s" dur="1.15s" fill="freeze"/>
          </circle>
        </mask>
      </defs>
      <g class="motion" mask="url(#hero-pulse-mask)" opacity="0.62">{pulse_nodes}</g>
      <path class="motion" d="{edge_path}" fill="none" stroke="{theme.signal}" stroke-width="1.05"
        mask="url(#hero-pulse-mask)" opacity="0.34"/>
      <path class="motion" d="{geometry.route_path}" fill="none" stroke="{theme.signal}" stroke-width="2.2"
        stroke-linecap="round" pathLength="1" stroke-dasharray="0.055 0.945" opacity="0">
        <animate class="motion" id="hero-pulse-trace" attributeName="stroke-dashoffset" from="0.06" to="-0.94"
          begin="1.95s;hero-pulse-trace.end+4.25s" dur="1.15s" fill="freeze"/>
        <animate id="hero-pulse-trace-opacity" attributeName="opacity" values="0;0.62;0" keyTimes="0;0.08;1"
          begin="1.95s;hero-pulse-trace-opacity.end+4.25s" dur="1.15s" fill="freeze"/>
      </path>
      <circle class="motion" r="5.2" fill="{theme.signal}" opacity="0">
        <animateMotion id="hero-pulse-motion" path="{geometry.route_path}" begin="1.95s;hero-pulse-motion.end+4.25s"
          dur="1.15s" fill="freeze"/>
        <animate attributeName="opacity" values="0;0.72;0" keyTimes="0;0.18;1" dur="1.15s"
          begin="1.95s;hero-pulse-opacity.end+4.25s" id="hero-pulse-opacity" fill="freeze"/>
      </circle>
    """
    network = f"""
      <path class="connection reveal" style="{_reveal_style(0.1, 0.18, 1.25)}" d="{edge_path}" fill="none"
        stroke="{theme.signal_soft}" stroke-width="0.75" opacity="0.1"/>
      <path class="draw" style="--draw-delay:.12s;--draw-duration:1.55s" d="{geometry.route_path}" fill="none"
        stroke="{theme.signal_soft}" stroke-width="0.85" pathLength="1" stroke-dasharray="1" stroke-dashoffset="0" opacity="0.16"/>
    """
    return f'<g id="m-core" data-node-count="365" data-cycle="{cycle:.1f}s" transform="{transform}">{network}{"".join(nodes)}{pulse}</g>'


def render_hero(
    config: Mapping[str, object],
    days: Sequence[ContributionDay],
    weeks: Sequence[ContributionWeek],
    metrics: SignalMetrics,
    geometry: MCoreGeometry,
    theme: Theme,
    mobile: bool = False,
    static: bool = False,
) -> str:
    identity = config["identity"]
    width, height = (360, 650) if mobile else (1000, 420)
    svg = [
        _svg_open(
            width,
            height,
            "Makaren Signal",
            "A 365-day contribution signal forming a network emblem shaped like the letter M.",
            theme,
            static,
        )
    ]
    svg.append(
        f'<path d="M 22 22 H {width - 22} M 22 {height - 22} H {width - 22}" fill="none" stroke="{theme.line}" stroke-width="1"/>'
    )

    if mobile:
        svg.append(
            '<text class="sans copy" x="24" y="54" font-size="25" font-weight="650" letter-spacing="1">MAKAREN SIGNAL</text>'
            '<text class="mono signal" x="336" y="52" font-size="9" text-anchor="end" letter-spacing="1.5">LIVE</text>'
            '<text class="mono label" x="24" y="86">M-CORE / 365 DAYS</text>'
        )
        svg.append(
            _hero_network(days, geometry, theme, "translate(-292 -4) scale(0.8)")
        )
        svg.append(
            f'<g class="reveal" style="{_reveal_style(1, 0.18, 0.45)}" opacity="1">'
            f'<text class="sans copy" x="24" y="378" font-size="27" font-weight="650">{escape(str(identity["handle"]).upper())}</text>'
            f'<text class="sans muted" x="24" y="403" font-size="13">{escape(str(identity["role"]))}</text>'
            f'<text class="mono signal" x="24" y="428" font-size="12">{escape(str(identity["focus"]).upper())}</text>'
            f'<line x1="24" y1="451" x2="336" y2="451" stroke="{theme.line}"/>'
            f'<text class="mono label" x="24" y="477">CONTRIBUTIONS</text><text class="mono copy" x="24" y="499" font-size="17">{metrics.total_contributions:,}</text>'
            f'<text class="mono label" x="180" y="477">ACTIVE DAYS</text><text class="mono copy" x="180" y="499" font-size="17">{metrics.active_days}</text>'
            f'<text class="mono label" x="24" y="533">PEAK WEEK</text><text class="mono copy" x="24" y="555" font-size="17">{metrics.peak_week.total_contributions}</text>'
            f'<text class="mono label" x="180" y="533">SIGNAL ID</text><text class="mono signal" x="180" y="555" font-size="13">{metrics.signal_id}</text>'
            f'<text class="mono muted" x="24" y="593" font-size="10">{metrics.start.isoformat()} → {metrics.end.isoformat()}</text>'
            f'<text class="mono muted" x="336" y="621" font-size="10" text-anchor="end">{escape(str(identity["location"]).upper())} · {escape(str(identity["site"]).upper())}</text></g>'
        )
    else:
        svg.append(
            f'<text class="sans copy" x="48" y="61" font-size="29" font-weight="650" letter-spacing="1.5">MAKAREN SIGNAL</text>'
            f'<text class="mono signal" x="946" y="58" font-size="10" text-anchor="end" letter-spacing="2">LIVE / {metrics.signal_id}</text>'
            f'<text class="mono label" x="398" y="89">M-CORE / DATA-DRIVEN NETWORK EMBLEM</text>'
        )
        svg.append(_hero_network(days, geometry, theme, ""))
        svg.append(
            f'<g class="reveal" style="{_reveal_style(1, 0.12, 0.5)}" opacity="1">'
            f'<text class="sans copy" x="48" y="137" font-size="35" font-weight="650">{escape(str(identity["handle"]).upper())}</text>'
            f'<text class="sans muted" x="48" y="168" font-size="16">{escape(str(identity["role"]))}</text>'
            f'<text class="mono signal" x="48" y="201" font-size="13" letter-spacing="1">{escape(str(identity["focus"]).upper())}</text>'
            f'<line x1="48" y1="232" x2="322" y2="232" stroke="{theme.line}"/>'
            f'<text class="mono label" x="48" y="259">365 DAYS</text><text class="mono copy" x="48" y="286" font-size="19">{metrics.total_contributions:,} CONTRIBUTIONS</text>'
            f'<text class="mono label" x="48" y="319">ACTIVE DAYS</text><text class="mono copy" x="48" y="346" font-size="19">{metrics.active_days}</text>'
            f'<text class="mono label" x="210" y="319">PEAK WEEK</text><text class="mono copy" x="210" y="346" font-size="19">{metrics.peak_week.total_contributions}</text>'
            f'<text class="mono muted" x="48" y="382" font-size="10">{metrics.start.isoformat()} → {metrics.end.isoformat()}</text>'
            f'<text class="mono muted" x="946" y="382" font-size="10" text-anchor="end">{escape(str(identity["location"]).upper())} · {escape(str(identity["site"]).upper())}</text></g>'
        )
    svg.append(_svg_close())
    return "".join(svg)


def render_capabilities(
    config: Mapping[str, object],
    theme: Theme,
    mobile: bool = False,
    static: bool = False,
) -> str:
    capabilities = config["capabilities"]
    items = [
        (str(name).upper(), " · ".join(map(str, values)))
        for name, values in capabilities.items()
    ]
    width, height = (360, 490) if mobile else (1000, 220)
    svg = [
        _svg_open(
            width,
            height,
            "Makaren Signal capabilities",
            "Four technology modules connected to a shared signal backbone.",
            theme,
            static,
        )
    ]
    svg.append(
        f'<text class="sans copy" x="{24 if mobile else 48}" y="{48 if mobile else 51}" font-size="{23 if mobile else 25}" font-weight="650" letter-spacing="1">CAPABILITIES</text>'
    )

    if mobile:
        backbone = "M 42 84 V 444"
        svg.append(
            f'<path class="draw" style="--draw-duration:.65s" d="{backbone}" fill="none" stroke="{theme.signal_soft}" stroke-width="1.2" pathLength="1" stroke-dasharray="1" stroke-dashoffset="0"/>'
        )
        for index, (name, technologies) in enumerate(items):
            y = 112 + index * 88
            delay = 0.45 + index * 0.16
            svg.append(
                f'<g class="reveal" style="{_reveal_style(1, delay, 0.34)}" opacity="1">'
                f'<line x1="42" y1="{y}" x2="72" y2="{y}" stroke="{theme.line}"/>'
                f'<circle cx="42" cy="{y}" r="4" fill="{theme.signal}"/>'
                f'<text class="mono signal" x="82" y="{y - 8}" font-size="11" letter-spacing="1.5">{escape(name)}</text>'
                f'<text class="sans copy" x="82" y="{y + 16}" font-size="14">{escape(technologies)}</text></g>'
            )
    else:
        backbone = "M 84 112 H 916"
        svg.append(
            f'<path class="draw" style="--draw-duration:.65s" d="{backbone}" fill="none" stroke="{theme.signal_soft}" stroke-width="1.2" pathLength="1" stroke-dasharray="1" stroke-dashoffset="0"/>'
        )
        placements = (
            (110, 158, 183, "start"),
            (370, 88, 66, "start"),
            (610, 158, 183, "start"),
            (920, 88, 66, "end"),
        )
        for index, ((name, technologies), placement) in enumerate(
            zip(items, placements)
        ):
            x, label_y, technology_y, anchor = placement
            delay = 0.45 + index * 0.16
            tap_y = 136 if label_y > 112 else 94
            svg.append(
                f'<g class="reveal" style="{_reveal_style(1, delay, 0.34)}" opacity="1">'
                f'<line x1="{x}" y1="112" x2="{x}" y2="{tap_y}" stroke="{theme.line}"/>'
                f'<circle cx="{x}" cy="112" r="4" fill="{theme.signal}"/>'
                f'<text class="mono signal" x="{x}" y="{label_y}" font-size="11" text-anchor="{anchor}" letter-spacing="1.5">{escape(name)}</text>'
                f'<text class="sans copy" x="{x}" y="{technology_y}" font-size="13" text-anchor="{anchor}">{escape(technologies)}</text></g>'
            )
    svg.append(_svg_close())
    return "".join(svg)


def _history_points(
    weeks: Sequence[ContributionWeek], width: int, mobile: bool
) -> list[tuple[float, float]]:
    totals = [week.total_contributions for week in weeks]
    clip = max(1.0, _percentile(totals, 0.9))
    left, right = (28.0, width - 28.0) if mobile else (62.0, width - 62.0)
    baseline = 218.0 if mobile else 174.0
    amplitude = 112.0 if mobile else 92.0
    points = []
    for index, total in enumerate(totals):
        normalized = math.log1p(min(total, clip)) / math.log1p(clip)
        x = left + (right - left) * index / (len(weeks) - 1)
        points.append((x, baseline - normalized * amplitude))
    return points


def _smooth_path(points: Sequence[tuple[float, float]]) -> str:
    if not points:
        return ""
    path = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
    for index in range(len(points) - 1):
        p0 = points[max(0, index - 1)]
        p1 = points[index]
        p2 = points[index + 1]
        p3 = points[min(len(points) - 1, index + 2)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        low, high = sorted((p1[1], p2[1]))
        c1 = (c1[0], min(high, max(low, c1[1])))
        c2 = (c2[0], min(high, max(low, c2[1])))
        path.append(
            f"C {c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} {p2[0]:.2f} {p2[1]:.2f}"
        )
    return " ".join(path)


def render_history(
    weeks: Sequence[ContributionWeek],
    metrics: SignalMetrics,
    theme: Theme,
    mobile: bool = False,
    static: bool = False,
) -> str:
    width, height = (360, 300) if mobile else (1000, 230)
    points = _history_points(weeks, width, mobile)
    path = _smooth_path(points)
    baseline = 218 if mobile else 174
    peak_index = max(
        range(len(weeks)), key=lambda index: weeks[index].total_contributions
    )
    peak = points[peak_index]
    svg = [
        _svg_open(
            width,
            height,
            "Makaren Signal history",
            "A 52-week contribution waveform with peak and current-week markers.",
            theme,
            static,
        )
    ]
    svg.append(
        f'<text class="sans copy" x="{24 if mobile else 48}" y="{46 if mobile else 48}" font-size="{23 if mobile else 25}" font-weight="650" letter-spacing="1">SIGNAL HISTORY</text>'
    )
    svg.append(
        f'<text class="mono muted" x="{width - (24 if mobile else 48)}" y="{68 if mobile else 46}" font-size="9" text-anchor="end" letter-spacing="1">52 WEEKS / ROBUST LOG SCALE</text>'
    )
    svg.append(
        f'<line x1="{points[0][0]:.2f}" y1="{baseline}" x2="{points[-1][0]:.2f}" y2="{baseline}" stroke="{theme.line}"/>'
    )
    area = f"{path} L {points[-1][0]:.2f} {baseline} L {points[0][0]:.2f} {baseline} Z"
    svg.append(
        f'<path class="reveal" style="{_reveal_style(0.055, 0.45, 1.2)}" d="{area}" fill="{theme.signal}" opacity="0.055"/>'
    )
    svg.append(
        f'<path class="draw" style="--draw-duration:1.7s" id="history-wave" data-week-count="52" data-cycle="5.4s" d="{path}" fill="none" stroke="{theme.signal}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" pathLength="1" stroke-dasharray="1" stroke-dashoffset="0"/>'
    )
    for index, ((x, y), week) in enumerate(zip(points, weeks)):
        point_style = _reveal_style(0.5, 0.62 + index * 0.018, 0.26)
        svg.append(
            f'<circle class="weekly-point reveal" style="{point_style}" data-week="{index}" '
            f'data-total="{week.total_contributions}" data-active-days="{week.active_days}" '
            f'cx="{x:.2f}" cy="{y:.2f}" r="1.6" fill="{theme.signal}" opacity="0.5"/>'
        )

    months: set[tuple[int, int]] = set()
    for index, week in enumerate(weeks):
        marker = (week.start.year, week.start.month)
        if marker in months:
            continue
        months.add(marker)
        x = points[index][0]
        svg.append(
            f'<line x1="{x:.2f}" y1="{baseline + 5}" x2="{x:.2f}" y2="{baseline + 10}" stroke="{theme.line}"/>'
        )
        if not mobile or len(months) % 2 == 1:
            svg.append(
                f'<text class="mono muted" x="{x:.2f}" y="{baseline + 26}" font-size="8">{week.start.strftime("%b").upper()}</text>'
            )

    svg.append(
        f'<g class="reveal" style="{_reveal_style(1, 1.42, 0.32)}">'
        f'<circle cx="{peak[0]:.2f}" cy="{peak[1]:.2f}" r="4" fill="{theme.background}" stroke="{theme.signal}" stroke-width="1.4"/>'
        f'<text class="mono signal" x="{peak[0]:.2f}" y="{max(76, peak[1] - 13):.2f}" font-size="8" text-anchor="middle">PEAK {weeks[peak_index].total_contributions}</text>'
        f'<line x1="{points[-1][0]:.2f}" y1="{points[-1][1]:.2f}" x2="{points[-1][0]:.2f}" y2="{baseline}" stroke="{theme.signal_soft}" stroke-dasharray="2 4"/>'
        f'<text class="mono muted" x="{points[-1][0]:.2f}" y="{baseline + 42}" font-size="8" text-anchor="end">CURRENT</text></g>'
    )
    svg.append(
        f'<circle class="motion" r="4.4" fill="{theme.signal}" opacity="0"><animateMotion id="history-pulse-motion" '
        f'path="{path}" begin="2.05s;history-pulse-motion.end+4.4s" dur="1s" fill="freeze"/>'
        f'<animate id="history-pulse-opacity" attributeName="opacity" values="0;0.75;0" keyTimes="0;0.15;1" '
        f'begin="2.05s;history-pulse-opacity.end+4.4s" dur="1s" fill="freeze"/></circle>'
    )
    svg.append(_svg_close())
    return "".join(svg)


def render_systems(
    config: Mapping[str, object],
    theme: Theme,
    mobile: bool = False,
    static: bool = False,
) -> str:
    systems = list(config["connected_systems"])
    width = 360 if mobile else 1000
    height = (150 + len(systems) * 92) if mobile else max(190, 112 + len(systems) * 62)
    svg = [
        _svg_open(
            width,
            height,
            "Makaren connected systems",
            "Manually configured projects and organizations connected to the signal.",
            theme,
            static,
        )
    ]
    svg.append(
        f'<text class="sans copy" x="{24 if mobile else 48}" y="{48 if mobile else 51}" font-size="{23 if mobile else 25}" font-weight="650" letter-spacing="1">CONNECTED SYSTEMS</text>'
    )
    origin_x = 42 if mobile else 86
    start_y = 102 if mobile else 102
    end_y = start_y + max(0, len(systems) - 1) * (82 if mobile else 56)
    svg.append(
        f'<line x1="{origin_x}" y1="{start_y}" x2="{origin_x}" y2="{end_y}" stroke="{theme.signal_soft}"/>'
    )
    for index, system in enumerate(systems):
        y = start_y + index * (82 if mobile else 56)
        text_x = 72 if mobile else 116
        url = escape(str(system["url"]), quote=True)
        name = escape(str(system["name"]).upper())
        description = escape(str(system["description"]).upper())
        display_url = escape(str(system["url"]).removeprefix("https://").upper())
        svg.append(
            f'<g class="system-node" data-system-index="{index}" data-system-url="{url}">'
            f'<circle cx="{origin_x}" cy="{y}" r="5.5" fill="{theme.background}" stroke="{theme.signal}" stroke-width="1.5"/>'
            f'<line x1="{origin_x + 6}" y1="{y}" x2="{text_x - 9}" y2="{y}" stroke="{theme.line}"/>'
            f'<text class="mono copy" x="{text_x}" y="{y - 4}" font-size="{14 if mobile else 15}" font-weight="600" letter-spacing="1">{name}</text>'
            f'<text class="mono muted" x="{text_x}" y="{y + 18}" font-size="9" letter-spacing="1.2">{description}</text>'
            f'<text class="mono signal" x="{text_x}" y="{y + 36}" font-size="8">{display_url}</text></g>'
        )
    svg.append(
        f'<text class="mono muted" x="{width - (24 if mobile else 48)}" y="{height - 28}" font-size="8" text-anchor="end">MANUAL CONFIG / {len(systems):02d} NODE</text>'
    )
    svg.append(_svg_close())
    return "".join(svg)


def render_all(
    output_dir: Path,
    config: Mapping[str, object],
    days: Sequence[ContributionDay],
    weeks: Sequence[ContributionWeek],
    metrics: SignalMetrics,
    geometry: MCoreGeometry,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for theme_name, theme in THEMES.items():
        renderers = {
            "hero": lambda mobile, static: render_hero(
                config, days, weeks, metrics, geometry, theme, mobile, static
            ),
            "capabilities": lambda mobile, static: render_capabilities(
                config, theme, mobile, static
            ),
            "history": lambda mobile, static: render_history(
                weeks, metrics, theme, mobile, static
            ),
            "systems": lambda mobile, static: render_systems(
                config, theme, mobile, static
            ),
        }
        for name, renderer in renderers.items():
            for mobile in (False, True):
                for static in (False, True):
                    suffix = "-mobile" if mobile else ""
                    suffix += "-reduced" if static else ""
                    path = output_dir / f"signal-{name}-{theme_name}{suffix}.svg"
                    content = renderer(mobile, static)
                    normalized = (
                        "\n".join(line.rstrip() for line in content.splitlines()) + "\n"
                    )
                    path.write_text(normalized, encoding="utf-8")
                    written.append(path)
    return written
