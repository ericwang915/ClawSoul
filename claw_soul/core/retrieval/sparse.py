"""
Sparse retriever — BM25Okapi.

Falls back to a simple TF-weighted word-overlap scorer when `rank_bm25`
is not installed.  Install rank-bm25 for best quality:

    pip install rank-bm25
"""

from __future__ import annotations

import re

try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class BM25Retriever:
    """
    Wraps BM25Okapi (or a simple fallback) for sparse retrieval.

    Usage
    -----
        r = BM25Retriever()
        r.fit(corpus)                         # corpus = list of {"content": ..., ...}
        results = r.retrieve("my query", 10)  # -> [(score, chunk_dict), ...]
    """

    def __init__(self) -> None:
        self._corpus: list[dict] = []
        self._bm25: object | None = None
        self._tokenized: list[list[str]] = []

    def fit(self, corpus: list[dict]) -> None:
        self._corpus = corpus
        self._tokenized = [_tokenize(c["content"]) for c in corpus]
        if _HAS_BM25 and corpus:
            self._bm25 = BM25Okapi(self._tokenized)

    def retrieve(self, query: str, top_k: int) -> list[tuple[float, dict]]:
        if not self._corpus:
            return []

        tokens = _tokenize(query)

        if _HAS_BM25 and self._bm25:
            raw_scores = self._bm25.get_scores(tokens)
            pairs = [(float(s), c) for s, c in zip(raw_scores, self._corpus) if s > 0]
        else:
            # Fallback: term-frequency word-overlap
            pairs = []
            query_set = set(tokens)
            for chunk in self._corpus:
                chunk_tokens = _tokenize(chunk["content"])
                if not chunk_tokens:
                    continue
                tf = sum(1 for t in chunk_tokens if t in query_set)
                if tf > 0:
                    pairs.append((float(tf) / len(chunk_tokens), chunk))

        pairs.sort(key=lambda x: x[0], reverse=True)
        return pairs[:top_k]
