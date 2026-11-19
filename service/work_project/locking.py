from sqlmodel import select

from core.errors import NotFoundError, ValidationError
from model.work_project.projects import WorkProject
from schema.work_project.projects import WorkProjectStatus


async def lock_active_work_project(session, project_id: int) -> WorkProject:
    project = (await session.exec(
        select(WorkProject).where(WorkProject.id == project_id).with_for_update()
    )).one_or_none()
    if project is None:
        raise NotFoundError("Work project not found")
    if project.status != WorkProjectStatus.ACTIVE:
        raise ValidationError(f"Work project is {project.status}")
    return project
