"""Measure retrieval quality on the golden set (answerable policy questions only).

Compares three setups so we can SEE what each component adds:
  1. dense only
  2. hybrid (dense + keyword, RRF)
  3. hybrid + reranker

Metrics: Recall@1, Recall@3, MRR (did the right document appear, and how high?)
         and Keyword@3 (do the top-3 chunks contain the expected answer text, e.g. "48 hours"?).

Usage: python -m evals.eval_retrieval
"""
import json
import time
from pathlib import Path

from app.retrieval import Retriever

GOLDEN = Path("data/eval/golden_set.jsonl")
INTENTS = {"policy_faq", "damaged_item"}


def load_rows():
    rows = [json.loads(line) for line in GOLDEN.open(encoding="utf-8")]
    return [r for r in rows if r["answerable"] and r["expected_doc"] and r["expected_intent"] in INTENTS]


def doc_rank(results, expected_doc):
    seen = []
    for r in results:
        if r.doc_name not in seen:
            seen.append(r.doc_name)
    return seen.index(expected_doc) + 1 if expected_doc in seen else None


def keyword_hit(results, keywords, k=3):
    if not keywords:
        return True
    text = " ".join(r.content for r in results[:k]).lower()
    return all(kw.lower() in text for kw in keywords)


def evaluate(retriever, rows, verbose=True):
    setups = {
        "dense only": lambda q: retriever.search(q, mode="dense"),
        "hybrid": lambda q: retriever.search(q, mode="hybrid"),
        "hybrid + rerank": lambda q: retriever.retrieve(q, top_n=10, rerank=True),
    }
    summary = {}
    for name, fn in setups.items():
        ranks, kw_hits, fails, t0 = [], [], [], time.perf_counter()
        for r in rows:
            res = fn(r["query"])
            rk = doc_rank(res, r["expected_doc"])
            ranks.append(rk)
            kw_hits.append(keyword_hit(res, r["answer_keywords"]))
            if rk is None or rk > 1 or not kw_hits[-1]:
                fails.append((r["id"], r["query"], rk, kw_hits[-1]))
        n = len(rows)
        summary[name] = {
            "recall@1": sum(1 for x in ranks if x == 1) / n,
            "recall@3": sum(1 for x in ranks if x and x <= 3) / n,
            "mrr": sum(1 / x for x in ranks if x) / n,
            "keyword@3": sum(kw_hits) / n,
            "ms/query": (time.perf_counter() - t0) / n * 1000,
        }
        if verbose:
            print(f"\n{name}")
            for k, v in summary[name].items():
                print(f"  {k:10s} {v:.2f}")
            for f in fails:
                print(f"  miss: {f[0]} rank={f[2]} keyword_ok={f[3]} | {f[1][:60]}")
    return summary


if __name__ == "__main__":
    rows = load_rows()
    print(f"Evaluating {len(rows)} golden queries")
    evaluate(Retriever(), rows)