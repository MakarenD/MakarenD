from __future__ import annotations

import math
import unittest
from collections import deque

from profile.geometry import DEFAULT_SEED, generate_m_core


class MCoreGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.geometry = generate_m_core()

    def test_generates_exactly_365_stable_nodes(self) -> None:
        self.assertEqual(365, len(self.geometry.nodes))
        self.assertEqual(self.geometry, generate_m_core(seed=DEFAULT_SEED))
        self.assertNotEqual(
            self.geometry.nodes, generate_m_core(seed="different").nodes
        )

    def test_coordinates_are_finite_and_inside_emblem_area(self) -> None:
        for node in self.geometry.nodes:
            self.assertTrue(math.isfinite(node.x) and math.isfinite(node.y))
            self.assertGreaterEqual(node.x, 390)
            self.assertLessEqual(node.x, 810)
            self.assertGreaterEqual(node.y, 80)
            self.assertLessEqual(node.y, 340)

    def test_bounding_box_and_landmarks_read_as_m(self) -> None:
        xs = [node.x for node in self.geometry.nodes]
        ys = [node.y for node in self.geometry.nodes]
        self.assertGreater(max(xs) - min(xs), 390)
        self.assertGreater(max(ys) - min(ys), 230)
        left_support = [node for node in self.geometry.nodes if node.x < 415]
        right_support = [node for node in self.geometry.nodes if node.x > 785]
        center_valley = [
            node
            for node in self.geometry.nodes
            if 585 <= node.x <= 615 and node.y > 250
        ]
        self.assertGreater(len(left_support), 65)
        self.assertGreater(len(right_support), 65)
        self.assertTrue(center_valley)
        self.assertLess(min(node.y for node in left_support), 105)
        self.assertLess(min(node.y for node in right_support), 105)

    def test_edges_are_unique_connected_and_bounded(self) -> None:
        edges = self.geometry.edges
        self.assertEqual(len(edges), len(set(edges)))
        self.assertTrue(all(left < right for left, right in edges))
        self.assertTrue(all((index, index + 1) in edges for index in range(364)))
        adjacency = [set() for _ in range(365)]
        for left, right in edges:
            adjacency[left].add(right)
            adjacency[right].add(left)
        self.assertLessEqual(max(map(len, adjacency)), 3)
        seen = {0}
        queue = deque([0])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current] - seen:
                seen.add(neighbor)
                queue.append(neighbor)
        self.assertEqual(365, len(seen))


if __name__ == "__main__":
    unittest.main()
