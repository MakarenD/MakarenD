"""Deterministic geometry for the 365-node M-Core emblem."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass


DEFAULT_SEED = "MakarenD|m-core|v1"
SKELETON = (
    (400.0, 330.0),
    (400.0, 90.0),
    (600.0, 270.0),
    (800.0, 90.0),
    (800.0, 330.0),
)


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class MCoreGeometry:
    nodes: tuple[Point, ...]
    edges: tuple[tuple[int, int], ...]
    route_path: str


def _seed_value(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


def _sample_polyline(count: int) -> list[tuple[Point, Point]]:
    segments = []
    total = 0.0
    for left, right in zip(SKELETON, SKELETON[1:]):
        length = math.dist(left, right)
        segments.append((left, right, length))
        total += length

    samples: list[tuple[Point, Point]] = []
    for index in range(count):
        distance = total * index / (count - 1)
        traversed = 0.0
        for left, right, length in segments:
            if distance <= traversed + length or (left, right, length) == segments[-1]:
                ratio = min(1.0, max(0.0, (distance - traversed) / length))
                x = left[0] + (right[0] - left[0]) * ratio
                y = left[1] + (right[1] - left[1]) * ratio
                dx = (right[0] - left[0]) / length
                dy = (right[1] - left[1]) / length
                samples.append((Point(x, y), Point(-dy, dx)))
                break
            traversed += length
    return samples


def _edge_score(left: int, right: int, seed: str) -> bytes:
    return hashlib.sha256(f"{seed}|edge|{left}|{right}".encode("utf-8")).digest()


def generate_m_core(count: int = 365, seed: str = DEFAULT_SEED) -> MCoreGeometry:
    if count < 5:
        raise ValueError("M-Core needs at least five nodes")

    rng = random.Random(_seed_value(seed))
    nodes: list[Point] = []
    for index, (point, normal) in enumerate(_sample_polyline(count)):
        if index in (0, count - 1):
            offset = 0.0
        else:
            wave = math.sin(index * 0.73) * 2.1
            offset = wave + rng.uniform(-6.5, 6.5)
        along = 0.0 if index in (0, count - 1) else rng.uniform(-1.4, 1.4)
        tangent = Point(normal.y, -normal.x)
        nodes.append(
            Point(
                round(point.x + normal.x * offset + tangent.x * along, 2),
                round(point.y + normal.y * offset + tangent.y * along, 2),
            )
        )

    edges: set[tuple[int, int]] = {(index, index + 1) for index in range(count - 1)}
    degree = [0] * count
    for left, right in edges:
        degree[left] += 1
        degree[right] += 1

    candidates: list[tuple[float, bytes, int, int]] = []
    for left in range(count):
        for right in range(left + 7, min(count, left + 121)):
            distance = math.dist(
                (nodes[left].x, nodes[left].y), (nodes[right].x, nodes[right].y)
            )
            if distance <= 28.0:
                candidates.append(
                    (distance, _edge_score(left, right, seed), left, right)
                )
    candidates.sort(key=lambda item: (item[0], item[1]))

    supplemental = 0
    for _, _, left, right in candidates:
        if supplemental >= 88:
            break
        if degree[left] >= 3 or degree[right] >= 3:
            continue
        edge = (left, right)
        if edge in edges:
            continue
        edges.add(edge)
        degree[left] += 1
        degree[right] += 1
        supplemental += 1

    route = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in SKELETON)
    return MCoreGeometry(tuple(nodes), tuple(sorted(edges)), route)
