"""HTTP layer for the retrieval service. POST /search {query, k?, mode?}, POST /redact {text}."""
from flask import Flask, jsonify, request

from .redaction import redact
from .search import MODES, Searcher

MAX_K = 20


def build_searcher() -> Searcher:
    from . import config, store
    from .models import Embedder, Reranker

    url = config.database_url()

    def vector_search(qvec, limit, source_type=None):
        # One connection per request: fine at this scale, no pool to manage.
        with store.connect(url) as conn:
            return store.vector_search(conn, qvec, limit, source_type)

    return Searcher(Embedder(), Reranker(), vector_search)


def create_app(searcher: Searcher | None = None) -> Flask:
    app = Flask(__name__)
    searcher = searcher or build_searcher()  # load models at startup, not on first request

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

        mode = body.get("mode", "balanced")
        if mode not in MODES:
            return jsonify(error=f"'mode' must be one of {', '.join(MODES)}"), 400

        results = searcher.search(query.strip(), k, mode=mode)
        return jsonify(query=query, k=k, mode=mode, results=results)

    @app.post("/redact")
    def redact_text():
        body = request.get_json(silent=True) or {}
        text = body.get("text")
        if not isinstance(text, str):
            return jsonify(error="'text' must be a string"), 400
        redacted, counts = redact(text)
        # Counts only: the response never echoes what was removed.
        return jsonify(text=redacted, redactions=counts)

    return app


def main() -> None:
    # 127.0.0.1 only; n8n reaches this via host.docker.internal on Docker Desktop.
    create_app().run(host="127.0.0.1", port=8000)
