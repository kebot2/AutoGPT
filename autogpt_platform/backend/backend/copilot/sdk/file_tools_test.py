"""Tests for the unified MCP file tools (Read/Write/Edit) in file_tools.py.

Covers: normal read/write/edit, large content warning, partial truncation,
complete truncation, path validation (no escape from working dir),
E2B delegation, binary file handling, and CLI built-in disallowance.
"""

import os

import pytest

from backend.copilot.sdk.tool_adapter import SDK_DISALLOWED_TOOLS

from .file_tools import (
    _LARGE_CONTENT_WARN_CHARS,
    EDIT_TOOL_NAME,
    EDIT_TOOL_SCHEMA,
    READ_TOOL_NAME,
    READ_TOOL_SCHEMA,
    WRITE_TOOL_NAME,
    WRITE_TOOL_SCHEMA,
    _handle_edit_non_e2b,
    _handle_read_non_e2b,
    _handle_write_non_e2b,
)


@pytest.fixture
def sdk_cwd(tmp_path, monkeypatch):
    """Provide a temporary SDK working directory."""
    cwd = str(tmp_path / "copilot-test-session")
    os.makedirs(cwd, exist_ok=True)
    monkeypatch.setattr("backend.copilot.sdk.file_tools.get_sdk_cwd", lambda: cwd)
    # Patch is_allowed_local_path to allow paths under our tmp cwd

    def _patched_is_allowed(path: str, cwd_arg: str | None = None) -> bool:
        resolved = os.path.realpath(path)
        norm_cwd = os.path.realpath(cwd)
        return resolved == norm_cwd or resolved.startswith(norm_cwd + os.sep)

    monkeypatch.setattr(
        "backend.copilot.sdk.file_tools.is_allowed_local_path",
        _patched_is_allowed,
    )
    return cwd


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestWriteToolSchema:
    def test_file_path_is_first_property(self):
        """file_path should be listed first in schema so truncation preserves it."""
        props = list(WRITE_TOOL_SCHEMA["properties"].keys())
        assert props[0] == "file_path"

    def test_no_required_in_schema(self):
        """required is omitted so MCP SDK does not reject truncated calls."""
        assert "required" not in WRITE_TOOL_SCHEMA


# ---------------------------------------------------------------------------
# Normal write
# ---------------------------------------------------------------------------


class TestNormalWrite:
    @pytest.mark.asyncio
    async def test_write_creates_file(self, sdk_cwd):
        result = await _handle_write_non_e2b(
            {"file_path": "hello.txt", "content": "Hello, world!"}
        )
        assert not result["isError"]
        written = open(os.path.join(sdk_cwd, "hello.txt")).read()
        assert written == "Hello, world!"

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, sdk_cwd):
        result = await _handle_write_non_e2b(
            {"file_path": "sub/dir/file.py", "content": "print('hi')"}
        )
        assert not result["isError"]
        assert os.path.isfile(os.path.join(sdk_cwd, "sub", "dir", "file.py"))

    @pytest.mark.asyncio
    async def test_write_absolute_path_within_cwd(self, sdk_cwd):
        abs_path = os.path.join(sdk_cwd, "abs.txt")
        result = await _handle_write_non_e2b(
            {"file_path": abs_path, "content": "absolute"}
        )
        assert not result["isError"]
        assert open(abs_path).read() == "absolute"

    @pytest.mark.asyncio
    async def test_success_message_contains_path(self, sdk_cwd):
        result = await _handle_write_non_e2b({"file_path": "msg.txt", "content": "ok"})
        text = result["content"][0]["text"]
        assert "Successfully wrote" in text
        assert "msg.txt" in text


# ---------------------------------------------------------------------------
# Large content warning
# ---------------------------------------------------------------------------


class TestLargeContentWarning:
    @pytest.mark.asyncio
    async def test_large_content_warns(self, sdk_cwd):
        big_content = "x" * (_LARGE_CONTENT_WARN_CHARS + 1)
        result = await _handle_write_non_e2b(
            {"file_path": "big.txt", "content": big_content}
        )
        assert not result["isError"]
        text = result["content"][0]["text"]
        assert "WARNING" in text
        assert "large" in text.lower()

    @pytest.mark.asyncio
    async def test_normal_content_no_warning(self, sdk_cwd):
        result = await _handle_write_non_e2b(
            {"file_path": "small.txt", "content": "small"}
        )
        text = result["content"][0]["text"]
        assert "WARNING" not in text


# ---------------------------------------------------------------------------
# Truncation detection
# ---------------------------------------------------------------------------


class TestTruncationDetection:
    @pytest.mark.asyncio
    async def test_partial_truncation_content_no_path(self, sdk_cwd):
        """Simulates API truncating file_path but preserving content."""
        result = await _handle_write_non_e2b({"content": "some content here"})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "truncated" in text.lower()
        assert "file_path" in text.lower()

    @pytest.mark.asyncio
    async def test_complete_truncation_empty_args(self, sdk_cwd):
        """Simulates API truncating to empty args {}."""
        result = await _handle_write_non_e2b({})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "truncated" in text.lower()
        assert "smaller steps" in text.lower()

    @pytest.mark.asyncio
    async def test_empty_file_path_string(self, sdk_cwd):
        """Empty string file_path should trigger truncation error."""
        result = await _handle_write_non_e2b({"file_path": "", "content": "data"})
        assert result["isError"]


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


class TestPathValidation:
    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, sdk_cwd):
        result = await _handle_write_non_e2b(
            {"file_path": "../../etc/passwd", "content": "evil"}
        )
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "must be within" in text.lower()

    @pytest.mark.asyncio
    async def test_absolute_outside_cwd_blocked(self, sdk_cwd):
        result = await _handle_write_non_e2b(
            {"file_path": "/etc/passwd", "content": "evil"}
        )
        assert result["isError"]

    @pytest.mark.asyncio
    async def test_no_sdk_cwd_returns_error(self, monkeypatch):
        monkeypatch.setattr("backend.copilot.sdk.file_tools.get_sdk_cwd", lambda: "")
        result = await _handle_write_non_e2b({"file_path": "test.txt", "content": "hi"})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "working directory" in text.lower()


# ---------------------------------------------------------------------------
# CLI built-in Write is disallowed
# ---------------------------------------------------------------------------


class TestCliBuiltinWriteDisallowed:
    def test_write_in_disallowed_tools(self):
        assert "Write" in SDK_DISALLOWED_TOOLS

    def test_tool_name_is_write(self):
        assert WRITE_TOOL_NAME == "Write"


# ===========================================================================
# Read tool tests
# ===========================================================================


class TestReadToolSchema:
    def test_file_path_is_first_property(self):
        props = list(READ_TOOL_SCHEMA["properties"].keys())
        assert props[0] == "file_path"

    def test_no_required_in_schema(self):
        """required is omitted so MCP SDK does not reject truncated calls."""
        assert "required" not in READ_TOOL_SCHEMA

    def test_tool_name_is_read_file(self):
        assert READ_TOOL_NAME == "read_file"


class TestNormalRead:
    @pytest.mark.asyncio
    async def test_read_file(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "hello.txt")
        with open(path, "w") as f:
            f.write("line1\nline2\nline3\n")
        result = await _handle_read_non_e2b({"file_path": "hello.txt"})
        assert not result["isError"]
        text = result["content"][0]["text"]
        assert "line1" in text
        assert "line2" in text
        assert "line3" in text

    @pytest.mark.asyncio
    async def test_read_with_line_numbers(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "numbered.txt")
        with open(path, "w") as f:
            f.write("alpha\nbeta\ngamma\n")
        result = await _handle_read_non_e2b({"file_path": "numbered.txt"})
        text = result["content"][0]["text"]
        # cat -n format: line numbers with tab separator
        assert "1\t" in text
        assert "2\t" in text
        assert "3\t" in text

    @pytest.mark.asyncio
    async def test_read_absolute_path_within_cwd(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "abs.txt")
        with open(path, "w") as f:
            f.write("absolute content")
        result = await _handle_read_non_e2b({"file_path": path})
        assert not result["isError"]
        assert "absolute content" in result["content"][0]["text"]


class TestReadOffsetLimit:
    @pytest.mark.asyncio
    async def test_read_with_offset(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "lines.txt")
        with open(path, "w") as f:
            for i in range(10):
                f.write(f"line{i}\n")
        result = await _handle_read_non_e2b(
            {"file_path": "lines.txt", "offset": 5, "limit": 3}
        )
        text = result["content"][0]["text"]
        assert "line5" in text
        assert "line6" in text
        assert "line7" in text
        assert "line4" not in text
        assert "line8" not in text

    @pytest.mark.asyncio
    async def test_read_with_limit(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "many.txt")
        with open(path, "w") as f:
            for i in range(100):
                f.write(f"line{i}\n")
        result = await _handle_read_non_e2b({"file_path": "many.txt", "limit": 2})
        text = result["content"][0]["text"]
        assert "line0" in text
        assert "line1" in text
        assert "line2" not in text

    @pytest.mark.asyncio
    async def test_offset_line_numbers_are_correct(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "offset_nums.txt")
        with open(path, "w") as f:
            for i in range(10):
                f.write(f"line{i}\n")
        result = await _handle_read_non_e2b(
            {"file_path": "offset_nums.txt", "offset": 3, "limit": 2}
        )
        text = result["content"][0]["text"]
        # Lines 4 and 5 (1-indexed) should appear
        assert "4\t" in text
        assert "5\t" in text


class TestReadInvalidOffsetLimit:
    @pytest.mark.asyncio
    async def test_non_integer_offset(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "valid.txt")
        with open(path, "w") as f:
            f.write("content\n")
        result = await _handle_read_non_e2b({"file_path": "valid.txt", "offset": "abc"})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "invalid" in text.lower()

    @pytest.mark.asyncio
    async def test_non_integer_limit(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "valid.txt")
        with open(path, "w") as f:
            f.write("content\n")
        result = await _handle_read_non_e2b({"file_path": "valid.txt", "limit": "xyz"})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "invalid" in text.lower()


class TestReadFileNotFound:
    @pytest.mark.asyncio
    async def test_file_not_found(self, sdk_cwd):
        result = await _handle_read_non_e2b({"file_path": "nonexistent.txt"})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "not found" in text.lower()


class TestReadPathTraversal:
    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, sdk_cwd):
        result = await _handle_read_non_e2b({"file_path": "../../etc/passwd"})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "must be within" in text.lower()

    @pytest.mark.asyncio
    async def test_absolute_outside_cwd_blocked(self, sdk_cwd):
        result = await _handle_read_non_e2b({"file_path": "/etc/passwd"})
        assert result["isError"]


class TestReadBinaryFile:
    @pytest.mark.asyncio
    async def test_binary_file_rejected(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "image.png")
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        result = await _handle_read_non_e2b({"file_path": "image.png"})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "binary" in text.lower()

    @pytest.mark.asyncio
    async def test_text_file_not_rejected_as_binary(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "code.py")
        with open(path, "w") as f:
            f.write("print('hello')\n")
        result = await _handle_read_non_e2b({"file_path": "code.py"})
        assert not result["isError"]


class TestReadTruncationDetection:
    @pytest.mark.asyncio
    async def test_truncation_offset_without_file_path(self, sdk_cwd):
        """offset present but file_path missing — truncated call."""
        result = await _handle_read_non_e2b({"offset": 5})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "truncated" in text.lower()

    @pytest.mark.asyncio
    async def test_truncation_limit_without_file_path(self, sdk_cwd):
        """limit present but file_path missing — truncated call."""
        result = await _handle_read_non_e2b({"limit": 100})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "truncated" in text.lower()

    @pytest.mark.asyncio
    async def test_no_truncation_plain_empty(self, sdk_cwd):
        """Empty args — treated as complete truncation."""
        result = await _handle_read_non_e2b({})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "truncated" in text.lower() or "empty arguments" in text.lower()


class TestReadEmptyFilePath:
    @pytest.mark.asyncio
    async def test_empty_file_path(self, sdk_cwd):
        result = await _handle_read_non_e2b({"file_path": ""})
        assert result["isError"]

    @pytest.mark.asyncio
    async def test_no_sdk_cwd(self, monkeypatch):
        monkeypatch.setattr("backend.copilot.sdk.file_tools.get_sdk_cwd", lambda: "")
        result = await _handle_read_non_e2b({"file_path": "test.txt"})
        assert result["isError"]
        assert "working directory" in result["content"][0]["text"].lower()


# ===========================================================================
# Edit tool tests
# ===========================================================================


class TestEditToolSchema:
    def test_file_path_is_first_property(self):
        props = list(EDIT_TOOL_SCHEMA["properties"].keys())
        assert props[0] == "file_path"

    def test_no_required_in_schema(self):
        """required is omitted so MCP SDK does not reject truncated calls."""
        assert "required" not in EDIT_TOOL_SCHEMA

    def test_tool_name_is_edit(self):
        assert EDIT_TOOL_NAME == "Edit"

    def test_edit_in_disallowed_tools(self):
        assert "Edit" in SDK_DISALLOWED_TOOLS


class TestNormalEdit:
    @pytest.mark.asyncio
    async def test_simple_replacement(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "edit_me.txt")
        with open(path, "w") as f:
            f.write("Hello World\n")
        result = await _handle_edit_non_e2b(
            {"file_path": "edit_me.txt", "old_string": "World", "new_string": "Earth"}
        )
        assert not result["isError"]
        content = open(path).read()
        assert content == "Hello Earth\n"

    @pytest.mark.asyncio
    async def test_edit_reports_replacement_count(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "count.txt")
        with open(path, "w") as f:
            f.write("one two three\n")
        result = await _handle_edit_non_e2b(
            {"file_path": "count.txt", "old_string": "two", "new_string": "2"}
        )
        text = result["content"][0]["text"]
        assert "1 replacement" in text

    @pytest.mark.asyncio
    async def test_edit_absolute_path(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "abs_edit.txt")
        with open(path, "w") as f:
            f.write("before\n")
        result = await _handle_edit_non_e2b(
            {"file_path": path, "old_string": "before", "new_string": "after"}
        )
        assert not result["isError"]
        assert open(path).read() == "after\n"


class TestEditOldStringNotFound:
    @pytest.mark.asyncio
    async def test_old_string_not_found(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "nope.txt")
        with open(path, "w") as f:
            f.write("Hello World\n")
        result = await _handle_edit_non_e2b(
            {"file_path": "nope.txt", "old_string": "MISSING", "new_string": "x"}
        )
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "not found" in text.lower()


class TestEditOldStringNotUnique:
    @pytest.mark.asyncio
    async def test_not_unique_without_replace_all(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "dup.txt")
        with open(path, "w") as f:
            f.write("foo bar foo baz\n")
        result = await _handle_edit_non_e2b(
            {"file_path": "dup.txt", "old_string": "foo", "new_string": "qux"}
        )
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "2 times" in text
        # File should be unchanged
        assert open(path).read() == "foo bar foo baz\n"


class TestEditReplaceAll:
    @pytest.mark.asyncio
    async def test_replace_all(self, sdk_cwd):
        path = os.path.join(sdk_cwd, "all.txt")
        with open(path, "w") as f:
            f.write("foo bar foo baz foo\n")
        result = await _handle_edit_non_e2b(
            {
                "file_path": "all.txt",
                "old_string": "foo",
                "new_string": "qux",
                "replace_all": True,
            }
        )
        assert not result["isError"]
        content = open(path).read()
        assert content == "qux bar qux baz qux\n"
        text = result["content"][0]["text"]
        assert "3 replacement" in text


class TestEditPartialTruncation:
    @pytest.mark.asyncio
    async def test_partial_truncation(self, sdk_cwd):
        """file_path missing but old_string/new_string present."""
        result = await _handle_edit_non_e2b(
            {"old_string": "something", "new_string": "else"}
        )
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "truncated" in text.lower()

    @pytest.mark.asyncio
    async def test_complete_truncation(self, sdk_cwd):
        result = await _handle_edit_non_e2b({})
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "truncated" in text.lower()

    @pytest.mark.asyncio
    async def test_empty_file_path_with_content(self, sdk_cwd):
        result = await _handle_edit_non_e2b(
            {"file_path": "", "old_string": "x", "new_string": "y"}
        )
        assert result["isError"]


class TestEditPathTraversal:
    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, sdk_cwd):
        result = await _handle_edit_non_e2b(
            {
                "file_path": "../../etc/passwd",
                "old_string": "root",
                "new_string": "evil",
            }
        )
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "must be within" in text.lower()

    @pytest.mark.asyncio
    async def test_absolute_outside_cwd_blocked(self, sdk_cwd):
        result = await _handle_edit_non_e2b(
            {
                "file_path": "/etc/passwd",
                "old_string": "root",
                "new_string": "evil",
            }
        )
        assert result["isError"]


class TestEditFileNotFound:
    @pytest.mark.asyncio
    async def test_file_not_found(self, sdk_cwd):
        result = await _handle_edit_non_e2b(
            {
                "file_path": "nonexistent.txt",
                "old_string": "x",
                "new_string": "y",
            }
        )
        assert result["isError"]
        text = result["content"][0]["text"]
        assert "not found" in text.lower()

    @pytest.mark.asyncio
    async def test_no_sdk_cwd(self, monkeypatch):
        monkeypatch.setattr("backend.copilot.sdk.file_tools.get_sdk_cwd", lambda: "")
        result = await _handle_edit_non_e2b(
            {"file_path": "test.txt", "old_string": "x", "new_string": "y"}
        )
        assert result["isError"]
        assert "working directory" in result["content"][0]["text"].lower()
