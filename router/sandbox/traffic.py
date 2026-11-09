from fastapi import APIRouter, Depends, Path, Query

from handler.sandbox.traffic import (
    clear_traffic_handler,
    get_traffic_entry_handler,
    ingest_traffic_handler,
    list_traffic_handler,
)
from middleware.system_user import AuthUser, require_user
from router.common.responses import BAD_REQUEST_RESPONSE, COMMON_ERROR_RESPONSES, not_found_response
from schema.common.responses import CommonResponse
from schema.sandbox.traffic import (
    ClearTrafficResponse,
    IngestTrafficRequest,
    IngestTrafficResponse,
    ListTrafficResponse,
    TrafficEntryDetailSchema,
)
from service.common.pagination import RESOURCE_PAGE_MAX_SIZE, RESOURCE_PAGE_SIZE


NOT_FOUND_RESPONSE = not_found_response("Traffic entry")

router = APIRouter(
    prefix="/traffic",
    tags=["traffic"],
    dependencies=[Depends(require_user)],
)


async def list_traffic_route(
    page: int = Query(1, ge=1),
    size: int = Query(RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE),
    sandbox_container_id: int | None = Query(None, gt=0),
    project_id: int | None = Query(None, gt=0),
    query: str = Query("", max_length=256),
    path: str = Query("", max_length=256),
    status: str = Query("", max_length=16),
    method: str = Query("", max_length=16),
    user: AuthUser = Depends(require_user),
) -> CommonResponse[ListTrafficResponse]:
    return await list_traffic_handler(
        page=page,
        size=size,
        sandbox_container_id=sandbox_container_id,
        project_id=project_id,
        query=query,
        path=path,
        status=status,
        method=method,
        user=user,
    )


async def get_traffic_entry_route(
    traffic_id: int = Path(..., gt=0),
    user: AuthUser = Depends(require_user),
) -> CommonResponse[TrafficEntryDetailSchema]:
    return await get_traffic_entry_handler(traffic_id=traffic_id, user=user)


async def ingest_traffic_route(
    request: IngestTrafficRequest,
    user: AuthUser = Depends(require_user),
) -> CommonResponse[IngestTrafficResponse]:
    return await ingest_traffic_handler(request=request, user=user)


async def clear_traffic_route(
    sandbox_container_id: int | None = Query(None, gt=0),
    project_id: int | None = Query(None, gt=0),
    user: AuthUser = Depends(require_user),
) -> CommonResponse[ClearTrafficResponse]:
    return await clear_traffic_handler(
        sandbox_container_id=sandbox_container_id,
        project_id=project_id,
        user=user,
    )


router.add_api_route(
    "",
    list_traffic_route,
    methods=["GET"],
    response_model=CommonResponse[ListTrafficResponse],
    responses=COMMON_ERROR_RESPONSES,
)
router.add_api_route(
    "/ingest",
    ingest_traffic_route,
    methods=["POST"],
    response_model=CommonResponse[IngestTrafficResponse],
    responses={**COMMON_ERROR_RESPONSES, **BAD_REQUEST_RESPONSE},
)
router.add_api_route(
    "",
    clear_traffic_route,
    methods=["DELETE"],
    response_model=CommonResponse[ClearTrafficResponse],
    responses=COMMON_ERROR_RESPONSES,
)
router.add_api_route(
    "/{traffic_id}",
    get_traffic_entry_route,
    methods=["GET"],
    response_model=CommonResponse[TrafficEntryDetailSchema],
    responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
