from middleware.system_user import AuthUser
from schema.common.responses import CommonResponse
from schema.work_project.projects import (
    CreateWorkProjectRequest,
    CreateWorkProjectSessionResponse,
    DeleteWorkProjectResponse,
    ListWorkProjectSessionsResponse,
    QueryWorkProjectsResponse,
    UpdateWorkProjectMetadataRequest,
)
from service.common.pagination import paginated_payload
from service.work_project.projects import (
    cancel_work_project,
    create_work_project,
    create_work_project_session,
    delete_work_project,
    delete_work_project_session,
    get_work_project_for_user,
    list_work_project_sessions,
    query_work_projects_for_user,
    retry_work_project,
    update_work_project_metadata,
)


async def create_work_project_handler(request: CreateWorkProjectRequest, user: AuthUser) -> CommonResponse:
    project = await create_work_project(request, user_id=user.id, user_role=user.role)
    return CommonResponse(data=project)


async def get_work_project_handler(id: int, user: AuthUser) -> CommonResponse:
    return CommonResponse(data=await get_work_project_for_user(id, user_id=user.id, user_role=user.role))


async def update_work_project_metadata_handler(
    id: int,
    request: UpdateWorkProjectMetadataRequest,
    user: AuthUser,
) -> CommonResponse:
    project = await update_work_project_metadata(id, request, user_id=user.id, user_role=user.role)
    return CommonResponse(message="Work project updated", data=project)


async def delete_work_project_handler(id: int) -> CommonResponse:
    await delete_work_project(id)
    return CommonResponse(data=DeleteWorkProjectResponse(id=id))


async def cancel_work_project_handler(id: int, user: AuthUser) -> CommonResponse:
    project = await cancel_work_project(id, user_id=user.id, user_role=user.role)
    return CommonResponse(message="Work project canceled", data=project)


async def retry_work_project_handler(id: int, user: AuthUser) -> CommonResponse:
    project = await retry_work_project(id, user_id=user.id, user_role=user.role)
    return CommonResponse(message="Work project restarted", data=project)


async def query_work_projects_handler(page: int, size: int, keyword: str, user: AuthUser) -> CommonResponse:
    projects = await query_work_projects_for_user(
        page=page,
        size=size,
        keyword=keyword,
        user_id=user.id,
        user_role=user.role,
    )
    return CommonResponse(data=QueryWorkProjectsResponse(**paginated_payload(projects, projects.items)))


async def create_work_project_session_handler(
    id: int,
    user: AuthUser,
) -> CommonResponse:
    session_id = await create_work_project_session(
        id,
        owner_id=user.id,
        user_role=user.role,
    )
    return CommonResponse(data=CreateWorkProjectSessionResponse(session_id=session_id))


async def list_work_project_sessions_handler(
    id: int,
    page: int,
    size: int,
    user: AuthUser,
) -> CommonResponse:
    sessions = await list_work_project_sessions(
        id,
        user_id=user.id,
        user_role=user.role,
        page=page,
        size=size,
    )
    return CommonResponse(data=ListWorkProjectSessionsResponse(
        **paginated_payload(sessions, sessions.items)
    ))


async def delete_work_project_session_handler(id: int, session_id: str, user: AuthUser) -> CommonResponse:
    await delete_work_project_session(
        project_id=id,
        session_id=session_id,
        user_id=user.id,
        user_role=user.role,
    )
    return CommonResponse(message="Work project session deleted")
