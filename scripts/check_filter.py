"""Compact, copy-friendly check of the product-line filter with the REAL models.
Usage (from project root):  python -m scripts.check_filter
Prints ONE short line per case: PASS/FAIL, the question, and which documents came back.
"""
import sys

ELECTRONICS_DOCS = {"returns_electronics.md", "warranty_phones_laptops.md"}
FASHION_DOCS = {"returns_fashion.md"}

CASES = [  # (question, product_line, documents that must NOT appear, label)
    ("Mera laptop damaged aaya hai, kitne din mein return kar sakta hu?", "electronics", FASHION_DOCS, "laptop + electronics filter"),
    ("Kapde return karne ke liye kitne din milte hain?", "fashion", ELECTRONICS_DOCS, "clothes + fashion filter"),
    ("Mera laptop damaged aaya hai, kitne din mein return kar sakta hu?", None, set(), "laptop, NO filter (control)"),
]


def run(retriever, cases=CASES):
    results = []
    for q, pl, forbidden, label in cases:
        chunks = retriever.retrieve(q, product_line=pl)
        docs = [c.doc_name.replace(".md", "") for c in chunks]
        bad = sorted({c.doc_name for c in chunks} & forbidden)
        verdict = "FAIL" if bad else "PASS"
        results.append((verdict, label, len(chunks), docs, bad))
    return results


if __name__ == "__main__":
    from app.retrieval import Retriever
    sys.stdout.reconfigure(encoding="utf-8")
    for verdict, label, n, docs, bad in run(Retriever()):
        extra = f" | FORBIDDEN: {bad}" if bad else ""
        print(f"{verdict} | {label} | {n} chunks | {', '.join(docs)}{extra}")