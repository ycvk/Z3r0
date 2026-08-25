from __future__ import annotations

import json
from dataclasses import replace

from agents import RunContextWrapper, function_tool
from pydantic import BaseModel, Field

from core.flow.barriers import FlowBarrier, set_barrier
from core.runtime.context import AgentRuntimeContext
from core.sandbox import command_output
from core.sandbox.command_jobs import start_async_sandbox_command
from core.work_project import validate_specialist_execution_context
from schema.flow.flows import MAX_FLOW_SUBTASKS
from schema.sandbox.async_jobs import SandboxAsyncJobStatus
from service.sandbox import async_jobs as sandbox_async_jobs


_ASYNC_COMMAND_CONCURRENCY_LIMIT = 3
_TERMINAL_DEFAULT_TIMEOUT_SECONDS = 600
_TERMINAL_MAX_TIMEOUT_SECONDS = 1800


def _command_result(**kwargs: object) -> str:
    return command_output.result_metadata(**kwargs).model_dump_json(exclude_none=True, exclude_defaults=True)


def _error_result(error: str) -> str:
    return _command_result(status=SandboxAsyncJobStatus.FAILED, error=error)


def _clamp_timeout(timeout_seconds: int | None) -> int:
    try:
        value = int(timeout_seconds) if timeout_seconds is not None else _TERMINAL_DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = _TERMINAL_DEFAULT_TIMEOUT_SECONDS
    return min(max(value, 1), _TERMINAL_MAX_TIMEOUT_SECONDS)


def _json_ok(**payload: object) -> str:
    return json.dumps({"status": "ok", **payload})


def _json_error(error: str) -> str:
    return json.dumps({"status": "error", "error": error})


class FlowSubtaskDraft(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = ""


class FlowPlanPatchOp(BaseModel):
    op: str = Field(min_length=1, max_length=16)
    position: int
    title: str = ""
    description: str = ""


@function_tool
async def terminal(
    ctx: RunContextWrapper[AgentRuntimeContext],
    command: str,
    timeout_seconds: int = _TERMINAL_DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Execute a sandbox command. Dispatch is turn-terminal; the resume payload includes an output excerpt.

    Args:
        command: Shell command to execute.
        timeout_seconds: Timeout in seconds, clamped to 1-1800, default 600.
    """
    container_id = ctx.context.sandbox_container_id
    if container_id is None:
        return _error_result("No sandbox container selected.")
    if error := await validate_specialist_execution_context(ctx.context):
        return _error_result(error)
    if not command.strip():
        return _error_result("sandbox container command is required")
    if not ctx.context.agent_instance_id:
        return _error_result("agent instance id is required for async command execution")

    running_jobs = await sandbox_async_jobs.count_running_async_jobs_for_agent(
        session_id=ctx.context.session_id,
        agent_instance_id=ctx.context.agent_instance_id,
    )
    if running_jobs >= _ASYNC_COMMAND_CONCURRENCY_LIMIT:
        return _error_result(
            f"sandbox async command limit reached; at most {_ASYNC_COMMAND_CONCURRENCY_LIMIT} commands may run concurrently",
        )

    timeout = _clamp_timeout(timeout_seconds)
    run_id = command_output.new_run_id()
    output_path = command_output.output_path_for_run(run_id)
    command_text = command.strip()
    await start_async_sandbox_command(
        run_id=run_id,
        context=replace(ctx.context, rag_context="", work_project_context=""),
        command=command_text,
        output_file=output_path,
        wrapped_command=command_output.async_command(command_text, output_path),
        stat_command=command_output.stat_command(output_path),
        timeout_seconds=timeout,
    )
    return _command_result(status=SandboxAsyncJobStatus.RUNNING, run_id=run_id)


@function_tool
async def done(ctx: RunContextWrapper[AgentRuntimeContext], summary: str) -> str:
    """Complete the current Flow subtask with a concise summary."""
    cleaned = (summary or "").strip()
    if not cleaned:
        return _json_error("summary is required")
    set_barrier(ctx.context.session_id, FlowBarrier(kind="done", payload={"summary": cleaned}))
    return _json_ok(barrier="done")


@function_tool
async def ask(ctx: RunContextWrapper[AgentRuntimeContext], question: str) -> str:
    """Pause the Flow and ask the operator a question."""
    cleaned = (question or "").strip()
    if not cleaned or len(cleaned) > 2000:
        return _json_error("question must be 1-2000 characters")
    set_barrier(ctx.context.session_id, FlowBarrier(kind="ask", payload={"question": cleaned}))
    return _json_ok(barrier="ask")


@function_tool
async def submit_subtask_list(ctx: RunContextWrapper[AgentRuntimeContext], items: list[FlowSubtaskDraft]) -> str:
    """Submit the Flow plan as an ordered list of subtasks.

    Args:
        items: Objects with `title` and optional `description`. 1-15 items.
    """
    normalized: list[dict[str, str]] = []
    for raw in items or []:
        title = raw.title.strip()
        if not title:
            continue
        normalized.append({"title": title[:160], "description": raw.description.strip()})
    if not normalized or len(normalized) > MAX_FLOW_SUBTASKS:
        return _json_error(f"submit 1-{MAX_FLOW_SUBTASKS} subtasks with titles")
    set_barrier(ctx.context.session_id, FlowBarrier(kind="submit_plan", payload={"items": normalized}))
    return _json_ok(barrier="submit_plan", count=len(normalized))


@function_tool
async def patch_subtask_list(ctx: RunContextWrapper[AgentRuntimeContext], ops: list[FlowPlanPatchOp]) -> str:
    """Patch remaining planned subtasks.

    Args:
        ops: `add`, `remove`, or `replace` operations with a `position` and optional title/description.
    """
    normalized: list[dict[str, object]] = []
    for raw in ops or []:
        op = raw.op.strip()
        if op not in {"add", "remove", "replace"}:
            continue
        item: dict[str, object] = {"op": op, "position": int(raw.position)}
        if raw.title.strip():
            item["title"] = raw.title.strip()[:160]
        item["description"] = raw.description.strip()
        normalized.append(item)
    set_barrier(ctx.context.session_id, FlowBarrier(kind="patch_plan", payload={"ops": normalized}))
    return _json_ok(barrier="patch_plan", count=len(normalized))
