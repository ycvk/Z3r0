from fastapi import APIRouter, Depends, Path, Query

from handler.work_project.records import (
    create_traffic_evidence_handler,
    create_work_project_credential_handler,
    delete_work_project_credential_handler,
    get_work_project_graph_handler,
    get_work_project_overview_handler,
    query_work_project_activity_handler,
    query_work_project_assets_handler,
    query_work_project_attack_paths_handler,
    query_work_project_credentials_handler,
    query_work_project_evidence_handler,
    query_work_project_findings_handler,
    query_work_project_work_items_handler,
)
from middleware.system_user import AuthUser, require_user
from router.common.responses import BAD_REQUEST_RESPONSE, COMMON_ERROR_RESPONSES, not_found_response
from schema.common.responses import CommonResponse
from schema.credential.credentials import CreateSessionCredentialRequest, SessionCredentialSchema
from schema.work_project.assets import WorkProjectAssetKind, WorkProjectAssetScope
from schema.work_project.evidence import WorkProjectEvidenceKind, WorkProjectEvidenceStatus
from schema.work_project.findings import WorkProjectFindingSeverity, WorkProjectFindingVerification
from schema.work_project.records import (
    CreateTrafficEvidenceRequest,
    DeleteWorkProjectCredentialResponse,
    QueryWorkProjectActivityResponse,
    QueryWorkProjectAssetsResponse,
    QueryWorkProjectAttackPathsResponse,
    QueryWorkProjectCredentialsResponse,
    QueryWorkProjectEvidenceResponse,
    QueryWorkProjectFindingsResponse,
    QueryWorkProjectWorkItemsResponse,
    WorkProjectEvidenceRecordSchema,
    WorkProjectGraphViewSchema,
    WorkProjectOverviewSchema,
)
from schema.work_project.workflow import WorkProjectWorkItemStatus
from service.common.pagination import RESOURCE_PAGE_MAX_SIZE, RESOURCE_PAGE_SIZE


NOT_FOUND_RESPONSE = not_found_response("Work project")
router = APIRouter(prefix="/work-projects/{id}", tags=["work-project-records"], dependencies=[Depends(require_user)])


@router.get("/overview", response_model=CommonResponse[WorkProjectOverviewSchema], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def get_work_project_overview_route(id: int, user: AuthUser = Depends(require_user)) -> CommonResponse:
    return await get_work_project_overview_handler(id, user)


@router.get("/assets", response_model=CommonResponse[QueryWorkProjectAssetsResponse], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def query_work_project_assets_route(id: int, page: int = Query(1, ge=1), size: int = Query(RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE), keyword: str = Query(""), kind: WorkProjectAssetKind | None = Query(None), scope: WorkProjectAssetScope | None = Query(None), user: AuthUser = Depends(require_user)) -> CommonResponse:
    return await query_work_project_assets_handler(id, page, size, keyword, kind, scope, user)


@router.get("/evidence", response_model=CommonResponse[QueryWorkProjectEvidenceResponse], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def query_work_project_evidence_route(id: int, page: int = Query(1, ge=1), size: int = Query(RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE), keyword: str = Query(""), kind: WorkProjectEvidenceKind | None = Query(None), status: WorkProjectEvidenceStatus | None = Query(None), user: AuthUser = Depends(require_user)) -> CommonResponse:
    return await query_work_project_evidence_handler(id, page, size, keyword, kind, status, user)


@router.get("/findings", response_model=CommonResponse[QueryWorkProjectFindingsResponse], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def query_work_project_findings_route(id: int, page: int = Query(1, ge=1), size: int = Query(RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE), keyword: str = Query(""), verification: WorkProjectFindingVerification | None = Query(None), severity: WorkProjectFindingSeverity | None = Query(None), user: AuthUser = Depends(require_user)) -> CommonResponse:
    return await query_work_project_findings_handler(id, page, size, keyword, verification, severity, user)


@router.get("/attack-paths", response_model=CommonResponse[QueryWorkProjectAttackPathsResponse], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def query_work_project_attack_paths_route(id: int, page: int = Query(1, ge=1), size: int = Query(RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE), keyword: str = Query(""), user: AuthUser = Depends(require_user)) -> CommonResponse:
    return await query_work_project_attack_paths_handler(id, page, size, keyword, user)


@router.get("/work-items", response_model=CommonResponse[QueryWorkProjectWorkItemsResponse], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def query_work_project_work_items_route(id: int, page: int = Query(1, ge=1), size: int = Query(RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE), keyword: str = Query(""), status: WorkProjectWorkItemStatus | None = Query(None), assignee_agent_code: str = Query("", max_length=32), user: AuthUser = Depends(require_user)) -> CommonResponse:
    return await query_work_project_work_items_handler(id, page, size, keyword, status, assignee_agent_code, user)


@router.get("/activity", response_model=CommonResponse[QueryWorkProjectActivityResponse], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def query_work_project_activity_route(id: int, page: int = Query(1, ge=1), size: int = Query(RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE), user: AuthUser = Depends(require_user)) -> CommonResponse:
    return await query_work_project_activity_handler(id, page, size, user)


@router.get("/graph", response_model=CommonResponse[WorkProjectGraphViewSchema], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def get_work_project_graph_route(id: int, user: AuthUser = Depends(require_user)) -> CommonResponse:
    return await get_work_project_graph_handler(id, user)


@router.get("/credentials", response_model=CommonResponse[QueryWorkProjectCredentialsResponse], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def query_work_project_credentials_route(
    id: int,
    page: int = Query(1, ge=1),
    size: int = Query(RESOURCE_PAGE_SIZE, ge=1, le=RESOURCE_PAGE_MAX_SIZE),
    keyword: str = Query(""),
    user: AuthUser = Depends(require_user),
) -> CommonResponse:
    return await query_work_project_credentials_handler(id, page, size, keyword, user)


@router.post("/credentials", response_model=CommonResponse[SessionCredentialSchema], responses={**COMMON_ERROR_RESPONSES, **BAD_REQUEST_RESPONSE, **NOT_FOUND_RESPONSE})
async def create_work_project_credential_route(
    id: int,
    request: CreateSessionCredentialRequest,
    user: AuthUser = Depends(require_user),
) -> CommonResponse:
    return await create_work_project_credential_handler(id, request, user)


@router.delete("/credentials/{credential_id}", response_model=CommonResponse[DeleteWorkProjectCredentialResponse], responses={**COMMON_ERROR_RESPONSES, **NOT_FOUND_RESPONSE})
async def delete_work_project_credential_route(
    id: int,
    credential_id: int = Path(..., gt=0),
    user: AuthUser = Depends(require_user),
) -> CommonResponse:
    return await delete_work_project_credential_handler(id, credential_id, user)


@router.post("/traffic/{traffic_id}/evidence", response_model=CommonResponse[WorkProjectEvidenceRecordSchema], responses={**COMMON_ERROR_RESPONSES, **BAD_REQUEST_RESPONSE, **NOT_FOUND_RESPONSE})
async def create_traffic_evidence_route(
    id: int,
    request: CreateTrafficEvidenceRequest,
    traffic_id: int = Path(..., gt=0),
    user: AuthUser = Depends(require_user),
) -> CommonResponse:
    return await create_traffic_evidence_handler(id, traffic_id, request, user)
