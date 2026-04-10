"""DecomposeGoalTool - Breaks agent-building goals into sub-instructions."""

import asyncio
import logging
from typing import Any
from uuid import uuid4

from backend.copilot.model import ChatMessage, ChatSession, append_message_if

from .base import BaseTool
from .models import (
    DecompositionStepModel,
    ErrorResponse,
    TaskDecompositionResponse,
    ToolResponseBase,
)

logger = logging.getLogger(__name__)

# Matches the guide's "4-8 steps max" constraint.
MAX_STEPS = 8
DEFAULT_ACTION = "add_block"
VALID_ACTIONS = {"add_block", "connect_blocks", "configure", "add_input", "add_output"}

# Auto-approve countdown — single source of truth for both client and server.
# The frontend reads ``auto_approve_seconds`` from the tool response and runs
# the visible countdown. The server fallback runs slightly longer to absorb
# network latency / SSE round-trip when the client also sends "Approved".
AUTO_APPROVE_CLIENT_SECONDS = 60
AUTO_APPROVE_SERVER_GRACE_SECONDS = 30
AUTO_APPROVE_SERVER_SECONDS = (
    AUTO_APPROVE_CLIENT_SECONDS + AUTO_APPROVE_SERVER_GRACE_SECONDS
)
AUTO_APPROVE_MESSAGE = "Approved. Please build the agent."

# Fire-and-forget tasks held to keep them alive and self-clean on completion.
# Same pattern as ``backend/copilot/tools/agent_browser.py``.
_auto_approve_tasks: set[asyncio.Task] = set()


def _no_user_action_since(baseline_index: int):
    """Predicate: returns True iff no ``role == "user"`` message exists at
    or after ``baseline_index`` in the session message list.

    Why an index instead of ``ChatMessage.sequence``: ``_save_session_to_db``
    persists messages with auto-assigned sequences in the DB but does NOT
    write those sequences back onto the in-memory ``ChatMessage`` objects,
    and ``cache_chat_session`` writes the in-memory copy to Redis. So when
    this predicate later loads the session from cache, freshly-appended
    messages have ``sequence=None``, which would falsely register as 0 and
    miss them entirely — the predicate would treat the user's manual
    "Approved" as if it never happened, and the auto-approve would fire a
    duplicate after the agent build had already completed. Indices are
    monotonic and require no DB-side bookkeeping.
    """

    def _check(session: ChatSession) -> bool:
        for m in session.messages[baseline_index:]:
            if m.role == "user":
                return False
        return True

    return _check


async def _run_auto_approve(
    session_id: str,
    user_id: str | None,
    baseline_index: int,
) -> None:
    """Wait the server-side timeout and inject a synthetic approval if the
    user has not acted in the meantime.

    Limitation: this lives in the executor process; if the worker restarts
    during the wait, the pending approval is lost (the user falls back to
    manual approve). Restart-resilience would need a Redis-backed scheduler.

    Modify-mode caveat: clicking "Modify" stops the *client* timer, not this
    one. Users have ``AUTO_APPROVE_SERVER_SECONDS`` total to finish editing
    and click Approve, otherwise the server fires the default approval. A
    follow-up should add an explicit cancel endpoint.
    """
    try:
        await asyncio.sleep(AUTO_APPROVE_SERVER_SECONDS)

        approval = ChatMessage(role="user", content=AUTO_APPROVE_MESSAGE)
        result = await append_message_if(
            session_id=session_id,
            message=approval,
            predicate=_no_user_action_since(baseline_index),
        )
        if result is None:
            # User already acted (or the session is gone) — nothing to do.
            return

        # Local imports avoid a circular dependency between this module and
        # the executor / API stream registry packages.
        from backend.copilot import stream_registry
        from backend.copilot.executor.utils import enqueue_copilot_turn

        turn_id = str(uuid4())
        await stream_registry.create_session(
            session_id=session_id,
            user_id=user_id or "",
            tool_call_id="chat_stream",
            tool_name="chat",
            turn_id=turn_id,
        )
        await enqueue_copilot_turn(
            session_id=session_id,
            user_id=user_id,
            message=AUTO_APPROVE_MESSAGE,
            turn_id=turn_id,
            is_user_message=True,
        )
        logger.info("decompose_goal auto-approve fired for session %s", session_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "decompose_goal auto-approve task failed for session %s",
            session_id,
        )


def _schedule_auto_approve(
    session_id: str | None, user_id: str | None, session: ChatSession
) -> None:
    """Schedule the fire-and-forget auto-approve task for this session.

    The baseline is the current message-list length: any message that
    arrives at or after this index is "after the decomposition", so a
    user message there means the user (or a follow-up turn) has acted
    and the auto-approve should be skipped.
    """
    if not session_id:
        return
    baseline_index = len(session.messages)
    task = asyncio.create_task(_run_auto_approve(session_id, user_id, baseline_index))
    _auto_approve_tasks.add(task)
    task.add_done_callback(_auto_approve_tasks.discard)


class DecomposeGoalTool(BaseTool):
    """Tool for decomposing an agent goal into sub-instructions."""

    @property
    def name(self) -> str:
        return "decompose_goal"

    @property
    def description(self) -> str:
        return (
            "Break down an agent-building goal into logical sub-instructions. "
            "Each step maps to one task (e.g. add a block, wire connections, "
            "configure settings). ALWAYS call this before create_agent to show "
            "the user your plan and get approval."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The user's agent-building goal.",
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {
                                "type": "string",
                                "description": "Human-readable step description.",
                            },
                            "action": {
                                "type": "string",
                                "description": (
                                    "Action type: 'add_block', 'connect_blocks', "
                                    "'configure', 'add_input', 'add_output'."
                                ),
                                "enum": list(VALID_ACTIONS),
                            },
                            "block_name": {
                                "type": "string",
                                "description": "Block name if adding a block.",
                            },
                        },
                        "required": ["description", "action"],
                    },
                    "description": "List of sub-instructions for the plan.",
                },
            },
            "required": ["goal", "steps"],
        }

    async def _execute(
        self,
        user_id: str | None,
        session: ChatSession,
        goal: str | None = None,
        steps: list[Any] | None = None,
        **kwargs,
    ) -> ToolResponseBase:
        session_id = session.session_id if session else None

        if not goal:
            return ErrorResponse(
                message="Please provide a goal to decompose.",
                error="missing_goal",
                session_id=session_id,
            )

        if not steps:
            return ErrorResponse(
                message="Please provide at least one step in the plan.",
                error="missing_steps",
                session_id=session_id,
            )

        if len(steps) > MAX_STEPS:
            return ErrorResponse(
                message=f"Too many steps ({len(steps)}). Keep the plan to {MAX_STEPS} steps max.",
                error="too_many_steps",
                session_id=session_id,
            )

        decomposition_steps: list[DecompositionStepModel] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                return ErrorResponse(
                    message=f"Step {i + 1} is malformed — expected an object.",
                    error="invalid_step",
                    session_id=session_id,
                )
            description = step.get("description", "")
            if not description or not description.strip():
                return ErrorResponse(
                    message=f"Step {i + 1} is missing a description.",
                    error="empty_description",
                    session_id=session_id,
                )
            action = step.get("action", DEFAULT_ACTION)
            if action not in VALID_ACTIONS:
                action = DEFAULT_ACTION
            decomposition_steps.append(
                DecompositionStepModel(
                    step_id=f"step_{i + 1}",
                    description=description,
                    action=action,
                    block_name=step.get("block_name"),
                    status="pending",
                )
            )

        _schedule_auto_approve(session_id, user_id, session)

        return TaskDecompositionResponse(
            message=f"Here's the plan to build your agent ({len(decomposition_steps)} steps):",
            goal=goal,
            steps=decomposition_steps,
            step_count=len(decomposition_steps),
            requires_approval=True,
            auto_approve_seconds=AUTO_APPROVE_CLIENT_SECONDS,
            session_id=session_id,
        )
