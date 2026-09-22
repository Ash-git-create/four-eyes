"""HTTP layer for the retrieval service.

Day 1: /search returns fixed results so the n8n workflow can be built against
the real response contract before any retrieval exists. `stub: true` makes
that explicit to every caller.
"""
from flask import Flask, jsonify, request

MAX_K = 20

STUB_RESULTS = [
    {
        "chunk_id": "sample-policy-returns#0",
        "source_id": "sample-policy-returns",
        "text": "SAMPLE POLICY: Unopened products can be returned within 30 days.",
        "score": 0.0,
    },
]


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/search")
    def search():
        body = request.get_json(silent=True) or {}
        query = body.get("query")
        if not isinstance(query, str) or not query.strip():
            return jsonify(error="'query' must be a non-empty string"), 400

        k = body.get("k", 5)
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
            return jsonify(error=f"'k' must be an integer between 1 and {MAX_K}"), 400

        return jsonify(query=query, k=k, stub=True, results=STUB_RESULTS[:k])

    return app


def main() -> None:
    # 127.0.0.1 only; n8n reaches this via host.docker.internal on Docker Desktop.
    create_app().run(host="127.0.0.1", port=8000)
