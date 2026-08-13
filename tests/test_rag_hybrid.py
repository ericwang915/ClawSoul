"""
Tests for the hybrid RAG pipeline:
  - Chunker
  - BM25Retriever (sparse)
  - EmbeddingRetriever (dense / bigram fallback)
  - Reciprocal Rank Fusion
  - LLMReranker
  - HybridRetriever (end-to-end)
  - KnowledgeRAG
  - MemoryManager.recall (smart retrieval)
"""

from unittest.mock import MagicMock


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_corpus(texts: list[str], source: str = "test.txt") -> list[dict]:
    return [{"source": source, "content": t, "_idx": i} for i, t in enumerate(texts)]


def make_provider(response_content: str = "[0, 1, 2]"):
    """Fake LLMProvider whose chat() returns a fixed JSON array (for reranker)."""
    msg = MagicMock()
    msg.content = response_content
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    provider = MagicMock()
    provider.chat.return_value = MagicMock(choices=[choice])
    return provider


SAMPLE_TEXTS = [
    "The capital of France is Paris. Paris is known for the Eiffel Tower.",
    "Python is a high-level programming language used for data science.",
    "The Eiffel Tower was built in 1889 for the World's Fair.",
    "Machine learning models require large amounts of training data.",
    "London is the capital of England and home to Buckingham Palace.",
]


# ── Chunker ──────────────────────────────────────────────────────────────────

class TestChunker:
    def test_basic_paragraph_split(self):
        from herandhim.core.retrieval.chunker import chunk_text
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_text(text, source="test.txt")
        assert len(chunks) == 3
        assert chunks[0]["content"] == "First paragraph."
        assert chunks[0]["source"] == "test.txt"

    def test_long_paragraph_sliding_window(self):
        from herandhim.core.retrieval.chunker import chunk_text
        long_text = "A" * 1200  # one big paragraph
        chunks = chunk_text(long_text, source="long.txt", chunk_size=400, overlap=80)
        # Should produce multiple chunks
        assert len(chunks) > 1
        # Each chunk should be <= chunk_size
        for c in chunks:
            assert len(c["content"]) <= 400

    def test_empty_text(self):
        from herandhim.core.retrieval.chunker import chunk_text
        assert chunk_text("", source="empty.txt") == []

    def test_load_corpus_from_directory(self, tmp_path):
        from herandhim.core.retrieval.chunker import load_corpus_from_directory
        (tmp_path / "doc1.txt").write_text("Hello world.\n\nSecond para.")
        (tmp_path / "doc2.md").write_text("Markdown content.")
        (tmp_path / "ignore.py").write_text("# ignored")
        corpus = load_corpus_from_directory(str(tmp_path))
        sources = {c["source"] for c in corpus}
        assert "doc1.txt" in sources
        assert "doc2.md" in sources
        assert "ignore.py" not in sources
        assert len(corpus) >= 3  # 2 from doc1 + 1 from doc2

    def test_chunk_idx_monotone(self):
        from herandhim.core.retrieval.chunker import chunk_text
        text = "Para one.\n\nPara two.\n\nPara three."
        chunks = chunk_text(text)
        indices = [c["chunk_idx"] for c in chunks]
        assert indices == sorted(indices)


# ── BM25Retriever (Sparse) ───────────────────────────────────────────────────

class TestBM25Retriever:
    def test_retrieve_returns_relevant(self):
        from herandhim.core.retrieval.sparse import BM25Retriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = BM25Retriever()
        r.fit(corpus)
        results = r.retrieve("Eiffel Tower Paris", top_k=3)
        contents = [c["content"] for _, c in results]
        assert any("Paris" in c or "Eiffel" in c for c in contents)

    def test_empty_corpus_returns_empty(self):
        from herandhim.core.retrieval.sparse import BM25Retriever
        r = BM25Retriever()
        r.fit([])
        assert r.retrieve("anything", top_k=5) == []

    def test_top_k_respected(self):
        from herandhim.core.retrieval.sparse import BM25Retriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = BM25Retriever()
        r.fit(corpus)
        results = r.retrieve("language data programming", top_k=2)
        assert len(results) <= 2

    def test_scores_descending(self):
        from herandhim.core.retrieval.sparse import BM25Retriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = BM25Retriever()
        r.fit(corpus)
        results = r.retrieve("Paris capital France", top_k=5)
        scores = [s for s, _ in results]
        assert scores == sorted(scores, reverse=True)

    def test_no_match_returns_empty(self):
        from herandhim.core.retrieval.sparse import BM25Retriever
        corpus = make_corpus(["hello world", "foo bar"])
        r = BM25Retriever()
        r.fit(corpus)
        results = r.retrieve("zxqwerty", top_k=3)
        assert results == []


# ── EmbeddingRetriever (Dense) ───────────────────────────────────────────────

class TestEmbeddingRetriever:
    def test_retrieve_returns_results(self):
        from herandhim.core.retrieval.dense import EmbeddingRetriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = EmbeddingRetriever()
        r.fit(corpus)
        results = r.retrieve("programming language", top_k=3)
        assert isinstance(results, list)
        # at least one result should mention programming or Python
        assert any("Python" in c["content"] or "programming" in c["content"]
                   for _, c in results)

    def test_empty_corpus_returns_empty(self):
        from herandhim.core.retrieval.dense import EmbeddingRetriever
        r = EmbeddingRetriever()
        r.fit([])
        assert r.retrieve("anything", top_k=3) == []

    def test_top_k_respected(self):
        from herandhim.core.retrieval.dense import EmbeddingRetriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = EmbeddingRetriever()
        r.fit(corpus)
        results = r.retrieve("capital city", top_k=2)
        assert len(results) <= 2

    def test_backend_name_set(self):
        from herandhim.core.retrieval.dense import EmbeddingRetriever
        r = EmbeddingRetriever()
        assert hasattr(r, "backend_name")
        assert r.backend_name  # non-empty string


# ── Reciprocal Rank Fusion ───────────────────────────────────────────────────

class TestRRF:
    def test_combines_two_lists(self):
        from herandhim.core.retrieval.fusion import reciprocal_rank_fusion
        a = {"_idx": 0, "content": "Paris"}
        b = {"_idx": 1, "content": "London"}
        c = {"_idx": 2, "content": "Berlin"}
        list1 = [(1.0, a), (0.5, b)]
        list2 = [(1.0, a), (0.8, c)]
        fused = reciprocal_rank_fusion([list1, list2])
        # 'a' appears in both lists at rank 0 → should be highest
        assert fused[0][1]["_idx"] == 0

    def test_single_list_passthrough(self):
        from herandhim.core.retrieval.fusion import reciprocal_rank_fusion
        a = {"_idx": 0, "content": "x"}
        b = {"_idx": 1, "content": "y"}
        fused = reciprocal_rank_fusion([[(1.0, a), (0.5, b)]])
        assert fused[0][1]["_idx"] == 0

    def test_empty_lists(self):
        from herandhim.core.retrieval.fusion import reciprocal_rank_fusion
        assert reciprocal_rank_fusion([]) == []
        assert reciprocal_rank_fusion([[]]) == []

    def test_scores_descending(self):
        from herandhim.core.retrieval.fusion import reciprocal_rank_fusion
        chunks = [{"_idx": i, "content": f"chunk {i}"} for i in range(5)]
        list1 = [(1.0 - i * 0.1, c) for i, c in enumerate(chunks)]
        list2 = list(reversed(list1))
        fused = reciprocal_rank_fusion([list1, list2])
        scores = [s for s, _ in fused]
        assert scores == sorted(scores, reverse=True)


# ── LLMReranker ──────────────────────────────────────────────────────────────

class TestLLMReranker:
    def test_reranks_by_llm_response(self):
        from herandhim.core.retrieval.reranker import LLMReranker
        corpus = make_corpus(SAMPLE_TEXTS)
        provider = make_provider("[2, 0, 1]")  # LLM says idx 2 is most relevant
        reranker = LLMReranker(provider)
        result = reranker.rerank("Eiffel Tower", corpus[:3], top_k=2)
        # Index 2 should be first
        assert result[0]["content"] == SAMPLE_TEXTS[2]
        assert len(result) == 2

    def test_fallback_on_bad_llm_response(self):
        from herandhim.core.retrieval.reranker import LLMReranker
        corpus = make_corpus(SAMPLE_TEXTS[:3])
        provider = make_provider("sorry I cannot help with that")
        reranker = LLMReranker(provider)
        # Should fall back to original order, not raise
        result = reranker.rerank("anything", corpus, top_k=2)
        assert len(result) == 2

    def test_empty_candidates(self):
        from herandhim.core.retrieval.reranker import LLMReranker
        reranker = LLMReranker(make_provider())
        assert reranker.rerank("query", [], top_k=3) == []

    def test_single_candidate(self):
        from herandhim.core.retrieval.reranker import LLMReranker
        corpus = make_corpus(["only one"])
        reranker = LLMReranker(make_provider("[0]"))
        result = reranker.rerank("query", corpus, top_k=1)
        assert len(result) == 1


# ── HybridRetriever (end-to-end) ─────────────────────────────────────────────

class TestHybridRetriever:
    def test_sparse_only(self):
        from herandhim.core.retrieval.retriever import HybridRetriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = HybridRetriever(use_sparse=True, use_dense=False, use_reranker=False)
        r.fit(corpus)
        hits = r.retrieve("Eiffel Tower", top_k=3)
        assert len(hits) <= 3
        assert any("Eiffel" in h["content"] for h in hits)

    def test_dense_only(self):
        from herandhim.core.retrieval.retriever import HybridRetriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = HybridRetriever(use_sparse=False, use_dense=True, use_reranker=False)
        r.fit(corpus)
        hits = r.retrieve("programming Python", top_k=3)
        assert len(hits) >= 1

    def test_hybrid_sparse_and_dense(self):
        from herandhim.core.retrieval.retriever import HybridRetriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = HybridRetriever(use_sparse=True, use_dense=True, use_reranker=False)
        r.fit(corpus)
        hits = r.retrieve("Paris capital France Eiffel", top_k=3)
        assert len(hits) >= 1
        assert any("Paris" in h["content"] or "Eiffel" in h["content"] for h in hits)

    def test_with_reranker(self):
        from herandhim.core.retrieval.retriever import HybridRetriever
        provider = make_provider("[1, 0, 2]")  # LLM reorders
        corpus = make_corpus(SAMPLE_TEXTS)
        r = HybridRetriever(
            provider=provider, use_sparse=True, use_dense=False, use_reranker=True
        )
        r.fit(corpus)
        hits = r.retrieve("Eiffel Tower Paris", top_k=2)
        assert len(hits) <= 2

    def test_no_idx_in_results(self):
        from herandhim.core.retrieval.retriever import HybridRetriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = HybridRetriever(use_sparse=True, use_dense=False, use_reranker=False)
        r.fit(corpus)
        hits = r.retrieve("London", top_k=2)
        for h in hits:
            assert "_idx" not in h, "Internal _idx should be stripped from results"

    def test_empty_corpus(self):
        from herandhim.core.retrieval.retriever import HybridRetriever
        r = HybridRetriever(use_sparse=True, use_dense=False, use_reranker=False)
        r.fit([])
        assert r.retrieve("anything", top_k=3) == []

    def test_empty_query(self):
        from herandhim.core.retrieval.retriever import HybridRetriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = HybridRetriever(use_sparse=True, use_dense=False, use_reranker=False)
        r.fit(corpus)
        assert r.retrieve("", top_k=3) == []

    def test_top_k_respected(self):
        from herandhim.core.retrieval.retriever import HybridRetriever
        corpus = make_corpus(SAMPLE_TEXTS)
        r = HybridRetriever(use_sparse=True, use_dense=True, use_reranker=False)
        r.fit(corpus)
        hits = r.retrieve("capital city London Paris", top_k=1)
        assert len(hits) == 1


# ── KnowledgeRAG ─────────────────────────────────────────────────────────────

class TestKnowledgeRAG:
    def test_load_and_retrieve(self, tmp_path):
        from herandhim.core.knowledge.rag import KnowledgeRAG
        (tmp_path / "animals.txt").write_text(
            "Dogs are loyal pets.\n\nCats are independent animals.\n\nBirds can fly."
        )
        rag = KnowledgeRAG(str(tmp_path), provider=None, use_reranker=False)
        hits = rag.retrieve("Birds fly", top_k=2)
        assert any("Birds" in h["content"] or "fly" in h["content"] for h in hits)

    def test_empty_dir_returns_empty(self, tmp_path):
        from herandhim.core.knowledge.rag import KnowledgeRAG
        rag = KnowledgeRAG(str(tmp_path), provider=None, use_reranker=False)
        assert rag.retrieve("anything") == []

    def test_reload(self, tmp_path):
        from herandhim.core.knowledge.rag import KnowledgeRAG
        (tmp_path / "doc.txt").write_text("Initial content about trees.")
        rag = KnowledgeRAG(str(tmp_path), provider=None, use_reranker=False)
        initial_len = len(rag)
        (tmp_path / "doc2.txt").write_text("New content about rivers and lakes.")
        rag.reload()
        assert len(rag) > initial_len

    def test_simplerag_alias(self, tmp_path):
        from herandhim.core.knowledge.rag import SimpleRAG
        assert SimpleRAG is not None  # backwards compat alias exists

    def test_result_has_source_and_content(self, tmp_path):
        from herandhim.core.knowledge.rag import KnowledgeRAG
        (tmp_path / "info.txt").write_text("The sky is blue.\n\nWater is wet.")
        rag = KnowledgeRAG(str(tmp_path), provider=None, use_reranker=False)
        hits = rag.retrieve("sky color", top_k=1)
        if hits:
            assert "source" in hits[0]
            assert "content" in hits[0]


# ── MemoryManager RAG recall ─────────────────────────────────────────────────

class TestMemoryManagerRAG:
    def _make_manager(self, tmp_path):
        from herandhim.core.memory.manager import MemoryManager
        mem_dir = str(tmp_path / "memory")
        mgr = MemoryManager(memory_dir=mem_dir, use_dense=False)
        return mgr

    def test_recall_returns_relevant_memory(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.remember("I love hiking in the mountains", key="hobby")
        mgr.remember("My favourite food is sushi", key="food")
        mgr.remember("I work as a software engineer", key="job")
        result = mgr.recall("What is my job?")
        assert "engineer" in result or "job" in result

    def test_recall_wildcard_returns_all(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.remember("value_a", key="key_a")
        mgr.remember("value_b", key="key_b")
        result = mgr.recall("*")
        assert "key_a" in result
        assert "key_b" in result

    def test_recall_empty_query_returns_all(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.remember("some fact", key="fact")
        result = mgr.recall("")
        assert "fact" in result

    def test_recall_no_memories(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        result = mgr.recall("anything")
        assert "No memories" in result

    def test_recall_miss_graceful_fallback(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.remember("my cat's name is Whiskers", key="cat")
        result = mgr.recall("xyzqwertyuiop")
        # Should fall back gracefully (no crash)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_list_all_helper(self, tmp_path):
        mgr = self._make_manager(tmp_path)
        mgr.remember("content", key="k")
        all_mem = mgr.list_all()
        assert "k" in all_mem
