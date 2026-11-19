# ADR-0004 Domain errors and single-point HTTP mapping

Status: `accepted`
Date: `2026-11-19`
Owners: `core/errors.py` (hierarchy) · `middleware/common/response.py` (HTTP mapping) · `tests/test_domain_errors.py` (verification)

## Decision

Services and entry points signal expected failures by raising a `DomainError`
subclass from `core/errors.py`. Return types carry only success values.

| Error | HTTP status | Meaning |
| --- | --- | --- |
| `ValidationError` | `400 Bad Request` | Input, state, or precondition disallows the requested action. |
| `AuthenticationError` | `401 Unauthorized` | Credentials are missing or invalid. |
| `ForbiddenError` | `403 Forbidden` | The authenticated actor lacks the role required for the action. |
| `NotFoundError` | `404 Not Found` | The target resource does not exist or is invisible to the actor. |
| `ConflictError` | `409 Conflict` | The action conflicts with an existing record or concurrent work. |
| `UpstreamError` | `502 Bad Gateway` | An external Docker daemon or sandbox control proxy failed to complete the operation. |

`middleware/common/response.py` registers `domain_exception_handler` on
`FastAPI` and renders every `DomainError` as `CommonResponse(code=status,
message=str(exc))`. `status_for_domain_error` raises `TypeError` when a
`DomainError` subclass has no entry in the table.

Agent tools under `tools/` catch `DomainError` and return a structured error
`ToolResultSchema`.

Resource endpoints addressed by record identifier (`/api/work-projects/{id}`,
`/api/sandbox-containers/{id}`, `/api/agent-sessions/{session_id}`,
`/api/flows/{public_id}`, `/api/traffic/{traffic_id}`, and their sub-resources)
raise `NotFoundError` (`404`) both when the record is absent and when the
caller may not use it. `ForbiddenError` (`403`) applies to role gates such as
`require_admin` and assigning another user as the owner of a new container.

## Consequences

- Handlers call services and return `CommonResponse` payloads directly.
- Adding a `DomainError` subclass without an HTTP mapping fails immediately
  with `TypeError`.
- `middleware` sits above `core` in the `lint-imports` layer contract:
  `router`, `handler` and `tools`, `service`, `middleware`, `core`, `model`,
  `schema`, `utils`.

## Evidence

2026-11-19, local: `tests/test_domain_errors.py` verifies every `DomainError`
subclass mapping, the `TypeError` guard on unmapped subclasses, and service
round-trips across `400`, `401`, `403`, `404`, and `409`.
`tests/test_access_matrix.py` verifies `404` across all record endpoints for
absent and unowned resources. `uv run ruff check`, `uv run lint-imports`
(2 contracts kept), `uv run basedpyright` (0 new errors, 651 in baseline),
`uv run pytest` (29 passed), OpenAPI contract generation, and both frontend
builds pass.
