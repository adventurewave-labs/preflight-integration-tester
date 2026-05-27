"""Initial schema — all Preflight tables.

Revision ID: 0001
Revises:
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all Preflight tables from scratch."""
    # diagnostic_runs
    op.create_table(
        "diagnostic_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("progress_pct", sa.Float, server_default="0.0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("scenario", sa.JSON, nullable=True),
        sa.Column("analysis_summary", sa.JSON, nullable=True),
        sa.Column("report_summary", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_diagnostic_runs_status", "diagnostic_runs", ["status"])
    op.create_index("ix_diagnostic_runs_created_at", "diagnostic_runs", ["created_at"])

    # connection_profiles
    op.create_table(
        "connection_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("diagnostic_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("system_type", sa.String(50), nullable=False),
        sa.Column("connector_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), server_default="disconnected"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("entity_count", sa.Integer, server_default="0"),
        sa.Column("connection_latency_ms", sa.Float, nullable=True),
        sa.Column("credential_ref", sa.JSON, nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=True),
        sa.Column("connected_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_connection_profiles_run_id", "connection_profiles", ["run_id"])
    op.create_index("ix_connection_profiles_status", "connection_profiles", ["status"])

    # schema_inconsistencies
    op.create_table(
        "schema_inconsistencies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("diagnostic_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_name", sa.String(255), nullable=False),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("target_system", sa.String(100), nullable=False),
        sa.Column("inconsistency_type", sa.String(100), nullable=False),
        sa.Column("field_name", sa.String(255), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("impact_description", sa.Text, server_default=""),
        sa.Column("remediation_hint", sa.Text, server_default=""),
        sa.Column("discovered_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_schema_inconsistencies_run_id", "schema_inconsistencies", ["run_id"])
    op.create_index("ix_schema_inconsistencies_severity", "schema_inconsistencies", ["severity"])

    # pipeline_bottlenecks
    op.create_table(
        "pipeline_bottlenecks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("diagnostic_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pipeline_name", sa.String(255), nullable=False),
        sa.Column("system", sa.String(100), nullable=False),
        sa.Column("bottleneck_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("p95_latency_ms", sa.Float, nullable=True),
        sa.Column("p99_latency_ms", sa.Float, nullable=True),
        sa.Column("error_rate_pct", sa.Float, nullable=True),
        sa.Column("breaking_point_qps", sa.Float, nullable=True),
        sa.Column("discovered_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_pipeline_bottlenecks_run_id", "pipeline_bottlenecks", ["run_id"])

    # middleware_gaps
    op.create_table(
        "middleware_gaps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("diagnostic_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gap_type", sa.String(100), nullable=False),
        sa.Column("source_system", sa.String(100), server_default=""),
        sa.Column("target_system", sa.String(100), server_default=""),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("blocking", sa.Boolean, server_default="false"),
        sa.Column("effort_min_days", sa.Integer, server_default="1"),
        sa.Column("effort_max_days", sa.Integer, server_default="10"),
        sa.Column("recommended_solution", sa.Text, server_default=""),
        sa.Column("discovered_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_middleware_gaps_run_id", "middleware_gaps", ["run_id"])
    op.create_index("ix_middleware_gaps_blocking", "middleware_gaps", ["blocking"])

    # remediation_items
    op.create_table(
        "remediation_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("diagnostic_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("priority", sa.Integer, server_default="5"),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("effort_min_days", sa.Integer, server_default="1"),
        sa.Column("effort_max_days", sa.Integer, server_default="10"),
        sa.Column("recommended_sequence", sa.Integer, server_default="0"),
    )
    op.create_index("ix_remediation_items_run_id", "remediation_items", ["run_id"])
    op.create_index("ix_remediation_items_priority", "remediation_items", ["priority"])

    # api_keys
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("expires_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_api_keys_active", "api_keys", ["active"])


def downgrade() -> None:
    """Drop all Preflight tables in reverse dependency order."""
    op.drop_table("api_keys")
    op.drop_table("remediation_items")
    op.drop_table("middleware_gaps")
    op.drop_table("pipeline_bottlenecks")
    op.drop_table("schema_inconsistencies")
    op.drop_table("connection_profiles")
    op.drop_table("diagnostic_runs")
