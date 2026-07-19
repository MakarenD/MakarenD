#!/usr/bin/env python3
"""Generate a local six-variant portrait contact sheet for visual review."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageOps

from generate import (
    LOCAL_AVATAR,
    PORTRAIT_COLUMNS,
    PORTRAIT_VARIANTS,
    THEMES,
    CropConfig,
    PortraitMosaic,
    crop_portrait,
    load_avatar,
    load_config,
    portrait_mosaic,
    render_portrait_glyphs,
    xml_escape,
)


DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "qa-artifacts" / "portraits"
WIDE_COMPARISON_CROP = CropConfig(x=0.1, y=0.06, width=0.8, height=0.9)


@dataclass(frozen=True)
class PortraitPreset:
    """One intentionally distinct local portrait comparison."""

    name: str
    crop: CropConfig
    columns: int


def portrait_presets(selected_crop: CropConfig) -> tuple[PortraitPreset, ...]:
    """Cover density, crop, edges, background, palette, and compositing."""

    presets = (
        PortraitPreset("reduced-density", WIDE_COMPARISON_CROP, 42),
        PortraitPreset("stronger-crop", selected_crop, 52),
        PortraitPreset("edge-enhanced", WIDE_COMPARISON_CROP, 52),
        PortraitPreset("background-suppressed", WIDE_COMPARISON_CROP, 52),
        PortraitPreset("simplified-palette", WIDE_COMPARISON_CROP, 52),
        PortraitPreset("combined-tone-edge", selected_crop, PORTRAIT_COLUMNS),
    )
    if tuple(preset.name for preset in presets) != PORTRAIT_VARIANTS:
        raise RuntimeError("Portrait QA presets must cover every pipeline variant")
    return presets


def preview_svg(variant: str, mosaic: PortraitMosaic) -> str:
    """Render one dark-theme mosaic at the same size as the README portrait panel."""

    theme = THEMES["dark"]
    group_id = f"preview-{variant}"
    glyphs = render_portrait_glyphs(mosaic, theme, group_id=group_id, x=20, y=38)
    return "".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="452" height="318" viewBox="0 0 452 318">\n',
            f'  <rect width="452" height="318" rx="14" fill="{theme.panel}" stroke="{theme.border}"/>\n',
            f'  <text x="20" y="21" fill="{theme.muted}" font-family="ui-monospace,monospace" font-size="11">{xml_escape(variant)}</text>\n',
            "  <defs>\n",
            glyphs,
            "  </defs>\n",
            f'  <use href="#{group_id}"/>\n',
            "</svg>\n",
        ]
    )


def build_contact_sheet(config_path: Path, avatar_path: Path, output_dir: Path) -> Path:
    """Write source crop, six SVG variants, metadata, and an HTML comparison page."""

    config = load_config(config_path)
    avatar, source_name = load_avatar(config["github_username"], avatar_path)
    crop = config["portrait"]["crop"]
    cropped = crop_portrait(avatar, crop)

    output_dir.mkdir(parents=True, exist_ok=True)
    source_preview = ImageOps.contain(cropped, (452, 318))
    source_preview.save(output_dir / "source-preview.png")

    cards = [
        '<article class="card"><h2>source crop</h2><div class="source"><img src="source-preview.png" alt="Cropped source portrait"></div></article>'
    ]
    summary: dict[str, object] = {
        "source": source_name,
        "crop": {
            "x": crop.x,
            "y": crop.y,
            "width": crop.width,
            "height": crop.height,
        },
        "variants": {},
    }
    for preset in portrait_presets(crop):
        mosaic = portrait_mosaic(
            avatar,
            preset.crop,
            variant=preset.name,
            columns=preset.columns,
        )
        svg = preview_svg(preset.name, mosaic)
        svg_path = output_dir / f"{preset.name}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        cards.append(
            f'<article class="card"><h2>{xml_escape(preset.name)}</h2><img src="{svg_path.name}" alt="{xml_escape(preset.name)} portrait variant"></article>'
        )
        summary["variants"][preset.name] = {
            "columns": mosaic.columns,
            "rows": mosaic.rows,
            "visible_cells": mosaic.visible_cells,
            "tone_levels": sorted(mosaic.tone_levels),
            "crop": {
                "x": preset.crop.x,
                "y": preset.crop.y,
                "width": preset.crop.width,
                "height": preset.crop.height,
            },
        }

    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Portrait pipeline variants</title>
  <style>
    :root { color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    body { margin: 0; padding: 24px; background: #0d1117; color: #e6edf3; }
    header { max-width: 1400px; margin: 0 auto 20px; }
    h1 { margin: 0 0 8px; font-size: 20px; color: #39c5cf; }
    p { margin: 0; color: #8b949e; }
    main { display: grid; grid-template-columns: repeat(2, 452px); gap: 20px; width: 924px; margin: 0 auto; }
    .card { margin: 0; }
    h2 { margin: 0 0 8px; color: #8b949e; font-size: 12px; font-weight: 500; text-transform: uppercase; letter-spacing: .08em; }
    .card > img, .source { display: block; width: 452px; height: 318px; border-radius: 14px; }
    .source { background: #161b22; border: 1px solid #30363d; box-sizing: border-box; display: grid; place-items: center; overflow: hidden; }
    .source img { max-width: 100%; max-height: 100%; object-fit: contain; }
  </style>
</head>
<body>
  <header><h1>portrait.pipeline</h1><p>Source crop + six variants at the README portrait-panel size (452 × 318).</p></header>
  <main>CARDS</main>
</body>
</html>
""".replace("CARDS", "\n".join(cards))
    contact_sheet = output_dir / "contact-sheet.html"
    contact_sheet.write_text(html, encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return contact_sheet


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).parent / "config.json"
    )
    parser.add_argument("--avatar-source", type=Path, default=LOCAL_AVATAR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = build_contact_sheet(args.config, args.avatar_source, args.output)
    print(result)
