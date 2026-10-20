"""Ingest policy documents into PostgreSQL + pgvector.

Usage:
    python -m app.ingest data/policies
Re-running on a changed file REPLACES its old chunks atomically (re-indexing, not retraining).
Unchanged files are skipped (saves embedding cost).
"""
import hashlib
import json
import sys
import uuid
from pathlib import Path

from fastembed import TextEmbedding
from pypdf import PdfReader

from app import config
from app.chunking import chunk_text
from app.db import get_conn


def load_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8")


def category_from_name(name: str) -> str:
    # File naming convention: <category>_<anything>.md  e.g. returns_electronics.md
    return name.split("_")[0].lower()


def load_product_lines(folder: Path) -> dict:
    """data/policies/product_lines.json maps file name -> electronics | fashion | home. Missing = 'general'."""
    f = folder / "product_lines.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def ingest_file(conn, dense_model, path: Path, product_line: str = "general") -> int:
    """Returns number of chunks written, -1 if unchanged, -2 if only the product line was updated."""
    text = load_text(path)
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

    row = conn.execute("SELECT content_hash, product_line FROM kb.chunks WHERE doc_name = %s LIMIT 1",
                       (path.name,)).fetchone()
    if row and row[0] == content_hash:
        if row[1] == product_line:
            return -1
        conn.execute("UPDATE kb.chunks SET product_line = %s WHERE doc_name = %s", (product_line, path.name))
        return -2      # text unchanged: no need to pay for embeddings again

    chunks = chunk_text(text, config.CHUNK_WORDS, config.OVERLAP_WORDS)
    if not chunks:
        return 0

    # "passage: " prefix is required by E5 models for documents.
    vectors = list(dense_model.embed([f"passage: {c.text}" for c in chunks]))
    rows = [
        (uuid.uuid5(uuid.NAMESPACE_URL, f"{path.name}:{c.index}"),  # deterministic id
         path.name, c.section, category_from_name(path.name), c.index, c.text, content_hash, product_line, v)
        for c, v in zip(chunks, vectors)
    ]

    # One transaction: readers see either the whole old version or the whole new one, never a mix or an empty gap.
    with conn.transaction():
        conn.execute("DELETE FROM kb.chunks WHERE doc_name = %s", (path.name,))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO kb.chunks
                   (id, doc_name, section, category, chunk_index, content, content_hash, product_line, embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                rows,
            )
    return len(rows)


def main(folder: str) -> None:
    conn = get_conn()
    dense_model = TextEmbedding(config.DENSE_MODEL)
    lines = load_product_lines(Path(folder))
    for path in sorted(Path(folder).glob("*")):
        if path.suffix.lower() in {".md", ".txt", ".pdf"}:
            pl = lines.get(path.name, "general")
            n = ingest_file(conn, dense_model, path, pl)
            msg = {-1: "unchanged, skipped", -2: f"text unchanged, product line set to {pl}"}.get(n, f"{n} chunks indexed ({pl})")
            print(f"{path.name}: {msg}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/policies")