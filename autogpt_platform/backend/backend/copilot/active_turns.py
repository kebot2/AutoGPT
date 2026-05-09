"""Per-user concurrent AutoPilot turn tracking.

Tracks how many copilot chat turns a user has running concurrently and
enforces a hard cap so a single user (typically via the API) cannot spawn
hundreds of simultaneous turns and exhaust shared infrastructure.

Storage is a Redis sorted set per user (``copilot:user_active_turns:{user_id}``),
member = ``session_id`` (one in-flight turn per session at most), score =
unix timestamp of acquisition. The atomic admit / sweep / cap logic lives
in :func:`backend.data.redis_helpers.try_reserve_slot` — this module
just supplies the per-user keying, the lifecycle context manager, and the
fail-open posture on Redis errors.

Lifecycle
---------

The supported pattern is the :func:`acquire_turn_slot` async context manager.
On a normal turn the chat route enters the manager, schedules the turn, and
calls ``slot.keep()`` to transfer ownership of the slot to
``mark_session_completed``, which releases it once the turn ends. If anything
between ``__aenter__`` and ``slot.keep()`` raises (``create_session`` failure,
``enqueue_copilot_turn`` failure, etc.), the slot is released automatically
on context exit, so the route never leaks a slot on infrastructure errors.
"""

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from redis.exceptions import RedisClusterException, RedisError

from backend.data.redis_client import AsyncRedisClient, get_redis_async
from backend.data.redis_helpers import SlotReservation, try_reserve_slot
from backend.util.settings import Settings

logger = logging.getLogger(__name__)


# Upper bound on a single AutoPilot turn's wall-clock duration. Beyond this
# we treat the turn as abandoned: the slot is reclaimed by the stale-cutoff
# sweep (so a crashed turn doesn't hold a slot forever) and the
# :class:`AutoPilotBlock` execution wait gives up. Far exceeds typical chat
# turn duration (seconds-minutes) so legitimate long-running tool calls
# (E2B sandbox, deep web crawls, etc.) aren't penalised. The normal release
# path is ``mark_session_completed``; this is the safety net.
MAX_TURN_LIFETIME_SECONDS = 6 * 60 * 60

# Backwards-compatible alias used by the active-turns sweep. Kept distinct
# in case future tuning wants different bounds for the two consumers.
STALE_TURN_CUTOFF_SECONDS = MAX_TURN_LIFETIME_SECONDS

_USER_ACTIVE_TURNS_KEY_PREFIX = "copilot:user_active_turns:"


def get_concurrent_turn_limit() -> int:
    """Resolve the configured per-user concurrent-turn cap at call time.

    Reading at call time (rather than module load) lets operators retune
    the cap by editing the env-backed Settings without redeploying the
    code that imports this module.
    """
    return Settings().config.max_concurrent_copilot_turns_per_user


def concurrent_turn_limit_message(limit: int | None = None) -> str:
    """User-facing 429 detail string. Pass ``limit`` if you already
    resolved it; otherwise we read the configured value."""
    resolved = get_concurrent_turn_limit() if limit is None else limit
    return (
        f"You've reached the limit of {resolved} active tasks. Please wait "
        f"for one of your current tasks to finish before starting a new one."
    )


class ConcurrentTurnLimitError(Exception):
    """User has reached the configured concurrent in-flight AutoPilot
    turn cap. Maps to HTTP 429 in the API layer.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or concurrent_turn_limit_message())


def _user_key(user_id: str) -> str:
    # Hash-tag braces ensure all keys for a single user co-locate on the same
    # Redis Cluster slot — required for any future Lua that touches multiple
    # per-user keys atomically.
    return f"{_USER_ACTIVE_TURNS_KEY_PREFIX}{{{user_id}}}"


async def try_acquire_turn_slot(
    user_id: str,
    session_id: str,
    limit: int | None = None,
) -> SlotReservation:
    """Atomically reserve a turn slot for ``user_id``.

    Returns the :class:`SlotReservation` outcome:

    * ``ADMITTED`` — a fresh slot was added; caller owns its release.
    * ``REFRESHED`` — ``session_id`` was already in the pool; the score
      was bumped but the slot count is unchanged. Some other caller
      already owns this slot's release; the current caller MUST NOT
      release it on exit.
    * ``REJECTED`` — pool was at ``limit`` and the slot wasn't already
      held (typically maps to HTTP 429 in the API layer).

    Fails open on Redis errors — returns ``ADMITTED`` so the route
    continues but logs a warning. Failing closed here would 429 every
    user during a Redis brown-out, which is worse than the abuse-
    protection brief gap.

    Most callers should use :func:`acquire_turn_slot` instead, which
    wraps the acquire/release lifecycle in a context manager so the slot
    can't leak on a downstream failure.
    """
    capacity = limit if limit is not None else get_concurrent_turn_limit()
    try:
        redis = await get_redis_async()
        now = time.time()
        return await try_reserve_slot(
            redis,
            pool_key=_user_key(user_id),
            slot_id=session_id,
            score=now,
            capacity=capacity,
            stale_before_score=now - STALE_TURN_CUTOFF_SECONDS,
            ttl_seconds=STALE_TURN_CUTOFF_SECONDS,
        )
    except (RedisError, RedisClusterException, ConnectionError, OSError) as exc:
        logger.warning(
            "try_acquire_turn_slot: Redis unavailable for user=%s; failing open: %s",
            user_id,
            exc,
        )
        return SlotReservation.ADMITTED


async def release_turn_slot(user_id: str, session_id: str) -> None:
    """Remove ``session_id`` from ``user_id``'s active-turns set.

    Idempotent. Best-effort — a Redis error here only delays slot release
    until the stale-cutoff sweep on the next acquisition.
    """
    try:
        redis = await get_redis_async()
        await redis.zrem(_user_key(user_id), session_id)
    except (RedisError, RedisClusterException, ConnectionError, OSError) as exc:
        logger.warning(
            "release_turn_slot: Redis unavailable for user=%s session=%s: %s",
            user_id,
            session_id,
            exc,
        )


async def count_active_turns(user_id: str) -> int:
    """Return the user's current active-turn count, after sweeping stale
    entries. Best-effort — returns 0 if Redis is unreachable.
    """
    try:
        redis: AsyncRedisClient = await get_redis_async()
        key = _user_key(user_id)
        await redis.zremrangebyscore(
            key, "-inf", time.time() - STALE_TURN_CUTOFF_SECONDS
        )
        return await redis.zcard(key)
    except (RedisError, RedisClusterException, ConnectionError, OSError) as exc:
        logger.warning(
            "count_active_turns: Redis unavailable for user=%s: %s", user_id, exc
        )
        return 0


class TurnSlot:
    """Handle yielded by :func:`acquire_turn_slot`. Call :meth:`keep` once
    the turn has been successfully scheduled to transfer ownership of the
    slot to ``mark_session_completed`` (which releases it on turn end).

    Only slots in the ``ADMITTED`` state (newly added to the pool) can be
    released by this context manager; ``REFRESHED`` reservations indicate
    another caller already owns the release path, so dropping out of this
    ``async with`` without ``keep()`` is a no-op for them.

    If :meth:`keep` is never called — because acquisition was refused, the
    user_id was empty, the slot was a refresh of an existing reservation,
    or the with-block exits with or without an exception before reaching
    the call — the context manager only releases the slot when this
    caller actually admitted it.
    """

    __slots__ = ("user_id", "session_id", "admitted", "_kept")

    def __init__(self, user_id: str, session_id: str) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.admitted = False  # True only when this caller newly admitted the slot
        self._kept = False

    def keep(self) -> None:
        """Transfer slot ownership out of this context. Caller is now
        responsible for ensuring ``mark_session_completed`` will release
        it (or accepting the stale-cutoff fallback)."""
        self._kept = True


@asynccontextmanager
async def acquire_turn_slot(
    user_id: str | None,
    session_id: str,
    limit: int | None = None,
) -> AsyncIterator[TurnSlot]:
    """Reserve a turn slot for the duration of the ``async with`` block.

    On entry, raises :class:`ConcurrentTurnLimitError` if the user is at
    ``limit`` AND ``session_id`` isn't already in the pool. A re-entrant
    reservation for an already-active ``session_id`` (e.g. a same-
    session network retry) just refreshes the existing slot's score —
    this branch never raises and never claims release ownership, since
    some earlier caller is still holding the slot for the original turn.

    Anonymous sessions (``user_id`` falsy) bypass the gate and yield a
    no-op handle.

    On exit:

    * if the body called :meth:`TurnSlot.keep`, the slot is held — the
      caller has handed off ownership to a downstream completion path.
    * if this caller newly admitted the slot but did not call ``keep``
      (clean exit *or* exception), the slot is released immediately so
      failed schedules don't leak a slot until the stale-cutoff sweep.
    * if this caller only refreshed an existing reservation, exiting
      without ``keep`` is a no-op — the slot stays held for whoever
      originally admitted it.
    """
    handle = TurnSlot(user_id or "", session_id)
    if user_id:
        outcome = await try_acquire_turn_slot(user_id, session_id, limit)
        if outcome is SlotReservation.REJECTED:
            raise ConcurrentTurnLimitError()
        if outcome is SlotReservation.ADMITTED:
            handle.admitted = True

    try:
        yield handle
    finally:
        if handle.admitted and not handle._kept:
            await release_turn_slot(handle.user_id, handle.session_id)
