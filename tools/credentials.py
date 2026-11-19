from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from core.runtime.context import AgentRuntimeContext
from schema.common.tool_results import ToolResultSchema, ToolResultStatusSchema, ToolResultTypeSchema
from service.agent import credentials as credential_service


def _cred_result(status: ToolResultStatusSchema, output: str) -> str:
    return ToolResultSchema(
        status=status,
        type=ToolResultTypeSchema.CREDENTIAL,
        output=output,
    ).model_dump_json()


@function_tool
async def save_credential(
    ctx: RunContextWrapper[AgentRuntimeContext],
    target: str,
    secret: str,
    username: str = "",
    credential_type: str = "password",
    note: str = "",
) -> str:
    """Save or update a discovered or operator-supplied credential in the task Credentials Vault.

    Saved credentials persist across subtasks and context compactions and are
    automatically injected into subsequent Agent turns.

    Args:
        target: Target host, URL, or service identifier (e.g. 'http://10.0.0.5:8080/login').
        secret: Password, token, cookie, API key, or flag value.
        username: Account username or identifier if applicable.
        credential_type: One of 'password', 'token', 'cookie', 'api_key', 'ssh_key', 'flag', 'other'.
        note: Short provenance or privilege note.
    """
    try:
        saved = await credential_service.upsert_credential(
            owner_id=ctx.context.user.id,
            session_id=ctx.context.session_id,
            project_id=ctx.context.work_project_id,
            target=target,
            credential_type=credential_type,
            username=username,
            secret=secret,
            note=note,
        )
    except Exception as exc:
        return _cred_result(ToolResultStatusSchema.ERROR, str(exc) or "Failed to save credential")
    return _cred_result(
        ToolResultStatusSchema.SUCCESS,
        json.dumps(saved.model_dump(mode="json"), ensure_ascii=False),
    )


@function_tool
async def list_credentials(
    ctx: RunContextWrapper[AgentRuntimeContext],
    target: str = "",
) -> str:
    """List credentials stored in the task Credentials Vault.

    Args:
        target: Optional substring filter for the target host/URL.
    """
    try:
        items = await credential_service.list_credentials_for_context(
            session_id=ctx.context.session_id,
            project_id=ctx.context.work_project_id,
            target=target,
        )
    except Exception as exc:
        return _cred_result(ToolResultStatusSchema.ERROR, str(exc) or "Failed to list credentials")
    return _cred_result(
        ToolResultStatusSchema.SUCCESS,
        json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False),
    )
