"""Delete a Flow with its session, and let a finished Flow outlive its sandbox container.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "flows"
_SESSION_FK = "flows_session_id_fkey"
_CONTAINER_FK = "flows_sandbox_container_id_fkey"


def upgrade() -> None:
    op.alter_column(_TABLE, "sandbox_container_id", existing_type=sa.Integer(), nullable=True)
    op.drop_constraint(_SESSION_FK, _TABLE, type_="foreignkey")
    op.drop_constraint(_CONTAINER_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(
        _SESSION_FK, _TABLE, "agent_sessions", ["session_id"], ["session_id"], ondelete="CASCADE"
    )
    op.create_foreign_key(
        _CONTAINER_FK, _TABLE, "sandbox_containers", ["sandbox_container_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(_CONTAINER_FK, _TABLE, type_="foreignkey")
    op.drop_constraint(_SESSION_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(_CONTAINER_FK, _TABLE, "sandbox_containers", ["sandbox_container_id"], ["id"])
    op.create_foreign_key(_SESSION_FK, _TABLE, "agent_sessions", ["session_id"], ["session_id"])
    # Fails when a Flow has outlived its sandbox container: the previous schema requires one.
    op.alter_column(_TABLE, "sandbox_container_id", existing_type=sa.Integer(), nullable=False)
