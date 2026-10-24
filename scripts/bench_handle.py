"""Latency benchmark for the whole pipeline in a WARM process (models already loaded, like a real server).
Uses the REAL LLM and the real database. Run from project root:  python -m scripts.bench_handle

Prints, for 12 typical messages: total time, number of LLM calls, time spent inside the LLM vs. our own code.
It also counts retries and fallbacks to the second model. Refund decisions it triggers are audit rows only
(no refund is created); those audit rows are deleted again at the end.
"""
import json
import statistics
import sys
import time

from app import config, llm
from app.db import get_conn
from app.handle import handle_message

MESSAGES = [  # (customer, message)
    ("C001", "Mera order #45821 kahan hai?"),
    ("C001", "Where is order 45822?"),
    ("C001", "Mera laptop damaged aaya hai, kitne din mein return kar sakta hu?"),
    ("C001", "Kapde return karne ke liye kitne din milte hain?"),
    ("C001", "Refund kitne din mein aata hai UPI se?"),
    ("C001", "wrnty kitni hai phone ki"),
    ("C001", "Free shipping kab milti hai?"),
    ("C002", "Order 45830 ka paisa kat gaya, refund kab milega?"),
    ("C004", "Mera payment kat gaya order #45832 fail ho gaya, refund do"),
    ("C001", "What is the capital of France?"),
    ("C001", "Tum log bekar ho, mujhe manager se baat karni hai"),
    ("C001", "Order late hai aur refund bhi chahiye"),
]


def pct(values, p):
    values = sorted(values)
    return values[int(p * (len(values) - 1))] if values else 0


def run(conn, retriever, generate, messages=MESSAGES, pause=3.0, verbose=True):
    rows = []
    for cust, msg in messages:
        calls = []

        def counting(prompt, system=None, **kw):
            t0 = time.perf_counter()
            try:
                res = generate(prompt, system=system, **kw)
                calls.append({"ms": (time.perf_counter() - t0) * 1000, "model": res.model, "attempts": res.attempts})
                return res
            except llm.LLMUnavailable:
                calls.append({"ms": (time.perf_counter() - t0) * 1000, "model": None, "attempts": 0})
                raise

        t0 = time.perf_counter()
        r = handle_message(conn, cust, msg, retriever=retriever, generate=counting)
        total = (time.perf_counter() - t0) * 1000
        llm_ms = sum(c["ms"] for c in calls)
        rows.append({"msg": msg, "actions": r.actions, "total": total, "llm_calls": len(calls),
                     "llm_ms": llm_ms, "own_ms": total - llm_ms, "calls": calls, "handoff": r.handoff})
        if verbose:
            print(f"{total:7.0f} ms | llm calls {len(calls)} ({llm_ms:6.0f} ms in LLM, {total - llm_ms:5.0f} ms ours) "
                  f"| {r.actions} | {msg[:48]}")
        time.sleep(pause)
    return rows


def summary(rows):
    totals = [r["total"] for r in rows]
    all_calls = [c for r in rows for c in r["calls"]]
    models = {}
    for c in all_calls:
        models[c["model"]] = models.get(c["model"], 0) + 1
    print("\n=== SUMMARY (warm process) ===")
    print(f"total ms per message: median {statistics.median(totals):.0f} | p95 {pct(totals, 0.95):.0f} | max {max(totals):.0f}")
    print(f"LLM calls per message: median {statistics.median(r['llm_calls'] for r in rows):.0f}, max {max(r['llm_calls'] for r in rows)}")
    llm_share = sum(r["llm_ms"] for r in rows) / max(sum(totals), 1)
    print(f"share of total time spent inside the LLM: {llm_share:.0%}")
    print(f"our own code (retrieval, database, checks): median {statistics.median(r['own_ms'] for r in rows):.0f} ms")
    print(f"single LLM call ms: median {statistics.median(c['ms'] for c in all_calls):.0f} | p95 {pct([c['ms'] for c in all_calls], 0.95):.0f}")
    print(f"models that answered: {models} | calls that needed a retry or fallback: {sum(1 for c in all_calls if c['attempts'] > 1)}")
    print(f"messages with no LLM call at all: {sum(1 for r in rows if r['llm_calls'] == 0)}; handoffs: {sum(r['handoff'] for r in rows)}")


if __name__ == "__main__":
    from app.retrieval import Retriever
    sys.stdout.reconfigure(encoding="utf-8")
    conn = get_conn()
    before = conn.execute("SELECT coalesce(max(audit_id), 0) FROM shop.refund_audit").fetchone()[0]
    retriever = Retriever(conn)
    print("warming up (loading models, one throw-away query)...")
    retriever.embed_query("warm up")
    try:
        summary(run(conn, retriever, llm.generate))
    finally:
        conn.execute("DELETE FROM shop.refund_audit WHERE audit_id > %s", (before,))
