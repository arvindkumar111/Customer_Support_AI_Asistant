"""Product-line filter tests (database-level, fake embedder: tests the SQL and ingest logic, NOT search quality).
Needs the database with migration 002 applied.  Run: python -m tests.test_product_filter
Temporary rows are named zz_test_* and removed at the end.
"""
import tempfile
import uuid
from pathlib import Path

import numpy as np

from app.db import get_conn
from app.ingest import ingest_file, load_product_lines
from app.retrieval import Retriever, product_line_filter

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


class FakeEmbed:
    calls = 0

    def embed(self, texts):
        FakeEmbed.calls += len(texts)
        return [VEC for _ in texts]


cleanup()
for doc, pl in [("zz_test_elec.md", "electronics"), ("zz_test_fash.md", "fashion"), ("zz_test_gen.md", "general")]:
    conn.execute("INSERT INTO kb.chunks (id, doc_name, section, category, chunk_index, content, content_hash, product_line, embedding) "
                 "VALUES (%s, %s, 'S', 'returns', 0, 'return policy days exchange', 'h', %s, %s)", (uuid.uuid4(), doc, pl, VEC))

R = Retriever(conn, embedder=FakeEmbed())
found = lambda pl: {c.doc_name for c in R.search("return policy days", product_line=pl, limit=200) if c.doc_name.startswith("zz_test_")}

check("filter list always includes 'general'", product_line_filter("electronics") == ["electronics", "general"])
check("None / unknown / empty mean no filter", product_line_filter(None) is None and product_line_filter("unknown") is None and product_line_filter("") is None)
check("electronics question gets electronics + general, NOT fashion", found("electronics") == {"zz_test_elec.md", "zz_test_gen.md"})
check("fashion question gets fashion + general, NOT electronics", found("fashion") == {"zz_test_fash.md", "zz_test_gen.md"})
check("product with no documents gets only general", found("home") == {"zz_test_gen.md"})
check("no product line -> everything (no filter)", found(None) == {"zz_test_elec.md", "zz_test_fash.md", "zz_test_gen.md"})
check("'unknown' -> everything (no filter)", found("unknown") == {"zz_test_elec.md", "zz_test_fash.md", "zz_test_gen.md"})
check("dense-only mode filters the same way", {c.doc_name for c in R.search("x", mode="dense", product_line="fashion", limit=200) if c.doc_name.startswith("zz_test_")} == {"zz_test_fash.md", "zz_test_gen.md"})
check("injection string as product line matches nothing and does not crash", found("x'; DROP TABLE kb.chunks;--") == {"zz_test_gen.md"})
check("kb.chunks table still exists", conn.execute("SELECT count(*) FROM kb.chunks").fetchone()[0] > 0)

# ---- ingest: product line is stored, and changing it alone does not re-embed ----
with tempfile.TemporaryDirectory() as d:
    f = Path(d) / "zz_test_doc.md"
    f.write_text("# Title\n\n## Section\nSome policy text about returns within 7 days.\n", encoding="utf-8")
    (Path(d) / "product_lines.json").write_text('{"zz_test_doc.md": "electronics"}', encoding="utf-8")
    check("product_lines.json is read", load_product_lines(Path(d)) == {"zz_test_doc.md": "electronics"})
    check("missing product_lines.json -> empty mapping", load_product_lines(Path(d) / "nope") == {})
    FakeEmbed.calls = 0
    n = ingest_file(conn, FakeEmbed(), f, "electronics")
    check("ingest stores the product line", n > 0 and conn.execute("SELECT DISTINCT product_line FROM kb.chunks WHERE doc_name='zz_test_doc.md'").fetchall() == [("electronics",)])
    FakeEmbed.calls = 0
    check("same text, same line -> skipped", ingest_file(conn, FakeEmbed(), f, "electronics") == -1 and FakeEmbed.calls == 0)
    check("same text, new line -> metadata-only update, NO re-embedding", ingest_file(conn, FakeEmbed(), f, "fashion") == -2 and FakeEmbed.calls == 0)
    check("the new line is stored", conn.execute("SELECT DISTINCT product_line FROM kb.chunks WHERE doc_name='zz_test_doc.md'").fetchall() == [("fashion",)])
    f.write_text("# Title\n\n## Section\nChanged text: 10 days.\n", encoding="utf-8")
    check("changed text -> re-embedded and replaced", ingest_file(conn, FakeEmbed(), f, "fashion") > 0 and FakeEmbed.calls > 0)

cleanup()
print(f"\nall {passed} checks passed")