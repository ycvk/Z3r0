from http import HTTPStatus

from fastapi.responses import FileResponse

from handler.common.http import raise_api_error
from middleware.system_user import AuthUser
from schema.common.responses import CommonResponse
from schema.agent.credentials import CreateSessionCredentialRequest
from schema.common.responses import CommonResponse
from schema.flow.flows import CreateFlowRequest, FlowInputRequest, ListFlowsResponse
from service.agent import reports as agent_reports
from service.common.pagination import paginated_payload
from service.flow.records import (
    FlowValidationError,
    add_flow_credential,
    answer_flow_input,
    cancel_flow,
    create_flow,
    get_flow,
    list_flows,
    remove_flow_credential,
)


async def create_flow_handler(request: CreateFlowRequest, user: AuthUser) -> CommonResponse:
    try:
        flow = await create_flow(request, user)
    except FlowValidationError as exc:
        message = str(exc)
        if message.endswith("not found"):
            raise_api_error(HTTPStatus.NOT_FOUND, message)
        raise_api_error(HTTPStatus.BAD_REQUEST, message)
    return CommonResponse(data=flow)


async def list_flows_handler(page: int, size: int, user: AuthUser) -> CommonResponse:
    flows = await list_flows(user, page=page, size=size)
    return CommonResponse(data=ListFlowsResponse(**paginated_payload(flows, flows.items)))


async def get_flow_handler(public_id: str, user: AuthUser) -> CommonResponse:
    flow = await get_flow(public_id, user)
    if flow is None:
        raise_api_error(HTTPStatus.NOT_FOUND, "flow not found")
    return CommonResponse(data=flow)


async def cancel_flow_handler(public_id: str, user: AuthUser) -> CommonResponse:
    flow = await cancel_flow(public_id, user)
    if flow is None:
        raise_api_error(HTTPStatus.NOT_FOUND, "flow not found")
    return CommonResponse(message="flow canceled", data=flow)


async def submit_flow_input_handler(public_id: str, request: FlowInputRequest, user: AuthUser) -> CommonResponse:
    try:
        flow = await answer_flow_input(public_id, user, request.text)
    except FlowValidationError as exc:
        raise_api_error(HTTPStatus.BAD_REQUEST, str(exc))
    if flow is None:
        raise_api_error(HTTPStatus.NOT_FOUND, "flow not found")
    return CommonResponse(data=flow)


async def download_flow_report_handler(public_id: str, user: AuthUser) -> FileResponse:
    flow = await get_flow(public_id, user)
    if flow is None:
        raise_api_error(HTTPStatus.NOT_FOUND, "flow not found")
    if not flow.report_id:
        raise_api_error(HTTPStatus.NOT_FOUND, "report not found")
    try:
        path = agent_reports.resolve_report_download_path(flow.report_id)
    except FileNotFoundError:
        raise_api_error(HTTPStatus.NOT_FOUND, "report not found")
    except ValueError:
        raise_api_error(HTTPStatus.NOT_FOUND, "report not found")
    return FileResponse(path, filename=agent_reports.report_download_filename(path), media_type="text/markdown")


async def add_flow_credential_handler(
    public_id: str,
    request: CreateSessionCredentialRequest,
    user: AuthUser,
) -> CommonResponse:
    try:
        flow = await add_flow_credential(public_id, request, user)
    except (FlowValidationError, ValueError) as exc:
        raise_api_error(HTTPStatus.BAD_REQUEST, str(exc))
    if flow is None:
        raise_api_error(HTTPStatus.NOT_FOUND, "flow not found")
    return CommonResponse(message="credential saved", data=flow)


async def remove_flow_credential_handler(
    public_id: str,
    credential_id: int,
    user: AuthUser,
) -> CommonResponse:
    try:
        flow = await remove_flow_credential(public_id, credential_id, user)
    except FlowValidationError as exc:
        raise_api_error(HTTPStatus.NOT_FOUND, str(exc))
    if flow is None:
        raise_api_error(HTTPStatus.NOT_FOUND, "flow not found")
    return CommonResponse(message="credential removed", data=flow)
