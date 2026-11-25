import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.server import app  # noqa: E402
from app.core.planner import apply_move  # noqa: E402

FAR = [[60, 80], [220, 120], [90, 170]]  # extraneous corner scribble
_SHI = json.load(open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "data", "characters", "shi.json"), encoding="utf-8"))
H = _SHI["strokes"][0]
V = _SHI["strokes"][1]


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_health_and_page(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("笔顺订正", html)
        self.assertIn("/static/js/app.js", html)

    def test_character_index_and_detail(self):
        data = self.client.get("/api/characters").get_json()
        ids = [c["id"] for c in data["characters"]]
        self.assertIn("mu", ids)
        detail = self.client.get("/api/characters/kou").get_json()
        self.assertEqual(len(detail["strokes"]), 3)
        self.assertEqual(self.client.get("/api/characters/nope").status_code, 404)

    def test_sample_endpoint(self):
        data = self.client.get("/api/sample/mu").get_json()
        self.assertIn("strokes", data)
        self.assertLessEqual(len(data["strokes"]), 11)  # one extra allowed at source
        # sample generator itself adds at most N kept-ish + one; N<=10 so <=10
        self.assertLessEqual(len(data["strokes"]), 10)

    def test_plan_exact_swap(self):
        body = {"character_id": "shi", "threshold": 180,
                "strokes": [{"uid": "b", "points": V},
                            {"uid": "a", "points": H}]}
        p = self.client.post("/api/plan", json=body).get_json()
        self.assertTrue(p["feasible"], p.get("reason"))
        self.assertEqual(p["cost"], 1)
        self.assertEqual(p["next_action"]["kind"], "move")

    def test_plan_no_solution_preserved_swap(self):
        body = {"character_id": "shi", "threshold": 180,
                "strokes": [{"uid": "b", "points": V, "preserved": True},
                            {"uid": "a", "points": H, "preserved": True}]}
        p = self.client.post("/api/plan", json=body).get_json()
        self.assertFalse(p["feasible"])
        self.assertIn("无解", p["reason"])
        self.assertIn("顺序", p["reason"])  # both match, but preserved order clashes

    def test_adopt_loop_recomputes_with_actual_order(self):
        # swapped + extra against shi -> move + delete + maybe add? shi has 2,
        # both present, so only one move + one delete.
        strokes = [
            {"uid": "b", "points": V, "preserved": False},
            {"uid": "x", "points": FAR, "preserved": False},
            {"uid": "a", "points": H, "preserved": False},
        ]
        costs = []
        for _ in range(12):
            p = self.client.post("/api/plan", json={
                "character_id": "shi", "threshold": 180, "strokes": strokes,
            }).get_json()
            self.assertTrue(p["feasible"], p.get("reason"))
            costs.append(p["cost"])
            na = p["next_action"]
            if na is None:
                terminal = p
                break
            if na["kind"] == "delete":
                strokes.pop(na["index"])
            else:
                apply_move(strokes, na["from"], na["to"])
        else:  # pragma: no cover
            self.fail("adopt loop did not terminate")

        # Every adoption lowers the optimum by exactly one.
        for a, b in zip(costs, costs[1:]):
            self.assertEqual(b, a - 1)
        self.assertEqual(terminal["counts"]["move"], 0)
        self.assertEqual(terminal["counts"]["delete"], 0)
        self.assertEqual(len(strokes), 2)
        self.assertEqual([s["uid"] for s in strokes], ["a", "b"])

    def test_sample_mu_adopt_loop(self):
        sample = self.client.get("/api/sample/mu").get_json()
        strokes = sample["strokes"]
        last_cost = None
        for _ in range(12):
            p = self.client.post("/api/plan", json={
                "character_id": "mu", "threshold": 180, "strokes": strokes,
            }).get_json()
            self.assertTrue(p["feasible"], p.get("reason"))
            if last_cost is not None:
                self.assertEqual(p["cost"], last_cost - 1)
            last_cost = p["cost"]
            na = p["next_action"]
            if na is None:
                self.assertEqual(p["counts"]["move"], 0)
                self.assertEqual(p["counts"]["delete"], 0)
                self.assertGreaterEqual(p["counts"]["add"], 1)
                break
            if na["kind"] == "delete":
                strokes.pop(na["index"])
            else:
                apply_move(strokes, na["from"], na["to"])

    def test_invalid_threshold_and_payload(self):
        r = self.client.post("/api/plan", data="not json",
                             headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        body = {"character_id": "shi", "threshold": "big", "strokes": []}
        p = self.client.post("/api/plan", json=body)
        self.assertEqual(p.status_code, 400)
        self.assertFalse(p.get_json()["feasible"])


if __name__ == "__main__":
    unittest.main()
