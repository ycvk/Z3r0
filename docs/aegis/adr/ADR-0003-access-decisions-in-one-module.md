# ADR-0003 Access decisions live in one module

Status: `accepted`
Date: `2026-11-19`
Owners: `service/common/access.py` (rules) · `tests/test_access_matrix.py` (expected answers)

## Context

Whether a user may reach a work project, a sandbox container, or an agent
session was decided in the module that happened to need the answer. The work
project rule existed four times, the container rule six times, and one copy
lived in a handler that queried the database itself. The traffic and
container list services carried their own copies because they could not
import the project service without an import cycle.

## Decision

`service/common/access.py` is the only place that reads ownership to decide
access. It imports models and nothing from `service`, so every service,
handler, and tool can use it.

- A `can_*` function answers for one record. A `*_scope` function is the same
  rule as a SQL condition for list queries.
- A function that takes part in a caller's transaction ends in `_in_tx` and
  receives the session.

Rules:

| Resource | Who may use it |
| --- | --- |
| Work project | An admin; a user who owns it. |
| Sandbox container, manage (lifecycle, shell, files, noVNC, selecting it for a session, a project, or a Flow) | An admin; the user who owns it. |
| Sandbox container, view (captured traffic) | Whoever may manage it; every owner of the project it is bound to. |
| Agent session outside a project | Its owner. An admin may also use a Flow session. |
| Agent session inside a project | Whoever may use the project. |
| Flow | An admin; the user who started it. |
| Traffic entry | An admin; the user who captured it; whoever may view its container; whoever may use its project. |

`router` and `handler` do not import `model`; a second `lint-imports`
contract enforces it, so an HTTP entry point reads records and access answers
through `service`.

## Consequences

- A rule changes in one function and its scope, and
  `tests/test_access_matrix.py` states the expected answer for an admin and
  two users on every list and record endpoint.
- The same test checks that every scope returns exactly the records its
  `can_*` function accepts.
- Every container endpoint answers 404 for a container that does not exist
  and 403 for one the user may not manage.
- User management, host management, and the subagent task records keep their
  own checks.

## Evidence

2026-11-19, local: the access matrix passed on the code before the change and
passes after it, with one intended difference: the container file endpoints
answer 404 instead of 403 to a non-admin user asking for a container that
does not exist. Three trial changes to `access.py` (container management, the
project scope, admin access to chat sessions) each failed the matrix.
`uv run pytest` (26 passed), `uv run lint-imports` (2 contracts kept),
`uv run ruff check`, and `uv run basedpyright` (0 new errors, 653 in
baseline) pass, and the API contract export has no diff.
