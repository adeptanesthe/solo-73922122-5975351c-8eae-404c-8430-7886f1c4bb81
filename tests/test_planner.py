import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.planner import apply_move, build_plan, solve  # noqa: E402

H = [(100, 500), (900, 500)]
V = [(500, 120), (500, 880)]
MODEL = [H, V]
FAR = [(60, 80), (220, 120), (90, 170)]  # extraneous corner scribble


def stroke(points, uid, preserved=False):
    return {"uid": uid, "points": [list(p) for p in points], "preserved": preserved}


def apply_plan_steps(strokes, plan):
    """Apply all move/delete steps sequentially (mirrors the canvas preview)."""
    arr = [dict(s) for s in strokes]
    for step in plan["steps"]:
        if step["kind"] == "delete":
            idx = step["index"]
            if step.get("uid") is not None and arr[idx]["uid"] != step["uid"]:
                idx = next(i for i, s in enumerate(arr) if s["uid"] == step["uid"])
            arr.pop(idx)
        elif step["kind"] == "move":
            apply_move(arr, step["from"], step["to"])
    return arr


class TestBasicPlans(unittest.TestCase):
    def test_perfect_order_zero_actions(self):
        p = build_plan([stroke(H, "a"), stroke(V, "b")], MODEL, 180)
        self.assertTrue(p["feasible"])
        self.assertEqual(p["cost"], 0)
        self.assertEqual(p["counts"], {"move": 0, "delete": 0, "add": 0})
        self.assertIsNone(p["next_action"])

    def test_swapped_order_one_move(self):
        p = build_plan([stroke(V, "b"), stroke(H, "a")], MODEL, 180)
        self.assertTrue(p["feasible"])
        self.assertEqual(p["cost"], 1)
        self.assertEqual(p["counts"]["move"], 1)
        na = p["next_action"]
        self.assertEqual(na["kind"], "move")
        self.assertEqual((na["from"], na["to"]), (1, 0))
        arr = apply_plan_steps([stroke(V, "b"), stroke(H, "a")], p)
        self.assertEqual([s["uid"] for s in arr], ["a", "b"])

    def test_extra_stroke_one_delete(self):
        ss = [stroke(H, "a"), stroke(FAR, "x"), stroke(V, "b")]
        p = build_plan(ss, MODEL, 180)
        self.assertTrue(p["feasible"])
        self.assertEqual(p["counts"]["delete"], 1)
        self.assertEqual(p["cost"], 1)
        self.assertEqual(p["next_action"]["kind"], "delete")
        arr = apply_plan_steps(ss, p)
        self.assertEqual([s["uid"] for s in arr], ["a", "b"])

    def test_missing_stroke_one_add(self):
        p = build_plan([stroke(H, "a")], MODEL, 180)
        self.assertTrue(p["feasible"])
        self.assertEqual(p["counts"]["add"], 1)
        self.assertEqual(p["cost"], 1)
        self.assertIsNone(p["next_action"])  # nothing to adopt: only a supplement
        add_steps = [s for s in p["steps"] if s["kind"] == "add"]
        self.assertEqual([s["slot"] for s in add_steps], [1])
        self.assertEqual(len(add_steps[0]["points"]), len(V))

    def test_move_delete_add_combo(self):
        # V first (wrong order), extra scribble, H present, missing nothing? add H?
        # Use 3-slot model to force an add as well.
        m3 = [H, V, [(200, 820), (800, 820)]]
        ss = [stroke(V, "b"), stroke(FAR, "x"), stroke(H, "a")]  # missing slot 2
        p = build_plan(ss, m3, 180)
        self.assertTrue(p["feasible"], p.get("reason"))
        self.assertEqual(p["counts"]["delete"], 1)
        self.assertEqual(p["counts"]["move"], 1)
        self.assertEqual(p["counts"]["add"], 1)
        self.assertEqual(p["cost"], 3)
        arr = apply_plan_steps(ss, p)
        # after real actions: two actual strokes left, in H(slot0),V(slot1) order
        self.assertEqual([s["uid"] for s in arr], ["a", "b"])
        r = solve([s["points"] for s in arr], [False, False], m3, 180)
        self.assertEqual((r.moves, r.deletes), (0, 0))
        self.assertEqual(r.adds, 1)

    def test_preserved_cannot_be_deleted_forces_use(self):
        # The preserved scribble matches no slot -> infeasible rather than delete.
        ss = [stroke(FAR, "x", preserved=True), stroke(H, "a")]
        p = build_plan(ss, MODEL, 120)
        self.assertFalse(p["feasible"])
        self.assertIn("无解", p["reason"])

    def test_preserved_order_conflict_no_solution(self):
        ss = [stroke(V, "b", preserved=True), stroke(H, "a", preserved=True)]
        p = build_plan(ss, MODEL, 180)
        self.assertFalse(p["feasible"])
        self.assertIn("顺序", p["reason"])

    def test_preserved_stroke_anchors_and_allows_solution(self):
        # H preserved in first position is already correct; V after it.
        ss = [stroke(H, "a", preserved=True), stroke(V, "b")]
        p = build_plan(ss, MODEL, 180)
        self.assertTrue(p["feasible"])
        self.assertEqual(p["cost"], 0)

    def test_preserved_stroke_need_not_move_others_move_around_it(self):
        # H is currently second and preserved, so H itself must stay unmoved.
        # The non-preserved V can be moved from first to second around H.
        ss = [stroke(V, "b"), stroke(H, "a", preserved=True)]
        p = build_plan(ss, MODEL, 180)
        self.assertTrue(p["feasible"], p.get("reason"))
        self.assertEqual(p["cost"], 1)
        na = p["next_action"]
        self.assertEqual(na["kind"], "move")
        self.assertEqual(na["uid"], "b")
        self.assertEqual((na["from"], na["to"]), (0, 1))
        arr = apply_plan_steps(ss, p)
        self.assertEqual([s["uid"] for s in arr], ["a", "b"])

    def test_validation_limits(self):
        too_many = [stroke(H, i) for i in range(11)]
        p = build_plan(too_many, MODEL, 180)
        self.assertFalse(p["feasible"])
        big = [{"uid": "z", "points": [[i, i] for i in range(65)]}]
        p2 = build_plan(big, MODEL, 1e9)
        self.assertFalse(p2["feasible"])


class TestOptimalityProperties(unittest.TestCase):
    def test_next_action_always_drops_cost_by_one(self):
        rng = random.Random(20261124)

        def rnd(n):
            return [(rng.randrange(1000), rng.randrange(1000)) for _ in range(n)]

        checked = 0
        for _ in range(120):
            n = rng.randrange(1, 6)
            k = rng.randrange(1, 8)
            model = [rnd(rng.randrange(2, 16)) for _ in range(n)]
            ss = []
            for i in range(k):
                if rng.random() < 0.65:
                    src = rng.choice(model)
                    pts = [[x + rng.randrange(-70, 71), y + rng.randrange(-70, 71)]
                           for x, y in src]
                else:
                    pts = rnd(rng.randrange(2, 16))
                ss.append(stroke(pts, f"u{i}", preserved=rng.random() < 0.18))
            threshold = rng.choice([140, 260, 10 ** 9])
            plan = build_plan(ss, model, threshold)
            if not plan["feasible"]:
                continue
            checked += 1
            c = plan["cost"]
            na = plan["next_action"]
            if na is None:
                self.assertEqual(c, plan["counts"]["add"])
                continue
            trial = [dict(s) for s in ss]
            if na["kind"] == "delete":
                trial.pop(na["index"])
            else:
                apply_move(trial, na["from"], na["to"])
            r = solve([s["points"] for s in trial],
                      [s["preserved"] for s in trial], model, threshold)
            self.assertTrue(r.feasible)
            self.assertEqual(r.cost, c - 1,
                             msg=f"cost {c} -> {r.cost} after {na}")
        self.assertGreater(checked, 20)

    def test_full_step_sequence_reaches_terminal_state(self):
        rng = random.Random(99)

        def rnd(n):
            return [(rng.randrange(1000), rng.randrange(1000)) for _ in range(n)]

        for _ in range(60):
            n = rng.randrange(1, 6)
            model = [rnd(rng.randrange(2, 12)) for _ in range(n)]
            k = rng.randrange(0, 8)
            ss = [stroke(rnd(rng.randrange(2, 12)), f"u{i}") for i in range(k)]
            plan = build_plan(ss, model, rng.choice([200, 10 ** 9]))
            if not plan["feasible"]:
                continue
            arr = apply_plan_steps(ss, plan)
            r = solve([s["points"] for s in arr],
                      [False] * len(arr), model, plan["threshold"])
            self.assertTrue(r.feasible)
            self.assertEqual((r.moves, r.deletes), (0, 0))
            self.assertEqual(r.adds, plan["counts"]["add"])


if __name__ == "__main__":
    unittest.main()
