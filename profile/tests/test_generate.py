from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image


PROFILE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROFILE_DIR))

import generate  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "contributions.json"


class ConfigurationTests(unittest.TestCase):
    def test_loads_configuration(self) -> None:
        config = generate.load_config(PROFILE_DIR / "config.json")
        self.assertEqual(config["username"], "makaren")
        self.assertEqual(config["github_username"], "MakarenD")

    def test_missing_status_is_supported(self) -> None:
        source = json.loads((PROFILE_DIR / "config.json").read_text(encoding="utf-8"))
        source.pop("status")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            config = generate.load_config(path)
            svg = generate.render_hero(generate.THEMES["dark"], config, ["@#", "[]"])
            self.assertNotIn(">status<", svg)
            self.assertIn(">location<", svg)
            ET.fromstring(svg)


class ConversionTests(unittest.TestCase):
    def test_brightness_maps_across_palette(self) -> None:
        self.assertEqual(generate.brightness_to_ascii(0, "@# "), "@")
        self.assertEqual(generate.brightness_to_ascii(255, "@# "), " ")
        self.assertEqual(generate.brightness_to_ascii(-5, "@# "), "@")
        self.assertEqual(generate.brightness_to_ascii(999, "@# "), " ")

    def test_xml_escape_handles_reserved_characters(self) -> None:
        self.assertEqual(
            generate.xml_escape('<tag a="1">&'), "&lt;tag a=&quot;1&quot;&gt;&amp;"
        )

    def test_contribution_levels(self) -> None:
        self.assertEqual(generate.contribution_level("NONE"), 0)
        self.assertEqual(generate.contribution_level("FIRST_QUARTILE"), 1)
        self.assertEqual(generate.contribution_level("SECOND_QUARTILE"), 2)
        self.assertEqual(generate.contribution_level("THIRD_QUARTILE"), 3)
        self.assertEqual(generate.contribution_level("FOURTH_QUARTILE"), 4)

    def test_portrait_dimensions_are_aspect_corrected(self) -> None:
        image = Image.new("RGB", (100, 80), "white")
        lines = generate.portrait_lines(image, columns=20)
        self.assertEqual(len(lines), 12)
        self.assertTrue(all(len(line) <= 20 for line in lines))


class SvgTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = generate.load_config(PROFILE_DIR / "config.json")
        self.calendar = generate.load_contributions_file(FIXTURE)

    def test_activity_has_one_cell_per_contribution_day(self) -> None:
        svg = generate.render_activity(generate.THEMES["dark"], self.calendar)
        root = ET.fromstring(svg)
        cells = [
            element
            for element in root.iter()
            if element.attrib.get("class") == "activity-cell"
        ]
        self.assertEqual(len(cells), 14)

    def test_animated_content_has_a_visible_static_fallback(self) -> None:
        hero = generate.render_hero(generate.THEMES["dark"], self.config, ["@#"])
        activity = generate.render_activity(generate.THEMES["dark"], self.calendar)
        self.assertIn(".portrait-line,.info-line,.command{opacity:1", hero)
        self.assertIn(".activity-cell{opacity:1", activity)
        self.assertIn("ease-out both", hero)
        self.assertIn("ease-out both", activity)

    def test_all_svg_variants_are_valid_and_safe(self) -> None:
        variants = []
        for theme in generate.THEMES.values():
            variants.append(
                generate.render_hero(theme, self.config, ["@#%", "{}[]<>/\\01"])
            )
            variants.append(generate.render_activity(theme, self.calendar))

        self.assertEqual(len(variants), 4)

        for svg in variants:
            root = ET.fromstring(svg)
            self.assertTrue(root.tag.endswith("svg"))
            self.assertIn("prefers-reduced-motion", svg)
            lowered = svg.lower()
            self.assertNotIn("<script", lowered)
            self.assertNotIn("javascript:", lowered)
            self.assertNotIn("github_token", lowered)
            self.assertNotIn("metrics_token", lowered)
            self.assertNotIn("ghp_", lowered)

            for element in root.iter():
                for attribute in (
                    "x",
                    "y",
                    "width",
                    "height",
                    "rx",
                    "ry",
                    "cx",
                    "cy",
                    "r",
                ):
                    value = element.attrib.get(attribute)
                    if value and value != "100%":
                        self.assertGreaterEqual(
                            float(value), 0, f"negative {attribute} in {element.tag}"
                        )

    def test_full_generation_writes_dark_and_light_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            avatar = temp_path / "avatar.png"
            Image.new("RGB", (64, 64), "#808080").save(avatar)
            output = temp_path / "dist"
            original = os.environ.pop("PROFILE_GITHUB_TOKEN", None)
            try:
                paths = generate.generate_assets(
                    PROFILE_DIR / "config.json",
                    output,
                    contributions_file=FIXTURE,
                    avatar_path=avatar,
                )
            finally:
                if original is not None:
                    os.environ["PROFILE_GITHUB_TOKEN"] = original

            self.assertEqual(
                {path.name for path in paths},
                {
                    "hero-dark.svg",
                    "hero-light.svg",
                    "activity-dark.svg",
                    "activity-light.svg",
                },
            )
            for path in paths:
                ET.parse(path)
                content = path.read_text(encoding="utf-8").lower()
                self.assertNotIn("token", content)
                self.assertNotIn("secret", content)


class GraphQlTests(unittest.TestCase):
    def test_extracts_graphql_calendar(self) -> None:
        calendar = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload = {
            "data": {
                "user": {"contributionsCollection": {"contributionCalendar": calendar}}
            }
        }
        self.assertEqual(generate.extract_calendar(payload)["totalContributions"], 26)


if __name__ == "__main__":
    unittest.main()
