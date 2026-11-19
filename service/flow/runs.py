"""The controller task of every running Flow and the signal that wakes one waiting for input."""

from __future__ import annotations

import asyncio


_tasks: dict[str, asyncio.Task[None]] = {}
_input_events: dict[str, asyncio.Event] = {}


def flow_run_is_active(public_id: str) -> bool:
    task = _tasks.get(public_id)
    return task is not None and not task.done()


def track_flow_run(public_id: str, task: asyncio.Task[None]) -> None:
    _tasks[public_id] = task


def forget_flow_run(public_id: str) -> None:
    _tasks.pop(public_id, None)
    _input_events.pop(public_id, None)


async def cancel_flow_run(public_id: str) -> None:
    event = _input_events.get(public_id)
    if event is not None:
        event.set()
    task = _tasks.get(public_id)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def cancel_all_flow_runs() -> None:
    tasks = list(_tasks.values())
    _tasks.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def signal_flow_input(public_id: str) -> None:
    event = _input_events.get(public_id)
    if event is None:
        event = asyncio.Event()
        _input_events[public_id] = event
    event.set()


async def wait_for_flow_input_signal(public_id: str) -> None:
    event = _input_events.get(public_id)
    if event is None:
        event = asyncio.Event()
        _input_events[public_id] = event
    await event.wait()
    event.clear()
