"""In-process sub-agent tool for baseline copilot mode.

The ``Task`` tool delegates a focused, context-isolated unit of work to a
fresh tool-call loop that runs **inside the current session** — same user,
same tools, same workspace — but with its own message history. The parent
LLM never sees the sub-agent's intermediate tool calls or reasoning; it
only sees the sub-agent's final summary as the tool result.

Why baseline needs its own: the Claude Agent SDK ships a built-in
``Task`` / ``Agent`` tool that does this natively. Baseline routes through
OpenAI-compatible providers (Kimi, GPT, Grok, Gemini) where no such
built-in exists. This platform-tool rebuild gives baseline feature parity
without giving up the model-flexibility advantage.

**Execution note.** Baseline's service loop short-circuits ``Task`` *before*
dispatching through ``execute_tool`` because the nested loop needs direct
access to the parent's ``_BaselineStreamState`` primitives (LLM caller,
tool executor, reasoning emitter). Calls that reach ``_execute`` here are
an unsupported path — they get a clear error so a misconfigured caller
fails loudly rather than silently producing a no-op response.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.copilot.model import ChatSession

from .base import BaseTool
from .models import ErrorResponse, ToolResponseBase

logger = logging.getLogger(__name__)


class TaskTool(BaseTool):
    """Delegate a focused task to an in-process sub-agent."""

    @property
    def name(self) -> str:
        # Capitalised to match the frontend's switch on ``"Task"`` / ``"Agent"``
        # (see ``copilot/tools/GenericTool/helpers.ts``). Keeping the name
        # identical to the SDK's built-in means the chat UI renders baseline
        # and SDK sub-agent runs the same way.
        return "Task"

    @property
    def description(self) -> str:
        return (
            "Run a focused task in an in-process sub-agent with isolated "
            "history; only its final summary returns. For durable/background "
            "work use `run_sub_session` instead."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Short (3-5 word) accordion label.",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Full instructions — sub-agent does NOT inherit "
                        "parent conversation."
                    ),
                },
                "subagent_type": {
                    "type": "string",
                    "description": "Optional profile name (SDK parity; ignored).",
                },
            },
            "required": ["description", "prompt"],
        }

    async def _execute(
        self,
        user_id: str | None,
        session: ChatSession,
        **kwargs: Any,
    ) -> ToolResponseBase:
        del user_id, kwargs
        # Baseline's service loop is supposed to intercept ``Task`` calls
        # before they reach this path. Reaching here means either the SDK
        # path dispatched through MCP (which would be a misconfiguration —
        # SDK already has a CLI-native Task tool) or baseline's short-circuit
        # was bypassed. Either way, return a loud error so the misconfig is
        # visible in the trace instead of silently returning nothing.
        logger.warning(
            "Task tool reached the generic execute path — expected baseline "
            "service to intercept. session=%s",
            session.session_id,
        )
        return ErrorResponse(
            message=(
                "Task is a baseline-only in-process sub-agent tool and must "
                "be dispatched by the baseline service loop. In SDK mode use "
                "the CLI-native Task tool; for durable/background work use "
                "run_sub_session instead."
            ),
            session_id=session.session_id,
        )
