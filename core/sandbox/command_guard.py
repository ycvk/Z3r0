"""Destructive commands need the operator's approval, one exact command at a time.

An intercepted command waits as the session's pending command. The operator's approval turns it
into a grant that lets the same command run once. Both are rows of `sandbox_guarded_commands`,
so they outlive a restart and go away with the session.
"""

from __future__ import annotations

import re

from sqlalchemy import delete
from sqlmodel import col, select

from database import get_async_session
from model.sandbox.guarded_commands import GuardedCommand


_HTTP_CONTEXT_RE = re.compile(r"(?:\b(?:curl|wget|httpx|httpie|ffuf|sqlmap)\b|https?://|requests\.)", re.IGNORECASE)
_HTTP_DELETE_METHOD_RE = re.compile(
    r"(?:(?:-X|--request)\s*['\"]?DELETE['\"]?\b|requests\.delete\s*\()",
    re.IGNORECASE,
)
# Matches destructive endpoint segments (/delete, /del, /remove, /unlink, /erase, /destroy)
# followed by a separator or end-of-token, avoiding false positives on /delivery, /details, /delta.
_HTTP_DESTRUCTIVE_PATH_RE = re.compile(
    r"/(?:delete|del|remove|unlink|erase|destroy|reset-db|wipe)(?=[/?&#'\"\s)]|$)",
    re.IGNORECASE,
)
_DESTRUCTIVE_SQL_RE = re.compile(
    r"\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b",
    re.IGNORECASE,
)
_DESTRUCTIVE_SHELL_RE = re.compile(
    r"(?:\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(?:\s|$|\*)"
    r"|\b(?:mkfs(?:\.[a-z0-9]+)?|shutdown|reboot|poweroff|halt)\b"
    r"|:\(\)\s*\{\s*:\|:\s*&\s*\}\s*;)",
)
_APPROVAL_REPLIES = frozenset({
    "allow",
    "approve",
    "approved",
    "yes",
    "y",
    "允许",
    "放行",
    "同意",
    "确认放行",
})

def normalize_guarded_command(command: str) -> str:
    return (command or "").strip()


def is_guard_approval_reply(text: str) -> bool:
    return (text or "").strip().lower() in _APPROVAL_REPLIES


def _pending(session_id: str):
    return (col(GuardedCommand.session_id) == session_id, col(GuardedCommand.approved).is_(False))


async def approve_pending_command(session_id: str) -> bool:
    """Grant the session's pending command one run; False when nothing is pending."""
    async with get_async_session() as session:
        pending = list((await session.exec(
            select(GuardedCommand)
            .where(*_pending(session_id))
            .order_by(col(GuardedCommand.id).desc())
            .with_for_update()
        )).all())
        if not pending:
            return False
        pending[0].approved = True
        session.add(pending[0])
        for stale in pending[1:]:
            await session.delete(stale)
        await session.commit()
    return True


async def clear_pending_command(session_id: str) -> None:
    async with get_async_session() as session:
        await session.execute(delete(GuardedCommand).where(*_pending(session_id)))
        await session.commit()


async def clear_session_guard(session_id: str) -> None:
    """Drop the session's pending command and every unused grant."""
    async with get_async_session() as session:
        await session.execute(delete(GuardedCommand).where(col(GuardedCommand.session_id) == session_id))
        await session.commit()


def match_destructive_rule(command: str) -> str | None:
    text = normalize_guarded_command(command)
    if not text:
        return None
    if _DESTRUCTIVE_SHELL_RE.search(text):
        return "destructive system/root shell command"
    if _DESTRUCTIVE_SQL_RE.search(text):
        return "destructive SQL statement (DROP/TRUNCATE)"
    if _HTTP_DELETE_METHOD_RE.search(text):
        return "destructive HTTP DELETE request"
    if _HTTP_CONTEXT_RE.search(text) and _HTTP_DESTRUCTIVE_PATH_RE.search(text):
        return "destructive HTTP endpoint path (/delete, /del, /remove, /unlink, /erase, /destroy)"
    return None


async def check_command_guard(command: str, *, session_id: str) -> str | None:
    """Return why *command* is intercepted, or None when it may run.

    A destructive command runs only by using up a grant for exactly that command. Without one it
    becomes the session's pending command.
    """
    reason = match_destructive_rule(command)
    if reason is None:
        return None
    text = normalize_guarded_command(command)
    async with get_async_session() as session:
        grant_id = (await session.exec(
            select(GuardedCommand.id)
            .where(
                col(GuardedCommand.session_id) == session_id,
                col(GuardedCommand.approved).is_(True),
                col(GuardedCommand.command) == text,
            )
            .order_by(col(GuardedCommand.id))
            .limit(1)
            .with_for_update(skip_locked=True)
        )).first()
        if grant_id is not None:
            await session.execute(delete(GuardedCommand).where(col(GuardedCommand.id) == grant_id))
            await session.commit()
            return None
        await session.execute(delete(GuardedCommand).where(*_pending(session_id)))
        session.add(GuardedCommand(session_id=session_id, command=text))
        await session.commit()
    return reason
