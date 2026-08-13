"""
Knowledge-base RAG wrapper.

Replaces the old SimpleRAG (keyword-only) with a HybridRetriever that
combines BM25 sparse retrieval, dense embedding retrieval, RRF fusion,
and an optional LLM re-ranker.

Backwards-compatible API:
    rag = KnowledgeRAG(knowledge_dir, provider=llm_provider)
    hits = rag.retrieve("my query", top_k=5)
    # hits = [{"source": "filename.txt", "content": "..."}, ...]

Old SimpleRAG is kept as an alias for scripts that import it directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..retrieval.chunker import load_corpus_from_directory
from ..retrieval.retriever import HybridRetriever

if TYPE_CHECKING:
    from ..llm.base import LLMProvider

logger = logging.getLogger(__name__)


class KnowledgeRAG:
    """
    Loads .txt / .md files from a directory and retrieves relevant chunks
    using hybrid sparse + dense retrieval with optional LLM re-ranking.

    Parameters
    ----------
    knowledge_dir : path to the directory containing knowledge files.
    provider      : LLMProvider (enables LLM re-ranker when provided).
    use_sparse    : enable BM25 retrieval (default True).
    use_dense     : enable embedding retrieval (default True).
    use_reranker  : enable LLM re-ranking (default True; requires provider).
    dense_model   : sentence-transformers model name.
    """

    def __init__(
        self,
        knowledge_dir: str,
        provider: "LLMProvider | None" = None,
        use_sparse: bool = True,
        use_dense: bool = True,
        use_reranker: bool = True,
        dense_model: str = "all-MiniLM-L6-v2",
    ) -> None:
        self.knowledge_dir = knowledge_dir

        self._retriever = HybridRetriever(
            provider=provider,
            use_sparse=use_sparse,
            use_dense=use_dense,
            use_reranker=use_reranker,
            dense_model=dense_model,
        )

        corpus = load_corpus_from_directory(knowledge_dir)
        self._retriever.fit(corpus)
        logger.info(
            "[KnowledgeRAG] Loaded %d chunks from '%s'", len(corpus), knowledge_dir
        )

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Return up to *top_k* relevant chunks for *query*.

        Each result dict has at least:
            {"source": str, "content": str}
        """
        return self._retriever.retrieve(query, top_k=top_k)

    def reload(self) -> None:
        """Re-scan the knowledge directory and re-index (hot reload)."""
        corpus = load_corpus_from_directory(self.knowledge_dir)
        self._retriever.fit(corpus)
        logger.info("[KnowledgeRAG] Reloaded %d chunks.", len(corpus))

    def __len__(self) -> int:
        return len(self._retriever)

    def __bool__(self) -> bool:
        return bool(self._retriever)


# Backwards-compatibility alias
SimpleRAG = KnowledgeRAG
