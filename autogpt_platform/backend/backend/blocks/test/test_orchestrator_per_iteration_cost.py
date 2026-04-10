"""Tests for OrchestratorBlock per-iteration cost charging.

The OrchestratorBlock in agent mode makes multiple LLM calls in a single
node execution. The executor uses ``Block.charge_per_llm_call`` to detect
this and charge ``base_cost * (llm_call_count - 1)`` extra credits after
the block completes.
"""

import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.blocks.orchestrator import OrchestratorBlock

# ── Class flag ──────────────────────────────────────────────────────


class TestChargePerLlmCallFlag:
    """OrchestratorBlock opts into per-LLM-call billing."""

    def test_orchestrator_opts_in(self):
        assert OrchestratorBlock.charge_per_llm_call is True

    def test_default_block_does_not_opt_in(self):
        from backend.blocks._base import Block

        assert Block.charge_per_llm_call is False


# ── charge_extra_iterations math ───────────────────────────────────


@pytest.fixture()
def fake_node_exec():
    node_exec = MagicMock()
    node_exec.user_id = "u"
    node_exec.graph_exec_id = "g"
    node_exec.graph_id = "g"
    node_exec.node_exec_id = "ne"
    node_exec.node_id = "n"
    node_exec.block_id = "b"
    node_exec.inputs = {}
    return node_exec


@pytest.fixture()
def patched_processor(monkeypatch):
    """ExecutionProcessor with stubbed db client / block lookup helpers.

    Returns the processor and a list of credit amounts spent so tests can
    assert on what was charged.
    """
    from backend.executor import manager

    spent: list[int] = []

    class FakeDb:
        def spend_credits(self, *, user_id, cost, metadata):
            spent.append(cost)
            return 1000  # remaining balance

    fake_block = MagicMock()
    fake_block.name = "FakeBlock"

    monkeypatch.setattr(manager, "get_db_client", lambda: FakeDb())
    monkeypatch.setattr(manager, "get_block", lambda block_id: fake_block)
    monkeypatch.setattr(
        manager,
        "block_usage_cost",
        lambda block, input_data, **_kw: (10, {"model": "claude-sonnet-4-6"}),
    )

    proc = manager.ExecutionProcessor.__new__(manager.ExecutionProcessor)
    return proc, spent


class TestChargeExtraIterations:
    def test_zero_extra_iterations_charges_nothing(
        self, patched_processor, fake_node_exec
    ):
        proc, spent = patched_processor
        cost, balance = proc.charge_extra_iterations(fake_node_exec, extra_iterations=0)
        assert cost == 0
        assert balance == 0
        assert spent == []

    def test_extra_iterations_multiplies_base_cost(
        self, patched_processor, fake_node_exec
    ):
        proc, spent = patched_processor
        cost, balance = proc.charge_extra_iterations(fake_node_exec, extra_iterations=4)
        assert cost == 40  # 4 × 10
        assert balance == 1000
        assert spent == [40]

    def test_negative_extra_iterations_charges_nothing(
        self, patched_processor, fake_node_exec
    ):
        proc, spent = patched_processor
        cost, balance = proc.charge_extra_iterations(
            fake_node_exec, extra_iterations=-1
        )
        assert cost == 0
        assert balance == 0
        assert spent == []

    def test_capped_at_max(self, monkeypatch, fake_node_exec):
        """Runaway llm_call_count is capped at _MAX_EXTRA_ITERATIONS."""
        from backend.executor import manager

        spent: list[int] = []

        class FakeDb:
            def spend_credits(self, *, user_id, cost, metadata):
                spent.append(cost)
                return 1000

        fake_block = MagicMock()
        fake_block.name = "FakeBlock"

        monkeypatch.setattr(manager, "get_db_client", lambda: FakeDb())
        monkeypatch.setattr(manager, "get_block", lambda block_id: fake_block)
        monkeypatch.setattr(
            manager,
            "block_usage_cost",
            lambda block, input_data, **_kw: (10, {}),
        )

        proc = manager.ExecutionProcessor.__new__(manager.ExecutionProcessor)
        cap = manager.ExecutionProcessor._MAX_EXTRA_ITERATIONS
        cost, _ = proc.charge_extra_iterations(
            fake_node_exec, extra_iterations=cap * 100
        )
        # Charged at most cap × 10
        assert cost == cap * 10
        assert spent == [cap * 10]

    def test_zero_base_cost_skips_charge(self, monkeypatch, fake_node_exec):
        from backend.executor import manager

        spent: list[int] = []

        class FakeDb:
            def spend_credits(self, *, user_id, cost, metadata):
                spent.append(cost)
                return 0

        fake_block = MagicMock()
        fake_block.name = "FakeBlock"

        monkeypatch.setattr(manager, "get_db_client", lambda: FakeDb())
        monkeypatch.setattr(manager, "get_block", lambda block_id: fake_block)
        monkeypatch.setattr(
            manager, "block_usage_cost", lambda block, input_data, **_kw: (0, {})
        )

        proc = manager.ExecutionProcessor.__new__(manager.ExecutionProcessor)
        cost, balance = proc.charge_extra_iterations(fake_node_exec, extra_iterations=4)
        assert cost == 0
        assert balance == 0
        assert spent == []

    def test_block_not_found_skips_charge(self, monkeypatch, fake_node_exec):
        from backend.executor import manager

        spent: list[int] = []

        class FakeDb:
            def spend_credits(self, *, user_id, cost, metadata):
                spent.append(cost)
                return 0

        monkeypatch.setattr(manager, "get_db_client", lambda: FakeDb())
        monkeypatch.setattr(manager, "get_block", lambda block_id: None)
        monkeypatch.setattr(
            manager, "block_usage_cost", lambda block, input_data, **_kw: (10, {})
        )

        proc = manager.ExecutionProcessor.__new__(manager.ExecutionProcessor)
        cost, balance = proc.charge_extra_iterations(fake_node_exec, extra_iterations=3)
        assert cost == 0
        assert balance == 0
        assert spent == []

    def test_propagates_insufficient_balance_error(self, monkeypatch, fake_node_exec):
        """Out-of-credits errors must propagate, not be silently swallowed."""
        from backend.executor import manager
        from backend.util.exceptions import InsufficientBalanceError

        class FakeDb:
            def spend_credits(self, *, user_id, cost, metadata):
                raise InsufficientBalanceError(
                    user_id=user_id,
                    message="Insufficient balance",
                    balance=0,
                    amount=cost,
                )

        fake_block = MagicMock()
        fake_block.name = "FakeBlock"

        monkeypatch.setattr(manager, "get_db_client", lambda: FakeDb())
        monkeypatch.setattr(manager, "get_block", lambda block_id: fake_block)
        monkeypatch.setattr(
            manager, "block_usage_cost", lambda block, input_data, **_kw: (10, {})
        )

        proc = manager.ExecutionProcessor.__new__(manager.ExecutionProcessor)
        with pytest.raises(InsufficientBalanceError):
            proc.charge_extra_iterations(fake_node_exec, extra_iterations=4)


# ── charge_node_usage ──────────────────────────────────────────────


class TestChargeNodeUsage:
    """charge_node_usage delegates to _charge_usage with execution_count=0."""

    def test_delegates_with_zero_execution_count(self, monkeypatch, fake_node_exec):
        """Nested tool charges should NOT inflate the per-execution counter."""
        from backend.executor import manager

        captured: dict = {}

        def fake_charge_usage(self, node_exec, execution_count):
            captured["execution_count"] = execution_count
            captured["node_exec"] = node_exec
            return (5, 100)

        monkeypatch.setattr(
            manager.ExecutionProcessor, "_charge_usage", fake_charge_usage
        )

        proc = manager.ExecutionProcessor.__new__(manager.ExecutionProcessor)
        cost, balance = proc.charge_node_usage(fake_node_exec)
        assert cost == 5
        assert balance == 100
        assert captured["execution_count"] == 0


# ── on_node_execution charging gate ────────────────────────────────


class _FakeNode:
    """Minimal stand-in for a ``Node`` object with a block attribute."""

    def __init__(self, charge_per_llm_call: bool, block_name: str = "FakeBlock"):
        self.block = MagicMock()
        self.block.charge_per_llm_call = charge_per_llm_call
        self.block.name = block_name


class _FakeExecContext:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run


def _make_node_exec(dry_run: bool = False) -> MagicMock:
    """Build a NodeExecutionEntry-like mock for on_node_execution tests."""
    ne = MagicMock()
    ne.user_id = "u"
    ne.graph_id = "g"
    ne.graph_exec_id = "ge"
    ne.node_id = "n"
    ne.node_exec_id = "ne"
    ne.block_id = "b"
    ne.inputs = {}
    ne.execution_context = _FakeExecContext(dry_run=dry_run)
    return ne


@pytest.fixture()
def gated_processor(monkeypatch):
    """ExecutionProcessor with on_node_execution's downstream calls stubbed.

    Lets tests flip the four gate conditions (status, charge_per_llm_call,
    llm_call_count, dry_run) and observe whether charge_extra_iterations
    was called.
    """
    from backend.data.execution import ExecutionStatus
    from backend.data.model import NodeExecutionStats
    from backend.executor import manager

    calls: dict[str, list] = {
        "charge_extra_iterations": [],
        "handle_low_balance": [],
        "handle_insufficient_funds_notif": [],
    }

    # Stub node lookup + DB client so the wrapper doesn't touch real infra.
    fake_db = MagicMock()
    fake_db.get_node = AsyncMock(return_value=_FakeNode(charge_per_llm_call=True))
    monkeypatch.setattr(manager, "get_db_async_client", lambda: fake_db)
    monkeypatch.setattr(manager, "get_db_client", lambda: fake_db)
    # get_block is called by LogMetadata construction in on_node_execution.
    monkeypatch.setattr(
        manager,
        "get_block",
        lambda block_id: MagicMock(name="FakeBlock"),
    )
    # Persistence + cost logging are not under test here.
    monkeypatch.setattr(
        manager,
        "async_update_node_execution_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        manager,
        "async_update_graph_execution_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        manager,
        "log_system_credential_cost",
        AsyncMock(return_value=None),
    )

    proc = manager.ExecutionProcessor.__new__(manager.ExecutionProcessor)

    # Control the status returned by the inner execution call.
    inner_result = {"status": ExecutionStatus.COMPLETED, "llm_call_count": 3}

    async def fake_inner(
        self,
        *,
        node,
        node_exec,
        node_exec_progress,
        stats,
        db_client,
        log_metadata,
        nodes_input_masks=None,
        nodes_to_skip=None,
    ):
        stats.llm_call_count = inner_result["llm_call_count"]
        return MagicMock(wall_time=0.1, cpu_time=0.1), inner_result["status"]

    monkeypatch.setattr(
        manager.ExecutionProcessor,
        "_on_node_execution",
        fake_inner,
    )

    def fake_charge_extra(self, node_exec, extra_iterations):
        calls["charge_extra_iterations"].append(extra_iterations)
        return (extra_iterations * 10, 500)

    monkeypatch.setattr(
        manager.ExecutionProcessor,
        "charge_extra_iterations",
        fake_charge_extra,
    )

    def fake_low_balance(self, **kwargs):
        calls["handle_low_balance"].append(kwargs)

    monkeypatch.setattr(
        manager.ExecutionProcessor,
        "_handle_low_balance",
        fake_low_balance,
    )

    def fake_notif(self, db_client, user_id, graph_id, e):
        calls["handle_insufficient_funds_notif"].append(
            {"user_id": user_id, "graph_id": graph_id, "error": e}
        )

    monkeypatch.setattr(
        manager.ExecutionProcessor,
        "_handle_insufficient_funds_notif",
        fake_notif,
    )

    return proc, calls, inner_result, fake_db, NodeExecutionStats


@pytest.mark.asyncio
async def test_on_node_execution_charges_extra_iterations_when_gate_passes(
    gated_processor,
):
    """COMPLETED + charge_per_llm_call + llm_call_count>1 + not dry_run → charged."""
    from backend.data.execution import ExecutionStatus

    proc, calls, inner, fake_db, _ = gated_processor
    inner["status"] = ExecutionStatus.COMPLETED
    inner["llm_call_count"] = 3  # → extra_iterations = 2
    fake_db.get_node = AsyncMock(return_value=_FakeNode(charge_per_llm_call=True))

    stats_pair = (
        MagicMock(
            node_count=0, nodes_cputime=0, nodes_walltime=0, cost=0, node_error_count=0
        ),
        threading.Lock(),
    )
    await proc.on_node_execution(
        node_exec=_make_node_exec(dry_run=False),
        node_exec_progress=MagicMock(),
        nodes_input_masks=None,
        graph_stats_pair=stats_pair,
    )
    assert calls["charge_extra_iterations"] == [2]


@pytest.mark.asyncio
async def test_on_node_execution_skips_when_status_not_completed(gated_processor):
    from backend.data.execution import ExecutionStatus

    proc, calls, inner, fake_db, _ = gated_processor
    inner["status"] = ExecutionStatus.FAILED
    inner["llm_call_count"] = 5
    fake_db.get_node = AsyncMock(return_value=_FakeNode(charge_per_llm_call=True))

    stats_pair = (
        MagicMock(
            node_count=0, nodes_cputime=0, nodes_walltime=0, cost=0, node_error_count=0
        ),
        threading.Lock(),
    )
    await proc.on_node_execution(
        node_exec=_make_node_exec(dry_run=False),
        node_exec_progress=MagicMock(),
        nodes_input_masks=None,
        graph_stats_pair=stats_pair,
    )
    assert calls["charge_extra_iterations"] == []


@pytest.mark.asyncio
async def test_on_node_execution_skips_when_charge_flag_false(gated_processor):
    from backend.data.execution import ExecutionStatus

    proc, calls, inner, fake_db, _ = gated_processor
    inner["status"] = ExecutionStatus.COMPLETED
    inner["llm_call_count"] = 5
    fake_db.get_node = AsyncMock(return_value=_FakeNode(charge_per_llm_call=False))

    stats_pair = (
        MagicMock(
            node_count=0, nodes_cputime=0, nodes_walltime=0, cost=0, node_error_count=0
        ),
        threading.Lock(),
    )
    await proc.on_node_execution(
        node_exec=_make_node_exec(dry_run=False),
        node_exec_progress=MagicMock(),
        nodes_input_masks=None,
        graph_stats_pair=stats_pair,
    )
    assert calls["charge_extra_iterations"] == []


@pytest.mark.asyncio
async def test_on_node_execution_skips_when_llm_call_count_le_1(gated_processor):
    from backend.data.execution import ExecutionStatus

    proc, calls, inner, fake_db, _ = gated_processor
    inner["status"] = ExecutionStatus.COMPLETED
    inner["llm_call_count"] = 1  # exactly the base charge, no extras
    fake_db.get_node = AsyncMock(return_value=_FakeNode(charge_per_llm_call=True))

    stats_pair = (
        MagicMock(
            node_count=0, nodes_cputime=0, nodes_walltime=0, cost=0, node_error_count=0
        ),
        threading.Lock(),
    )
    await proc.on_node_execution(
        node_exec=_make_node_exec(dry_run=False),
        node_exec_progress=MagicMock(),
        nodes_input_masks=None,
        graph_stats_pair=stats_pair,
    )
    assert calls["charge_extra_iterations"] == []


@pytest.mark.asyncio
async def test_on_node_execution_skips_when_dry_run(gated_processor):
    from backend.data.execution import ExecutionStatus

    proc, calls, inner, fake_db, _ = gated_processor
    inner["status"] = ExecutionStatus.COMPLETED
    inner["llm_call_count"] = 5
    fake_db.get_node = AsyncMock(return_value=_FakeNode(charge_per_llm_call=True))

    stats_pair = (
        MagicMock(
            node_count=0, nodes_cputime=0, nodes_walltime=0, cost=0, node_error_count=0
        ),
        threading.Lock(),
    )
    await proc.on_node_execution(
        node_exec=_make_node_exec(dry_run=True),
        node_exec_progress=MagicMock(),
        nodes_input_masks=None,
        graph_stats_pair=stats_pair,
    )
    assert calls["charge_extra_iterations"] == []


@pytest.mark.asyncio
async def test_on_node_execution_insufficient_balance_records_error_and_notifies(
    monkeypatch,
    gated_processor,
):
    """When extra-iteration charging fails with InsufficientBalanceError:

    - the run still reports COMPLETED (the work is already done)
    - execution_stats.error is set so monitoring picks it up
    - _handle_insufficient_funds_notif is called so the user is notified
    """
    from backend.data.execution import ExecutionStatus
    from backend.executor import manager
    from backend.util.exceptions import InsufficientBalanceError

    proc, calls, inner, fake_db, _ = gated_processor
    inner["status"] = ExecutionStatus.COMPLETED
    inner["llm_call_count"] = 4
    fake_db.get_node = AsyncMock(return_value=_FakeNode(charge_per_llm_call=True))

    def raise_ibe(self, node_exec, extra_iterations):
        raise InsufficientBalanceError(
            user_id=node_exec.user_id,
            message="Insufficient balance",
            balance=0,
            amount=extra_iterations * 10,
        )

    monkeypatch.setattr(
        manager.ExecutionProcessor, "charge_extra_iterations", raise_ibe
    )

    stats_pair = (
        MagicMock(
            node_count=0, nodes_cputime=0, nodes_walltime=0, cost=0, node_error_count=0
        ),
        threading.Lock(),
    )
    result_stats = await proc.on_node_execution(
        node_exec=_make_node_exec(dry_run=False),
        node_exec_progress=MagicMock(),
        nodes_input_masks=None,
        graph_stats_pair=stats_pair,
    )
    # Error recorded on stats so downstream monitoring can surface it.
    assert isinstance(result_stats.error, InsufficientBalanceError)
    # User notification fired.
    assert len(calls["handle_insufficient_funds_notif"]) == 1
    assert calls["handle_insufficient_funds_notif"][0]["user_id"] == "u"


# ── Orchestrator _execute_single_tool_with_manager charging gates ──


async def _run_tool_exec_with_stats(
    *,
    dry_run: bool,
    tool_stats_error,
    charge_node_usage_mock=None,
):
    """Invoke _execute_single_tool_with_manager against fully mocked deps
    and return (charge_call_count, merge_stats_calls).

    Used to prove the dry_run and error guards around charge_node_usage
    behave as documented, and that InsufficientBalanceError propagates.
    """
    import threading
    from collections import defaultdict
    from unittest.mock import AsyncMock, MagicMock, patch

    from backend.blocks.orchestrator import ExecutionParams, OrchestratorBlock
    from backend.data.execution import ExecutionContext

    block = OrchestratorBlock()

    # Mocked async DB client used inside orchestrator.
    mock_db_client = AsyncMock()
    mock_target_node = MagicMock()
    mock_target_node.block_id = "test-block-id"
    mock_target_node.input_default = {}
    mock_db_client.get_node.return_value = mock_target_node
    mock_node_exec_result = MagicMock()
    mock_node_exec_result.node_exec_id = "test-tool-exec-id"
    mock_db_client.upsert_execution_input.return_value = (
        mock_node_exec_result,
        {"query": "t"},
    )
    mock_db_client.get_execution_outputs_by_node_exec_id.return_value = {"result": "ok"}

    # ExecutionProcessor mock: on_node_execution returns supplied error.
    mock_processor = AsyncMock()
    mock_processor.running_node_execution = defaultdict(MagicMock)
    mock_processor.execution_stats = MagicMock()
    mock_processor.execution_stats_lock = threading.Lock()
    mock_node_stats = MagicMock()
    mock_node_stats.error = tool_stats_error
    mock_processor.on_node_execution = AsyncMock(return_value=mock_node_stats)
    mock_processor.charge_node_usage = charge_node_usage_mock or MagicMock(
        return_value=(10, 990)
    )

    # Build a tool_info shaped like _build_tool_info_from_args output.
    tool_call = MagicMock()
    tool_call.id = "call-1"
    tool_call.name = "search_keywords"
    tool_call.arguments = '{"query":"t"}'
    tool_def = {
        "type": "function",
        "function": {
            "name": "search_keywords",
            "_sink_node_id": "test-sink-node-id",
            "_field_mapping": {},
            "parameters": {
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
    tool_info = OrchestratorBlock._build_tool_info_from_args(
        tool_call_id="call-1",
        tool_name="search_keywords",
        tool_args={"query": "t"},
        tool_def=tool_def,
    )

    exec_params = ExecutionParams(
        user_id="u",
        graph_id="g",
        node_id="n",
        graph_version=1,
        graph_exec_id="ge",
        node_exec_id="ne",
        execution_context=ExecutionContext(
            human_in_the_loop_safe_mode=False, dry_run=dry_run
        ),
    )

    with patch(
        "backend.blocks.orchestrator.get_database_manager_async_client",
        return_value=mock_db_client,
    ):
        try:
            await block._execute_single_tool_with_manager(
                tool_info, exec_params, mock_processor, responses_api=False
            )
            raised = None
        except Exception as e:
            raised = e

    return mock_processor.charge_node_usage, raised


@pytest.mark.asyncio
async def test_tool_execution_skips_charging_on_dry_run():
    """dry_run=True → charge_node_usage is NOT called."""
    charge_mock, raised = await _run_tool_exec_with_stats(
        dry_run=True, tool_stats_error=None
    )
    assert raised is None
    assert charge_mock.call_count == 0


@pytest.mark.asyncio
async def test_tool_execution_skips_charging_on_failed_tool():
    """tool_node_stats.error is an Exception → charge_node_usage NOT called."""
    charge_mock, raised = await _run_tool_exec_with_stats(
        dry_run=False, tool_stats_error=RuntimeError("tool blew up")
    )
    assert raised is None
    assert charge_mock.call_count == 0


@pytest.mark.asyncio
async def test_tool_execution_skips_charging_on_cancelled_tool():
    """Cancellation (BaseException subclass) → charge_node_usage NOT called.

    Guards the fix for sentry's BaseException concern: the old
    `isinstance(error, Exception)` check would have treated CancelledError
    as "no error" and billed the user for a terminated run.
    """
    import asyncio as _asyncio

    charge_mock, raised = await _run_tool_exec_with_stats(
        dry_run=False, tool_stats_error=_asyncio.CancelledError()
    )
    assert raised is None
    assert charge_mock.call_count == 0


@pytest.mark.asyncio
async def test_tool_execution_insufficient_balance_propagates():
    """InsufficientBalanceError from charge_node_usage must propagate out.

    If this leaked into a ToolCallResult the LLM loop would keep running
    with 'tool failed' errors and the user would get unpaid work.
    """
    from unittest.mock import MagicMock

    from backend.util.exceptions import InsufficientBalanceError

    raising_charge = MagicMock(
        side_effect=InsufficientBalanceError(
            user_id="u", message="nope", balance=0, amount=10
        )
    )
    _, raised = await _run_tool_exec_with_stats(
        dry_run=False,
        tool_stats_error=None,
        charge_node_usage_mock=raising_charge,
    )
    assert isinstance(raised, InsufficientBalanceError)
