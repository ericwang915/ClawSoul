"""
claw_soul.core — the core reasoning engine.

Public API
----------
  Agent               — main reasoning loop
  LLMProvider         — abstract base class for LLM backends
  MemoryManager       — long-term key-value memory
  KnowledgeRAG        — hybrid knowledge-base retrieval
  HybridRetriever     — low-level sparse + dense + reranker pipeline
"""

from .agent import Agent
from .knowledge.rag import KnowledgeRAG
from .llm.base import LLMProvider
from .memory.manager import MemoryManager
from .retrieval import HybridRetriever

__all__ = [
    "Agent",
    "LLMProvider",
    "MemoryManager",
    "KnowledgeRAG",
    "HybridRetriever",
]
