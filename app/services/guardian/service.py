"""FastAPI 与 Guardian Core 之间的应用服务边界."""

import asyncio
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus
from typing import Any, cast
from uuid import UUID, uuid4

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.logging import logger
from app.core.metrics import (
    guardian_analysis_degraded_total,
    guardian_scan_duration_seconds,
    guardian_scans_total,
)
from app.models.guardian import GuardianScan, GuardianScanStatus
from app.schemas.guardian import GuardianReportResponse, GuardianScanDetailResponse, GuardianScanResponse
from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.graph import create_guardian_graph, guardian_policy_from_settings
from app.services.guardian.models import (
    GuardianAnalysisReport,
    GuardianContractReport,
    GuardianPolicy,
    GuardianRegressionReport,
    GuardianReport,
    GuardianSpecSummary,
    GuardianToolSummary,
    GuardianVerdict,
    ScopeSelection,
)
from app.services.guardian.diagnosis import FailureDiagnoser
from app.services.guardian.oasdiff import OASDIFF_VERSION, OasdiffPort
from app.services.guardian.repository import GuardianRepository
from app.services.guardian.risk import RiskAnalyzer
from app.services.guardian.schemathesis import SCHEMATHESIS_VERSION, SchemathesisPort
from app.services.guardian.security import prepare_openapi_spec, validate_target_base_url
from app.services.guardian.state import GuardianState, rehydrate_guardian_state

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

# psycopg async 明确不支持 Windows Proactor loop；API 模块在 Uvicorn 建 loop 前导入此处。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class GuardianScanNotFoundError(Exception):
    """扫描不存在或不属于当前用户."""


class GuardianActiveScanError(Exception):
    """同一用户已有 pending/running 扫描."""


class GuardianCheckpointInterrupted(Exception):
    """仅供 checkpoint 恢复测试使用的可识别中断."""

    def __init__(self, scan_id: UUID, next_nodes: tuple[str, ...], phase: str) -> None:
        """保存不含输入路径或 Secret 的中断证据."""
        super().__init__("Guardian graph intentionally interrupted")
        self.scan_id = scan_id
        self.next_nodes = next_nodes
        self.phase = phase


class GuardianService:
    """协调 durable input、Guardian graph、checkpoint 与数据库生命周期."""

    def __init__(
        self,
        *,
        repository: GuardianRepository | None = None,
        policy: GuardianPolicy | None = None,
        runs_dir: Path | None = None,
        checkpoint_database_url: str | None = None,
        oasdiff: OasdiffPort | None = None,
        schemathesis: SchemathesisPort | None = None,
        risk_analyzer: RiskAnalyzer | None = None,
        failure_diagnoser: FailureDiagnoser | None = None,
        graph: CompiledStateGraph | None = None,
        interrupt_after: list[str] | None = None,
    ) -> None:
        """注入最少依赖；默认路径使用独立 PostgreSQL checkpointer."""
        self._repository = repository or GuardianRepository()
        self._policy = policy or guardian_policy_from_settings()
        self._runs_dir = (runs_dir or settings.GUARDIAN_RUNS_DIR).resolve()
        self._checkpoint_database_url = checkpoint_database_url or self._database_url_from_settings()
        self._oasdiff = oasdiff
        self._schemathesis = schemathesis
        self._risk_analyzer = risk_analyzer
        self._failure_diagnoser = failure_diagnoser
        self._graph = graph
        self._interrupt_after = interrupt_after
        self._connection_pool: PostgresConnPool | None = None
        self._initialization_lock = asyncio.Lock()
        self._user_locks: dict[int, asyncio.Lock] = {}

    async def initialize(self) -> None:
        """预创建独立 Guardian graph、连接池和 checkpoint 表."""
        await self._get_graph()

    async def close(self) -> None:
        """关闭 Guardian 自己的 PostgreSQL 连接池."""
        if self._connection_pool is not None:
            await self._connection_pool.close()
            self._connection_pool = None
            if self._graph is not None and self._checkpoint_database_url:
                self._graph = None
            logger.info("guardian_checkpoint_pool_closed")

    async def create_scan(
        self,
        *,
        user_id: int,
        base_spec: bytes,
        head_spec: bytes,
        base_extension: str,
        head_extension: str,
        target_base_url: str,
    ) -> GuardianScanResponse:
        """保存并验证输入，执行完整 graph，然后原子持久化摘要和报告."""
        user_lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with user_lock:
            if await self._repository.has_active_scan(user_id):
                raise GuardianActiveScanError
            scan_id = uuid4()
            workspace_created = False
            database_record_created = False
            try:
                base_path, head_path = await self._save_and_validate_specs(
                    scan_id,
                    base_spec,
                    head_spec,
                    base_extension,
                    head_extension,
                )
                workspace_created = True
                normalized_target = await validate_target_base_url(
                    target_base_url,
                    self._policy.allowed_target_hosts,
                    self._policy.allow_private_targets,
                )
                base_info, head_info = await asyncio.gather(
                    asyncio.to_thread(prepare_openapi_spec, str(base_path), self._policy.max_openapi_bytes),
                    asyncio.to_thread(prepare_openapi_spec, str(head_path), self._policy.max_openapi_bytes),
                )
                scan = await self._repository.create_scan(
                    scan_id=scan_id,
                    user_id=user_id,
                    base_spec_sha256=base_info.sha256,
                    head_spec_sha256=head_info.sha256,
                    policy_snapshot=self._policy.model_dump(mode="json"),
                )
                database_record_created = True
                scan = await self._repository.mark_running(scan_id)
                state = await self._invoke_graph(
                    scan_id,
                    {
                        "scan_id": str(scan_id),
                        "base_spec_path": str(base_path),
                        "head_spec_path": str(head_path),
                        "target_base_url": normalized_target,
                    },
                )
                completed = await self._complete_scan(scan, state)
                return self._scan_response(completed)
            except GuardianCheckpointInterrupted as exc:
                # 测试中断必须保留 running 状态、durable workspace 和 checkpoint。
                await self._repository.update_phase(scan_id, exc.phase)
                raise
            except GuardianException:
                if not database_record_created and workspace_created:
                    await self._remove_workspace(scan_id)
                raise
            except Exception:
                if database_record_created:
                    await self._mark_failed_safely(scan_id)
                elif workspace_created:
                    await self._remove_workspace(scan_id)
                raise

    async def resume_scan(self, scan_id: UUID, user_id: int) -> GuardianScanResponse:
        """使用同一 thread_id/namespace 从 PostgreSQL checkpoint 继续运行."""
        scan = await self._repository.get_scan(scan_id, user_id)
        if scan is None or scan.status != GuardianScanStatus.RUNNING.value:
            raise GuardianScanNotFoundError
        try:
            state = await self._invoke_graph(scan_id, None)
            completed = await self._complete_scan(scan, state)
            return self._scan_response(completed)
        except GuardianCheckpointInterrupted as exc:
            await self._repository.update_phase(scan_id, exc.phase)
            raise
        except Exception:
            await self._mark_failed_safely(scan_id)
            raise

    async def get_scan(self, scan_id: UUID, user_id: int) -> GuardianScanDetailResponse:
        """返回 owner 过滤后的扫描详情."""
        scan = await self._repository.get_scan(scan_id, user_id)
        if scan is None:
            raise GuardianScanNotFoundError
        return self._scan_detail_response(scan)

    async def get_report(self, scan_id: UUID, user_id: int) -> GuardianReportResponse:
        """返回 owner 过滤并重新经过 Pydantic 白名单校验的报告."""
        report = await self._repository.get_report(scan_id, user_id)
        if report is None:
            raise GuardianScanNotFoundError
        return GuardianReportResponse.model_validate(report.payload)

    async def _get_graph(self) -> CompiledStateGraph:
        """惰性创建独立 Guardian PostgreSQL checkpointer graph."""
        if self._graph is not None:
            return self._graph
        async with self._initialization_lock:
            if self._graph is not None:
                return self._graph
            self._connection_pool = AsyncConnectionPool(
                self._checkpoint_database_url,
                open=False,
                max_size=settings.POSTGRES_POOL_SIZE,
                kwargs={
                    "autocommit": True,
                    "connect_timeout": 5,
                    "prepare_threshold": None,
                    "row_factory": dict_row,
                },
            )
            await self._connection_pool.open()
            checkpointer = AsyncPostgresSaver(self._connection_pool)
            await checkpointer.setup()
            self._graph = create_guardian_graph(
                policy=self._policy,
                oasdiff=self._oasdiff,
                schemathesis=self._schemathesis,
                risk_analyzer=self._risk_analyzer,
                failure_diagnoser=self._failure_diagnoser,
                checkpointer=checkpointer,
                interrupt_after=self._interrupt_after,
            )
            logger.info("guardian_checkpoint_graph_created", checkpoint_thread_prefix="guardian:")
            return self._graph

    async def _invoke_graph(self, scan_id: UUID, state: GuardianState | None) -> GuardianState:
        """使用稳定 namespace/thread_id 调用或恢复 graph."""
        graph = await self._get_graph()
        config = self._checkpoint_config(scan_id)
        result = await graph.ainvoke(state, config=config)
        snapshot = await graph.aget_state(config)
        normalized = rehydrate_guardian_state(cast(dict[str, Any], result))
        if snapshot.next:
            completed_nodes = normalized.get("completed_nodes", ())
            phase = completed_nodes[-1] if completed_nodes else "checkpoint_interrupted"
            raise GuardianCheckpointInterrupted(scan_id, tuple(snapshot.next), phase)
        return normalized

    async def _complete_scan(self, scan: GuardianScan, state: GuardianState) -> GuardianScan:
        """从白名单 state 构造报告，并与 completed 状态原子保存."""
        completed_at = datetime.now(UTC)
        report = self._build_report(scan, state, completed_at)
        errors = state.get("errors", ())
        first_error = errors[0] if errors else None
        completed_nodes = state.get("completed_nodes", ())
        completed = await self._repository.complete_scan(
            scan_id=scan.id,
            phase=completed_nodes[-1] if completed_nodes else "build_final_verdict",
            contract_verdict=state.get("contract_verdict"),
            regression_verdict=state.get("regression_verdict"),
            final_verdict=report.final_verdict,
            analysis_degraded=report.analysis.degraded,
            oasdiff_version=OASDIFF_VERSION,
            schemathesis_version=SCHEMATHESIS_VERSION,
            error_code=first_error.code.value if first_error else None,
            safe_error_summary=first_error.message if first_error else None,
            completed_at=completed_at,
            report_payload=report.model_dump(mode="json"),
        )
        guardian_scans_total.labels(final_verdict=report.final_verdict.value).inc()
        duration_seconds = max(0.0, (completed_at - (scan.started_at or scan.created_at)).total_seconds())
        guardian_scan_duration_seconds.observe(duration_seconds)
        if report.analysis.degraded:
            guardian_analysis_degraded_total.inc()
        return completed

    def _build_report(
        self,
        scan: GuardianScan,
        state: GuardianState,
        completed_at: datetime,
    ) -> GuardianReport:
        """显式挑选允许持久化的字段，绝不转储整个 State."""
        contract_verdict = state.get("contract_verdict", GuardianVerdict.ERROR)
        final_verdict = state.get("final_verdict", GuardianVerdict.ERROR)
        scope = state.get("selected_scope", ScopeSelection())
        return GuardianReport(
            scan_id=scan.id,
            specs=GuardianSpecSummary(
                base_sha256=state.get("base_spec_sha256", scan.base_spec_sha256),
                head_sha256=state.get("head_spec_sha256", scan.head_spec_sha256),
            ),
            tools=GuardianToolSummary(
                oasdiff_version=OASDIFF_VERSION,
                schemathesis_version=SCHEMATHESIS_VERSION,
                contract_run=state.get("contract_tool"),
                regression_run=state.get("regression_tool"),
            ),
            policy=state.get("policy", self._policy),
            contract=GuardianContractReport(
                verdict=contract_verdict,
                changes=state.get("contract_changes", ()),
            ),
            scope=scope,
            regression=GuardianRegressionReport(
                verdict=state.get("regression_verdict"),
                results=state.get("regression_results", ()),
            ),
            final_verdict=final_verdict,
            errors=state.get("errors", ()),
            analysis=GuardianAnalysisReport(
                enabled=state.get("analysis_enabled", self._policy.analysis_enabled),
                degraded=state.get("analysis_degraded", False),
                risk_explanations=state.get("risk_explanations", ()),
                failure_diagnoses=state.get("failure_diagnoses", ()),
                warnings=state.get("analysis_warnings", ()),
                truncated=state.get("analysis_truncated", False),
            ),
            created_at=scan.created_at,
            completed_at=completed_at,
        )

    async def _save_and_validate_specs(
        self,
        scan_id: UUID,
        base_spec: bytes,
        head_spec: bytes,
        base_extension: str,
        head_extension: str,
    ) -> tuple[Path, Path]:
        """使用服务端文件名写入 durable workspace，并立即校验实际内容."""
        if len(base_spec) > self._policy.max_openapi_bytes or len(head_spec) > self._policy.max_openapi_bytes:
            raise GuardianException(GuardianErrorCode.OPENAPI_TOO_LARGE, "OpenAPI 文件超过配置限制")
        if base_extension not in {".json", ".yaml"} or head_extension not in {".json", ".yaml"}:
            raise GuardianException(GuardianErrorCode.INVALID_OPENAPI, "OpenAPI 文件扩展名无效")
        input_dir = self._runs_dir / str(scan_id) / "input"
        await asyncio.to_thread(input_dir.mkdir, parents=True, exist_ok=False)
        base_path = input_dir / f"base{base_extension}"
        head_path = input_dir / f"head{head_extension}"
        try:
            await asyncio.gather(
                asyncio.to_thread(base_path.write_bytes, base_spec),
                asyncio.to_thread(head_path.write_bytes, head_spec),
            )
            await asyncio.gather(
                asyncio.to_thread(prepare_openapi_spec, str(base_path), self._policy.max_openapi_bytes),
                asyncio.to_thread(prepare_openapi_spec, str(head_path), self._policy.max_openapi_bytes),
            )
        except Exception:
            await self._remove_workspace(scan_id)
            raise
        return base_path, head_path

    async def _remove_workspace(self, scan_id: UUID) -> None:
        """仅清理由服务端 UUID 精确定位、尚未提交的扫描目录."""
        scan_dir = self._runs_dir / str(scan_id)
        await asyncio.to_thread(shutil.rmtree, scan_dir, True)

    async def _mark_failed_safely(self, scan_id: UUID) -> None:
        """尽力记录内部失败；避免二次数据库异常覆盖原始异常."""
        try:
            await self._repository.fail_scan(scan_id)
        except Exception:
            logger.exception("guardian_scan_fail_status_update_failed", scan_id=str(scan_id))

    @staticmethod
    def _checkpoint_config(scan_id: UUID) -> RunnableConfig:
        """用稳定 thread 前缀隔离 Guardian；非空 checkpoint_ns 仅供子图使用."""
        return {
            "configurable": {
                "thread_id": f"guardian:{scan_id}",
            }
        }

    @staticmethod
    def _scan_response(scan: GuardianScan) -> GuardianScanResponse:
        """把 ORM 转成独立 HTTP schema."""
        return GuardianScanResponse(
            scan_id=scan.id,
            status=scan.status,
            contract_verdict=GuardianService._optional_verdict(scan.contract_verdict),
            regression_verdict=GuardianService._optional_verdict(scan.regression_verdict),
            final_verdict=GuardianService._optional_verdict(scan.final_verdict),
            analysis_degraded=scan.analysis_degraded,
            created_at=scan.created_at,
            completed_at=scan.completed_at,
        )

    @staticmethod
    def _scan_detail_response(scan: GuardianScan) -> GuardianScanDetailResponse:
        """把 ORM 转成完整安全 HTTP schema."""
        return GuardianScanDetailResponse(
            scan_id=scan.id,
            status=scan.status,
            phase=scan.phase,
            contract_verdict=GuardianService._optional_verdict(scan.contract_verdict),
            regression_verdict=GuardianService._optional_verdict(scan.regression_verdict),
            final_verdict=GuardianService._optional_verdict(scan.final_verdict),
            base_spec_sha256=scan.base_spec_sha256,
            head_spec_sha256=scan.head_spec_sha256,
            oasdiff_version=scan.oasdiff_version,
            schemathesis_version=scan.schemathesis_version,
            policy_snapshot=scan.policy_snapshot,
            analysis_degraded=scan.analysis_degraded,
            error_code=scan.error_code,
            safe_error_summary=scan.safe_error_summary,
            created_at=scan.created_at,
            started_at=scan.started_at,
            completed_at=scan.completed_at,
            updated_at=scan.updated_at,
        )

    @staticmethod
    def _database_url_from_settings() -> str:
        """构造只传给 psycopg、不进入日志的连接串."""
        return (
            "postgresql://"
            f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
            f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
        )

    @staticmethod
    def _optional_verdict(value: str | None) -> GuardianVerdict | None:
        """把数据库稳定字符串恢复为 HTTP verdict enum."""
        return GuardianVerdict(value) if value is not None else None


guardian_service = GuardianService()
