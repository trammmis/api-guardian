"""API Guardian HTTP 请求响应模型."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.services.guardian.models import GuardianReport, GuardianVerdict


class GuardianScanResponse(BaseModel):
    """同步创建扫描后返回的最小摘要."""

    model_config = ConfigDict(from_attributes=True)

    scan_id: UUID
    status: str
    contract_verdict: GuardianVerdict | None
    regression_verdict: GuardianVerdict | None
    final_verdict: GuardianVerdict | None
    analysis_degraded: bool
    created_at: datetime
    completed_at: datetime | None


class GuardianScanDetailResponse(GuardianScanResponse):
    """查询单次扫描时返回的安全详情."""

    phase: str
    base_spec_sha256: str
    head_spec_sha256: str
    oasdiff_version: str | None
    schemathesis_version: str | None
    policy_snapshot: dict[str, Any]
    error_code: str | None
    safe_error_summary: str | None
    started_at: datetime | None
    updated_at: datetime


class GuardianReportResponse(GuardianReport):
    """只包含白名单 GuardianReport 字段的 HTTP 响应."""


class GuardianErrorResponse(BaseModel):
    """Guardian HTTP 边界的稳定错误响应."""

    detail: str | dict[str, str]
    code: str | None = None
