"""Runs the fixed tasks as Flows against a running Z3r0 instance and one running sandbox container.

    Z3R0_EVAL_PASSWORD=... uv run python -m evals.run \\
        --base-url http://127.0.0.1:8000 --email admin@z3r0.local --container-id 1

Task files are placed with `docker exec`, so the sandbox container must run on this machine.
Every run calls the configured models. Results are written to `evals/results/`.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from evals.scoring import FlowOutcome, score
from evals.tasks import TASKS, EvalTask

ACCESS_TOKEN_HEADER = "X-Z3r0-Access-Token"
SERVICE_PORT = 18081
_POLL_SECONDS = 5
_FINISHED = frozenset({"completed", "failed", "cancelled"})


def docker_exec(container: str, command: str, *, stdin: str | None = None, detach: bool = False) -> str:
    args = ["docker", "exec", *(["-d"] if detach else ["-i"]), container, "sh", "-c", command]
    done = subprocess.run(args, input=stdin, capture_output=True, text=True, check=True)
    return done.stdout


def prepare_task(container: str, task: EvalTask, flag: str) -> None:
    """Put the task's files in the container, run its setup, and start its service."""
    for path, content in task.files.items():
        body = content.replace("{flag}", flag).replace("{port}", str(SERVICE_PORT))
        docker_exec(container, f"mkdir -p '{Path(path).parent}' && cat > '{path}'", stdin=body)
    for command in task.setup:
        docker_exec(container, command)
    if task.service:
        docker_exec(container, task.service, detach=True)
        url = task.service_url.replace("{port}", str(SERVICE_PORT))
        docker_exec(container, f"for i in $(seq 1 50); do curl -fsS -o /dev/null '{url}' && exit 0; sleep 0.2; done; exit 1")


def cleanup_task(container: str, task: EvalTask) -> None:
    if task.service:
        docker_exec(container, f"pkill -f '[{task.service[0]}]{task.service[1:]}' || true")
    docker_exec(container, f"rm -rf '/tmp/eval/{task.id}'")


def run_task(client: httpx.Client, container_id: int, container: str, task: EvalTask) -> dict[str, Any]:
    flag = f"FLAG{{{secrets.token_hex(8)}}}"
    prepare_task(container, task, flag)
    started = time.monotonic()
    try:
        created = client.post("/api/flows", json={
            "goal": task.goal.replace("{port}", str(SERVICE_PORT)),
            "sandbox_container_id": container_id,
        })
        created.raise_for_status()
        public_id = created.json()["data"]["public_id"]

        flow: dict[str, Any] = {}
        timed_out = False
        while True:
            flow = client.get(f"/api/flows/{public_id}").raise_for_status().json()["data"]
            if flow["status"] in _FINISHED or flow["status"] == "waiting":
                break
            if time.monotonic() - started > task.timeout_seconds:
                timed_out = True
                break
            time.sleep(_POLL_SECONDS)

        report = ""
        if flow["status"] == "completed":
            report = client.get(f"/api/flows/{public_id}/report").raise_for_status().text
        if flow["status"] not in _FINISHED:
            client.post(f"/api/flows/{public_id}/cancel").raise_for_status()
    finally:
        cleanup_task(container, task)

    result = score(task, flag, FlowOutcome(
        status=flow["status"],
        report=report,
        subtask_summaries=tuple(item.get("summary") or "" for item in flow.get("subtasks", [])),
        ask_question=flow.get("ask_question") or "",
        error=flow.get("error") or "",
        timed_out=timed_out,
    ))
    return {
        "task": task.id,
        "passed": result.passed,
        "reasons": list(result.reasons),
        "status": flow["status"],
        "seconds": round(time.monotonic() - started, 1),
        "flow": public_id,
        **{key: flow.get(key, 0) for key in ("llm_requests", "input_tokens", "cached_tokens", "output_tokens")},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--container-id", type=int, required=True, help="a running sandbox container the user manages")
    parser.add_argument("--task", action="append", choices=[task.id for task in TASKS], help="default: every task")
    parser.add_argument("--runs", type=int, default=1, help="runs per task")
    parser.add_argument("--label", default="", help="what this run measures, stored with the results")
    args = parser.parse_args()
    password = os.environ.get("Z3R0_EVAL_PASSWORD")
    if not password:
        parser.error("set Z3R0_EVAL_PASSWORD")
    tasks = [task for task in TASKS if not args.task or task.id in args.task]

    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        login = client.post("/api/system-users/login", json={"email": args.email, "password": password})
        login.raise_for_status()
        client.headers[ACCESS_TOKEN_HEADER] = login.json()["data"]["token"]
        listed = client.get("/api/sandbox-containers", params={"page": 1, "size": 100}).raise_for_status()
        container = next(
            (item for item in listed.json()["data"]["items"] if item["id"] == args.container_id), None,
        )
        if container is None or str(container["status"]).lower() != "running":
            parser.error(f"sandbox container {args.container_id} is not a running container of this user")

        rows = []
        for task in tasks:
            for _ in range(args.runs):
                row = run_task(client, args.container_id, container["container_name"], task)
                rows.append(row)
                verdict = "PASS" if row["passed"] else "FAIL"
                print(f"{verdict} {row['task']:<28} {row['status']:<10} {row['seconds']:>7}s "
                      f"{row['llm_requests']:>4} req  {'; '.join(row['reasons'])}", flush=True)

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"label": args.label, "base_url": args.base_url, "rows": rows}, indent=2) + "\n")
    passed = sum(1 for row in rows if row["passed"])
    print(f"{passed}/{len(rows)} passed -> {out}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
