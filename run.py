"""Local development entry point:  python run.py  (then open http://127.0.0.1:8000)."""
import os

from app.server import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
