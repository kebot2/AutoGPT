"""Tool for waiting on, polling, or cancelling a backgrounded tool call.

Long-running tool calls exceed their per-call timeout and are parked in the
background registry by :func:`_execute_tool_sync`. This tool lets the agent
decide whether to keep waiting, poll status, or cancel — so the autopilot
stays in control rather than the handler making an irreversible choice.
"""

import asyncio
import logging
from typing import Any

from backend.copilot.model import ChatSession
from backend.copilot.sdk.background_registry import (
    MAX_BACKGROUND_WAIT_SECONDS as _MAX_BACKGROUND_WAIT_SECONDS,
)
from backend.copilot.sdk.background_registry import (
    get_background_task,
    unregister_background_task,
)

from .base import BaseTool
from .models import BackgroundToolStatus, ErrorResponse, ToolResponseBase

logger = logging.getLogger(__name__)


class CheckBackgroundToolTool(BaseTool):
    """Inspect, wait on, or cancel a backgrounded tool call."""

    @property
    def name(self) -> str:
        return "check_background_tool"

    @property
    def timeout_seconds(self) -> int | None:
        # This tool drives its own wait loop up to _MAX_BACKGROUND_WAIT_SECONDS.
        # Applying a second timeout on top would be redundant and could cancel
        # the wait prematurely.
        return None

    @property
    def description(self) -> str:
        return (
            "Inspect a backgrounded tool call by its background_id. "
            "Use when a prior tool call returned type='background'. "
            "Options: wait for completion up to wait_seconds "
            f"(default 60, max {_MAX_BACKGROUND_WAIT_SECONDS}), just "
            "check status with wait_seconds=0, or cancel=true to "
            "abort the task and discard its result."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "background_id": {
                    "type": "string",
                    "description": "The background_id returned by the timed-out tool.",
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": (
                        "Max seconds to wait for completion. 0 = just check "
                        "status. Values above "
                        f"{_MAX_BACKGROUND_WAIT_SECONDS} are clamped to that "
                        "maximum — call again to keep waiting."
                    ),
                    "default": 60,
                },
                "cancel": {
                    "type": "boolean",
                    "description": (
                        "If true, cancel the background task and discard "
                        "its result. Takes precedence over wait_seconds."
                    ),
                    "default": False,
                },
            },
            "required": ["background_id"],
        }

    async def _execute(
        self,
        user_id: str | None,
        session: ChatSession,
        *,
        background_id: str = "",
        wait_seconds: int = 60,
        cancel: bool = False,
        **kwargs,
    ) -> ToolResponseBase:
        if not background_id:
            return ErrorResponse(
                message="background_id is required",
                session_id=session.session_id,
            )

        entry = get_background_task(background_id)
        if entry is None:
            return ErrorResponse(
                message=(
                    f"No background task with id {background_id}. It may "
                    "have already completed (and been consumed) or never "
                    "existed."
                ),
                session_id=session.session_id,
            )

        task: asyncio.Task = entry["task"]
        tool_name: str = entry["tool_name"]

        if cancel:
            # Race guard: the task may have finished between the registry
            # lookup and the cancel. If so, surface the real result rather
            # than reporting 'cancelled' and losing the output.
            if task.done():
                return _status_from_finished_task(
                    session, tool_name, background_id, task
                )
            task.cancel()
            unregister_background_task(background_id)
            logger.info(
                "Cancelled background task %s for tool %s by agent request",
                background_id,
                tool_name,
            )
            return BackgroundToolStatus(
                message=f"Cancelled background task for '{tool_name}'.",
                session_id=session.session_id,
                status="cancelled",
                tool=tool_name,
                background_id=background_id,
            )

        if task.done():
            return _status_from_finished_task(session, tool_name, background_id, task)

        effective_wait = max(0, min(wait_seconds, _MAX_BACKGROUND_WAIT_SECONDS))
        if effective_wait == 0:
            return BackgroundToolStatus(
                message=(
                    f"'{tool_name}' is still running. Call again with "
                    "wait_seconds>0 to wait, or cancel=true to abort."
                ),
                session_id=session.session_id,
                status="still_running",
                tool=tool_name,
                background_id=background_id,
            )

        await asyncio.wait({task}, timeout=effective_wait)
        if task.done():
            return _status_from_finished_task(session, tool_name, background_id, task)

        return BackgroundToolStatus(
            message=(
                f"'{tool_name}' still running after waiting "
                f"{effective_wait}s. Call again to keep waiting, or "
                "cancel=true to abort."
            ),
            session_id=session.session_id,
            status="still_running",
            tool=tool_name,
            background_id=background_id,
            waited_seconds=effective_wait,
        )


def _status_from_finished_task(
    session: ChatSession,
    tool_name: str,
    background_id: str,
    task: asyncio.Task,
) -> ToolResponseBase:
    """Unregister a finished task and return its status."""
    unregister_background_task(background_id)

    if task.cancelled():
        return BackgroundToolStatus(
            message=f"Background task for '{tool_name}' was cancelled.",
            session_id=session.session_id,
            status="cancelled",
            tool=tool_name,
            background_id=background_id,
        )

    exc = task.exception()
    if exc is not None:
        return BackgroundToolStatus(
            message=f"'{tool_name}' raised {type(exc).__name__}: {exc}",
            session_id=session.session_id,
            status="error",
            tool=tool_name,
            background_id=background_id,
        )

    result = task.result()
    # A tool can complete with success=False without raising — preserve
    # that as status="error" so the agent doesn't treat it as a win.
    if not result.success:
        return BackgroundToolStatus(
            message=f"'{tool_name}' completed with an error.",
            session_id=session.session_id,
            status="error",
            tool=tool_name,
            background_id=background_id,
            output=result.output,
        )
    return BackgroundToolStatus(
        message=f"'{tool_name}' completed.",
        session_id=session.session_id,
        status="completed",
        tool=tool_name,
        background_id=background_id,
        output=result.output,
    )
