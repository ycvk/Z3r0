"""Store Command Guard approvals and the operator's answer to a waiting Flow.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sandbox_guarded_commands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("command", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sandbox_guarded_commands_session_id"), "sandbox_guarded_commands", ["session_id"], unique=False
    )
    op.add_column("flows", sa.Column("pending_input", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("flows", "pending_input")
    op.drop_index(op.f("ix_sandbox_guarded_commands_session_id"), table_name="sandbox_guarded_commands")
    op.drop_table("sandbox_guarded_commands")
