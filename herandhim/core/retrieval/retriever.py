"""
HybridRetriever — the main retrieval class.

Pipeline
--------
                corpus (list of chunk dicts)
                        |
            +-----------+-----------+
            |                       |
    BM25Retriever           EmbeddingRetriever
     (sparse)                 (dense)
            |                       |
            +-----------+-----------+
                     |
          Reciprocal Rank Fusion
                     |
                     | (top fetch_k candidates)
            LLMReranker  (optional)
                     |
                     | top_k final results

Usage
-----
    from herandhim.core.retrieval import HybridRetriever, load_corpus_from_directory

    retriever = HybridRetriever(provider=llm_provider)
    retriever.fit(load_corpus_from_directory("context/knowledge"))
    hits = retriever.retrieve("What is the refund policy?", top_k=5)
    # hits = [{"source": "...", "content": "...", ...}, ...]

Configuration
-------------
    use_sparse   : enable BM25 (default True)
    use_dense    : enable embedding retriever (default True)
    use_reranker : enable LLM re-ranking (default True, requires provider)
    dense_model  : sentence-transformers model name
    top_k        : number of results returned
    fetch_k      : candidates fetched before fusion/reranking (>= top_k)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .dense import EmbeddingRetriever
from .fusion import reciprocal_rank_fusion
from .reranker import LLMReranker
from .sparse import BM25Retriever

if TYPE_CHECKING:
    from ..llm.base import LLMProvider

logger = logging.getLogger(__name__)


class HybridRetriever:
    """
    Combines sparse (BM25) + dense (embedding) retrieval with RRF fusion and
    an optional LLM re-ranker.

    Parameters
    ----------
    provider     : LLMProvider instance (required for use_reranker=True).
    use_sparse   : include BM25 retrieval.
    use_dense    : include embedding retrieval.
    use_reranker : re-rank fused candidates with the LLM.
    dense_model  : sentence-transformers model name.
    """

    def __init__(
        self,
        provider: "LLMProvider | None" = None,
        use_sparse: bool = True,
        use_dense: bool = True,
        use_reranker: bool = True,
        dense_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self._provider = provider
        self.use_sparse = use_sparse
        self.use_dense = use_dense
        self.use_reranker = use_reranker and provider is not None

        self._sparse = BM25Retriever() if use_sparse else None
        self._dense = EmbeddingRetriever(dense_model) if use_dense else None
        self._reranker = LLMReranker(provider) if self.use_reranker else None
        self._corpus: list[dict] = []

        if use_dense and self._dense:
            logger.info("[HybridRetriever] Dense backend: %s", self._dense.backend_name)

    # ── Indexing ──────────────────────────────────────────────────────────────

    def fit(self, corpus: list[dict]) -> "HybridRetriever":
        """
        Index the corpus.  Each item must have a 'content' key.
        Mutates corpus in-place by adding '_idx' for RRF deduplication.
        """
        for i, chunk in enumerate(corpus):
            chunk["_idx"] = i
        self._corpus = corpus

        if self._sparse:
            self._sparse.fit(corpus)
        if self._dense:
            self._dense.fit(corpus)

        logger.info(
            "[HybridRetriever] Indexed %d chunks (sparse=%s dense=%s reranker=%s)",
            len(corpus), self.use_sparse, self.use_dense, self.use_reranker,
        )
        return self

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Retrieve the *top_k* most relevant chunks for *query*.

        Returns a list of chunk dicts (internal '_idx' field stripped).
        """
        if not self._corpus or not query.strip():
            return []

        # How many candidates to fetch before reranking
        fetch_k = max(top_k * 3, top_k + 5)

        ranked_lists: list[list[tuple[float, dict]]] = []

        if self._sparse:
            sparse_results = self._sparse.retrieve(query, top_k=fetch_k)
            if sparse_results:
                ranked_lists.append(sparse_results)

        if self._dense:
            dense_results = self._dense.retrieve(query, top_k=fetch_k)
            if dense_results:
                ranked_lists.append(dense_results)

        if not ranked_lists:
            return []

        # Fusion
        if len(ranked_lists) == 1:
            fused = [(s, c) for s, c in ranked_lists[0]]
        else:
            fused = reciprocal_rank_fusion(ranked_lists)

        candidates = [c for _, c in fused[: fetch_k if self._reranker else top_k]]

        # Re-rank
        if self._reranker and candidates:
            candidates = self._reranker.rerank(query, candidates, top_k)
        else:
            candidates = candidates[:top_k]

        # Strip internal index field before returning
        return [{k: v for k, v in c.items() if k != "_idx"} for c in candidates]

    # ── Convenience ──────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._corpus)

    def __bool__(self) -> bool:
        return bool(self._corpus)
