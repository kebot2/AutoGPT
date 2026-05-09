"""Per-user concurrent AutoPilot turn tracking.

Tracks how many copilot chat turns a user has running concurrently and
enforces a hard cap so a single user (typically via the API) cannot spawn
hundreds of simultaneous turns and exhaust shared infrastructure.

Storage is a Redis sorted set per user (``copilot:user_active_turns:{user_id}``),
member = ``session_id`` (one in-flight turn per session at most), score =
unix timestamp of acquisition. Stale entries (older than
:data:`STALE_TURN_CUTOFF_SECONDS`) are auto-cleaned on every acquisition,
so a crashed turn that never released its slot does not permanently consume
the cap.

Acquisition is via a single Lua script that atomically:

* drops stale entries
* refreshes the score for an existing session_id (re-acquire is a no-op)
* otherwise checks the count against the limit and adds the new member

This keeps two concurrent ``POST /chat`` requests from both reading
``count = 14`` and both sneaking through.

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

logger = logging.getLogger(__name__)


# Default cap; can be overridden by the ``copilot_max_inflight_turns_per_user``
# setting once SECRT-2339 lands the configurable queue. For the hotfix this
# is the single in-flight gate.
MAX_CONCURRENT_TURNS_PER_USER = 15

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

CONCURRENT_TURN_LIMIT_MESSAGE = (
    f"You've reached the limit of {MAX_CONCURRENT_TURNS_PER_USER} active tasks. "
    f"Please wait for one of your current tasks to finish before starting a new one."
)

_USER_ACTIVE_TURNS_KEY_PREFIX = "copilot:user_active_turns:"


class ConcurrentTurnLimitError(Exception):
    """User has reached :data:`MAX_CONCURRENT_TURNS_PER_USER` in-flight
    AutoPilot turns. Maps to HTTP 429 in the API layer.
    """

    def __init__(self, message: str = CONCURRENT_TURN_LIMIT_MESSAGE) -> None:
        super().__init__(message)


# Atomic check-and-add. KEYS[1] = user's sorted set; ARGV[1] = session_id;
# ARGV[2] = now (score for new entry); ARGV[3] = stale cutoff timestamp;
# ARGV[4] = limit; ARGV[5] = key TTL seconds. Returns 1 on acquired, 0 on rejected.
_TRY_ACQUIRE_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[3])
local existing = redis.call('ZSCORE', KEYS[1], ARGV[1])
if existing then
    redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
    redis.call('EXPIRE', KEYS[1], ARGV[5])
    return 1
end
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[4]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


def _user_key(user_id: str) -> str:
    # Hash-tag braces ensure all keys for a single user co-locate on the same
    # Redis Cluster slot — required for any future Lua that touches multiple
    # per-user keys atomically.
    return f"{_USER_ACTIVE_TURNS_KEY_PREFIX}{{{user_id}}}"


async def try_acquire_turn_slot(
    user_id: str,
    session_id: str,
    limit: int = MAX_CONCURRENT_TURNS_PER_USER,
) -> bool:
    """Atomically reserve a turn slot for ``user_id``.

    Returns ``True`` if a slot was acquired (or the same ``session_id`` was
    already present and got its score refreshed), ``False`` if the user is at
    or above ``limit`` active turns.

    Fails open on Redis errors — the route continues but logs a warning.
    Failing closed here would 429 every user during a Redis brown-out, which
    is worse than the abuse-protection brief gap.

    Most callers should use :func:`acquire_turn_slot` instead, which wraps
    the acquire/release lifecycle in a context manager so the slot can't
    leak on a downstream failure.
    """
    try:
        redis = await get_redis_async()
        now = time.time()
        stale_cutoff = now - STALE_TURN_CUTOFF_SECONDS
        result = await redis.eval(  # type: ignore[misc]
            _TRY_ACQUIRE_SCRIPT,
            1,
            _user_key(user_id),
            session_id,
            str(now),
            str(stale_cutoff),
            str(limit),
            str(STALE_TURN_CUTOFF_SECONDS),
        )
    except (RedisError, RedisClusterException, ConnectionError, OSError) as exc:
        logger.warning(
            "try_acquire_turn_slot: Redis unavailable for user=%s; failing open: %s",
            user_id,
            exc,
        )
        return True
    return int(result) == 1


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

    If :meth:`keep` is never called — because acquisition was refused, the
    user_id was empty, or the with-block exits with or without an
    exception before reaching the call — the context manager releases the
    slot itself on ``__aexit__``.
    """

    __slots__ = ("user_id", "session_id", "acquired", "_kept")

    def __init__(self, user_id: str, session_id: str) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.acquired = False
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
    limit: int = MAX_CONCURRENT_TURNS_PER_USER,
) -> AsyncIterator[TurnSlot]:
    """Reserve a turn slot for the duration of the ``async with`` block.

    On entry, raises :class:`ConcurrentTurnLimitError` if the user is at or
    above ``limit`` (caller maps this to HTTP 429). Anonymous sessions
    (``user_id`` falsy) bypass the gate and yield a no-op handle.

    On exit:

    * if the body called :meth:`TurnSlot.keep`, the slot is held — the
      caller has handed off ownership to a downstream completion path.
    * otherwise (clean exit *or* exception), the slot is released
      immediately so failed schedules don't leak a slot until the
      stale-cutoff sweep.
    """
    handle = TurnSlot(user_id or "", session_id)
    if user_id:
        if not await try_acquire_turn_slot(user_id, session_id, limit):
            raise ConcurrentTurnLimitError()
        handle.acquired = True

    try:
        yield handle
    finally:
        if handle.acquired and not handle._kept:
            await release_turn_slot(handle.user_id, handle.session_id)
