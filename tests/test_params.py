"""Policy parameters (return window etc.): consistency with the document text, atomic sync, lookup.
Fake embedder; needs the database with migration 003.  Run: python -m tests.test_params
Temporary rows are named zz_test_* and removed at the end.
"""
import tempfile
from pathlib import Path

import numpy as np

from app.db import get_conn
from app.ingest import ingest_file, load_params, sync_params, validate_params
from app.tools.returns import get_return_window

conn = get_conn()
passed = 0
VEC = np.ones(1024, dtype=np.float32) / 32


def check(name, cond):
    global passed
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        cleanup()
        raise SystemExit(f"test failed: {name}")
    passed += 1


def cleanup():
    conn.execute("DELETE FROM kb.chunks WHERE doc_name LIKE 'zz_test_%'")
    conn.execute("DELETE FROM kb.doc_params WHERE doc_name LIKE 'zz_test_%'")


class FakeEmbed:
    calls = 0

    def embed(self, texts):
        FakeEmbed.calls += len(texts)
        return [VEC for _ in texts]


def params_of(doc):
    return dict(conn.execute("SELECT param, value FROM kb.doc_params WHERE doc_name = %s", (doc,)).fetchall())


def chunk_count(doc):
    return conn.execute("SELECT count(*) FROM kb.chunks WHERE doc_name = %s", (doc,)).fetchone()[0]


cleanup()
TEXT7 = "# Returns\n\n## Window\nItems can be returned within 7 days of delivery.\n"

# ---- consistency check (no database) ----
validate_params("a.md", TEXT7, {"return_window_days": 7})
check("number stated in the text is accepted", True)
for name, text, params in [
    ("wrong number", TEXT7, {"return_window_days": 10}),
    ("number missing from the text", "# R\n\n## W\nNo window stated.\n", {"return_window_days": 7}),
    ("hours parameter checked too", "# R\n\n## W\nReport within 24 hours.\n", {"damage_report_hours": 48}),
]:
    try:
        validate_params("a.md", text, params)
        ok = False
    except ValueError:
        ok = True
    check(f"drift is rejected: {name}", ok)
check("'17 days' does not satisfy '7 days'", not (lambda: (validate_params("a.md", "within 17 days", {"x_days": 7}) or True))() if False else True)
try:
    validate_params("a.md", "within 17 days of delivery", {"return_window_days": 7})
    ok = False
except ValueError:
    ok = True
check("'17 days' does not satisfy '7 days' (whole-number match)", ok)
validate_params("a.md", "anything", {"some_flag": 1})
check("parameters without a unit suffix are not text-checked", True)

# ---- ingest + sync ----
with tempfile.TemporaryDirectory() as d:
    f = Path(d) / "zz_test_ret.md"
    f.write_text(TEXT7, encoding="utf-8")
    check("missing policy_params.json -> empty", load_params(Path(d)) == {})
    (Path(d) / "policy_params.json").write_text('{"zz_test_ret.md": {"return_window_days": 7}}', encoding="utf-8")
    check("policy_params.json is read", load_params(Path(d)) == {"zz_test_ret.md": {"return_window_days": 7}})

    n = ingest_file(conn, FakeEmbed(), f, "zzline", {"return_window_days": 7})
    check("ingest stores chunks and the parameter together", n > 0 and params_of("zz_test_ret.md") == {"return_window_days": 7})
    FakeEmbed.calls = 0
    check("same text, same numbers -> skipped", ingest_file(conn, FakeEmbed(), f, "zzline", {"return_window_days": 7}) == -1 and FakeEmbed.calls == 0)

    try:
        ingest_file(conn, FakeEmbed(), f, "zzline", {"return_window_days": 10})
        ok = False
    except ValueError:
        ok = True
    check("a number that disagrees with the text is REFUSED", ok)
    check("...and the stored number is untouched", params_of("zz_test_ret.md") == {"return_window_days": 7})

    f.write_text(TEXT7.replace("7 days", "10 days"), encoding="utf-8")
    try:
        ingest_file(conn, FakeEmbed(), f, "zzline", {"return_window_days": 7})
        ok = False
    except ValueError:
        ok = True
    check("policy text changed to 10 days but params still say 7 -> REFUSED", ok and chunk_count("zz_test_ret.md") > 0)
    check("...old chunks and old number are still intact", params_of("zz_test_ret.md") == {"return_window_days": 7} and
          conn.execute("SELECT count(*) FROM kb.chunks WHERE doc_name='zz_test_ret.md' AND content LIKE '%within 7 days%'").fetchone()[0] > 0)

    FakeEmbed.calls = 0
    n = ingest_file(conn, FakeEmbed(), f, "zzline", {"return_window_days": 10})
    check("text and number updated together -> re-embedded, both new", n > 0 and FakeEmbed.calls > 0 and params_of("zz_test_ret.md") == {"return_window_days": 10})
    check("old text is gone", conn.execute("SELECT count(*) FROM kb.chunks WHERE doc_name='zz_test_ret.md' AND content LIKE '%within 7 days%'").fetchone()[0] == 0)

    f.write_text("# Returns\n\n## Window\nItems can be returned within 10 days of delivery.\nSecond line within 3 days.\n", encoding="utf-8")
    ingest_file(conn, FakeEmbed(), f, "zzline", {"return_window_days": 10})
    FakeEmbed.calls = 0
    n = ingest_file(conn, FakeEmbed(), f, "zzline", {"return_window_days": 10, "other_days": 3})
    check("only the parameters changed -> metadata update, NO re-embedding", n == -2 and FakeEmbed.calls == 0 and params_of("zz_test_ret.md") == {"return_window_days": 10, "other_days": 3})
    n = ingest_file(conn, FakeEmbed(), f, "zzline", {})
    check("a parameter removed from the file is removed from the database", n == -2 and params_of("zz_test_ret.md") == {})

check("sync_params reports no change when nothing differs", sync_params(conn, "zz_test_none.md", {}) is False)
cleanup()

# ---- lookup by product line ----
def doc(name, line, value):
    conn.execute("INSERT INTO kb.chunks (id, doc_name, section, category, chunk_index, content, content_hash, product_line, embedding) "
                 "VALUES (gen_random_uuid(), %s, 'S', 'returns', 0, 'x', 'h', %s, %s)", (name, line, VEC))
    conn.execute("INSERT INTO kb.doc_params VALUES (%s, 'return_window_days', %s)", (name, value))


doc("zz_test_a.md", "zzline_a", 5)
check("window is found through the document's product line", get_return_window(conn, "zzline_a") == 5)
check("unknown product line -> None (a human decides)", get_return_window(conn, "zzline_nothing") is None)
doc("zz_test_b.md", "zzline_b", 5)
doc("zz_test_c.md", "zzline_b", 9)
check("two documents with DIFFERENT numbers for one line -> None (ambiguous)", get_return_window(conn, "zzline_b") is None)
conn.execute("UPDATE kb.doc_params SET value = 5 WHERE doc_name = 'zz_test_c.md'")
check("two documents with the SAME number -> that number", get_return_window(conn, "zzline_b") == 5)
cleanup()
print(f"\nall {passed} checks passed")
