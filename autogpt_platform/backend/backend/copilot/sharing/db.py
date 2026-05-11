"""Data layer for chat session sharing.

Mirrors :mod:`backend.data.execution`'s share helpers but for
:class:`prisma.models.ChatSession`.  Sharing a chat also opts-in a
caller-selected set of :class:`AgentGraphExecution` rows so the public
viewer can drill into the underlying agent run; the cascade rules are
encoded here (see :func:`disable_chat_session_share`).
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from prisma.enums import SharedVia
from prisma.errors import ForeignKeyViolationError, UniqueViolationError
from prisma.models import AgentGraph, AgentGraphExecution, ChatLinkedShare
from prisma.models import ChatMessage as PrismaChatMessage
from prisma.models import ChatSession as PrismaChatSession
from prisma.models import SharedChatFile, UserWorkspace, UserWorkspaceFile

from backend.copilot.db import get_chat_messages_paginated
from backend.copilot.model import ChatSessionInfo
from backend.copilot.sharing.models import (
    SharedChatLinkedExecution,
    SharedChatMessagesPage,
    SharedChatSession,
    sanitize_chat_message,
    sanitize_chat_session,
)
from backend.data.sharing.tokens import generate_share_token
from backend.data.sharing.workspace_refs import extract_workspace_file_ids

logger = logging.getLogger(__name__)

# Linked-execution discovery scans assistant tool responses (role="tool"
# rows) for ``ExecutionStartedResponse`` payloads.  The shape is defined
# by :class:`backend.copilot.tools.models.ExecutionStartedResponse`.
_EXECUTION_STARTED_TYPE = "execution_started"


# ---------- Enable / disable -------------------------------------------------


async def enable_chat_session_share(
    session_id: str,
    user_id: str,
    linked_execution_ids: list[str],
) -> str:
    """Enable sharing on a chat session.

    Generates a fresh token, refreshes the file allowlist from the
    session's messages, and opts the caller-selected
    ``linked_execution_ids`` in to the share.  Linked executions that
    are already independently shared keep their existing token; only
    not-yet-shared executions get a new ``CHAT_LINK`` share.

    Returns the share token.

    Raises ValueError if the session does not belong to *user_id* or if
    any ``linked_execution_ids`` is not owned by *user_id*.
    """
    session = await PrismaChatSession.prisma().find_first(
        where={"id": session_id, "userId": user_id},
    )
    if not session:
        raise ValueError(f"Chat session {session_id} not found for user")

    requested_links = await _validate_owned_executions(
        execution_ids=linked_execution_ids, user_id=user_id
    )

    share_token = generate_share_token()
    now = datetime.now(UTC)

    # Clear stale allowlist + linkage rows before re-keying the token,
    # so an old token + new linkage never coexist.
    await SharedChatFile.prisma().delete_many(where={"sessionId": session_id})
    await ChatLinkedShare.prisma().delete_many(where={"sessionId": session_id})

    await PrismaChatSession.prisma().update(
        where={"id": session_id},
        data={
            "isShared": True,
            "shareToken": share_token,
            "sharedAt": now,
        },
    )

    await _build_shared_chat_files(
        session_id=session_id, share_token=share_token, user_id=user_id
    )

    await _link_executions_to_share(
        session_id=session_id,
        executions=requested_links,
        shared_at=now,
    )

    return share_token


async def disable_chat_session_share(session_id: str, user_id: str) -> None:
    """Revoke sharing on a chat session and cascade-revoke linked executions.

    Cascade rule: only executions whose ``sharedVia == CHAT_LINK`` get
    their share state cleared.  User-initiated execution shares survive
    chat-share revocation even when they were also opted-in here.
    """
    session = await PrismaChatSession.prisma().find_first(
        where={"id": session_id, "userId": user_id},
    )
    if not session:
        raise ValueError(f"Chat session {session_id} not found for user")

    # Walk the linkage table and disable only the chat-derived shares.
    linked = await ChatLinkedShare.prisma().find_many(
        where={"sessionId": session_id},
        include={"Execution": True},
    )
    for row in linked:
        execution = row.Execution
        if execution is None or execution.sharedVia != SharedVia.CHAT_LINK:
            continue
        await AgentGraphExecution.prisma().update(
            where={"id": execution.id},
            data={
                "isShared": False,
                "shareToken": None,
                "sharedAt": None,
                "sharedVia": None,
            },
        )

    await ChatLinkedShare.prisma().delete_many(where={"sessionId": session_id})
    await SharedChatFile.prisma().delete_many(where={"sessionId": session_id})

    await PrismaChatSession.prisma().update(
        where={"id": session_id},
        data={
            "isShared": False,
            "shareToken": None,
            "sharedAt": None,
        },
    )


# ---------- Public read path -------------------------------------------------


async def get_chat_session_by_share_token(
    share_token: str,
) -> SharedChatSession | None:
    """Look up a shared session header (no messages, no auth)."""
    session = await PrismaChatSession.prisma().find_first(
        where={"shareToken": share_token, "isShared": True},
    )
    if not session:
        return None

    linked = await _resolve_linked_executions(session_id=session.id)
    return sanitize_chat_session(
        ChatSessionInfo.from_db(session), linked_executions=linked
    )


async def get_shared_chat_messages_paginated(
    share_token: str,
    *,
    limit: int = 50,
    before_sequence: int | None = None,
) -> SharedChatMessagesPage | None:
    """Paginate messages for a shared session.

    Returns ``None`` when the token is unknown or sharing has been
    disabled (uniform with :func:`get_chat_session_by_share_token`).
    """
    session = await PrismaChatSession.prisma().find_first(
        where={"shareToken": share_token, "isShared": True},
    )
    if not session:
        return None

    page = await get_chat_messages_paginated(
        session_id=session.id,
        limit=limit,
        before_sequence=before_sequence,
        # No user scoping — the share token already authorises the read.
    )
    if page is None:
        return None

    return SharedChatMessagesPage(
        messages=[sanitize_chat_message(m) for m in page.messages],
        has_more=page.has_more,
        oldest_sequence=page.oldest_sequence,
    )


async def get_shared_chat_file(share_token: str, file_id: str) -> str | None:
    """Allowlist lookup for the public file-download endpoint.

    Returns the owning ``session_id`` if the file is allowlisted,
    ``None`` otherwise.  Uniform None for every failure mode prevents
    timing-based enumeration of valid file IDs.
    """
    record = await SharedChatFile.prisma().find_first(
        where={"shareToken": share_token, "fileId": file_id}
    )
    return record.sessionId if record else None


# ---------- Linked execution discovery (share-modal helper) ------------------


@dataclass
class ChatShareState:
    is_shared: bool
    share_token: str | None


async def get_chat_share_state(session_id: str, user_id: str) -> ChatShareState:
    """Return the current share state for *session_id* scoped to *user_id*.

    Returns ``ChatShareState(is_shared=False, share_token=None)`` when the
    session is missing or not owned by the user — the same shape callers
    see for never-shared sessions, so the share modal renders the
    "enable" path without an extra existence check.
    """
    row = await PrismaChatSession.prisma().find_first(
        where={"id": session_id, "userId": user_id},
    )
    if not row:
        return ChatShareState(is_shared=False, share_token=None)
    return ChatShareState(is_shared=row.isShared, share_token=row.shareToken)


async def find_linked_executions_in_session(
    session_id: str, user_id: str
) -> list[SharedChatLinkedExecution]:
    """Return executions referenced by the chat's tool responses.

    Used by the share modal to prompt the owner "this chat ran 3
    agents — include them in the share?".  Only executions still owned
    by *user_id* are returned (so deleted / cross-user runs don't leak).
    Each entry's ``share_token`` reflects the execution's CURRENT share
    state, which the UI uses to disambiguate "already shared
    independently" from "not yet shared at all".
    """
    session = await PrismaChatSession.prisma().find_first(
        where={"id": session_id, "userId": user_id},
    )
    if not session:
        return []

    execution_ids = await _collect_execution_ids_from_messages(session_id=session_id)
    if not execution_ids:
        return []

    executions = await AgentGraphExecution.prisma().find_many(
        where={
            "id": {"in": list(execution_ids)},
            "userId": user_id,
            "isDeleted": False,
        },
        include={"AgentGraph": True},
    )

    return [
        SharedChatLinkedExecution(
            execution_id=e.id,
            graph_id=e.agentGraphId,
            graph_name=_graph_name(e.AgentGraph),
            share_token=e.shareToken if e.isShared else None,
        )
        for e in executions
    ]


# ---------- Helpers ----------------------------------------------------------


async def _validate_owned_executions(
    execution_ids: list[str], user_id: str
) -> list[AgentGraphExecution]:
    if not execution_ids:
        return []
    rows = await AgentGraphExecution.prisma().find_many(
        where={
            "id": {"in": execution_ids},
            "userId": user_id,
            "isDeleted": False,
        },
    )
    found_ids = {r.id for r in rows}
    missing = set(execution_ids) - found_ids
    if missing:
        raise ValueError(
            f"Executions not found or not owned by user: {sorted(missing)}"
        )
    return rows


async def _build_shared_chat_files(
    session_id: str, share_token: str, user_id: str
) -> int:
    """Scan all messages in the session for workspace://<id> refs + persist allowlist."""
    rows = await PrismaChatMessage.prisma().find_many(
        where={"sessionId": session_id},
    )

    file_ids: set[str] = set()
    for row in rows:
        if row.content is not None:
            file_ids |= extract_workspace_file_ids(row.content)
        if row.toolCalls is not None:
            file_ids |= extract_workspace_file_ids(_json_or_none(row.toolCalls))
        if row.functionCall is not None:
            file_ids |= extract_workspace_file_ids(_json_or_none(row.functionCall))

    if not file_ids:
        return 0

    workspace = await UserWorkspace.prisma().find_unique(where={"userId": user_id})
    if not workspace:
        return 0

    owned_files = await UserWorkspaceFile.prisma().find_many(
        where={
            "id": {"in": list(file_ids)},
            "workspaceId": workspace.id,
            "isDeleted": False,
        }
    )

    created = 0
    for file in owned_files:
        try:
            await SharedChatFile.prisma().create(
                data={
                    "sessionId": session_id,
                    "fileId": file.id,
                    "shareToken": share_token,
                }
            )
            created += 1
        except UniqueViolationError:
            logger.debug("SharedChatFile already exists: %s/%s", session_id, file.id)
        except ForeignKeyViolationError:
            logger.debug("SharedChatFile FK violation for file %s", file.id)
    return created


async def _link_executions_to_share(
    session_id: str,
    executions: list[AgentGraphExecution],
    shared_at: datetime,
) -> None:
    """Insert ChatLinkedShare rows and cascade-enable share on unshared ones."""
    for execution in executions:
        await ChatLinkedShare.prisma().create(
            data={"sessionId": session_id, "executionId": execution.id}
        )
        if execution.isShared:
            # Already independently shared — keep its token and provenance.
            continue
        await AgentGraphExecution.prisma().update(
            where={"id": execution.id},
            data={
                "isShared": True,
                "shareToken": generate_share_token(),
                "sharedAt": shared_at,
                "sharedVia": SharedVia.CHAT_LINK,
            },
        )


async def _resolve_linked_executions(
    session_id: str,
) -> list[SharedChatLinkedExecution]:
    rows = await ChatLinkedShare.prisma().find_many(
        where={"sessionId": session_id},
        include={"Execution": {"include": {"AgentGraph": True}}},
    )
    out: list[SharedChatLinkedExecution] = []
    for row in rows:
        execution = row.Execution
        if execution is None:
            continue
        out.append(
            SharedChatLinkedExecution(
                execution_id=execution.id,
                graph_id=execution.agentGraphId,
                graph_name=_graph_name(execution.AgentGraph),
                share_token=execution.shareToken if execution.isShared else None,
            )
        )
    return out


async def _collect_execution_ids_from_messages(session_id: str) -> set[str]:
    """Find execution IDs referenced by ``role=tool`` responses in a session."""
    rows = await PrismaChatMessage.prisma().find_many(
        where={"sessionId": session_id, "role": "tool"},
    )
    execution_ids: set[str] = set()
    for row in rows:
        if not row.content:
            continue
        payload = _json_or_none(row.content)
        if not isinstance(payload, dict):
            continue
        if payload.get("type") != _EXECUTION_STARTED_TYPE:
            continue
        execution_id = payload.get("execution_id")
        if isinstance(execution_id, str) and execution_id:
            execution_ids.add(execution_id)
    return execution_ids


def _graph_name(graph: "AgentGraph | None") -> str | None:
    if graph is None:
        return None
    return graph.name


def _json_or_none(value: Any) -> Any:
    """Parse *value* as JSON if it's a string; else return as-is."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return None
    return value
