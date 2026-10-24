"""Show WHY a policy question was (not) answered: which chunks the LLM received, what it said, and our decision.
Usage (from project root):
    python -m scripts.debug_policy --product electronics "query one" "query two"
Output is a few compact lines per query, so it is easy to copy.
"""
import sys

from app import llm, rag


def inspect(question, retriever, generate, product_line=None):
    chunks = retriever.retrieve(question, product_line=product_line)
    labels = [f"{i}:{c.doc_name.replace('.md', '')}>{c.section}" for i, c in enumerate(chunks, 1)]
    raw = generate(rag.build_prompt(question, chunks, product_line), system=rag.SYSTEM_PROMPT, temperature=0.0)
    decision = rag.answer(question, chunks, generate=lambda *a, **k: raw, product_line=product_line)
    return labels, raw.text, decision


if __name__ == "__main__":
    from app.retrieval import Retriever
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    product = None
    if "--product" in args:
        i = args.index("--product")
        product = args[i + 1]
        del args[i:i + 2]
    retriever = Retriever()
    for q in args:
        labels, raw, d = inspect(q, retriever, llm.generate, product)
        print(f"\nQUESTION: {q}")
        print(f"CHUNKS SENT ({len(labels)}): " + " | ".join(labels))
        print(f"LLM RAW: {raw[:400]}")
        print(f"DECISION: {d.status}/{d.reason} handoff={d.handoff} sources={d.sources}")
