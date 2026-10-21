"""API Guardian 的两张最小业务持久化表."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    """返回带时区的 UTC 时间，避免跨环境产生歧义."""
    return datetime.now(UTC)


class GuardianScanStatus(StrEnum):
    """扫描业务生命周期；检测 ERROR 与服务失败保持独立."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GuardianScan(SQLModel, table=True):
    """保存一次 Guardian 扫描的脱敏摘要."""

    __tablename__: ClassVar[str] = "guardian_scan"  # pyright: ignore[reportIncompatibleVariableOverride]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    status: str = Field(default=GuardianScanStatus.PENDING.value, index=True)
    phase: str = Field(default="pending")
    contract_verdict: str | None = Field(default=None)
    regression_verdict: str | None = Field(default=None)
    final_verdict: str | None = Field(default=None, index=True)
    base_spec_sha256: str
    head_spec_sha256: str
    oasdiff_version: str | None = Field(default=None)
    schemathesis_version: str | None = Field(default=None)
    policy_snapshot: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    analysis_degraded: bool = Field(default=False)
    error_code: str | None = Field(default=None)
    safe_error_summary: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class GuardianReportRecord(SQLModel, table=True):
    """保存由白名单领域 DTO 构造的最终报告."""

    __tablename__: ClassVar[str] = "guardian_report_record"  # pyright: ignore[reportIncompatibleVariableOverride]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    scan_id: UUID = Field(foreign_key="guardian_scan.id", unique=True, index=True)
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    generated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
