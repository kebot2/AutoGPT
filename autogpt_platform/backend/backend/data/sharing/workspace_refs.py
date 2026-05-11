"""Extract ``workspace://<file_id>`` references from arbitrary nested data.

Both execution-output sharing and chat-message sharing need to build an
allowlist of workspace files exposed by a share.  The scan logic is
identical — only the input shape differs (execution output dicts vs.
chat message content/tool-call payloads) — so a single recursive walker
handles both.

Only plain strings that *start* with ``workspace://`` are matched; the
URI cannot appear as a substring inside other text.  This mirrors the
output of :func:`backend.util.file.store_media_file` and prevents false
positives from quoted strings in narrative content.
"""

from typing import Any

_WORKSPACE_PREFIX = "workspace://"


def extract_workspace_file_ids(value: Any) -> set[str]:
    """Walk *value* recursively and collect referenced workspace file IDs.

    Accepts any JSON-shaped value: strings, lists, dicts, primitives.
    Non-string leaves are ignored.  Returns the unique set of file IDs
    (the part between ``workspace://`` and an optional ``#fragment``).
    """
    file_ids: set[str] = set()
    _scan(value, file_ids)
    return file_ids


def _scan(value: Any, sink: set[str]) -> None:
    if isinstance(value, str):
        if value.startswith(_WORKSPACE_PREFIX):
            raw = value.removeprefix(_WORKSPACE_PREFIX)
            file_ref = raw.split("#", 1)[0] if "#" in raw else raw
            # Reject leading slashes — those would denote a path under
            # the workspace, not a file ID, and our allowlist keys on
            # file ID only.
            if file_ref and not file_ref.startswith("/"):
                sink.add(file_ref)
        return
    if isinstance(value, list):
        for item in value:
            _scan(item, sink)
        return
    if isinstance(value, dict):
        for v in value.values():
            _scan(v, sink)
