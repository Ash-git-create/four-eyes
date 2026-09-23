"""Retrieval metrics and the eval runner.

Usage: uv run evaluate            # runs every mode in search.MODES on the same questions

Metrics are computed over answerable questions (policy, product, combined).
Unanswerable questions have no gold; for them we record the top-1 scores, which
is the data for choosing an "I don't know" threshold later.
"""
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .search import MODES

ANSWERABLE = ("policy", "product", "combined")
# Evaluate at the k the workflow actually requests. Balanced mode's reserved slots depend
# on k, so hit@5 read off a k=10 list would not be what n8n receives.
EVAL_K = 5


def first_gold_rank(retrieved: list[str], gold: set[str]) -> int | None:
    """1-based rank of the first gold chunk, or None if no gold chunk was retrieved."""
    for rank, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in gold:
            return rank
    return None


def hit_at(rank: int | None, k: int) -> float:
    return 1.0 if rank is not None and rank <= k else 0.0


def reciprocal_rank(rank: int | None) -> float:
    return 1.0 / rank if rank is not None else 0.0


def summarise(rows: list[dict]) -> dict:
    """rows: dicts with 'rank' (int|None). Returns hit@1, hit@5, MRR@5 and n."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "hit@1": round(sum(hit_at(r["rank"], 1) for r in rows) / n, 3),
        "hit@5": round(sum(hit_at(r["rank"], 5) for r in rows) / n, 3),
        "mrr@5": round(sum(reciprocal_rank(r["rank"]) for r in rows) / n, 3),
    }


def score_stats(values: list[float]) -> dict:
    if not values:
        return {}
    return {"min": round(min(values), 3), "median": round(statistics.median(values), 3),
            "max": round(max(values), 3)}


def run(searcher, questions: list[dict], mode: str) -> dict:
    per_q, latencies = [], []
    for q in questions:
        t0 = time.perf_counter()
        results = searcher.search(q["question"], EVAL_K, mode=mode)
        latencies.append((time.perf_counter() - t0) * 1000)
        retrieved = [r["chunk_id"] for r in results]
        gold = set(q["gold_chunk_ids"])
        top = results[0] if results else {}
        per_q.append({
            "id": q["id"], "type": q["type"], "lang": q["lang"],
            "rank": first_gold_rank(retrieved, gold) if gold else None,
            "all_gold_in_top5": gold.issubset(retrieved[:5]) if gold else None,
            "top1_score": top.get("score") if mode == "vector" else top.get("rerank_score"),
            "retrieved": retrieved,
        })

    answerable = [r for r in per_q if r["type"] in ANSWERABLE]
    return {
        "mode": mode,
        "overall": summarise(answerable),
        "by_type": {t: summarise([r for r in answerable if r["type"] == t]) for t in ANSWERABLE},
        "by_lang": {l: summarise([r for r in answerable if r["lang"] == l]) for l in ("en", "de")},
        "combined_all_gold_in_top5": sum(bool(r["all_gold_in_top5"]) for r in per_q if r["type"] == "combined"),
        "top1_score_answerable": score_stats([r["top1_score"] for r in answerable]),
        "top1_score_unanswerable": score_stats([r["top1_score"] for r in per_q if r["type"] == "unanswerable"]),
        "latency_ms_p50": round(statistics.median(latencies), 1),
        "latency_note": "in-process Searcher.search (embed + DB query + rerank), warm models, local Mac",
        "per_question": per_q,
    }


def main() -> None:
    from .app import build_searcher

    questions_path = config.REPO_ROOT / "eval" / "questions.jsonl"
    questions = [json.loads(line) for line in questions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    searcher = build_searcher()
    searcher.search("warm-up query", EVAL_K, mode="balanced")  # first call loads kernels; keep it out of latency

    stamp = datetime.now(timezone.utc)
    report = {
        "run_at": stamp.isoformat(),
        "questions_file": str(questions_path.relative_to(config.REPO_ROOT)),
        "n_questions": len(questions),
        "embed_model": config.EMBED_MODEL,
        "rerank_model": config.RERANK_MODEL,
        "rerank_candidates": config.RERANK_CANDIDATES,
        "min_per_source": config.MIN_PER_SOURCE,
        "runs": [run(searcher, questions, mode) for mode in MODES],
    }
    out_dir = config.REPO_ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"retrieval_{stamp:%Y-%m-%dT%H%M%SZ}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))

    for r in report["runs"]:
        print(f"\n== {r['mode']} ==")
        for key in ("overall", "by_type", "by_lang", "combined_all_gold_in_top5",
                    "top1_score_answerable", "top1_score_unanswerable", "latency_ms_p50"):
            print(f"{key}: {r[key]}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
