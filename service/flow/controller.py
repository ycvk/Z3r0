from __future__ import annotations

import asyncio
import time

from core.agent.constants import DEFAULT_AGENT_CODE
from core.flow.barriers import pop_barrier
from core.flow.prompts import PLANNING_PROMPT, REFINE_PROMPT, REPORT_PROMPT, SUBTASK_PROMPT
from core.runtime.session import get_agent_pool
from core.sandbox.command_guard import (
    approve_pending_command,
    clear_pending_command,
    is_guard_approval_reply,
)
from logger import get_logger
from middleware.system_user import AuthUser
from schema.agent.events import AgentTextInputPart
from schema.flow.flows import (
    FLOW_TERMINAL_STATUSES,
    CreateFlowRequest,
    FlowSchema,
    FlowStatus,
    FlowSubtaskStatus,
)
from service.agent.reports import export_session_report, latest_report_id_for_session
from service.agent.runtime import submit_turn
from service.agent.session_state import get_session_meta, has_outstanding_session_work
from service.flow import records as flow_records
from service.flow.runs import (
    cancel_all_flow_runs,
    flow_run_is_active,
    forget_flow_run,
    track_flow_run,
    wait_for_flow_input_signal,
)
from service.system_user.users import query_system_user_by_id


logger = get_logger(__name__)

_IDLE_POLL_SECONDS = 0.25
_TURN_IDLE_TIMEOUT_SECONDS = 2400
_PLANNING_ATTEMPTS = 2
_SUBTASK_ATTEMPTS = 2

_started = False
_pending: list[str] = []


async def start_flow(request: CreateFlowRequest, user: AuthUser) -> FlowSchema:
    """Create a Flow and hand it to the controller."""
    flow = await flow_records.create_flow(request, user)
    schedule_flow(flow.public_id)
    return flow


def schedule_flow(public_id: str) -> None:
    if not public_id:
        return
    if not _started:
        if public_id not in _pending:
            _pending.append(public_id)
        return
    _spawn(public_id)


def _spawn(public_id: str) -> None:
    if flow_run_is_active(public_id):
        return
    track_flow_run(public_id, asyncio.create_task(_run_flow(public_id), name=f"flow-{public_id}"))


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
    await cancel_all_flow_runs()
    logger.info("flow runtime stopped")


async def _run_flow(public_id: str) -> None:
    try:
        flow = await flow_records.load_flow_row(public_id)
        if flow is None or flow.status in FLOW_TERMINAL_STATUSES:
            return
        user = await _auth_user(flow.owner_id)
        if user is None:
            await flow_records.fail_flow_run(public_id, "Flow owner no longer exists.")
            return
        initial_schema = await flow_records.load_flow_schema(public_id)
        if initial_schema is None:
            return
        if flow.status in {FlowStatus.QUEUED, FlowStatus.PLANNING} or not initial_schema.subtasks:
            if not await _plan(public_id, user):
                return
        while True:
            flow_schema = await flow_records.load_flow_schema(public_id)
            if flow_schema is None or flow_schema.status in FLOW_TERMINAL_STATUSES:
                return
            if flow_schema.status == FlowStatus.WAITING:
                answer = await flow_records.take_flow_input(public_id)
                if answer is None:
                    await wait_for_flow_input_signal(public_id)
                    continue
                if is_guard_approval_reply(answer):
                    await approve_pending_command(flow_schema.session_id)
                else:
                    await clear_pending_command(flow_schema.session_id)
                await flow_records.update_flow_status(public_id, FlowStatus.RUNNING, ask_question=None)
                if not await _run_subtask(
                    public_id,
                    user,
                    flow_schema.session_id,
                    f"Operator answer:\n{answer}",
                ):
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
            prompt = _with_goal(
                SUBTASK_PROMPT.format(
                    title=running_title,
                    description=running_description or "No additional description.",
                    remaining="\n".join(remaining) or "- none",
                ),
                flow_schema.goal,
            )
            if not await _run_subtask(public_id, user, flow_schema.session_id, prompt):
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("flow failed: %s", public_id)
        await flow_records.fail_flow_run(public_id, "Flow runtime failed.")
    finally:
        forget_flow_run(public_id)


async def _plan(public_id: str, user: AuthUser) -> bool:
    flow = await flow_records.load_flow_row(public_id)
    if flow is None:
        return False
    await flow_records.update_flow_status(public_id, FlowStatus.PLANNING)
    for _ in range(_PLANNING_ATTEMPTS):
        pop_barrier(flow.session_id)
        await _run_turn(flow.session_id, user, _with_goal(PLANNING_PROMPT, flow.goal))
        await _wait_idle(flow.session_id)
        barrier = pop_barrier(flow.session_id)
        if barrier is not None and barrier.kind == "submit_plan":
            items = barrier.payload.get("items") or []
            if isinstance(items, list) and items:
                await flow_records.replace_plan(public_id, items)
                return True
    await flow_records.fail_flow_run(public_id, "Flow planning did not produce a subtask list.")
    return False


_SUBTASK_RETRY_PROMPT = (
    "The current subtask is still open. Call `done` with a concise evidence-backed summary "
    "from the last command output, or `ask` if you must stop. "
    "Do not call `export_report`. Do not repeat a successful `terminal` command."
)


async def _run_subtask(public_id: str, user: AuthUser, session_id: str, prompt: str) -> bool:
    for attempt in range(_SUBTASK_ATTEMPTS):
        pop_barrier(session_id)
        await _run_turn(session_id, user, prompt if attempt == 0 else _SUBTASK_RETRY_PROMPT)
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
    schema = await flow_records.load_flow_schema(public_id)
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
        running = await get_agent_pool().is_running(session_id)
        if (meta is None or not meta.is_running) and not outstanding and not running:
            return
        await asyncio.sleep(_IDLE_POLL_SECONDS)
    raise TimeoutError("flow turn idle timeout")


def _with_goal(body: str, goal: str) -> str:
    cleaned = goal.strip() or "No goal provided."
    return f"Goal:\n{cleaned}\n\n{body}"


async def _auth_user(user_id: int) -> AuthUser | None:
    user = await query_system_user_by_id(user_id)
    if user is None:
        return None
    return AuthUser(id=user.id, role=user.role, email=user.email, username=user.username)

