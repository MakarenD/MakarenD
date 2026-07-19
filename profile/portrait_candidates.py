#!/usr/bin/env python3
"""Build source-specific vector stencil portrait candidates from SVG glyphs."""

from __future__ import annotations

import hashlib
import html
import math
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps


PROFILE_DIR = Path(__file__).resolve().parent
LOCAL_AVATAR = PROFILE_DIR / "avatar-source.png"
EXPECTED_SOURCE_SHA256 = (
    "9e2480c7723b80734dcb8def7b71ef35abfec83fb7ecbf5a1e57beff4f1712ac"
)
SOURCE_CROP = (0, 100, 1050, 1300)
PANEL_SIZE = (452, 318)
ART_TRANSFORM = "translate(58 14) scale(0.32 0.235) translate(0 -100)"
ALLOWED_GLYPHS = frozenset("01+-=/\\|()[]{}#@:")
FONT_FAMILY = "ui-monospace,SFMono-Regular,Consolas,monospace"


@dataclass(frozen=True)
class CandidatePreset:
    """One deliberately distinct glyph strategy over the same vector stencil."""

    name: str
    description: str
    tone_count: int
    font_size: float
    row_step: float
    gap: int
    contour_step: float
    contour_weight: float
    region_scale: dict[str, float] = field(default_factory=dict)
    region_opacity: dict[str, float] = field(default_factory=dict)
    mixed_sizes: bool = False


@dataclass(frozen=True)
class Theme:
    name: str
    panel: str
    border: str
    accent: str


THEMES = {
    "dark": Theme("dark", "#161b22", "#30363d", "#39c5cf"),
    "light": Theme("light", "#f6f8fa", "#d0d7de", "#087f8c"),
}


CANDIDATES = (
    CandidatePreset(
        "stencil-3-tone",
        "Three broad tones with restrained medium glyphs.",
        3,
        43,
        50,
        1,
        42,
        0.76,
    ),
    CandidatePreset(
        "stencil-4-tone",
        "Four semantic tones preserve the face, hand, beard, and hoodie planes.",
        4,
        39,
        44,
        1,
        38,
        0.82,
        region_opacity={"face": 0.48, "hand": 0.62, "hoodie": 0.34},
    ),
    CandidatePreset(
        "dense-glyph-fill",
        "Small tightly packed glyphs retain the most stencil detail.",
        4,
        29,
        31,
        0,
        34,
        0.72,
    ),
    CandidatePreset(
        "sparse-glyph-fill",
        "Large widely spaced glyphs test whether the silhouette reads unaided.",
        3,
        54,
        68,
        2,
        46,
        0.88,
        region_opacity={"face": 0.50, "hand": 0.64, "hoodie": 0.30},
    ),
    CandidatePreset(
        "contour-emphasis",
        "Sparse interiors with stronger glyph-built critical contours.",
        3,
        49,
        64,
        2,
        25,
        1.0,
        region_opacity={"face": 0.40, "hand": 0.48, "hoodie": 0.24},
    ),
    CandidatePreset(
        "beard-and-gesture-emphasis",
        "Denser beard, hair, eye ring, and hand gesture with quieter skin.",
        4,
        46,
        54,
        1,
        27,
        1.0,
        region_scale={"hair": 0.72, "beard": 0.70, "ring": 0.68, "eyes": 0.66},
        region_opacity={"face": 0.36, "hand": 0.52, "beard": 0.94, "hair": 0.96},
    ),
    CandidatePreset(
        "large-glyph-poster",
        "Very large symbols make the construction unmistakably typographic.",
        3,
        66,
        76,
        1,
        39,
        0.96,
        region_scale={"eyes": 0.52, "nose": 0.56, "mouth": 0.56},
    ),
    CandidatePreset(
        "mixed-size-glyphs",
        "Mixed glyph sizes and slight row rotations follow the portrait curvature.",
        4,
        41,
        47,
        1,
        29,
        0.94,
        region_scale={"hair": 0.78, "beard": 0.82, "eyes": 0.60, "mouth": 0.68},
        mixed_sizes=True,
    ),
)


REGION_PATHS = {
    "hair": (
        "M176 468 C156 323 210 192 342 145 C504 86 702 137 807 268 "
        "C842 312 866 384 870 474 L828 432 C800 342 744 286 650 255 "
        "C523 214 390 244 303 312 C249 354 217 409 205 469 Z"
    ),
    "head": (
        "M176 468 C156 323 210 192 342 145 C504 86 702 137 807 268 "
        "C875 352 901 507 885 692 C874 856 847 1042 744 1172 "
        "C684 1246 611 1287 527 1245 C422 1193 378 1088 356 974 "
        "C326 820 306 657 176 468 Z"
    ),
    "face": (
        "M279 424 C351 276 541 232 704 276 C834 311 889 434 885 617 "
        "C882 770 842 923 751 1002 C679 1064 526 1044 425 968 "
        "C350 912 315 815 302 701 C291 605 255 513 279 424 Z"
    ),
    "beard": (
        "M377 849 C426 793 483 789 535 824 C582 849 616 849 661 823 "
        "C730 782 815 804 850 879 C892 971 850 1096 771 1180 "
        "C713 1241 652 1287 582 1262 C483 1228 410 1148 377 1041 "
        "C357 976 350 905 377 849 Z"
    ),
    "moustache": (
        "M455 858 C505 813 557 812 613 849 C672 811 733 817 786 859 "
        "C731 903 669 911 617 899 C554 913 497 900 455 858 Z"
    ),
    "ring_outer": (
        "M347 565 C390 500 458 476 525 494 C589 512 625 566 672 618 "
        "C697 647 674 681 646 715 C610 760 561 804 498 803 "
        "C435 802 393 763 353 720 C317 681 307 624 347 565 Z"
    ),
    "ring_inner": (
        "M408 590 C439 554 479 540 519 552 C556 563 578 592 607 626 "
        "C622 644 608 664 590 685 C565 714 534 737 497 738 "
        "C458 739 434 715 412 690 C387 663 383 621 408 590 Z"
    ),
    "right_eye": ("M632 585 C666 563 714 560 752 581 C722 617 667 623 632 585 Z"),
    "left_eye": ("M395 635 C423 615 461 609 490 625 C466 655 422 664 395 635 Z"),
    "right_brow": (
        "M625 542 C669 510 729 501 775 522 L769 545 C725 528 674 534 632 558 Z"
    ),
    "nose": (
        "M618 596 C614 660 629 720 662 772 C667 802 642 824 607 828 "
        "L585 817 C607 827 633 827 650 812 C626 744 611 674 618 596 Z"
    ),
    "mouth": ("M501 944 C555 967 621 972 690 948 C667 1006 559 1015 522 951 Z"),
    "hoodie": (
        "M0 823 C110 838 210 902 292 1011 C339 1073 393 1197 486 1278 "
        "L1050 1300 L1050 935 C970 910 919 856 854 797 "
        "C835 927 785 1089 690 1238 C617 1322 497 1322 407 1245 "
        "C345 1192 302 1098 260 1014 C190 910 90 875 0 900 Z"
    ),
}


REGION_BOUNDS = {
    "hoodie": (0, 790, 1050, 1300),
    "face": (250, 230, 900, 1050),
    "hair": (150, 100, 890, 490),
    "beard": (340, 780, 900, 1290),
    "moustache": (430, 800, 810, 930),
    "hand": (-20, 120, 710, 1310),
    "ring": (300, 460, 710, 820),
    "eyes": (375, 490, 800, 680),
    "nose": (540, 570, 710, 850),
    "mouth": (470, 900, 720, 1030),
}


REGION_GLYPHS = {
    "hoodie": "+-=",
    "face": ":+-",
    "hair": "#@/\\",
    "beard": "#+/\\",
    "moustache": "#=@",
    "hand": ":+-",
    "ring": "()+-",
    "eyes": "@-()",
    "nose": "|/\\",
    "mouth": "-=()",
}


REGION_DEFAULT_OPACITY = {
    "hoodie": 0.30,
    "face": 0.46,
    "hair": 0.90,
    "beard": 0.84,
    "moustache": 0.96,
    "hand": 0.58,
    "ring": 0.76,
    "eyes": 1.0,
    "nose": 0.78,
    "mouth": 0.96,
}


def _capsule_path(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    radius: float,
) -> str:
    dx = end_x - start_x
    dy = end_y - start_y
    length = math.hypot(dx, dy)
    normal_x = -dy / length * radius
    normal_y = dx / length * radius
    return (
        f"M{start_x + normal_x:.1f} {start_y + normal_y:.1f} "
        f"L{end_x + normal_x:.1f} {end_y + normal_y:.1f} "
        f"A{radius} {radius} 0 0 1 {end_x - normal_x:.1f} {end_y - normal_y:.1f} "
        f"L{start_x - normal_x:.1f} {start_y - normal_y:.1f} "
        f"A{radius} {radius} 0 0 1 {start_x + normal_x:.1f} {start_y + normal_y:.1f} Z"
    )


HAND_BASE_PATH = " ".join(
    (
        "M0 1300 L0 698 C67 642 92 585 108 521 L107 356 "
        "C104 294 131 252 169 258 C205 264 220 310 214 370 L205 540 "
        "C220 610 251 660 302 704 C357 753 398 822 390 910 "
        "C378 1040 303 1178 217 1300 Z",
        _capsule_path(153, 553, 153, 275, 48),
        _capsule_path(243, 505, 331, 180, 50),
        _capsule_path(346, 525, 472, 237, 48),
        _capsule_path(598, 594, 676, 644, 36),
    )
)


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source(path: Path = LOCAL_AVATAR) -> Image.Image:
    """Load the exact reviewed photograph and apply its fixed direct crop."""

    if not path.is_file():
        raise FileNotFoundError(f"Required portrait source is missing: {path}")
    digest = source_sha256(path)
    if digest != EXPECTED_SOURCE_SHA256:
        raise ValueError(
            "Portrait source differs from the reviewed photograph: "
            f"expected {EXPECTED_SOURCE_SHA256}, got {digest}"
        )
    with Image.open(path) as image:
        source = ImageOps.exif_transpose(image).convert("RGB")
    if source.size[0] < SOURCE_CROP[2] or source.size[1] < SOURCE_CROP[3]:
        raise ValueError("Portrait source is smaller than the fixed crop")
    return source.crop(SOURCE_CROP)


def _id(prefix: str, name: str) -> str:
    return f"{prefix}-{name.replace('_', '-')}"


def _shape_defs(prefix: str) -> str:
    parts = []
    for name, path in REGION_PATHS.items():
        parts.append(f'    <path id="{_id(prefix, f"shape-{name}")}" d="{path}"/>')
    parts.append(
        f'    <path id="{_id(prefix, "shape-hand-base")}" d="{HAND_BASE_PATH}"/>'
    )

    for name in (
        "hoodie",
        "hair",
        "right_eye",
        "left_eye",
        "right_brow",
        "nose",
        "mouth",
        "moustache",
    ):
        parts.extend(
            (
                f'    <clipPath id="{_id(prefix, f"clip-{name}")}" clipPathUnits="userSpaceOnUse">',
                f'      <use href="#{_id(prefix, f"shape-{name}")}"/>',
                "    </clipPath>",
            )
        )

    mask_attrs = 'maskUnits="userSpaceOnUse" x="-60" y="70" width="1180" height="1260"'
    parts.extend(
        (
            f'    <mask id="{_id(prefix, "mask-hand")}" {mask_attrs}>',
            '      <rect x="-60" y="70" width="1180" height="1260" fill="black"/>',
            f'      <use href="#{_id(prefix, "shape-hand-base")}" fill="white"/>',
            f'      <use href="#{_id(prefix, "shape-ring-outer")}" fill="white"/>',
            f'      <use href="#{_id(prefix, "shape-ring-inner")}" fill="black"/>',
            "    </mask>",
        )
    )
    for name in ("hoodie", "face", "hair", "beard", "moustache"):
        parts.extend(
            (
                f'    <mask id="{_id(prefix, f"mask-{name}-visible")}" {mask_attrs}>',
                '      <rect x="-60" y="70" width="1180" height="1260" fill="black"/>',
                f'      <use href="#{_id(prefix, f"shape-{name}")}" fill="white"/>',
                f'      <use href="#{_id(prefix, "shape-hand-base")}" fill="black"/>',
                f'      <use href="#{_id(prefix, "shape-ring-outer")}" fill="black"/>',
                f'      <use href="#{_id(prefix, "shape-ring-inner")}" fill="white"/>',
                "    </mask>",
            )
        )
    parts.extend(
        (
            f'    <mask id="{_id(prefix, "mask-ring")}" {mask_attrs}>',
            '      <rect x="-60" y="70" width="1180" height="1260" fill="black"/>',
            f'      <use href="#{_id(prefix, "shape-ring-outer")}" fill="white"/>',
            f'      <use href="#{_id(prefix, "shape-ring-inner")}" fill="black"/>',
            "    </mask>",
        )
    )
    return "\n".join(parts)


def _glyph_line(glyphs: str, gap: int, length: int, offset: int) -> str:
    cycle = "".join(glyph + " " * gap for glyph in glyphs)
    rotated = cycle[offset % len(cycle) :] + cycle[: offset % len(cycle)]
    return (rotated * (length // len(rotated) + 2))[:length]


def _region_fill(
    prefix: str,
    region: str,
    preset: CandidatePreset,
    *,
    clip: str | None = None,
    mask: str | None = None,
) -> str:
    left, top, right, bottom = REGION_BOUNDS[region]
    scale = preset.region_scale.get(region, 1.0)
    font_size = preset.font_size * scale
    row_step = preset.row_step * scale
    opacity = preset.region_opacity.get(region, REGION_DEFAULT_OPACITY[region])
    if preset.tone_count == 3 and region in {"face", "hand", "hoodie"}:
        opacity *= 0.88
    attributes = []
    if clip:
        attributes.append(f'clip-path="url(#{_id(prefix, clip)})"')
    if mask:
        attributes.append(f'mask="url(#{_id(prefix, mask)})"')
    rows = []
    row_index = 0
    y = top + font_size
    while y <= bottom + font_size:
        size = font_size
        angle = 0.0
        if preset.mixed_sizes:
            size *= (0.78, 1.0, 1.22)[row_index % 3]
            angle = (-2.6, 1.8, 0.0, 2.2)[row_index % 4]
        x = left - 90 + ((row_index * 47 + len(region) * 13) % 97)
        length = max(24, math.ceil((right - left + 180) / max(10, size * 0.54)))
        glyphs = _glyph_line(
            REGION_GLYPHS[region], preset.gap, length, row_index * 3 + len(region)
        )
        transform = (
            f' transform="rotate({angle:.1f} {(left + right) / 2:.1f} {y:.1f})"'
            if angle
            else ""
        )
        rows.append(
            f'      <text x="{x:.1f}" y="{y:.1f}" font-size="{size:.1f}" '
            f'opacity="{opacity:.2f}"{transform}>{html.escape(glyphs)}</text>'
        )
        row_index += 1
        y += row_step
    return (
        f'    <g class="glyph-fill glyph-{region}" {" ".join(attributes)}>\n'
        + "\n".join(rows)
        + "\n    </g>"
    )


def _sample_polyline(
    points: tuple[tuple[float, float], ...], step: float
) -> list[tuple[float, float, float]]:
    samples: list[tuple[float, float, float]] = []
    for start, end in zip(points, points[1:], strict=False):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        count = max(1, round(length / step))
        angle = math.degrees(math.atan2(dy, dx))
        for index in range(count):
            position = index / count
            samples.append((start[0] + position * dx, start[1] + position * dy, angle))
    samples.append((*points[-1], samples[-1][2] if samples else 0.0))
    return samples


def _ellipse_points(
    center_x: float,
    center_y: float,
    radius_x: float,
    radius_y: float,
    count: int,
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (
            center_x + radius_x * math.cos(index * math.tau / count),
            center_y + radius_y * math.sin(index * math.tau / count),
        )
        for index in range(count + 1)
    )


def _contour_features() -> tuple[tuple[str, tuple[tuple[float, float], ...], str], ...]:
    return (
        (
            "head",
            (
                (186, 450),
                (215, 300),
                (340, 160),
                (520, 125),
                (700, 170),
                (815, 280),
                (885, 500),
                (882, 750),
                (840, 960),
                (744, 1172),
            ),
            "/\\()",
        ),
        (
            "hairline",
            (
                (205, 469),
                (303, 312),
                (390, 244),
                (523, 214),
                (650, 255),
                (744, 286),
                (828, 432),
            ),
            "/\\#",
        ),
        (
            "palm",
            (
                (0, 698),
                (70, 625),
                (108, 521),
                (120, 610),
                (205, 720),
                (290, 820),
                (250, 1050),
                (170, 1260),
            ),
            "/\\|()",
        ),
        ("finger-one", ((153, 540), (153, 285)), "|"),
        ("finger-two", ((243, 505), (331, 180)), "/|"),
        ("finger-three", ((346, 525), (472, 237)), "/|"),
        ("ring-outer", _ellipse_points(500, 642, 185, 155, 24), "()/-\\"),
        ("ring-inner", _ellipse_points(500, 644, 104, 94, 20), "()/-\\"),
        ("right-brow", ((625, 542), (669, 510), (729, 501), (775, 522)), "=/"),
        (
            "right-eye",
            (
                (632, 585),
                (666, 563),
                (714, 560),
                (752, 581),
                (722, 617),
                (667, 623),
                (632, 585),
            ),
            "-()",
        ),
        (
            "left-eye",
            (
                (395, 635),
                (423, 615),
                (461, 609),
                (490, 625),
                (466, 655),
                (422, 664),
                (395, 635),
            ),
            "-()",
        ),
        (
            "nose",
            ((625, 601), (624, 666), (642, 718), (662, 772), (642, 824), (607, 828)),
            "|/\\",
        ),
        (
            "moustache",
            (
                (455, 858),
                (505, 813),
                (557, 812),
                (613, 849),
                (672, 811),
                (733, 817),
                (786, 859),
            ),
            "=#",
        ),
        ("mouth", ((501, 944), (555, 967), (621, 972), (690, 948)), "-="),
        (
            "beard",
            (
                (377, 849),
                (357, 976),
                (410, 1148),
                (582, 1262),
                (713, 1241),
                (850, 1096),
                (850, 879),
            ),
            "/\\()#",
        ),
        (
            "hood-left",
            ((0, 823), (110, 838), (210, 902), (292, 1011), (393, 1197), (486, 1278)),
            "/=",
        ),
        ("hood-right", ((854, 797), (835, 927), (785, 1089), (690, 1238)), "\\="),
    )


def _contour_glyphs(preset: CandidatePreset) -> str:
    items = []
    font_size = 34 if preset.name != "large-glyph-poster" else 42
    for feature_index, (name, points, glyphs) in enumerate(_contour_features()):
        step = preset.contour_step
        if name in {"right-eye", "left-eye", "nose", "mouth", "moustache"}:
            step *= 0.72
        for index, (x, y, angle) in enumerate(_sample_polyline(points, step)):
            glyph = glyphs[(index + feature_index) % len(glyphs)]
            items.append(
                f'      <text x="{x:.1f}" y="{y:.1f}" font-size="{font_size:.1f}" '
                f'opacity="{preset.contour_weight:.2f}" text-anchor="middle" '
                f'transform="rotate({angle:.1f} {x:.1f} {y:.1f})">{glyph}</text>'
            )
    # Irises and pupils stay explicit at final size instead of relying on a tone field.
    items.extend(
        (
            '      <text x="697" y="606" font-size="42" opacity="1" text-anchor="middle">@</text>',
            '      <text x="443" y="650" font-size="36" opacity="1" text-anchor="middle">@</text>',
        )
    )
    return '    <g class="glyph-contours">\n' + "\n".join(items) + "\n    </g>"


def render_candidate_svg(
    preset: CandidatePreset,
    theme: Theme,
) -> str:
    """Render a text-only portrait; paths remain hidden inside masks and clips."""

    prefix = f"portrait-{preset.name}-{theme.name}"
    defs = _shape_defs(prefix)
    layers = (
        _region_fill(prefix, "hoodie", preset, mask="mask-hoodie-visible"),
        _region_fill(prefix, "face", preset, mask="mask-face-visible"),
        _region_fill(prefix, "hair", preset, mask="mask-hair-visible"),
        _region_fill(prefix, "beard", preset, mask="mask-beard-visible"),
        _region_fill(prefix, "moustache", preset, mask="mask-moustache-visible"),
        _region_fill(prefix, "hand", preset, mask="mask-hand"),
        _region_fill(prefix, "ring", preset, mask="mask-ring"),
        _region_fill(prefix, "eyes", preset, clip="clip-right-brow"),
        _region_fill(prefix, "eyes", preset, clip="clip-right-eye"),
        _region_fill(prefix, "eyes", preset, clip="clip-left-eye"),
        _region_fill(prefix, "nose", preset, clip="clip-nose"),
        _region_fill(prefix, "mouth", preset, clip="clip-mouth"),
        _contour_glyphs(preset),
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="452" height="318" viewBox="0 0 452 318" '
        'role="img" aria-labelledby="title desc">\n'
        f'  <title id="title">{html.escape(preset.name)} portrait candidate</title>\n'
        f'  <desc id="desc">Vector stencil portrait filled only with text glyphs; {html.escape(preset.description)}</desc>\n'
        f'  <rect width="452" height="318" rx="14" fill="{theme.panel}" stroke="{theme.border}"/>\n'
        "  <defs>\n"
        f"{defs}\n"
        "  </defs>\n"
        f'  <g class="portrait-glyphs" fill="{theme.accent}" font-family="{FONT_FAMILY}" '
        f'transform="{ART_TRANSFORM}">\n' + "\n".join(layers) + "\n  </g>\n</svg>\n"
    )


def render_stencil_svg(theme: Theme = THEMES["dark"]) -> str:
    """Render the pre-glyph poster reference used as the candidate quality gate."""

    prefix = "stencil-reference"
    defs = _shape_defs(prefix)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="452" height="318" viewBox="0 0 452 318">
  <rect width="452" height="318" rx="14" fill="{theme.panel}" stroke="{theme.border}"/>
  <defs>
{defs}
  </defs>
  <g transform="{ART_TRANSFORM}">
    <use href="#{_id(prefix, "shape-hoodie")}" fill="#7b8792" mask="url(#{_id(prefix, "mask-hoodie-visible")})"/>
    <use href="#{_id(prefix, "shape-face")}" fill="#aeb8c2" mask="url(#{_id(prefix, "mask-face-visible")})"/>
    <use href="#{_id(prefix, "shape-hair")}" fill="#26313a" mask="url(#{_id(prefix, "mask-hair-visible")})"/>
    <use href="#{_id(prefix, "shape-beard")}" fill="#3c4852" mask="url(#{_id(prefix, "mask-beard-visible")})"/>
    <use href="#{_id(prefix, "shape-moustache")}" fill="#182129" mask="url(#{_id(prefix, "mask-moustache-visible")})"/>
    <rect x="-60" y="70" width="1180" height="1260" fill="#98a3ad" mask="url(#{_id(prefix, "mask-hand")})"/>
    <use href="#{_id(prefix, "shape-right-brow")}" fill="#111820"/>
    <use href="#{_id(prefix, "shape-right-eye")}" fill="#111820"/>
    <use href="#{_id(prefix, "shape-left-eye")}" fill="#111820"/>
    <use href="#{_id(prefix, "shape-nose")}" fill="#53606a"/>
    <use href="#{_id(prefix, "shape-mouth")}" fill="#1a232b"/>
  </g>
</svg>
'''


def rasterize_svg(svg: str) -> Image.Image:
    """Rasterize SVG deterministically with resvg, imported only for QA generation."""

    try:
        from resvg_py import svg_to_bytes
    except ImportError as exc:
        raise RuntimeError(
            "Install profile/qa-requirements.txt to rasterize portrait candidates"
        ) from exc
    return Image.open(
        BytesIO(
            svg_to_bytes(
                svg_string=svg,
                width=PANEL_SIZE[0],
                height=PANEL_SIZE[1],
                text_rendering="optimize_legibility",
            )
        )
    ).convert("RGB")


def _binary_foreground(image: Image.Image, panel: str) -> Image.Image:
    background = Image.new("RGB", image.size, panel)
    difference = ImageChops.difference(image.convert("RGB"), background).convert("L")
    mask = difference.point(lambda value: 255 if value > 18 else 0)
    # Ignore the panel border; metrics cover only the portrait art.
    canvas = Image.new("L", image.size, 0)
    canvas.paste(mask.crop((48, 10, 414, 318)), (48, 10))
    return canvas


def compare_with_stencil(
    candidate: Image.Image,
    stencil: Image.Image,
    *,
    panel: str,
) -> dict[str, float]:
    """Measure coarse silhouette and edge recall at the actual README panel size."""

    candidate_mask = _binary_foreground(candidate, panel)
    target_mask = _binary_foreground(stencil, panel)
    expanded = candidate_mask.filter(ImageFilter.MaxFilter(13))
    intersection = ImageChops.multiply(expanded, target_mask)
    union = ImageChops.lighter(expanded, target_mask)
    intersection_pixels = sum(value > 0 for value in intersection.getdata())
    union_pixels = sum(value > 0 for value in union.getdata())
    silhouette_iou = intersection_pixels / max(1, union_pixels)

    target_edges = target_mask.filter(ImageFilter.FIND_EDGES).point(
        lambda value: 255 if value > 20 else 0
    )
    edge_support = expanded.filter(ImageFilter.MaxFilter(5))
    supported = ImageChops.multiply(target_edges, edge_support)
    edge_pixels = sum(value > 0 for value in target_edges.getdata())
    supported_pixels = sum(value > 0 for value in supported.getdata())
    edge_overlap = supported_pixels / max(1, edge_pixels)
    ink_pixels = sum(value > 0 for value in candidate_mask.getdata())
    target_pixels = sum(value > 0 for value in target_mask.getdata())
    return {
        "silhouette_iou": round(silhouette_iou, 4),
        "edge_overlap": round(edge_overlap, 4),
        "ink_to_stencil_ratio": round(ink_pixels / max(1, target_pixels), 4),
    }


def validate_glyph_vocabulary(svg: str) -> None:
    """Reject accidental prose or unsupported symbols inside visible SVG text."""

    import xml.etree.ElementTree as ET

    root = ET.fromstring(svg)
    visible = []
    for element in root.iter():
        if element.tag.endswith("text"):
            visible.extend((element.text or "").replace(" ", ""))
    invalid = set(visible) - ALLOWED_GLYPHS
    if invalid:
        raise ValueError(f"Unsupported visible portrait glyphs: {sorted(invalid)}")
