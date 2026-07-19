from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw


PROFILE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROFILE_DIR))

import generate  # noqa: E402
import qa_portraits  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "contributions.json"


def portrait_fixture() -> Image.Image:
    image = Image.new("RGB", (180, 240), "#f4f4f4")
    draw = ImageDraw.Draw(image)
    draw.ellipse((38, 26, 156, 190), fill="#8b8b8b", outline="#202020", width=5)
    draw.ellipse((67, 84, 82, 95), fill="#101010")
    draw.ellipse((112, 84, 127, 95), fill="#101010")
    draw.line((97, 92, 91, 126, 103, 128), fill="#303030", width=4)
    draw.arc((72, 116, 124, 156), 15, 165, fill="#202020", width=4)
    draw.rectangle((47, 152, 148, 220), fill="#303030")
    return image


class ConfigurationTests(unittest.TestCase):
    def test_loads_crop_configuration(self) -> None:
        config = generate.load_config(PROFILE_DIR / "config.json")
        self.assertEqual(config["username"], "makaren")
        self.assertEqual(config["github_username"], "MakarenD")
        self.assertEqual(
            config["portrait"]["crop"],
            generate.CropConfig(x=0.28, y=0.02, width=0.64, height=0.86),
        )

    def test_absent_crop_uses_default(self) -> None:
        source = json.loads((PROFILE_DIR / "config.json").read_text(encoding="utf-8"))
        source.pop("portrait")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            config = generate.load_config(path)
        self.assertEqual(
            config["portrait"]["crop"],
            generate.CropConfig(x=0.28, y=0.02, width=0.64, height=0.86),
        )

    def test_production_crop_resolves_to_face_focused_pixel_bounds(self) -> None:
        image = Image.new("RGB", (200, 300), "white")
        crop = generate.CropConfig(*generate.DEFAULT_CROP_VALUES)
        cropped = generate.crop_portrait(image, crop)
        self.assertEqual(cropped.size, (128, 172))

    def test_missing_status_is_supported(self) -> None:
        source = json.loads((PROFILE_DIR / "config.json").read_text(encoding="utf-8"))
        source.pop("status")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            config = generate.load_config(path)
        mosaic = generate.portrait_mosaic(
            portrait_fixture(), config["portrait"]["crop"], columns=24
        )
        svg = generate.render_hero(generate.THEMES["dark"], config, mosaic)
        self.assertNotIn(">status<", svg)
        self.assertIn(">location<", svg)
        ET.fromstring(svg)

    def test_rejects_invalid_crop_values(self) -> None:
        invalid = (
            "not-an-object",
            {"x": 0, "y": 0, "width": 1},
            {"x": True, "y": 0, "width": 1, "height": 1},
            {"x": -0.1, "y": 0, "width": 1, "height": 1},
            {"x": 0, "y": 0, "width": 0, "height": 1},
            {"x": 0.4, "y": 0, "width": 0.7, "height": 1},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(generate.GenerationError):
                generate.parse_crop_config(value)


class PortraitPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.crop = generate.CropConfig(*generate.DEFAULT_CROP_VALUES)

    def test_brightness_maps_across_palette(self) -> None:
        self.assertEqual(generate.brightness_to_ascii(0, "@# "), "@")
        self.assertEqual(generate.brightness_to_ascii(255, "@# "), " ")
        self.assertEqual(generate.brightness_to_ascii(-5, "@# "), "@")
        self.assertEqual(generate.brightness_to_ascii(999, "@# "), " ")

    def test_xml_escape_handles_reserved_characters(self) -> None:
        self.assertEqual(
            generate.xml_escape('<tag a="1">&'), "&lt;tag a=&quot;1&quot;&gt;&amp;"
        )

    def test_portrait_dimensions_are_character_aspect_corrected(self) -> None:
        mosaic = generate.portrait_mosaic(portrait_fixture(), self.crop)
        self.assertEqual((mosaic.columns, mosaic.rows), (48, 34))

    def test_quantization_uses_five_coarse_opacity_tones(self) -> None:
        mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.crop, variant="combined-tone-edge"
        )
        self.assertGreaterEqual(len(mosaic.tone_levels), 3)
        self.assertLessEqual(len(mosaic.tone_levels), 5)
        self.assertTrue(mosaic.tone_levels <= set(range(1, 6)))

    def test_palette_is_limited_and_combines_tone_with_directional_edges(self) -> None:
        mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.crop, variant="combined-tone-edge"
        )
        glyphs = {cell.glyph for row in mosaic.cells for cell in row}
        allowed = (
            set(generate.PORTRAIT_TONE_GLYPHS)
            | set(generate.PORTRAIT_EDGE_GLYPHS)
            | {" "}
        )
        self.assertTrue(glyphs <= allowed)
        self.assertLessEqual(len(glyphs - {" "}), 9)
        self.assertTrue(glyphs & set(generate.PORTRAIT_TONE_GLYPHS))
        self.assertTrue(glyphs & set(generate.PORTRAIT_EDGE_GLYPHS))

    def test_combined_pipeline_suppresses_bright_flat_background(self) -> None:
        flat = Image.new("RGB", (180, 240), "white")
        flat_mosaic = generate.portrait_mosaic(flat, self.crop, columns=66)
        subject_mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.crop, columns=66
        )
        self.assertLess(flat_mosaic.visible_cells, subject_mosaic.visible_cells // 4)

    def test_all_six_portrait_variants_are_available(self) -> None:
        for variant in generate.PORTRAIT_VARIANTS:
            with self.subTest(variant=variant):
                mosaic = generate.portrait_mosaic(
                    portrait_fixture(), self.crop, variant=variant, columns=24
                )
                self.assertEqual(mosaic.variant, variant)

    def test_qa_presets_cover_real_density_crop_and_palette_variants(self) -> None:
        presets = qa_portraits.portrait_presets(self.crop)
        self.assertEqual(
            tuple(preset.name for preset in presets), generate.PORTRAIT_VARIANTS
        )
        self.assertLess(presets[0].columns, generate.PORTRAIT_COLUMNS)
        self.assertEqual(presets[1].crop, self.crop)
        self.assertEqual(presets[-1].columns, generate.PORTRAIT_COLUMNS)
        self.assertEqual(presets[-1].crop, self.crop)

        image = portrait_fixture()
        mosaics = [
            generate.portrait_mosaic(
                image,
                preset.crop,
                variant=preset.name,
                columns=preset.columns,
            )
            for preset in presets
        ]
        signatures = {
            (
                mosaic.columns,
                mosaic.rows,
                tuple("".join(cell.glyph for cell in row) for row in mosaic.cells),
            )
            for mosaic in mosaics
        }
        self.assertEqual(len(signatures), len(presets))

        simplified = mosaics[4]
        simplified_tonal_glyphs = {
            cell.glyph
            for row in simplified.cells
            for cell in row
            if cell.glyph not in generate.PORTRAIT_EDGE_GLYPHS and cell.glyph != " "
        }
        self.assertTrue(simplified_tonal_glyphs <= set(generate.SIMPLIFIED_TONE_GLYPHS))

    def test_derived_mosaic_cache_round_trips(self) -> None:
        mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.crop, variant="combined-tone-edge", columns=24
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "portrait.json"
            generate.save_portrait_mosaic(mosaic, path)
            loaded = generate.load_portrait_mosaic(path)
        self.assertEqual(loaded, mosaic)

    def test_local_avatar_precedes_github_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp) / "avatar-source.png"
            Image.new("RGB", (12, 12), "#123456").save(local)
            with mock.patch.object(generate, "_request") as request:
                avatar, source = generate.load_avatar("someone", local)
        request.assert_not_called()
        self.assertEqual(source, "local avatar source")
        self.assertEqual(avatar.getpixel((0, 0)), (18, 52, 86))

    def test_github_avatar_is_used_when_local_source_is_absent(self) -> None:
        encoded = BytesIO()
        Image.new("RGB", (8, 8), "#abcdef").save(encoded, format="PNG")
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(
                generate, "_request", return_value=encoded.getvalue()
            ) as request,
        ):
            avatar, source = generate.load_avatar(
                "someone", Path(temp) / "missing-avatar.png"
            )
        request.assert_called_once_with("https://github.com/someone.png?size=920")
        self.assertEqual(source, "GitHub avatar")
        self.assertEqual(avatar.size, (8, 8))


class SvgTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = generate.load_config(PROFILE_DIR / "config.json")
        self.calendar = generate.load_contributions_file(FIXTURE)
        self.mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.config["portrait"]["crop"], columns=24
        )

    def test_contribution_levels(self) -> None:
        self.assertEqual(generate.contribution_level("NONE"), 0)
        self.assertEqual(generate.contribution_level("FIRST_QUARTILE"), 1)
        self.assertEqual(generate.contribution_level("SECOND_QUARTILE"), 2)
        self.assertEqual(generate.contribution_level("THIRD_QUARTILE"), 3)
        self.assertEqual(generate.contribution_level("FOURTH_QUARTILE"), 4)

    def test_activity_has_separate_static_and_colored_layers(self) -> None:
        svg = generate.render_activity(generate.THEMES["dark"], self.calendar)
        root = ET.fromstring(svg)
        base_cells = [
            element
            for element in root.iter()
            if element.attrib.get("class") == "base-cell"
        ]
        colored_cells = [
            element
            for element in root.iter()
            if element.attrib.get("class") == "colored-cell"
        ]
        self.assertEqual(len(base_cells), 14)
        self.assertGreater(len(colored_cells), 0)
        self.assertIn('id="activity-static-base-grid"', svg)
        self.assertIn('id="activity-colored-reveal-layer"', svg)

    def test_svg_contains_true_glyphs_not_a_raster(self) -> None:
        hero = generate.render_hero(generate.THEMES["dark"], self.config, self.mosaic)
        root = ET.fromstring(hero)
        tspans = [element for element in root.iter() if element.tag.endswith("tspan")]
        self.assertGreater(len(tspans), self.mosaic.visible_cells)
        lowered = hero.lower()
        self.assertNotIn("<image", lowered)
        self.assertNotIn("base64", lowered)

    def test_animations_use_repeating_native_clip_reveals(self) -> None:
        self.assertTrue(1.6 <= generate.ANIMATION_REVEAL_SECONDS <= 2.0)
        self.assertTrue(2.5 <= generate.ANIMATION_HOLD_SECONDS <= 3.5)
        self.assertTrue(0.2 <= generate.ANIMATION_RESET_SECONDS <= 0.4)
        self.assertTrue(0.2 <= generate.ANIMATION_PAUSE_SECONDS <= 0.4)
        self.assertTrue(4.5 <= generate.ANIMATION_CYCLE_SECONDS <= 6.0)
        self.assertAlmostEqual(
            generate.ANIMATION_CYCLE_SECONDS,
            generate.ANIMATION_REVEAL_SECONDS
            + generate.ANIMATION_HOLD_SECONDS
            + generate.ANIMATION_RESET_SECONDS
            + generate.ANIMATION_PAUSE_SECONDS,
        )

        variants: list[str] = []
        for theme in generate.THEMES.values():
            variants.append(generate.render_hero(theme, self.config, self.mosaic))
            variants.append(generate.render_activity(theme, self.calendar))

        phase_times = generate._animation_key_times(
            0,
            generate.ANIMATION_REVEAL_SECONDS,
            generate.ANIMATION_REVEAL_SECONDS + generate.ANIMATION_HOLD_SECONDS,
            generate.ANIMATION_REVEAL_SECONDS
            + generate.ANIMATION_HOLD_SECONDS
            + generate.ANIMATION_RESET_SECONDS,
            generate.ANIMATION_CYCLE_SECONDS,
        )
        for svg in variants:
            self.assertIn("<clipPath", svg)
            self.assertIn('repeatCount="indefinite"', svg)
            self.assertIn("prefers-reduced-motion: reduce", svg)
            durations = [float(value) for value in re.findall(r'dur="([0-9.]+)s"', svg)]
            self.assertTrue(durations)
            self.assertTrue(
                all(
                    duration == generate.ANIMATION_CYCLE_SECONDS
                    for duration in durations
                )
            )
            self.assertIn(f'keyTimes="{phase_times}"', svg)
            self.assertNotIn("<script", svg.lower())

        hero, activity = variants[:2]
        self.assertIn("portrait-reduced-final", hero)
        self.assertIn('href="#portrait-glyphs-dark"', hero)
        self.assertIn(".portrait-motion-layer{display:none}", hero)
        self.assertIn("activity-reduced-final", activity)
        self.assertIn('href="#activity-colored-cells-dark"', activity)
        self.assertIn(".activity-motion-layer{display:none}", activity)

    def test_all_svg_variants_are_valid_safe_and_nonnegative(self) -> None:
        variants: list[str] = []
        for theme in generate.THEMES.values():
            variants.append(generate.render_hero(theme, self.config, self.mosaic))
            variants.append(generate.render_activity(theme, self.calendar))

        self.assertEqual(len(variants), 4)
        for svg in variants:
            root = ET.fromstring(svg)
            self.assertTrue(root.tag.endswith("svg"))
            lowered = svg.lower()
            for forbidden in (
                "<script",
                "javascript:",
                "github_token",
                "metrics_token",
                "ghp_",
                "data:image",
                "base64",
            ):
                self.assertNotIn(forbidden, lowered)

            for element in root.iter():
                for attribute in (
                    "x",
                    "y",
                    "x1",
                    "y1",
                    "x2",
                    "y2",
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

    def test_full_generation_writes_dark_and_light_outputs_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            cache = temp_path / "portrait.json"
            generate.save_portrait_mosaic(self.mosaic, cache)
            output = temp_path / "dist"
            original = os.environ.pop("PROFILE_GITHUB_TOKEN", None)
            try:
                paths = generate.generate_assets(
                    PROFILE_DIR / "config.json",
                    output,
                    contributions_file=FIXTURE,
                    portrait_cache=cache,
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
