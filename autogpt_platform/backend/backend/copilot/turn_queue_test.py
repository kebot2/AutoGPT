"""Unit tests for turn_queue: per-user FIFO queue layered over ChatMessage.

DB access is mocked via the ``backend.copilot.turn_queue.chat_db``
indirection — same accessor pattern the executor subprocess uses to RPC
into ``DatabaseManager``. Patching the accessor avoids reaching for
Prisma directly while still exercising the queue's branching.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.copilot import turn_queue
from backend.copilot.model import ChatMessage as PydanticChatMessage


class _NoopAsyncCM:
    """Stand-in for the Redis NX session lock context manager. The lock
    only matters in production for cross-replica serialisation; in unit
    tests there's no concurrent submitter so we just no-op."""

    async def __aenter__(self):
        return True

    async def __aexit__(self, *exc):
        return None


def _pyd_message(**overrides) -> PydanticChatMessage:
    """Build a Pydantic ChatMessage with sensible defaults overrideable."""
    base = {
        "id": "msg-1",
        "role": "user",
        "content": "hello",
        "session_id": "s1",
        "queue_status": turn_queue.STATUS_QUEUED,
        "queue_metadata": None,
        "created_at": datetime.now(timezone.utc),
        "sequence": 1,
    }
    base.update(overrides)
    return PydanticChatMessage(**base)


# ── enqueue_turn payload encoding ──────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_turn_packs_metadata_into_queue_metadata_payload() -> None:
    """Non-message dispatch params (file_ids, mode, model, permissions,
    context, request_arrival_at) land in the ``queue_metadata`` payload
    so the dispatcher can replay the original turn shape later."""
    db = MagicMock()
    db.get_next_sequence = AsyncMock(return_value=42)
    db.insert_queued_turn = AsyncMock(return_value=_pyd_message(sequence=42))
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
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
    kwargs = db.insert_queued_turn.call_args.kwargs
    assert kwargs["session_id"] == "s1"
    assert kwargs["sequence"] == 42
    metadata = kwargs["queue_metadata"]
    assert metadata["context"] == {"url": "https://example.com"}
    assert metadata["file_ids"] == ["f1", "f2"]
    assert metadata["mode"] == "extended_thinking"
    assert metadata["model"] == "advanced"
    assert metadata["permissions"] == {"tool_filter": "allow"}
    assert metadata["request_arrival_at"] == 123.45


@pytest.mark.asyncio
async def test_enqueue_turn_omits_null_fields_from_metadata() -> None:
    """A turn with no extra params (no file_ids / mode / context) leaves
    ``queue_metadata`` NULL rather than an empty object — keeps the
    column tiny on the hot ChatMessage table."""
    db = MagicMock()
    db.get_next_sequence = AsyncMock(return_value=1)
    db.insert_queued_turn = AsyncMock(return_value=_pyd_message())
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
        patch(
            "backend.copilot.model._get_session_lock",
            return_value=_NoopAsyncCM(),
        ),
        patch(
            "backend.copilot.model.invalidate_session_cache",
            new=AsyncMock(),
        ),
    ):
        await turn_queue.enqueue_turn(session_id="s1", message="hello")
    assert db.insert_queued_turn.call_args.kwargs["queue_metadata"] is None


# ── cancel_queued_turn ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_queued_turn_returns_true_and_invalidates_cache() -> None:
    """A successful cancel returns the row's sessionId from chat_db and
    invalidates the session cache so the frontend drops the badge."""
    db = MagicMock()
    db.cancel_queued_turn_for_user = AsyncMock(return_value="s1")
    invalidate = AsyncMock()
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
        patch(
            "backend.copilot.model.invalidate_session_cache",
            new=invalidate,
        ),
    ):
        ok = await turn_queue.cancel_queued_turn(user_id="u1", message_id="msg-1")
    assert ok is True
    invalidate.assert_awaited_once_with("s1")


@pytest.mark.asyncio
async def test_cancel_queued_turn_returns_false_when_not_owned_or_not_queued() -> None:
    db = MagicMock()
    db.cancel_queued_turn_for_user = AsyncMock(return_value=None)
    with patch.object(turn_queue, "chat_db", return_value=db):
        ok = await turn_queue.cancel_queued_turn(user_id="u1", message_id="msg-1")
    assert ok is False


# ── mark_queued_turn_blocked ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_queued_turn_blocked_invalidates_cache_on_success() -> None:
    db = MagicMock()
    db.mark_queued_turn_blocked_db = AsyncMock(return_value="s1")
    invalidate = AsyncMock()
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
        patch(
            "backend.copilot.model.invalidate_session_cache",
            new=invalidate,
        ),
    ):
        await turn_queue.mark_queued_turn_blocked(message_id="msg-1", reason="paywall")
    invalidate.assert_awaited_once_with("s1")


@pytest.mark.asyncio
async def test_mark_queued_turn_blocked_noop_when_not_queued() -> None:
    """No invalidation when the row was already cancelled / claimed."""
    db = MagicMock()
    db.mark_queued_turn_blocked_db = AsyncMock(return_value=None)
    invalidate = AsyncMock()
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
        patch(
            "backend.copilot.model.invalidate_session_cache",
            new=invalidate,
        ),
    ):
        await turn_queue.mark_queued_turn_blocked(message_id="msg-1", reason="paywall")
    invalidate.assert_not_awaited()


# ── claim_queued_turn_by_id ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_queued_turn_by_id_returns_none_when_no_longer_queued() -> None:
    """A parallel cancel / block that flipped queueStatus before the
    claim's update_many matched any row → None, so the dispatcher
    doesn't promote a different unvalidated row."""
    db = MagicMock()
    db.claim_queued_turn_by_id_db = AsyncMock(return_value=None)
    with patch.object(turn_queue, "chat_db", return_value=db):
        row = await turn_queue.claim_queued_turn_by_id("msg-1")
    assert row is None


@pytest.mark.asyncio
async def test_claim_queued_turn_by_id_returns_row_when_claimed() -> None:
    """When the claim wins the race, the dispatcher gets the row back."""
    claimed = _pyd_message(queue_status=None)
    db = MagicMock()
    db.claim_queued_turn_by_id_db = AsyncMock(return_value=claimed)
    with patch.object(turn_queue, "chat_db", return_value=db):
        row = await turn_queue.claim_queued_turn_by_id("msg-1")
    assert row is claimed
    db.claim_queued_turn_by_id_db.assert_awaited_once_with(message_id="msg-1")


# ── try_enqueue_turn ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_try_enqueue_turn_raises_when_at_inflight_cap() -> None:
    """Pre-check rejects when running + queued already equals the cap."""
    db = MagicMock()
    db.count_queued_turns_for_user = AsyncMock(return_value=10)
    db.insert_queued_turn = AsyncMock()
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
        patch.object(turn_queue, "count_running_turns", new=AsyncMock(return_value=5)),
    ):
        with pytest.raises(turn_queue.InflightCapExceeded):
            await turn_queue.try_enqueue_turn(
                user_id="u1",
                inflight_cap=15,
                session_id="s1",
                message="hi",
            )
    db.insert_queued_turn.assert_not_awaited()


# ── dispatch_next_for_user gating ──────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_marks_blocked_when_user_paywalled() -> None:
    """A queued head whose owner has lapsed to NO_TIER is marked
    ``blocked`` with a paywall reason instead of consuming a running
    slot for a turn that would immediately 402."""
    head = _pyd_message()
    db = MagicMock()
    db.find_oldest_queued_turn_for_user = AsyncMock(return_value=head)
    db.mark_queued_turn_blocked_db = AsyncMock(return_value="s1")
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
        patch(
            "backend.copilot.rate_limit.is_user_paywalled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "backend.copilot.active_turns.get_running_session_ids",
            new=AsyncMock(return_value=set()),
        ),
        patch(
            "backend.copilot.model.invalidate_session_cache",
            new=AsyncMock(),
        ),
    ):
        promoted = await turn_queue.dispatch_next_for_user("u1")
    assert promoted is False
    db.mark_queued_turn_blocked_db.assert_awaited_once()
    kwargs = db.mark_queued_turn_blocked_db.call_args.kwargs
    assert kwargs["message_id"] == "msg-1"
    assert "Subscription required" in kwargs["reason"]


@pytest.mark.asyncio
async def test_dispatch_returns_false_when_queue_empty() -> None:
    """No-op when there's nothing queued for the user."""
    db = MagicMock()
    db.find_oldest_queued_turn_for_user = AsyncMock(return_value=None)
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
        patch(
            "backend.copilot.active_turns.get_running_session_ids",
            new=AsyncMock(return_value=set()),
        ),
    ):
        promoted = await turn_queue.dispatch_next_for_user("u1")
    assert promoted is False


@pytest.mark.asyncio
async def test_dispatch_skips_busy_session() -> None:
    """If the queued head's session already has a running turn, defer."""
    head = _pyd_message(session_id="busy-session")
    db = MagicMock()
    db.find_oldest_queued_turn_for_user = AsyncMock(return_value=head)
    db.mark_queued_turn_blocked_db = AsyncMock()
    with (
        patch.object(turn_queue, "chat_db", return_value=db),
        patch(
            "backend.copilot.active_turns.get_running_session_ids",
            new=AsyncMock(return_value={"busy-session"}),
        ),
    ):
        promoted = await turn_queue.dispatch_next_for_user("u1")
    assert promoted is False
    db.mark_queued_turn_blocked_db.assert_not_awaited()


# ── status constants pinned ────────────────────────────────────────────


def test_status_constants_match_schema_strings() -> None:
    """If we ever rename a status string in turn_queue, the
    ``ChatMessage_queue_dispatch_idx`` partial index in the migration
    plus the frontend's queue-status badge would silently break. Pin
    the values."""
    assert turn_queue.STATUS_QUEUED == "queued"
    assert turn_queue.STATUS_BLOCKED == "blocked"
    assert turn_queue.STATUS_CANCELLED == "cancelled"
