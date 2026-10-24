"""Run every test suite (tests/test_*.py) and print ONE summary table.
Usage (Docker up, from the project root):
    python -m scripts.run_all_tests              # run all suites
    python -m scripts.run_all_tests --reseed     # first reset the synthetic orders (python -m scripts.seed_db)

A suite passes when it prints 'all N checks passed'. Output of passing suites is hidden (some suites print an
intentional, harmless crash traceback). For a failing suite the first FAIL line or the last lines are shown.
"""
import re
import subprocess
import sys
import time
from pathlib import Path


def run(module: str):
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", module], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    m = re.search(r"all (\d+) checks passed", out)
    secs = time.time() - t0
    if m and p.returncode == 0:
        return True, int(m.group(1)), secs, ""
    fail = next((l for l in out.splitlines() if l.startswith("FAIL") or "test failed" in l), None)
    detail = fail or " | ".join(l.strip() for l in out.strip().splitlines()[-3:])
    return False, 0, secs, detail


if __name__ == "__main__":
    if "--reseed" in sys.argv:
        r = subprocess.run([sys.executable, "-m", "scripts.seed_db"], capture_output=True, text=True)
        print("reseeded orders" if r.returncode == 0 else f"RESEED FAILED: {r.stderr[-300:]}")
    suites = sorted(p.stem for p in Path("tests").glob("test_*.py"))
    total, failed = 0, []
    print(f"{'suite':24s} {'result':8s} {'checks':>6s} {'sec':>5s}")
    for name in suites:
        ok, n, secs, detail = run(f"tests.{name}")
        total += n
        print(f"{name:24s} {'PASS' if ok else 'FAIL':8s} {n:6d} {secs:5.1f}" + ("" if ok else f"   {detail[:200]}"))
        if not ok:
            failed.append(name)
    print(f"\n{len(suites) - len(failed)} of {len(suites)} suites passed, {total} checks in total")
    if failed:
        print("failed:", ", ".join(failed))
    sys.exit(1 if failed else 0)
