from fastapi.responses import FileResponse

from middleware.system_user import AuthUser
from schema.agent.credentials import CreateSessionCredentialRequest
from schema.common.responses import CommonResponse
from schema.flow.flows import CreateFlowRequest, FlowInputRequest, ListFlowsResponse
from service.agent import reports as agent_reports
from service.common.pagination import paginated_payload
from service.flow.records import (
    add_flow_credential,
    answer_flow_input,
    cancel_flow,
    create_flow,
    get_flow,
    list_flows,
    remove_flow_credential,
    resolve_flow_report_path,
)


async def create_flow_handler(request: CreateFlowRequest, user: AuthUser) -> CommonResponse:
    return CommonResponse(data=await create_flow(request, user))


async def list_flows_handler(page: int, size: int, user: AuthUser) -> CommonResponse:
    flows = await list_flows(user, page=page, size=size)
    return CommonResponse(data=ListFlowsResponse(**paginated_payload(flows, flows.items)))


async def get_flow_handler(public_id: str, user: AuthUser) -> CommonResponse:
    return CommonResponse(data=await get_flow(public_id, user))


async def cancel_flow_handler(public_id: str, user: AuthUser) -> CommonResponse:
    return CommonResponse(message="flow canceled", data=await cancel_flow(public_id, user))


async def submit_flow_input_handler(public_id: str, request: FlowInputRequest, user: AuthUser) -> CommonResponse:
    return CommonResponse(data=await answer_flow_input(public_id, user, request.text))


async def download_flow_report_handler(public_id: str, user: AuthUser) -> FileResponse:
    path = await resolve_flow_report_path(public_id, user)
    return FileResponse(path, filename=agent_reports.report_download_filename(path), media_type="text/markdown")


async def add_flow_credential_handler(
    public_id: str,
    request: CreateSessionCredentialRequest,
    user: AuthUser,
) -> CommonResponse:
    return CommonResponse(message="credential saved", data=await add_flow_credential(public_id, request, user))


async def remove_flow_credential_handler(
    public_id: str,
    credential_id: int,
    user: AuthUser,
) -> CommonResponse:
    flow = await remove_flow_credential(public_id, credential_id, user)
    return CommonResponse(message="credential removed", data=flow)
