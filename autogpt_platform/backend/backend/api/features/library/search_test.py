"""Tests for hybrid_search_library_agents."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.features.library.search import (
    LIBRARY_SIMILARITY_THRESHOLD,
    hybrid_search_library_agents,
)


def _patch_search(return_value):
    """Patch the db_accessors.search() shim to return a mock module/client
    whose ``unified_hybrid_search`` returns ``return_value``."""
    mock_shim = MagicMock()
    mock_shim.unified_hybrid_search = AsyncMock(return_value=return_value)
    return (
        patch(
            "backend.api.features.library.search.search",
            return_value=mock_shim,
        ),
        mock_shim,
    )


@pytest.mark.asyncio
async def test_returns_empty_list_for_empty_query():
    """Empty/whitespace query short-circuits without calling the DB."""
    patcher, mock_shim = _patch_search(([], 0))
    with patcher:
        result = await hybrid_search_library_agents(query="   ", user_id="u1")
    assert result == []
    mock_shim.unified_hybrid_search.assert_not_called()


@pytest.mark.asyncio
async def test_delegates_to_unified_hybrid_search_with_user_scope():
    """Delegates the heavy lifting and forwards user_id + threshold."""
    rows = [{"content_id": "lib-1", "combined_score": 0.82}]
    patcher, mock_shim = _patch_search((rows, 1))
    with patcher:
        result = await hybrid_search_library_agents(
            query="summarise my email", user_id="user-42", limit=3
        )

    assert result == rows
    mock_shim.unified_hybrid_search.assert_awaited_once()
    kwargs = mock_shim.unified_hybrid_search.call_args.kwargs
    assert kwargs["user_id"] == "user-42"
    assert kwargs["page_size"] == 3
    assert kwargs["min_score"] == LIBRARY_SIMILARITY_THRESHOLD
    assert len(kwargs["content_types"]) == 1


@pytest.mark.asyncio
async def test_threshold_can_be_overridden_per_call():
    rows: list[dict] = []
    patcher, mock_shim = _patch_search((rows, 0))
    with patcher:
        await hybrid_search_library_agents(
            query="x", user_id="u", limit=5, min_score=0.9
        )
    assert mock_shim.unified_hybrid_search.call_args.kwargs["min_score"] == 0.9
