"""Per-user FIFO queue for AutoPilot chat turns that exceeded the soft
running cap. SECRT-2339.

Storage is the existing :class:`prisma.models.ChatMessage` table with
a sparse ``queueStatus`` column — a queued task IS the user's message
in the conversation, just one waiting for a running slot. NULL on every
chat-history row (the 99% case); set to one of:

* ``"queued"``    — waiting for the running cap to drop below 5
* ``"blocked"``   — pre-start re-validation failed (paywall lapsed,
                    USD cap hit). Row stays so the user sees *why*
                    instead of having the task vanish.
* ``"cancelled"`` — user dropped it before the dispatcher claimed it.

When the dispatcher promotes a row to running, ``queueStatus`` is
cleared back to NULL — the row becomes a normal chat message.

Layered on top of:

* :mod:`backend.copilot.active_turns` — Redis sorted set per user
  tracking *running* turns (soft cap, default 5).
* :mod:`backend.copilot.executor.utils` — :func:`schedule_chat_turn`
  is the same primitive the HTTP route uses for an immediate dispatch;
  the dispatcher reuses it so queued + immediate dispatches share one
  code path.

Caps are configured via:

* :func:`backend.copilot.active_turns.get_running_turn_limit`   (soft / 5)
* :func:`backend.copilot.active_turns.get_inflight_turn_limit`  (hard / 15)
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from prisma import Json
from prisma.models import ChatMessage
from prisma.types import ChatMessageWhereInput

from backend.copilot.active_turns import count_running_turns
from backend.util.json import SafeJson

logger = logging.getLogger(__name__)


# ChatMessage.queueStatus values. Strings (not an enum) so adding a new
# state is code-only with no Prisma migration.
STATUS_QUEUED = "queued"
STATUS_BLOCKED = "blocked"
STATUS_CANCELLED = "cancelled"


# ============================================================================
# Counts & queries
# ============================================================================


async def count_queued_turns(user_id: str) -> int:
    """Number of ``queueStatus='queued'`` ChatMessage rows for ``user_id``.

    The session→user join is cheap because ``ChatSession.userId`` is
    indexed; the partial index on ``ChatMessage(queueStatus, createdAt)
    WHERE queueStatus IS NOT NULL`` keeps the candidate set tiny.
    """
    return await ChatMessage.prisma().count(
        where={
            "queueStatus": STATUS_QUEUED,
            "Session": {"is": {"userId": user_id}},
        },
    )


async def count_inflight_turns(user_id: str) -> int:
    """Running (Redis) + queued (DB). Hard cap is enforced against this.

    Counts queued first, then running: a concurrent ``queued → running``
    promotion between the two reads can be double-counted (safe — caller
    rejects an extra task) but never missed entirely. The cap may
    therefore briefly read high under burst load, but never low.
    """
    queued = await count_queued_turns(user_id)
    running = await count_running_turns(user_id)
    return queued + running


async def list_queued_turns(user_id: str) -> list[ChatMessage]:
    """User's queued tasks, oldest-first (FIFO order). UX surface for the
    'your queued tasks' panel."""
    return await ChatMessage.prisma().find_many(
        where={
            "queueStatus": STATUS_QUEUED,
            "Session": {"is": {"userId": user_id}},
        },
        order={"createdAt": "asc"},
    )


async def list_blocked_turns(user_id: str) -> list[ChatMessage]:
    """Tasks the dispatcher gave up on (paywall / cap re-check failed).
    UX surface for the 'why didn't this run?' panel."""
    return await ChatMessage.prisma().find_many(
        where={
            "queueStatus": STATUS_BLOCKED,
            "Session": {"is": {"userId": user_id}},
        },
        order={"createdAt": "desc"},
    )


# ============================================================================
# Mutations
# ============================================================================


class InflightCapExceeded(Exception):
    """User's running + queued total has reached the configured hard cap.

    Raised by :func:`try_enqueue_turn` so the route can map to HTTP 429.
    """


async def try_enqueue_turn(
    *,
    user_id: str,
    inflight_cap: int,
    session_id: str,
    message: str,
    message_id: str | None = None,
    is_user_message: bool = True,
    context: Mapping[str, str] | None = None,
    file_ids: list[str] | None = None,
    mode: str | None = None,
    model: str | None = None,
    permissions: Mapping[str, Any] | None = None,
    request_arrival_at: float = 0.0,
) -> ChatMessage:
    """Atomically admit a queued turn against the user's hard cap.

    Optimistic protocol with post-insert recount: a concurrent submit
    that races past the pre-check is detected by the recount and one of
    the in-flight inserts is rolled back. The losing caller sees
    :class:`InflightCapExceeded` and the route maps to HTTP 429.

    The dispatcher race (claim between insert and recount) is handled
    by gating the rollback delete on ``queueStatus='queued'`` — a row
    already claimed by a concurrent dispatcher survives, and that
    caller is admitted (the message is now running).
    """
    if await count_inflight_turns(user_id) >= inflight_cap:
        raise InflightCapExceeded()

    row = await enqueue_turn(
        session_id=session_id,
        message=message,
        message_id=message_id,
        is_user_message=is_user_message,
        context=context,
        file_ids=file_ids,
        mode=mode,
        model=model,
        permissions=permissions,
        request_arrival_at=request_arrival_at,
    )

    if await count_inflight_turns(user_id) > inflight_cap:
        deleted = await ChatMessage.prisma().delete_many(
            where={"id": row.id, "queueStatus": STATUS_QUEUED},
        )
        if deleted > 0:
            from backend.copilot.model import invalidate_session_cache

            await invalidate_session_cache(session_id)
            raise InflightCapExceeded()
    return row


async def enqueue_turn(
    *,
    session_id: str,
    message: str,
    message_id: str | None = None,
    is_user_message: bool = True,
    context: Mapping[str, str] | None = None,
    file_ids: list[str] | None = None,
    mode: str | None = None,
    model: str | None = None,
    permissions: Mapping[str, Any] | None = None,
    request_arrival_at: float = 0.0,
) -> ChatMessage:
    """Persist a user message that couldn't dispatch immediately because
    the user is at the running cap. Caller is responsible for the
    in-flight cap check AND session-ownership check upstream — once the
    row is committed the dispatcher owns it.

    The row is a regular ChatMessage (with ``role='user'``) plus the
    queue lifecycle columns. When the dispatcher claims it the queue
    columns are cleared and the row becomes an ordinary
    chat-conversation message.
    """
    metadata: dict[str, Any] = {}
    if context is not None:
        metadata["context"] = dict(context)
    if file_ids is not None:
        metadata["file_ids"] = list(file_ids)
    if mode is not None:
        metadata["mode"] = mode
    if model is not None:
        metadata["model"] = model
    if permissions is not None:
        metadata["permissions"] = dict(permissions)
    if request_arrival_at:
        metadata["request_arrival_at"] = request_arrival_at

    # The Redis NX session lock serialises with ``append_and_save_message``
    # so two concurrent submits to the same session can't pick the same
    # ``sequence`` and PK-collide on ``(sessionId, sequence)``. The caller's
    # ``get_next_sequence`` was an optimistic read; re-fetch inside the
    # lock so the authoritative value is whatever the lock holder sees now.
    from backend.copilot.db import get_next_sequence
    from backend.copilot.model import _get_session_lock, invalidate_session_cache

    async with _get_session_lock(session_id):
        live_sequence = await get_next_sequence(session_id)
        row = await ChatMessage.prisma().create(
            data={
                "id": message_id or _generate_id(),
                "sessionId": session_id,
                "role": "user" if is_user_message else "assistant",
                "content": message,
                "sequence": live_sequence,
                "queueStatus": STATUS_QUEUED,
                "queueMetadata": SafeJson(metadata) if metadata else None,
            }
        )
    # The chat-session cache holds the message list; invalidate so the
    # next /chat read picks up the queued row (frontend renders a
    # 'Queued' badge based on ``queueStatus``).
    await invalidate_session_cache(session_id)
    return row


async def cancel_queued_turn(*, user_id: str, message_id: str) -> bool:
    """Mark a queued row as cancelled. Returns True iff it was queued
    AND owned by the user (via session). The user-ownership check is
    via the session relation — both guards in a single update so
    cancel/dispatch races resolve in one round trip.

    Invalidates the session cache on success so the frontend stops
    rendering the 'Queued' badge for this message on its next refetch.
    """
    where: ChatMessageWhereInput = {
        "id": message_id,
        "queueStatus": STATUS_QUEUED,
        "Session": {"is": {"userId": user_id}},
    }
    updated = await ChatMessage.prisma().update_many(
        where=where,
        data={"queueStatus": STATUS_CANCELLED},
    )
    if updated > 0:
        from backend.copilot.model import invalidate_session_cache

        row = await ChatMessage.prisma().find_unique(where={"id": message_id})
        if row is not None:
            await invalidate_session_cache(row.sessionId)
        return True
    return False


async def mark_queued_turn_blocked(*, message_id: str, reason: str) -> None:
    """Pre-start re-validation failed at dispatch time; preserve the
    row so the user sees why their queued task didn't run.

    Gated on ``queueStatus='queued'`` so a parallel cancel (user clicked
    the cancel button between the dispatcher's gate check and this call)
    isn't silently overwritten with ``blocked``. Also a no-op if the row
    was already claimed by a concurrent dispatcher.

    Invalidates the session cache on success so the frontend transitions
    from 'Queued' to 'Blocked' on its next refetch.
    """
    updated = await ChatMessage.prisma().update_many(
        where={"id": message_id, "queueStatus": STATUS_QUEUED},
        data={
            "queueStatus": STATUS_BLOCKED,
            "queueBlockedReason": reason,
        },
    )
    if updated > 0:
        from backend.copilot.model import invalidate_session_cache

        row = await ChatMessage.prisma().find_unique(where={"id": message_id})
        if row is not None:
            await invalidate_session_cache(row.sessionId)


async def claim_queued_turn_by_id(message_id: str) -> ChatMessage | None:
    """Atomically claim the specific queued row identified by
    ``message_id`` (clear ``queueStatus`` and stamp ``queueStartedAt``).
    Returns the claimed row, or ``None`` if it was cancelled / blocked /
    already claimed by a concurrent dispatcher between the gate check
    and this call.

    The caller passes the exact ``message_id`` they validated (paywall,
    rate-limit) so a parallel cancel of the validated head doesn't
    silently promote a *different* — unvalidated — queued row.
    """
    claimed = await ChatMessage.prisma().update_many(
        where={"id": message_id, "queueStatus": STATUS_QUEUED},
        data={"queueStatus": None, "queueStartedAt": datetime.now(timezone.utc)},
    )
    if claimed == 0:
        return None
    return await ChatMessage.prisma().find_unique(where={"id": message_id})


async def claim_next_queued_turn(user_id: str) -> ChatMessage | None:
    """Find the user's oldest queued row and claim it atomically.

    Convenience wrapper — finds head then delegates to
    :func:`claim_queued_turn_by_id`. Two concurrent dispatchers see only
    one win the row; the loser sees ``None``.

    Prefer :func:`claim_queued_turn_by_id` directly when the caller has
    already validated a specific row, so a parallel cancel doesn't
    promote a *different*, unvalidated row.
    """
    head = await ChatMessage.prisma().find_first(
        where={
            "queueStatus": STATUS_QUEUED,
            "Session": {"is": {"userId": user_id}},
        },
        order={"createdAt": "asc"},
    )
    if head is None:
        return None

    claimed = await ChatMessage.prisma().update_many(
        where={"id": head.id, "queueStatus": STATUS_QUEUED},
        data={"queueStatus": None, "queueStartedAt": datetime.now(timezone.utc)},
    )
    if claimed == 0:
        # Lost the race; caller should retry the loop or wait for the
        # next slot-free event.
        return None
    return await ChatMessage.prisma().find_unique(where={"id": head.id})


# ============================================================================
# Dispatch
# ============================================================================


async def dispatch_next_for_user(user_id: str) -> bool:
    """Promote at most one queued row for ``user_id`` from queued →
    running. Called when a running turn ends (slot frees) and on a
    routine timer to recover from missed dispatch events.

    Returns ``True`` iff a row was actually promoted.

    Pre-start re-validation runs *before* claiming the row so a
    paywalled user's queue head is marked ``blocked`` (with a reason)
    rather than consuming a running slot for a turn that would
    immediately 402.
    """
    # Local imports to keep the cold-start path light and avoid pulling
    # the rate-limit + executor pipeline into modules that just want
    # queue counts.
    from backend.copilot.active_turns import acquire_turn_slot, get_running_session_ids
    from backend.copilot.config import ChatConfig
    from backend.copilot.executor.utils import dispatch_turn
    from backend.copilot.model import invalidate_session_cache
    from backend.copilot.rate_limit import (
        RateLimitExceeded,
        RateLimitUnavailable,
        check_rate_limit,
        get_global_rate_limits,
        is_user_paywalled,
    )

    head = await ChatMessage.prisma().find_first(
        where={
            "queueStatus": STATUS_QUEUED,
            "Session": {"is": {"userId": user_id}},
        },
        order={"createdAt": "asc"},
    )
    if head is None:
        return False

    # Skip dispatch if the head's session already has a running turn —
    # otherwise ``acquire_turn_slot`` returns ``REFRESHED`` instead of
    # admitting a fresh slot, two turns share a single slot, and the
    # first turn's completion releases the shared slot for both. The
    # next slot-free hook (or routine timer) will retry once that
    # session is idle.
    busy_sessions = await get_running_session_ids(user_id)
    if head.sessionId in busy_sessions:
        logger.debug(
            "dispatch_next_for_user: queued head %s targets busy session %s; "
            "deferring to next slot-free tick",
            head.id,
            head.sessionId,
        )
        return False

    if await is_user_paywalled(user_id):
        await mark_queued_turn_blocked(
            message_id=head.id,
            reason=(
                "Subscription required to run AutoPilot tasks. " "Upgrade to continue."
            ),
        )
        return False

    cfg = ChatConfig()
    try:
        daily_limit, weekly_limit, _ = await get_global_rate_limits(
            user_id,
            cfg.daily_cost_limit_microdollars,
            cfg.weekly_cost_limit_microdollars,
        )
        await check_rate_limit(
            user_id=user_id,
            daily_cost_limit=daily_limit,
            weekly_cost_limit=weekly_limit,
        )
    except RateLimitExceeded as exc:
        await mark_queued_turn_blocked(
            message_id=head.id,
            reason=(
                f"This task is ready to run, but your current usage limit "
                f"has been reached ({exc}). Top up or wait until your "
                "limit resets to continue."
            ),
        )
        return False
    except RateLimitUnavailable:
        logger.warning(
            "dispatch_next_for_user: rate-limit service degraded for user=%s; "
            "leaving queue intact for the next tick",
            user_id,
        )
        return False

    # Claim by the validated head's id, NOT find-then-claim-head: a
    # parallel cancel of ``head`` between validation and claim would
    # otherwise let ``claim_next_queued_turn`` claim a *different* row
    # that never went through paywall / rate-limit re-validation.
    row = await claim_queued_turn_by_id(head.id)
    if row is None:
        # ``head`` was cancelled / blocked / claimed by a concurrent
        # dispatcher. Caller's loop decides whether to retry; the next
        # slot-free event will fire this again anyway.
        return False

    metadata = _decode_metadata(row.queueMetadata)
    turn_id = str(uuid.uuid4())
    try:
        # The user's message is already persisted in ``ChatMessage``
        # from ``enqueue_turn``; the dispatcher must NOT route through
        # ``schedule_chat_turn``, which would re-save the row, hit the
        # PK-collision dedup, return None, and silently drop the
        # dispatch. Acquire the running slot ourselves and go straight
        # to the create-session + enqueue layer.
        async with acquire_turn_slot(user_id, row.sessionId) as slot:
            await dispatch_turn(
                slot,
                session_id=row.sessionId,
                user_id=user_id,
                turn_id=turn_id,
                message=row.content or "",
                is_user_message=row.role == "user",
                context=metadata.get("context"),
                file_ids=metadata.get("file_ids"),
                mode=metadata.get("mode"),
                model=metadata.get("model"),
                permissions=metadata.get("permissions"),
                request_arrival_at=float(metadata.get("request_arrival_at") or 0.0),
            )
    except Exception:
        # Roll the claim back so a missed-dispatch tick or the next
        # slot-free event can retry. We re-set queueStatus rather than
        # leaving the row half-promoted with stale metadata.
        await ChatMessage.prisma().update_many(
            where={"id": row.id, "queueStatus": None},
            data={"queueStatus": STATUS_QUEUED, "queueStartedAt": None},
        )
        raise
    # The promoted row's queue columns were cleared in
    # ``claim_next_queued_turn``; refresh the chat session cache so the
    # frontend stops rendering the 'Queued' badge for this message.
    await invalidate_session_cache(row.sessionId)
    return True


# ============================================================================
# Helpers
# ============================================================================


def _decode_metadata(raw: Json | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _generate_id() -> str:
    """Match :func:`backend.copilot.model.append_and_save_message`'s id
    generation — the column is the same primary key the chat history
    uses, so the same source of uniqueness applies."""
    return str(uuid.uuid4())
