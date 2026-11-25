"""Deterministic erroneous "student writing" samples for the demo button/tests."""
from __future__ import annotations

import random
from typing import Any, Dict, List, Sequence


def _jitter(stroke: Sequence[Sequence[float]], rng: random.Random) -> List[List[float]]:
    # A per-stroke drift plus smaller per-point wobble keeps the shape and
    # direction recognisable (Frechet well under the default threshold).
    ox = rng.uniform(-42, 42)
    oy = rng.uniform(-42, 42)
    return [[round(float(x) + ox + rng.gauss(0, 13), 1),
             round(float(y) + oy + rng.gauss(0, 13), 1)] for x, y in stroke]


def _scribble(rng: random.Random) -> List[List[float]]:
    # A small extraneous zigzag in the top-left margin, far from every glyph.
    base_x, base_y = 70.0, 95.0
    pts = []
    for i in range(9):
        pts.append([round(base_x + i * 17 + rng.uniform(-5, 5), 1),
                    round(base_y + (18 if i % 2 else -14) + rng.uniform(-6, 6), 1)])
    return pts


def make_sample(model: Sequence[Sequence[float]], seed: int = 20261124) -> List[Dict[str, Any]]:
    """Return actual strokes (with uids/preserved=False) carrying typical errors.

    The sample omits one reference stroke (an add is needed), reorders two
    others (a move is needed when possible), and adds one extraneous scribble
    (a delete is needed).
    """
    rng = random.Random(seed)
    n = len(model)
    # Choose errors so each stroke count still showcases the key actions:
    #   n == 1 : drop the only stroke  -> delete scribble + add
    #   n == 2 : keep both but swap     -> one move + delete scribble
    #   n >= 3 : omit stroke #2, swap two of the rest -> add + move + delete
    if n == 1:
        omit = 0
    elif n == 2:
        omit = -1
    else:
        omit = 1

    kept_indices = [i for i in range(n) if i != omit]
    drawn = [_jitter(model[i], rng) for i in kept_indices]

    # Create an order mistake: swap the first two kept strokes.
    order = list(range(len(drawn)))
    if len(order) >= 2:
        order[0], order[1] = order[1], order[0]

    strokes: List[Dict[str, Any]] = []
    uid = 0
    for k in order:
        strokes.append({"uid": f"s{uid}", "points": drawn[k], "preserved": False})
        uid += 1
    strokes.append({"uid": f"s{uid}", "points": _scribble(rng), "preserved": False})
    return strokes
