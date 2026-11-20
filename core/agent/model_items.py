"""Read Agents SDK model input for the models that translate it to another wire protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agents import AgentOutputSchemaBase, FunctionTool, Handoff, ModelSettings, TResponseInputItem, Tool, UserError


SYSTEM_ROLES = frozenset({"system", "developer"})
_TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text"})


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    """An image given inline as base64 data, or by URL when `data` is empty."""

    media_type: str
    data: str
    url: str


ContentPart = TextPart | ImagePart


def input_items(input: str | list[TResponseInputItem]) -> list[dict[str, Any]]:
    """Return the model input as plain dict items; a bare string is one user message."""
    if isinstance(input, str):
        return [{"type": "message", "role": "user", "content": input}]
    items: list[dict[str, Any]] = []
    for entry in input:
        if isinstance(entry, dict):
            items.append(dict(entry))
            continue
        dump = getattr(entry, "model_dump", None)
        dumped = dump(exclude_unset=True) if callable(dump) else None
        if not isinstance(dumped, dict):
            raise UserError(f"Unsupported model input item: {entry!r}")
        items.append(dumped)
    return items


def item_type(item: dict[str, Any]) -> str:
    """Messages may omit their type; everything else names it."""
    return item.get("type") or ("message" if "role" in item else "")


def content_parts(content: Any, *, api: str) -> list[ContentPart]:
    """Parse message or tool output content, dropping text that is empty or whitespace."""
    if isinstance(content, str):
        return [TextPart(content)] if content.strip() else []
    if not isinstance(content, list):
        raise UserError(f"Unsupported content for {api}: {content!r}")
    parts: list[ContentPart] = []
    for piece in content:
        kind = piece.get("type") if isinstance(piece, dict) else None
        if kind in _TEXT_PART_TYPES:
            if piece["text"].strip():
                parts.append(TextPart(piece["text"]))
        elif kind == "refusal":
            if piece["refusal"].strip():
                parts.append(TextPart(piece["refusal"]))
        elif kind == "input_image" and isinstance(piece.get("image_url"), str):
            parts.append(_image_part(piece["image_url"]))
        else:
            raise UserError(f"Unsupported content part for {api}: {kind!r}")
    return parts


def tool_arguments(item: dict[str, Any]) -> dict[str, Any]:
    arguments = json.loads(item.get("arguments") or "{}")
    if not isinstance(arguments, dict):
        raise UserError(f"Tool call {item.get('name')!r} arguments are not a JSON object")
    return arguments


def own_reasoning(item: dict[str, Any], provider_data: dict[str, Any]) -> tuple[str, str] | None:
    """Return (text, signature) of a reasoning item this exact protocol and model produced.

    A signature only verifies with the model that issued it, so reasoning from any other source is
    left out of the request.
    """
    item_data = item.get("provider_data") or {}
    if any(item_data.get(key) != value for key, value in provider_data.items()):
        return None
    signature = item.get("encrypted_content")
    if not signature:
        return None
    text = "".join(piece.get("text", "") for piece in item.get("content") or [])
    return text, signature


def function_tools(
    tools: list[Tool],
    *,
    handoffs: list[Handoff],
    output_schema: AgentOutputSchemaBase | None,
    prompt: Any,
    api: str,
) -> list[FunctionTool]:
    """Return the function tools of a request, rejecting features the protocol models do not map."""
    if handoffs:
        raise UserError(f"Handoffs are not supported for {api}")
    if output_schema is not None and not output_schema.is_plain_text():
        raise UserError(f"Structured output is not supported for {api}")
    if prompt is not None:
        raise UserError(f"Prompt templates are not supported for {api}")
    unsupported = [type(tool).__name__ for tool in tools if not isinstance(tool, FunctionTool)]
    if unsupported:
        raise UserError(f"Only function tools are supported for {api}, got {', '.join(unsupported)}")
    return [tool for tool in tools if isinstance(tool, FunctionTool)]


def extra_headers(model_settings: ModelSettings) -> dict[str, str]:
    return {
        name: value
        for name, value in (model_settings.extra_headers or {}).items()
        if isinstance(value, str)
    }


def extra_body(model_settings: ModelSettings) -> dict[str, Any]:
    body = model_settings.extra_body
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise UserError("Extra request body must be a JSON object")
    return dict(body)


def _image_part(image_url: str) -> ImagePart:
    if not image_url.startswith("data:"):
        return ImagePart(media_type="", data="", url=image_url)
    header, _, data = image_url.partition(",")
    media_type, _, encoding = header.removeprefix("data:").partition(";")
    if encoding != "base64" or not media_type or not data:
        raise UserError("Image data URLs must be base64 encoded with a media type")
    return ImagePart(media_type=media_type, data=data, url="")
