"""Hybrid semantic + lexical search over a user's library agents.

Used by the CoPilot ``find_library_agent`` tool's ``for_creation`` mode and
by the ``create_agent`` similarity gate. Library-agent embeddings live in
the ``UnifiedContentEmbedding`` table scoped by ``userId``; we delegate the
actual ranking to ``unified_hybrid_search`` (via the ``db_accessors.search``
shim) so we inherit its graceful degradation (lexical-only fallback when
the embedding API is unavailable) and its BM25 reranking — and so the
call works whether Prisma is connected in-process or only available via
the database-manager RPC service.
"""

from __future__ import annotations

import logging
from typing import Any

from prisma.enums import ContentType

from backend.api.features.store.hybrid_search import UnifiedSearchWeights
from backend.data.db_accessors import search

logger = logging.getLogger(__name__)

# Minimum combined relevance score for a library agent to be considered
# "functionally similar" enough to recommend before creating a new one.
# Calibrated against ``_LIBRARY_SEARCH_WEIGHTS``: with semantic-biased
# weights, a true near-duplicate scores ~0.75 (semantic ≈0.85 × 0.85
# weight) and an unrelated agent lands well below 0.5.
LIBRARY_SIMILARITY_THRESHOLD = 0.55

# Library-agent search weights differ from the default unified-search mix
# (which assumes content has categories and a populated tsvector). Library
# agents in this deployment have:
#   * no categories on LibraryAgent metadata → ``category`` carries no signal
#   * the tsvector ``search`` column is unreliable for LIBRARY_AGENT rows
#     in dev environments (trigger-populated; not all stacks fire it)
# so we let semantic carry the bulk of the score. Recency stays small but
# non-zero so two equally-similar agents tie-break toward the most recent.
_LIBRARY_SEARCH_WEIGHTS = UnifiedSearchWeights(
    semantic=0.85,
    lexical=0.10,
    category=0.0,
    recency=0.05,
)


async def hybrid_search_library_agents(
    query: str,
    user_id: str,
    limit: int = 5,
    min_score: float = LIBRARY_SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Search the user's library agents by hybrid relevance.

    Args:
        query: The user's goal text (free-form).
        user_id: Owner of the library agents to search.
        limit: Maximum number of matches to return.
        min_score: Minimum combined relevance to keep a match.

    Returns:
        A list of result dicts ordered by relevance. Each row contains at
        least ``content_id`` (the LibraryAgent id) and ``combined_score`` /
        ``relevance``. Returns ``[]`` when the query is empty.
    """
    query = (query or "").strip()
    if not query:
        return []

    results, _total = await search().unified_hybrid_search(
        query=query,
        content_types=[ContentType.LIBRARY_AGENT],
        page=1,
        page_size=max(1, limit),
        min_score=min_score,
        user_id=user_id,
        weights=_LIBRARY_SEARCH_WEIGHTS,
    )
    return results
