"""Token estimates for conversation items."""

from __future__ import annotations

import json
from typing import Any

import tiktoken
from agents import TResponseInputItem


def estimate_items_tokens(items: list[TResponseInputItem], model: str) -> int:
    encoding = _encoding_for_model(model)
    total = 0
    for item in items:
        total += 4
        total += len(encoding.encode(json.dumps(summary_safe_value(item), ensure_ascii=False, separators=(",", ":"))))
    return total


def estimate_text_tokens(text: str, model: str) -> int:
    return len(_encoding_for_model(model).encode(text))


def summary_safe_value(value: Any) -> Any:
    if isinstance(value, list):
        return [summary_safe_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("type") == "input_image":
        media_type = _image_media_type(value.get("image_url"))
        return {
            "type": "input_image",
            "image_url": f"data:{media_type};base64,[omitted]",
            "detail": value.get("detail") or "auto",
            "note": "Image bytes omitted from context compaction prompt",
        }
    return {key: summary_safe_value(item) for key, item in value.items()}


def _image_media_type(image_url: Any) -> str:
    if not isinstance(image_url, str) or not image_url.startswith("data:"):
        return "image/*"
    header = image_url.split(",", 1)[0]
    media_type = header.removeprefix("data:").split(";", 1)[0]
    return media_type or "image/*"


def _encoding_for_model(model: str):
    try:
        return tiktoken.encoding_for_model(model.split("/", 1)[-1])
    except Exception:
        return tiktoken.get_encoding("cl100k_base")
