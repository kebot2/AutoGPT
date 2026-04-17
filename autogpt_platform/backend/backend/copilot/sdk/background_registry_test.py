"""Tests for the background task registry."""

import asyncio
import contextlib

import pytest

from .background_registry import (
    MAX_BACKGROUND_TASKS_PER_SESSION,
    cancel_all_background_tasks,
    get_background_task,
    init_registry,
    register_background_task,
    unregister_background_task,
)


@pytest.fixture(autouse=True)
def _init_for_each_test():
    init_registry()


@pytest.mark.asyncio
async def test_register_and_lookup():
    async def hang():
        await asyncio.sleep(60)

    task = asyncio.create_task(hang())
    bg_id = register_background_task(task, "some_tool")

    entry = get_background_task(bg_id)
    assert entry is not None
    assert entry["tool_name"] == "some_tool"
    assert entry["task"] is task

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_unregister_removes_entry():
    async def hang():
        await asyncio.sleep(60)

    task = asyncio.create_task(hang())
    bg_id = register_background_task(task, "some_tool")
    unregister_background_task(bg_id)
    assert get_background_task(bg_id) is None

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_all_cancels_pending_tasks_and_empties_registry():
    events = []

    async def hang_with_cancel_trap(idx: int):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            events.append(idx)
            raise

    tasks = [asyncio.create_task(hang_with_cancel_trap(i)) for i in range(3)]
    # Let the tasks start before cancellation.
    await asyncio.sleep(0)
    bg_ids = [register_background_task(t, f"tool_{i}") for i, t in enumerate(tasks)]

    # Sanity check: all three actually got registered under real IDs.
    for bg_id in bg_ids:
        assert get_background_task(bg_id) is not None

    count = cancel_all_background_tasks(reason="test")
    assert count == 3

    # Let the cancellations propagate.
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
    assert sorted(events) == [0, 1, 2]

    # Registry should be empty now — verify using the actual IDs we registered.
    for bg_id in bg_ids:
        assert get_background_task(bg_id) is None


@pytest.mark.asyncio
async def test_registry_cap_evicts_oldest_on_overflow():
    tasks: list[asyncio.Task] = []
    ids: list[str] = []

    async def hang():
        await asyncio.sleep(60)

    # Fill to capacity.
    for _ in range(MAX_BACKGROUND_TASKS_PER_SESSION):
        t = asyncio.create_task(hang())
        tasks.append(t)
        ids.append(register_background_task(t, "pool_tool"))

    oldest_id = ids[0]
    oldest_task = tasks[0]
    assert get_background_task(oldest_id) is not None

    # One more registration should evict + cancel the oldest.
    extra_task = asyncio.create_task(hang())
    extra_id = register_background_task(extra_task, "overflow_tool")
    tasks.append(extra_task)
    ids.append(extra_id)

    assert get_background_task(oldest_id) is None
    assert get_background_task(extra_id) is not None
    # The evicted task was cancelled.
    with contextlib.suppress(asyncio.CancelledError):
        await oldest_task
    assert oldest_task.cancelled()

    # Cleanup.
    for t in tasks[1:]:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
