from http import HTTPStatus

from handler.common.http import raise_api_error
from middleware.system_user import AuthUser
from schema.common.responses import CommonResponse
from schema.sandbox.traffic import (
    ClearTrafficResponse,
    IngestTrafficRequest,
    IngestTrafficResponse,
    ListTrafficResponse,
)
from service.common.pagination import paginated_payload
from service.sandbox.traffic import (
    clear_traffic,
    get_traffic_entry,
    ingest_traffic,
    query_traffic,
)


async def list_traffic_handler(
    *,
    page: int,
    size: int,
    sandbox_container_id: int | None,
    project_id: int | None,
    query: str,
    path: str,
    status: str,
    method: str,
    user: AuthUser,
) -> CommonResponse:
    result = await query_traffic(
        user_id=user.id,
        user_role=user.role,
        sandbox_container_id=sandbox_container_id,
        project_id=project_id,
        query=query,
        path=path,
        status=status,
        method=method,
        page=page,
        size=size,
    )
    return CommonResponse(data=ListTrafficResponse(**paginated_payload(result, result.items)))


async def get_traffic_entry_handler(traffic_id: int, user: AuthUser) -> CommonResponse:
    detail = await get_traffic_entry(traffic_id, user_id=user.id, user_role=user.role)
    if detail is None:
        raise_api_error(HTTPStatus.NOT_FOUND, "traffic entry not found")
    return CommonResponse(data=detail)


async def ingest_traffic_handler(request: IngestTrafficRequest, user: AuthUser) -> CommonResponse:
    count = await ingest_traffic(request, owner_id=user.id)
    return CommonResponse(message="traffic ingested", data=IngestTrafficResponse(ingested=count))


async def clear_traffic_handler(
    *,
    sandbox_container_id: int | None,
    project_id: int | None,
    user: AuthUser,
) -> CommonResponse:
    deleted = await clear_traffic(
        user_id=user.id,
        user_role=user.role,
        sandbox_container_id=sandbox_container_id,
        project_id=project_id,
    )
    return CommonResponse(message="traffic cleared", data=ClearTrafficResponse(deleted=deleted))
