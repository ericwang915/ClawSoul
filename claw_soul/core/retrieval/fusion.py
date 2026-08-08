"""
Reciprocal Rank Fusion (RRF).

RRF is a simple, parameter-free method for combining ranked lists from multiple
retrievers.  For each document, its RRF score is:

    rrf(d) = Σ  1 / (k + rank_i(d))
             i

where rank_i(d) is d's 1-based rank in list i and k=60 is the standard constant.

Documents that appear in multiple lists get a boost; documents missing from a
list contribute 0 for that list.

Reference: Cormack et al. (2009) "Reciprocal rank fusion outperforms condorcet
and individual rank learning methods."
"""

from __future__ import annotations

from collections import defaultdict


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[float, dict]]],
    k: int = 60,
) -> list[tuple[float, dict]]:
    """
    Fuse multiple ranked lists using RRF.

    Parameters
    ----------
    ranked_lists : each sub-list is [(score, chunk_dict), ...] sorted desc by score.
                   Chunks must have an '_idx' field set by HybridRetriever.fit().
    k            : smoothing constant (default 60, per original paper).

    Returns
    -------
    Fused list of (rrf_score, chunk_dict) sorted desc by rrf_score.
    """
    rrf_scores: dict[int, float] = defaultdict(float)
    chunk_by_idx: dict[int, dict] = {}

    for ranked in ranked_lists:
        for rank, (_, chunk) in enumerate(ranked):
            idx = chunk.get("_idx", id(chunk))
            rrf_scores[idx] += 1.0 / (k + rank + 1)
            chunk_by_idx[idx] = chunk

    fused = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return [(score, chunk_by_idx[idx]) for idx, score in fused]
