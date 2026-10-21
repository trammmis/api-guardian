"""新增 API Guardian 扫描与报告表.

Revision ID: e3a1b7c9d2f4
Revises: c7f2a08e4b1d
Create Date: 2026-09-01 17:40:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import sqlmodel  # noqa: F401

from alembic import op

revision: str = "e3a1b7c9d2f4"
down_revision: Union[str, Sequence[str], None] = "c7f2a08e4b1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 Guardian V1 的两张最小业务表."""
    op.create_table(
        "guardian_scan",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("phase", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("contract_verdict", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("regression_verdict", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("final_verdict", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("base_spec_sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("head_spec_sha256", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("oasdiff_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("schemathesis_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("analysis_degraded", sa.Boolean(), nullable=False),
        sa.Column("error_code", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("safe_error_summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_guardian_scan_final_verdict"), "guardian_scan", ["final_verdict"], unique=False)
    op.create_index(op.f("ix_guardian_scan_status"), "guardian_scan", ["status"], unique=False)
    op.create_index(op.f("ix_guardian_scan_user_id"), "guardian_scan", ["user_id"], unique=False)

    op.create_table(
        "guardian_report_record",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scan_id"], ["guardian_scan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_guardian_report_record_scan_id"),
        "guardian_report_record",
        ["scan_id"],
        unique=True,
    )


def downgrade() -> None:
    """删除 Guardian 业务表，不触碰 checkpointer 自管表."""
    op.drop_index(op.f("ix_guardian_report_record_scan_id"), table_name="guardian_report_record")
    op.drop_table("guardian_report_record")
    op.drop_index(op.f("ix_guardian_scan_user_id"), table_name="guardian_scan")
    op.drop_index(op.f("ix_guardian_scan_status"), table_name="guardian_scan")
    op.drop_index(op.f("ix_guardian_scan_final_verdict"), table_name="guardian_scan")
    op.drop_table("guardian_scan")
