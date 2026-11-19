from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from core.runtime.context import AgentRuntimeContext
from schema.common.tool_results import ToolResultSchema, ToolResultStatusSchema, ToolResultTypeSchema
from service.sandbox import traffic as traffic_service


def _traffic_result(status: ToolResultStatusSchema, output: str) -> str:
    return ToolResultSchema(
        status=status,
        type=ToolResultTypeSchema.TRAFFIC,
        output=output,
    ).model_dump_json()


@function_tool
async def search_traffic(
    ctx: RunContextWrapper[AgentRuntimeContext],
    query: str = "",
    path: str = "",
    status: str = "",
    method: str = "",
    limit: int = 20,
) -> str:
    """Search captured HTTP requests and responses from the sandbox proxy or browser ingest.

    Args:
        query: Full-text search keyword across URL, request/response headers, and bodies.
        path: Substring filter on the URL path/query.
        status: Exact HTTP status code (e.g. '200') or bucket ('2xx', '3xx', '4xx', '5xx').
        method: Optional HTTP method filter ('GET', 'POST', etc.).
        limit: Maximum number of records to return (1-50).
    """
    clamped_limit = min(max(int(limit or 20), 1), 50)
    try:
        page = await traffic_service.query_traffic(
            user_id=ctx.context.user.id,
            user_role=ctx.context.user.role,
            sandbox_container_id=ctx.context.sandbox_container_id,
            project_id=ctx.context.work_project_id,
            query=query,
            path=path,
            status=status,
            method=method,
            page=1,
            size=clamped_limit,
        )
    except Exception as exc:
        return _traffic_result(ToolResultStatusSchema.ERROR, str(exc) or "Failed to search traffic")
    return _traffic_result(
        ToolResultStatusSchema.SUCCESS,
        json.dumps(
            {
                "total": page.total,
                "items": [item.model_dump(mode="json") for item in page.items],
            },
            ensure_ascii=False,
        ),
    )


@function_tool
async def get_traffic_detail(
    ctx: RunContextWrapper[AgentRuntimeContext],
    traffic_id: int,
) -> str:
    """Fetch full request and response headers and bodies for a captured traffic entry.

    Args:
        traffic_id: ID of the traffic record returned by `search_traffic`.
    """
    try:
        detail = await traffic_service.get_traffic_entry(
            int(traffic_id),
            user_id=ctx.context.user.id,
            user_role=ctx.context.user.role,
        )
    except Exception as exc:
        return _traffic_result(ToolResultStatusSchema.ERROR, str(exc) or "Failed to load traffic entry")
    if detail is None:
        return _traffic_result(ToolResultStatusSchema.ERROR, f"Traffic entry {traffic_id} not found")
    return _traffic_result(
        ToolResultStatusSchema.SUCCESS,
        json.dumps(detail.model_dump(mode="json"), ensure_ascii=False),
    )
