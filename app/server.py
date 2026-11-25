"""Flask application: canvas UI plus the revision-planning JSON API."""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request

from .core.frechet import GRID
from .core.planner import build_plan
from .core.sample import make_sample

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "characters")
DEFAULT_THRESHOLD = 180.0


def _load_index() -> Dict[str, Any]:
    with open(os.path.join(DATA_DIR, "index.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def load_character(char_id: str) -> Dict[str, Any]:
    # Guard against path traversal: ids are simple slugs.
    safe = "".join(c for c in char_id if c.isalnum() or c in "-_")
    path = os.path.join(DATA_DIR, f"{safe}.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(char_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["JSON_AS_ASCII"] = False

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            grid=int(GRID),
            default_threshold=DEFAULT_THRESHOLD,
        )

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.get("/api/characters")
    def api_characters():
        return jsonify(_load_index())

    @app.get("/api/characters/<char_id>")
    def api_character(char_id: str):
        try:
            data = load_character(char_id)
        except FileNotFoundError:
            return jsonify({"error": "unknown character"}), 404
        return jsonify(data)

    @app.get("/api/sample/<char_id>")
    def api_sample(char_id: str):
        try:
            data = load_character(char_id)
        except FileNotFoundError:
            return jsonify({"error": "unknown character"}), 404
        strokes = make_sample(data["strokes"])
        return jsonify({"character": data, "strokes": strokes})

    @app.post("/api/plan")
    def api_plan():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"feasible": False, "reason": "请求体必须是 JSON 对象"}), 400

        threshold = body.get("threshold", DEFAULT_THRESHOLD)
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            return jsonify({"feasible": False, "reason": "上限必须是数字"}), 400
        if not math.isfinite(threshold):
            return jsonify({"feasible": False, "reason": "上限必须是有限数字"}), 400
        if threshold < 0:
            threshold = 0.0

        # Resolve the reference character: explicit model wins, else id.
        model = body.get("model")
        if not isinstance(model, list) or not model:
            char_id = body.get("character_id") or "shi"
            try:
                model = load_character(str(char_id))["strokes"]
            except FileNotFoundError:
                return jsonify({"feasible": False, "reason": f"未知范字：{char_id}"}), 404

        strokes = body.get("strokes", [])
        if not isinstance(strokes, list):
            return jsonify({"feasible": False, "reason": "strokes 必须是数组"}), 400

        plan = build_plan(strokes, model, threshold)
        # Domain failures (invalid limits, no solution) are returned as
        # feasible=false with a Chinese reason so the page can explain them.
        return jsonify(plan), 200

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
