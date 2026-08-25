from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FlowBarrier:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


_barriers: dict[str, FlowBarrier] = {}


def set_barrier(session_id: str, barrier: FlowBarrier) -> None:
    if session_id:
        _barriers[session_id] = barrier


def pop_barrier(session_id: str) -> FlowBarrier | None:
    return _barriers.pop(session_id, None)
