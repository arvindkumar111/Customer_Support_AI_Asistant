"""Measure the router on the golden set (32 messages) with the REAL LLM.
Usage: python -m evals.eval_router
Prints accuracy, a confusion list of mistakes, and the router latency. Provider outages are shown as ERROR, not as wrong answers.
"""
import json
import statistics
import time
from collections import Counter
from pathlib import Path

from app import llm
from app.router import route

GOLDEN = Path("data/eval/golden_set.jsonl")
EXPECTED = {   # golden expected_intent -> set of router actions
    "policy_faq": {"policy"}, "damaged_item": {"policy"}, "return_request": {"return"},
    "order_status": {"order_status"}, "clarify": {"order_status"}, "refund_request": {"refund"},
    "escalate_human": {"human"}, "out_of_scope": {"decline"},
}
MULTI = {"G031": {"order_status", "refund"}, "G032": {"order_status", "policy"}}   # multi_intent rows


def main(pause=4.0, retries=2, retry_wait=15):
    rows = [json.loads(line) for line in GOLDEN.open(encoding="utf-8")]
    ok, wrong, errors, lat = 0, [], 0, []
    for r in rows:
        want = MULTI.get(r["id"]) or EXPECTED.get(r["expected_intent"])
        if want is None:
            continue
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            rt = route(r["query"])
            ms = (time.perf_counter() - t0) * 1000
            if rt.reason != "router_llm_unavailable" or attempt == retries:
                break
            time.sleep(retry_wait)
        if rt.reason == "router_llm_unavailable":
            errors += 1
            print(f"ERROR {r['id']} provider unavailable")
            continue
        lat.append(ms)
        if set(rt.actions) == want:
            ok += 1
        else:
            wrong.append((r["id"], r["query"], sorted(want), rt.actions, rt.reason, rt.confidence, rt.raw_output))
        time.sleep(pause)
    n = ok + len(wrong)
    print(f"\nrouter accuracy: {ok}/{n} = {ok / max(n, 1):.2f}   (provider errors: {errors})")
    for w in wrong:
        print(f"MISS {w[0]} | {w[1][:60]} | wanted {w[2]} got {w[3]} ({w[4]}, conf {w[5]})")
        if w[6]:
            print(f"     raw LLM output: {w[6][:300]!r}")
    if lat:
        lat.sort()
        print(f"latency ms: median {statistics.median(lat):.0f}, p95 {lat[int(0.95 * (len(lat) - 1))]:.0f}")
    print("reasons:", dict(Counter(w[4] for w in wrong)))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    main()
