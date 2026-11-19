"""Baseline schema as of v0.3.1.

Every statement is conditional, so this revision builds an empty database and adopts a database
that was created before migrations existed.

Revision ID: 0001
Revises:
Create Date: 2026-11-19

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ENUMS = {
    "egressproxytype": ("HTTP", "HTTPS", "SOCKS5"),
    "systemuserrole": ("ADMIN", "USER"),
    "sandboxcontaineregressmode": ("DIRECT", "PROXY", "TOR"),
    "sandboxcontainerstatus": ("CREATED", "RUNNING", "PAUSED", "STOPPED", "ERROR"),
    "workprojectstatus": ("ACTIVE", "COMPLETED", "CANCELED"),
    "workprojecttype": ("PENETRATION_TEST", "SOURCE_CODE_AUDIT"),
    "sessiontype": ("CHAT", "PROJECT", "FLOW"),
    "flowstatus": ("QUEUED", "PLANNING", "RUNNING", "WAITING", "REPORTING", "COMPLETED", "FAILED", "CANCELLED"),
    "flowsubtaskstatus": ("PLANNED", "RUNNING", "DONE", "SKIPPED", "FAILED"),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    for name in _ENUMS:
        _enum(name).create(op.get_bind(), checkfirst=True)
    op.create_table(
        "egress_proxies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proxy_type", _enum("egressproxytype"), nullable=False),
        sa.Column("proxy_host", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("proxy_port", sa.Integer(), nullable=False),
        sa.Column("proxy_account", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("proxy_password", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_egress_proxies_proxy_host"), "egress_proxies", ["proxy_host"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_egress_proxies_proxy_type"), "egress_proxies", ["proxy_type"], unique=False, if_not_exists=True
    )
    op.create_table(
        "managed_hosts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("host_account", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("host_password", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("docker_management_port", sa.Integer(), nullable=False),
        sa.Column("docker_tls_enabled", sa.Boolean(), nullable=False),
        sa.Column("docker_client_ca_cert", sa.Text(), nullable=False),
        sa.Column("docker_client_cert", sa.Text(), nullable=False),
        sa.Column("docker_client_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_managed_hosts_ip_address"), "managed_hosts", ["ip_address"], unique=False, if_not_exists=True
    )
    op.create_table(
        "sandbox_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("image_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("control_proxy_port", sa.Integer(), nullable=False),
        sa.Column("supports_tor", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "sandbox_traffic_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("sandbox_container_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("proxy_seq", sa.Integer(), nullable=False),
        sa.Column("proxy_boot", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column("method", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("url", sqlmodel.sql.sqltypes.AutoString(length=2048), nullable=False),
        sa.Column("host", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("path", sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("request_headers_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("request_body", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("response_headers_json", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("response_body", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("response_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_traffic_entries_created_at"),
        "sandbox_traffic_entries",
        ["created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_traffic_entries_host"), "sandbox_traffic_entries", ["host"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_sandbox_traffic_entries_method"),
        "sandbox_traffic_entries",
        ["method"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_traffic_entries_owner_id"),
        "sandbox_traffic_entries",
        ["owner_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_traffic_entries_project_id"),
        "sandbox_traffic_entries",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_traffic_entries_proxy_seq"),
        "sandbox_traffic_entries",
        ["proxy_seq"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_traffic_entries_sandbox_container_id"),
        "sandbox_traffic_entries",
        ["sandbox_container_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_traffic_entries_status_code"),
        "sandbox_traffic_entries",
        ["status_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "system_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("role", _enum("systemuserrole"), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("username", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("password", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(op.f("ix_system_users_email"), "system_users", ["email"], unique=True, if_not_exists=True)
    op.create_index(op.f("ix_system_users_username"), "system_users", ["username"], unique=True, if_not_exists=True)
    op.create_table(
        "agent_context_compactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("viewer_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("nested_for", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("nested_call_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("start_message_id", sa.Integer(), nullable=False),
        sa.Column("end_message_id", sa.Integer(), nullable=False),
        sa.Column("summary_item", sa.JSON(), nullable=False),
        sa.Column("source_item_count", sa.Integer(), nullable=False),
        sa.Column("source_token_estimate", sa.Integer(), nullable=False),
        sa.Column("summary_token_estimate", sa.Integer(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "viewer_agent_code", "nested_for", "nested_call_id", name="uq_agent_context_compactions_scope"
        ),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_context_compactions_end_message_id"),
        "agent_context_compactions",
        ["end_message_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_context_compactions_nested_call_id"),
        "agent_context_compactions",
        ["nested_call_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_context_compactions_nested_for"),
        "agent_context_compactions",
        ["nested_for"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_context_compactions_session_id"),
        "agent_context_compactions",
        ["session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_context_compactions_start_message_id"),
        "agent_context_compactions",
        ["start_message_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_context_compactions_viewer_agent_code"),
        "agent_context_compactions",
        ["viewer_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "agent_event_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "item_key", name="uq_agent_event_log_session_item"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_event_log_session_id"), "agent_event_log", ["session_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_agent_event_log_session_seq", "agent_event_log", ["session_id", "seq"], unique=False, if_not_exists=True
    )
    op.create_table(
        "agent_notifications",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("target_agent_instance_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("nested_for_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("nested_call_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sandbox_container_id", sa.Integer(), nullable=True),
        sa.Column("sandbox_container_generation", sa.BigInteger(), nullable=False),
        sa.Column("sandbox_skill_metadata", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("run_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "run_id", name="uq_agent_notifications_kind_run_id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_notifications_kind"), "agent_notifications", ["kind"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_notifications_nested_call_id"),
        "agent_notifications",
        ["nested_call_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_notifications_nested_for_agent_code"),
        "agent_notifications",
        ["nested_for_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_notifications_run_id"), "agent_notifications", ["run_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_notifications_sandbox_container_id"),
        "agent_notifications",
        ["sandbox_container_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_notifications_session_id"),
        "agent_notifications",
        ["session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_notifications_status"), "agent_notifications", ["status"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_notifications_target_agent_code"),
        "agent_notifications",
        ["target_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_notifications_target_agent_instance_id"),
        "agent_notifications",
        ["target_agent_instance_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "sandbox_containers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host_id", sa.Integer(), nullable=False),
        sa.Column("container_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("container_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("egress_mode", _enum("sandboxcontaineregressmode"), nullable=False),
        sa.Column("egress_proxy_id", sa.Integer(), nullable=True),
        sa.Column("control_proxy_host_port", sa.Integer(), nullable=False),
        sa.Column("control_proxy_token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("port_mappings", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            _enum("sandboxcontainerstatus"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["egress_proxy_id"],
            ["egress_proxies.id"],
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["managed_hosts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["image_id"],
            ["sandbox_images.id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["system_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_containers_egress_mode"),
        "sandbox_containers",
        ["egress_mode"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_containers_egress_proxy_id"),
        "sandbox_containers",
        ["egress_proxy_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_containers_host_id"), "sandbox_containers", ["host_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_sandbox_containers_image_id"), "sandbox_containers", ["image_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_sandbox_containers_owner_id"), "sandbox_containers", ["owner_id"], unique=False, if_not_exists=True
    )
    op.create_table(
        "agent_message_meta",
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("owner_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("nested_for", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("nested_call_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["agent_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id"),
        if_not_exists=True,
    )
    op.create_table(
        "work_projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sandbox_container_id", sa.Integer(), nullable=True),
        sa.Column("status", _enum("workprojectstatus"), nullable=False),
        sa.Column("type", _enum("workprojecttype"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["sandbox_container_id"], ["sandbox_containers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(op.f("ix_work_projects_name"), "work_projects", ["name"], unique=False, if_not_exists=True)
    op.create_index(
        op.f("ix_work_projects_sandbox_container_id"),
        "work_projects",
        ["sandbox_container_id"],
        unique=True,
        if_not_exists=True,
    )
    op.create_index(op.f("ix_work_projects_status"), "work_projects", ["status"], unique=False, if_not_exists=True)
    op.create_index(op.f("ix_work_projects_type"), "work_projects", ["type"], unique=False, if_not_exists=True)
    op.create_table(
        "agent_session_meta",
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("session_type", _enum("sessiontype"), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("selected_sandbox_container_id", sa.Integer(), nullable=True),
        sa.Column("selected_sandbox_container_generation", sa.BigInteger(), nullable=False),
        sa.Column("is_running", sa.Boolean(), nullable=False),
        sa.Column("runtime_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("runtime_sandbox_container_id", sa.Integer(), nullable=True),
        sa.Column("runtime_sandbox_container_generation", sa.BigInteger(), nullable=False),
        sa.Column("run_started_at", sa.DateTime(), nullable=True),
        sa.Column("run_finished_at", sa.DateTime(), nullable=True),
        sa.Column("run_error", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("llm_requests", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["work_projects.id"],
        ),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_session_meta_is_running"), "agent_session_meta", ["is_running"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_session_meta_owner_id"), "agent_session_meta", ["owner_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_session_meta_project_id"), "agent_session_meta", ["project_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_session_meta_runtime_sandbox_container_id"),
        "agent_session_meta",
        ["runtime_sandbox_container_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_session_meta_selected_sandbox_container_id"),
        "agent_session_meta",
        ["selected_sandbox_container_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_session_meta_session_type"),
        "agent_session_meta",
        ["session_type"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "flows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=80), nullable=False),
        sa.Column("goal", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "status",
            _enum("flowstatus"),
            nullable=False,
        ),
        sa.Column("sandbox_container_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("ask_question", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("report_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["system_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["work_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["sandbox_container_id"],
            ["sandbox_containers.id"],
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.session_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(op.f("ix_flows_owner_id"), "flows", ["owner_id"], unique=False, if_not_exists=True)
    op.create_index(op.f("ix_flows_project_id"), "flows", ["project_id"], unique=False, if_not_exists=True)
    op.create_index(op.f("ix_flows_public_id"), "flows", ["public_id"], unique=True, if_not_exists=True)
    op.create_index(
        op.f("ix_flows_sandbox_container_id"), "flows", ["sandbox_container_id"], unique=False, if_not_exists=True
    )
    op.create_index(op.f("ix_flows_session_id"), "flows", ["session_id"], unique=False, if_not_exists=True)
    op.create_index(op.f("ix_flows_status"), "flows", ["status"], unique=False, if_not_exists=True)
    op.create_table(
        "session_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("target", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("credential_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("username", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("secret", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("note", sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["system_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["work_projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_session_credentials_owner_id"), "session_credentials", ["owner_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_session_credentials_project_id"),
        "session_credentials",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_session_credentials_session_id"),
        "session_credentials",
        ["session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_session_credentials_target"), "session_credentials", ["target"], unique=False, if_not_exists=True
    )
    op.create_table(
        "work_project_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("locator", sa.String(length=1000), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("criticality", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_by_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_from_session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "kind", "locator", name="uq_work_project_asset_identity"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_assets_created_by_agent_code"),
        "work_project_assets",
        ["created_by_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_assets_created_from_session_id"),
        "work_project_assets",
        ["created_from_session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_assets_locator"), "work_project_assets", ["locator"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_assets_name"), "work_project_assets", ["name"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_assets_origin"), "work_project_assets", ["origin"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_assets_project_id"),
        "work_project_assets",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_assets_project_kind",
        "work_project_assets",
        ["project_id", "kind"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_assets_project_scope",
        "work_project_assets",
        ["project_id", "scope"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_assets_scope"), "work_project_assets", ["scope"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_assets_state"), "work_project_assets", ["state"], unique=False, if_not_exists=True
    )
    op.create_table(
        "work_project_owners",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["system_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "user_id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_owners_user_id"), "work_project_owners", ["user_id"], unique=False, if_not_exists=True
    )
    op.create_table(
        "flow_subtasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=160), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "status",
            _enum("flowsubtaskstatus"),
            nullable=False,
        ),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["flow_id"], ["flows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(op.f("ix_flow_subtasks_flow_id"), "flow_subtasks", ["flow_id"], unique=False, if_not_exists=True)
    op.create_index(op.f("ix_flow_subtasks_status"), "flow_subtasks", ["status"], unique=False, if_not_exists=True)
    op.create_table(
        "work_project_attack_paths",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("objective", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("entry_asset_id", sa.Integer(), nullable=False),
        sa.Column("target_asset_id", sa.Integer(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("archive_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_from_session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["entry_asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_paths_archived_at"),
        "work_project_attack_paths",
        ["archived_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_paths_created_by_agent_code"),
        "work_project_attack_paths",
        ["created_by_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_paths_created_from_session_id"),
        "work_project_attack_paths",
        ["created_from_session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_paths_entry_asset_id"),
        "work_project_attack_paths",
        ["entry_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_paths_project_id"),
        "work_project_attack_paths",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_paths_target_asset_id"),
        "work_project_attack_paths",
        ["target_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_paths_title"),
        "work_project_attack_paths",
        ["title"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("primary_asset_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("verification", sa.String(length=32), nullable=False),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("preconditions", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("impact", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("recommendation", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cwe_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cvss_vector", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cvss_score", sa.Float(), nullable=True),
        sa.Column("deferral_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_from_session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["primary_asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_findings_category"),
        "work_project_findings",
        ["category"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_findings_created_by_agent_code"),
        "work_project_findings",
        ["created_by_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_findings_created_from_session_id"),
        "work_project_findings",
        ["created_from_session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_findings_cwe_id"), "work_project_findings", ["cwe_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_findings_primary_asset_id"),
        "work_project_findings",
        ["primary_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_findings_project_id"),
        "work_project_findings",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_findings_project_severity",
        "work_project_findings",
        ["project_id", "severity"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_findings_project_verification",
        "work_project_findings",
        ["project_id", "verification"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_findings_resolution"),
        "work_project_findings",
        ["resolution"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_findings_severity"),
        "work_project_findings",
        ["severity"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_findings_title"), "work_project_findings", ["title"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_findings_verification"),
        "work_project_findings",
        ["verification"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_relations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_asset_id", sa.Integer(), nullable=False),
        sa.Column("target_asset_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_from_session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "source_asset_id", "target_asset_id", "type", name="uq_work_project_relation"
        ),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_relations_created_by_agent_code"),
        "work_project_relations",
        ["created_by_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_relations_created_from_session_id"),
        "work_project_relations",
        ["created_from_session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_relations_project_id"),
        "work_project_relations",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_relations_project_status",
        "work_project_relations",
        ["project_id", "status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_relations_source",
        "work_project_relations",
        ["source_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_relations_source_asset_id"),
        "work_project_relations",
        ["source_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_relations_status"), "work_project_relations", ["status"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_work_project_relations_target",
        "work_project_relations",
        ["target_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_relations_target_asset_id"),
        "work_project_relations",
        ["target_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_attack_path_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("path_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source_asset_id", sa.Integer(), nullable=False),
        sa.Column("target_asset_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("preconditions", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("result", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("relation_id", sa.Integer(), nullable=True),
        sa.Column("finding_id", sa.Integer(), nullable=True),
        sa.Column("attack_technique_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("blocker_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_from_session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["finding_id"], ["work_project_findings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["path_id"], ["work_project_attack_paths.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["relation_id"], ["work_project_relations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_id", "sequence", name="uq_work_project_attack_path_step_sequence"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_action"),
        "work_project_attack_path_steps",
        ["action"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_attack_technique_id"),
        "work_project_attack_path_steps",
        ["attack_technique_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_created_by_agent_code"),
        "work_project_attack_path_steps",
        ["created_by_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_created_from_session_id"),
        "work_project_attack_path_steps",
        ["created_from_session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_finding_id"),
        "work_project_attack_path_steps",
        ["finding_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_path_id"),
        "work_project_attack_path_steps",
        ["path_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_project_id"),
        "work_project_attack_path_steps",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_attack_path_steps_project_path",
        "work_project_attack_path_steps",
        ["project_id", "path_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_relation_id"),
        "work_project_attack_path_steps",
        ["relation_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_sequence"),
        "work_project_attack_path_steps",
        ["sequence"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_source_asset_id"),
        "work_project_attack_path_steps",
        ["source_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_status"),
        "work_project_attack_path_steps",
        ["status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_steps_target_asset_id"),
        "work_project_attack_path_steps",
        ["target_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_finding_assets",
        sa.Column("finding_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finding_id"], ["work_project_findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("finding_id", "asset_id", "role"),
        sa.UniqueConstraint("finding_id", "asset_id", "role", name="uq_work_project_finding_asset"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_finding_assets_asset_id"),
        "work_project_finding_assets",
        ["asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_work_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("assignee_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("objective", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("execution_scope", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("completion_criteria", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("result_summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("blocker_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("focus_relation_id", sa.Integer(), nullable=True),
        sa.Column("focus_finding_id", sa.Integer(), nullable=True),
        sa.Column("focus_attack_path_id", sa.Integer(), nullable=True),
        sa.Column("focus_attack_path_step_id", sa.Integer(), nullable=True),
        sa.Column("created_by_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_from_session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(CASE WHEN focus_relation_id IS NULL THEN 0 ELSE 1 END + CASE WHEN focus_finding_id IS NULL THEN 0 ELSE 1 END + CASE WHEN focus_attack_path_id IS NULL THEN 0 ELSE 1 END + CASE WHEN focus_attack_path_step_id IS NULL THEN 0 ELSE 1 END) <= 1",
            name="ck_work_project_work_item_single_focus",
        ),
        sa.ForeignKeyConstraint(["focus_attack_path_id"], ["work_project_attack_paths.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["focus_attack_path_step_id"], ["work_project_attack_path_steps.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["focus_finding_id"], ["work_project_findings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["focus_relation_id"], ["work_project_relations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_id"], ["work_project_work_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_assignee_agent_code"),
        "work_project_work_items",
        ["assignee_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_created_by_agent_code"),
        "work_project_work_items",
        ["created_by_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_created_from_session_id"),
        "work_project_work_items",
        ["created_from_session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_focus_attack_path_id"),
        "work_project_work_items",
        ["focus_attack_path_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_focus_attack_path_step_id"),
        "work_project_work_items",
        ["focus_attack_path_step_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_focus_finding_id"),
        "work_project_work_items",
        ["focus_finding_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_focus_relation_id"),
        "work_project_work_items",
        ["focus_relation_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_parent_id"),
        "work_project_work_items",
        ["parent_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_phase"), "work_project_work_items", ["phase"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_work_project_work_items_project_assignee",
        "work_project_work_items",
        ["project_id", "assignee_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_project_id"),
        "work_project_work_items",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_work_items_project_status",
        "work_project_work_items",
        ["project_id", "status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_status"),
        "work_project_work_items",
        ["status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_items_title"), "work_project_work_items", ["title"], unique=False, if_not_exists=True
    )
    op.create_table(
        "agent_subordinates",
        sa.Column("run_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("parent_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("parent_agent_instance_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("agent_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("brief", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("result", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("progress", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("work_item_id", sa.Integer(), nullable=True),
        sa.Column("nested_call_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_project_work_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("run_id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_subordinates_agent_code"), "agent_subordinates", ["agent_code"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_subordinates_nested_call_id"),
        "agent_subordinates",
        ["nested_call_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_subordinates_owner_id"), "agent_subordinates", ["owner_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_subordinates_parent_agent_code"),
        "agent_subordinates",
        ["parent_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_subordinates_parent_agent_instance_id"),
        "agent_subordinates",
        ["parent_agent_instance_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_agent_subordinates_session_id"), "agent_subordinates", ["session_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_subordinates_status"), "agent_subordinates", ["status"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_agent_subordinates_work_item_id"),
        "agent_subordinates",
        ["work_item_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "sandbox_async_jobs",
        sa.Column("run_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("agent_instance_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("work_item_id", sa.Integer(), nullable=True),
        sa.Column("command", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("output_file", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exit_code", sa.BigInteger(), nullable=True),
        sa.Column("output_bytes", sa.BigInteger(), nullable=False),
        sa.Column("output_lines", sa.BigInteger(), nullable=False),
        sa.Column("error", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("nested_for_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("nested_call_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("sandbox_container_id", sa.Integer(), nullable=True),
        sa.Column("sandbox_container_generation", sa.BigInteger(), nullable=False),
        sa.Column("sandbox_skill_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_project_work_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("run_id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_async_jobs_agent_code"), "sandbox_async_jobs", ["agent_code"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_sandbox_async_jobs_agent_instance_id"),
        "sandbox_async_jobs",
        ["agent_instance_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_async_jobs_nested_call_id"),
        "sandbox_async_jobs",
        ["nested_call_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_async_jobs_nested_for_agent_code"),
        "sandbox_async_jobs",
        ["nested_for_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_async_jobs_sandbox_container_id"),
        "sandbox_async_jobs",
        ["sandbox_container_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_sandbox_async_jobs_session_id"), "sandbox_async_jobs", ["session_id"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_sandbox_async_jobs_status"), "sandbox_async_jobs", ["status"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_sandbox_async_jobs_work_item_id"),
        "sandbox_async_jobs",
        ["work_item_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("reference", sa.String(length=2000), nullable=False),
        sa.Column("sha256", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("primary_asset_id", sa.Integer(), nullable=True),
        sa.Column("work_item_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("supersedes_evidence_id", sa.Integer(), nullable=True),
        sa.Column("invalidation_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_from_session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["primary_asset_id"], ["work_project_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supersedes_evidence_id"], ["work_project_evidence.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_project_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_evidence_captured_at"),
        "work_project_evidence",
        ["captured_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_evidence_created_by_agent_code"),
        "work_project_evidence",
        ["created_by_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_evidence_created_from_session_id"),
        "work_project_evidence",
        ["created_from_session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_evidence_primary_asset_id"),
        "work_project_evidence",
        ["primary_asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_evidence_project_id"),
        "work_project_evidence",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_evidence_project_kind",
        "work_project_evidence",
        ["project_id", "kind"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_evidence_project_status",
        "work_project_evidence",
        ["project_id", "status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_evidence_sha256"), "work_project_evidence", ["sha256"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_evidence_status"), "work_project_evidence", ["status"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_evidence_supersedes_evidence_id"),
        "work_project_evidence",
        ["supersedes_evidence_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_evidence_title"), "work_project_evidence", ["title"], unique=False, if_not_exists=True
    )
    op.create_index(
        op.f("ix_work_project_evidence_work_item_id"),
        "work_project_evidence",
        ["work_item_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_work_item_dependencies",
        sa.Column("work_item_id", sa.Integer(), nullable=False),
        sa.Column("depends_on_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["depends_on_id"], ["work_project_work_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_project_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("work_item_id", "depends_on_id"),
        sa.UniqueConstraint("work_item_id", "depends_on_id", name="uq_work_project_work_item_dependency"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_item_dependencies_depends_on_id"),
        "work_project_work_item_dependencies",
        ["depends_on_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_work_item_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("work_item_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("surface", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conclusion", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("deferral_reason", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["work_project_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_project_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_item_id", "asset_id", "surface", name="uq_work_project_work_item_target"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_item_targets_asset_id"),
        "work_project_work_item_targets",
        ["asset_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_work_project_work_item_targets_asset_status",
        "work_project_work_item_targets",
        ["asset_id", "status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_item_targets_status"),
        "work_project_work_item_targets",
        ["status"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_item_targets_work_item_id"),
        "work_project_work_item_targets",
        ["work_item_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_work_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("work_item_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by_agent_code", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_from_session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["work_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_project_work_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_logs_created_by_agent_code"),
        "work_project_work_logs",
        ["created_by_agent_code"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_logs_created_from_session_id"),
        "work_project_work_logs",
        ["created_from_session_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_logs_kind"), "work_project_work_logs", ["kind"], unique=False, if_not_exists=True
    )
    op.create_index(
        "ix_work_project_work_logs_project_created",
        "work_project_work_logs",
        ["project_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_logs_project_id"),
        "work_project_work_logs",
        ["project_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_work_logs_work_item_id"),
        "work_project_work_logs",
        ["work_item_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_attack_path_step_evidence",
        sa.Column("step_id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["work_project_evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["work_project_attack_path_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("step_id", "evidence_id"),
        sa.UniqueConstraint("step_id", "evidence_id", name="uq_work_project_attack_path_step_evidence"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_attack_path_step_evidence_evidence_id"),
        "work_project_attack_path_step_evidence",
        ["evidence_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_finding_evidence",
        sa.Column("finding_id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["work_project_evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finding_id"], ["work_project_findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("finding_id", "evidence_id"),
        sa.UniqueConstraint("finding_id", "evidence_id", name="uq_work_project_finding_evidence"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_finding_evidence_evidence_id"),
        "work_project_finding_evidence",
        ["evidence_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "work_project_relation_evidence",
        sa.Column("relation_id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["work_project_evidence.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["relation_id"], ["work_project_relations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("relation_id", "evidence_id"),
        sa.UniqueConstraint("relation_id", "evidence_id", name="uq_work_project_relation_evidence"),
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_work_project_relation_evidence_evidence_id"),
        "work_project_relation_evidence",
        ["evidence_id"],
        unique=False,
        if_not_exists=True,
    )
    # Columns added to these tables after their first release.
    for column in ("llm_requests", "input_tokens", "output_tokens", "cached_tokens", "total_tokens"):
        op.add_column(
            "agent_session_meta",
            sa.Column(column, sa.BigInteger(), server_default="0", nullable=False),
            if_not_exists=True,
        )
    op.add_column(
        "sandbox_traffic_entries",
        sa.Column("proxy_boot", sqlmodel.sql.sqltypes.AutoString(length=64), server_default="", nullable=False),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_work_project_relation_evidence_evidence_id"), table_name="work_project_relation_evidence")
    op.drop_table("work_project_relation_evidence")
    op.drop_index(op.f("ix_work_project_finding_evidence_evidence_id"), table_name="work_project_finding_evidence")
    op.drop_table("work_project_finding_evidence")
    op.drop_index(
        op.f("ix_work_project_attack_path_step_evidence_evidence_id"),
        table_name="work_project_attack_path_step_evidence",
    )
    op.drop_table("work_project_attack_path_step_evidence")
    op.drop_index(op.f("ix_work_project_work_logs_work_item_id"), table_name="work_project_work_logs")
    op.drop_index(op.f("ix_work_project_work_logs_project_id"), table_name="work_project_work_logs")
    op.drop_index("ix_work_project_work_logs_project_created", table_name="work_project_work_logs")
    op.drop_index(op.f("ix_work_project_work_logs_kind"), table_name="work_project_work_logs")
    op.drop_index(op.f("ix_work_project_work_logs_created_from_session_id"), table_name="work_project_work_logs")
    op.drop_index(op.f("ix_work_project_work_logs_created_by_agent_code"), table_name="work_project_work_logs")
    op.drop_table("work_project_work_logs")
    op.drop_index(op.f("ix_work_project_work_item_targets_work_item_id"), table_name="work_project_work_item_targets")
    op.drop_index(op.f("ix_work_project_work_item_targets_status"), table_name="work_project_work_item_targets")
    op.drop_index("ix_work_project_work_item_targets_asset_status", table_name="work_project_work_item_targets")
    op.drop_index(op.f("ix_work_project_work_item_targets_asset_id"), table_name="work_project_work_item_targets")
    op.drop_table("work_project_work_item_targets")
    op.drop_index(
        op.f("ix_work_project_work_item_dependencies_depends_on_id"), table_name="work_project_work_item_dependencies"
    )
    op.drop_table("work_project_work_item_dependencies")
    op.drop_index(op.f("ix_work_project_evidence_work_item_id"), table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_title"), table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_supersedes_evidence_id"), table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_status"), table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_sha256"), table_name="work_project_evidence")
    op.drop_index("ix_work_project_evidence_project_status", table_name="work_project_evidence")
    op.drop_index("ix_work_project_evidence_project_kind", table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_project_id"), table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_primary_asset_id"), table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_created_from_session_id"), table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_created_by_agent_code"), table_name="work_project_evidence")
    op.drop_index(op.f("ix_work_project_evidence_captured_at"), table_name="work_project_evidence")
    op.drop_table("work_project_evidence")
    op.drop_index(op.f("ix_sandbox_async_jobs_work_item_id"), table_name="sandbox_async_jobs")
    op.drop_index(op.f("ix_sandbox_async_jobs_status"), table_name="sandbox_async_jobs")
    op.drop_index(op.f("ix_sandbox_async_jobs_session_id"), table_name="sandbox_async_jobs")
    op.drop_index(op.f("ix_sandbox_async_jobs_sandbox_container_id"), table_name="sandbox_async_jobs")
    op.drop_index(op.f("ix_sandbox_async_jobs_nested_for_agent_code"), table_name="sandbox_async_jobs")
    op.drop_index(op.f("ix_sandbox_async_jobs_nested_call_id"), table_name="sandbox_async_jobs")
    op.drop_index(op.f("ix_sandbox_async_jobs_agent_instance_id"), table_name="sandbox_async_jobs")
    op.drop_index(op.f("ix_sandbox_async_jobs_agent_code"), table_name="sandbox_async_jobs")
    op.drop_table("sandbox_async_jobs")
    op.drop_index(op.f("ix_agent_subordinates_work_item_id"), table_name="agent_subordinates")
    op.drop_index(op.f("ix_agent_subordinates_status"), table_name="agent_subordinates")
    op.drop_index(op.f("ix_agent_subordinates_session_id"), table_name="agent_subordinates")
    op.drop_index(op.f("ix_agent_subordinates_parent_agent_instance_id"), table_name="agent_subordinates")
    op.drop_index(op.f("ix_agent_subordinates_parent_agent_code"), table_name="agent_subordinates")
    op.drop_index(op.f("ix_agent_subordinates_owner_id"), table_name="agent_subordinates")
    op.drop_index(op.f("ix_agent_subordinates_nested_call_id"), table_name="agent_subordinates")
    op.drop_index(op.f("ix_agent_subordinates_agent_code"), table_name="agent_subordinates")
    op.drop_table("agent_subordinates")
    op.drop_index(op.f("ix_work_project_work_items_title"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_status"), table_name="work_project_work_items")
    op.drop_index("ix_work_project_work_items_project_status", table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_project_id"), table_name="work_project_work_items")
    op.drop_index("ix_work_project_work_items_project_assignee", table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_phase"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_parent_id"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_focus_relation_id"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_focus_finding_id"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_focus_attack_path_step_id"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_focus_attack_path_id"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_created_from_session_id"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_created_by_agent_code"), table_name="work_project_work_items")
    op.drop_index(op.f("ix_work_project_work_items_assignee_agent_code"), table_name="work_project_work_items")
    op.drop_table("work_project_work_items")
    op.drop_index(op.f("ix_work_project_finding_assets_asset_id"), table_name="work_project_finding_assets")
    op.drop_table("work_project_finding_assets")
    op.drop_index(
        op.f("ix_work_project_attack_path_steps_target_asset_id"), table_name="work_project_attack_path_steps"
    )
    op.drop_index(op.f("ix_work_project_attack_path_steps_status"), table_name="work_project_attack_path_steps")
    op.drop_index(
        op.f("ix_work_project_attack_path_steps_source_asset_id"), table_name="work_project_attack_path_steps"
    )
    op.drop_index(op.f("ix_work_project_attack_path_steps_sequence"), table_name="work_project_attack_path_steps")
    op.drop_index(op.f("ix_work_project_attack_path_steps_relation_id"), table_name="work_project_attack_path_steps")
    op.drop_index("ix_work_project_attack_path_steps_project_path", table_name="work_project_attack_path_steps")
    op.drop_index(op.f("ix_work_project_attack_path_steps_project_id"), table_name="work_project_attack_path_steps")
    op.drop_index(op.f("ix_work_project_attack_path_steps_path_id"), table_name="work_project_attack_path_steps")
    op.drop_index(op.f("ix_work_project_attack_path_steps_finding_id"), table_name="work_project_attack_path_steps")
    op.drop_index(
        op.f("ix_work_project_attack_path_steps_created_from_session_id"), table_name="work_project_attack_path_steps"
    )
    op.drop_index(
        op.f("ix_work_project_attack_path_steps_created_by_agent_code"), table_name="work_project_attack_path_steps"
    )
    op.drop_index(
        op.f("ix_work_project_attack_path_steps_attack_technique_id"), table_name="work_project_attack_path_steps"
    )
    op.drop_index(op.f("ix_work_project_attack_path_steps_action"), table_name="work_project_attack_path_steps")
    op.drop_table("work_project_attack_path_steps")
    op.drop_index(op.f("ix_work_project_relations_target_asset_id"), table_name="work_project_relations")
    op.drop_index("ix_work_project_relations_target", table_name="work_project_relations")
    op.drop_index(op.f("ix_work_project_relations_status"), table_name="work_project_relations")
    op.drop_index(op.f("ix_work_project_relations_source_asset_id"), table_name="work_project_relations")
    op.drop_index("ix_work_project_relations_source", table_name="work_project_relations")
    op.drop_index("ix_work_project_relations_project_status", table_name="work_project_relations")
    op.drop_index(op.f("ix_work_project_relations_project_id"), table_name="work_project_relations")
    op.drop_index(op.f("ix_work_project_relations_created_from_session_id"), table_name="work_project_relations")
    op.drop_index(op.f("ix_work_project_relations_created_by_agent_code"), table_name="work_project_relations")
    op.drop_table("work_project_relations")
    op.drop_index(op.f("ix_work_project_findings_verification"), table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_title"), table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_severity"), table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_resolution"), table_name="work_project_findings")
    op.drop_index("ix_work_project_findings_project_verification", table_name="work_project_findings")
    op.drop_index("ix_work_project_findings_project_severity", table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_project_id"), table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_primary_asset_id"), table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_cwe_id"), table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_created_from_session_id"), table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_created_by_agent_code"), table_name="work_project_findings")
    op.drop_index(op.f("ix_work_project_findings_category"), table_name="work_project_findings")
    op.drop_table("work_project_findings")
    op.drop_index(op.f("ix_work_project_attack_paths_title"), table_name="work_project_attack_paths")
    op.drop_index(op.f("ix_work_project_attack_paths_target_asset_id"), table_name="work_project_attack_paths")
    op.drop_index(op.f("ix_work_project_attack_paths_project_id"), table_name="work_project_attack_paths")
    op.drop_index(op.f("ix_work_project_attack_paths_entry_asset_id"), table_name="work_project_attack_paths")
    op.drop_index(op.f("ix_work_project_attack_paths_created_from_session_id"), table_name="work_project_attack_paths")
    op.drop_index(op.f("ix_work_project_attack_paths_created_by_agent_code"), table_name="work_project_attack_paths")
    op.drop_index(op.f("ix_work_project_attack_paths_archived_at"), table_name="work_project_attack_paths")
    op.drop_table("work_project_attack_paths")
    op.drop_index(op.f("ix_flow_subtasks_status"), table_name="flow_subtasks")
    op.drop_index(op.f("ix_flow_subtasks_flow_id"), table_name="flow_subtasks")
    op.drop_table("flow_subtasks")
    op.drop_index(op.f("ix_work_project_owners_user_id"), table_name="work_project_owners")
    op.drop_table("work_project_owners")
    op.drop_index(op.f("ix_work_project_assets_state"), table_name="work_project_assets")
    op.drop_index(op.f("ix_work_project_assets_scope"), table_name="work_project_assets")
    op.drop_index("ix_work_project_assets_project_scope", table_name="work_project_assets")
    op.drop_index("ix_work_project_assets_project_kind", table_name="work_project_assets")
    op.drop_index(op.f("ix_work_project_assets_project_id"), table_name="work_project_assets")
    op.drop_index(op.f("ix_work_project_assets_origin"), table_name="work_project_assets")
    op.drop_index(op.f("ix_work_project_assets_name"), table_name="work_project_assets")
    op.drop_index(op.f("ix_work_project_assets_locator"), table_name="work_project_assets")
    op.drop_index(op.f("ix_work_project_assets_created_from_session_id"), table_name="work_project_assets")
    op.drop_index(op.f("ix_work_project_assets_created_by_agent_code"), table_name="work_project_assets")
    op.drop_table("work_project_assets")
    op.drop_index(op.f("ix_session_credentials_target"), table_name="session_credentials")
    op.drop_index(op.f("ix_session_credentials_session_id"), table_name="session_credentials")
    op.drop_index(op.f("ix_session_credentials_project_id"), table_name="session_credentials")
    op.drop_index(op.f("ix_session_credentials_owner_id"), table_name="session_credentials")
    op.drop_table("session_credentials")
    op.drop_index(op.f("ix_flows_status"), table_name="flows")
    op.drop_index(op.f("ix_flows_session_id"), table_name="flows")
    op.drop_index(op.f("ix_flows_sandbox_container_id"), table_name="flows")
    op.drop_index(op.f("ix_flows_public_id"), table_name="flows")
    op.drop_index(op.f("ix_flows_project_id"), table_name="flows")
    op.drop_index(op.f("ix_flows_owner_id"), table_name="flows")
    op.drop_table("flows")
    op.drop_index(op.f("ix_agent_session_meta_session_type"), table_name="agent_session_meta")
    op.drop_index(op.f("ix_agent_session_meta_selected_sandbox_container_id"), table_name="agent_session_meta")
    op.drop_index(op.f("ix_agent_session_meta_runtime_sandbox_container_id"), table_name="agent_session_meta")
    op.drop_index(op.f("ix_agent_session_meta_project_id"), table_name="agent_session_meta")
    op.drop_index(op.f("ix_agent_session_meta_owner_id"), table_name="agent_session_meta")
    op.drop_index(op.f("ix_agent_session_meta_is_running"), table_name="agent_session_meta")
    op.drop_table("agent_session_meta")
    op.drop_index(op.f("ix_work_projects_type"), table_name="work_projects")
    op.drop_index(op.f("ix_work_projects_status"), table_name="work_projects")
    op.drop_index(op.f("ix_work_projects_sandbox_container_id"), table_name="work_projects")
    op.drop_index(op.f("ix_work_projects_name"), table_name="work_projects")
    op.drop_table("work_projects")
    op.drop_table("agent_message_meta")
    op.drop_index(op.f("ix_sandbox_containers_owner_id"), table_name="sandbox_containers")
    op.drop_index(op.f("ix_sandbox_containers_image_id"), table_name="sandbox_containers")
    op.drop_index(op.f("ix_sandbox_containers_host_id"), table_name="sandbox_containers")
    op.drop_index(op.f("ix_sandbox_containers_egress_proxy_id"), table_name="sandbox_containers")
    op.drop_index(op.f("ix_sandbox_containers_egress_mode"), table_name="sandbox_containers")
    op.drop_table("sandbox_containers")
    op.drop_index(op.f("ix_agent_notifications_target_agent_instance_id"), table_name="agent_notifications")
    op.drop_index(op.f("ix_agent_notifications_target_agent_code"), table_name="agent_notifications")
    op.drop_index(op.f("ix_agent_notifications_status"), table_name="agent_notifications")
    op.drop_index(op.f("ix_agent_notifications_session_id"), table_name="agent_notifications")
    op.drop_index(op.f("ix_agent_notifications_sandbox_container_id"), table_name="agent_notifications")
    op.drop_index(op.f("ix_agent_notifications_run_id"), table_name="agent_notifications")
    op.drop_index(op.f("ix_agent_notifications_nested_for_agent_code"), table_name="agent_notifications")
    op.drop_index(op.f("ix_agent_notifications_nested_call_id"), table_name="agent_notifications")
    op.drop_index(op.f("ix_agent_notifications_kind"), table_name="agent_notifications")
    op.drop_table("agent_notifications")
    op.drop_index("ix_agent_event_log_session_seq", table_name="agent_event_log")
    op.drop_index(op.f("ix_agent_event_log_session_id"), table_name="agent_event_log")
    op.drop_table("agent_event_log")
    op.drop_index(op.f("ix_agent_context_compactions_viewer_agent_code"), table_name="agent_context_compactions")
    op.drop_index(op.f("ix_agent_context_compactions_start_message_id"), table_name="agent_context_compactions")
    op.drop_index(op.f("ix_agent_context_compactions_session_id"), table_name="agent_context_compactions")
    op.drop_index(op.f("ix_agent_context_compactions_nested_for"), table_name="agent_context_compactions")
    op.drop_index(op.f("ix_agent_context_compactions_nested_call_id"), table_name="agent_context_compactions")
    op.drop_index(op.f("ix_agent_context_compactions_end_message_id"), table_name="agent_context_compactions")
    op.drop_table("agent_context_compactions")
    op.drop_index(op.f("ix_system_users_username"), table_name="system_users")
    op.drop_index(op.f("ix_system_users_email"), table_name="system_users")
    op.drop_table("system_users")
    op.drop_index(op.f("ix_sandbox_traffic_entries_status_code"), table_name="sandbox_traffic_entries")
    op.drop_index(op.f("ix_sandbox_traffic_entries_sandbox_container_id"), table_name="sandbox_traffic_entries")
    op.drop_index(op.f("ix_sandbox_traffic_entries_proxy_seq"), table_name="sandbox_traffic_entries")
    op.drop_index(op.f("ix_sandbox_traffic_entries_project_id"), table_name="sandbox_traffic_entries")
    op.drop_index(op.f("ix_sandbox_traffic_entries_owner_id"), table_name="sandbox_traffic_entries")
    op.drop_index(op.f("ix_sandbox_traffic_entries_method"), table_name="sandbox_traffic_entries")
    op.drop_index(op.f("ix_sandbox_traffic_entries_host"), table_name="sandbox_traffic_entries")
    op.drop_index(op.f("ix_sandbox_traffic_entries_created_at"), table_name="sandbox_traffic_entries")
    op.drop_table("sandbox_traffic_entries")
    op.drop_table("sandbox_images")
    op.drop_index(op.f("ix_managed_hosts_ip_address"), table_name="managed_hosts")
    op.drop_table("managed_hosts")
    op.drop_index(op.f("ix_egress_proxies_proxy_type"), table_name="egress_proxies")
    op.drop_index(op.f("ix_egress_proxies_proxy_host"), table_name="egress_proxies")
    op.drop_table("egress_proxies")
    for name in _ENUMS:
        _enum(name).drop(op.get_bind(), checkfirst=True)
