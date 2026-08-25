from __future__ import annotations

import asyncio
import time

from core.agent.constants import DEFAULT_AGENT_CODE
from core.flow.barriers import pop_barrier
from core.flow.prompts import PLANNING_PROMPT, REFINE_PROMPT, REPORT_PROMPT, SUBTASK_PROMPT
from core.runtime.session import get_agent_pool
from logger import get_logger
from middleware.system_user import AuthUser
from schema.agent.events import AgentTextInputPart
from schema.flow.flows import FLOW_TERMINAL_STATUSES, FlowStatus, FlowSubtaskStatus
from service.agent.reports import export_session_report, latest_report_id_for_session
from service.agent.runtime import submit_turn
from service.agent.session_state import get_session_meta, has_outstanding_session_work
from service.flow import records as flow_records
from service.system_user.users import query_system_user_by_id


logger = get_logger(__name__)

_IDLE_POLL_SECONDS = 0.25
_TURN_IDLE_TIMEOUT_SECONDS = 2400
_PLANNING_ATTEMPTS = 2
_SUBTASK_ATTEMPTS = 2

_started = False
_tasks: dict[str, asyncio.Task[None]] = {}
_pending: list[str] = []
_input_events: dict[str, asyncio.Event] = {}
_input_text: dict[str, str] = {}


def schedule_flow(public_id: str) -> None:
    if not public_id:
        return
    if not _started:
        if public_id not in _pending:
            _pending.append(public_id)
        return
    _spawn(public_id)


def _spawn(public_id: str) -> None:
    current = _tasks.get(public_id)
    if current is not None and not current.done():
        return
    _tasks[public_id] = asyncio.create_task(_run_flow(public_id), name=f"flow-{public_id}")


async def start_flow_runtime() -> None:
    global _started
    _started = True
    for public_id in (*_pending, *await flow_records.list_recoverable_flow_ids()):
        _spawn(public_id)
    _pending.clear()
    logger.info("flow runtime started")


async def stop_flow_runtime() -> None:
    global _started
    _started = False
    tasks = list(_tasks.values())
    _tasks.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("flow runtime stopped")


async def cancel_scheduled_flow(public_id: str) -> None:
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


async def submit_flow_input(public_id: str, text: str) -> None:
    _input_text[public_id] = text
    event = _input_events.get(public_id)
    if event is None:
        event = asyncio.Event()
        _input_events[public_id] = event
    event.set()


async def _run_flow(public_id: str) -> None:
    try:
        flow = await flow_records.load_flow_row(public_id)
        if flow is None or flow.status in FLOW_TERMINAL_STATUSES:
            return
        user = await _auth_user(flow.owner_id)
        if user is None:
            await flow_records.fail_flow_run(public_id, "Flow owner no longer exists.")
            return
        if flow.status in {FlowStatus.QUEUED, FlowStatus.PLANNING} or not (await flow_records.get_flow(public_id, user)).subtasks:
            if not await _plan(public_id, user):
                return
        while True:
            flow_schema = await flow_records.get_flow(public_id, user)
            if flow_schema is None or flow_schema.status in FLOW_TERMINAL_STATUSES:
                return
            if flow_schema.status == FlowStatus.WAITING:
                answer = await _wait_for_input(public_id)
                if answer is None:
                    return
                await flow_records.update_flow_status(public_id, FlowStatus.RUNNING, ask_question=None)
                await _run_turn(flow_schema.session_id, user, f"Operator answer:\n{answer}")
                await _wait_idle(flow_schema.session_id)
                if not await _consume_primary_barrier(public_id, user, allow_done=True, allow_ask=True, allow_patch=False):
                    return
                continue
            running = next((item for item in flow_schema.subtasks if item.status == FlowSubtaskStatus.RUNNING), None)
            if running is None:
                nxt = await flow_records.activate_next_subtask(public_id)
                if nxt is None:
                    await _report(public_id, user)
                    return
                running_title = nxt.title
                running_description = nxt.description
            else:
                running_title = running.title
                running_description = running.description
            remaining = [
                f"- {item.title}" for item in flow_schema.subtasks
                if item.status == FlowSubtaskStatus.PLANNED
            ]
            prompt = SUBTASK_PROMPT.format(
                title=running_title,
                description=running_description or "No additional description.",
                remaining="\n".join(remaining) or "- none",
            )
            if not await _run_subtask(public_id, user, flow_schema.session_id, prompt):
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("flow failed: %s", public_id)
        await flow_records.fail_flow_run(public_id, "Flow runtime failed.")
    finally:
        _tasks.pop(public_id, None)
        _input_events.pop(public_id, None)
        _input_text.pop(public_id, None)


async def _plan(public_id: str, user: AuthUser) -> bool:
    flow = await flow_records.load_flow_row(public_id)
    if flow is None:
        return False
    await flow_records.update_flow_status(public_id, FlowStatus.PLANNING)
    for _ in range(_PLANNING_ATTEMPTS):
        pop_barrier(flow.session_id)
        await _run_turn(flow.session_id, user, PLANNING_PROMPT)
        await _wait_idle(flow.session_id)
        barrier = pop_barrier(flow.session_id)
        if barrier is not None and barrier.kind == "submit_plan":
            items = barrier.payload.get("items") or []
            if isinstance(items, list) and items:
                await flow_records.replace_plan(public_id, items)
                return True
    await flow_records.fail_flow_run(public_id, "Flow planning did not produce a subtask list.")
    return False


async def _run_subtask(public_id: str, user: AuthUser, session_id: str, prompt: str) -> bool:
    for _ in range(_SUBTASK_ATTEMPTS):
        pop_barrier(session_id)
        await _run_turn(session_id, user, prompt)
        await _wait_idle(session_id)
        if await _consume_primary_barrier(public_id, user, allow_done=True, allow_ask=True, allow_patch=False):
            return True
    await flow_records.fail_flow_run(public_id, "Flow subtask ended without done or ask.")
    return False


async def _consume_primary_barrier(
    public_id: str,
    user: AuthUser,
    *,
    allow_done: bool,
    allow_ask: bool,
    allow_patch: bool,
) -> bool:
    flow = await flow_records.load_flow_row(public_id)
    if flow is None:
        return False
    barrier = pop_barrier(flow.session_id)
    if barrier is None:
        return False
    if allow_done and barrier.kind == "done":
        summary = str(barrier.payload.get("summary") or "").strip()
        await flow_records.complete_running_subtask(public_id, summary)
        await _refine(public_id, user, flow.session_id, summary)
        return True
    if allow_ask and barrier.kind == "ask":
        question = str(barrier.payload.get("question") or "").strip()
        await flow_records.update_flow_status(public_id, FlowStatus.WAITING, ask_question=question)
        return True
    if allow_patch and barrier.kind == "patch_plan":
        ops = barrier.payload.get("ops") or []
        if isinstance(ops, list):
            await flow_records.apply_plan_patch(public_id, ops)
        return True
    return False


async def _refine(public_id: str, user: AuthUser, session_id: str, summary: str) -> None:
    pop_barrier(session_id)
    await _run_turn(session_id, user, REFINE_PROMPT.format(summary=summary or "No summary."))
    await _wait_idle(session_id)
    await _consume_primary_barrier(public_id, user, allow_done=False, allow_ask=False, allow_patch=True)


async def _report(public_id: str, user: AuthUser) -> None:
    flow = await flow_records.load_flow_row(public_id)
    if flow is None:
        return
    await flow_records.update_flow_status(public_id, FlowStatus.REPORTING)
    schema = await flow_records.get_flow(public_id, user)
    await _run_turn(flow.session_id, user, REPORT_PROMPT)
    await _wait_idle(flow.session_id)
    report_id = latest_report_id_for_session(flow.session_id)
    if not report_id:
        body = [f"# {flow.title or 'Flow report'}", "", flow.goal, ""]
        if schema is not None:
            for item in schema.subtasks:
                body.append(f"## {item.title}")
                body.append(item.summary or item.status)
                body.append("")
        exported = await export_session_report(flow.session_id, "\n".join(body))
        report_id = exported.report_id
    await flow_records.update_flow_status(
        public_id,
        FlowStatus.COMPLETED,
        report_id=report_id,
        terminal=True,
    )


async def _run_turn(session_id: str, user: AuthUser, text: str) -> None:
    await submit_turn(
        session_id=session_id,
        content=[AgentTextInputPart(text=text)],
        user=user,
        requested_agent_code=DEFAULT_AGENT_CODE,
    )


async def _wait_idle(session_id: str) -> None:
    deadline = time.monotonic() + _TURN_IDLE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        meta = await get_session_meta(session_id)
        outstanding = await has_outstanding_session_work(session_id)
        runtime = get_agent_pool()
        running = False
        async with runtime._lock:
            entry = runtime._pool.get(session_id)
            running = entry is not None and entry.session.is_running()
        if (meta is None or not meta.is_running) and not outstanding and not running:
            return
        await asyncio.sleep(_IDLE_POLL_SECONDS)
    raise TimeoutError("flow turn idle timeout")


async def _wait_for_input(public_id: str) -> str | None:
    event = _input_events.get(public_id)
    if event is None:
        event = asyncio.Event()
        _input_events[public_id] = event
    await event.wait()
    event.clear()
    return _input_text.pop(public_id, None)


async def _auth_user(user_id: int) -> AuthUser | None:
    user = await query_system_user_by_id(user_id)
    if user is None:
        return None
    return AuthUser(id=user.id, role=user.role, email=user.email, username=user.username)


# Keep a name the plan referenced.
start_flow = schedule_flow
