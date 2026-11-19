from middleware.system_user import AuthUser
from schema.agent.credentials import CreateSessionCredentialRequest
from schema.common.responses import CommonResponse
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
)
from schema.work_project.workflow import WorkProjectWorkItemStatus
from service.common.pagination import paginated_payload
from service.work_project.records import (
    create_work_project_credential_for_user,
    create_work_project_evidence_from_traffic_for_user,
    delete_work_project_credential_for_user,
    get_work_project_graph_for_user,
    get_work_project_overview_for_user,
    query_work_project_activity_for_user,
    query_work_project_assets_for_user,
    query_work_project_attack_paths_for_user,
    query_work_project_credentials_for_user,
    query_work_project_evidence_for_user,
    query_work_project_findings_for_user,
    query_work_project_work_items_for_user,
)


async def _page_response(query, response_model, project_id: int, page: int, size: int, user: AuthUser, **kwargs):
    result = await query(project_id, page=page, size=size, user_id=user.id, user_role=user.role, **kwargs)
    return CommonResponse(data=response_model(**paginated_payload(result, result.items)))


async def get_work_project_overview_handler(id: int, user: AuthUser) -> CommonResponse:
    return CommonResponse(data=await get_work_project_overview_for_user(id, user_id=user.id, user_role=user.role))


async def query_work_project_assets_handler(
    id: int,
    page: int,
    size: int,
    keyword: str,
    kind: WorkProjectAssetKind | None,
    scope: WorkProjectAssetScope | None,
    user: AuthUser,
) -> CommonResponse:
    return await _page_response(
        query_work_project_assets_for_user,
        QueryWorkProjectAssetsResponse,
        id,
        page,
        size,
        user,
        keyword=keyword,
        kind=kind,
        scope=scope,
    )


async def query_work_project_evidence_handler(
    id: int,
    page: int,
    size: int,
    keyword: str,
    kind: WorkProjectEvidenceKind | None,
    status: WorkProjectEvidenceStatus | None,
    user: AuthUser,
) -> CommonResponse:
    return await _page_response(
        query_work_project_evidence_for_user,
        QueryWorkProjectEvidenceResponse,
        id,
        page,
        size,
        user,
        keyword=keyword,
        kind=kind,
        status=status,
    )


async def query_work_project_findings_handler(
    id: int,
    page: int,
    size: int,
    keyword: str,
    verification: WorkProjectFindingVerification | None,
    severity: WorkProjectFindingSeverity | None,
    user: AuthUser,
) -> CommonResponse:
    return await _page_response(
        query_work_project_findings_for_user,
        QueryWorkProjectFindingsResponse,
        id,
        page,
        size,
        user,
        keyword=keyword,
        verification=verification,
        severity=severity,
    )


async def query_work_project_attack_paths_handler(id: int, page: int, size: int, keyword: str, user: AuthUser) -> CommonResponse:
    return await _page_response(
        query_work_project_attack_paths_for_user,
        QueryWorkProjectAttackPathsResponse,
        id,
        page,
        size,
        user,
        keyword=keyword,
    )


async def query_work_project_work_items_handler(
    id: int,
    page: int,
    size: int,
    keyword: str,
    status: WorkProjectWorkItemStatus | None,
    assignee_agent_code: str,
    user: AuthUser,
) -> CommonResponse:
    return await _page_response(
        query_work_project_work_items_for_user,
        QueryWorkProjectWorkItemsResponse,
        id,
        page,
        size,
        user,
        keyword=keyword,
        status=status,
        assignee_agent_code=assignee_agent_code,
    )


async def query_work_project_activity_handler(id: int, page: int, size: int, user: AuthUser) -> CommonResponse:
    return await _page_response(query_work_project_activity_for_user, QueryWorkProjectActivityResponse, id, page, size, user)


async def get_work_project_graph_handler(id: int, user: AuthUser) -> CommonResponse:
    return CommonResponse(data=await get_work_project_graph_for_user(id, user_id=user.id, user_role=user.role))


async def query_work_project_credentials_handler(
    id: int,
    page: int,
    size: int,
    keyword: str,
    user: AuthUser,
) -> CommonResponse:
    return await _page_response(
        query_work_project_credentials_for_user,
        QueryWorkProjectCredentialsResponse,
        id,
        page,
        size,
        user,
        keyword=keyword,
    )


async def create_work_project_credential_handler(
    id: int,
    request: CreateSessionCredentialRequest,
    user: AuthUser,
) -> CommonResponse:
    saved = await create_work_project_credential_for_user(
        id,
        request,
        user_id=user.id,
        user_role=user.role,
    )
    return CommonResponse(message="Credential saved", data=saved)


async def delete_work_project_credential_handler(
    id: int,
    credential_id: int,
    user: AuthUser,
) -> CommonResponse:
    await delete_work_project_credential_for_user(
        id,
        credential_id,
        user_id=user.id,
        user_role=user.role,
    )
    return CommonResponse(message="Credential deleted", data=DeleteWorkProjectCredentialResponse(deleted=True))


async def create_traffic_evidence_handler(
    id: int,
    traffic_id: int,
    request: CreateTrafficEvidenceRequest,
    user: AuthUser,
) -> CommonResponse:
    record = await create_work_project_evidence_from_traffic_for_user(
        id,
        traffic_id,
        request,
        user_id=user.id,
        user_role=user.role,
    )
    return CommonResponse(message="HTTP exchange saved as evidence", data=record)
