from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, or_
from sqlmodel import col, select

from core.errors import NotFoundError, ValidationError
from database import get_async_session
from model.credential.credentials import SessionCredential
from schema.credential.credentials import SessionCredentialSchema
from service.common.pagination import Page, paginate_statement


def _to_schema(row: SessionCredential) -> SessionCredentialSchema:
    return SessionCredentialSchema(
        id=row.id or 0,
        session_id=row.session_id,
        project_id=row.project_id,
        target=row.target,
        credential_type=row.credential_type,
        username=row.username,
        secret=row.secret,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _vault(*, session_id: str, project_id: int | None) -> ColumnElement[bool]:
    """Everything recorded in a work project shares the project's vault; a session outside a project has its own."""
    if project_id is not None:
        return col(SessionCredential.project_id) == project_id
    return col(SessionCredential.session_id) == session_id


async def upsert_credential(
    *,
    owner_id: int,
    session_id: str,
    project_id: int | None,
    target: str,
    credential_type: str = "password",
    username: str = "",
    secret: str,
    note: str = "",
) -> SessionCredentialSchema:
    norm_target = target.strip()[:255]
    norm_type = (credential_type.strip() or "password")[:64]
    norm_user = username.strip()[:255]
    norm_secret = secret.strip()
    norm_note = note.strip()[:500]
    if not norm_target:
        raise ValidationError("credential target is required")
    if not norm_secret:
        raise ValidationError("credential secret is required")

    now = datetime.now()
    async with get_async_session() as session:
        existing = (await session.exec(
            select(SessionCredential).where(
                _vault(session_id=session_id, project_id=project_id),
                SessionCredential.target == norm_target,
                SessionCredential.credential_type == norm_type,
                SessionCredential.username == norm_user,
            )
        )).first()
        if existing is not None:
            existing.secret = norm_secret
            if norm_note:
                existing.note = norm_note
            existing.updated_at = now
            session.add(existing)
            await session.commit()
            await session.refresh(existing)
            return _to_schema(existing)

        row = SessionCredential(
            owner_id=owner_id,
            session_id=None if project_id is not None else session_id,
            project_id=project_id,
            target=norm_target,
            credential_type=norm_type,
            username=norm_user,
            secret=norm_secret,
            note=norm_note,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _to_schema(row)


async def list_credentials_for_context(
    *,
    session_id: str = "",
    project_id: int | None = None,
    target: str = "",
) -> list[SessionCredentialSchema]:
    stmt = (
        select(SessionCredential)
        .where(_vault(session_id=session_id, project_id=project_id))
        .order_by(col(SessionCredential.updated_at).desc(), col(SessionCredential.id).desc())
    )
    norm_target = target.strip().lower()
    async with get_async_session() as session:
        rows = list((await session.exec(stmt)).all())
    if norm_target:
        rows = [row for row in rows if norm_target in row.target.lower()]
    return [_to_schema(row) for row in rows]


async def query_project_credentials(
    project_id: int,
    *,
    page: int = 1,
    size: int = 20,
    keyword: str = "",
) -> Page[SessionCredentialSchema]:
    stmt = (
        select(SessionCredential)
        .where(SessionCredential.project_id == project_id)
        .order_by(col(SessionCredential.updated_at).desc(), col(SessionCredential.id).desc())
    )
    if cleaned := keyword.strip():
        pattern = f"%{cleaned}%"
        stmt = stmt.where(
            or_(
                col(SessionCredential.target).ilike(pattern),
                col(SessionCredential.username).ilike(pattern),
                col(SessionCredential.credential_type).ilike(pattern),
                col(SessionCredential.note).ilike(pattern),
            )
        )
    page_result = await paginate_statement(stmt, page=page, size=size)
    return Page(
        page=page_result.page,
        size=page_result.size,
        total=page_result.total,
        items=[_to_schema(row) for row in page_result.items],
    )


async def delete_credential(
    credential_id: int,
    *,
    session_id: str = "",
    project_id: int | None = None,
) -> None:
    async with get_async_session() as session:
        row = (await session.exec(
            select(SessionCredential).where(
                SessionCredential.id == credential_id,
                _vault(session_id=session_id, project_id=project_id),
            )
        )).first()
        if row is None:
            raise NotFoundError("Credential not found")
        await session.delete(row)
        await session.commit()
