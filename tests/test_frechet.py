import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.frechet import discrete_frechet, resample, stroke_distance  # noqa: E402


class TestFrechet(unittest.TestCase):
    def test_identical_is_zero(self):
        p = [(0, 0), (10, 5), (20, 0)]
        self.assertAlmostEqual(discrete_frechet(p, [list(x) for x in p]), 0.0)

    def test_constant_offset(self):
        p = [(0, 0), (10, 0)]
        q = [(0, 5), (10, 5)]
        self.assertAlmostEqual(discrete_frechet(p, q), 5.0)

    def test_direction_is_preserved(self):
        # Reversing one polyline must NOT be treated as the same stroke.
        forward = [(0, 0), (10, 0)]
        reversed_q = [(10, 0), (0, 0)]
        self.assertAlmostEqual(discrete_frechet(forward, forward), 0.0)
        self.assertAlmostEqual(discrete_frechet(forward, reversed_q), 10.0)

    def test_perpendicular_lines(self):
        h = [(100, 100), (900, 100)]
        v = [(500, 100), (500, 900)]
        d = stroke_distance(h, v)
        self.assertGreater(d, 400)

    def test_empty(self):
        self.assertTrue(math.isinf(discrete_frechet([], [(0, 0)])))

    def test_resample_basic_and_limits(self):
        pts = [(0, 0), (100, 0)]
        r = resample(pts, 11)
        self.assertEqual(len(r), 11)
        self.assertAlmostEqual(r[0][0], 0.0)
        self.assertAlmostEqual(r[-1][0], 100.0)
        # evenly spaced
        self.assertAlmostEqual(r[1][0], 10.0, places=4)
        # single point taps do not produce empty output
        self.assertEqual(len(resample([(3, 4)], 32)), 32)


if __name__ == "__main__":
    unittest.main()
