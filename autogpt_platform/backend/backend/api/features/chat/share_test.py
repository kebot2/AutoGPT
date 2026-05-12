"""Route tests for chat-share endpoints (no DB)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response

from backend.api.features.chat.share import owner_router, public_router
from backend.copilot.sharing.models import (
    SharedChatLinkedExecution,
    SharedChatMessage,
    SharedChatMessagesPage,
    SharedChatSession,
)
from backend.data.workspace import WorkspaceFile

app = FastAPI()
app.include_router(owner_router, prefix="/api/chat")
app.include_router(public_router, prefix="/api/public/shared/chats")

VALID_TOKEN = "550e8400-e29b-41d4-a716-446655440000"
VALID_FILE_ID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
SESSION_ID = "cffea7e0-1111-2222-3333-444444444444"


def _make_workspace_file(**overrides) -> WorkspaceFile:
    defaults = {
        "id": VALID_FILE_ID,
        "workspace_id": "ws-001",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "name": "image.png",
        "path": "/image.png",
        "storage_path": "local://uploads/image.png",
        "mime_type": "image/png",
        "size_bytes": 4,
        "checksum": None,
        "is_deleted": False,
        "deleted_at": None,
        "metadata": {},
    }
    defaults.update(overrides)
    return WorkspaceFile(**defaults)


def _mock_download_response():
    async def _handler(file, *, inline=False):
        return Response(content=b"\x89PNG", media_type="image/png")

    return _handler


@pytest.fixture(autouse=True)
def setup_app_auth(mock_jwt_user):
    """Bypass JWT auth for owner-router tests."""
    from autogpt_libs.auth.jwt_utils import get_jwt_payload

    app.dependency_overrides[get_jwt_payload] = mock_jwt_user["get_jwt_payload"]
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_frontend_base_url():
    """Pin ``settings.config.frontend_base_url`` for the share-enable path.

    The route fails fast with 500 when this is unset (it'd otherwise
    return a broken localhost URL in production).  CI runs without the
    setting populated, so the route tests need an explicit value.
    """
    from backend.api.features.chat import share as share_module

    original = share_module.settings.config.frontend_base_url
    share_module.settings.config.frontend_base_url = "http://localhost:3000"
    yield
    share_module.settings.config.frontend_base_url = original


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestEnableChatSharing:
    def test_enables_share_when_flag_on(self, client):
        with (
            patch(
                "backend.api.features.chat.share.is_feature_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "backend.api.features.chat.share.share_db.enable_chat_session_share",
                new_callable=AsyncMock,
                return_value=VALID_TOKEN,
            ),
        ):
            response = client.post(
                f"/api/chat/sessions/{SESSION_ID}/share",
                json={"linked_execution_ids": []},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["share_token"] == VALID_TOKEN
        assert body["share_url"].endswith(f"/share/chat/{VALID_TOKEN}")

    def test_flag_off_refuses_with_403(self, client):
        with patch(
            "backend.api.features.chat.share.is_feature_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = client.post(
                f"/api/chat/sessions/{SESSION_ID}/share",
                json={"linked_execution_ids": []},
            )
        assert response.status_code == 403

    def test_missing_session_returns_404(self, client):
        with (
            patch(
                "backend.api.features.chat.share.is_feature_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "backend.api.features.chat.share.share_db.enable_chat_session_share",
                new_callable=AsyncMock,
                side_effect=ValueError("Chat session not found"),
            ),
        ):
            response = client.post(
                f"/api/chat/sessions/{SESSION_ID}/share",
                json={"linked_execution_ids": ["bad-exec-id"]},
            )
        assert response.status_code == 404


class TestDisableChatSharing:
    def test_disables_share(self, client):
        with patch(
            "backend.api.features.chat.share.share_db.disable_chat_session_share",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.delete(f"/api/chat/sessions/{SESSION_ID}/share")
        assert response.status_code == 204

    def test_unknown_session_returns_404(self, client):
        with patch(
            "backend.api.features.chat.share.share_db.disable_chat_session_share",
            new_callable=AsyncMock,
            side_effect=ValueError("Chat session not found"),
        ):
            response = client.delete(f"/api/chat/sessions/{SESSION_ID}/share")
        assert response.status_code == 404


class TestListLinkedExecutions:
    def test_returns_listing(self, client):
        from backend.copilot.sharing.db import ChatShareState

        listing = [
            SharedChatLinkedExecution(
                execution_id="exec-1",
                graph_id="graph-1",
                graph_name="My Agent",
                share_token=None,
            ),
        ]
        with (
            patch(
                "backend.api.features.chat.share.share_db.get_chat_share_state",
                new_callable=AsyncMock,
                return_value=ChatShareState(is_shared=False, share_token=None),
            ),
            patch(
                "backend.api.features.chat.share.share_db.find_linked_executions_in_session",
                new_callable=AsyncMock,
                return_value=listing,
            ),
        ):
            response = client.get(
                f"/api/chat/sessions/{SESSION_ID}/share/linked-executions"
            )
        assert response.status_code == 200
        assert response.json()["linked_executions"][0]["execution_id"] == "exec-1"


class TestPublicChatRead:
    def test_unknown_token_returns_404(self, client):
        with patch(
            "backend.api.features.chat.share.share_db.get_chat_session_by_share_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get(f"/api/public/shared/chats/{VALID_TOKEN}")
        assert response.status_code == 404

    def test_returns_sanitized_session(self, client):
        sess = SharedChatSession(
            id=SESSION_ID,
            title="hello",
            created_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
            linked_executions=[],
        )
        with patch(
            "backend.api.features.chat.share.share_db.get_chat_session_by_share_token",
            new_callable=AsyncMock,
            return_value=sess,
        ):
            response = client.get(f"/api/public/shared/chats/{VALID_TOKEN}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == SESSION_ID
        # Critical: credentials/metadata must NOT appear in the public payload.
        assert "credentials" not in body
        assert "metadata" not in body

    def test_messages_endpoint_returns_page(self, client):
        page = SharedChatMessagesPage(
            messages=[
                SharedChatMessage(
                    id="m1",
                    role="assistant",
                    content="hi",
                    name=None,
                    tool_call_id=None,
                    tool_calls=None,
                    function_call=None,
                    sequence=1,
                    created_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
                )
            ],
            has_more=False,
            oldest_sequence=1,
        )
        with patch(
            "backend.api.features.chat.share.share_db.get_shared_chat_messages_paginated",
            new_callable=AsyncMock,
            return_value=page,
        ):
            response = client.get(f"/api/public/shared/chats/{VALID_TOKEN}/messages")
        assert response.status_code == 200
        assert response.json()["messages"][0]["content"] == "hi"

    def test_malformed_token_rejected_by_path_pattern(self, client):
        response = client.get("/api/public/shared/chats/not-a-uuid")
        assert response.status_code == 422


class TestDownloadSharedChatFile:
    def test_uniform_404_for_unknown_token(self, client):
        with patch(
            "backend.api.features.chat.share.share_db.get_shared_chat_file",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = client.get(
                f"/api/public/shared/chats/{VALID_TOKEN}/files/{VALID_FILE_ID}/download"
            )
        assert response.status_code == 404

    def test_valid_token_returns_inline(self, client):
        with (
            patch(
                "backend.api.features.chat.share.share_db.get_shared_chat_file",
                new_callable=AsyncMock,
                return_value=SESSION_ID,
            ),
            patch(
                "backend.api.features.chat.share.get_workspace_file_by_id",
                new_callable=AsyncMock,
                return_value=_make_workspace_file(),
            ),
            patch(
                "backend.api.features.chat.share.create_file_download_response",
                side_effect=_mock_download_response(),
            ),
        ):
            response = client.get(
                f"/api/public/shared/chats/{VALID_TOKEN}/files/{VALID_FILE_ID}/download"
            )
        assert response.status_code == 200
        assert response.content == b"\x89PNG"
