"""Check that every project file exists and has the expected content size (catches missing or wrongly saved files).
Usage (from the project root):  python -m scripts.verify_files

For each file the expected size is (bytes with Unix line endings, number of lines). On Windows each line ending
adds one byte, so a file is OK if its size is between `bytes` and `bytes + lines + 2`. A file that is far from that
range was saved with different content (for example one file saved over another).
Config-like files you edited on purpose (e.g. .env.example) may legitimately differ.
Generated from the current project; regenerate after big changes.
"""
import sys
from pathlib import Path

EXPECTED = {
    ".env.example": (787, 14),
    "README.md": (3800, 59),
    "app/__init__.py": (0, 0),
    "app/api.py": (9225, 218),
    "app/chunking.py": (1382, 40),
    "app/config.py": (3058, 49),
    "app/db.py": (328, 11),
    "app/guardrails.py": (6683, 158),
    "app/handle.py": (13414, 255),
    "app/ingest.py": (6016, 137),
    "app/llm.py": (5993, 117),
    "app/logging_setup.py": (1817, 49),
    "app/order_flow.py": (7426, 148),
    "app/rag.py": (7118, 139),
    "app/ratelimit.py": (2131, 52),
    "app/refund_flow.py": (10058, 174),
    "app/retrieval.py": (6889, 165),
    "app/return_flow.py": (7201, 121),
    "app/router.py": (10172, 185),
    "app/session.py": (1683, 43),
    "app/static/app.css": (3820, 47),
    "app/static/app.js": (6355, 160),
    "app/static/index.html": (2282, 53),
    "app/tickets.py": (506, 7),
    "app/tools/__init__.py": (0, 0),
    "app/tools/orders.py": (2377, 55),
    "app/tools/refunds.py": (5797, 112),
    "app/tools/returns.py": (2814, 58),
    "data/eval/golden_set.jsonl": (10642, 32),
    "data/eval/red_team.jsonl": (3483, 14),
    "data/policies/cancellation_policy.md": (515, 10),
    "data/policies/payments_failed.md": (695, 13),
    "data/policies/policy_params.json": (110, 4),
    "data/policies/product_lines.json": (128, 5),
    "data/policies/refunds_policy.md": (794, 16),
    "data/policies/returns_electronics.md": (552, 10),
    "data/policies/returns_fashion.md": (651, 13),
    "data/policies/shipping_delivery.md": (627, 15),
    "data/policies/warranty_phones_laptops.md": (781, 15),
    "db/init.sql": (4176, 87),
    "db/migrations/001_refund_audit.sql": (514, 13),
    "db/migrations/002_product_line.sql": (275, 3),
    "db/migrations/003_doc_params.sql": (510, 9),
    "db/migrations/004_handoff_tickets.sql": (596, 11),
    "docker-compose.yml": (622, 19),
    "evals/__init__.py": (0, 0),
    "evals/eval_answers.py": (4750, 106),
    "evals/eval_e2e.py": (9910, 199),
    "evals/eval_retrieval.py": (2715, 79),
    "evals/eval_router.py": (2445, 62),
    "frontend_tests/README.md": (432, 9),
    "frontend_tests/frontend_test.js": (10697, 149),
    "requirements.txt": (142, 9),
    "scripts/__init__.py": (0, 0),
    "scripts/apply_sql.py": (484, 15),
    "scripts/bench_handle.py": (4486, 94),
    "scripts/check_filter.py": (1515, 33),
    "scripts/debug_policy.py": (1436, 34),
    "scripts/latency_test.py": (1265, 36),
    "scripts/make_eval_data.py": (8481, 68),
    "scripts/model_compare.py": (1498, 37),
    "scripts/network_test.py": (1235, 37),
    "scripts/run_all_tests.py": (2053, 45),
    "scripts/seed_db.py": (5304, 99),
    "scripts/summarize_log.py": (1834, 40),
    "tests/__init__.py": (0, 0),
    "tests/test_api.py": (11309, 197),
    "tests/test_eval_checks.py": (6696, 78),
    "tests/test_guardrails.py": (6995, 102),
    "tests/test_handle.py": (12531, 194),
    "tests/test_handle_guards.py": (6760, 147),
    "tests/test_llm.py": (6373, 166),
    "tests/test_order_flow.py": (5335, 87),
    "tests/test_orders.py": (2722, 49),
    "tests/test_params.py": (6589, 139),
    "tests/test_product_filter.py": (4433, 78),
    "tests/test_rag.py": (5874, 101),
    "tests/test_ratelimit.py": (2418, 72),
    "tests/test_refund_flow.py": (6282, 117),
    "tests/test_refunds.py": (6057, 124),
    "tests/test_return_flow.py": (4218, 72),
    "tests/test_returns.py": (3540, 67),
    "tests/test_router.py": (8386, 137),
    "tests/test_session.py": (2743, 41),
}

if __name__ == "__main__":
    bad = 0
    for path, (size, nlines) in EXPECTED.items():
        p = Path(path)
        if not p.exists():
            print(f"MISSING  {path}")
            bad += 1
            continue
        actual = p.stat().st_size
        if not (size - 2 <= actual <= size + nlines + 2):
            print(f"DIFFERS  {path}: {actual} bytes, expected about {size}-{size + nlines}")
            bad += 1
    print(f"\n{len(EXPECTED) - bad} of {len(EXPECTED)} files look right" + ("" if not bad else f"; {bad} need attention"))
    sys.exit(1 if bad else 0)
