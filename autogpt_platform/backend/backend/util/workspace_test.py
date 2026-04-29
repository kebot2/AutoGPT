"""
Tests for WorkspaceManager.write_file UniqueViolationError handling.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prisma.errors import UniqueViolationError

from backend.data.workspace import WorkspaceFile
from backend.util.workspace import WorkspaceManager

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_workspace_file(
    id: str = "existing-file-id",
    workspace_id: str = "ws-123",
    name: str = "test.txt",
    path: str = "/test.txt",
    storage_path: str = "ws-123/existing-uuid/test.txt",
    mime_type: str = "text/plain",
    size_bytes: int = 5,
    checksum: str = "abc123",
) -> WorkspaceFile:
    """Create a mock WorkspaceFile with sensible defaults."""
    return WorkspaceFile(
        id=id,
        workspace_id=workspace_id,
        name=name,
        path=path,
        storage_path=storage_path,
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum=checksum,
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )


def _unique_violation() -> UniqueViolationError:
    """Create a UniqueViolationError for testing."""
    data = {
        "user_facing_error": {
            "message": "Unique constraint failed on the fields: (`path`)",
        }
    }
    return UniqueViolationError(data)


@pytest.fixture
def manager():
    return WorkspaceManager(user_id="user-123", workspace_id="ws-123")


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.store.return_value = "ws-123/some-uuid/test.txt"
    storage.delete = AsyncMock()
    return storage


@pytest.fixture
def mock_db():
    """Create a mock workspace_db() return value."""
    db = MagicMock()
    db.create_workspace_file = AsyncMock()
    db.get_workspace_file_by_path = AsyncMock()
    db.get_workspace_file = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_write_file_no_overwrite_unique_violation_raises_and_cleans_up(
    manager, mock_storage, mock_db
):
    """overwrite=False + UniqueViolationError → ValueError + storage cleanup."""
    mock_db.get_workspace_file_by_path.return_value = None
    mock_db.create_workspace_file.side_effect = _unique_violation()

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=250 * 1024 * 1024,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=0),
    ):
        with pytest.raises(ValueError, match="File already exists"):
            await manager.write_file(
                filename="test.txt", content=b"hello", overwrite=False
            )

    mock_storage.delete.assert_called_once()


@pytest.mark.asyncio
async def test_write_file_overwrite_conflict_then_retry_succeeds(
    manager, mock_storage, mock_db
):
    """overwrite=True + conflict → delete existing → retry succeeds."""
    created_file = _make_workspace_file()
    existing_file = _make_workspace_file(id="old-id")

    mock_db.get_workspace_file_by_path.return_value = existing_file
    mock_db.create_workspace_file.side_effect = [_unique_violation(), created_file]

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=250 * 1024 * 1024,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=0),
        patch.object(manager, "delete_file", new_callable=AsyncMock) as mock_delete,
    ):
        result = await manager.write_file(
            filename="test.txt", content=b"hello", overwrite=True
        )

    assert result == created_file
    mock_delete.assert_called_once_with("old-id")
    mock_storage.delete.assert_not_called()


@pytest.mark.asyncio
async def test_write_file_overwrite_exhausted_retries_raises_and_cleans_up(
    manager, mock_storage, mock_db
):
    """overwrite=True + all retries exhausted → ValueError + cleanup."""
    existing_file = _make_workspace_file(id="old-id")

    mock_db.get_workspace_file_by_path.return_value = existing_file
    # Initial + 2 retries = 3 UniqueViolationErrors
    mock_db.create_workspace_file.side_effect = [
        _unique_violation(),
        _unique_violation(),
        _unique_violation(),
    ]

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=250 * 1024 * 1024,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=0),
        patch.object(manager, "delete_file", new_callable=AsyncMock),
    ):
        with pytest.raises(ValueError, match="Unable to overwrite.*concurrent write"):
            await manager.write_file(
                filename="test.txt", content=b"hello", overwrite=True
            )

    mock_storage.delete.assert_called_once()


@pytest.mark.asyncio
async def test_write_file_quota_exceeded_raises_value_error(
    manager, mock_storage, mock_db
):
    """write_file raises ValueError when workspace storage quota is exceeded."""
    mock_db.get_workspace_file_by_path.return_value = None

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch(
            "backend.util.workspace.scan_content_safe", new_callable=AsyncMock
        ) as mock_scan,
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=250 * 1024 * 1024,  # 250 MB limit
        ),
        patch(
            "backend.util.workspace.get_workspace_total_size",
            return_value=250 * 1024 * 1024,  # already at limit
        ),
    ):
        with pytest.raises(ValueError, match="Storage limit exceeded"):
            await manager.write_file(filename="test.txt", content=b"hello")

    # Quota rejection should short-circuit before expensive virus scan
    mock_scan.assert_not_called()
    # Storage should NOT have been written to
    mock_storage.store.assert_not_called()


@pytest.mark.asyncio
async def test_write_file_rejects_upload_when_usage_already_exceeds_downgraded_limit(
    manager, mock_storage, mock_db
):
    """Downgrading below current usage should block further uploads until usage drops."""
    mock_db.get_workspace_file_by_path.return_value = None

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch(
            "backend.util.workspace.scan_content_safe", new_callable=AsyncMock
        ) as mock_scan,
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=250 * 1024 * 1024,
        ),
        patch(
            "backend.util.workspace.get_workspace_total_size",
            return_value=300 * 1024 * 1024,
        ),
    ):
        with pytest.raises(ValueError, match="Storage limit exceeded"):
            await manager.write_file(filename="test.txt", content=b"hello")

    mock_scan.assert_not_called()
    mock_storage.store.assert_not_called()


@pytest.mark.asyncio
async def test_write_file_80pct_warning_logged(manager, mock_storage, mock_db, caplog):
    """write_file logs a warning when workspace usage crosses 80%."""
    created_file = _make_workspace_file()
    mock_db.get_workspace_file_by_path.return_value = None
    mock_db.create_workspace_file.return_value = created_file

    limit_bytes = 100  # 100 bytes total limit
    current_usage = 75  # 75 bytes used → 75% before write
    content = b"123456"  # 6 bytes → 81% after write

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=limit_bytes,
        ),
        patch(
            "backend.util.workspace.get_workspace_total_size",
            return_value=current_usage,
        ),
    ):
        import logging

        with caplog.at_level(logging.WARNING, logger="backend.util.workspace"):
            await manager.write_file(filename="test.txt", content=content)

    assert any("workspace storage at" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_write_file_overwrite_not_double_counted(manager, mock_storage, mock_db):
    """Overwriting a file subtracts the old file size from usage check."""
    existing_file = _make_workspace_file(size_bytes=50)
    created_file = _make_workspace_file()
    mock_db.get_workspace_file_by_path.return_value = existing_file
    mock_db.create_workspace_file.return_value = created_file

    limit_bytes = 100
    current_usage = 90  # 90 bytes used, 50 of which is the file being replaced
    content = b"x" * 50  # replacing with same-size file — should succeed

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=limit_bytes,
        ),
        patch(
            "backend.util.workspace.get_workspace_total_size",
            return_value=current_usage,
        ),
    ):
        # Should NOT raise — net usage after overwrite is 90 - 50 + 50 = 90, under 100
        result = await manager.write_file(
            filename="test.txt", content=content, overwrite=True
        )
    assert result == created_file


@pytest.mark.asyncio
async def test_write_file_zero_limit_bypasses_quota_check(
    manager, mock_storage, mock_db
):
    """When limit is 0 (internal sentinel, not reachable via LD), quota is skipped."""
    created_file = _make_workspace_file()
    mock_db.get_workspace_file_by_path.return_value = None
    mock_db.create_workspace_file.return_value = created_file

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=0,  # Zero limit → uncapped
        ),
        patch(
            "backend.util.workspace.get_workspace_total_size",
            return_value=999_999_999,  # Huge existing usage
        ),
    ):
        # Should NOT raise — zero limit means no enforcement
        result = await manager.write_file(filename="big.txt", content=b"data")
    assert result == created_file


@pytest.mark.asyncio
async def test_write_file_exactly_at_limit_is_rejected(manager, mock_storage, mock_db):
    """Writing a file that puts usage at exactly the limit should be rejected
    because projected_usage > storage_limit (not >=)."""
    mock_db.get_workspace_file_by_path.return_value = None

    limit = 100
    current = 95
    content = b"x" * 6  # 95 + 6 = 101 > 100

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=limit,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=current),
    ):
        with pytest.raises(ValueError, match="Storage limit exceeded"):
            await manager.write_file(filename="test.txt", content=content)


@pytest.mark.asyncio
async def test_write_file_exactly_at_limit_boundary_succeeds(
    manager, mock_storage, mock_db
):
    """Writing a file that puts usage at exactly the limit should succeed
    because the guard is > not >=."""
    created_file = _make_workspace_file()
    mock_db.get_workspace_file_by_path.return_value = None
    mock_db.create_workspace_file.return_value = created_file

    limit = 100
    current = 95
    content = b"x" * 5  # 95 + 5 = 100 == limit → NOT > limit → passes

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=limit,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=current),
    ):
        result = await manager.write_file(filename="test.txt", content=content)
    assert result == created_file


@pytest.mark.asyncio
async def test_write_file_overwrite_larger_replacement_rejected(
    manager, mock_storage, mock_db
):
    """Replacing a small file with a much larger one near quota is rejected."""
    existing_file = _make_workspace_file(size_bytes=10)
    mock_db.get_workspace_file_by_path.return_value = existing_file

    limit = 100
    current = 90  # 90 bytes used, existing file is 10 of those
    content = b"x" * 25  # net: 90 - 10 + 25 = 105 > 100

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=limit,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=current),
    ):
        with pytest.raises(ValueError, match="Storage limit exceeded"):
            await manager.write_file(
                filename="test.txt", content=content, overwrite=True
            )


@pytest.mark.asyncio
async def test_write_file_overwrite_smaller_replacement_succeeds(
    manager, mock_storage, mock_db
):
    """Replacing a large file with a smaller one near quota succeeds."""
    existing_file = _make_workspace_file(size_bytes=40)
    created_file = _make_workspace_file()
    mock_db.get_workspace_file_by_path.return_value = existing_file
    mock_db.create_workspace_file.return_value = created_file

    limit = 100
    current = 90  # 90 bytes used, existing file is 40 of those
    content = b"x" * 30  # net: 90 - 40 + 30 = 80 < 100

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=limit,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=current),
    ):
        result = await manager.write_file(
            filename="test.txt", content=content, overwrite=True
        )
    assert result == created_file


@pytest.mark.asyncio
async def test_write_file_quota_rejection_skips_virus_scan_and_storage(
    manager, mock_storage, mock_db
):
    """Quota rejection must short-circuit BEFORE expensive virus scan and storage."""
    mock_db.get_workspace_file_by_path.return_value = None

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch(
            "backend.util.workspace.scan_content_safe", new_callable=AsyncMock
        ) as mock_scan,
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=100,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=100),
    ):
        with pytest.raises(ValueError, match="Storage limit exceeded"):
            await manager.write_file(filename="test.txt", content=b"data")

    mock_scan.assert_not_called()
    mock_storage.store.assert_not_called()


@pytest.mark.asyncio
async def test_write_file_empty_content_near_limit_succeeds(
    manager, mock_storage, mock_db
):
    """Empty file (0 bytes) should always fit even when at the limit."""
    created_file = _make_workspace_file()
    mock_db.get_workspace_file_by_path.return_value = None
    mock_db.create_workspace_file.return_value = created_file

    with (
        patch(
            "backend.util.workspace.get_workspace_storage",
            return_value=mock_storage,
        ),
        patch("backend.util.workspace.workspace_db", return_value=mock_db),
        patch("backend.util.workspace.scan_content_safe", new_callable=AsyncMock),
        patch(
            "backend.util.workspace.get_workspace_storage_limit_bytes",
            return_value=100,
        ),
        patch("backend.util.workspace.get_workspace_total_size", return_value=100),
    ):
        result = await manager.write_file(filename="empty.txt", content=b"")
    assert result == created_file
