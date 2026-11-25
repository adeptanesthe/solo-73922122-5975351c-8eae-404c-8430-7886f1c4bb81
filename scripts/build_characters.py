"""Generate shipped model-character JSON files in the shared 1000-grid.

Run:  python3 scripts/build_characters.py
Each stroke is sampled to at most ``SAMPLES`` points (<= 64).  Stroke order in
every glyph follows standard PRC stroke order.
"""
from __future__ import annotations

import json
import os
from typing import List, Sequence, Tuple

Point = Tuple[float, float]

SAMPLES = 28
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "app", "data", "characters")


def qbez(p0: Point, p1: Point, p2: Point, n: int = SAMPLES) -> List[Point]:
    out = []
    for i in range(n):
        t = i / (n - 1)
        u = 1 - t
        x = u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0]
        y = u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]
        out.append((round(x, 1), round(y, 1)))
    return out


def cbez(p0: Point, p1: Point, p2: Point, p3: Point, n: int = SAMPLES) -> List[Point]:
    out = []
    for i in range(n):
        t = i / (n - 1)
        u = 1 - t
        x = (u ** 3 * p0[0] + 3 * u * u * t * p1[0]
             + 3 * u * t * t * p2[0] + t ** 3 * p3[0])
        y = (u ** 3 * p0[1] + 3 * u * u * t * p1[1]
             + 3 * u * t * t * p2[1] + t ** 3 * p3[1])
        out.append((round(x, 1), round(y, 1)))
    return out


def line(p0: Point, p1: Point, n: int = 12) -> List[Point]:
    return qbez(p0, ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2), p1, n)


def poly(points: Sequence[Point], per_seg: int = 10) -> List[Point]:
    """Sample a sharp-cornered polyline while keeping the corner."""
    out: List[Point] = []
    for si in range(len(points) - 1):
        a, b = points[si], points[si + 1]
        seg = line(a, b, per_seg)
        if si:
            seg = seg[1:]
        out.extend(seg)
    return [(round(x, 1), round(y, 1)) for x, y in out]


CHARACTERS = [
    {
        "id": "yi",
        "char": "一",
        "meaning": "数字一",
        "strokes": [
            line((180, 520), (825, 500), 20),
        ],
    },
    {
        "id": "shi",
        "char": "十",
        "meaning": "十",
        "strokes": [
            line((215, 430), (785, 430), 18),                 # 横
            line((500, 235), (500, 830), 22),                 # 竖
        ],
    },
    {
        "id": "ren",
        "char": "人",
        "meaning": "人",
        "strokes": [
            qbez((520, 235), (470, 520), (255, 815)),         # 撇
            cbez((500, 300), (560, 500), (640, 660), (790, 800)),  # 捺
        ],
    },
    {
        "id": "ba",
        "char": "八",
        "meaning": "八",
        "strokes": [
            qbez((430, 270), (380, 520), (235, 770)),         # 撇
            cbez((560, 285), (600, 500), (690, 650), (790, 770)),  # 捺
        ],
    },
    {
        "id": "da",
        "char": "大",
        "meaning": "大",
        "strokes": [
            line((225, 375), (775, 375), 18),                 # 横
            qbez((505, 235), (470, 560), (265, 835)),         # 撇
            cbez((500, 395), (570, 560), (660, 700), (795, 825)),  # 捺
        ],
    },
    {
        "id": "kou",
        "char": "口",
        "meaning": "口",
        "strokes": [
            line((305, 300), (300, 745), 16),                 # 竖
            poly([(305, 300), (700, 300), (695, 745)], 12),   # 横折
            line((300, 745), (695, 745), 14),                 # 横(底)
        ],
    },
    {
        "id": "tu",
        "char": "土",
        "meaning": "泥土",
        "strokes": [
            line((355, 330), (645, 330), 12),                 # 短横
            line((500, 300), (500, 780), 20),                 # 竖
            line((235, 780), (765, 780), 18),                 # 长横
        ],
    },
    {
        "id": "mu",
        "char": "木",
        "meaning": "树木",
        "strokes": [
            line((215, 360), (785, 360), 18),                 # 横
            line((500, 215), (500, 845), 24),                 # 竖
            qbez((500, 395), (420, 560), (250, 770)),         # 撇
            cbez((500, 395), (580, 560), (665, 680), (765, 770)),  # 捺
        ],
    },
]


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    index = []
    for ch in CHARACTERS:
        assert len(ch["strokes"]) <= 10
        for s in ch["strokes"]:
            assert len(s) <= 64, (ch["id"], len(s))
        path = os.path.join(OUT_DIR, f"{ch['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"id": ch["id"], "char": ch["char"],
                       "meaning": ch["meaning"], "grid": 1000,
                       "strokes": ch["strokes"]}, f, ensure_ascii=False)
        index.append({"id": ch["id"], "char": ch["char"],
                      "meaning": ch["meaning"], "stroke_count": len(ch["strokes"])})
        print("wrote", path, "strokes=", len(ch["strokes"]),
              "points=", [len(s) for s in ch["strokes"]])
    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"grid": 1000, "characters": index}, f, ensure_ascii=False, indent=2)
    print("index:", [c["id"] for c in CHARACTERS])


if __name__ == "__main__":
    main()
