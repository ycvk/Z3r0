from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import requests
from agents import RunContextWrapper, function_tool

from config import get_config
from core.runtime.context import AgentRuntimeContext
from schema.agent.fofa import (
    FOFA_FIELDS,
    FofaAssetSchema,
    FofaSearchResultSchema,
    _FOFA_FIELDS,
)
from schema.common.tool_results import ToolResultSchema, ToolResultStatusSchema, ToolResultTypeSchema


_DEFAULT_PAGE = 1
_DEFAULT_SIZE = 30
_MAX_PAGE_SIZE = 300
_REQUEST_TIMEOUT_SECONDS = 30
_FOFA_SEARCH_PATH = "/api/v1/search/all"


def _search(query: str, page: int = _DEFAULT_PAGE, size: int = _DEFAULT_SIZE) -> dict[str, Any]:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("FOFA query is required")
    if page < 1:
        raise ValueError("FOFA page must be greater than or equal to 1")
    if size < 1 or size > _MAX_PAGE_SIZE:
        raise ValueError(f"FOFA size must be between 1 and {_MAX_PAGE_SIZE}")

    fofa_config = get_config().fofa
    base_url = fofa_config.base_url.strip().rstrip("/")
    api_key = fofa_config.api_key.strip()
    if not fofa_config.enabled:
        raise RuntimeError("FOFA integration is disabled")
    if not base_url:
        raise RuntimeError("FOFA base URL is not configured")
    if not api_key:
        raise RuntimeError("FOFA API key is not configured")

    qbase64 = base64.b64encode(normalized_query.encode("utf-8")).decode("ascii")
    target = f"{base_url}{_FOFA_SEARCH_PATH}"
    try:
        response = requests.get(
            url=target,
            params={
                "key": api_key,
                "fields": FOFA_FIELDS,
                "page": page,
                "size": size,
                "qbase64": qbase64,
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        raise RuntimeError(f"FOFA request failed{suffix}") from exc
    except requests.RequestException as exc:
        raise RuntimeError("FOFA request failed") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("FOFA returned invalid JSON") from exc

    if not isinstance(data, dict):
        raise RuntimeError("FOFA returned an invalid response payload")
    if data.get("error"):
        raise RuntimeError(str(data.get("errmsg") or "FOFA search failed"))

    raw_results = data.get("results")
    if raw_results is None:
        raw_results = []
    if not isinstance(raw_results, list):
        raise RuntimeError("FOFA returned an invalid results payload")

    result = FofaSearchResultSchema(
        query=str(data.get("query") or normalized_query),
        total_size=_int_value(data.get("size")),
        current_page=_int_value(data.get("page", page), default=page, minimum=1),
        results=[_asset_from_row(row) for row in raw_results],
    )
    return result.model_dump(mode="json")


@function_tool
async def fofa_search(
    _ctx: RunContextWrapper[AgentRuntimeContext],
    query: str,
    page: int = _DEFAULT_PAGE,
    size: int = _DEFAULT_SIZE,
) -> str:
    """Search FOFA for internet-facing assets.

    Use this tool when an authorized task requires an asset search in FOFA.
    The query follows FOFA's expression language: use ``field="value"`` for
    exact matches, ``field=="value"`` for contains-style matches where
    supported, and ``field!="value"`` for exclusions.  Combine expressions
    with ``&&`` (AND), ``||`` (OR), and ``!`` (NOT); date-range expressions
    such as ``after="2024-01-01"`` and ``before="2024-12-31"`` can narrow a
    search.

    Args:
        query: FOFA search expression.
        page: One-based result page.
        size: Number of results to request, from 1 to 300.

    Returns:
        A JSON tool result using the common tool-result envelope.
    """

    try:
        payload = await asyncio.to_thread(_search, query, page, size)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return _fofa_result(ToolResultStatusSchema.ERROR, str(exc) or "FOFA search failed")
    return _fofa_result(
        ToolResultStatusSchema.SUCCESS,
        json.dumps(payload, ensure_ascii=False),
    )


def _fofa_result(status: ToolResultStatusSchema, output: str) -> str:
    return ToolResultSchema(
        status=status,
        type=ToolResultTypeSchema.FOFA,
        output=output,
    ).model_dump_json()


def _asset_from_row(row: Any) -> FofaAssetSchema:
    if not isinstance(row, list):
        raise RuntimeError("FOFA returned an invalid asset row")
    return FofaAssetSchema.model_validate(
        {
            field: _string_value(value)
            for field, value in zip(_FOFA_FIELDS, row)
        }
    )


def _string_value(value: Any) -> str:
    return "" if value is None else str(value)


def _int_value(value: Any, *, default: int = 0, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, minimum)
