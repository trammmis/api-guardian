"""Guardian 的最小 SQLModel Repository."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from app.models.guardian import GuardianReportRecord, GuardianScan, GuardianScanStatus
from app.services.database import database_service
from app.services.guardian.models import GuardianVerdict


def _utc_now() -> datetime:
    """返回带时区的 UTC 当前时间."""
    return datetime.now(UTC)


class GuardianRepository:
    """仅封装 Guardian 数据库短事务，并在线程中执行同步 SQLModel."""

    def __init__(self, engine: Engine | None = None) -> None:
        """允许测试注入独立 PostgreSQL engine."""
        self._engine = engine or database_service.engine

    async def create_scan(
        self,
        *,
        scan_id: UUID,
        user_id: int,
        base_spec_sha256: str,
        head_spec_sha256: str,
        policy_snapshot: dict[str, Any],
    ) -> GuardianScan:
        """创建 pending 扫描记录."""
        return await asyncio.to_thread(
            self._create_scan_sync,
            scan_id,
            user_id,
            base_spec_sha256,
            head_spec_sha256,
            policy_snapshot,
        )

    def _create_scan_sync(
        self,
        scan_id: UUID,
        user_id: int,
        base_spec_sha256: str,
        head_spec_sha256: str,
        policy_snapshot: dict[str, Any],
    ) -> GuardianScan:
        """执行创建扫描的同步短事务."""
        with Session(self._engine, expire_on_commit=False) as session:
            scan = GuardianScan(
                id=scan_id,
                user_id=user_id,
                base_spec_sha256=base_spec_sha256,
                head_spec_sha256=head_spec_sha256,
                policy_snapshot=policy_snapshot,
            )
            session.add(scan)
            session.commit()
            return scan

    async def mark_running(self, scan_id: UUID) -> GuardianScan:
        """把 pending 扫描标为 running."""
        return await asyncio.to_thread(self._mark_running_sync, scan_id)

    def _mark_running_sync(self, scan_id: UUID) -> GuardianScan:
        """执行 running 状态事务."""
        with Session(self._engine, expire_on_commit=False) as session:
            scan = self._required_scan(session, scan_id)
            now = _utc_now()
            scan.status = GuardianScanStatus.RUNNING.value
            scan.phase = "prepare_scan"
            scan.started_at = now
            scan.updated_at = now
            session.add(scan)
            session.commit()
            return scan

    async def update_phase(self, scan_id: UUID, phase: str) -> GuardianScan:
        """更新安全、稳定的 graph phase 名称."""
        return await asyncio.to_thread(self._update_phase_sync, scan_id, phase)

    def _update_phase_sync(self, scan_id: UUID, phase: str) -> GuardianScan:
        """执行 phase 更新事务."""
        with Session(self._engine, expire_on_commit=False) as session:
            scan = self._required_scan(session, scan_id)
            scan.phase = phase
            scan.updated_at = _utc_now()
            session.add(scan)
            session.commit()
            return scan

    async def complete_scan(
        self,
        *,
        scan_id: UUID,
        phase: str,
        contract_verdict: GuardianVerdict | None,
        regression_verdict: GuardianVerdict | None,
        final_verdict: GuardianVerdict,
        analysis_degraded: bool,
        oasdiff_version: str,
        schemathesis_version: str,
        error_code: str | None,
        safe_error_summary: str | None,
        completed_at: datetime,
        report_payload: dict[str, Any],
    ) -> GuardianScan:
        """在同一事务中完成扫描并保存唯一报告."""
        return await asyncio.to_thread(
            self._complete_scan_sync,
            scan_id,
            phase,
            contract_verdict,
            regression_verdict,
            final_verdict,
            analysis_degraded,
            oasdiff_version,
            schemathesis_version,
            error_code,
            safe_error_summary,
            completed_at,
            report_payload,
        )

    def _complete_scan_sync(
        self,
        scan_id: UUID,
        phase: str,
        contract_verdict: GuardianVerdict | None,
        regression_verdict: GuardianVerdict | None,
        final_verdict: GuardianVerdict,
        analysis_degraded: bool,
        oasdiff_version: str,
        schemathesis_version: str,
        error_code: str | None,
        safe_error_summary: str | None,
        completed_at: datetime,
        report_payload: dict[str, Any],
    ) -> GuardianScan:
        """执行完成扫描和 upsert 报告的同步短事务."""
        with Session(self._engine, expire_on_commit=False) as session:
            scan = self._required_scan(session, scan_id)
            scan.status = GuardianScanStatus.COMPLETED.value
            scan.phase = phase
            scan.contract_verdict = contract_verdict.value if contract_verdict else None
            scan.regression_verdict = regression_verdict.value if regression_verdict else None
            scan.final_verdict = final_verdict.value
            scan.analysis_degraded = analysis_degraded
            scan.oasdiff_version = oasdiff_version
            scan.schemathesis_version = schemathesis_version
            scan.error_code = error_code
            scan.safe_error_summary = safe_error_summary
            scan.completed_at = completed_at
            scan.updated_at = completed_at
            report = session.exec(
                select(GuardianReportRecord).where(col(GuardianReportRecord.scan_id) == scan_id)
            ).first()
            if report is None:
                report = GuardianReportRecord(scan_id=scan_id, payload=report_payload, generated_at=completed_at)
            else:
                report.payload = report_payload
                report.generated_at = completed_at
            session.add(scan)
            session.add(report)
            session.commit()
            return scan

    async def fail_scan(
        self,
        scan_id: UUID,
        *,
        error_code: str = "guardian_service_failed",
        safe_error_summary: str = "Guardian 服务内部错误",
    ) -> GuardianScan | None:
        """仅为真正的服务异常标记 failed."""
        return await asyncio.to_thread(self._fail_scan_sync, scan_id, error_code, safe_error_summary)

    def _fail_scan_sync(self, scan_id: UUID, error_code: str, safe_error_summary: str) -> GuardianScan | None:
        """执行 failed 状态事务；记录缺失时返回 None."""
        with Session(self._engine, expire_on_commit=False) as session:
            scan = session.get(GuardianScan, scan_id)
            if scan is None:
                return None
            now = _utc_now()
            scan.status = GuardianScanStatus.FAILED.value
            scan.phase = "service_failed"
            scan.error_code = error_code
            scan.safe_error_summary = safe_error_summary
            scan.completed_at = now
            scan.updated_at = now
            session.add(scan)
            session.commit()
            return scan

    async def save_report(self, scan_id: UUID, payload: dict[str, Any]) -> GuardianReportRecord:
        """显式保存或替换脱敏报告，供恢复与维护路径复用."""
        return await asyncio.to_thread(self._save_report_sync, scan_id, payload)

    def _save_report_sync(self, scan_id: UUID, payload: dict[str, Any]) -> GuardianReportRecord:
        """执行报告 upsert 短事务."""
        with Session(self._engine, expire_on_commit=False) as session:
            self._required_scan(session, scan_id)
            report = session.exec(
                select(GuardianReportRecord).where(col(GuardianReportRecord.scan_id) == scan_id)
            ).first()
            if report is None:
                report = GuardianReportRecord(scan_id=scan_id, payload=payload)
            else:
                report.payload = payload
                report.generated_at = _utc_now()
            session.add(report)
            session.commit()
            return report

    async def get_scan(self, scan_id: UUID, user_id: int | None = None) -> GuardianScan | None:
        """按 scan ID 查询，并可在 SQL 层同时执行 owner 过滤."""
        return await asyncio.to_thread(self._get_scan_sync, scan_id, user_id)

    def _get_scan_sync(self, scan_id: UUID, user_id: int | None) -> GuardianScan | None:
        """执行扫描查询."""
        with Session(self._engine, expire_on_commit=False) as session:
            statement = select(GuardianScan).where(col(GuardianScan.id) == scan_id)
            if user_id is not None:
                statement = statement.where(col(GuardianScan.user_id) == user_id)
            return session.exec(statement).first()

    async def get_report(self, scan_id: UUID, user_id: int | None = None) -> GuardianReportRecord | None:
        """查询报告前先在同一 SQL 中校验 owner."""
        return await asyncio.to_thread(self._get_report_sync, scan_id, user_id)

    def _get_report_sync(self, scan_id: UUID, user_id: int | None) -> GuardianReportRecord | None:
        """执行带 owner 约束的报告查询."""
        with Session(self._engine, expire_on_commit=False) as session:
            statement = select(GuardianReportRecord).join(
                GuardianScan,
                col(GuardianReportRecord.scan_id) == col(GuardianScan.id),
            )
            statement = statement.where(col(GuardianReportRecord.scan_id) == scan_id)
            if user_id is not None:
                statement = statement.where(col(GuardianScan.user_id) == user_id)
            return session.exec(statement).first()

    async def has_active_scan(self, user_id: int) -> bool:
        """防止单用户并发启动多个外部工具进程."""
        return await asyncio.to_thread(self._has_active_scan_sync, user_id)

    def _has_active_scan_sync(self, user_id: int) -> bool:
        """查询 pending/running 扫描是否存在."""
        with Session(self._engine) as session:
            statement = select(GuardianScan.id).where(
                col(GuardianScan.user_id) == user_id,
                col(GuardianScan.status).in_((GuardianScanStatus.PENDING.value, GuardianScanStatus.RUNNING.value)),
            )
            return session.exec(statement).first() is not None

    @staticmethod
    def _required_scan(session: Session, scan_id: UUID) -> GuardianScan:
        """在事务内读取必需扫描."""
        scan = session.get(GuardianScan, scan_id)
        if scan is None:
            raise LookupError("Guardian scan not found")
        return scan
