from dataclasses import dataclass

from agents import Tool


@dataclass(frozen=True, slots=True)
class ToolMount:
    tool: Tool
    requires_sandbox_container: bool = False
    requires_work_project: bool = False
    requires_fofa_config: bool = False
    # Each of these adds the matching guidance to the instructions of an agent that mounts the tool.
    runs_sandbox_commands: bool = False
    loads_sandbox_skills: bool = False
    exports_reports: bool = False
    # A successful dispatch from this tool ends the turn; the completion notification wakes the agent.
    ends_turn_on_dispatch: bool = False


@dataclass(frozen=True, slots=True)
class SubagentMount:
    code: str


@dataclass(frozen=True, slots=True)
class AgentSpec:
    code: str
    tools: tuple[ToolMount, ...] = ()
    # Mounted in place of `tools` when the session is bound to a Flow.
    flow_tools: tuple[ToolMount, ...] = ()
    subagents: tuple[SubagentMount, ...] = ()
