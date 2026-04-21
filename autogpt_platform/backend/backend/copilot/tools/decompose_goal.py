"""DecomposeGoalTool - Breaks agent-building goals into sub-instructions."""

import asyncio
import logging
from typing import Any

from backend.copilot.model import ChatSession
from backend.data.redis_client import get_redis_async

from .base import BaseTool
from .models import (
    DecompositionStepModel,
    ErrorResponse,
    TaskDecompositionResponse,
    ToolResponseBase,
)

logger = logging.getLogger(__name__)

DEFAULT_ACTION = "add_block"
VALID_ACTIONS = {"add_block", "connect_blocks", "configure", "add_input", "add_output"}

# Auto-approve countdown — the frontend reads ``auto_approve_seconds`` from the
# tool response and runs the visible countdown (60s). The server fires 5s later
# as a fallback for the "user closed the tab" case. The 5s gap ensures the
# client always fires first when present, creating the SSE subscription that
# lets the user see the build in real-time. When the server wakes at 65s, it
# checks the predicate and skips (the client's message is already there).
AUTO_APPROVE_CLIENT_SECONDS = 60
AUTO_APPROVE_SERVER_GRACE_SECONDS = 5
AUTO_APPROVE_SERVER_SECONDS = (
    AUTO_APPROVE_CLIENT_SECONDS + AUTO_APPROVE_SERVER_GRACE_SECONDS
)
AUTO_APPROVE_MESSAGE = "Approved. Please build the agent."

# Redis key prefix for cross-process cancel signalling. The cancel
# endpoint (AgentServer process) SETs the key; _run_auto_approve
# (CoPilotExecutor process) checks it before firing.
_CANCEL_KEY_PREFIX = "copilot:cancel_auto_approve:"
_CANCEL_KEY_TTL_SECONDS = AUTO_APPROVE_SERVER_SECONDS + 30

# In-process dict for best-effort cancel when both the cancel call and
# the asyncio task happen to live in the same process (single-worker).
_pending_auto_approvals: dict[str, asyncio.Task] = {}


def needs_build_plan_approval(session: ChatSession) -> bool:
    """Return True if the current build must be blocked pending user response.

    Enforces the "STOP — do not proceed until the user responds" gate from
    ``agent_generation_guide.md`` at the *code* level. Natural-language
    instruction alone is not enough — the LLM has been observed calling
    ``decompose_goal`` and ``create_agent`` in the same turn.

    Rule: a ``decompose_goal`` tool call must exist in the session AND at
    least one user message must appear after it. The gate does NOT check
    *what* the user said — the LLM interprets the intent (build, modify,
    or reject). The gate only blocks same-turn builds where the user hasn't
    responded at all.

    - No decompose_goal in session → block (must decompose first).
    - decompose_goal called but no user response yet → block.
    - Any user message after decompose_goal → allow (LLM decides).
    """
    # Walk backward to find the latest decompose_goal tool call.
    decompose_idx = -1
    for i in range(len(session.messages) - 1, -1, -1):
        msg = session.messages[i]
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                name = (tc.get("function") or {}).get("name") or tc.get("name")
                if name == "decompose_goal":
                    decompose_idx = i
                    break
            if decompose_idx >= 0:
                break

    if decompose_idx < 0:
        return True

    # Any user message after the decompose_goal call unblocks the gate.
    for msg in session.messages[decompose_idx + 1 :]:
        if msg.role == "user":
            return False

    return True


async def _run_auto_approve(session_id: str, user_id: str | None) -> None:
    """Wait the server-side timeout and dispatch the approval via
    ``run_copilot_turn_via_queue`` — the canonical helper that queues the
    message if a turn is already in flight, or starts a new turn if idle.

    Cancelled when the user clicks "Modify" (via ``cancel_auto_approve``).
    """
    try:
        await asyncio.sleep(AUTO_APPROVE_SERVER_SECONDS)

        # Check the cross-process cancel flag set by cancel_auto_approve().
        redis = await get_redis_async()
        if await redis.get(f"{_CANCEL_KEY_PREFIX}{session_id}"):
            logger.info(
                "decompose_goal auto-approve skipped (cancelled) for session %s",
                session_id,
            )
            return

        # Skip if a turn is already in flight — the client already sent
        # "Approved" and started the build. Only fire when the session is
        # idle (client closed the tab).
        from backend.copilot.pending_message_helpers import is_turn_in_flight

        if await is_turn_in_flight(session_id):
            logger.info(
                "decompose_goal auto-approve skipped (turn in flight) for session %s",
                session_id,
            )
            return

        from backend.copilot.sdk.session_waiter import run_copilot_turn_via_queue

        outcome, result = await run_copilot_turn_via_queue(
            session_id=session_id,
            user_id=user_id or "",
            message=AUTO_APPROVE_MESSAGE,
            timeout=0,
            tool_call_id="auto_approve",
            tool_name="decompose_goal_auto_approve",
        )
        logger.info(
            "decompose_goal auto-approve fired for session %s (outcome=%s)",
            session_id,
            outcome,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "decompose_goal auto-approve task failed for session %s",
            session_id,
        )


async def cancel_auto_approve(session_id: str) -> bool:
    """Cancel the pending auto-approve task for a session.

    Called by the ``/sessions/{session_id}/cancel-auto-approve`` endpoint
    when the user clicks "Modify" in the build-plan UI.

    Uses **two** cancellation channels:
    1. **Redis flag** (cross-process) — the executor checks this before
       firing. Works even when the cancel endpoint runs in the AgentServer
       process and the asyncio task lives in the CoPilotExecutor process.
    2. **In-process task cancel** (best-effort) — if both happen to share
       the same process, cancels the asyncio task directly.
    """
    redis = await get_redis_async()
    await redis.set(
        f"{_CANCEL_KEY_PREFIX}{session_id}",
        "1",
        ex=_CANCEL_KEY_TTL_SECONDS,
    )
    logger.info(
        "decompose_goal auto-approve cancel flag set for session %s", session_id
    )

    # Best-effort in-process cancel (no-op if the task is in another process).
    task = _pending_auto_approvals.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()

    return True


async def _schedule_auto_approve(
    session_id: str | None, user_id: str | None, session: ChatSession
) -> None:
    """Schedule the fire-and-forget auto-approve task for this session."""
    if not session_id:
        return
    # Cancel any existing pending approval for this session (e.g. if the
    # LLM called decompose_goal twice in one turn).
    old_task = _pending_auto_approvals.pop(session_id, None)
    if old_task is not None and not old_task.done():
        old_task.cancel()
    # Clear any stale Redis cancel flag from a previous Modify click so
    # the new auto-approve task isn't incorrectly suppressed.
    redis = await get_redis_async()
    await redis.delete(f"{_CANCEL_KEY_PREFIX}{session_id}")
    task = asyncio.create_task(_run_auto_approve(session_id, user_id))
    _pending_auto_approvals[session_id] = task
    # Only remove from dict if this task is still the current one — a
    # cancelled old task's callback must not clobber a newly-scheduled one.
    task.add_done_callback(
        lambda t: (
            _pending_auto_approvals.pop(session_id, None)
            if _pending_auto_approvals.get(session_id) is t
            else None
        )
    )


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

        await _schedule_auto_approve(session_id, user_id, session)

        return TaskDecompositionResponse(
            message=f"Here's the plan to build your agent ({len(decomposition_steps)} steps):",
            goal=goal,
            steps=decomposition_steps,
            step_count=len(decomposition_steps),
            requires_approval=True,
            auto_approve_seconds=AUTO_APPROVE_CLIENT_SECONDS,
            session_id=session_id,
        )
