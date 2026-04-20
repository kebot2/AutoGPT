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
    _no_user_action_since,
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
# Predicate: _no_user_action_since
# ---------------------------------------------------------------------------


def test_predicate_passes_when_no_user_messages_after_baseline():
    session = make_session(_USER_ID)
    # Two pre-existing messages (indices 0, 1).
    session.messages.append(ChatMessage(role="user", content="initial"))
    session.messages.append(ChatMessage(role="assistant", content="tool call"))
    # Tool result lands at index 2 — this is what the executor appends after
    # _execute returns. baseline_index was captured at 2 inside _execute.
    session.messages.append(ChatMessage(role="tool", content="{...}"))
    assert _no_user_action_since(2)(session) is True


def test_predicate_rejects_when_user_message_after_baseline():
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="initial"))
    session.messages.append(ChatMessage(role="assistant", content="tool call"))
    session.messages.append(ChatMessage(role="tool", content="{...}"))
    session.messages.append(ChatMessage(role="user", content="Approved"))
    assert _no_user_action_since(2)(session) is False


def test_predicate_ignores_assistant_messages_after_baseline():
    """Only user messages count as 'user action' — assistant messages are
    just the LLM continuing on its own."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="initial"))
    session.messages.append(ChatMessage(role="assistant", content="tool call"))
    session.messages.append(ChatMessage(role="tool", content="{...}"))
    session.messages.append(ChatMessage(role="assistant", content="summary"))
    assert _no_user_action_since(2)(session) is True


def test_predicate_handles_messages_with_none_sequence():
    """Regression: the previous sequence-based predicate ignored messages
    whose sequence was None (which is what cached/in-memory messages have
    until they're round-tripped through the DB), causing the auto-approve
    to fire after the user had already manually approved. The new
    index-based predicate must catch user messages regardless of sequence.
    """
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="initial"))
    session.messages.append(ChatMessage(role="assistant", content="tool call"))
    session.messages.append(ChatMessage(role="tool", content="{...}"))
    # Sequence intentionally None — the cache often returns this state.
    user_msg = ChatMessage(role="user", content="Approved", sequence=None)
    session.messages.append(user_msg)
    assert user_msg.sequence is None
    assert _no_user_action_since(2)(session) is False


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


def test_needs_approval_blocks_when_last_user_is_not_approval():
    """Even with a decompose_goal earlier, a fresh non-approval user message
    starts a new build flow that requires its own decomposition."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build v1"))
    session.messages.append(
        ChatMessage(role="assistant", content="", tool_calls=[_decompose_tool_call()])
    )
    session.messages.append(ChatMessage(role="tool", content="{plan v1}"))
    session.messages.append(ChatMessage(role="user", content="Approved"))
    session.messages.append(ChatMessage(role="assistant", content="agent built."))
    # User asks for a second build — LLM must call decompose_goal again.
    session.messages.append(ChatMessage(role="user", content="Now build v2"))
    assert needs_build_plan_approval(session) is True


def test_needs_approval_allows_when_user_approved_after_decompose():
    """User said "Approved" after a decompose_goal → build may proceed."""
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


def test_needs_approval_allows_modified_approval():
    """Approved with modifications also counts as approval."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    session.messages.append(
        ChatMessage(role="assistant", content="", tool_calls=[_decompose_tool_call()])
    )
    session.messages.append(ChatMessage(role="tool", content="{plan}"))
    session.messages.append(
        ChatMessage(
            role="user",
            content="Approved with modifications. Please build the agent following these steps: ...",
        )
    )
    assert needs_build_plan_approval(session) is False


def test_needs_approval_blocks_same_turn_decompose_and_build():
    """LLM calls decompose_goal then immediately tries create_agent in the
    same turn — the last user message is still the original build request,
    not an approval."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    session.messages.append(
        ChatMessage(role="assistant", content="", tool_calls=[_decompose_tool_call()])
    )
    session.messages.append(ChatMessage(role="tool", content="{plan}"))
    # No user message yet — still mid-countdown.
    assert needs_build_plan_approval(session) is True


def test_needs_approval_blocks_approval_without_prior_decompose():
    """User spontaneously says "Approved" but no decompose_goal was ever
    called — the LLM did not show a plan, so the gate stays closed."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Approved"))
    assert needs_build_plan_approval(session) is True


def test_needs_approval_case_insensitive():
    """Approval detection is case-insensitive."""
    session = make_session(_USER_ID)
    session.messages.append(ChatMessage(role="user", content="Build me an agent"))
    session.messages.append(
        ChatMessage(role="assistant", content="", tool_calls=[_decompose_tool_call()])
    )
    session.messages.append(ChatMessage(role="tool", content="{plan}"))
    session.messages.append(ChatMessage(role="user", content="APPROVED, go."))
    assert needs_build_plan_approval(session) is False


# ---------------------------------------------------------------------------
# Server-side auto-approve task — full flow
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
async def test_auto_approve_fires_when_user_idle():
    """When no user message is appended after the baseline sequence, the
    task should append the synthetic approval and enqueue a new turn."""
    session_id = "session-auto-approve-idle"

    captured_message = {}

    async def fake_append_message_if(session_id, message, predicate):
        captured_message["msg"] = message
        return make_session(_USER_ID)

    fake_enqueue = AsyncMock()
    fake_create_session = AsyncMock()

    with (
        _stub_redis(),
        patch(
            "backend.copilot.tools.decompose_goal.append_message_if",
            new=fake_append_message_if,
        ),
        patch(
            "backend.copilot.tools.decompose_goal.AUTO_APPROVE_SERVER_SECONDS",
            0,
        ),
        patch(
            "backend.copilot.executor.utils.enqueue_copilot_turn",
            new=fake_enqueue,
        ),
        patch(
            "backend.copilot.stream_registry.create_session",
            new=fake_create_session,
        ),
    ):
        await decompose_goal_module._run_auto_approve(
            session_id=session_id,
            user_id=_USER_ID,
            baseline_index=5,
        )

    assert captured_message["msg"].role == "user"
    assert captured_message["msg"].content == "Approved. Please build the agent."
    fake_create_session.assert_awaited_once()
    fake_enqueue.assert_awaited_once()
    assert fake_enqueue.await_args is not None
    enqueue_kwargs = fake_enqueue.await_args.kwargs
    assert enqueue_kwargs["session_id"] == session_id
    assert enqueue_kwargs["message"] == "Approved. Please build the agent."
    assert enqueue_kwargs["is_user_message"] is True


@pytest.mark.asyncio
async def test_auto_approve_skips_when_user_already_acted():
    """If append_message_if returns None (predicate rejected because the
    user already sent a message), no turn should be enqueued."""
    fake_append_message_if = AsyncMock(return_value=None)
    fake_enqueue = AsyncMock()
    fake_create_session = AsyncMock()

    with (
        _stub_redis(),
        patch(
            "backend.copilot.tools.decompose_goal.append_message_if",
            new=fake_append_message_if,
        ),
        patch(
            "backend.copilot.tools.decompose_goal.AUTO_APPROVE_SERVER_SECONDS",
            0,
        ),
        patch(
            "backend.copilot.executor.utils.enqueue_copilot_turn",
            new=fake_enqueue,
        ),
        patch(
            "backend.copilot.stream_registry.create_session",
            new=fake_create_session,
        ),
    ):
        await decompose_goal_module._run_auto_approve(
            session_id="session-acted",
            user_id=_USER_ID,
            baseline_index=5,
        )

    fake_append_message_if.assert_awaited_once()
    fake_enqueue.assert_not_awaited()
    fake_create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_approve_swallows_unexpected_errors():
    """A failure inside the task must never propagate — the worker should
    keep running."""

    async def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    with (
        _stub_redis(),
        patch(
            "backend.copilot.tools.decompose_goal.append_message_if",
            new=boom,
        ),
        patch(
            "backend.copilot.tools.decompose_goal.AUTO_APPROVE_SERVER_SECONDS",
            0,
        ),
    ):
        # Should not raise.
        await decompose_goal_module._run_auto_approve(
            session_id="session-error",
            user_id=_USER_ID,
            baseline_index=0,
        )


@pytest.mark.asyncio
async def test_schedule_auto_approve_creates_task(monkeypatch):
    """_schedule_auto_approve should add a task to the tracking set and
    auto-remove it on completion. The baseline passed to _run_auto_approve
    must be the current message-list length at schedule time."""
    monkeypatch.setattr(decompose_goal_module, "AUTO_APPROVE_SERVER_SECONDS", 0)
    fake_run = AsyncMock()
    monkeypatch.setattr(decompose_goal_module, "_run_auto_approve", fake_run)
    monkeypatch.setattr(
        decompose_goal_module,
        "get_redis_async",
        AsyncMock(return_value=_FakeRedisNoCancelFlag()),
    )

    session = make_session(_USER_ID)
    # make_session pre-populates 1 message (guide_read). Add 2 more.
    session.messages.append(ChatMessage(role="user", content="initial"))
    session.messages.append(ChatMessage(role="assistant", content="tool call"))
    expected_baseline = len(session.messages)

    await _REAL_SCHEDULE_AUTO_APPROVE(
        session_id="session-schedule",
        user_id=_USER_ID,
        session=session,
    )

    # Wait for the scheduled task to complete.
    await asyncio.sleep(0)
    while decompose_goal_module._pending_auto_approvals:
        await asyncio.sleep(0)

    fake_run.assert_awaited_once_with("session-schedule", _USER_ID, expected_baseline)


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
