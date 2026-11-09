from __future__ import annotations

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
_ALLOW_PREFIX_RE = re.compile(r"^\s*ALLOW_GUARD=1\s+")
_APPROVAL_REPLIES = frozenset({"allow", "approve", "approved", "yes", "y", "允许", "放行", "同意"})

# ponytail: in-memory per-session approval set; cleared when process restarts
_approved_sessions: set[str] = set()


def approve_session_guard(session_id: str) -> None:
    if session_id:
        _approved_sessions.add(session_id)


def is_guard_approval_reply(text: str) -> bool:
    cleaned = (text or "").strip().lower()
    return cleaned in _APPROVAL_REPLIES or cleaned.startswith("allow ") or cleaned.startswith("允许") or cleaned.startswith("放行")


def strip_guard_prefix(command: str) -> str:
    return _ALLOW_PREFIX_RE.sub("", command, count=1)


def check_command_guard(command: str, *, session_id: str = "") -> str | None:
    """Return an interception reason if *command* matches a destructive rule, else None."""
    text = (command or "").strip()
    if not text:
        return None
    if _ALLOW_PREFIX_RE.match(text) or (session_id and session_id in _approved_sessions):
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


if __name__ == "__main__":
    assert check_command_guard("curl -s http://target/api/users") is None
    assert check_command_guard("curl -s http://target/api/delivery/status") is None
    assert check_command_guard("curl -s http://target/api/details?id=1") is None
    assert check_command_guard("curl -X DELETE http://target/api/users/1") is not None
    assert check_command_guard("curl 'http://target/api/user/delete?id=1'") is not None
    assert check_command_guard("curl http://target/admin/destroy") is not None
    assert check_command_guard("psql -c 'DROP TABLE users;'") is not None
    assert check_command_guard("rm -rf /") is not None
    assert check_command_guard("ALLOW_GUARD=1 curl -X DELETE http://target/api/users/1") is None
    approve_session_guard("sess-test")
    assert check_command_guard("curl -X DELETE http://target/api/users/1", session_id="sess-test") is None
    print("command_guard self-check OK")
