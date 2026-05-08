from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.data.execution import (
    ExecutionStatus,
    update_graph_execution_stats,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [
        ExecutionStatus.FAILED,
        ExecutionStatus.COMPLETED,
        ExecutionStatus.TERMINATED,
    ],
)
async def test_terminal_transition_cascades_running_children(terminal_status):
    """When a graph_exec moves to a terminal state, its still-RUNNING child
    node_execs must be batch-updated to FAILED in the same call so we never
    leak the 'parent terminal, child running' invariant."""
    with patch("backend.data.execution.AgentGraphExecution") as mock_graph, patch(
        "backend.data.execution.AgentNodeExecution"
    ) as mock_node:
        mock_graph.prisma.return_value.update_many = AsyncMock()
        mock_graph.prisma.return_value.find_unique_or_raise = AsyncMock(
            return_value=MagicMock()
        )
        mock_node.prisma.return_value.update_many = AsyncMock()

        with patch(
            "backend.data.execution.GraphExecution.from_db",
            return_value=MagicMock(),
        ):
            await update_graph_execution_stats(
                graph_exec_id="ge-1",
                status=terminal_status,
            )

    mock_node.prisma.return_value.update_many.assert_awaited_once()
    where = mock_node.prisma.return_value.update_many.await_args.kwargs["where"]
    assert where["agentGraphExecutionId"] == "ge-1"
    assert where["executionStatus"] == ExecutionStatus.RUNNING.value


@pytest.mark.asyncio
async def test_non_terminal_transition_does_not_cascade():
    """Mid-flight status changes (RUNNING/QUEUED/REVIEW) must leave child rows alone."""
    with patch("backend.data.execution.AgentGraphExecution") as mock_graph, patch(
        "backend.data.execution.AgentNodeExecution"
    ) as mock_node:
        mock_graph.prisma.return_value.update_many = AsyncMock()
        mock_graph.prisma.return_value.find_unique_or_raise = AsyncMock(
            return_value=MagicMock()
        )
        mock_node.prisma.return_value.update_many = AsyncMock()

        with patch(
            "backend.data.execution.GraphExecution.from_db",
            return_value=MagicMock(),
        ):
            await update_graph_execution_stats(
                graph_exec_id="ge-1",
                status=ExecutionStatus.RUNNING,
            )

    mock_node.prisma.return_value.update_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_cascade_can_be_disabled_explicitly():
    """`cascade_running_children=False` is the escape hatch for callers that
    need to mark a graph terminal without touching children (e.g. resume flows)."""
    with patch("backend.data.execution.AgentGraphExecution") as mock_graph, patch(
        "backend.data.execution.AgentNodeExecution"
    ) as mock_node:
        mock_graph.prisma.return_value.update_many = AsyncMock()
        mock_graph.prisma.return_value.find_unique_or_raise = AsyncMock(
            return_value=MagicMock()
        )
        mock_node.prisma.return_value.update_many = AsyncMock()

        with patch(
            "backend.data.execution.GraphExecution.from_db",
            return_value=MagicMock(),
        ):
            await update_graph_execution_stats(
                graph_exec_id="ge-1",
                status=ExecutionStatus.FAILED,
                cascade_running_children=False,
            )

    mock_node.prisma.return_value.update_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_cascade_records_terminal_status_in_node_error():
    """The child error stamp should reference which terminal status caused it
    so we can tell deploy-time cancellations from billing failures from manual stops."""
    captured = {}

    async def capture_update(**kwargs):
        captured.update(kwargs)

    with patch("backend.data.execution.AgentGraphExecution") as mock_graph, patch(
        "backend.data.execution.AgentNodeExecution"
    ) as mock_node:
        mock_graph.prisma.return_value.update_many = AsyncMock()
        mock_graph.prisma.return_value.find_unique_or_raise = AsyncMock(
            return_value=MagicMock()
        )
        mock_node.prisma.return_value.update_many = AsyncMock(
            side_effect=capture_update
        )

        with patch(
            "backend.data.execution.GraphExecution.from_db",
            return_value=MagicMock(),
        ):
            await update_graph_execution_stats(
                graph_exec_id="ge-1",
                status=ExecutionStatus.TERMINATED,
            )

    data = captured.get("data") or {}
    stats_payload = data.get("stats") or {}
    error_msg = (
        stats_payload.get("error", "") if isinstance(stats_payload, dict) else ""
    )
    assert "terminated" in error_msg.lower()
