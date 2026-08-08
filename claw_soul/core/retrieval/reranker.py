"""
LLM-based re-ranker.

Given a query and a list of candidate chunks (retrieved by sparse + dense),
asks the LLM to sort them by relevance and returns the top-k.

Prompt strategy
---------------
We ask the LLM to return a JSON array of 0-based indices sorted from most
to least relevant.  This is compact, deterministic to parse, and works well
with instruction-tuned models.

The re-ranker is *optional*.  It adds one extra LLM call per retrieval but
significantly improves precision, especially for ambiguous queries.

Usage
-----
    from claw_soul.core.retrieval.reranker import LLMReranker
    reranker = LLMReranker(provider)
    best = reranker.rerank(query="...", candidates=[...], top_k=3)
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm.base import LLMProvider

logger = logging.getLogger(__name__)

_RERANK_PROMPT = """\
You are a relevance scoring assistant. Given a search query and a list of text passages, rank the passages by their relevance to the query.

Query: {query}

Passages:
{passages}

Return ONLY a valid JSON array of passage indices (0-based), ordered from most relevant to least relevant.
Example: [2, 0, 3, 1]

Your response (JSON array only):"""


class LLMReranker:
    """
    Re-ranks retrieval candidates using a single LLM call.

    Parameters
    ----------
    provider   : any LLMProvider instance.
    max_chars  : truncate each candidate to this many characters in the prompt.
    """

    def __init__(self, provider: "LLMProvider", max_chars: int = 300) -> None:
        self._provider = provider
        self._max_chars = max_chars

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int,
    ) -> list[dict]:
        """
        Re-rank *candidates* for *query* and return the best *top_k*.

        Falls back to the original order if the LLM response cannot be parsed.
        """
        if not candidates:
            return []
        if len(candidates) == 1:
            return candidates[:top_k]

        passages_text = "\n\n".join(
            f"[{i}] {c['content'][: self._max_chars]}"
            for i, c in enumerate(candidates)
        )
        prompt = _RERANK_PROMPT.format(query=query, passages=passages_text)

        try:
            response = self._provider.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                tool_choice=None,
            )
            raw = response.choices[0].message.content.strip()

            # Extract first JSON array from the response
            match = re.search(r"\[[\d,\s]+\]", raw)
            if not match:
                raise ValueError(f"No JSON array found in: {raw!r}")
            indices: list[int] = json.loads(match.group())

            reranked = [
                candidates[i] for i in indices if 0 <= i < len(candidates)
            ]
            # Append any candidates the LLM missed (shouldn't happen, but be safe)
            seen = set(indices)
            for i, c in enumerate(candidates):
                if i not in seen:
                    reranked.append(c)

            return reranked[:top_k]

        except Exception as exc:
            logger.warning("[LLMReranker] Reranking failed (%s), using original order.", exc)
            return candidates[:top_k]
