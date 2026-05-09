"""Unit tests for active_turns: per-user concurrent AutoPilot turn tracking.

Backed entirely by ``ChatSession.currentTurnStartedAt`` accessed through
``chat_db()``; tests patch ``backend.copilot.active_turns.chat_db`` to
return an :class:`unittest.mock.AsyncMock`.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.copilot import active_turns
from backend.copilot.active_turns import (
    ConcurrentTurnLimitError,
    acquire_turn_slot,
    release_turn_slot,
)


def _mock_db(
    *,
    started_at: datetime | None = None,
    running_count: int = 0,
) -> MagicMock:
    """Mock chat_db() return value with the methods active_turns calls."""
    db = MagicMock()
    db.get_session_current_turn_started_at = AsyncMock(return_value=started_at)
    db.count_running_turns_for_user = AsyncMock(return_value=running_count)
    db.list_running_session_ids_for_user = AsyncMock(return_value=[])
    db.stamp_session_current_turn = AsyncMock()
    db.clear_session_current_turn = AsyncMock()
    return db


# ── release_turn_slot ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_clears_session_current_turn() -> None:
    """``release_turn_slot`` calls ``clear_session_current_turn`` with
    a userId guard so a misrouted call can't clear another user's row."""
    db = _mock_db()
    with patch.object(active_turns, "chat_db", return_value=db):
        await release_turn_slot("user-1", "session-a")
    db.clear_session_current_turn.assert_awaited_once_with("session-a", "user-1")


@pytest.mark.asyncio
async def test_release_anonymous_user_is_noop() -> None:
    """``release_turn_slot`` with empty user_id makes no DB write."""
    db = _mock_db()
    with patch.object(active_turns, "chat_db", return_value=db):
        await release_turn_slot("", "session-a")
    db.clear_session_current_turn.assert_not_awaited()


# ── acquire_turn_slot lifecycle ───────────────────────────────────────


@pytest.mark.asyncio
async def test_admitted_slot_releases_on_exit_without_keep() -> None:
    """Forgetting ``keep()`` on a clean exit releases the slot."""
    db = _mock_db(started_at=None)
    with patch.object(active_turns, "chat_db", return_value=db):
        async with acquire_turn_slot("user-1", "session-a"):
            pass
    db.stamp_session_current_turn.assert_awaited_once()
    db.clear_session_current_turn.assert_awaited_once_with("session-a", "user-1")


@pytest.mark.asyncio
async def test_admitted_slot_releases_on_exception() -> None:
    """An exception inside the with-block also releases the slot."""
    db = _mock_db(started_at=None)
    with patch.object(active_turns, "chat_db", return_value=db):
        with pytest.raises(RuntimeError, match="downstream blew up"):
            async with acquire_turn_slot("user-1", "session-a"):
                raise RuntimeError("downstream blew up")
    db.clear_session_current_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_kept_slot_is_not_released_on_exit() -> None:
    """``keep()`` transfers ownership; the context manager leaves the
    slot held for ``mark_session_completed`` to clean up."""
    db = _mock_db(started_at=None)
    with patch.object(active_turns, "chat_db", return_value=db):
        async with acquire_turn_slot("user-1", "session-a") as slot:
            slot.keep()
    db.stamp_session_current_turn.assert_awaited_once()
    db.clear_session_current_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejection_raises_concurrent_turn_limit_error() -> None:
    """At-or-above cap raises before any DB write."""
    db = _mock_db(started_at=None, running_count=5)
    with patch.object(active_turns, "chat_db", return_value=db):
        with pytest.raises(ConcurrentTurnLimitError):
            async with acquire_turn_slot("user-1", "session-a", capacity=5):
                pytest.fail("body must not run on rejection")  # pragma: no cover
    db.stamp_session_current_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_refreshed_slot_is_not_released_on_clean_exit() -> None:
    """Same-session re-entry → existing caller owns release; we just
    bump the timestamp and exit without clearing it."""
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    db = _mock_db(started_at=started)
    with patch.object(active_turns, "chat_db", return_value=db):
        async with acquire_turn_slot("user-1", "session-a"):
            pass
    db.stamp_session_current_turn.assert_awaited_once()
    db.clear_session_current_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_refreshed_slot_is_not_released_on_exception() -> None:
    """Same-session retry's failure must NOT tear down the original turn."""
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    db = _mock_db(started_at=started)
    with patch.object(active_turns, "chat_db", return_value=db):
        with pytest.raises(RuntimeError, match="boom"):
            async with acquire_turn_slot("user-1", "session-a"):
                raise RuntimeError("boom")
    db.clear_session_current_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_session_is_treated_as_fresh_admission() -> None:
    """A session whose ``currentTurnStartedAt`` is past the 6h cutoff is
    considered abandoned. The next acquire admits it as a fresh slot."""
    stale = datetime.now(timezone.utc) - timedelta(hours=7)
    db = _mock_db(started_at=stale)
    with patch.object(active_turns, "chat_db", return_value=db):
        async with acquire_turn_slot("user-1", "session-a"):
            pass
    # Fresh admit ⇒ released on exit.
    db.clear_session_current_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_anonymous_user_skips_gate() -> None:
    """``user_id`` falsy → no DB query, no exception."""
    db = _mock_db()
    with patch.object(active_turns, "chat_db", return_value=db):
        async with acquire_turn_slot(None, "session-a"):
            pass
    db.get_session_current_turn_started_at.assert_not_awaited()
    db.stamp_session_current_turn.assert_not_awaited()
    db.count_running_turns_for_user.assert_not_awaited()


# ── default cap pinning ───────────────────────────────────────────────


def test_schema_default_concurrent_turn_limit_is_15() -> None:
    """Pin the schema default so a config drift can't silently relax the
    abuse cap. Reads the field default directly so a local ``.env``
    override (e.g. lower cap for development) doesn't break the test."""
    from backend.util.settings import Config

    assert Config.model_fields["max_concurrent_copilot_turns_per_user"].default == 15
