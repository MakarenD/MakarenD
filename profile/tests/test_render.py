from __future__ import annotations

import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

from profile.generate import (
    DEFAULT_CONFIG,
    README,
    connected_systems_block,
    generate,
    identity_footer_block,
    load_config,
    sync_readme,
)
from profile.github_data import ContributionDay
from profile.render import THEMES, render_system_node, system_asset_stem


SVG_NS = "{http://www.w3.org/2000/svg}"


def fixture_days() -> list[ContributionDay]:
    start = date(2025, 7, 22)
    levels = [
        "NONE",
        "FIRST_QUARTILE",
        "SECOND_QUARTILE",
        "THIRD_QUARTILE",
        "FOURTH_QUARTILE",
    ]
    return [
        ContributionDay(
            start + timedelta(days=index),
            0 if index % 4 else (index % 17) + 1,
            "NONE" if index % 4 else levels[1 + (index % 4)],
        )
        for index in range(365)
    ]


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name) / "dist"
        days = fixture_days()
        fixture = Path(self.temporary.name) / "days.json"
        fixture.write_text(
            json.dumps({"days": [day.to_json() for day in days]}), encoding="utf-8"
        )
        self.summary = generate(DEFAULT_CONFIG, self.output, fixture, days[-1].date)
        self.paths = sorted(self.output.glob("signal-*.svg"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_all_dark_light_desktop_and_mobile_assets_exist(self) -> None:
        self.assertEqual(32, len(self.paths))
        self.assertEqual(32, self.summary["assets"])
        for section in ("hero", "capabilities", "history"):
            for theme in ("dark", "light"):
                for suffix in ("", "-mobile", "-reduced", "-mobile-reduced"):
                    self.assertTrue(
                        (
                            self.output / f"signal-{section}-{theme}{suffix}.svg"
                        ).is_file()
                    )
        for theme in ("dark", "light"):
            for suffix in ("", "-mobile"):
                self.assertTrue(
                    (
                        self.output / f"signal-systems-header-{theme}{suffix}.svg"
                    ).is_file()
                )
                self.assertTrue(
                    (
                        self.output
                        / f"signal-system-univer-project-{theme}{suffix}.svg"
                    ).is_file()
                )

    def test_svgs_are_accessible_safe_and_numerically_valid(self) -> None:
        numeric_nonnegative = {"width", "height", "r", "rx", "ry", "stroke-width"}
        for path in self.paths:
            source = path.read_text(encoding="utf-8")
            root = ET.fromstring(source)
            self.assertEqual(f"{SVG_NS}svg", root.tag)
            self.assertEqual("100%", root.attrib["width"])
            self.assertEqual("img", root.attrib["role"])
            view_box = [float(value) for value in root.attrib["viewBox"].split()]
            self.assertEqual(4, len(view_box))
            self.assertGreater(view_box[2], 0)
            self.assertGreater(view_box[3], 0)
            self.assertIsNotNone(root.find(f"{SVG_NS}title"))
            self.assertIsNotNone(root.find(f"{SVG_NS}desc"))
            lower = source.lower()
            self.assertTrue(
                all(line == line.rstrip() for line in source.splitlines()), path
            )
            self.assertNotIn("<script", lower)
            self.assertNotIn("javascript:", lower)
            self.assertNotIn("base64", lower)
            self.assertNotIn("<image", lower)
            self.assertNotIn("foreignobject", lower)
            self.assertNotRegex(source, r"\b(?:NaN|Infinity|-Infinity)\b")
            for element in root.iter():
                self.assertFalse(
                    any(name.lower().startswith("on") for name in element.attrib)
                )
                for name in numeric_nonnegative:
                    if name in element.attrib and not element.attrib[name].endswith(
                        "%"
                    ):
                        self.assertGreaterEqual(
                            float(element.attrib[name]), 0, (path, name)
                        )

    def test_hero_contains_365_data_nodes_and_stable_geometry(self) -> None:
        positions = []
        for theme in ("dark", "light"):
            root = ET.parse(self.output / f"signal-hero-{theme}.svg").getroot()
            nodes = [
                element
                for element in root.iter()
                if "data-node" in element.attrib.get("class", "")
            ]
            self.assertEqual(365, len(nodes))
            positions.append([(node.attrib["cx"], node.attrib["cy"]) for node in nodes])
        self.assertEqual(positions[0], positions[1])

    def test_history_contains_52_points(self) -> None:
        root = ET.parse(self.output / "signal-history-dark.svg").getroot()
        points = [
            element
            for element in root.iter()
            if "weekly-point" in element.attrib.get("class", "")
        ]
        self.assertEqual(52, len(points))

    def test_animation_contract_and_reduced_motion_fallback(self) -> None:
        hero = (self.output / "signal-hero-dark.svg").read_text(encoding="utf-8")
        history = (self.output / "signal-history-dark.svg").read_text(encoding="utf-8")
        capabilities = (self.output / "signal-capabilities-dark.svg").read_text(
            encoding="utf-8"
        )
        self.assertIn("prefers-reduced-motion: reduce", hero)
        self.assertIn("hero-pulse-motion", hero)
        self.assertIn('data-cycle="5.4s"', hero)
        self.assertIn('dur="1.15s"', hero)
        self.assertIn('fill="freeze"', hero)
        self.assertIn("@keyframes reveal", hero)
        self.assertIn("animation: none !important", hero)
        reduced = (self.output / "signal-hero-dark-reduced.svg").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(
            reduced.count(".motion { display: none !important; }"), 2
        )
        self.assertIn('data-week-count="52"', history)
        self.assertIn('data-animation="cyclic-reveal"', history)
        self.assertIn('data-build-duration="3.5s"', history)
        self.assertIn('data-hold-duration="5.0s"', history)
        self.assertIn('data-collapse-duration="1.2s"', history)
        self.assertIn('data-pause-duration="0.3s"', history)
        self.assertIn('data-cycle="10.0s"', history)
        phases = [3.5, 5.0, 1.2, 0.3]
        self.assertAlmostEqual(sum(phases), 10.0)
        self.assertIn('id="history-reveal-clip"', history)
        self.assertIn('id="history-reveal-width"', history)
        self.assertIn('values="0;876.00;876.00;0;0"', history)
        self.assertIn('keyTimes="0;0.35;0.85;0.97;1"', history)
        self.assertIn('dur="10s"', history)
        self.assertIn('id="history-scan-front"', history)
        self.assertNotIn("history-pulse-motion", history)
        self.assertNotIn("history-pulse-halo-motion", history)
        self.assertNotIn("history-pulse-trail", history)
        self.assertNotIn("history-pulse-points-mask", history)
        self.assertNotIn("animateMotion", history)
        history_root = ET.fromstring(history)
        by_id = {
            element.attrib["id"]: element
            for element in history_root.iter()
            if "id" in element.attrib
        }
        data = by_id["history-data"]
        self.assertEqual("url(#history-reveal-clip)", data.attrib["clip-path"])
        self.assertNotIn("clip-path", by_id["history-baseline"].attrib)
        self.assertNotIn("clip-path", by_id["history-static"].attrib)
        data_ids = {
            element.attrib.get("id")
            for element in data.iter()
            if "id" in element.attrib
        }
        self.assertTrue(
            {
                "history-area",
                "history-wave",
                "history-peak-marker",
                "history-current-marker",
            }
            <= data_ids
        )
        self.assertEqual(
            52,
            sum(
                "weekly-point" in element.attrib.get("class", "")
                for element in data.iter()
            ),
        )
        static = by_id["history-static"]
        self.assertTrue(
            any(
                "month-label" in element.attrib.get("class", "")
                for element in static.iter()
            )
        )
        reduced_history = (self.output / "signal-history-dark-reduced.svg").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-animation="cyclic-reveal"', reduced_history)
        self.assertNotIn("history-reveal-clip", reduced_history)
        self.assertNotIn("<animate", reduced_history)
        self.assertNotIn("<script", reduced_history.lower())
        reduced_root = ET.fromstring(reduced_history)
        reduced_data = next(
            element
            for element in reduced_root.iter()
            if element.attrib.get("id") == "history-data"
        )
        self.assertEqual(
            52,
            sum(
                "weekly-point" in element.attrib.get("class", "")
                for element in reduced_data.iter()
            ),
        )
        self.assertTrue(
            any(
                element.attrib.get("id") == "history-area"
                for element in reduced_data.iter()
            )
        )
        self.assertIn("--draw-duration:.65s", capabilities)
        cycles = [
            float(value)
            for value in re.findall(r'data-cycle="([0-9.]+)s"', hero + history)
        ]
        self.assertTrue(cycles)
        self.assertIn(10.0, cycles)

    def test_capabilities_keep_python_in_all_variants_without_overflow_text(
        self,
    ) -> None:
        backend = ".NET · C# · Python · Node.js · PHP · Lua"
        for theme in ("dark", "light"):
            for suffix in ("", "-mobile", "-reduced", "-mobile-reduced"):
                source = (
                    self.output / f"signal-capabilities-{theme}{suffix}.svg"
                ).read_text(encoding="utf-8")
                self.assertIn(backend, source)
                self.assertEqual(1, source.count("Python"))
                self.assertNotIn("textLength=", source)

    def test_connected_system_assets_use_config_order_and_safe_filename(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        config["connected_systems"] = [
            {
                "id": "first",
                "kind": "project",
                "name": "First",
                "url": "https://github.com/first",
                "description": "Project",
            },
            {
                "id": "second",
                "kind": "organization",
                "name": "Second",
                "url": "https://github.com/second",
                "description": "Organization",
            },
        ]
        svg = render_system_node(config["connected_systems"][0], THEMES["dark"], 0, 2)
        self.assertEqual("signal-system-first", system_asset_stem("first"))
        self.assertEqual(1, svg.count('class="system-node"'))
        self.assertIn("PROJECT", svg)
        self.assertIn("GITHUB.COM/FIRST", svg)
        self.assertNotIn("SECOND", svg)

    def test_same_input_is_byte_deterministic(self) -> None:
        second = Path(self.temporary.name) / "second"
        days = fixture_days()
        fixture = Path(self.temporary.name) / "days-2.json"
        fixture.write_text(
            json.dumps({"days": [day.to_json() for day in days]}), encoding="utf-8"
        )
        generate(DEFAULT_CONFIG, second, fixture, days[-1].date)
        for path in self.paths:
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())

    def test_readme_systems_are_synced_as_real_links(self) -> None:
        readme = README.read_text(encoding="utf-8")
        systems = connected_systems_block(load_config(DEFAULT_CONFIG))
        self.assertIn(systems, readme)
        self.assertIn('<a href="https://github.com/UNIVER-Project"', readme)
        self.assertIn("signal-system-univer-project-dark.svg?v=2", readme)
        self.assertIn('aria-label="Open UNIVER-Project"', readme)
        self.assertIn('<a href="https://makaren.pro">MAKAREN.PRO</a>', readme)
        self.assertNotIn("↗", readme)
        self.assertEqual(9, readme.count("signal-capabilities-"))
        self.assertEqual(9, readme.count("signal-history-"))
        self.assertNotRegex(
            readme, r"signal-(?:capabilities|history)-[^\" ]+\.svg\?v=(?!3\b)"
        )
        self.assertIn("prefers-reduced-motion: reduce", readme)
        for forbidden in (
            "typing-svg",
            "snake",
            "github-metrics",
            "skillicons",
            "visitor",
            "trophy",
            "streak",
        ):
            self.assertNotIn(forbidden, readme.lower())

    def test_footer_uses_identity_site_and_url_without_arrow(self) -> None:
        config = load_config(DEFAULT_CONFIG)
        identity = dict(config["identity"])
        identity["site"] = "example.test"
        identity["site_url"] = "https://example.test"
        config["identity"] = identity
        footer = identity_footer_block(config)
        self.assertIn('<a href="https://example.test">EXAMPLE.TEST</a>', footer)
        self.assertNotIn("↗", footer)
        self.assertNotIn('target="_blank"', footer)

    def test_readme_sync_is_idempotent_and_updates_only_marker_blocks(self) -> None:
        before = README.read_text(encoding="utf-8")
        self.assertTrue(sync_readme())
        self.assertEqual(before, README.read_text(encoding="utf-8"))
        self.assertTrue(sync_readme(check=True))


if __name__ == "__main__":
    unittest.main()
