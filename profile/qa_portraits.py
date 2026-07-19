#!/usr/bin/env python3
"""Generate eight vector-stencil + glyph-fill portrait candidates for review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from portrait_candidates import (
    CANDIDATES,
    EXPECTED_SOURCE_SHA256,
    LOCAL_AVATAR,
    PANEL_SIZE,
    SOURCE_CROP,
    THEMES,
    compare_with_stencil,
    load_source,
    rasterize_svg,
    render_candidate_svg,
    render_stencil_svg,
    source_sha256,
    validate_glyph_vocabulary,
)


DEFAULT_OUTPUT = Path(__file__).resolve().parent / "qa"
CARD_GAP = 24
LABEL_HEIGHT = 34
CONTACT_COLUMNS = 3
MIN_SILHOUETTE_IOU = 0.70
MIN_EDGE_OVERLAP = 0.80


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _source_panel(source_crop: Image.Image) -> Image.Image:
    theme = THEMES["dark"]
    panel = Image.new("RGB", PANEL_SIZE, theme.panel)
    fitted = ImageOps.contain(source_crop, (PANEL_SIZE[0] - 24, PANEL_SIZE[1] - 24))
    panel.paste(
        fitted,
        ((PANEL_SIZE[0] - fitted.width) // 2, (PANEL_SIZE[1] - fitted.height) // 2),
    )
    draw = ImageDraw.Draw(panel)
    draw.rounded_rectangle(
        (0, 0, PANEL_SIZE[0] - 1, PANEL_SIZE[1] - 1),
        radius=14,
        outline=theme.border,
    )
    return panel


def _write_candidate_assets(
    output_dir: Path,
) -> tuple[list[dict[str, object]], Image.Image]:
    candidates_dir = output_dir / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    stencil = rasterize_svg(render_stencil_svg())
    stencil.save(output_dir / "stencil-reference.png", optimize=True)

    summaries: list[dict[str, object]] = []
    for number, preset in enumerate(CANDIDATES, start=1):
        theme_files: dict[str, dict[str, str]] = {}
        metrics: dict[str, dict[str, float]] = {}
        for theme_name, theme in THEMES.items():
            svg = render_candidate_svg(preset, theme)
            validate_glyph_vocabulary(svg)
            stem = f"{number:02d}-{preset.name}-{theme_name}"
            svg_path = candidates_dir / f"{stem}.svg"
            png_path = candidates_dir / f"{stem}.png"
            svg_path.write_text(svg, encoding="utf-8")
            rendered = rasterize_svg(svg)
            rendered.save(png_path, optimize=True)
            theme_files[theme_name] = {
                "svg": f"candidates/{svg_path.name}",
                "png": f"candidates/{png_path.name}",
            }
            theme_stencil = (
                stencil
                if theme_name == "dark"
                else rasterize_svg(render_stencil_svg(theme))
            )
            metrics[theme_name] = compare_with_stencil(
                rendered, theme_stencil, panel=theme.panel
            )

        if any(
            score["silhouette_iou"] < MIN_SILHOUETTE_IOU
            or score["edge_overlap"] < MIN_EDGE_OVERLAP
            for score in metrics.values()
        ):
            raise RuntimeError(
                f"Candidate {number} ({preset.name}) failed the stencil rejection gate: {metrics}"
            )
        summaries.append(
            {
                "number": number,
                "name": preset.name,
                "description": preset.description,
                "tone_count": preset.tone_count,
                "files": theme_files,
                "metrics": metrics,
            }
        )
    return summaries, stencil


def _build_contact_sheet(
    output_dir: Path,
    source_panel: Image.Image,
    stencil: Image.Image,
    summaries: list[dict[str, object]],
) -> Path:
    cards: list[tuple[str, Image.Image]] = [
        ("SOURCE · fixed crop", source_panel),
        ("STENCIL · 4 semantic tones", stencil),
    ]
    for candidate in summaries:
        number = int(candidate["number"])
        name = str(candidate["name"])
        files = candidate["files"]
        assert isinstance(files, dict)
        for theme_name in ("dark", "light"):
            theme_files = files[theme_name]
            assert isinstance(theme_files, dict)
            image = Image.open(output_dir / str(theme_files["png"])).convert("RGB")
            cards.append((f"{number}. {name} · {theme_name}", image))

    rows = (len(cards) + CONTACT_COLUMNS - 1) // CONTACT_COLUMNS
    width = CONTACT_COLUMNS * PANEL_SIZE[0] + (CONTACT_COLUMNS + 1) * CARD_GAP
    header_height = 78
    card_height = LABEL_HEIGHT + PANEL_SIZE[1]
    height = header_height + rows * card_height + (rows + 1) * CARD_GAP
    canvas = Image.new("RGB", (width, height), "#0d1117")
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (CARD_GAP, 18),
        "PORTRAIT CANDIDATES · VECTOR STENCIL + GLYPH FILL",
        fill="#39c5cf",
        font=_font(22),
    )
    draw.text(
        (CARD_GAP, 49),
        "Each preview is the real 452 × 318 README portrait-panel size. No candidate is selected.",
        fill="#8b949e",
        font=_font(13),
    )
    label_font = _font(13)
    for index, (label, image) in enumerate(cards):
        row, column = divmod(index, CONTACT_COLUMNS)
        x = CARD_GAP + column * (PANEL_SIZE[0] + CARD_GAP)
        y = header_height + CARD_GAP + row * (card_height + CARD_GAP)
        draw.text((x, y + 7), label.upper(), fill="#8b949e", font=label_font)
        canvas.paste(image, (x, y + LABEL_HEIGHT))

    path = output_dir / "portrait-candidates.png"
    canvas.save(path, optimize=True)
    return path


def _build_html(
    output_dir: Path,
    summaries: list[dict[str, object]],
) -> Path:
    cards = [
        """<article class="reference"><h2>Source · fixed crop</h2><img src="source-preview.png" alt="Fixed crop of the source portrait"></article>""",
        """<article class="reference"><h2>Stencil · four semantic tones</h2><img src="stencil-reference.png" alt="Four-tone vector stencil reference"></article>""",
    ]
    for candidate in summaries:
        number = int(candidate["number"])
        name = str(candidate["name"])
        description = str(candidate["description"])
        files = candidate["files"]
        metrics = candidate["metrics"]
        assert isinstance(files, dict) and isinstance(metrics, dict)
        dark = files["dark"]
        light = files["light"]
        assert isinstance(dark, dict) and isinstance(light, dict)
        dark_metrics = metrics["dark"]
        light_metrics = metrics["light"]
        assert isinstance(dark_metrics, dict) and isinstance(light_metrics, dict)
        cards.append(
            f'''<article class="candidate" id="candidate-{number}">
  <header><div><span class="number">{number:02d}</span><h2>{name}</h2></div><p>{description}</p></header>
  <div class="themes">
    <figure><img src="{dark["svg"]}" alt="Candidate {number}, dark theme"><figcaption>dark · IoU {dark_metrics["silhouette_iou"]:.3f} · edge {dark_metrics["edge_overlap"]:.3f}</figcaption></figure>
    <figure><img src="{light["svg"]}" alt="Candidate {number}, light theme"><figcaption>light · IoU {light_metrics["silhouette_iou"]:.3f} · edge {light_metrics["edge_overlap"]:.3f}</figcaption></figure>
  </div>
</article>'''
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Portrait candidate gallery</title>
  <style>
    :root {{ color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 28px; background: #0d1117; color: #e6edf3; }}
    body > header, main {{ width: min(100%, 972px); margin-inline: auto; }}
    body > header {{ margin-bottom: 24px; }}
    h1 {{ margin: 0 0 8px; color: #39c5cf; font-size: 22px; }}
    p {{ color: #8b949e; }}
    .reference, .candidate {{ margin: 0 0 28px; }}
    .reference {{ display: inline-block; width: 452px; vertical-align: top; margin-right: 16px; }}
    h2 {{ margin: 0; font-size: 14px; }}
    .reference h2 {{ margin-bottom: 10px; color: #8b949e; text-transform: uppercase; }}
    .reference img, figure img {{ display: block; width: 452px; height: 318px; }}
    .candidate {{ border-top: 1px solid #30363d; padding-top: 22px; }}
    .candidate > header {{ display: flex; align-items: end; justify-content: space-between; gap: 24px; margin-bottom: 12px; }}
    .candidate > header div {{ display: flex; align-items: center; gap: 12px; }}
    .candidate > header p {{ margin: 0; max-width: 560px; text-align: right; font-size: 12px; }}
    .number {{ color: #39c5cf; font-size: 20px; }}
    .themes {{ display: grid; grid-template-columns: repeat(2, 452px); gap: 16px; }}
    figure {{ margin: 0; }}
    figcaption {{ margin-top: 7px; color: #8b949e; font-size: 11px; }}
    @media (max-width: 980px) {{
      body {{ padding: 16px; }}
      .reference {{ display: block; width: 100%; margin-right: 0; }}
      .reference img, figure img {{ width: 163px; height: auto; }}
      .themes {{ grid-template-columns: 1fr; }}
      .candidate > header {{ display: block; }}
      .candidate > header p {{ text-align: left; margin-top: 8px; }}
    }}
  </style>
</head>
<body>
  <header><h1>portrait.candidates</h1><p>Eight vector-stencil + glyph-fill variants. Dark and light are shown at the real README panel size; no final candidate is selected.</p></header>
  <main>{"".join(cards)}</main>
</body>
</html>
"""
    path = output_dir / "portrait-candidates.html"
    path.write_text(html, encoding="utf-8")
    return path


def _capture_browser_gallery(html_path: Path, output_dir: Path) -> list[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Install profile/qa-requirements.txt to capture browser gallery screenshots"
        ) from exc

    captures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.wait_for_function(
                "() => [...document.images].every(image => image.complete && image.naturalWidth > 0)"
            )
            desktop = output_dir / "portrait-gallery-desktop.png"
            page.screenshot(path=str(desktop), full_page=True)
            captures.append(desktop.name)

            page.set_viewport_size({"width": 390, "height": 844})
            page.reload(wait_until="load")
            page.wait_for_function(
                "() => [...document.images].every(image => image.complete && image.naturalWidth > 0)"
            )
            mobile = output_dir / "portrait-gallery-mobile.png"
            page.screenshot(path=str(mobile), full_page=True)
            captures.append(mobile.name)
        finally:
            browser.close()
    for capture in captures:
        with Image.open(output_dir / capture) as image:
            image.save(output_dir / capture, optimize=True)
    return captures


def build_gallery(source_path: Path, output_dir: Path) -> Path:
    source_crop = load_source(source_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_panel = _source_panel(source_crop)
    source_panel.save(output_dir / "source-preview.png", optimize=True)

    summaries, stencil = _write_candidate_assets(output_dir)
    contact_sheet = _build_contact_sheet(output_dir, source_panel, stencil, summaries)
    html_path = _build_html(output_dir, summaries)
    screenshots = _capture_browser_gallery(html_path, output_dir)
    summary = {
        "pipeline": "fixed crop -> art-directed semantic masks -> four-tone vector stencil -> clipped glyph fill -> glyph contours",
        "source": {
            "path": "profile/avatar-source.png",
            "sha256": source_sha256(source_path),
            "expected_sha256": EXPECTED_SOURCE_SHA256,
            "crop_pixels": {
                "x": SOURCE_CROP[0],
                "y": SOURCE_CROP[1],
                "width": SOURCE_CROP[2] - SOURCE_CROP[0],
                "height": SOURCE_CROP[3] - SOURCE_CROP[1],
            },
        },
        "panel_size": list(PANEL_SIZE),
        "rejection_thresholds": {
            "silhouette_iou": MIN_SILHOUETTE_IOU,
            "edge_overlap": MIN_EDGE_OVERLAP,
        },
        "candidates": summaries,
        "contact_sheet": contact_sheet.name,
        "html": html_path.name,
        "browser_screenshots": screenshots,
        "selected_candidate": None,
    }
    summary_path = output_dir / "portrait-candidates.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return contact_sheet


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--avatar-source", type=Path, default=LOCAL_AVATAR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = build_gallery(args.avatar_source, args.output)
    print(result)
