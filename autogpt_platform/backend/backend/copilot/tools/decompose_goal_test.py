"""Unit tests for DecomposeGoalTool."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from backend.copilot.model import ChatMessage

from . import decompose_goal as decompose_goal_module
from ._test_data import make_session
from .decompose_goal import (
    AUTO_APPROVE_CLIENT_SECONDS,
    DEFAULT_ACTION,
    DecomposeGoalTool,
    cancel_auto_approve,
    needs_build_plan_approval,
)
from .models import ErrorResponse, TaskDecompositionResponse

# Captured before the autouse fixture stubs the real scheduler.
_REAL_SCHEDULE_AUTO_APPROVE = decompose_goal_module._schedule_auto_approve

_USER_ID = "test-user-decompose-goal"

_VALID_STEPS = [
    {"description": "Add input block", "action": "add_input"},
    {
        "description": "Add AI summarizer block",
        "action": "add_block",
        "block_name": "AI Text Generator",
    },
    {"description": "Connect blocks together", "action": "connect_blocks"},
]


@pytest.fixture(autouse=True)
def _stub_auto_approve_scheduler():
    """The existing happy-path tests don't have a database; stub the
    fire-and-forget scheduler so they don't kick off real timers that try to
    hit Redis/Postgres. Tests that exercise auto-approve override this with
    their own patches inside the test body."""

    async def _noop(*a, **kw):
        pass

    with patch.object(decompose_goal_module, "_schedule_auto_approve", _noop):
        yield


@pytest.fixture()
def tool() -> DecomposeGoalTool:
    return DecomposeGoalTool()


@pytest.fixture()
def session():
    return make_session(_USER_ID)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path(tool: DecomposeGoalTool, session):
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build a news summarizer agent",
        steps=_VALID_STEPS,
    )

    assert isinstance(result, TaskDecompositionResponse)
    assert result.goal == "Build a news summarizer agent"
    assert len(result.steps) == 3
    assert result.step_count == 3
    assert result.requires_approval is True
    assert result.steps[0].step_id == "step_1"
    assert result.steps[0].description == "Add input block"
    assert result.steps[1].block_name == "AI Text Generator"


@pytest.mark.asyncio
async def test_step_count_matches_steps(tool: DecomposeGoalTool, session):
    """TaskDecompositionResponse.step_count must always equal len(steps)."""
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Simple agent",
        steps=[{"description": "Only step", "action": "add_block"}],
    )
    assert isinstance(result, TaskDecompositionResponse)
    assert result.step_count == len(result.steps)


@pytest.mark.asyncio
async def test_requires_approval_always_true(tool: DecomposeGoalTool, session):
    """requires_approval must always be True regardless of kwargs."""
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=_VALID_STEPS,
        require_approval=False,  # should be ignored
    )
    assert isinstance(result, TaskDecompositionResponse)
    assert result.requires_approval is True


@pytest.mark.asyncio
async def test_invalid_action_defaults_to_add_block(tool: DecomposeGoalTool, session):
    """Unknown action values are coerced to DEFAULT_ACTION."""
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=[{"description": "Do something weird", "action": "fly_to_moon"}],
    )
    assert isinstance(result, TaskDecompositionResponse)
    assert result.steps[0].action == DEFAULT_ACTION


@pytest.mark.asyncio
async def test_block_name_optional(tool: DecomposeGoalTool, session):
    """Steps without block_name should succeed with block_name=None."""
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Agent with no block name",
        steps=[{"description": "Configure the agent", "action": "configure"}],
    )
    assert isinstance(result, TaskDecompositionResponse)
    assert result.steps[0].block_name is None


# ---------------------------------------------------------------------------
# Validation — missing inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_goal_returns_error(tool: DecomposeGoalTool, session):
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal=None,
        steps=_VALID_STEPS,
    )
    assert isinstance(result, ErrorResponse)
    assert result.error == "missing_goal"


@pytest.mark.asyncio
async def test_empty_goal_returns_error(tool: DecomposeGoalTool, session):
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="",
        steps=_VALID_STEPS,
    )
    assert isinstance(result, ErrorResponse)
    assert result.error == "missing_goal"


@pytest.mark.asyncio
async def test_missing_steps_returns_error(tool: DecomposeGoalTool, session):
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=None,
    )
    assert isinstance(result, ErrorResponse)
    assert result.error == "missing_steps"


@pytest.mark.asyncio
async def test_empty_steps_returns_error(tool: DecomposeGoalTool, session):
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=[],
    )
    assert isinstance(result, ErrorResponse)
    assert result.error == "missing_steps"


# ---------------------------------------------------------------------------
# Validation — malformed step items
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_dict_step_returns_error(tool: DecomposeGoalTool, session):
    """A step that is not a dict should return an error."""
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=["not a dict"],  # type: ignore[list-item]
    )
    assert isinstance(result, ErrorResponse)
    assert result.error == "invalid_step"


@pytest.mark.asyncio
async def test_step_with_empty_description_returns_error(
    tool: DecomposeGoalTool, session
):
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=[{"description": "", "action": "add_block"}],
    )
    assert isinstance(result, ErrorResponse)
    assert result.error == "empty_description"


@pytest.mark.asyncio
async def test_step_with_missing_description_returns_error(
    tool: DecomposeGoalTool, session
):
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=[{"action": "add_block"}],
    )
    assert isinstance(result, ErrorResponse)
    assert result.error == "empty_description"


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_ids_are_sequential(tool: DecomposeGoalTool, session):
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=_VALID_STEPS,
    )
    assert isinstance(result, TaskDecompositionResponse)
    for i, step in enumerate(result.steps):
        assert step.step_id == f"step_{i + 1}"


# ---------------------------------------------------------------------------
# auto_approve_seconds field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_includes_auto_approve_seconds(tool: DecomposeGoalTool, session):
    """The response carries the countdown so the frontend has a single
    source of truth instead of a hard-coded constant."""
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=_VALID_STEPS,
    )
    assert isinstance(result, TaskDecompositionResponse)
    assert result.auto_approve_seconds == AUTO_APPROVE_CLIENT_SECONDS


@pytest.mark.asyncio
async def test_response_includes_created_at(tool: DecomposeGoalTool, session):
    """created_at must be stamped at execution time so the client can
    compute remaining countdown when the user reopens the session."""
    before = datetime.now(UTC)
    result = await tool._execute(
        user_id=_USER_ID,
        session=session,
        goal="Build agent",
        steps=_VALID_STEPS,
    )
    after = datetime.now(UTC)

    assert isinstance(result, TaskDecompositionResponse)
    assert isinstance(result.created_at, datetime)
    # Stamped during the call.
    assert before <= result.created_at <= after


# ---------------------------------------------------------------------------
# needs_build_plan_approval — build-tool approval gate
# ---------------------------------------------------------------------------


def _decompose_tool_call() -> dict:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": "decompose_goal", "arguments": "{}"},
    }


def test_needs_approval_blocks_when_no_decompose_in_session():
    """LLM tries to build without calling decompose_goal at all."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    assert needs_build_plan_approval(session) is True


def test_needs_approval_allows_any_user_response():
    """Any user message after decompose_goal unblocks the gate."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    session.messages.append(
        ChatMessage(role="assistant", content="", tool_calls=[_decompose_tool_call()])
    )
    session.messages.append(ChatMessage(role="tool", content="{plan}"))
    session.messages.append(ChatMessage(role="user", content="Sure"))
    assert needs_build_plan_approval(session) is False


def test_needs_approval_allows_explicit_approval():
    """Explicit 'Approved' also passes (common from button/auto-approve)."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    session.messages.append(
        ChatMessage(role="assistant", content="", tool_calls=[_decompose_tool_call()])
    )
    session.messages.append(ChatMessage(role="tool", content="{plan}"))
    session.messages.append(
        ChatMessage(role="user", content="Approved. Please build the agent.")
    )
    assert needs_build_plan_approval(session) is False


def test_needs_approval_allows_modification_request():
    """User asking to modify the plan also passes — LLM decides what to do."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    session.messages.append(
        ChatMessage(role="assistant", content="", tool_calls=[_decompose_tool_call()])
    )
    session.messages.append(ChatMessage(role="tool", content="{plan}"))
    session.messages.append(
        ChatMessage(role="user", content="Change step 3 to use Gmail instead")
    )
    assert needs_build_plan_approval(session) is False


def test_needs_approval_blocks_same_turn_decompose_and_build():
    """LLM calls decompose_goal then immediately tries create_agent in the
    same turn — no user message after decompose_goal yet."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    session.messages.append(
        ChatMessage(role="assistant", content="", tool_calls=[_decompose_tool_call()])
    )
    session.messages.append(ChatMessage(role="tool", content="{plan}"))
    assert needs_build_plan_approval(session) is True


def test_needs_approval_blocks_without_prior_decompose():
    """No decompose_goal in session → must decompose first."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    assert needs_build_plan_approval(session) is True


# ---------------------------------------------------------------------------
# Server-side auto-approve task — uses run_copilot_turn_via_queue
# ---------------------------------------------------------------------------


class _FakeRedisNoCancelFlag:
    """Stub Redis that reports no cancel flag and ignores writes."""

    async def get(self, key):
        return None

    async def set(self, key, value, ex=None):
        pass

    async def delete(self, key):
        pass


def _stub_redis():
    """Patch get_redis_async to return a fake Redis (no real connection)."""
    return patch(
        "backend.copilot.tools.decompose_goal.get_redis_async",
        new=AsyncMock(return_value=_FakeRedisNoCancelFlag()),
    )


@pytest.mark.asyncio
async def test_auto_approve_dispatches_via_queue_helper():
    """_run_auto_approve should delegate to run_copilot_turn_via_queue."""
    fake_dispatch = AsyncMock(return_value=("completed", None))

    with (
        _stub_redis(),
        patch(
            "backend.copilot.sdk.session_waiter.run_copilot_turn_via_queue",
            new=fake_dispatch,
        ),
        patch(
            "backend.copilot.tools.decompose_goal.AUTO_APPROVE_SERVER_SECONDS",
            0,
        ),
    ):
        await decompose_goal_module._run_auto_approve("session-idle", _USER_ID)

    fake_dispatch.assert_awaited_once()
    call_kwargs = fake_dispatch.await_args.kwargs
    assert call_kwargs["session_id"] == "session-idle"
    assert call_kwargs["message"] == "Approved. Please build the agent."
    assert call_kwargs["timeout"] == 0
    assert call_kwargs["tool_name"] == "decompose_goal_auto_approve"


@pytest.mark.asyncio
async def test_auto_approve_swallows_unexpected_errors():
    """A failure inside the task must never propagate."""

    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    with (
        _stub_redis(),
        patch(
            "backend.copilot.sdk.session_waiter.run_copilot_turn_via_queue",
            new=boom,
        ),
        patch(
            "backend.copilot.tools.decompose_goal.AUTO_APPROVE_SERVER_SECONDS",
            0,
        ),
    ):
        await decompose_goal_module._run_auto_approve("session-error", None)


@pytest.mark.asyncio
async def test_schedule_auto_approve_creates_task(monkeypatch):
    """_schedule_auto_approve should add a task to the tracking dict."""
    monkeypatch.setattr(decompose_goal_module, "AUTO_APPROVE_SERVER_SECONDS", 0)
    fake_run = AsyncMock()
    monkeypatch.setattr(decompose_goal_module, "_run_auto_approve", fake_run)
    monkeypatch.setattr(
        decompose_goal_module,
        "get_redis_async",
        AsyncMock(return_value=_FakeRedisNoCancelFlag()),
    )

    session = make_session(_USER_ID)

    await _REAL_SCHEDULE_AUTO_APPROVE(
        session_id="session-schedule",
        user_id=_USER_ID,
        session=session,
    )

    await asyncio.sleep(0)
    while decompose_goal_module._pending_auto_approvals:
        await asyncio.sleep(0)

    fake_run.assert_awaited_once_with("session-schedule", _USER_ID)


@pytest.mark.asyncio
async def test_schedule_auto_approve_no_op_without_session_id():
    """Empty session id should be a no-op (defensive)."""
    session = make_session(_USER_ID)
    await decompose_goal_module._schedule_auto_approve(
        session_id=None, user_id=_USER_ID, session=session
    )
    assert len(decompose_goal_module._pending_auto_approvals) == 0


# ---------------------------------------------------------------------------
# cancel_auto_approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_auto_approve_sets_redis_flag_and_cancels_task(monkeypatch):
    """cancel_auto_approve should set a Redis cancel flag AND cancel the
    in-process task. Returns True always (Redis flag is authoritative)."""
    monkeypatch.setattr(decompose_goal_module, "AUTO_APPROVE_SERVER_SECONDS", 999)
    fake_run = AsyncMock()
    monkeypatch.setattr(decompose_goal_module, "_run_auto_approve", fake_run)

    captured_redis_calls: list[tuple] = []

    class FakeRedis:
        async def set(self, key, value, ex=None):
            captured_redis_calls.append(("set", key, value, ex))

        async def get(self, key):
            return None

        async def delete(self, key):
            pass

    monkeypatch.setattr(
        decompose_goal_module, "get_redis_async", AsyncMock(return_value=FakeRedis())
    )

    session = make_session(_USER_ID)
    await _REAL_SCHEDULE_AUTO_APPROVE(
        session_id="session-cancel-test",
        user_id=_USER_ID,
        session=session,
    )

    assert "session-cancel-test" in decompose_goal_module._pending_auto_approvals
    result = await cancel_auto_approve("session-cancel-test")
    assert result is True
    assert "session-cancel-test" not in decompose_goal_module._pending_auto_approvals
    assert len(captured_redis_calls) == 1
    assert captured_redis_calls[0][0] == "set"
    assert "session-cancel-test" in captured_redis_calls[0][1]


@pytest.mark.asyncio
async def test_cancel_auto_approve_returns_true_even_without_in_process_task(
    monkeypatch,
):
    """Even if no in-process task exists (e.g. task is in another process),
    cancel_auto_approve should still set the Redis flag and return True."""

    class FakeRedis:
        async def set(self, key, value, ex=None):
            pass

    monkeypatch.setattr(
        decompose_goal_module, "get_redis_async", AsyncMock(return_value=FakeRedis())
    )
    result = await cancel_auto_approve("nonexistent-session")
    assert result is True
