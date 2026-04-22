"""Tests for TaskTool (the schema stub).

The actual sub-agent execution is unit-tested alongside the baseline
service loop in ``baseline/service_unit_test.py`` because it requires the
baseline's LLM caller and tool executor closures. This file just verifies
the tool schema and that the fall-back path surfaces a loud error if the
service loop short-circuit ever gets bypassed.
"""

import pytest

from backend.copilot.model import ChatSession
from backend.copilot.tools.models import ErrorResponse
from backend.copilot.tools.task import TaskTool


@pytest.fixture()
def tool() -> TaskTool:
    return TaskTool()


@pytest.fixture()
def session() -> ChatSession:
    return ChatSession.new(user_id="test-user", dry_run=False)


def test_openai_schema_shape(tool: TaskTool):
    schema = tool.as_openai_tool()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "Task"
    params = schema["function"]["parameters"]
    assert sorted(params["required"]) == ["description", "prompt"]
    # ``subagent_type`` must remain optional (SDK parity) so models that
    # don't know about it don't break schema validation.
    assert "subagent_type" in params["properties"]
    assert "subagent_type" not in params["required"]


@pytest.mark.asyncio
async def test_generic_dispatch_returns_error(tool: TaskTool, session: ChatSession):
    """If anything dispatches Task through BaseTool.execute instead of the
    baseline short-circuit, surface a loud error so the misconfig is
    visible in logs and transcripts."""
    result = await tool._execute(
        user_id="user",
        session=session,
        description="demo",
        prompt="do a thing",
    )

    assert isinstance(result, ErrorResponse)
    assert "baseline service loop" in result.message
