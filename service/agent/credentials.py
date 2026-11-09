from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_
from sqlmodel import col, select

from database import get_async_session
from model.agent.credentials import SessionCredential
from schema.agent.credentials import SessionCredentialSchema


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
        raise ValueError("credential target is required")
    if not norm_secret:
        raise ValueError("credential secret is required")

    now = datetime.now()
    async with get_async_session() as session:
        stmt = select(SessionCredential).where(
            SessionCredential.target == norm_target,
            SessionCredential.credential_type == norm_type,
            SessionCredential.username == norm_user,
        )
        if project_id is not None:
            stmt = stmt.where(SessionCredential.project_id == project_id)
        else:
            stmt = stmt.where(SessionCredential.session_id == session_id)
        existing = (await session.exec(stmt)).first()
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
            session_id=session_id,
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
    clauses = []
    if project_id is not None and session_id:
        clauses.append(or_(SessionCredential.project_id == project_id, SessionCredential.session_id == session_id))
    elif project_id is not None:
        clauses.append(SessionCredential.project_id == project_id)
    elif session_id:
        clauses.append(SessionCredential.session_id == session_id)
    else:
        return []

    stmt = select(SessionCredential).where(*clauses).order_by(col(SessionCredential.updated_at).desc(), col(SessionCredential.id).desc())
    norm_target = target.strip().lower()
    async with get_async_session() as session:
        rows = list((await session.exec(stmt)).all())
    if norm_target:
        rows = [row for row in rows if norm_target in row.target.lower()]
    return [_to_schema(row) for row in rows]


async def delete_credential(
    credential_id: int,
    *,
    session_id: str = "",
    project_id: int | None = None,
) -> bool:
    async with get_async_session() as session:
        row = await session.get(SessionCredential, credential_id)
        if row is None:
            return False
        if project_id is not None and row.project_id == project_id:
            pass
        elif session_id and row.session_id == session_id:
            pass
        else:
            return False
        await session.delete(row)
        await session.commit()
        return True
