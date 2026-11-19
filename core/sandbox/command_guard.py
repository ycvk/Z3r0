from __future__ import annotations

from collections import Counter
import re


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

# In-memory per-session state: exact intercepted command awaiting operator reply,
# and one-shot grants for exact commands approved by the operator.
_pending_commands: dict[str, str] = {}
_approved_commands: dict[str, Counter[str]] = {}


def normalize_guarded_command(command: str) -> str:
    return (command or "").strip()


def is_guard_approval_reply(text: str) -> bool:
    return (text or "").strip().lower() in _APPROVAL_REPLIES


def has_pending_command(session_id: str) -> bool:
    return bool(session_id and session_id in _pending_commands)


def approve_pending_command(session_id: str) -> bool:
    if not session_id:
        return False
    pending = _pending_commands.pop(session_id, None)
    if not pending:
        return False
    grants = _approved_commands.setdefault(session_id, Counter())
    grants[pending] += 1
    return True


def clear_pending_command(session_id: str) -> None:
    if session_id:
        _pending_commands.pop(session_id, None)


def clear_session_guard(session_id: str) -> None:
    if session_id:
        _pending_commands.pop(session_id, None)
        _approved_commands.pop(session_id, None)


def reset_guard_state() -> None:
    _pending_commands.clear()
    _approved_commands.clear()


def _match_destructive_rule(text: str) -> str | None:
    if _DESTRUCTIVE_SHELL_RE.search(text):
        return "destructive system/root shell command"
    if _DESTRUCTIVE_SQL_RE.search(text):
        return "destructive SQL statement (DROP/TRUNCATE)"
    if _HTTP_DELETE_METHOD_RE.search(text):
        return "destructive HTTP DELETE request"
    if _HTTP_CONTEXT_RE.search(text) and _HTTP_DESTRUCTIVE_PATH_RE.search(text):
        return "destructive HTTP endpoint path (/delete, /del, /remove, /unlink, /erase, /destroy)"
    return None


def check_command_guard(command: str, *, session_id: str = "") -> str | None:
    """Return an interception reason if *command* matches a destructive rule and has no one-shot operator grant."""
    text = normalize_guarded_command(command)
    if not text:
        return None
    reason = _match_destructive_rule(text)
    if reason is None:
        return None
    if session_id:
        grants = _approved_commands.get(session_id)
        if grants and grants[text] > 0:
            grants[text] -= 1
            if grants[text] <= 0:
                del grants[text]
            if not grants:
                _approved_commands.pop(session_id, None)
            return None
        _pending_commands[session_id] = text
    return reason


if __name__ == "__main__":
    reset_guard_state()
    assert check_command_guard("curl -s http://target/api/users") is None
    assert check_command_guard("curl -s http://target/api/delivery/status") is None
    assert check_command_guard("curl -s http://target/api/details?id=1") is None
    assert check_command_guard("curl -X DELETE http://target/api/users/1", session_id="sess-test") is not None
    assert check_command_guard("ALLOW_GUARD=1 curl -X DELETE http://target/api/users/1", session_id="sess-test") is not None
    assert check_command_guard("curl 'http://target/api/user/delete?id=1'", session_id="sess-test") is not None
    assert approve_pending_command("sess-test") is True
    assert check_command_guard("psql -c 'DROP TABLE users;'", session_id="sess-test") is not None
    assert check_command_guard("curl 'http://target/api/user/delete?id=1'", session_id="sess-test") is None
    assert check_command_guard("curl 'http://target/api/user/delete?id=1'", session_id="sess-test") is not None
    print("command_guard self-check OK")
