"""Background embedding generation for LibraryAgent rows.

LibraryAgent embeddings power the "similar agents in your library" check
that runs before CoPilot creates a new agent. Generation is fire-and-forget
so user-facing latency on create/update is unaffected; failures are logged
and swallowed because a missing embedding only degrades search quality, it
never breaks correctness.
"""

from __future__ import annotations

import asyncio
import logging

from prisma.enums import ContentType

from backend.api.features.store.embeddings import ensure_content_embedding
from backend.data import graph as graph_db

logger = logging.getLogger(__name__)


def _build_searchable_text(graph: graph_db.GraphModel) -> str:
    parts = [
        graph.name or "",
        graph.description or "",
        graph.instructions or "",
    ]
    return " ".join(part for part in parts if part).strip()


async def _run_embedding(
    library_agent_id: str, user_id: str, graph: graph_db.GraphModel
) -> None:
    try:
        searchable_text = _build_searchable_text(graph)
        if not searchable_text:
            logger.debug(
                "Skipping library agent embedding for %s: empty searchable text",
                library_agent_id,
            )
            return
        await ensure_content_embedding(
            content_type=ContentType.LIBRARY_AGENT,
            content_id=library_agent_id,
            searchable_text=searchable_text,
            metadata={"name": graph.name or ""},
            user_id=user_id,
            force=True,
        )
    except Exception as e:
        logger.warning(
            "Failed to ensure library agent embedding for %s: %s",
            library_agent_id,
            e,
        )


def schedule_library_agent_embedding(
    library_agent_id: str, user_id: str, graph: graph_db.GraphModel
) -> asyncio.Task[None]:
    """Schedule a background task that (re-)generates the embedding.

    Always passes ``force=True`` so updates (name/description/instructions
    changes via ``update_library_agent_version_and_settings``) refresh the
    embedding. The returned task is not awaited by callers; failures are
    logged inside ``_run_embedding``.
    """
    return asyncio.create_task(_run_embedding(library_agent_id, user_id, graph))
