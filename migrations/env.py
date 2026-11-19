import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlmodel import SQLModel

from config import load_config
from database import close_engine, get_engine, init_engine

# The agents SDK creates and owns these tables; migrations never touch them.
_SDK_OWNED_TABLES = frozenset({"agent_sessions", "agent_messages"})


def _include_object(obj: object, name: str | None, type_: str, _reflected: bool, _compare_to: object) -> bool:
    table_name = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
    return table_name not in _SDK_OWNED_TABLES


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=SQLModel.metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_from_cli() -> None:
    load_config()
    init_engine()
    try:
        async with get_engine().begin() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await close_engine()


if context.is_offline_mode():
    raise RuntimeError("Offline SQL generation is not supported; run migrations against a database")

# The application passes its own connection at startup; the alembic CLI connects through .z3r0/config.json.
_connection = context.config.attributes.get("connection")
if _connection is not None:
    _run_migrations(_connection)
else:
    asyncio.run(_run_migrations_from_cli())
