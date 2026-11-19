"""Give every credential one owning vault: a work project, or an agent session outside a project.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "session_credentials"
_OWNER_FK = "session_credentials_owner_id_fkey"
_PROJECT_FK = "session_credentials_project_id_fkey"
_SESSION_FK = "session_credentials_session_id_fkey"
_SINGLE_VAULT = "ck_session_credentials_single_vault"


def upgrade() -> None:
    # A credential recorded by a session of a work project belongs to the project's vault.
    op.execute(
        """
        UPDATE session_credentials AS credential
        SET project_id = meta.project_id
        FROM agent_session_meta AS meta
        WHERE credential.project_id IS NULL
          AND credential.session_id = meta.session_id
          AND meta.project_id IS NOT NULL
        """
    )
    op.alter_column(_TABLE, "session_id", existing_type=sa.String(), nullable=True)
    op.execute("UPDATE session_credentials SET session_id = NULL WHERE project_id IS NOT NULL")
    # The remaining credentials belong to a session; those whose session is gone have no vault left.
    op.execute(
        """
        DELETE FROM session_credentials AS credential
        WHERE credential.project_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM agent_sessions AS session WHERE session.session_id = credential.session_id
          )
        """
    )
    op.alter_column(_TABLE, "owner_id", existing_type=sa.Integer(), nullable=True)

    op.drop_constraint(_OWNER_FK, _TABLE, type_="foreignkey")
    op.drop_constraint(_PROJECT_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(_OWNER_FK, _TABLE, "system_users", ["owner_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(_PROJECT_FK, _TABLE, "work_projects", ["project_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        _SESSION_FK, _TABLE, "agent_sessions", ["session_id"], ["session_id"], ondelete="CASCADE"
    )
    op.create_check_constraint(_SINGLE_VAULT, _TABLE, "(project_id IS NULL) <> (session_id IS NULL)")


def downgrade() -> None:
    op.drop_constraint(_SINGLE_VAULT, _TABLE, type_="check")
    op.drop_constraint(_SESSION_FK, _TABLE, type_="foreignkey")
    op.drop_constraint(_PROJECT_FK, _TABLE, type_="foreignkey")
    op.drop_constraint(_OWNER_FK, _TABLE, type_="foreignkey")
    op.create_foreign_key(_OWNER_FK, _TABLE, "system_users", ["owner_id"], ["id"])
    op.create_foreign_key(_PROJECT_FK, _TABLE, "work_projects", ["project_id"], ["id"])
    op.execute("UPDATE session_credentials SET session_id = '' WHERE session_id IS NULL")
    op.alter_column(_TABLE, "session_id", existing_type=sa.String(), nullable=False)
    # Fails when the user who recorded a credential has been deleted: the previous schema requires one.
    op.alter_column(_TABLE, "owner_id", existing_type=sa.Integer(), nullable=False)
