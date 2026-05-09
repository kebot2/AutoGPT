"""Per-user concurrent AutoPilot turn tracking, backed entirely by Postgres.

Each :class:`prisma.models.ChatSession` carries a
``currentTurnStartedAt`` timestamp:

* ``NULL`` — no active turn (the 99% case for idle sessions).
* set — a turn is currently running on this session. Stale entries
  (older than :data:`MAX_TURN_LIFETIME_SECONDS`) are filtered out at
  read time, so a crashed turn cannot permanently hold a slot.

Public API
----------

* :func:`acquire_turn_slot` — async context manager. Counts the user's
  currently-running sessions, raises :class:`ConcurrentTurnLimitError`
  at the cap, otherwise stamps ``currentTurnStartedAt`` and yields a
  handle whose release transfers to ``mark_session_completed`` via
  :meth:`TurnSlot.keep`.
* :func:`release_turn_slot` — clears ``currentTurnStartedAt``. Called
  from ``mark_session_completed`` when the turn ends.
* :func:`count_running_turns` / :func:`get_running_session_ids` —
  used by the queue layer (in-flight = running + queued) and the
  dispatcher's busy-session check.

Cap admission is a *non-locked* count-then-update. Two concurrent
submits from the same user can both pass the count and both update,
leaving the user briefly one or two over the cap. This is the same
trade-off the graph-execution credit rate-limit accepts on its
``INCRBY`` path: the cap is a safeguard, not a budget. Going to 16/17
in-flight under burst is acceptable; the row-locked alternative would
add a transaction round trip per admit for no real correctness gain.

DB access goes through :func:`backend.data.db_accessors.chat_db` so
the dispatcher works from both the HTTP server (Prisma directly) and
the copilot_executor process (RPC via DatabaseManager).
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from backend.data.db_accessors import chat_db
from backend.util.settings import Settings

# Upper bound on a single AutoPilot turn's wall-clock duration. Beyond
# this we treat the turn as abandoned: the read-time filter excludes the
# row from the running count so a crashed turn doesn't hold a slot
# forever. Far exceeds typical chat-turn duration (seconds-minutes) so
# legitimate long-running tool calls (E2B, deep web crawls) aren't
# penalised. The normal release path is ``mark_session_completed``;
# this is the safety net.
MAX_TURN_LIFETIME_SECONDS = 6 * 60 * 60


def get_running_turn_limit() -> int:
    """Configured soft cap on concurrently *running* turns per user.

    Tasks submitted while the user is at this cap are queued up to
    :func:`get_inflight_turn_limit`. Reading at call time so operators
    can retune via env-backed Settings without a redeploy.
    """
    return Settings().config.max_running_copilot_turns_per_user


def get_inflight_turn_limit() -> int:
    """Configured hard cap on in-flight (running + queued) turns per user.

    Once total in-flight hits this, :class:`ConcurrentTurnLimitError`
    is raised on new submissions and the API returns HTTP 429.
    """
    return Settings().config.max_concurrent_copilot_turns_per_user


def inflight_turn_limit_message(limit: int | None = None) -> str:
    """User-facing 429 detail when the in-flight cap is hit. Includes
    queued tasks in the count to match the user's mental model
    ('15 active = 5 running + 10 queued')."""
    resolved = get_inflight_turn_limit() if limit is None else limit
    return (
        f"You've reached the limit of {resolved} active tasks (running + queued). "
        "Please wait for one of your current tasks to finish before starting a new one."
    )


def running_turn_limit_message(limit: int | None = None) -> str:
    """Default :class:`ConcurrentTurnLimitError` detail when the
    *running* cap is hit on a path that does not queue (e.g.
    ``AutoPilotBlock``, ``run_sub_session``). The HTTP route catches
    the error before it surfaces and replaces the message with the
    inflight one."""
    resolved = get_running_turn_limit() if limit is None else limit
    return (
        f"You have {resolved} AutoPilot tasks already running. "
        "Please wait for one of them to finish before starting a new one."
    )


def queued_turn_message() -> str:
    """User-facing message rendered when a turn is queued instead of
    starting immediately because the running cap is full."""
    return (
        "Your task has been queued and will start automatically when one of "
        "your current tasks finishes."
    )


# Back-compat shims — older module name. Prefer the explicit running /
# inflight variants in new code.
get_concurrent_turn_limit = get_running_turn_limit
concurrent_turn_limit_message = running_turn_limit_message


class ConcurrentTurnLimitError(Exception):
    """User has reached the configured running AutoPilot turn cap.

    The HTTP chat route catches this and falls through to the FIFO
    queue (or 429 at the inflight cap). Non-HTTP paths surface the
    default :func:`running_turn_limit_message` to the user.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or running_turn_limit_message())


def _stale_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=MAX_TURN_LIFETIME_SECONDS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# Reads
# ============================================================================


async def count_running_turns(user_id: str) -> int:
    """User's current running-turn count, excluding stale entries."""
    return await chat_db().count_running_turns_for_user(user_id, _stale_cutoff())


async def get_running_session_ids(user_id: str) -> set[str]:
    """Set of the user's session IDs currently running a turn.

    Used by the dispatcher to skip queued heads whose session already
    has a running turn — promoting a second turn for the same session
    would silently replace its ``currentTurnStartedAt`` and the first
    completion would clear it for both.
    """
    return set(
        await chat_db().list_running_session_ids_for_user(user_id, _stale_cutoff())
    )


# ============================================================================
# Mutations
# ============================================================================


async def release_turn_slot(user_id: str, session_id: str) -> None:
    """Clear the session's ``currentTurnStartedAt``. Idempotent.

    Called from ``mark_session_completed`` when a turn ends. The
    ``userId`` guard ensures we never clear another user's session if
    a stale call ever fires with the wrong identity.
    """
    if not user_id:
        return
    await chat_db().clear_session_current_turn(session_id, user_id)


class TurnSlot:
    """Handle yielded by :func:`acquire_turn_slot`.

    Call :meth:`keep` once a turn has been successfully scheduled to
    transfer release ownership to ``mark_session_completed``. Without
    ``keep``, the context manager auto-releases on exit — but only when
    *this* caller admitted the slot. A re-entrant refresh leaves the
    slot alone, since some earlier caller still owns it.
    """

    __slots__ = ("user_id", "session_id", "admitted", "_kept")

    def __init__(self, user_id: str, session_id: str) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.admitted = False
        self._kept = False

    def keep(self) -> None:
        """Transfer slot ownership out of this context. Caller is now
        responsible for ensuring ``mark_session_completed`` releases the
        slot (or accepts the stale-cutoff fallback)."""
        self._kept = True


@asynccontextmanager
async def acquire_turn_slot(
    user_id: str | None,
    session_id: str,
    capacity: int | None = None,
) -> AsyncIterator[TurnSlot]:
    """Reserve a turn slot for the duration of the ``async with`` block.

    ``capacity`` controls how many concurrent slots the user may hold:

    * The HTTP chat route uses the default (running cap, default 5) so
      the 6th submit raises :class:`ConcurrentTurnLimitError` and the
      route falls through to the FIFO queue.
    * Non-HTTP entry points (``schedule_turn`` for ``run_sub_session``
      / ``AutoPilotBlock``) pass the inflight cap (default 15) since
      they have no queue and must preserve the prior #13064 cap.

    Three branches on entry:

    * **Admitted** — session was idle and the user is below the cap;
      ``currentTurnStartedAt`` is stamped now. Exit auto-releases
      unless :meth:`TurnSlot.keep` was called.
    * **Refreshed** — same ``session_id`` already running (network
      retry, duplicate request); the timestamp is bumped but this
      caller does NOT own the release. Exiting without ``keep`` is a
      no-op.
    * **Rejected** — at the cap; raises :class:`ConcurrentTurnLimitError`.

    Anonymous sessions (``user_id`` falsy) bypass the cap entirely.
    """
    handle = TurnSlot(user_id or "", session_id)
    if not user_id:
        yield handle
        return

    resolved_capacity = capacity if capacity is not None else get_running_turn_limit()
    db = chat_db()
    started_at = await db.get_session_current_turn_started_at(session_id)
    is_refresh = started_at is not None and started_at > _stale_cutoff()

    if not is_refresh:
        if await count_running_turns(user_id) >= resolved_capacity:
            raise ConcurrentTurnLimitError(
                running_turn_limit_message(resolved_capacity)
            )
        handle.admitted = True

    # Idempotent stamp — bumps the timestamp on refresh, sets it on a
    # fresh admit. ``userId`` guard prevents stamping someone else's
    # session under a misrouted request.
    await db.stamp_session_current_turn(session_id, user_id, _now())

    try:
        yield handle
    finally:
        if handle.admitted and not handle._kept:
            await release_turn_slot(user_id, session_id)
