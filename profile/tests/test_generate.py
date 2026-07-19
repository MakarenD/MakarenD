from __future__ import annotations

import hashlib
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
import portrait_candidates  # noqa: E402
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
            generate.CropConfig(x=0.08, y=0.0, width=0.84, height=0.96),
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
            generate.CropConfig(x=0.08, y=0.0, width=0.84, height=0.96),
        )

    def test_production_crop_resolves_to_face_focused_pixel_bounds(self) -> None:
        image = Image.new("RGB", (200, 300), "white")
        crop = generate.CropConfig(*generate.DEFAULT_CROP_VALUES)
        cropped = generate.crop_portrait(image, crop)
        self.assertEqual(cropped.size, (168, 192))

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
        self.assertEqual((mosaic.columns, mosaic.rows), (34, 28))

    def test_quantization_uses_four_coarse_opacity_tones(self) -> None:
        mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.crop, variant="gesture-emphasized"
        )
        self.assertEqual(generate.TONE_OPACITIES, (0.34, 0.58, 0.82, 1.0))
        self.assertEqual(mosaic.tone_levels, {1, 2, 3, 4})

    def test_palette_is_limited_to_the_portrait_symbol_vocabulary(self) -> None:
        mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.crop, variant="gesture-emphasized"
        )
        glyphs = {cell.glyph for row in mosaic.cells for cell in row}
        self.assertTrue(glyphs <= generate.PORTRAIT_ALLOWED_GLYPHS)
        self.assertLessEqual(len(glyphs - {" "}), 11)
        self.assertTrue(glyphs & set(generate.PORTRAIT_TONE_GLYPHS))
        self.assertTrue(glyphs & set(generate.PORTRAIT_EDGE_GLYPHS))
        self.assertTrue(glyphs & set(generate.PORTRAIT_DETAIL_GLYPHS))

    def test_committed_gesture_portrait_preserves_recognition_anchors(self) -> None:
        mosaic = generate.load_portrait_mosaic(PROFILE_DIR / "portrait-mosaic.json")
        rows = tuple("".join(cell.glyph for cell in row) for row in mosaic.cells)
        visible_ratio = mosaic.visible_cells / (mosaic.columns * mosaic.rows)
        pupils = [
            (row, column)
            for row, glyph_row in enumerate(rows)
            for column, glyph in enumerate(glyph_row)
            if glyph == "@"
        ]

        self.assertEqual((mosaic.columns, mosaic.rows), (34, 28))
        self.assertTrue(0.20 <= visible_ratio <= 0.38)
        self.assertEqual(len(pupils), 1)
        pupil_row, pupil_column = pupils[0]

        ring_rows = rows[pupil_row - 3 : pupil_row + 4]
        ring_left = "".join(row[pupil_column - 7 : pupil_column] for row in ring_rows)
        ring_right = "".join(
            row[pupil_column + 1 : pupil_column + 8] for row in ring_rows
        )
        self.assertIn("(", ring_left)
        self.assertIn(")", ring_right)
        self.assertTrue(any("-" in row for row in rows[pupil_row - 3 : pupil_row]))
        self.assertTrue(any("-" in row for row in rows[pupil_row + 1 : pupil_row + 4]))

        def visible_in_region(
            row_start: int, row_end: int, column_start: int, column_end: int
        ) -> int:
            return sum(
                glyph != " "
                for row in rows[row_start:row_end]
                for glyph in row[column_start:column_end]
            )

        self.assertGreater(visible_in_region(0, 12, 0, 14), 0)  # raised fingers
        self.assertGreater(visible_in_region(0, 8, 12, 29), 0)  # hair
        self.assertGreater(visible_in_region(18, 25, 9, 27), 0)  # beard
        self.assertGreater(visible_in_region(23, 28, 0, 34), 0)  # hoodie
        self.assertEqual(visible_in_region(0, 10, 28, 34), 0)
        self.assertGreater(1 - visible_ratio, 0.60)

    def test_rejects_portrait_grid_that_would_exceed_the_hero_panel(self) -> None:
        with self.assertRaises(ValueError):
            generate.portrait_mosaic(
                portrait_fixture(), self.crop, variant="edge-first", columns=40
            )

    def test_all_six_portrait_variants_are_available(self) -> None:
        self.assertEqual(
            generate.PORTRAIT_VARIANTS,
            (
                "silhouette-first",
                "edge-first",
                "sparse-tonal",
                "sparse-tonal-contour",
                "foreground-masked",
                "gesture-emphasized",
            ),
        )
        for variant in generate.PORTRAIT_VARIANTS:
            with self.subTest(variant=variant):
                mosaic = generate.portrait_mosaic(
                    portrait_fixture(), self.crop, variant=variant, columns=24
                )
                self.assertEqual(mosaic.variant, variant)

    def test_qa_stage_exposes_eight_vector_stencil_candidates(self) -> None:
        self.assertEqual(
            tuple(preset.name for preset in portrait_candidates.CANDIDATES),
            (
                "stencil-3-tone",
                "stencil-4-tone",
                "dense-glyph-fill",
                "sparse-glyph-fill",
                "contour-emphasis",
                "beard-and-gesture-emphasis",
                "large-glyph-poster",
                "mixed-size-glyphs",
            ),
        )
        self.assertEqual(qa_portraits.MIN_SILHOUETTE_IOU, 0.70)
        self.assertEqual(qa_portraits.MIN_EDGE_OVERLAP, 0.80)

    def test_candidate_svg_uses_paths_only_as_hidden_masks_and_clips(self) -> None:
        signatures = set()
        for preset in portrait_candidates.CANDIDATES:
            themed_text = []
            for theme in portrait_candidates.THEMES.values():
                svg = portrait_candidates.render_candidate_svg(preset, theme)
                portrait_candidates.validate_glyph_vocabulary(svg)
                root = ET.fromstring(svg)
                parent = {
                    child: element for element in root.iter() for child in element
                }
                paths = [
                    element for element in root.iter() if element.tag.endswith("path")
                ]
                texts = [
                    element for element in root.iter() if element.tag.endswith("text")
                ]
                self.assertGreater(len(paths), 10)
                self.assertGreater(len(texts), 100)
                for path in paths:
                    ancestors = []
                    current = path
                    while current in parent:
                        current = parent[current]
                        ancestors.append(current)
                    self.assertTrue(
                        any(node.tag.endswith("defs") for node in ancestors)
                    )
                lowered = svg.lower()
                self.assertNotIn("<image", lowered)
                self.assertNotIn("base64", lowered)
                self.assertNotIn("<script", lowered)
                self.assertNotIn("javascript:", lowered)
                themed_text.append(
                    tuple(
                        (
                            element.attrib.get("x"),
                            element.attrib.get("y"),
                            element.attrib.get("font-size"),
                            element.text,
                        )
                        for element in texts
                    )
                )
            self.assertEqual(themed_text[0], themed_text[1])
            signatures.add(themed_text[0])
        self.assertEqual(len(signatures), 8)

    def test_candidate_source_is_the_exact_reviewed_photo_and_crop(self) -> None:
        self.assertEqual(
            portrait_candidates.EXPECTED_SOURCE_SHA256,
            "9e2480c7723b80734dcb8def7b71ef35abfec83fb7ecbf5a1e57beff4f1712ac",
        )
        self.assertEqual(portrait_candidates.SOURCE_CROP, (0, 100, 1050, 1300))
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.png"
            with self.assertRaises(FileNotFoundError):
                portrait_candidates.load_source(missing)

            mismatched = Path(temp) / "mismatched.png"
            Image.new("RGB", (1122, 1402), "white").save(mismatched)
            with self.assertRaises(ValueError):
                portrait_candidates.load_source(mismatched)

        if portrait_candidates.LOCAL_AVATAR.is_file():
            self.assertEqual(
                portrait_candidates.source_sha256(portrait_candidates.LOCAL_AVATAR),
                portrait_candidates.EXPECTED_SOURCE_SHA256,
            )
            self.assertEqual(portrait_candidates.load_source().size, (1050, 1200))

    def test_derived_mosaic_cache_round_trips(self) -> None:
        mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.crop, variant="gesture-emphasized", columns=24
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "portrait.json"
            generate.save_portrait_mosaic(mosaic, path)
            loaded = generate.load_portrait_mosaic(path)
        self.assertEqual(loaded, mosaic)

    def test_derived_mosaic_cache_rejects_inconsistent_cells_and_size(self) -> None:
        mosaic = generate.portrait_mosaic(
            portrait_fixture(), self.crop, variant="gesture-emphasized", columns=24
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "portrait.json"
            generate.save_portrait_mosaic(mosaic, path)
            valid = json.loads(path.read_text(encoding="utf-8"))

            invalid_payloads: list[tuple[str, dict[str, object]]] = []
            for label, invalid_tone in (("fractional", 1.5), ("string", "1")):
                payload = json.loads(json.dumps(valid))
                payload["tone_rows"][0][0] = invalid_tone
                invalid_payloads.append((label, payload))

            zero_tone_glyph = json.loads(json.dumps(valid))
            zero_tone_glyph["glyph_rows"][0] = (
                "@" + zero_tone_glyph["glyph_rows"][0][1:]
            )
            zero_tone_glyph["tone_rows"][0][0] = 0
            invalid_payloads.append(("zero-tone glyph", zero_tone_glyph))

            positive_tone_space = json.loads(json.dumps(valid))
            positive_tone_space["glyph_rows"][0] = (
                " " + positive_tone_space["glyph_rows"][0][1:]
            )
            positive_tone_space["tone_rows"][0][0] = 1
            invalid_payloads.append(("positive-tone space", positive_tone_space))

            for label, columns, rows in (("wide", 49, 16), ("tall", 16, 31)):
                invalid_payloads.append(
                    (
                        label,
                        {
                            "version": generate.PORTRAIT_CACHE_VERSION,
                            "variant": "gesture-emphasized",
                            "columns": columns,
                            "rows": rows,
                            "glyph_rows": [" " * columns for _ in range(rows)],
                            "tone_rows": [[0] * columns for _ in range(rows)],
                        },
                    )
                )

            for label, invalid_columns in (("string size", "24"), ("float size", 24.0)):
                payload = json.loads(json.dumps(valid))
                payload["columns"] = invalid_columns
                invalid_payloads.append((label, payload))

            for label, payload in invalid_payloads:
                with self.subTest(label=label):
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(generate.GenerationError):
                        generate.load_portrait_mosaic(path)

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

    def test_activity_rendering_is_unchanged_for_both_themes(self) -> None:
        expected = {
            "dark": "e00c66a20eb3c9bc04887ffc574473762b375d6705c5d1a325ee3e77ed562935",
            "light": "958255207ca418b0c4b61b204283df3a73950ebb0de085828a0e0bd261d845b9",
        }
        for theme_name, digest in expected.items():
            with self.subTest(theme=theme_name):
                svg = generate.render_activity(
                    generate.THEMES[theme_name], self.calendar
                )
                self.assertEqual(hashlib.sha256(svg.encode()).hexdigest(), digest)

    def test_svg_contains_true_glyphs_not_a_raster(self) -> None:
        hero = generate.render_hero(generate.THEMES["dark"], self.config, self.mosaic)
        root = ET.fromstring(hero)
        tspans = [element for element in root.iter() if element.tag.endswith("tspan")]
        self.assertGreater(len(tspans), self.mosaic.visible_cells)
        lowered = hero.lower()
        self.assertNotIn("<image", lowered)
        self.assertNotIn("base64", lowered)

    def test_animations_use_repeating_native_clip_reveals(self) -> None:
        self.assertEqual(
            (
                generate.ANIMATION_REVEAL_SECONDS,
                generate.ANIMATION_HOLD_SECONDS,
                generate.ANIMATION_RESET_SECONDS,
                generate.ANIMATION_PAUSE_SECONDS,
                generate.ANIMATION_CYCLE_SECONDS,
            ),
            (1.8, 3.0, 0.3, 0.3, 5.4),
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
        scan_times = generate._animation_key_times(
            0,
            generate.ANIMATION_REVEAL_SECONDS,
            generate.ANIMATION_REVEAL_SECONDS + 0.12,
            generate.ANIMATION_REVEAL_SECONDS
            + generate.ANIMATION_HOLD_SECONDS
            + generate.ANIMATION_RESET_SECONDS,
            generate.ANIMATION_CYCLE_SECONDS,
        )
        self.assertEqual(phase_times, "0;0.333333;0.888889;0.944444;1")
        self.assertEqual(scan_times, "0;0.333333;0.355556;0.944444;1")
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
            self.assertIn(f'keyTimes="{scan_times}"', svg)
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
