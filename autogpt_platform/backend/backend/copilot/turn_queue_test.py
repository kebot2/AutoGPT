"""Unit tests for turn_queue: per-user FIFO queue layered over ChatMessage.

Pure logic tests (status transitions, payload encoding, dispatch
re-validation branches). DB and Redis interactions are mocked at
module boundaries — see ``backend/copilot/turn_queue_integration_test.py``
(if added later) for coverage with a live Postgres / Redis fixture.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.copilot import turn_queue


class _NoopAsyncCM:
    """Stand-in for the Redis NX session lock context manager. The lock
    only matters in production for cross-replica serialisation; in unit
    tests there's no concurrent submitter so we just no-op."""

    async def __aenter__(self):
        return True

    async def __aexit__(self, *exc):
        return None

# ── enqueue_turn payload encoding ──────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_turn_packs_metadata_into_queue_metadata_json() -> None:
    """All non-message dispatch params (file_ids, mode, model,
    permissions, context, request_arrival_at) land in the
    ``queueMetadata`` JSON column so the dispatcher can replay the
    original turn shape later."""
    create = AsyncMock(return_value=MagicMock(id="msg-1"))
    with (
        patch.object(
            turn_queue.ChatMessage,
            "prisma",
            return_value=MagicMock(create=create),
        ),
        patch(
            "backend.copilot.db.get_next_sequence",
            new=AsyncMock(return_value=42),
        ),
        patch(
            "backend.copilot.model._get_session_lock",
            return_value=_NoopAsyncCM(),
        ),
        patch(
            "backend.copilot.model.invalidate_session_cache",
            new=AsyncMock(),
        ),
    ):
        await turn_queue.enqueue_turn(
            user_id="u1",
            session_id="s1",
            message="hello",
            message_id="msg-1",
            context={"url": "https://example.com"},
            file_ids=["f1", "f2"],
            mode="extended_thinking",
            model="advanced",
            permissions={"tool_filter": "allow"},
            request_arrival_at=123.45,
        )
    args, kwargs = create.call_args
    data = kwargs["data"]
    assert data["sessionId"] == "s1"
    assert data["sequence"] == 42
    assert data["queueStatus"] == turn_queue.STATUS_QUEUED
    metadata = data["queueMetadata"]  # SafeJson; Prisma will serialise
    # SafeJson wraps a dict — we just assert the inner shape.
    inner = getattr(metadata, "data", metadata)
    assert inner["context"] == {"url": "https://example.com"}
    assert inner["file_ids"] == ["f1", "f2"]
    assert inner["mode"] == "extended_thinking"
    assert inner["model"] == "advanced"
    assert inner["permissions"] == {"tool_filter": "allow"}
    assert inner["request_arrival_at"] == 123.45


@pytest.mark.asyncio
async def test_enqueue_turn_omits_null_fields_from_metadata() -> None:
    """A turn with no extra params (no file_ids / mode / context) leaves
    ``queueMetadata`` NULL rather than an empty object — keeps the
    column tiny on the hot ChatMessage table."""
    create = AsyncMock(return_value=MagicMock(id="msg-1"))
    with (
        patch.object(
            turn_queue.ChatMessage,
            "prisma",
            return_value=MagicMock(create=create),
        ),
        patch(
            "backend.copilot.db.get_next_sequence",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "backend.copilot.model._get_session_lock",
            return_value=_NoopAsyncCM(),
        ),
        patch(
            "backend.copilot.model.invalidate_session_cache",
            new=AsyncMock(),
        ),
    ):
        await turn_queue.enqueue_turn(
            user_id="u1",
            session_id="s1",
            message="hello",
        )
    args, kwargs = create.call_args
    assert kwargs["data"]["queueMetadata"] is None


# ── cancel_queued_turn ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_queued_turn_returns_true_on_atomic_update() -> None:
    """Update_many returning >0 rows means the cancel transition was
    applied (was queued AND owned by user)."""
    update_many = AsyncMock(return_value=1)
    with patch.object(
        turn_queue.ChatMessage,
        "prisma",
        return_value=MagicMock(update_many=update_many),
    ):
        ok = await turn_queue.cancel_queued_turn(user_id="u1", message_id="msg-1")
    assert ok is True
    where = update_many.call_args.kwargs["where"]
    assert where["queueStatus"] == turn_queue.STATUS_QUEUED
    assert where["Session"] == {"is": {"userId": "u1"}}


@pytest.mark.asyncio
async def test_cancel_queued_turn_returns_false_when_not_owned_or_not_queued() -> None:
    update_many = AsyncMock(return_value=0)
    with patch.object(
        turn_queue.ChatMessage,
        "prisma",
        return_value=MagicMock(update_many=update_many),
    ):
        ok = await turn_queue.cancel_queued_turn(user_id="u1", message_id="msg-1")
    assert ok is False


# ── dispatch_next_for_user gating ──────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_marks_blocked_when_user_paywalled() -> None:
    """A queued head row whose owner has lapsed to NO_TIER is marked
    ``blocked`` with a paywall reason instead of consuming a running
    slot for a turn that would immediately 402."""
    head = MagicMock(id="msg-1")
    find_first = AsyncMock(return_value=head)
    update = AsyncMock()
    with (
        patch.object(
            turn_queue.ChatMessage,
            "prisma",
            return_value=MagicMock(find_first=find_first, update=update),
        ),
        patch(
            "backend.copilot.rate_limit.is_user_paywalled",
            new=AsyncMock(return_value=True),
        ),
    ):
        promoted = await turn_queue.dispatch_next_for_user("u1")
    assert promoted is False
    update.assert_awaited_once()
    args, kwargs = update.call_args
    assert kwargs["where"] == {"id": "msg-1"}
    assert kwargs["data"]["queueStatus"] == turn_queue.STATUS_BLOCKED
    assert "Subscription required" in kwargs["data"]["queueBlockedReason"]


@pytest.mark.asyncio
async def test_dispatch_returns_false_when_queue_empty() -> None:
    """No-op when there's nothing queued for the user. Cheaper than
    raising — the slot-free hook fires for every completion regardless
    of queue state."""
    find_first = AsyncMock(return_value=None)
    with patch.object(
        turn_queue.ChatMessage,
        "prisma",
        return_value=MagicMock(find_first=find_first),
    ):
        promoted = await turn_queue.dispatch_next_for_user("u1")
    assert promoted is False


# ── status constants pinned ────────────────────────────────────────────


def test_status_constants_match_schema_strings() -> None:
    """If we ever rename a status string in turn_queue, the
    ``ChatMessage_queue_dispatch_idx`` partial index in the migration
    plus the frontend's queue-status badge would silently break. Pin
    the values."""
    assert turn_queue.STATUS_QUEUED == "queued"
    assert turn_queue.STATUS_BLOCKED == "blocked"
    assert turn_queue.STATUS_CANCELLED == "cancelled"
