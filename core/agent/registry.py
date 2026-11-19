"""Agent declarations and session-bound SDK Agent construction."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from agents import (
    Agent,
    FunctionToolResult,
    Model,
    ModelSettings,
    RunContextWrapper,
    Tool,
    ToolsToFinalOutputResult,
)

from config import AgentConfig, WORKSPACE, get_config
from core.agent.constants import DEFAULT_AGENT_CODE
from core.agent.instructions import build_instructions
from core.agent.models import build_model_retry_settings, build_openai_model
from core.agent.specs import AgentSpec, ToolMount
from core.agent.tool_snapshot import AgentToolSnapshot
from core.delegation.subagents import build_subagent_tools
from core.flow.prompts import FLOW_PRIMARY_ADDENDUM, FLOW_SPECIALIST_ADDENDUM
from core.runtime.context import AgentRuntimeContext
from schema.sandbox.async_jobs import SandboxAsyncJobStatus


_ToolUseBehavior = Callable[
    [RunContextWrapper[AgentRuntimeContext], list[FunctionToolResult]],
    Awaitable[ToolsToFinalOutputResult],
]


def _end_turn_after_dispatch(dispatch_tool_names: frozenset[str]) -> _ToolUseBehavior:
    async def end_turn_after_dispatch(
        _ctx: RunContextWrapper[AgentRuntimeContext],
        results: list[FunctionToolResult],
    ) -> ToolsToFinalOutputResult:
        """End the turn the moment an async command is successfully dispatched.

        Dispatching background work is turn-terminal: the agent stops here and is
        woken by the completion notification, so it can never poll for the result.
        A failed dispatch is not terminal, letting the agent react in the same turn.
        """
        for result in results:
            if getattr(result.tool, "name", None) not in dispatch_tool_names:
                continue
            try:
                payload = json.loads(result.output)
                status = payload.get("status")
                barrier = payload.get("barrier")
            except (TypeError, ValueError):
                status = None
                barrier = None
            if status == SandboxAsyncJobStatus.RUNNING.value or barrier == "ask":
                return ToolsToFinalOutputResult(is_final_output=True, final_output=result.output)
        return ToolsToFinalOutputResult(is_final_output=False)

    return end_turn_after_dispatch


class AgentRegistry:
    def __init__(self, specs: tuple[AgentSpec, ...]) -> None:
        self._specs: dict[str, AgentSpec] = {spec.code: spec for spec in specs}
        self._codes_cache: tuple[str, ...] | None = None
        self._code_to_name_cache: dict[str, str] | None = None
        # Reject self-mounts and circular subagent chains at boot.
        self._validate_subagent_graph()

    @property
    def specs(self) -> tuple[AgentSpec, ...]:
        return tuple(self._specs.values())

    def codes(self) -> list[str]:
        if self._codes_cache is None:
            configured = set(get_config().agents.keys())
            self._codes_cache = tuple(code for code in self._specs if code in configured)
        return list(self._codes_cache)

    def code_to_name(self) -> dict[str, str]:
        if self._code_to_name_cache is None:
            cfg = get_config()
            self._code_to_name_cache = {code: cfg.agents[code].name for code in self.codes()}
        return self._code_to_name_cache

    def has(self, agent_code: str) -> bool:
        return agent_code in self.codes()

    def bind(self, tool_snapshot: AgentToolSnapshot) -> SessionAgentGraph:
        return SessionAgentGraph(self, tool_snapshot)

    def _spec(self, agent_code: str) -> AgentSpec:
        spec = self._specs.get(agent_code)
        if spec is None:
            raise ValueError(f"Agent spec not declared for code: {agent_code}")
        return spec

    def _validate_subagent_graph(self) -> None:
        for code in self._specs:
            self._check_subagent_chain(code, [code])

    def _check_subagent_chain(self, code: str, path: list[str]) -> None:
        spec = self._specs.get(code)
        if spec is None:
            return
        for mount in spec.subagents:
            if mount.code == code:
                raise ValueError(f"Agent {code} cannot mount itself as a subagent")
            if mount.code in path:
                chain = " -> ".join([*path, mount.code])
                raise ValueError(f"Circular subagent mount detected: {chain}")
            self._check_subagent_chain(mount.code, [*path, mount.code])

    def _build(self, spec: AgentSpec, cfg: AgentConfig, graph: SessionAgentGraph) -> Agent:
        agent_path = WORKSPACE / "agents" / spec.code
        soul = (agent_path / "SOUL.md").read_text(encoding="utf-8").strip()
        rules = (agent_path / "AGENTS.md").read_text(encoding="utf-8").strip()
        flow_bound = graph.tool_snapshot.flow_bound
        mounts = spec.flow_tools if flow_bound else spec.tools
        instructions = build_instructions(
            soul,
            rules,
            graph.tool_snapshot.sandbox_skill_metadata,
            has_sandbox_container=graph.tool_snapshot.sandbox_container_id is not None,
            include_sandbox_commands=any(mount.runs_sandbox_commands for mount in mounts),
            include_sandbox_skills=any(mount.loads_sandbox_skills for mount in mounts),
            include_work_project_tools=(
                graph.tool_snapshot.work_project_id is not None
                and any(mount.requires_work_project for mount in mounts)
            ),
            include_delegation_tools=bool(spec.subagents),
            include_report_tools=any(mount.exports_reports for mount in mounts),
            flow_addendum=(
                FLOW_PRIMARY_ADDENDUM if spec.code == DEFAULT_AGENT_CODE else FLOW_SPECIALIST_ADDENDUM
            ) if flow_bound else "",
        )

        tools: list[Tool] = [
            mount.tool for mount in mounts
            if _tool_mount_available(mount, graph.tool_snapshot)
        ]
        if spec.subagents:
            tools.extend(_build_subagent_tools(spec, self))

        return Agent(
            name=cfg.name,
            model=build_openai_model(cfg),
            model_settings=ModelSettings(
                parallel_tool_calls=False,
                retry=build_model_retry_settings(),
            ),
            instructions=lambda run_context, _: _assemble_instructions(
                stable_prefix=instructions,
                work_project_context=run_context.context.work_project_context,
                rag_context=run_context.context.rag_context,
            ),
            tools=tools,
            tool_use_behavior=_end_turn_after_dispatch(
                frozenset(mount.tool.name for mount in mounts if mount.ends_turn_on_dispatch)
            ),
        )


class SessionAgentGraph:
    """Single-owner container for an Agent and its httpx client.

    Each driver (main session or one sub-agent) binds its own graph, so
    disposing one graph never tears down a sibling's in-flight HTTP stream.
    """

    def __init__(self, registry: AgentRegistry, tool_snapshot: AgentToolSnapshot) -> None:
        self._registry = registry
        self.tool_snapshot = tool_snapshot
        self._agents: dict[str, Agent] = {}
        self._models: list[Model] = []

    def code_to_name(self) -> dict[str, str]:
        return self._registry.code_to_name()

    def get(self, agent_code: str) -> Agent:
        cached = self._agents.get(agent_code)
        if cached is not None:
            return cached

        spec = self._registry._spec(agent_code)
        cfg = get_config().agents.get(agent_code)
        if cfg is None:
            raise ValueError(f"Agent config missing for code: {agent_code}")

        agent = self._registry._build(spec, cfg, self)
        self._agents[agent_code] = agent
        self._models.append(agent.model)
        return agent

    async def close(self) -> None:
        for model in self._models:
            await model.close()
        self._agents.clear()
        self._models.clear()


def _assemble_instructions(
    *,
    stable_prefix: str,
    work_project_context: str,
    rag_context: str,
) -> str:
    """Append changing turn context after the cacheable instruction prefix."""
    return "\n\n".join(
        part
        for part in (
            stable_prefix,
            work_project_context,
            rag_context,
        )
        if part
    )


def _tool_mount_available(mount: ToolMount, snapshot: AgentToolSnapshot) -> bool:
    return not (
        (mount.requires_sandbox_container and snapshot.sandbox_container_id is None)
        or (mount.requires_work_project and snapshot.work_project_id is None)
        or (mount.requires_fofa_config and not _fofa_config_available())
    )


def _fofa_config_available() -> bool:
    cfg = get_config().fofa
    return cfg.enabled and bool(cfg.base_url.strip()) and bool(cfg.api_key.strip())


def _build_subagent_tools(spec: AgentSpec, registry: AgentRegistry) -> list[Tool]:
    return build_subagent_tools(
        spec.code,
        (mount.code for mount in spec.subagents),
        registry=registry,
    )
