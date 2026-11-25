"""Geometry helpers: arc-length resampling and discrete Frechet distance.

All points live on the shared 0..GRID coordinate grid.  Distances are in
grid units.  Stroke direction is preserved: the discrete Frechet recurrence
runs from the first point to the last and never reverses either curve.
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

Point = Tuple[float, float]

GRID = 1000.0
# Number of evenly spaced samples used for distance computation.  Keeping this
# fixed makes the distance robust to how many pointer events a browser fired.
RESAMPLE_POINTS = 32
# Hard limits from the specification.
MAX_STROKES = 10
MAX_POINTS_PER_STROKE = 64


def euclidean(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def path_length(points: Sequence[Sequence[float]]) -> float:
    total = 0.0
    for i in range(1, len(points)):
        total += euclidean(points[i - 1], points[i])
    return total


def resample(points: Sequence[Sequence[float]], count: int = RESAMPLE_POINTS) -> List[Point]:
    """Return ``count`` points evenly spaced by arc length.

    Endpoints are included.  A single-point (tap) stroke becomes ``count``
    identical points so callers never have to deal with empty sequences.
    """
    if count <= 1:
        return [(float(points[0][0]), float(points[0][1]))] if points else []
    if not points:
        return []

    pts: List[Point] = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) == 1:
        return [pts[0]] * count

    total = path_length(pts)
    if total <= 1e-9:
        return [pts[0]] * count

    interval = total / (count - 1)
    out: List[Point] = [pts[0]]
    accum = 0.0
    idx = 0
    cur = pts[0]
    # Walk segments, emitting a point every ``interval`` arc units.
    while len(out) < count - 1 and idx < len(pts) - 1:
        nxt = pts[idx + 1]
        seg = euclidean(cur, nxt)
        if seg <= 1e-12:
            idx += 1
            cur = nxt
            continue
        if accum + seg >= interval:
            remain = interval - accum
            t = remain / seg
            nx = cur[0] + (nxt[0] - cur[0]) * t
            ny = cur[1] + (nxt[1] - cur[1]) * t
            new_pt = (nx, ny)
            out.append(new_pt)
            cur = new_pt
            accum = 0.0
        else:
            accum += seg
            cur = nxt
            idx += 1
    # Guard against floating point shortfalls.
    while len(out) < count:
        out.append(pts[-1])
    out[-1] = pts[-1]
    return out[:count]


def discrete_frechet(p: Sequence[Sequence[float]], q: Sequence[Sequence[float]]) -> float:
    """Discrete Frechet distance between two ordered polylines.

    Direction is taken as given (no reversal).  Uses the standard dynamic
    program with two rows.  Returns ``inf`` if either sequence is empty.
    """
    n, m = len(p), len(q)
    if n == 0 or m == 0:
        return math.inf

    inf = math.inf
    prev = [inf] * m
    for i in range(n):
        cur = [inf] * m
        pi = p[i]
        for j in range(m):
            d = euclidean(pi, q[j])
            if i == 0 and j == 0:
                cur[j] = d
            elif i == 0:
                cur[j] = max(d, cur[j - 1])
            elif j == 0:
                cur[j] = max(d, prev[j])
            else:
                best = prev[j]
                up_left = prev[j - 1]
                left = cur[j - 1]
                if up_left < best:
                    best = up_left
                if left < best:
                    best = left
                cur[j] = max(d, best)
        prev = cur
    return prev[m - 1]


def stroke_distance(
    actual: Sequence[Sequence[float]],
    model: Sequence[Sequence[float]],
    samples: int = RESAMPLE_POINTS,
) -> float:
    """Frechet distance after fair arc-length resampling of both strokes."""
    return discrete_frechet(resample(actual, samples), resample(model, samples))
