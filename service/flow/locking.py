from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from model.flow.flows import Flow


async def lock_flow_by_public_id(session: AsyncSession, public_id: str) -> Flow | None:
    return (await session.exec(
        select(Flow).where(Flow.public_id == public_id).with_for_update()
    )).one_or_none()
