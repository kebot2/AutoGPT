"""Unified MCP file tools (Read/Write/Edit) for both E2B and non-E2B modes.

Replaces the CLI's built-in Write and Edit tools, which have no defence against
output-token truncation.  When the LLM generates a very large argument the API
truncates the response mid-JSON and Ajv rejects it with the opaque
"'file_path' is a required property" error, losing the user's work.

Each MCP tool:
- Detects partial truncation (arguments present but file_path missing)
- Detects complete truncation (empty args)
- In non-E2B mode: operates on the SDK working directory
- In E2B mode: delegates to the E2B sandbox handler

The JSON schemas place ``file_path`` FIRST so that truncation is more likely
to preserve the path (the API serialises properties in schema order).
"""

import asyncio
import itertools
import json
import logging
import os
from typing import Any, Callable

from backend.copilot.context import get_sdk_cwd, is_allowed_local_path

logger = logging.getLogger(__name__)

# Per-path lock for edit operations to prevent parallel lost updates.
# When MCP tools are dispatched in parallel (readOnlyHint=True annotation),
# two Edit calls on the same file could race through read-modify-write
# and silently drop one change.  Keyed by resolved absolute path.
_edit_locks: dict[str, asyncio.Lock] = {}

# Inline content above this threshold triggers a warning — it survived this
# time but is dangerously close to the API output-token truncation limit.
_LARGE_CONTENT_WARN_CHARS = 50_000


def _mcp(text: str, *, error: bool = False) -> dict[str, Any]:
    if error:
        text = json.dumps({"error": text, "type": "error"})
    return {"content": [{"type": "text", "text": text}], "isError": error}


_PARTIAL_TRUNCATION_MSG = (
    "Your Write call was truncated (file_path missing but content "
    "was present). The content was too large for a single tool call. "
    "Write in chunks: use bash_exec with "
    "'cat > file << \"EOF\"\\n...\\nEOF' for the first section, "
    "'cat >> file << \"EOF\"\\n...\\nEOF' to append subsequent "
    "sections, then reference the file with "
    "@@agptfile:/path/to/file if needed."
)

_COMPLETE_TRUNCATION_MSG = (
    "Your Write call had empty arguments — this means your previous "
    "response was too long and the tool call was truncated by the API. "
    "Break your work into smaller steps. For large content, write "
    "section-by-section using bash_exec with "
    "'cat > file << \"EOF\"\\n...\\nEOF' and "
    "'cat >> file << \"EOF\"\\n...\\nEOF'."
)


def _check_truncation(file_path: str, content: str) -> dict[str, Any] | None:
    """Return an error response if the args look truncated, else ``None``."""
    if not file_path:
        if content:
            return _mcp(_PARTIAL_TRUNCATION_MSG, error=True)
        return _mcp(_COMPLETE_TRUNCATION_MSG, error=True)
    return None


def _resolve_and_validate(
    file_path: str, sdk_cwd: str
) -> tuple[str, None] | tuple[None, dict[str, Any]]:
    """Resolve *file_path* against *sdk_cwd* and validate it stays within bounds.

    Returns ``(resolved_path, None)`` on success, or ``(None, error_response)``
    on failure.
    """
    if not os.path.isabs(file_path):
        resolved = os.path.normpath(os.path.join(sdk_cwd, file_path))
    else:
        resolved = os.path.normpath(file_path)

    if not is_allowed_local_path(resolved, sdk_cwd):
        return None, _mcp(
            f"Path must be within the working directory: {os.path.basename(file_path)}",
            error=True,
        )
    return resolved, None


async def _handle_write_non_e2b(args: dict[str, Any]) -> dict[str, Any]:
    """Write content to a file in the SDK working directory (non-E2B mode)."""
    if not args:
        return _mcp(_COMPLETE_TRUNCATION_MSG, error=True)
    file_path: str = args.get("file_path", "")
    content: str = args.get("content", "")

    truncation_err = _check_truncation(file_path, content)
    if truncation_err is not None:
        return truncation_err

    sdk_cwd = get_sdk_cwd()
    if not sdk_cwd:
        return _mcp("No SDK working directory available", error=True)

    resolved, err = _resolve_and_validate(file_path, sdk_cwd)
    if err is not None:
        return err
    assert resolved is not None  # for type narrowing

    try:
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as exc:
        logger.error("Write failed for %s: %s", resolved, exc, exc_info=True)
        return _mcp(
            f"Failed to write {os.path.basename(resolved)}: {type(exc).__name__}",
            error=True,
        )

    msg = f"Successfully wrote to {resolved}"
    if len(content) > _LARGE_CONTENT_WARN_CHARS:
        logger.warning(
            "[Write] large inline content (%d chars) for %s",
            len(content),
            resolved,
        )
        msg += (
            f"\n\nWARNING: The content was very large ({len(content)} chars). "
            "Next time, write large files in sections using bash_exec with "
            "'cat > file << EOF ... EOF' and 'cat >> file << EOF ... EOF' "
            "to avoid output-token truncation."
        )
    return _mcp(msg)


async def _handle_write_e2b(args: dict[str, Any]) -> dict[str, Any]:
    """Write content to a file, delegating to the E2B sandbox."""
    from .e2b_file_tools import _handle_write_file

    if not args:
        return _mcp(_COMPLETE_TRUNCATION_MSG, error=True)
    file_path: str = args.get("file_path", "")
    content: str = args.get("content", "")

    truncation_err = _check_truncation(file_path, content)
    if truncation_err is not None:
        return truncation_err

    return await _handle_write_file(args)


def get_write_tool_handler(*, use_e2b: bool) -> Callable[..., Any]:
    """Return the appropriate Write handler for the current execution mode."""
    if use_e2b:
        return _handle_write_e2b
    return _handle_write_non_e2b


WRITE_TOOL_NAME = "Write"
WRITE_TOOL_DESCRIPTION = (
    "Write or create a file. Parent directories are created automatically. "
    "For large content (>2000 words), prefer writing in sections using "
    "bash_exec with 'cat > file' and 'cat >> file' instead."
)
WRITE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": (
                "The path to the file to write. "
                "Relative paths are resolved against the working directory."
            ),
        },
        "content": {
            "type": "string",
            "description": "The content to write to the file.",
        },
    },
}


# ---------------------------------------------------------------------------
# Unified Read tool
# ---------------------------------------------------------------------------

_READ_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".bz2",
        ".xz",
        ".7z",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".o",
        ".a",
        ".pyc",
        ".pyo",
        ".class",
        ".wasm",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".mkv",
        ".wav",
        ".flac",
        ".sqlite",
        ".db",
    }
)


def _is_likely_binary(path: str) -> bool:
    """Heuristic check for binary files by extension."""
    _, ext = os.path.splitext(path)
    return ext.lower() in _READ_BINARY_EXTENSIONS


async def _handle_read_non_e2b(args: dict[str, Any]) -> dict[str, Any]:
    """Read a file from the SDK working directory (non-E2B mode)."""
    if not args:
        return _mcp(
            "Your read_file call had empty arguments — this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )
    file_path: str = args.get("file_path", "")
    try:
        offset: int = max(0, int(args.get("offset", 0)))
        limit: int = max(1, int(args.get("limit", 2000)))
    except (ValueError, TypeError):
        return _mcp(
            "Invalid offset/limit — must be integers.",
            error=True,
        )

    if not file_path:
        # Truncation detection: if offset/limit present but file_path missing,
        # the call was likely truncated by the API.
        if "offset" in args or "limit" in args:
            return _mcp(
                "Your read_file call was truncated (file_path missing but "
                "offset/limit were present). Resend with the full file_path.",
                error=True,
            )
        return _mcp("file_path is required", error=True)

    sdk_cwd = get_sdk_cwd()
    if not sdk_cwd:
        return _mcp("No SDK working directory available", error=True)

    resolved, err = _resolve_and_validate(file_path, sdk_cwd)
    if err is not None:
        return err
    assert resolved is not None

    if _is_likely_binary(resolved):
        return _mcp(
            f"Cannot read binary file: {os.path.basename(resolved)}. "
            "Use bash_exec with 'xxd' or 'file' to inspect binary files.",
            error=True,
        )

    try:
        with open(resolved, encoding="utf-8", errors="replace") as f:
            selected = list(itertools.islice(f, offset, offset + limit))
    except FileNotFoundError:
        return _mcp(f"File not found: {file_path}", error=True)
    except PermissionError:
        return _mcp(f"Permission denied: {file_path}", error=True)
    except Exception as exc:
        return _mcp(f"Failed to read {file_path}: {exc}", error=True)

    numbered = "".join(
        f"{i + offset + 1:>6}\t{line}" for i, line in enumerate(selected)
    )
    return _mcp(numbered)


async def _handle_read_e2b(args: dict[str, Any]) -> dict[str, Any]:
    """Read a file, delegating to the E2B sandbox."""
    from .e2b_file_tools import _handle_read_file

    return await _handle_read_file(args)


def get_read_tool_handler(*, use_e2b: bool) -> Callable[..., Any]:
    """Return the appropriate Read handler for the current execution mode."""
    if use_e2b:
        return _handle_read_e2b
    return _handle_read_non_e2b


READ_TOOL_NAME = "read_file"
READ_TOOL_DESCRIPTION = (
    "Read a file from the working directory. Returns content with line numbers "
    "(cat -n format). Use offset and limit to read specific ranges for large files."
)
READ_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": (
                "The path to the file to read. "
                "Relative paths are resolved against the working directory."
            ),
        },
        "offset": {
            "type": "integer",
            "description": (
                "Line number to start reading from (0-indexed). Default: 0."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Number of lines to read. Default: 2000.",
        },
    },
}


# ---------------------------------------------------------------------------
# Unified Edit tool
# ---------------------------------------------------------------------------

_EDIT_PARTIAL_TRUNCATION_MSG = (
    "Your Edit call was truncated (file_path missing but old_string/new_string "
    "were present). The arguments were too large for a single tool call. "
    "Break your edit into smaller replacements, or use bash_exec with "
    "'sed' for large-scale find-and-replace."
)


async def _handle_edit_non_e2b(args: dict[str, Any]) -> dict[str, Any]:
    """Edit a file in the SDK working directory (non-E2B mode)."""
    if not args:
        return _mcp(
            "Your Edit call had empty arguments — this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )
    file_path: str = args.get("file_path", "")
    old_string: str = args.get("old_string", "")
    new_string: str = args.get("new_string", "")
    replace_all: bool = args.get("replace_all", False)

    # Partial truncation: file_path missing but edit strings present
    if not file_path:
        if old_string or new_string:
            return _mcp(_EDIT_PARTIAL_TRUNCATION_MSG, error=True)
        return _mcp(
            "Your Edit call had empty arguments — this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )

    if not old_string:
        return _mcp("old_string is required", error=True)

    sdk_cwd = get_sdk_cwd()
    if not sdk_cwd:
        return _mcp("No SDK working directory available", error=True)

    resolved, err = _resolve_and_validate(file_path, sdk_cwd)
    if err is not None:
        return err
    assert resolved is not None

    # Per-path lock prevents parallel edits from racing through
    # the read-modify-write cycle and silently dropping changes.
    if resolved not in _edit_locks:
        _edit_locks[resolved] = asyncio.Lock()
    lock = _edit_locks[resolved]
    async with lock:
        try:
            with open(resolved, encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return _mcp(f"File not found: {file_path}", error=True)
        except PermissionError:
            return _mcp(f"Permission denied: {file_path}", error=True)
        except Exception as exc:
            return _mcp(f"Failed to read {file_path}: {exc}", error=True)

        count = content.count(old_string)
        if count == 0:
            return _mcp(f"old_string not found in {file_path}", error=True)
        if count > 1 and not replace_all:
            return _mcp(
                f"old_string appears {count} times in {file_path}. "
                "Use replace_all=true or provide a more unique string.",
                error=True,
            )

        updated = (
            content.replace(old_string, new_string)
            if replace_all
            else content.replace(old_string, new_string, 1)
        )

        try:
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(updated)
        except Exception as exc:
            return _mcp(f"Failed to write {file_path}: {exc}", error=True)

    # Evict lock when no other coroutine is waiting, preventing unbounded growth.
    if not lock.locked() and _edit_locks.get(resolved) is lock:
        _edit_locks.pop(resolved, None)

    return _mcp(f"Edited {resolved} ({count} replacement{'s' if count > 1 else ''})")


async def _handle_edit_e2b(args: dict[str, Any]) -> dict[str, Any]:
    """Edit a file, delegating to the E2B sandbox."""
    from .e2b_file_tools import _handle_edit_file

    if not args:
        return _mcp(
            "Your Edit call had empty arguments — this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )
    file_path: str = args.get("file_path", "")
    old_string: str = args.get("old_string", "")
    new_string: str = args.get("new_string", "")

    # Check truncation before delegating
    if not file_path:
        if old_string or new_string:
            return _mcp(_EDIT_PARTIAL_TRUNCATION_MSG, error=True)
        return _mcp(
            "Your Edit call had empty arguments — this means your previous "
            "response was too long and the tool call was truncated by the API. "
            "Break your work into smaller steps.",
            error=True,
        )

    return await _handle_edit_file(args)


def get_edit_tool_handler(*, use_e2b: bool) -> Callable[..., Any]:
    """Return the appropriate Edit handler for the current execution mode."""
    if use_e2b:
        return _handle_edit_e2b
    return _handle_edit_non_e2b


EDIT_TOOL_NAME = "Edit"
EDIT_TOOL_DESCRIPTION = (
    "Make targeted text replacements in a file. Finds old_string in the file "
    "and replaces it with new_string. For replacing all occurrences, set "
    "replace_all=true."
)
EDIT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": (
                "The path to the file to edit. "
                "Relative paths are resolved against the working directory."
            ),
        },
        "old_string": {
            "type": "string",
            "description": "The text to find in the file.",
        },
        "new_string": {
            "type": "string",
            "description": "The replacement text.",
        },
        "replace_all": {
            "type": "boolean",
            "description": (
                "Replace all occurrences of old_string (default: false). "
                "When false, old_string must appear exactly once."
            ),
        },
    },
}
