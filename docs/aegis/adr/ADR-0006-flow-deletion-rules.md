# ADR-0006 A Flow is deleted with its session

Status: `accepted`
Date: `2026-11-19`
Owners: `service/flow/lifecycle.py` (rules) · `model/flow/flows.py` (foreign keys) · `tests/test_flow_lifecycle.py` (expected outcomes)

## Decision

A Flow and its hidden session are one unit. Deleting the session deletes the
Flow record through `ON DELETE CASCADE`, and `delete_flow_session` is the only
way to delete that session. The session endpoints answer 404 for it.

| Deleted | What happens to Flows |
| --- | --- |
| A user | Flows the user started outside a work project are deleted. Flows inside a work project stay with the project: unfinished ones are cancelled, and each passes to the user who takes over its session. |
| A work project | Its Flows are deleted. |
| A sandbox container | Deletion is refused with 400 while an unfinished Flow uses the container. Finished Flows stay and report `sandbox_container_id` as null. |

`service/flow/runs.py` holds the controller task and the pending operator
input of every running Flow, so deletion and cancellation stop a controller
without importing it.

## Consequences

- Deleting a user, a work project, or a sandbox container no longer fails on a
  Flow that refers to it.
- An operator cancels a running Flow before deleting its container.
- `sandbox_container_id` of a Flow is nullable in the API, and the Flow pages
  show a finished Flow without its container.

## Evidence

2026-11-19, local: `tests/test_flow_lifecycle.py` covers the three deletions,
the refusal for an unfinished Flow, the session endpoints, and a controller
stopped by a deletion; each case failed with a foreign key violation before
the change. Revision `0003` ran on an empty database and on a copy of a
database that predates migrations. The application image, started on an empty
database, served the same deletions over its HTTP API. `uv run ruff check`,
`uv run lint-imports` (3 contracts kept), `uv run basedpyright` (0 new
errors), and `uv run pytest` (41 passed) pass, and both frontend builds pass.
