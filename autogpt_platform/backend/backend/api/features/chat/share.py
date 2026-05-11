"""HTTP routes for sharing chat sessions.

Three groups of routes:

- **Owner-only** — list linked-execution candidates, enable share with
  opt-ins, disable share.  Mirrors the ``/graphs/.../share`` shape on
  :mod:`backend.api.features.v1` for execution sharing.
- **Public-by-token** — the public viewer reads through these
  unauthenticated routes; the share token is the bearer credential.
  Same enumeration defenses as execution sharing (strict UUID path
  validation, allowlist lookup, uniform 404).
"""

import logging
from typing import Annotated

from autogpt_libs import auth
from fastapi import APIRouter, HTTPException, Path, Response, Security
from pydantic import BaseModel
from starlette.status import HTTP_204_NO_CONTENT

from backend.api.features.workspace.routes import create_file_download_response
from backend.copilot.sharing import db as share_db
from backend.copilot.sharing.models import (
    SharedChatLinkedExecution,
    SharedChatMessagesPage,
    SharedChatSession,
)
from backend.data.sharing.tokens import SHARE_TOKEN_PATTERN
from backend.data.workspace import get_workspace_file_by_id
from backend.util.feature_flag import Flag, is_feature_enabled
from backend.util.settings import Settings

logger = logging.getLogger(__name__)
settings = Settings()


# --------------------------------------------------------------------------
# Owner-only routes — mounted at the chat router's prefix (``/api/chat``).
# --------------------------------------------------------------------------

owner_router = APIRouter(tags=["chat", "share"])


class LinkedExecutionsResponse(BaseModel):
    """Listing returned to the share modal so the owner can opt-in.

    Also surfaces the chat's current share state so the modal can open
    in the right mode (share-vs-revoke) without an extra round-trip.
    """

    linked_executions: list[SharedChatLinkedExecution]
    is_shared: bool = False
    share_token: str | None = None


class EnableShareRequest(BaseModel):
    """Per-execution opt-in choices made in the share modal.

    Empty list = share the chat without exposing any underlying agent
    runs (viewer sees the inline tool-call snapshots, no drill-in).
    """

    linked_execution_ids: list[str] = []


class ShareResponse(BaseModel):
    share_url: str
    share_token: str


@owner_router.get("/sessions/{session_id}/share/linked-executions")
async def list_linked_executions(
    session_id: Annotated[str, Path],
    user_id: Annotated[str, Security(auth.get_user_id)],
) -> LinkedExecutionsResponse:
    """List executions referenced by this chat's tool responses.

    The share modal renders these as a checklist so the owner explicitly
    consents to exposing each underlying agent run.  Already-shared
    executions still appear so the owner sees the full picture.
    """
    state = await share_db.get_chat_share_state(session_id=session_id, user_id=user_id)
    linked = await share_db.find_linked_executions_in_session(
        session_id=session_id, user_id=user_id
    )
    return LinkedExecutionsResponse(
        linked_executions=linked,
        is_shared=state.is_shared,
        share_token=state.share_token,
    )


@owner_router.post("/sessions/{session_id}/share")
async def enable_chat_sharing(
    session_id: Annotated[str, Path],
    user_id: Annotated[str, Security(auth.get_user_id)],
    body: EnableShareRequest = EnableShareRequest(),
) -> ShareResponse:
    """Enable sharing for a chat session.

    Flag-gated: refuses with 403 when ``chat-sharing`` is off so a stale
    frontend cannot enable shares post-rollback.
    """
    if not await is_feature_enabled(Flag.CHAT_SHARING, user_id):
        raise HTTPException(status_code=403, detail="Chat sharing is not enabled")

    try:
        share_token = await share_db.enable_chat_session_share(
            session_id=session_id,
            user_id=user_id,
            linked_execution_ids=body.linked_execution_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    base_url = settings.config.frontend_base_url or "http://localhost:3000"
    return ShareResponse(
        share_url=f"{base_url}/share/chat/{share_token}",
        share_token=share_token,
    )


@owner_router.delete(
    "/sessions/{session_id}/share",
    status_code=HTTP_204_NO_CONTENT,
)
async def disable_chat_sharing(
    session_id: Annotated[str, Path],
    user_id: Annotated[str, Security(auth.get_user_id)],
) -> None:
    """Revoke sharing for a chat session.

    Cascade-revokes linked executions whose share originated here
    (``sharedVia == CHAT_LINK``).  User-initiated execution shares
    survive — see :func:`backend.copilot.sharing.db.disable_chat_session_share`.
    """
    try:
        await share_db.disable_chat_session_share(
            session_id=session_id, user_id=user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# --------------------------------------------------------------------------
# Public routes — mounted at ``/api/public/shared/chats``.
# Stays on even when ``chat-sharing`` flag is off so revoked-then-fixed
# rollbacks don't break already-shared URLs mid-flight.
# --------------------------------------------------------------------------

public_router = APIRouter(tags=["chat", "share", "public"])


@public_router.get("/{share_token}")
async def get_shared_chat(
    share_token: Annotated[str, Path(pattern=SHARE_TOKEN_PATTERN)],
) -> SharedChatSession:
    session = await share_db.get_chat_session_by_share_token(share_token)
    if not session:
        raise HTTPException(status_code=404, detail="Shared chat not found")
    return session


@public_router.get("/{share_token}/messages")
async def get_shared_chat_messages(
    share_token: Annotated[str, Path(pattern=SHARE_TOKEN_PATTERN)],
    limit: int = 50,
    before_sequence: int | None = None,
) -> SharedChatMessagesPage:
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=400, detail="limit must be in [1, 200]")
    page = await share_db.get_shared_chat_messages_paginated(
        share_token, limit=limit, before_sequence=before_sequence
    )
    if page is None:
        raise HTTPException(status_code=404, detail="Shared chat not found")
    return page


@public_router.get(
    "/{share_token}/files/{file_id}/download",
    summary="Download a file from a shared chat",
    operation_id="download_shared_chat_file",
)
async def download_shared_chat_file(
    share_token: Annotated[str, Path(pattern=SHARE_TOKEN_PATTERN)],
    file_id: Annotated[str, Path(pattern=SHARE_TOKEN_PATTERN)],
) -> Response:
    """Download a workspace file allowlisted by a shared chat (no auth).

    Returns uniform 404 for every failure mode to prevent enumeration —
    indistinguishable from "unknown token" or "wrong file id".
    """
    session_id = await share_db.get_shared_chat_file(share_token, file_id)
    if not session_id:
        raise HTTPException(status_code=404, detail="Not found")
    file = await get_workspace_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="Not found")
    return await create_file_download_response(file, inline=True)
