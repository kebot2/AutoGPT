"""Unit tests for active_turns: per-user concurrent AutoPilot turn tracking.

Backed entirely by ``ChatSession.currentTurnStartedAt``; tests mock
Prisma's ``ChatSession.prisma()`` directly.
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


def _idle_session(session_id: str = "session-a") -> MagicMock:
    """ChatSession row with no active turn."""
    row = MagicMock(id=session_id)
    row.currentTurnStartedAt = None
    return row


def _running_session(session_id: str = "session-a") -> MagicMock:
    """ChatSession row whose currentTurnStartedAt is fresh (not stale)."""
    row = MagicMock(id=session_id)
    row.currentTurnStartedAt = datetime.now(timezone.utc) - timedelta(seconds=5)
    return row


def _stale_session(session_id: str = "session-a") -> MagicMock:
    """ChatSession row whose currentTurnStartedAt is past the 6h cutoff."""
    row = MagicMock(id=session_id)
    row.currentTurnStartedAt = datetime.now(timezone.utc) - timedelta(hours=7)
    return row


# ── release_turn_slot ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_clears_session_current_turn() -> None:
    """``release_turn_slot`` sets ``currentTurnStartedAt`` back to NULL,
    gated on userId so a misrouted call can't clear another user's session."""
    update_many = AsyncMock(return_value=1)
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(update_many=update_many),
    ):
        await release_turn_slot("user-1", "session-a")
    update_many.assert_awaited_once()
    kwargs = update_many.call_args.kwargs
    assert kwargs["where"] == {"id": "session-a", "userId": "user-1"}
    assert kwargs["data"] == {"currentTurnStartedAt": None}


@pytest.mark.asyncio
async def test_release_anonymous_user_is_noop() -> None:
    """``release_turn_slot`` with empty user_id makes no DB write."""
    update_many = AsyncMock()
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(update_many=update_many),
    ):
        await release_turn_slot("", "session-a")
    update_many.assert_not_awaited()


# ── acquire_turn_slot lifecycle ───────────────────────────────────────


@pytest.mark.asyncio
async def test_admitted_slot_releases_on_exit_without_keep() -> None:
    """Forgetting ``keep()`` on a clean exit releases the slot."""
    find_unique = AsyncMock(return_value=_idle_session())
    update_many = AsyncMock(return_value=1)
    count = AsyncMock(return_value=0)
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(
            find_unique=find_unique, update_many=update_many, count=count
        ),
    ):
        async with acquire_turn_slot("user-1", "session-a"):
            pass
    # 2 update_many calls: 1 admit stamp, 1 release.
    assert update_many.await_count == 2
    assert update_many.await_args_list[-1].kwargs["data"] == {
        "currentTurnStartedAt": None
    }


@pytest.mark.asyncio
async def test_admitted_slot_releases_on_exception() -> None:
    """An exception inside the with-block also releases the slot."""
    find_unique = AsyncMock(return_value=_idle_session())
    update_many = AsyncMock(return_value=1)
    count = AsyncMock(return_value=0)
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(
            find_unique=find_unique, update_many=update_many, count=count
        ),
    ):
        with pytest.raises(RuntimeError, match="downstream blew up"):
            async with acquire_turn_slot("user-1", "session-a"):
                raise RuntimeError("downstream blew up")
    # admit + release
    assert update_many.await_count == 2


@pytest.mark.asyncio
async def test_kept_slot_is_not_released_on_exit() -> None:
    """``keep()`` transfers ownership; the context manager leaves the
    slot held for ``mark_session_completed`` to clean up."""
    find_unique = AsyncMock(return_value=_idle_session())
    update_many = AsyncMock(return_value=1)
    count = AsyncMock(return_value=0)
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(
            find_unique=find_unique, update_many=update_many, count=count
        ),
    ):
        async with acquire_turn_slot("user-1", "session-a") as slot:
            slot.keep()
    # only the admit stamp; no release.
    assert update_many.await_count == 1


@pytest.mark.asyncio
async def test_rejection_raises_concurrent_turn_limit_error() -> None:
    """At-or-above cap raises before any DB write."""
    find_unique = AsyncMock(return_value=_idle_session())
    update_many = AsyncMock()
    count = AsyncMock(return_value=5)  # at the default running cap
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(
            find_unique=find_unique, update_many=update_many, count=count
        ),
    ):
        with pytest.raises(ConcurrentTurnLimitError):
            async with acquire_turn_slot("user-1", "session-a", capacity=5):
                pytest.fail("body must not run on rejection")  # pragma: no cover
    update_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_refreshed_slot_is_not_released_on_clean_exit() -> None:
    """Same-session re-entry → existing caller owns release; we just
    bump the timestamp and exit without clearing it."""
    find_unique = AsyncMock(return_value=_running_session())
    update_many = AsyncMock(return_value=1)
    count = AsyncMock(return_value=0)
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(
            find_unique=find_unique, update_many=update_many, count=count
        ),
    ):
        async with acquire_turn_slot("user-1", "session-a"):
            pass
    # 1 timestamp-bump update, no release.
    update_many.assert_awaited_once()
    assert (
        update_many.await_args_list[-1].kwargs["data"]["currentTurnStartedAt"]
        is not None
    )


@pytest.mark.asyncio
async def test_refreshed_slot_is_not_released_on_exception() -> None:
    """Same-session retry's failure must NOT tear down the original turn."""
    find_unique = AsyncMock(return_value=_running_session())
    update_many = AsyncMock(return_value=1)
    count = AsyncMock(return_value=0)
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(
            find_unique=find_unique, update_many=update_many, count=count
        ),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            async with acquire_turn_slot("user-1", "session-a"):
                raise RuntimeError("boom")
    update_many.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_session_is_treated_as_fresh_admission() -> None:
    """A session whose ``currentTurnStartedAt`` is past the 6h cutoff is
    considered abandoned. The next acquire admits it as a fresh slot."""
    find_unique = AsyncMock(return_value=_stale_session())
    update_many = AsyncMock(return_value=1)
    count = AsyncMock(return_value=0)
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(
            find_unique=find_unique, update_many=update_many, count=count
        ),
    ):
        async with acquire_turn_slot("user-1", "session-a"):
            pass
    # Fresh admit ⇒ released on exit. 2 update_many calls.
    assert update_many.await_count == 2


@pytest.mark.asyncio
async def test_anonymous_user_skips_gate() -> None:
    """``user_id`` falsy → no DB query, no exception."""
    find_unique = AsyncMock()
    update_many = AsyncMock()
    count = AsyncMock()
    with patch.object(
        active_turns.ChatSession,
        "prisma",
        return_value=MagicMock(
            find_unique=find_unique, update_many=update_many, count=count
        ),
    ):
        async with acquire_turn_slot(None, "session-a"):
            pass
    find_unique.assert_not_awaited()
    update_many.assert_not_awaited()
    count.assert_not_awaited()


# ── default cap pinning ───────────────────────────────────────────────


def test_schema_default_concurrent_turn_limit_is_15() -> None:
    """Pin the schema default so a config drift can't silently relax the
    abuse cap. Reads the field default directly so a local ``.env``
    override (e.g. lower cap for development) doesn't break the test."""
    from backend.util.settings import Config

    assert Config.model_fields["max_concurrent_copilot_turns_per_user"].default == 15
