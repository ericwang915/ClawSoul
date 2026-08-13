"""
Text chunking utilities.

Strategy
--------
1. Split document by paragraphs (double newline).
2. Any paragraph longer than `max_chars` is further split with a sliding
   window of size `chunk_size` and overlap `overlap`.
3. Each chunk carries metadata: source filename, chunk index, character offset.

Supported file extensions: .txt  .md
"""

from __future__ import annotations

import os
import re


def chunk_text(
    text: str,
    source: str = "",
    chunk_size: int = 400,
    overlap: int = 80,
) -> list[dict]:
    """
    Split *text* into chunks suitable for retrieval.

    Returns a list of dicts:
        {"source": str, "content": str, "chunk_idx": int}
    """
    # Split by blank lines (paragraph boundaries)
    raw_paragraphs = re.split(r"\n{2,}", text)
    paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]

    chunks: list[dict] = []
    idx = 0

    for para in paragraphs:
        if len(para) <= chunk_size:
            chunks.append({"source": source, "content": para, "chunk_idx": idx})
            idx += 1
        else:
            # Sliding-window split for long paragraphs
            start = 0
            while start < len(para):
                end = min(start + chunk_size, len(para))
                window = para[start:end].strip()
                if window:
                    chunks.append({"source": source, "content": window, "chunk_idx": idx})
                    idx += 1
                if end == len(para):
                    break
                start += chunk_size - overlap

    return chunks


def load_corpus_from_directory(directory: str) -> list[dict]:
    """
    Load all .txt and .md files from *directory* and return a flat list of chunks.
    """
    corpus: list[dict] = []
    if not os.path.isdir(directory):
        return corpus

    for filename in sorted(os.listdir(directory)):
        if not filename.lower().endswith((".txt", ".md")):
            continue
        filepath = os.path.join(directory, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                text = f.read()
            corpus.extend(chunk_text(text, source=filename))
        except OSError as exc:
            print(f"[Chunker] Could not read '{filepath}': {exc}")

    return corpus
