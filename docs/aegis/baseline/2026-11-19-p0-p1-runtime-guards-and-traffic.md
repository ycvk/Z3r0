# Z3r0 Baseline at v0.3.0 (Runtime Guards, Credentials, and Traffic Capture)

Date: `2026-11-19`
Status: `current dual-baseline snapshot`
Supersedes: `docs/aegis/baseline/2026-08-26-flow-runtime.md`
ADR: `docs/aegis/adr/ADR-0001-flow-is-a-separate-aggregate.md`

## 1. Purpose

Record product and architecture truth at v0.3.0 after landing Token Ledger,
Credentials Vault, Command Guard one-shot exact-command grants, sandbox
HTTP/HTTPS MITM traffic capture with background synchronization, and upstream
low-volume sandbox alignment.

## 2. Product / Requirement Baseline

### 2.1 Current Truth

- Operators launch autonomous Flows on `/flows` against a running sandbox; the
  controller advances plan -> subtask -> `ask`/`done` -> report.
- Session and Flow summaries expose cumulative LLM request counts and input,
  output, cached, and total tokens.
- Operators and agents store task or project credentials in the Credentials
  Vault; active credentials are injected into subsequent turns.
- Destructive shell, SQL, and HTTP commands are intercepted by Command Guard.
  Approvals apply once to the exact intercepted command and can only be granted
  by an authenticated operator turn or Flow input reply.
- Sandbox HTTP and HTTPS egress exchanges are decrypted with a sandbox-local
  MITM root CA, synced periodically into PostgreSQL, scoped to container and
  work project ownership, and searchable via `/api/traffic` and agent tools.
- Optional FOFA asset search (`fofa_search`) is mounted when enabled and
  configured in system settings.

### 2.2 Non-negotiables

1. Authorized-scope operation only; no unsandboxed host execution
2. Tenant isolation on sessions, Flows, work projects, credentials, and captured traffic
3. Command Guard grants are operator-issued, exact-command, and one-shot
4. Flow progression is owned by `core/flow/controller.py`
5. Real proxy, database, and runtime errors surface without silent suppression

## 3. Architecture / Runtime Boundary Baseline

- Chat owner: `core/runtime/session.py` (`AgentSession`)
- Flow owner: `core/flow/controller.py` + `service/flow/`
- Token Ledger owner: `service/agent/session_state.py` (`record_session_usage`)
- Credentials Vault owner: `service/agent/credentials.py` + `core/tools/credentials.py`
- Command Guard owner: `core/sandbox/command_guard.py`
- Traffic capture owner: `sandbox/proxy/mitm.go` + `sandbox/proxy/traffic.go` + `service/sandbox/traffic.py` + `core/tools/traffic.py`
- Persistence: PostgreSQL via SQLModel (`0.3.0`)
