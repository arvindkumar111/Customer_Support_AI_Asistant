"""Ingest policy documents into PostgreSQL + pgvector.

Usage:
    python -m app.ingest data/policies
Re-running on a changed file REPLACES its old chunks atomically (re-indexing, not retraining).
Unchanged files are skipped (saves embedding cost).
"""
import hashlib
import json
import re
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


def load_params(folder: Path) -> dict:
    """data/policies/policy_params.json: file name -> {param: number}. Numbers that code needs AND the document states."""
    f = folder / "policy_params.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def validate_params(name: str, text: str, params: dict) -> None:
    """A parameter must be stated in the document itself (e.g. return_window_days = 7 needs '7 days' in the text).
    If someone edits the policy but not policy_params.json (or the reverse), ingest fails loudly instead of drifting."""
    for key, value in params.items():
        unit = "days" if key.endswith("_days") else "hours" if key.endswith("_hours") else None
        if unit and not re.search(rf"\b{int(value)}\s+{unit}\b", text):
            raise ValueError(f"{name}: policy_params.json says {key} = {value}, but the document never says "
                             f"'{value} {unit}'. Update the document and policy_params.json together.")


def sync_params(conn, doc_name: str, params: dict) -> bool:
    """Make kb.doc_params match `params` for this document. Returns True if anything changed."""
    desired = {k: int(v) for k, v in params.items()}
    current = dict(conn.execute("SELECT param, value FROM kb.doc_params WHERE doc_name = %s", (doc_name,)).fetchall())
    if current == desired:
        return False
    with conn.transaction():
        conn.execute("DELETE FROM kb.doc_params WHERE doc_name = %s", (doc_name,))
        for k, v in desired.items():
            conn.execute("INSERT INTO kb.doc_params (doc_name, param, value) VALUES (%s, %s, %s)", (doc_name, k, v))
    return True


def ingest_file(conn, dense_model, path: Path, product_line: str = "general", params: dict | None = None) -> int:
    """Returns number of chunks written, -1 if unchanged, -2 if only metadata (product line / parameters) was updated.
    Raises ValueError if policy_params.json disagrees with the document text."""
    params = params or {}
    text = load_text(path)
    validate_params(path.name, text, params)
    content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

    row = conn.execute("SELECT content_hash, product_line FROM kb.chunks WHERE doc_name = %s LIMIT 1",
                       (path.name,)).fetchone()
    if row and row[0] == content_hash:
        changed = False
        if row[1] != product_line:
            conn.execute("UPDATE kb.chunks SET product_line = %s WHERE doc_name = %s", (product_line, path.name))
            changed = True
        if sync_params(conn, path.name, params):
            changed = True
        return -2 if changed else -1      # text unchanged: no need to pay for embeddings again

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
        sync_params(conn, path.name, params)          # same transaction: text and numbers always change together
    return len(rows)


def main(folder: str) -> None:
    conn = get_conn()
    dense_model = TextEmbedding(config.DENSE_MODEL)
    lines = load_product_lines(Path(folder))
    all_params = load_params(Path(folder))
    failed = False
    for path in sorted(Path(folder).glob("*")):
        if path.suffix.lower() in {".md", ".txt", ".pdf"}:
            pl, params = lines.get(path.name, "general"), all_params.get(path.name, {})
            try:
                n = ingest_file(conn, dense_model, path, pl, params)
            except ValueError as e:
                print(f"{path.name}: ERROR {e}")
                failed = True
                continue
            msg = {-1: "unchanged, skipped", -2: f"text unchanged, metadata updated (product line {pl}, params {params})"}.get(
                n, f"{n} chunks indexed ({pl})")
            print(f"{path.name}: {msg}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/policies")
