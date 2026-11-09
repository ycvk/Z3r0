from core.agent.constants import DEFAULT_AGENT_CODE
from core.agent.specs import (
    CREDENTIAL_TOOLS,
    FOFA_TOOLS,
    TRAFFIC_TOOLS,
    WORK_PROJECT_GOVERNANCE_TOOLS,
    WORK_PROJECT_RECORD_TOOLS,
    WORK_PROJECT_TOOLS,
    AgentSpec,
    ToolMount,
)
from core.tools.flow import ask, done, patch_subtask_list, submit_subtask_list, terminal
from core.tools.reports import export_report
from core.tools.sandbox import (
    cancel_sandbox_async_job,
    execute_async_command,
    execute_sync_command,
    load_skill,
    read_sandbox_command_output,
)
from core.tools.search import web_search
from core.tools.work_project import (
    complete_current_work_project,
    merge_work_project_asset_records,
)


FLOW_SANDBOX_TOOLS = (
    ToolMount(terminal, requires_sandbox_container=True),
    ToolMount(read_sandbox_command_output, requires_sandbox_container=True),
    ToolMount(cancel_sandbox_async_job, requires_sandbox_container=True),
    ToolMount(load_skill, requires_sandbox_container=True),
)

FLOW_CONTROL_TOOLS = (
    ToolMount(web_search),
    ToolMount(submit_subtask_list),
    ToolMount(patch_subtask_list),
    ToolMount(done),
    ToolMount(ask),
    ToolMount(export_report),
)

_CHAT_COMMAND_TOOLS = (execute_sync_command, execute_async_command)


def flow_mounts_for_spec(spec: AgentSpec) -> tuple[ToolMount, ...]:
    if spec.code == DEFAULT_AGENT_CODE:
        return (
            *CREDENTIAL_TOOLS,
            *TRAFFIC_TOOLS,
            *WORK_PROJECT_TOOLS,
            *WORK_PROJECT_GOVERNANCE_TOOLS,
            *WORK_PROJECT_RECORD_TOOLS,
            ToolMount(merge_work_project_asset_records, requires_work_project=True),
            ToolMount(complete_current_work_project, requires_work_project=True),
            *FLOW_SANDBOX_TOOLS,
            *FLOW_CONTROL_TOOLS,
            *FOFA_TOOLS,
        )
    remaining = tuple(
        mount for mount in spec.tools
        if mount.tool not in _CHAT_COMMAND_TOOLS
    )
    return (*FLOW_SANDBOX_TOOLS, ToolMount(web_search), *remaining)


def is_terminal_dispatch_tool(name: str | None) -> bool:
    return name in {execute_async_command.name, terminal.name}


def uses_sandbox_commands(spec: AgentSpec, *, flow_bound: bool) -> bool:
    mounts = flow_mounts_for_spec(spec) if flow_bound else spec.tools
    command_tools = (execute_sync_command, execute_async_command, terminal, load_skill, read_sandbox_command_output)
    return any(mount.tool in command_tools for mount in mounts)



