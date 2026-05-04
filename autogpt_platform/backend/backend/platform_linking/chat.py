"""Chat-turn orchestration for the platform bot bridge."""

import logging
from typing import NamedTuple
from uuid import uuid4

from backend.copilot import stream_registry
from backend.copilot.executor.utils import enqueue_copilot_turn
from backend.copilot.model import (
    ChatMessage,
    append_and_save_message,
    create_chat_session,
    get_chat_session,
)
from backend.data.db_accessors import platform_linking_db
from backend.util.exceptions import DuplicateChatMessageError, NotFoundError

from .models import BotChatRequest, ChatTurnHandle

logger = logging.getLogger(__name__)

CHAT_TOOL_CALL_ID = "chat_stream"
CHAT_TOOL_NAME = "chat"


class _ChatOwner(NamedTuple):
    user_id: str
    organization_id: str | None
    team_id: str | None


async def _resolve_team_for_org(organization_id: str | None) -> str | None:
    """Derive the default team within *organization_id*.

    Falls back to None when the org has no default team or org is unknown.
    """
    if not organization_id:
        return None
    try:
        from backend.data.db import prisma

        workspace = await prisma.team.find_first(
            where={"orgId": organization_id, "isDefault": True}
        )
        return workspace.id if workspace else None
    except Exception:
        logger.debug("Could not resolve team for org %s", organization_id)
    return None


async def resolve_chat_owner(request: BotChatRequest) -> _ChatOwner:
    """Return the AutoGPT user who owns the conversation, with org/team context.

    Server context → server owner + persisted org from PlatformLink.
    DM context → the DM-linked user, org/team resolved at runtime.
    """
    platform = request.platform.value
    db = platform_linking_db()

    if request.platform_server_id:
        details = await db.find_server_link_details(
            platform, request.platform_server_id
        )
        if details is None:
            raise NotFoundError("This server is not linked to an AutoGPT account.")

        team_id = await _resolve_team_for_org(details.organization_id)
        return _ChatOwner(
            user_id=details.user_id,
            organization_id=details.organization_id,
            team_id=team_id,
        )

    owner = await db.find_user_link_owner(platform, request.platform_user_id)
    if owner is None:
        raise NotFoundError("Your DMs are not linked to an AutoGPT account.")

    # DM context: resolve org/team dynamically
    org_id: str | None = None
    team_id_dm: str | None = None
    try:
        from backend.api.features.orgs.db import get_user_default_team

        org_id, team_id_dm = await get_user_default_team(owner)
    except Exception:
        logger.debug("Could not resolve default team for DM user %s", owner[-8:])

    return _ChatOwner(user_id=owner, organization_id=org_id, team_id=team_id_dm)


async def start_chat_turn(request: BotChatRequest) -> ChatTurnHandle:
    """Prepare a copilot turn; caller subscribes via the returned handle.

    ``subscribe_from="0-0"`` on the handle means a late subscriber replays
    the full stream (Redis Streams, not pub/sub).
    """
    chat_owner = await resolve_chat_owner(request)

    session_id = request.session_id
    if session_id:
        session = await get_chat_session(session_id, chat_owner.user_id)
        if not session:
            raise NotFoundError("Session not found.")
    else:
        session = await create_chat_session(
            chat_owner.user_id,
            dry_run=False,
            organization_id=chat_owner.organization_id,
            team_id=chat_owner.team_id,
        )
        session_id = session.session_id

    # Persist the user message before enqueueing, mirroring the REST chat
    # endpoint — otherwise the executor runs against empty history.
    is_duplicate = (
        await append_and_save_message(
            session_id, ChatMessage(role="user", content=request.message)
        )
    ) is None
    if is_duplicate:
        logger.info(
            "Duplicate bot message for session %s (platform %s, user ...%s)",
            session_id,
            request.platform.value,
            chat_owner.user_id[-8:],
        )
        raise DuplicateChatMessageError("Message already in flight.")

    turn_id = str(uuid4())

    await stream_registry.create_session(
        session_id=session_id,
        user_id=chat_owner.user_id,
        tool_call_id=CHAT_TOOL_CALL_ID,
        tool_name=CHAT_TOOL_NAME,
        turn_id=turn_id,
    )

    await enqueue_copilot_turn(
        session_id=session_id,
        user_id=chat_owner.user_id,
        message=request.message,
        turn_id=turn_id,
        is_user_message=True,
        organization_id=chat_owner.organization_id,
        team_id=chat_owner.team_id,
    )

    logger.info(
        "Bot chat turn started: %s (server %s, session %s, turn %s, "
        "owner ...%s, org %s)",
        request.platform.value,
        request.platform_server_id or "DM",
        session_id,
        turn_id,
        chat_owner.user_id[-8:],
        chat_owner.organization_id or "none",
    )

    return ChatTurnHandle(
        session_id=session_id,
        turn_id=turn_id,
        user_id=chat_owner.user_id,
    )
