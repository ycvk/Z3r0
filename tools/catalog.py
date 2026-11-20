"""Which tools each agent mounts in chat sessions and in Flow-bound sessions."""

from core.agent.constants import DEFAULT_AGENT_CODE, SPECIALIST_AGENT_CODES
from core.agent.specs import AgentSpec, SubagentMount, ToolMount
from tools.credentials import list_credentials, save_credential
from tools.flow import ask, done, patch_subtask_list, submit_subtask_list, terminal
from tools.fofa import fofa_search
from tools.reports import export_report
from tools.sandbox import (
    cancel_sandbox_async_job,
    execute_async_command,
    execute_sync_command,
    load_skill,
    read_sandbox_command_output,
)
from tools.search import web_search
from tools.traffic import get_traffic_detail, search_traffic
from tools.work_project import (
    activate_work_project_work_item,
    block_work_project_work_item,
    cancel_work_project_work_item,
    complete_current_work_project,
    create_work_project_work_item,
    get_work_project_work_item,
    invalidate_work_project_evidence_record,
    list_work_project_assets,
    list_work_project_attack_paths,
    list_work_project_evidence,
    list_work_project_findings,
    list_work_project_relations,
    list_work_project_work_items,
    load_work_project_context,
    merge_work_project_asset_records,
    record_work_project_evidence,
    record_work_project_work_log,
    reopen_work_project_work_item,
    review_work_project_work_item,
    save_work_project_asset,
    save_work_project_attack_path,
    save_work_project_finding,
    save_work_project_relation,
    submit_work_project_work_item_review,
    update_work_project_work_item_plan,
    update_work_project_work_item_target,
)


WORK_PROJECT_TOOLS = (
    ToolMount(load_work_project_context, requires_work_project=True),
    ToolMount(list_work_project_work_items, requires_work_project=True),
    ToolMount(get_work_project_work_item, requires_work_project=True),
    ToolMount(activate_work_project_work_item, requires_work_project=True),
    ToolMount(update_work_project_work_item_target, requires_work_project=True),
    ToolMount(block_work_project_work_item, requires_work_project=True),
    ToolMount(submit_work_project_work_item_review, requires_work_project=True),
    ToolMount(record_work_project_work_log, requires_work_project=True),
)

WORK_PROJECT_GOVERNANCE_TOOLS = (
    ToolMount(create_work_project_work_item, requires_work_project=True),
    ToolMount(update_work_project_work_item_plan, requires_work_project=True),
    ToolMount(review_work_project_work_item, requires_work_project=True),
    ToolMount(cancel_work_project_work_item, requires_work_project=True),
    ToolMount(reopen_work_project_work_item, requires_work_project=True),
)

WORK_PROJECT_RECORD_TOOLS = (
    ToolMount(list_work_project_assets, requires_work_project=True),
    ToolMount(save_work_project_asset, requires_work_project=True),
    ToolMount(list_work_project_relations, requires_work_project=True),
    ToolMount(list_work_project_evidence, requires_work_project=True),
    ToolMount(record_work_project_evidence, requires_work_project=True),
    ToolMount(invalidate_work_project_evidence_record, requires_work_project=True),
    ToolMount(list_work_project_findings, requires_work_project=True),
    ToolMount(save_work_project_finding, requires_work_project=True),
    ToolMount(save_work_project_relation, requires_work_project=True),
    ToolMount(list_work_project_attack_paths, requires_work_project=True),
    ToolMount(save_work_project_attack_path, requires_work_project=True),
)

SANDBOX_TOOLS = (
    ToolMount(execute_sync_command, requires_sandbox_container=True, runs_sandbox_commands=True),
    ToolMount(read_sandbox_command_output, requires_sandbox_container=True),
    ToolMount(
        execute_async_command,
        requires_sandbox_container=True,
        runs_sandbox_commands=True,
        ends_turn_on_dispatch=True,
    ),
    ToolMount(cancel_sandbox_async_job, requires_sandbox_container=True),
    ToolMount(load_skill, requires_sandbox_container=True, loads_sandbox_skills=True),
)

CREDENTIAL_TOOLS = (
    ToolMount(save_credential),
    ToolMount(list_credentials),
)
TRAFFIC_TOOLS = (
    ToolMount(search_traffic),
    ToolMount(get_traffic_detail),
)


SPECIALIST_TOOLS = (
    *SANDBOX_TOOLS,
    *CREDENTIAL_TOOLS,
    *TRAFFIC_TOOLS,
    *WORK_PROJECT_TOOLS,
    *WORK_PROJECT_RECORD_TOOLS,
)

FOFA_TOOLS = (
    ToolMount(fofa_search, requires_fofa_config=True),
)

PRIMARY_TOOLS = (
    *CREDENTIAL_TOOLS,
    *TRAFFIC_TOOLS,
    *WORK_PROJECT_TOOLS,
    *WORK_PROJECT_GOVERNANCE_TOOLS,
    *WORK_PROJECT_RECORD_TOOLS,
    ToolMount(merge_work_project_asset_records, requires_work_project=True),
    ToolMount(complete_current_work_project, requires_work_project=True),
)

FLOW_SANDBOX_TOOLS = (
    ToolMount(terminal, requires_sandbox_container=True, runs_sandbox_commands=True, ends_turn_on_dispatch=True),
    ToolMount(read_sandbox_command_output, requires_sandbox_container=True),
    ToolMount(cancel_sandbox_async_job, requires_sandbox_container=True),
    ToolMount(load_skill, requires_sandbox_container=True, loads_sandbox_skills=True),
)

FLOW_CONTROL_TOOLS = (
    ToolMount(web_search),
    # Each of these sets the one signal the Flow controller reads after the turn.
    ToolMount(submit_subtask_list, ends_turn_on_dispatch=True),
    ToolMount(patch_subtask_list, ends_turn_on_dispatch=True),
    ToolMount(done, ends_turn_on_dispatch=True),
    ToolMount(ask, ends_turn_on_dispatch=True),
    ToolMount(export_report, exports_reports=True),
)

def _flow_specialist_tools(chat_tools: tuple[ToolMount, ...]) -> tuple[ToolMount, ...]:
    remaining = tuple(mount for mount in chat_tools if mount not in SANDBOX_TOOLS)
    return (*FLOW_SANDBOX_TOOLS, ToolMount(web_search), *remaining)


def _specialist_spec(code: str) -> AgentSpec:
    chat_tools = (*SPECIALIST_TOOLS, *FOFA_TOOLS) if code == "cie" else SPECIALIST_TOOLS
    return AgentSpec(code=code, tools=chat_tools, flow_tools=_flow_specialist_tools(chat_tools))


AGENT_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        code=DEFAULT_AGENT_CODE,
        tools=(*PRIMARY_TOOLS, ToolMount(export_report, exports_reports=True)),
        flow_tools=(*PRIMARY_TOOLS, *FLOW_SANDBOX_TOOLS, *FLOW_CONTROL_TOOLS, *FOFA_TOOLS),
        subagents=tuple(SubagentMount(code=code) for code in SPECIALIST_AGENT_CODES),
    ),
    *(_specialist_spec(code) for code in SPECIALIST_AGENT_CODES),
)
