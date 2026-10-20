"""Structure-aware chunking: split on headings first, then by size with overlap."""
import re
from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    section: str
    index: int


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown/plain text into (heading, body) pairs."""
    parts = re.split(r"(?m)^(#{1,4}\s+.+)$", text)
    sections, heading = [], "Introduction"
    if parts[0].strip():
        sections.append((heading, parts[0].strip()))
    for i in range(1, len(parts), 2):
        heading = parts[i].lstrip("# ").strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if body:
            sections.append((heading, body))
    return sections


def chunk_text(text: str, max_words: int = 300, overlap: int = 40) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading, body in _split_sections(text):
        words = body.split()
        start = 0
        while start < len(words):
            piece = words[start : start + max_words]
            # Prepend the heading so "7 days" never loses its "electronics" context.
            chunk_body = f"{heading}\n{' '.join(piece)}"
            chunks.append(Chunk(text=chunk_body, section=heading, index=len(chunks)))
            if start + max_words >= len(words):
                break
            start += max_words - overlap
    return chunks