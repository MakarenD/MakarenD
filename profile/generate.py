#!/usr/bin/env python3
"""Generate the profile hero and contribution activity SVG assets."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps


PROFILE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROFILE_DIR / "config.json"
DEFAULT_OUTPUT = PROFILE_DIR.parent / "dist"
LOCAL_AVATAR = PROFILE_DIR / "avatar-source.png"
GRAPHQL_URL = "https://api.github.com/graphql"
PORTRAIT_VARIANTS = (
    "silhouette-first",
    "edge-first",
    "sparse-tonal",
    "sparse-tonal-contour",
    "foreground-masked",
    "gesture-emphasized",
)
DEFAULT_PORTRAIT_VARIANT = "gesture-emphasized"
PORTRAIT_COLUMNS = 34
PORTRAIT_CACHE_VERSION = 3
PORTRAIT_TONE_GLYPHS = (".", "+", "#", "#")
PORTRAIT_EDGE_GLYPHS = frozenset("-|/\\()")
PORTRAIT_DETAIL_GLYPHS = frozenset("@=")
PORTRAIT_ALLOWED_GLYPHS = frozenset(
    {" ", *PORTRAIT_TONE_GLYPHS, *PORTRAIT_EDGE_GLYPHS, *PORTRAIT_DETAIL_GLYPHS}
)
TONE_OPACITIES = (0.34, 0.58, 0.82, 1.0)
DEFAULT_CROP_VALUES = (0.08, 0.0, 0.84, 0.96)
PORTRAIT_FONT_SIZE = 14.5
PORTRAIT_LETTER_SPACING = 0.55
PORTRAIT_ROW_STEP = 9.1
PORTRAIT_GLYPH_ADVANCE = 9.3
PORTRAIT_MIN_COLUMNS = 16
PORTRAIT_MAX_COLUMNS = 48
PORTRAIT_MIN_ROWS = 16
PORTRAIT_MAX_ROWS = 30

ANIMATION_REVEAL_SECONDS = 1.8
ANIMATION_HOLD_SECONDS = 3.0
ANIMATION_RESET_SECONDS = 0.3
ANIMATION_PAUSE_SECONDS = 0.3
ANIMATION_CYCLE_SECONDS = sum(
    (
        ANIMATION_REVEAL_SECONDS,
        ANIMATION_HOLD_SECONDS,
        ANIMATION_RESET_SECONDS,
        ANIMATION_PAUSE_SECONDS,
    )
)
LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


@dataclass(frozen=True)
class Theme:
    """Colors for one GitHub color scheme."""

    name: str
    background: str
    panel: str
    border: str
    text: str
    muted: str
    accent: str
    activity: tuple[str, str, str, str, str]


@dataclass(frozen=True)
class CropConfig:
    """Normalized crop rectangle within the square portrait source."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class MosaicCell:
    """One character cell with a quantized opacity tone."""

    glyph: str
    tone: int
    opacity: float


@dataclass(frozen=True)
class PortraitMosaic:
    """Processed character mosaic ready for SVG rendering."""

    columns: int
    rows: int
    cells: tuple[tuple[MosaicCell, ...], ...]
    variant: str

    @property
    def visible_cells(self) -> int:
        return sum(cell.tone > 0 for row in self.cells for cell in row)

    @property
    def tone_levels(self) -> set[int]:
        return {cell.tone for row in self.cells for cell in row if cell.tone > 0}


@dataclass(frozen=True)
class PortraitStyle:
    """Controls one portrait-first comparison without changing its composition."""

    tone_threshold: float
    edge_threshold: float
    sparsity: float
    use_tone: bool
    use_image_edges: bool
    use_semantic_contours: bool
    foreground_cutoff: float
    emphasize_gesture: bool = False


PORTRAIT_STYLES = {
    "silhouette-first": PortraitStyle(0.33, 0.16, 0.20, True, False, True, 0.12),
    "edge-first": PortraitStyle(1.0, 0.075, 0.0, False, True, True, 0.12),
    "sparse-tonal": PortraitStyle(0.48, 1.0, 0.55, True, False, False, 0.15),
    "sparse-tonal-contour": PortraitStyle(
        0.46, 0.10, 0.48, True, True, True, 0.15
    ),
    "foreground-masked": PortraitStyle(0.44, 0.11, 0.42, True, False, True, 0.25),
    "gesture-emphasized": PortraitStyle(
        0.46, 0.11, 0.74, True, False, False, 0.22, True
    ),
}


def save_portrait_mosaic(mosaic: PortraitMosaic, path: Path) -> None:
    """Persist the derived glyph grid without storing the source photograph."""

    payload = {
        "version": PORTRAIT_CACHE_VERSION,
        "variant": mosaic.variant,
        "columns": mosaic.columns,
        "rows": mosaic.rows,
        "glyph_rows": ["".join(cell.glyph for cell in row) for row in mosaic.cells],
        "tone_rows": [[cell.tone for cell in row] for row in mosaic.cells],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def load_portrait_mosaic(path: Path) -> PortraitMosaic:
    """Load and validate a reproducible derived portrait grid."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        columns = payload["columns"]
        rows = payload["rows"]
        variant = str(payload["variant"])
        glyph_rows = payload["glyph_rows"]
        tone_rows = payload["tone_rows"]
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Cannot read portrait mosaic cache: {path}") from exc

    if (
        payload.get("version") != PORTRAIT_CACHE_VERSION
        or variant not in PORTRAIT_VARIANTS
    ):
        raise GenerationError("Portrait mosaic cache has an unsupported format")
    if (
        type(columns) is not int
        or type(rows) is not int
        or not PORTRAIT_MIN_COLUMNS <= columns <= PORTRAIT_MAX_COLUMNS
        or not PORTRAIT_MIN_ROWS <= rows <= PORTRAIT_MAX_ROWS
        or len(glyph_rows) != rows
        or len(tone_rows) != rows
    ):
        raise GenerationError("Portrait mosaic cache has invalid dimensions")

    cells: list[tuple[MosaicCell, ...]] = []
    for glyph_row, tone_row in zip(glyph_rows, tone_rows, strict=True):
        if not isinstance(glyph_row, str) or len(glyph_row) != columns:
            raise GenerationError("Portrait mosaic cache has an invalid glyph row")
        if not isinstance(tone_row, list) or len(tone_row) != columns:
            raise GenerationError("Portrait mosaic cache has an invalid tone row")
        output_row: list[MosaicCell] = []
        for glyph, raw_tone in zip(glyph_row, tone_row, strict=True):
            if glyph not in PORTRAIT_ALLOWED_GLYPHS or type(raw_tone) is not int:
                raise GenerationError("Portrait mosaic cache contains an invalid cell")
            tone = raw_tone
            if tone < 0 or tone > len(TONE_OPACITIES):
                raise GenerationError("Portrait mosaic cache contains an invalid tone")
            if (glyph == " ") != (tone == 0):
                raise GenerationError(
                    "Portrait mosaic cache contains an inconsistent cell"
                )
            opacity = 0.0 if tone == 0 else TONE_OPACITIES[tone - 1]
            output_row.append(MosaicCell(glyph=glyph, tone=tone, opacity=opacity))
        cells.append(tuple(output_row))
    return PortraitMosaic(columns, rows, tuple(cells), variant)


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


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenerationError(f"Portrait crop field '{field}' must be a number")
    return float(value)


def parse_crop_config(raw: object | None) -> CropConfig:
    """Validate a normalized crop or return the face-focused default."""

    if raw is None:
        return CropConfig(*DEFAULT_CROP_VALUES)
    if not isinstance(raw, dict):
        raise GenerationError("Configuration field 'portrait.crop' must be an object")

    missing = {"x", "y", "width", "height"} - raw.keys()
    if missing:
        raise GenerationError("Portrait crop is missing: " + ", ".join(sorted(missing)))

    crop = CropConfig(
        x=_number(raw["x"], "x"),
        y=_number(raw["y"], "y"),
        width=_number(raw["width"], "width"),
        height=_number(raw["height"], "height"),
    )
    if not (0 <= crop.x < 1 and 0 <= crop.y < 1):
        raise GenerationError("Portrait crop x and y must be in the range [0, 1)")
    if not (0 < crop.width <= 1 and 0 < crop.height <= 1):
        raise GenerationError(
            "Portrait crop width and height must be in the range (0, 1]"
        )
    if crop.x + crop.width > 1 or crop.y + crop.height > 1:
        raise GenerationError("Portrait crop must stay inside the normalized source")
    return crop


def load_config(path: Path) -> dict[str, Any]:
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

    portrait = raw.get("portrait", {})
    if not isinstance(portrait, dict):
        raise GenerationError("Configuration field 'portrait' must be an object")

    return {
        "username": raw["username"].strip(),
        "github_username": github_username.strip(),
        "host": raw["host"].strip(),
        "role": raw["role"].strip(),
        "focus": raw["focus"].strip(),
        "status": status.strip(),
        "location": raw["location"].strip(),
        "portrait": {"crop": parse_crop_config(portrait.get("crop"))},
    }


def brightness_to_ascii(brightness: int | float, palette: str = "@#+-/ ") -> str:
    """Map an 8-bit brightness value to a compact density palette."""

    if not palette:
        raise ValueError("ASCII palette cannot be empty")
    value = max(0.0, min(255.0, float(brightness)))
    index = round((value / 255.0) * (len(palette) - 1))
    return palette[index]


def _square_source(image: Image.Image) -> Image.Image:
    source = ImageOps.exif_transpose(image).convert("RGB")
    side = min(source.size)
    left = (source.width - side) / 2
    top = (source.height - side) / 2
    return source.crop((left, top, left + side, top + side))


def crop_portrait(image: Image.Image, crop: CropConfig) -> Image.Image:
    """Apply the normalized face crop without stretching the source."""

    square = _square_source(image)
    left = round(crop.x * square.width)
    top = round(crop.y * square.height)
    right = round((crop.x + crop.width) * square.width)
    bottom = round((crop.y + crop.height) * square.height)
    if right <= left or bottom <= top:
        raise GenerationError("Portrait crop resolves to an empty image")
    return square.crop((left, top, right, bottom))


def _pixels(image: Image.Image) -> list[int]:
    getter = getattr(image, "get_flattened_data", image.getdata)
    return [int(value) for value in getter()]


def _quantize_tone(signal: float, threshold: float = 0.30) -> int:
    """Reduce a normalized tonal signal to a small, readable set of masses."""

    if signal < threshold:
        return 0
    normalized = (signal - threshold) / max(0.01, 1.0 - threshold)
    return min(
        len(TONE_OPACITIES),
        max(1, math.ceil(normalized * len(TONE_OPACITIES))),
    )


def _glyph_for(
    tone: int,
    gx: float,
    gy: float,
    *,
    edge: float = 0.0,
    edge_threshold: float | None = None,
    tone_glyphs: tuple[str, ...] = PORTRAIT_TONE_GLYPHS,
) -> str:
    """Choose a coarse tonal glyph or one directional contour glyph."""

    if tone <= 0:
        return " "
    if edge_threshold is not None and edge >= edge_threshold:
        if abs(gx) > abs(gy) * 1.3:
            return "|"
        if abs(gy) > abs(gx) * 1.3:
            return "-"
        return "/" if gx * gy < 0 else "\\"
    return tone_glyphs[tone - 1]


def _ellipse_score(
    x: float,
    y: float,
    center_x: float,
    center_y: float,
    radius_x: float,
    radius_y: float,
) -> float:
    distance = ((x - center_x) / radius_x) ** 2 + ((y - center_y) / radius_y) ** 2
    return max(0.0, 1.0 - distance)


def _segment_distance(
    x: float,
    y: float,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
) -> float:
    dx = end_x - start_x
    dy = end_y - start_y
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(x - start_x, y - start_y)
    position = max(
        0.0,
        min(1.0, ((x - start_x) * dx + (y - start_y) * dy) / length_squared),
    )
    return math.hypot(x - (start_x + position * dx), y - (start_y + position * dy))


def _capsule_score(
    x: float,
    y: float,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    radius: float,
) -> float:
    return max(
        0.0,
        1.0 - _segment_distance(x, y, start_x, start_y, end_x, end_y) / radius,
    )


def _portrait_regions(x: float, y: float) -> dict[str, float]:
    """Return semantic masses for the fixed source composition.

    The hand-over-eye gesture is the identity of this photograph. Geometry keeps
    that gesture, hair, beard, and hoodie legible while the photograph supplies
    only texture inside those regions.
    """

    head = _ellipse_score(x, y, 0.57, 0.50, 0.33, 0.46)
    hair = _ellipse_score(x, y, 0.60, 0.16, 0.25, 0.14) if y < 0.29 else 0.0
    beard = (
        _ellipse_score(x, y, 0.58, 0.74, 0.24, 0.20) if y > 0.56 else 0.0
    )
    hoodie = max(
        _ellipse_score(x, y, 0.51, 1.05, 0.60, 0.30),
        _capsule_score(x, y, 0.18, 0.98, 0.32, 0.74, 0.10),
        _capsule_score(x, y, 0.93, 0.98, 0.80, 0.70, 0.11),
    )

    palm = max(
        _ellipse_score(x, y, 0.16, 0.55, 0.27, 0.44),
        _capsule_score(x, y, -0.03, 0.98, 0.19, 0.57, 0.18),
    )
    fingers = max(
        _capsule_score(x, y, 0.03, 0.37, 0.03, 0.04, 0.052),
        _capsule_score(x, y, 0.14, 0.38, 0.20, 0.00, 0.057),
        _capsule_score(x, y, 0.26, 0.36, 0.38, 0.02, 0.054),
    )
    ring_distance = math.sqrt(((x - 0.41) / 0.16) ** 2 + ((y - 0.45) / 0.105) ** 2)
    ring = max(0.0, 1.0 - abs(ring_distance - 1.0) / 0.30)
    pinch = _capsule_score(x, y, 0.50, 0.38, 0.59, 0.49, 0.046)
    hand = max(palm, fingers, ring, pinch)
    foreground = max(head, hair, beard, hoodie, hand)
    return {
        "head": head,
        "hair": hair,
        "beard": beard,
        "hoodie": hoodie,
        "hand": hand,
        "foreground": foreground,
        "ring_distance": ring_distance,
    }


def _semantic_gradient(
    x: float, y: float, column_step: float, row_step: float
) -> tuple[float, float, float]:
    """Return a one-cell outline of each semantic mass.

    Comparing mask membership instead of the masks' continuous gradients avoids
    filling every interior cell with a contour glyph.
    """

    left = _portrait_regions(x - column_step, y)
    right = _portrait_regions(x + column_step, y)
    up = _portrait_regions(x, y - row_step)
    down = _portrait_regions(x, y + row_step)
    thresholds = {
        "head": 0.12,
        "hair": 0.08,
        "beard": 0.08,
        "hoodie": 0.10,
        "hand": 0.14,
    }
    gradients = [
        (
            float(right[key] >= threshold) - float(left[key] >= threshold),
            float(down[key] >= threshold) - float(up[key] >= threshold),
        )
        for key, threshold in thresholds.items()
    ]
    horizontal, vertical = max(
        gradients, key=lambda gradient: abs(gradient[0]) + abs(gradient[1])
    )
    boundary = min(1.0, abs(horizontal) + abs(vertical))
    return (
        horizontal,
        vertical,
        boundary,
    )


def _ellipse_glyph(
    x: float,
    y: float,
    center_x: float,
    center_y: float,
    radius_x: float,
    radius_y: float,
) -> str:
    horizontal = (x - center_x) / radius_x
    vertical = (y - center_y) / radius_y
    if abs(horizontal) > abs(vertical) * 1.15:
        return "(" if horizontal < 0 else ")"
    if abs(vertical) > abs(horizontal) * 1.25:
        return "-"
    return "/" if horizontal * vertical < 0 else "\\"


def _line_glyph(start_x: float, start_y: float, end_x: float, end_y: float) -> str:
    dx = end_x - start_x
    dy = end_y - start_y
    if abs(dx) <= abs(dy) * 0.35:
        return "|"
    if abs(dy) <= abs(dx) * 0.35:
        return "-"
    return "\\" if dx * dy > 0 else "/"


def _gesture_feature(
    x: float, y: float, columns: int, rows: int
) -> tuple[str, int] | None:
    """Overlay a few deliberate glyphs after the sparse image-derived layers."""

    column = min(columns - 1, int(x * columns))
    row = min(rows - 1, int(y * rows))
    ring_column = round(columns * 0.41)
    ring_row = round(rows * 0.45)
    dc = column - ring_column
    dr = row - ring_row

    # A discrete ring is more legible than a sampled mathematical ellipse at
    # README size. Its clean hole and single pupil carry the whole gesture.
    if abs(dr) == 3 and abs(dc) <= 3:
        return "-", 4
    if dr == -2 and dc in (-4, 4):
        return ("/" if dc < 0 else "\\"), 4
    if abs(dr) <= 1 and dc in (-5, 5):
        return ("(" if dc < 0 else ")"), 4
    if dr == 2 and dc in (-4, 4):
        return ("\\" if dc < 0 else "/"), 4
    if dr == 0 and abs(dc) <= 1:
        return ("@", 4) if dc == 0 else ("-", 4)
    if abs(dr) <= 4 and abs(dc) <= 6:
        return " ", 0

    feature_width = 0.52 / max(columns, rows)

    # Three separate center lines read more clearly as raised fingers than six
    # parallel capsule edges at this small size.
    fingers = (
        (0.04, 0.38, 0.06, 0.05, 0.041),
        (0.15, 0.39, 0.20, 0.03, 0.044),
        (0.27, 0.37, 0.37, 0.05, 0.043),
    )
    for start_x, start_y, end_x, end_y, radius in fingers:
        if abs(y - end_y) <= feature_width and abs(x - end_x) <= radius * 0.55:
            return "-", 4
        if (
            _segment_distance(x, y, start_x, start_y, end_x, end_y)
            <= feature_width
        ):
            return _line_glyph(start_x, start_y, end_x, end_y), 4

    palm_outline = (
        (-0.02, 0.24, 0.04, 0.36),
        (0.04, 0.36, 0.24, 0.47),
        (-0.01, 0.98, 0.17, 0.90),
        (0.17, 0.90, 0.30, 0.69),
        (0.30, 0.69, 0.29, 0.57),
    )
    for start_x, start_y, end_x, end_y in palm_outline:
        if (
            _segment_distance(x, y, start_x, start_y, end_x, end_y)
            <= feature_width
        ):
            return _line_glyph(start_x, start_y, end_x, end_y), 4

    hand_connections = (
        (0.25, 0.39, 0.24, 0.45),
        (0.24, 0.50, 0.30, 0.61),
        (0.58, 0.41, 0.65, 0.49),
    )
    for start_x, start_y, end_x, end_y in hand_connections:
        if (
            _segment_distance(x, y, start_x, start_y, end_x, end_y)
            <= feature_width
        ):
            return _line_glyph(start_x, start_y, end_x, end_y), 4

    face_width = 0.72 / max(columns, rows)
    if _segment_distance(x, y, 0.54, 0.49, 0.57, 0.60) <= face_width:
        return "|", 3
    if min(
        _segment_distance(x, y, 0.46, 0.65, 0.56, 0.63),
        _segment_distance(x, y, 0.56, 0.63, 0.68, 0.66),
    ) <= face_width:
        return "=", 4
    if _segment_distance(x, y, 0.49, 0.71, 0.66, 0.71) <= face_width:
        return "-", 4

    head_distance = math.sqrt(((x - 0.57) / 0.33) ** 2 + ((y - 0.50) / 0.46) ** 2)
    if abs(head_distance - 1.0) <= 0.055 and (x > 0.45 or y < 0.28):
        return _ellipse_glyph(x, y, 0.57, 0.50, 0.33, 0.46), 3

    hairline = (
        (0.28, 0.24, 0.45, 0.18),
        (0.45, 0.18, 0.67, 0.20),
        (0.67, 0.20, 0.78, 0.29),
    )
    for start_x, start_y, end_x, end_y in hairline:
        if (
            _segment_distance(x, y, start_x, start_y, end_x, end_y)
            <= feature_width
        ):
            return _line_glyph(start_x, start_y, end_x, end_y), 3

    beard_distance = math.sqrt(((x - 0.58) / 0.24) ** 2 + ((y - 0.74) / 0.20) ** 2)
    if y > 0.58 and abs(beard_distance - 1.0) <= 0.07:
        return _ellipse_glyph(x, y, 0.58, 0.74, 0.24, 0.20), 3

    hoodie_lines = (
        (0.01, 0.99, 0.25, 0.76),
        (0.25, 0.76, 0.49, 0.97),
        (0.99, 0.99, 0.80, 0.73),
        (0.80, 0.73, 0.58, 0.97),
    )
    for start_x, start_y, end_x, end_y in hoodie_lines:
        if (
            _segment_distance(x, y, start_x, start_y, end_x, end_y)
            <= feature_width
        ):
            return _line_glyph(start_x, start_y, end_x, end_y), 3
    for drawstring_x in (0.50, 0.69):
        if (
            abs(x - drawstring_x) <= feature_width
            and 0.89 <= y <= 0.99
        ):
            return "|", 3
    return None


def portrait_mosaic(
    image: Image.Image,
    crop: CropConfig,
    *,
    variant: str = DEFAULT_PORTRAIT_VARIANT,
    columns: int = PORTRAIT_COLUMNS,
) -> PortraitMosaic:
    """Build a sparse semantic portrait centered on the hand-over-eye gesture."""

    if variant not in PORTRAIT_VARIANTS:
        raise ValueError(f"Unknown portrait variant: {variant}")
    if not PORTRAIT_MIN_COLUMNS <= columns <= PORTRAIT_MAX_COLUMNS:
        raise ValueError(
            f"Portrait columns must be between {PORTRAIT_MIN_COLUMNS} "
            f"and {PORTRAIT_MAX_COLUMNS}"
        )

    cropped = crop_portrait(image, crop)
    rows = max(24, round(columns * 0.82))
    if rows > PORTRAIT_MAX_ROWS:
        raise ValueError(
            f"Portrait grid would exceed the {PORTRAIT_MAX_ROWS}-row hero panel"
        )
    style = PORTRAIT_STYLES[variant]

    gray = ImageOps.grayscale(cropped).filter(ImageFilter.MedianFilter(size=5))
    gray = ImageOps.autocontrast(gray, cutoff=(2, 2))
    gray = gray.point(lambda value: round(255 * ((value / 255) ** 0.96)))

    # Tone is deliberately blurred before downsampling so skin, hair, beard, and
    # clothing read as large masses instead of a field of tiny contrast changes.
    tone_source = gray.filter(ImageFilter.GaussianBlur(radius=2.0))
    local_mean = tone_source.filter(
        ImageFilter.GaussianBlur(radius=max(7, min(gray.size) / 30))
    )
    local_contrast = ImageChops.difference(tone_source, local_mean).point(
        lambda value: min(255, round(value * 1.8))
    )

    # Keep only meaningful contours. Global edge autocontrast made upholstery,
    # skin texture, and clothing seams compete with the facial features.
    edges = tone_source.filter(ImageFilter.GaussianBlur(radius=1.1)).filter(
        ImageFilter.FIND_EDGES
    )
    edges = edges.point(lambda value: max(0, min(255, round((value - 16) * 1.8))))

    target = (columns, rows)
    reduced = tone_source.resize(target, Image.Resampling.BOX)
    reduced_edges = edges.resize(target, Image.Resampling.LANCZOS)
    reduced_local = local_contrast.resize(target, Image.Resampling.LANCZOS)
    luminance = _pixels(reduced)
    edge_values = _pixels(reduced_edges)
    local_values = _pixels(reduced_local)

    def lum_at(row: int, column: int) -> float:
        row = min(rows - 1, max(0, row))
        column = min(columns - 1, max(0, column))
        return luminance[row * columns + column] / 255

    cells: list[tuple[MosaicCell, ...]] = []
    for row in range(rows):
        output_row: list[MosaicCell] = []
        for column in range(columns):
            index = row * columns + column
            light = luminance[index] / 255
            darkness = 1 - light
            edge = edge_values[index] / 255
            local = local_values[index] / 255
            gx = lum_at(row, column + 1) - lum_at(row, column - 1)
            gy = lum_at(row + 1, column) - lum_at(row - 1, column)
            nx = (column + 0.5) / columns
            ny = (row + 0.5) / rows
            regions = _portrait_regions(nx, ny)
            mask_gx, mask_gy, semantic_boundary = _semantic_gradient(
                nx, ny, 1 / columns, 1 / rows
            )

            if regions["foreground"] < style.foreground_cutoff:
                output_row.append(MosaicCell(glyph=" ", tone=0, opacity=0.0))
                continue

            if regions["hand"] > 0.16:
                signal = 0.16 + 0.22 * darkness + 0.22 * local + 0.18 * regions["hand"]
            elif regions["hair"] > 0.08:
                signal = 0.42 + 0.42 * darkness + 0.16 * regions["hair"]
            elif regions["beard"] > 0.08:
                signal = (
                    0.36
                    + 0.38 * darkness
                    + 0.14 * local
                    + 0.16 * regions["beard"]
                )
            elif regions["hoodie"] > 0.08:
                signal = 0.09 + 0.30 * darkness + 0.12 * local
            else:
                signal = 0.08 + 0.28 * darkness + 0.15 * local

            pattern = ((row * 17 + column * 29) % 19) / 18
            dense_feature = max(regions["hair"], regions["beard"])
            signal = max(0.0, min(1.0, signal))
            tone = (
                _quantize_tone(signal, style.tone_threshold) if style.use_tone else 0
            )
            keep_threshold = style.sparsity * (1.0 - 0.35 * dense_feature)
            if tone and pattern < keep_threshold:
                tone = 0
            if style.emphasize_gesture:
                if max(regions["hair"], regions["beard"], regions["hand"]) < 0.08:
                    tone = 0
                elif regions["hand"] > max(regions["hair"], regions["beard"]):
                    tone = min(tone, 1)
                elif regions["beard"] > regions["hair"]:
                    tone = min(tone, 2)
                else:
                    tone = min(tone, 3)
            glyph = _glyph_for(tone, gx, gy)

            contour_signal = max(
                edge if style.use_image_edges else 0.0,
                semantic_boundary * 0.70 if style.use_semantic_contours else 0.0,
            )
            if (
                contour_signal >= style.edge_threshold
                and 0 < row < rows - 1
                and 0 < column < columns - 1
            ):
                tone = max(tone, 3)
                glyph = _glyph_for(
                    tone,
                    gx + mask_gx,
                    gy + mask_gy,
                    edge=contour_signal,
                    edge_threshold=style.edge_threshold,
                )

            if style.emphasize_gesture:
                feature = _gesture_feature(nx, ny, columns, rows)
                if feature is not None:
                    glyph, tone = feature
            opacity = 0.0 if tone == 0 else TONE_OPACITIES[tone - 1]
            output_row.append(MosaicCell(glyph=glyph, tone=tone, opacity=opacity))
        cells.append(tuple(output_row))

    return PortraitMosaic(
        columns=columns,
        rows=rows,
        cells=tuple(cells),
        variant=variant,
    )


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
                return image.copy(), "local avatar source"

        url = f"https://github.com/{github_username}.png?size=920"
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


def render_portrait_glyphs(
    mosaic: PortraitMosaic,
    theme: Theme,
    *,
    group_id: str,
    x: float | None = None,
    y: float = 113,
    row_step: float = PORTRAIT_ROW_STEP,
) -> str:
    """Render real SVG glyphs with per-cell opacity."""

    if (
        not PORTRAIT_MIN_COLUMNS <= mosaic.columns <= PORTRAIT_MAX_COLUMNS
        or not PORTRAIT_MIN_ROWS <= mosaic.rows <= PORTRAIT_MAX_ROWS
    ):
        raise ValueError("Portrait mosaic does not fit the hero panel")

    if x is None:
        x = 32 + (452 - mosaic.columns * PORTRAIT_GLYPH_ADVANCE) / 2

    rows = [
        f'    <g id="{group_id}" fill="{theme.accent}" '
        'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" '
        f'font-size="{PORTRAIT_FONT_SIZE}" '
        f'letter-spacing="{PORTRAIT_LETTER_SPACING}">\n'
    ]
    for row_index, row in enumerate(mosaic.cells):
        baseline = y + row_index * row_step
        rows.append(f'      <text x="{x}" y="{baseline:.2f}">')
        for cell in row:
            opacity = f"{cell.opacity:.2f}" if cell.tone else "0"
            rows.append(
                f'<tspan class="tone-{cell.tone}" fill-opacity="{opacity}">'
                f"{xml_escape(cell.glyph)}</tspan>"
            )
        rows.append("</text>\n")
    rows.append("    </g>\n")
    return "".join(rows)


def _animation_key_times(*seconds: float) -> str:
    """Format absolute phase boundaries as normalized SMIL key times."""

    return ";".join(
        f"{seconds_from_start / ANIMATION_CYCLE_SECONDS:.6f}".rstrip("0").rstrip(".")
        for seconds_from_start in seconds
    )


def _portrait_animation(theme: Theme, mosaic: PortraitMosaic) -> str:
    top = 104
    height = 280
    bottom = top + height
    group_id = f"portrait-glyphs-{theme.name}"
    cycle = f"{ANIMATION_CYCLE_SECONDS:g}s"
    reveal_end = ANIMATION_REVEAL_SECONDS
    hold_end = reveal_end + ANIMATION_HOLD_SECONDS
    reset_end = hold_end + ANIMATION_RESET_SECONDS
    phase_times = _animation_key_times(
        0, reveal_end, hold_end, reset_end, ANIMATION_CYCLE_SECONDS
    )
    scan_times = _animation_key_times(
        0,
        reveal_end,
        min(reveal_end + 0.12, hold_end),
        reset_end,
        ANIMATION_CYCLE_SECONDS,
    )
    return "".join(
        [
            "  <defs>\n",
            render_portrait_glyphs(mosaic, theme, group_id=group_id),
            f'    <clipPath id="portrait-reveal-{theme.name}">\n',
            f'      <rect x="48" y="{top}" width="426" height="{height}">\n',
            f'        <animate class="smil-motion" attributeName="height" dur="{cycle}" '
            'repeatCount="indefinite" calcMode="linear" '
            f'values="0;{height};{height};0;0" keyTimes="{phase_times}"/>\n',
            "      </rect>\n",
            "    </clipPath>\n",
            "  </defs>\n",
            '  <g class="portrait-motion-layer">\n',
            f'    <use href="#{group_id}" opacity="0.035"/>\n',
            f'    <g clip-path="url(#portrait-reveal-{theme.name})"><use href="#{group_id}"/></g>\n',
            f'    <rect class="portrait-scan-line" x="48" y="{top}" width="426" height="2" rx="1" fill="{theme.accent}">\n',
            f'      <animate class="smil-motion" attributeName="y" dur="{cycle}" '
            f'repeatCount="indefinite" values="{top};{bottom};{bottom};{top};{top}" '
            f'keyTimes="{phase_times}"/>\n',
            f'      <animate class="smil-motion" attributeName="opacity" dur="{cycle}" '
            'repeatCount="indefinite" values="0.95;0.95;0;0;0" '
            f'keyTimes="{scan_times}"/>\n',
            "    </rect>\n",
            f'    <rect class="portrait-scan-trail" x="48" y="{top - 8}" width="426" height="10" fill="{theme.accent}" opacity="0">\n',
            f'      <animate class="smil-motion" attributeName="y" dur="{cycle}" '
            f'repeatCount="indefinite" values="{top - 8};{bottom - 8};{bottom - 8};{top - 8};{top - 8}" '
            f'keyTimes="{phase_times}"/>\n',
            f'      <animate class="smil-motion" attributeName="opacity" dur="{cycle}" '
            'repeatCount="indefinite" values="0.12;0.12;0;0;0" '
            f'keyTimes="{scan_times}"/>\n',
            "    </rect>\n",
            "  </g>\n",
            f'  <use class="portrait-reduced-final reduced-final" href="#{group_id}"/>\n',
        ]
    )


def render_hero(theme: Theme, config: dict[str, Any], mosaic: PortraitMosaic) -> str:
    """Render the terminal identity card with a repeating scan reveal."""

    width, height = 1000, 420
    svg = [
        _svg_prelude(
            width,
            height,
            "Makaren system profile",
            "Character mosaic portrait and software engineering profile",
        )
    ]
    svg.append(
        "  <style>\n"
        "    .reduced-final{display:none}\n"
        "    @media (prefers-reduced-motion: reduce){.portrait-motion-layer{display:none}.portrait-reduced-final{display:inline}}\n"
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
            f'  <text x="52" y="91" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="11" letter-spacing="1">AVATAR.MOSAIC</text>\n',
            _portrait_animation(theme, mosaic),
            f'  <text x="535" y="103" fill="{theme.text}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="22" font-weight="600">'
            f'<tspan fill="{theme.accent}">{xml_escape(config["username"])}</tspan>'
            f'<tspan fill="{theme.muted}">@</tspan>{xml_escape(config["host"])}</text>\n',
            f'  <line x1="535" y1="122" x2="958" y2="122" stroke="{theme.border}"/>\n',
        ]
    )

    fields = [("role", config["role"]), ("focus", config["focus"])]
    if config.get("status"):
        fields.append(("status", config["status"]))
    fields.append(("location", config["location"]))

    y = 164
    for label, value in fields:
        svg.append(
            f'  <text x="535" y="{y}" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="14">'
            f"{xml_escape(label)}</text>\n"
        )
        value_lines = [value]
        if len(value) > 32 and " · " in value:
            value_lines = [part.strip() for part in value.split("·")]
        for line_index, value_line in enumerate(value_lines):
            line_y = y + line_index * 21
            svg.append(
                f'  <text x="648" y="{line_y}" fill="{theme.text}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="15">'
                f"{xml_escape(value_line)}</text>\n"
            )
        y += 44 + (len(value_lines) - 1) * 21

    command_y = min(y + 30, 374)
    svg.append(
        f'  <text x="535" y="{command_y}" fill="{theme.text}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="16">'
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
    """Render a static base grid with a repeating left-to-right color reveal."""

    width, height = 1000, 220
    weeks = calendar.get("weeks", [])
    days = _calendar_days(calendar)
    cell, gap = 10, 3
    grid_width = len(weeks) * (cell + gap) - gap
    grid_x = max(116, (width - grid_width) // 2)
    grid_y = 72
    cycle = f"{ANIMATION_CYCLE_SECONDS:g}s"
    reveal_end = ANIMATION_REVEAL_SECONDS
    hold_end = reveal_end + ANIMATION_HOLD_SECONDS
    reset_end = hold_end + ANIMATION_RESET_SECONDS
    phase_times = _animation_key_times(
        0, reveal_end, hold_end, reset_end, ANIMATION_CYCLE_SECONDS
    )
    scan_times = _animation_key_times(
        0,
        reveal_end,
        min(reveal_end + 0.12, hold_end),
        reset_end,
        ANIMATION_CYCLE_SECONDS,
    )
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
        "    .reduced-final{display:none}\n"
        "    @media (prefers-reduced-motion: reduce){.activity-motion-layer{display:none}.activity-reduced-final{display:inline}}\n"
        "  </style>\n"
    )
    svg.extend(
        [
            f'  <rect width="{width}" height="{height}" rx="18" fill="{theme.background}"/>\n',
            f'  <rect x="1" y="1" width="998" height="218" rx="17" fill="none" stroke="{theme.border}"/>\n',
            f'  <text x="32" y="37" fill="{theme.accent}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="15" font-weight="600">activity.log</text>\n',
            f'  <text x="968" y="37" text-anchor="end" fill="{theme.muted}" font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="13">{total} contributions</text>\n',
            f'  <line x1="32" y1="51" x2="968" y2="51" stroke="{theme.border}"/>\n',
            "  <defs>\n",
            f'    <g id="activity-colored-cells-{theme.name}">\n',
        ]
    )

    colored_cells = 0
    base_cells: list[str] = []
    for week_index, day in days:
        weekday = int(day.get("weekday", 0))
        if not 0 <= weekday <= 6:
            raise GenerationError(f"Invalid contribution weekday: {weekday}")
        count = int(day.get("contributionCount", 0))
        level = contribution_level(str(day.get("contributionLevel", "NONE")), count)
        x = grid_x + week_index * (cell + gap)
        y = grid_y + weekday * (cell + gap)
        base_cells.append(
            f'    <rect class="base-cell" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{theme.activity[0]}"/>\n'
        )
        if level > 0:
            colored_cells += 1
            svg.append(
                f'      <rect class="colored-cell" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{theme.activity[level]}">'
                f"<title>{xml_escape(day.get('date', 'unknown date'))}: {count} contributions</title></rect>\n"
            )

    svg.extend(
        [
            "    </g>\n",
            f'    <clipPath id="activity-reveal-{theme.name}">\n',
            f'      <rect x="{grid_x}" y="{grid_y - 2}" width="{grid_width}" height="{7 * (cell + gap)}">\n',
            f'        <animate class="smil-motion" attributeName="width" dur="{cycle}" '
            'repeatCount="indefinite" calcMode="linear" '
            f'values="0;{grid_width};{grid_width};0;0" keyTimes="{phase_times}"/>\n',
            "      </rect>\n",
            "    </clipPath>\n",
            "  </defs>\n",
            '  <g id="activity-static-base-grid">\n',
            *base_cells,
            "  </g>\n",
            '  <g class="activity-motion-layer">\n',
            f'    <g id="activity-colored-reveal-layer" clip-path="url(#activity-reveal-{theme.name})">'
            f'<use href="#activity-colored-cells-{theme.name}"/></g>\n',
            f'    <rect class="activity-scan-line" x="{grid_x}" y="{grid_y - 4}" width="2" height="{7 * (cell + gap) + 4}" rx="1" fill="{theme.accent}">\n',
            f'      <animate class="smil-motion" attributeName="x" dur="{cycle}" '
            f'repeatCount="indefinite" values="{grid_x};{grid_x + grid_width};{grid_x + grid_width};{grid_x};{grid_x}" '
            f'keyTimes="{phase_times}"/>\n',
            f'      <animate class="smil-motion" attributeName="opacity" dur="{cycle}" '
            'repeatCount="indefinite" values="0.8;0.8;0;0;0" '
            f'keyTimes="{scan_times}"/>\n',
            "    </rect>\n",
            "  </g>\n",
            f'  <use class="activity-reduced-final reduced-final" href="#activity-colored-cells-{theme.name}"/>\n',
        ]
    )

    for weekday, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        y = grid_y + weekday * (cell + gap) + 8
        svg.append(
            f'  <text x="{grid_x - 14}" y="{y}" text-anchor="end" fill="{theme.muted}" '
            f'font-family="ui-monospace,SFMono-Regular,Consolas,monospace" font-size="10">{label}</text>\n'
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
    svg.append(f"  <!-- colored contribution cells: {colored_cells} -->\n")
    svg.append("</svg>\n")
    return "".join(svg)


def generate_assets(
    config_path: Path,
    output_dir: Path,
    contributions_file: Path | None = None,
    avatar_path: Path = LOCAL_AVATAR,
    portrait_variant: str = DEFAULT_PORTRAIT_VARIANT,
    portrait_cache: Path | None = None,
    write_portrait_cache: Path | None = None,
) -> list[Path]:
    """Generate all four public SVG assets and return their paths."""

    print(f"config: {config_path}")
    config = load_config(config_path)
    if portrait_cache is not None:
        mosaic = load_portrait_mosaic(portrait_cache)
        print(f"portrait source: derived cache {portrait_cache}")
    else:
        avatar, avatar_source = load_avatar(config["github_username"], avatar_path)
        print(f"avatar: {avatar_source}")
        crop = config["portrait"]["crop"]
        mosaic = portrait_mosaic(avatar, crop, variant=portrait_variant)
    if write_portrait_cache is not None:
        save_portrait_mosaic(mosaic, write_portrait_cache)
        print(f"wrote: {write_portrait_cache}")
    print(
        f"portrait: {mosaic.variant}, {mosaic.columns}x{mosaic.rows}, "
        f"{mosaic.visible_cells} glyphs, {len(mosaic.tone_levels)} tones"
    )

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
        hero_path.write_text(render_hero(theme, config, mosaic), encoding="utf-8")
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
    parser.add_argument("--avatar-source", type=Path, default=LOCAL_AVATAR)
    parser.add_argument(
        "--portrait-cache",
        type=Path,
        help="reuse a derived glyph grid instead of reading a photograph",
    )
    parser.add_argument(
        "--write-portrait-cache",
        type=Path,
        help="write the selected derived glyph grid for reproducible CI output",
    )
    parser.add_argument(
        "--portrait-variant",
        choices=PORTRAIT_VARIANTS,
        default=DEFAULT_PORTRAIT_VARIANT,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        generate_assets(
            args.config,
            args.output,
            args.contributions_file,
            avatar_path=args.avatar_source,
            portrait_variant=args.portrait_variant,
            portrait_cache=args.portrait_cache,
            write_portrait_cache=args.write_portrait_cache,
        )
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
