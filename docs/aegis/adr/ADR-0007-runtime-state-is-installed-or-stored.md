# ADR-0007 Runtime state is installed at startup or stored in the database

Status: `accepted`
Date: `2026-11-19`
Owners: `app.py` (installation) · `core/sandbox/command_guard.py` and `service/flow/records.py` (stored state) · `core/runtime/coordination.py` (runtime wiring)

## Decision

State that an operator relies on across a restart is stored:

- Command Guard keeps the intercepted command of a session and its one-run
  approvals as rows of `sandbox_guarded_commands`. The rows are deleted with
  the session.
- The operator's answer to a waiting Flow is stored in `flows.pending_input`
  until the controller takes it. `service/flow/runs.py` only wakes a controller
  that is already waiting; a controller started after a restart reads the
  stored answer.

State that wires the runtime together is installed by `app.py`:

- `install_agent_runtime()` installs `RuntimePorts`, `RuntimeCoordination`,
  and the session pool. `RuntimeCoordination` carries how the session pool,
  the subagent runtime, and sandbox command jobs reach each other. Using any
  of the three before installation raises.
- No module registers a callback at import time, and no function imports a
  project module at call time.
- `service` calls the session pool through its public methods;
  `service/flow/controller.py` creates a Flow and schedules it in `start_flow`.

The remaining module-level state is local to one process:

| Kind | Where | On restart |
| --- | --- | --- |
| Handles of background loops | traffic sync, container status monitor, report cleanup, knowledge processing, Flow runtime | Started and stopped by the `app.py` lifespan. |
| Work in flight | subagent drivers, sandbox command jobs, Flow controller tasks | Their records are stored; startup recovers or settles them. |
| Flow barriers | `core/flow/barriers.py` | Live for one turn; the controller repeats the subtask prompt. |
| Locks, wake-up signals, HTTP clients, short caches | next to the code that uses them | Rebuilt on demand. |

## Consequences

- An approval or an answer given before a restart still applies after it.
- A destructive command check and an approval each cost a database round trip.
- Tests that exercise Command Guard use a real session row.

## Evidence

2026-11-19, local: `tests/test_command_guard.py` covers the stored pending
command and approval and their deletion with the session.
`tests/test_flow_lifecycle.py` answers a waiting Flow while no controller
runs, starts the Flow runtime, and sees the controller take the answer and
the approval. `tests/test_runtime_ports.py` covers use before installation.
