from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import FileResponse

from handler.flow.flows import (
    cancel_flow_handler,
    create_flow_handler,
    download_flow_report_handler,
    get_flow_handler,
    list_flows_handler,
    submit_flow_input_handler,
)
from middleware.system_user import AuthUser, require_user
from router.common.responses import BAD_REQUEST_RESPONSE, COMMON_ERROR_RESPONSES, not_found_response
from schema.common.responses import CommonResponse
from schema.flow.flows import (
    CreateFlowRequest,
    FlowInputRequest,
    FlowSchema,
    ListFlowsResponse,
)
from service.common.pagination import RESOURCE_PAGE_MAX_SIZE, RESOURCE_PAGE_SIZE


NOT_FOUND_RESPONSE = not_found_response("Flow")

router = APIRouter(
    prefix="/flows",
    tags=["flows"],
    dependencies=[Depends(require_user)],
)


async def list_flows_route(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE),
    user: AuthUser = Depends(require_user),
) -> CommonResponse[ListFlowsResponse]:
    return await list_flows_handler(page=page, size=size, user=user)


async def create_flow_route(
    request: CreateFlowRequest,
    user: AuthUser = Depends(require_user),
) -> CommonResponse[FlowSchema]:
    return await create_flow_handler(request=request, user=user)


async def get_flow_route(
    public_id: str = Path(min_length=1, max_length=64),
    user: AuthUser = Depends(require_user),
) -> CommonResponse[FlowSchema]:
    return await get_flow_handler(public_id=public_id, user=user)


async def submit_flow_input_route(
    request: FlowInputRequest,
    public_id: str = Path(min_length=1, max_length=64),
    user: AuthUser = Depends(require_user),
) -> CommonResponse[FlowSchema]:
    return await submit_flow_input_handler(public_id=public_id, request=request, user=user)


async def cancel_flow_route(
    public_id: str = Path(min_length=1, max_length=64),
    user: AuthUser = Depends(require_user),
) -> CommonResponse[FlowSchema]:
    return await cancel_flow_handler(public_id=public_id, user=user)


async def download_flow_report_route(
    public_id: str = Path(min_length=1, max_length=64),
    user: AuthUser = Depends(require_user),
) -> FileResponse:
    return await download_flow_report_handler(public_id=public_id, user=user)


router.add_api_route(
    "",
    create_flow_route,
    methods=["POST"],
    response_model=CommonResponse[FlowSchema],
    responses={**COMMON_ERROR_RESPONSES, **BAD_REQUEST_RESPONSE, **NOT_FOUND_RESPONSE},
)
router.add_api_route(
    "",
    list_flows_route,
    methods=["GET"],
    response_model=CommonResponse[ListFlowsResponse],
    responses=COMMON_ERROR_RESPONSES,
)
router.add_api_route(
    "/{public_id}",
    get_flow_route,
    methods=["GET"],
    response_model=CommonResponse[FlowSchema],
    responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
router.add_api_route(
    "/{public_id}/input",
    submit_flow_input_route,
    methods=["POST"],
    response_model=CommonResponse[FlowSchema],
    responses={**COMMON_ERROR_RESPONSES, **BAD_REQUEST_RESPONSE, **NOT_FOUND_RESPONSE},
)
router.add_api_route(
    "/{public_id}/cancel",
    cancel_flow_route,
    methods=["POST"],
    response_model=CommonResponse[FlowSchema],
    responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
router.add_api_route(
    "/{public_id}/report",
    download_flow_report_route,
    methods=["GET"],
    responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
