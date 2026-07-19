#!/usr/bin/env python3
"""Capture and pixel-check the real SVG animation timeline in Chromium."""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import Page, sync_playwright

from generate import (
    ANIMATION_CYCLE_SECONDS,
    ANIMATION_HOLD_SECONDS,
    ANIMATION_RESET_SECONDS,
    ANIMATION_REVEAL_SECONDS,
)


DEFAULT_ASSETS = Path(__file__).resolve().parent.parent / "dist"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "qa-artifacts" / "timeline"
REVEAL_MID_MS = round(ANIMATION_REVEAL_SECONDS * 500)
FULLY_VISIBLE_MS = round((ANIMATION_REVEAL_SECONDS + 0.3) * 1000)
HOLD_MS = round((ANIMATION_REVEAL_SECONDS + 1.2) * 1000)
RESET_START_MS = round((ANIMATION_REVEAL_SECONDS + ANIMATION_HOLD_SECONDS) * 1000)
RESET_END_MS = round(
    (ANIMATION_REVEAL_SECONDS + ANIMATION_HOLD_SECONDS + ANIMATION_RESET_SECONDS) * 1000
)
CYCLE_MS = round(ANIMATION_CYCLE_SECONDS * 1000)
NEXT_REVEAL_MS = CYCLE_MS + 350
NEXT_REVEAL_MID_MS = CYCLE_MS + REVEAL_MID_MS
STATIC_WAIT_MS = CYCLE_MS + 600
TIMESTAMPS_MS = (
    0,
    REVEAL_MID_MS,
    FULLY_VISIBLE_MS,
    HOLD_MS,
    RESET_START_MS,
    RESET_END_MS,
    CYCLE_MS,
    NEXT_REVEAL_MS,
    NEXT_REVEAL_MID_MS,
)


@dataclass(frozen=True)
class Capture:
    """One rendered SVG frame and its stable element dimensions."""

    path: Path
    width: int
    height: int


def build_page(assets: Path, output: Path, theme: str) -> Path:
    """Create a local page that exercises the same external-image path as README."""

    hero = (assets / f"hero-{theme}.svg").resolve().as_uri()
    activity = (assets / f"activity-{theme}.svg").resolve().as_uri()
    background = "#0d1117" if theme == "dark" else "#ffffff"
    text = "#e6edf3" if theme == "dark" else "#1f2328"
    page = output / f"preview-{theme}.html"
    page.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body {{ margin: 0; background: {background}; color: {text}; }}
    main {{ width: min(100%, 1000px); margin: 0 auto; }}
    img {{ display: block; width: 100%; height: auto; }}
    #activity {{ margin-top: 28px; }}
  </style>
</head>
<body><main>
  <img id="hero" src="{hero}" alt="Animated portrait">
  <img id="activity" src="{activity}" alt="Animated contribution grid">
</main></body>
</html>
""",
        encoding="utf-8",
    )
    return page


def capture_element(page: Page, selector: str, path: Path) -> Capture:
    locator = page.locator(selector)
    box = locator.bounding_box()
    if box is None:
        raise RuntimeError(f"Cannot resolve bounds for {selector}")
    locator.screenshot(path=str(path), animations="allow")
    return Capture(path, round(box["width"]), round(box["height"]))


def changed_pixel_ratio(first: Path, second: Path, threshold: int = 10) -> float:
    """Return the share of pixels whose largest channel delta exceeds threshold."""

    with (
        Image.open(first).convert("RGB") as left,
        Image.open(second).convert("RGB") as right,
    ):
        if left.size != right.size:
            raise AssertionError(f"Frame size changed: {left.size} != {right.size}")
        difference = ImageChops.difference(left, right)
        getter = getattr(difference, "get_flattened_data", difference.getdata)
        changed = sum(max(pixel) > threshold for pixel in getter())
        return changed / (left.width * left.height)


def corner_rgb(path: Path) -> tuple[int, int, int]:
    with Image.open(path).convert("RGB") as image:
        return image.getpixel((3, 3))


def assert_changed(
    frames: dict[int, Capture], first: int, second: int, label: str
) -> float:
    ratio = changed_pixel_ratio(frames[first].path, frames[second].path)
    if ratio <= 0.0002:
        raise AssertionError(
            f"{label} frames at {first}ms and {second}ms do not visibly differ ({ratio:.6f})"
        )
    return ratio


def assert_unchanged(
    frames: dict[int, Capture], first: int, second: int, label: str
) -> float:
    ratio = changed_pixel_ratio(frames[first].path, frames[second].path)
    if ratio > 0.0002:
        raise AssertionError(
            f"{label} hold frames at {first}ms and {second}ms changed ({ratio:.6f})"
        )
    return ratio


def capture_normal_timeline(
    page: Page, page_url: str, output: Path, theme: str, width: int
) -> dict[str, object]:
    page.emulate_media(color_scheme=theme, reduced_motion="no-preference")
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(page_url, wait_until="load")
    page.wait_for_function(
        """() => [...document.images].every(image => image.complete && image.naturalWidth > 0)"""
    )

    frames: dict[str, dict[int, Capture]] = {"hero": {}, "activity": {}}
    started_at = time.monotonic()
    for timestamp in TIMESTAMPS_MS:
        elapsed = (time.monotonic() - started_at) * 1000
        if timestamp > elapsed:
            page.wait_for_timeout(timestamp - elapsed)
        for asset in ("hero", "activity"):
            path = output / f"{theme}-{width}-{asset}-{timestamp:05d}ms.png"
            frames[asset][timestamp] = capture_element(page, f"#{asset}", path)

    for asset_frames in frames.values():
        sizes = {(capture.width, capture.height) for capture in asset_frames.values()}
        if len(sizes) != 1:
            raise AssertionError(f"Layout shift detected: {sorted(sizes)}")
        corners = {corner_rgb(capture.path) for capture in asset_frames.values()}
        if len(corners) != 1:
            raise AssertionError(f"Background flash detected: {sorted(corners)}")

    diffs: dict[str, float] = {}
    for asset in ("hero", "activity"):
        asset_frames = frames[asset]
        diffs[f"{asset}_start_to_mid_reveal"] = assert_changed(
            asset_frames, 0, REVEAL_MID_MS, asset
        )
        diffs[f"{asset}_mid_reveal_to_full"] = assert_changed(
            asset_frames, REVEAL_MID_MS, FULLY_VISIBLE_MS, asset
        )
        diffs[f"{asset}_fully_visible_hold"] = assert_unchanged(
            asset_frames, FULLY_VISIBLE_MS, HOLD_MS, asset
        )
        diffs[f"{asset}_hold_to_reset"] = assert_changed(
            asset_frames, HOLD_MS, RESET_END_MS, asset
        )
        diffs[f"{asset}_next_cycle_begins"] = assert_changed(
            asset_frames, CYCLE_MS, NEXT_REVEAL_MS, asset
        )
        diffs[f"{asset}_next_reveal_progresses"] = assert_changed(
            asset_frames, NEXT_REVEAL_MS, NEXT_REVEAL_MID_MS, asset
        )
    return {
        "diff_ratios": diffs,
        "sizes": {
            asset: [
                next(iter(asset_frames.values())).width,
                next(iter(asset_frames.values())).height,
            ]
            for asset, asset_frames in frames.items()
        },
        "frames": {
            asset: {
                str(timestamp): capture.path.name
                for timestamp, capture in asset_frames.items()
            }
            for asset, asset_frames in frames.items()
        },
    }


def capture_reduced_motion(
    page: Page, assets: Path, output: Path, theme: str, width: int
) -> dict[str, object]:
    """Validate the SVG's own media query as the top-level document.

    DevTools media emulation does not propagate into a separate image document,
    so direct SVG navigation is required for a deterministic reduced-motion test.
    """

    page.emulate_media(color_scheme=theme, reduced_motion="reduce")
    result: dict[str, object] = {}
    for asset in ("hero", "activity"):
        intrinsic_height = 420 if asset == "hero" else 220
        page.set_viewport_size(
            {"width": width, "height": math.ceil(width * intrinsic_height / 1000)}
        )
        page.goto(
            (assets / f"{asset}-{theme}.svg").resolve().as_uri(), wait_until="load"
        )
        first = output / f"{theme}-{width}-{asset}-reduced-00000ms.png"
        first_capture = capture_element(page, "svg", first)
        hold = output / f"{theme}-{width}-{asset}-{HOLD_MS:05d}ms.png"
        final_ratio = changed_pixel_ratio(first, hold)
        final_threshold = 0.15 if width == 360 else 0.01
        if final_ratio > final_threshold:
            raise AssertionError(
                f"Reduced-motion {asset} is not the final state ({final_ratio:.6f})"
            )
        frames = [first.name]
        static_ratio: float | None = None
        if width == 1000:
            second = output / (
                f"{theme}-{width}-{asset}-reduced-{STATIC_WAIT_MS:05d}ms.png"
            )
            page.wait_for_timeout(STATIC_WAIT_MS)
            capture_element(page, "svg", second)
            static_ratio = changed_pixel_ratio(first, second)
            if static_ratio > 0.0001:
                raise AssertionError(
                    f"Reduced-motion {asset} is not static ({static_ratio:.6f} changed pixels)"
                )
            frames.append(second.name)
        result[asset] = {
            "frames": frames,
            "size": [first_capture.width, first_capture.height],
            "changed_ratio": static_ratio,
            "difference_from_normal_hold": final_ratio,
        }
    return result


def run(assets: Path, output: Path) -> Path:
    """Run dark/light, desktop/mobile, normal/reduced visual assertions."""

    output.mkdir(parents=True, exist_ok=True)
    pages = {theme: build_page(assets, output, theme) for theme in ("dark", "light")}
    summary: dict[str, object] = {
        "timestamps_ms": list(TIMESTAMPS_MS),
        "normal": {},
        "reduced_motion": {},
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            for theme in ("dark", "light"):
                page_url = pages[theme].resolve().as_uri()
                for width in (1000, 360):
                    key = f"{theme}-{width}"
                    summary["normal"][key] = capture_normal_timeline(
                        page, page_url, output, theme, width
                    )
                    summary["reduced_motion"][key] = capture_reduced_motion(
                        page, assets, output, theme, width
                    )
        finally:
            browser.close()

    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = run(args.assets, args.output)
    print(result)
