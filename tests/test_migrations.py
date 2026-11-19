from __future__ import annotations

import pytest
from alembic import command

from database import alembic_config, get_engine


@pytest.mark.asyncio
async def test_models_match_migrations(test_db: None) -> None:
    # Raises when a model changed without a matching revision under migrations/versions.
    async with get_engine().begin() as connection:
        await connection.run_sync(lambda sync_connection: command.check(alembic_config(sync_connection)))
