from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlmodel import SQLModel

import database
from config import get_config, load_config
from database import close_engine, get_engine, init_engine, migrate_database
from middleware.system_user import ACCESS_TOKEN_HEADER
from model.system_user.users import SystemUser
from service.sandbox.control_proxy import close_control_proxy_http_client


TEST_DB_NAME = "z3r0_test"


@pytest_asyncio.fixture()
async def test_db() -> None:
    load_config()
    cfg = get_config()
    await close_engine()
    cfg.database.database = TEST_DB_NAME
    cfg.database.pool_size = 4
    cfg.database.max_overflow = 4
    init_engine()
    await migrate_database()
    async with get_engine().begin() as conn:
        table_names = [f'"{t.name}"' for t in SQLModel.metadata.sorted_tables]
        table_names.extend(['"agent_messages"', '"agent_sessions"'])
        await conn.execute(text("TRUNCATE TABLE " + ", ".join(table_names) + " RESTART IDENTITY CASCADE"))
    try:
        yield
    finally:
        await close_control_proxy_http_client()
        await close_engine()
        assert database._engine is None


def auth_headers(user: SystemUser) -> dict[str, str]:
    cfg = get_config()
    token = jwt.encode(
        {
            "sub": "z3r0",
            "id": user.id,
            "role": user.role.value,
            "email": user.email,
            "username": user.username,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        key=cfg.system.encrypt_key,
        algorithm="HS256",
    )
    return {ACCESS_TOKEN_HEADER: token}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: async test")
