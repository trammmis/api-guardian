"""Phase 3 HTTP、PostgreSQL 与 checkpoint 恢复验收测试."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any, override
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, select

from app.api.v1 import auth as auth_module
from app.api.v1.guardian import get_guardian_service, router as guardian_router
from app.core.config import settings
from app.core.limiter import limiter
from app.models.guardian import GuardianReportRecord, GuardianScan, GuardianScanStatus
from app.models.user import User
from app.schemas.guardian import GuardianReportResponse, GuardianScanDetailResponse, GuardianScanResponse
from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianPolicy, GuardianVerdict
from app.services.guardian.diagnosis import FailureDiagnoser
from app.services.guardian.oasdiff import OasdiffAdapter
from app.services.guardian.repository import GuardianRepository
from app.services.guardian.risk import RiskAnalyzer
from app.services.guardian.schemathesis import SchemathesisAdapter
from app.services.guardian.service import GuardianActiveScanError, GuardianCheckpointInterrupted, GuardianService
from app.services.guardian.subprocess import ProcessResult
from app.utils.auth import create_access_token
from tests.guardian.fixture_api import app as regression_fixture_app
from tests.guardian.fakes import StaticProcessRunner
from tests.guardian.test_analysis import FactDrivenLLM

FIXTURE_DIR = Path(__file__).parent / "fixtures"
ROOT_DIR = Path(__file__).parents[2]
TEST_EMAIL_PREFIX = "guardian-phase3-"


def _test_database_url() -> str:
    """读取真实 PostgreSQL 测试连接；未配置时跳过数据库集成测试."""
    value = os.getenv("GUARDIAN_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("GUARDIAN_TEST_POSTGRES_URL is required for Phase 3 PostgreSQL tests")
    return value


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    """对真实 PostgreSQL 执行正式 Alembic upgrade，并提供同步 SQLModel engine."""
    database_url = _test_database_url()
    parsed = make_url(database_url)
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_HOST": parsed.host or "127.0.0.1",
            "POSTGRES_PORT": str(parsed.port or 5432),
            "POSTGRES_DB": parsed.database or "",
            "POSTGRES_USER": parsed.username or "",
            "POSTGRES_PASSWORD": parsed.password or "",
        }
    )
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT_DIR,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert migration.returncode == 0, migration.stderr
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        _cleanup_phase3_records(engine)
        engine.dispose()


@pytest.fixture
def guardian_tmp_path() -> Iterator[Path]:
    """在仓库受控目录创建测试 workspace，绕开宿主临时目录 ACL 差异."""
    path = ROOT_DIR / ".guardian_runs" / "_tests" / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _cleanup_phase3_records(engine: Engine) -> None:
    """清理由本模块创建的业务记录与相应 checkpoint."""
    with engine.begin() as connection:
        scan_ids = connection.execute(
            text(
                'SELECT gs.id::text FROM guardian_scan gs JOIN "user" u ON u.id = gs.user_id '
                "WHERE u.email LIKE :prefix"
            ),
            {"prefix": f"{TEST_EMAIL_PREFIX}%"},
        ).scalars()
        thread_ids = [f"guardian:{scan_id}" for scan_id in scan_ids]
        for table_name in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            exists = connection.execute(text("SELECT to_regclass(:table_name)"), {"table_name": table_name}).scalar()
            if exists and thread_ids:
                connection.execute(
                    text(f"DELETE FROM {table_name} WHERE thread_id = ANY(:thread_ids)"),
                    {"thread_ids": thread_ids},
                )
        connection.execute(
            text(
                "DELETE FROM guardian_report_record WHERE scan_id IN "
                '(SELECT gs.id FROM guardian_scan gs JOIN "user" u ON u.id = gs.user_id '
                "WHERE u.email LIKE :prefix)"
            ),
            {"prefix": f"{TEST_EMAIL_PREFIX}%"},
        )
        connection.execute(
            text('DELETE FROM guardian_scan WHERE user_id IN (SELECT id FROM "user" WHERE email LIKE :prefix)'),
            {"prefix": f"{TEST_EMAIL_PREFIX}%"},
        )
        connection.execute(
            text('DELETE FROM "user" WHERE email LIKE :prefix'),
            {"prefix": f"{TEST_EMAIL_PREFIX}%"},
        )


def _checkpoint_count(engine: Engine, scan_id: UUID) -> int:
    """查询指定 Guardian thread 已写入的真实 PostgreSQL checkpoint 数量."""
    with engine.connect() as connection:
        value = connection.execute(
            text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread_id"),
            {"thread_id": f"guardian:{scan_id}"},
        ).scalar_one()
    return int(value)


def _create_user(engine: Engine) -> User:
    """在真实业务表创建独立 JWT 用户."""
    with Session(engine, expire_on_commit=False) as session:
        user = User(
            email=f"{TEST_EMAIL_PREFIX}{uuid4().hex}@example.test",
            hashed_password=User.hash_password("Phase3-test-password"),
            username="guardian-test",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


class _ThreadedAuthDatabase:
    """测试中保持 get_current_user 的真实 JWT 流程，仅替换数据库连接."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def get_user(self, user_id: int) -> User | None:
        """在线程中读取测试用户，避免测试 event loop 被同步 Session 阻塞."""
        return await asyncio.to_thread(self._get_user_sync, user_id)

    def _get_user_sync(self, user_id: int) -> User | None:
        """执行真实 PostgreSQL 用户查询."""
        with Session(self._engine, expire_on_commit=False) as session:
            return session.get(User, user_id)


class _FailingCompleteRepository(GuardianRepository):
    """仅在最终事务制造内部数据库失败的测试 Repository."""

    @override
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
        """模拟报告/完成事务失败，其他方法仍使用真实 PostgreSQL."""
        del (
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
        raise RuntimeError("simulated repository failure")


def _policy() -> GuardianPolicy:
    """只对精确 loopback host 开启 development-only 策略."""
    return GuardianPolicy(allowed_target_hosts=("127.0.0.1",), allow_private_targets=True)


def _fake_service(
    engine: Engine,
    runs_dir: Path,
    database_url: str,
    *,
    schemathesis_report: str = "schemathesis_fail.ndjson",
    interrupt_after: list[str] | None = None,
    repository: GuardianRepository | None = None,
    policy: GuardianPolicy | None = None,
    risk_analyzer: RiskAnalyzer | None = None,
    failure_diagnoser: FailureDiagnoser | None = None,
) -> GuardianService:
    """构造使用真实 DB/checkpointer、离线确定性工具 fixture 的 Service."""
    oasdiff = OasdiffAdapter(
        runner=StaticProcessRunner(
            ProcessResult(
                exit_code=1,
                stdout=(FIXTURE_DIR / "oasdiff_breaking.json").read_bytes(),
                stderr=b"",
            )
        )
    )
    schemathesis = SchemathesisAdapter(
        runner=StaticProcessRunner(
            ProcessResult(exit_code=1, stdout=b"", stderr=b""),
            report_payload=(FIXTURE_DIR / schemathesis_report).read_bytes(),
        )
    )
    return GuardianService(
        repository=repository or GuardianRepository(engine),
        policy=policy or _policy(),
        runs_dir=runs_dir,
        checkpoint_database_url=database_url,
        oasdiff=oasdiff,
        schemathesis=schemathesis,
        risk_analyzer=risk_analyzer,
        failure_diagnoser=failure_diagnoser,
        interrupt_after=interrupt_after,
    )


def _test_app(service: GuardianService) -> FastAPI:
    """只挂载 Guardian route，避免测试触发无关 LLM 启动流程."""
    limiter.reset()
    application = FastAPI()
    application.state.limiter = limiter
    application.include_router(guardian_router, prefix="/api/v1/guardian")
    application.dependency_overrides[get_guardian_service] = lambda: service
    return application


def _auth_header(user: User) -> dict[str, str]:
    """用项目现有 JWT 工具创建 Bearer header."""
    token = create_access_token(str(user.id))
    return {"Authorization": f"Bearer {token.access_token}"}


async def _post_scan(
    application: FastAPI,
    user: User,
    target_url: str,
    *,
    base_path: Path | None = None,
    head_path: Path | None = None,
) -> httpx.Response:
    """通过 multipart 调用真实 Guardian route."""
    selected_base = base_path or FIXTURE_DIR / "baseline_openapi.yaml"
    selected_head = head_path or FIXTURE_DIR / "head_breaking_openapi.yaml"
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://guardian.test") as client:
        return await client.post(
            "/api/v1/guardian/scans",
            headers=_auth_header(user),
            data={"target_base_url": target_url},
            files={
                "base_spec": ("ignored-base-name.yaml", selected_base.read_bytes(), "application/yaml"),
                "head_spec": ("ignored-head-name.yaml", selected_head.read_bytes(), "application/yaml"),
            },
        )


def test_alembic_upgrade_created_guardian_tables(postgres_engine: Engine) -> None:
    """正式 migration 必须创建两张业务表并到达唯一 head."""
    with postgres_engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = set(connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars())
    assert revision == "e3a1b7c9d2f4"
    assert {"guardian_scan", "guardian_report_record"} <= tables


def test_http_jwt_persistence_ownership_and_secret_whitelist(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST/GET 走真实 JWT、DB、checkpoint，并拒绝跨用户读取."""
    database_url = _test_database_url()
    owner = _create_user(postgres_engine)
    other_user = _create_user(postgres_engine)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "phase3-integration-jwt-key")
    monkeypatch.setattr(auth_module, "db_service", _ThreadedAuthDatabase(postgres_engine))
    service = _fake_service(postgres_engine, guardian_tmp_path / "runs", database_url)
    application = _test_app(service)

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        try:
            created = await _post_scan(application, owner, "http://127.0.0.1:8000")
            assert created.status_code == 200, created.text
            scan_id = created.json()["scan_id"]
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(transport=transport, base_url="http://guardian.test") as client:
                detail = await client.get(f"/api/v1/guardian/scans/{scan_id}", headers=_auth_header(owner))
                report = await client.get(f"/api/v1/guardian/scans/{scan_id}/report", headers=_auth_header(owner))
                other_detail = await client.get(f"/api/v1/guardian/scans/{scan_id}", headers=_auth_header(other_user))
                other_report = await client.get(
                    f"/api/v1/guardian/scans/{scan_id}/report",
                    headers=_auth_header(other_user),
                )
            return created, detail, report, other_detail, other_report
        finally:
            await service.close()

    created, detail, report, other_detail, other_report = asyncio.run(exercise())
    assert created.json()["status"] == GuardianScanStatus.COMPLETED.value
    assert created.json()["contract_verdict"] == "fail"
    assert created.json()["regression_verdict"] == "fail"
    assert created.json()["final_verdict"] == "fail"
    assert detail.status_code == 200
    assert report.status_code == 200
    assert other_detail.status_code == 404
    assert other_report.status_code == 404

    report_text = report.text.lower()
    assert "authorization" not in report_text
    assert "cookie" not in report_text
    assert "raw ndjson" not in report_text
    assert "request_body" not in report_text
    assert "response_body" not in report_text
    assert str(guardian_tmp_path).lower().replace("\\", "/") not in report_text.replace("\\", "/")
    scan_id = UUID(created.json()["scan_id"])
    with Session(postgres_engine) as session:
        assert session.get(GuardianScan, scan_id) is not None
        assert (
            session.exec(select(GuardianReportRecord).where(GuardianReportRecord.scan_id == scan_id)).first()
            is not None
        )
    workspace_files = {path.name for path in (guardian_tmp_path / "runs" / str(scan_id) / "input").iterdir()}
    assert workspace_files == {"base.yaml", "head.yaml"}


def test_http_persists_enhanced_analysis_without_raw_llm_data(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """增强 Report/Scan 只保存结构化分析，不保存 prompt、provider response 或 Secret."""
    user = _create_user(postgres_engine)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "phase4-analysis-jwt-key")
    monkeypatch.setattr(auth_module, "db_service", _ThreadedAuthDatabase(postgres_engine))
    analysis_policy = _policy().model_copy(update={"analysis_enabled": True})
    service = _fake_service(
        postgres_engine,
        guardian_tmp_path / "runs",
        _test_database_url(),
        policy=analysis_policy,
        risk_analyzer=RiskAnalyzer(FactDrivenLLM()),
        failure_diagnoser=FailureDiagnoser(FactDrivenLLM()),
    )
    application = _test_app(service)

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        try:
            response = await _post_scan(application, user, "http://127.0.0.1:8000")
            scan_id = response.json()["scan_id"]
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(transport=transport, base_url="http://guardian.test") as client:
                report = await client.get(
                    f"/api/v1/guardian/scans/{scan_id}/report",
                    headers=_auth_header(user),
                )
            return response, report
        finally:
            await service.close()

    response, report = asyncio.run(exercise())
    payload = report.json()
    assert response.status_code == 200
    assert response.json()["final_verdict"] == GuardianVerdict.FAIL.value
    assert response.json()["analysis_degraded"] is False
    assert payload["analysis"]["enabled"] is True
    assert payload["analysis"]["degraded"] is False
    assert len(payload["analysis"]["risk_explanations"]) == 1
    assert len(payload["analysis"]["failure_diagnoses"]) == 1
    scan_id = UUID(response.json()["scan_id"])
    with Session(postgres_engine) as session:
        scan = session.get(GuardianScan, scan_id)
        record = session.exec(select(GuardianReportRecord).where(GuardianReportRecord.scan_id == scan_id)).one()
    assert scan is not None
    assert scan.analysis_degraded is False
    persisted = json.dumps(record.payload, ensure_ascii=False).lower()
    for forbidden in (
        "authorization",
        "cookie",
        "api_key",
        "jwt",
        "database_url",
        "raw_ndjson",
        "request_body",
        "response_body",
        str(guardian_tmp_path).lower().replace("\\", "/"),
    ):
        assert forbidden not in persisted.replace("\\", "/")


def test_http_llm_failure_completes_scan_with_unchanged_verdict(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 失败只设置 analysis_degraded，不得变成 service failed 或 final ERROR."""
    user = _create_user(postgres_engine)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "phase4-degraded-jwt-key")
    monkeypatch.setattr(auth_module, "db_service", _ThreadedAuthDatabase(postgres_engine))
    policy = _policy().model_copy(update={"analysis_enabled": True})
    service = _fake_service(
        postgres_engine,
        guardian_tmp_path / "runs",
        _test_database_url(),
        policy=policy,
        risk_analyzer=RiskAnalyzer(FactDrivenLLM(RuntimeError("provider secret must not persist"))),
        failure_diagnoser=FailureDiagnoser(FactDrivenLLM()),
    )
    application = _test_app(service)

    async def exercise() -> tuple[httpx.Response, GuardianScanDetailResponse, GuardianReportResponse]:
        try:
            response = await _post_scan(application, user, "http://127.0.0.1:8000")
            scan_id = UUID(response.json()["scan_id"])
            detail = await service.get_scan(scan_id, user.id)
            report = await service.get_report(scan_id, user.id)
            return response, detail, report
        finally:
            await service.close()

    response, detail, report = asyncio.run(exercise())
    assert response.status_code == 200
    assert response.json()["status"] == GuardianScanStatus.COMPLETED.value
    assert response.json()["contract_verdict"] == GuardianVerdict.FAIL.value
    assert response.json()["regression_verdict"] == GuardianVerdict.FAIL.value
    assert response.json()["final_verdict"] == GuardianVerdict.FAIL.value
    assert response.json()["analysis_degraded"] is True
    assert detail.status == GuardianScanStatus.COMPLETED.value
    assert detail.analysis_degraded is True
    assert report.analysis.degraded is True
    assert "provider secret" not in report.model_dump_json()


def test_http_requires_jwt_and_rejects_invalid_inputs(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 JWT、invalid OpenAPI 与不允许 target 必须在工具执行前返回 4xx."""
    database_url = _test_database_url()
    user = _create_user(postgres_engine)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "phase3-input-jwt-key")
    monkeypatch.setattr(auth_module, "db_service", _ThreadedAuthDatabase(postgres_engine))
    service = _fake_service(postgres_engine, guardian_tmp_path / "runs", database_url)
    application = _test_app(service)

    async def exercise() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        try:
            transport = httpx.ASGITransport(app=application)
            async with httpx.AsyncClient(transport=transport, base_url="http://guardian.test") as client:
                unauthorized = await client.post("/api/v1/guardian/scans")
            invalid = await _post_scan(
                application,
                user,
                "http://127.0.0.1:8000",
                head_path=FIXTURE_DIR / "invalid_openapi.yaml",
            )
            forbidden_target = await _post_scan(application, user, "http://localhost:8000")
            return unauthorized, invalid, forbidden_target
        finally:
            await service.close()

    unauthorized, invalid, forbidden_target = asyncio.run(exercise())
    assert unauthorized.status_code in {401, 403}
    assert invalid.status_code == 422
    assert forbidden_target.status_code == 403


def test_invalid_jwt_is_not_copied_into_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """复用现有 JWT 依赖时，失败日志也不能包含 Bearer token."""
    secret_token = "abc.def.ghi"
    captured: list[tuple[str, dict[str, object]]] = []

    def capture_error(event: str, **kwargs: object) -> None:
        captured.append((event, kwargs))

    monkeypatch.setattr(auth_module.logger, "error", capture_error)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=secret_token)
    with pytest.raises(HTTPException):
        asyncio.run(auth_module.get_current_user(credentials))
    assert secret_token not in repr(captured)


def test_http_upload_is_bounded_before_service_execution(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multipart 上传按块读取，超过配置上限立即返回 413."""
    user = _create_user(postgres_engine)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "phase3-upload-jwt-key")
    monkeypatch.setattr(settings, "GUARDIAN_MAX_OPENAPI_BYTES", 16)
    monkeypatch.setattr(auth_module, "db_service", _ThreadedAuthDatabase(postgres_engine))
    service = _fake_service(postgres_engine, guardian_tmp_path / "runs", _test_database_url())
    application = _test_app(service)

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://guardian.test") as client:
            return await client.post(
                "/api/v1/guardian/scans",
                headers=_auth_header(user),
                data={"target_base_url": "http://127.0.0.1:8000"},
                files={
                    "base_spec": ("ignored.yaml", b"x" * 17, "application/yaml"),
                    "head_spec": ("ignored.yaml", b"openapi: 3.1.0", "application/yaml"),
                },
            )

    response = asyncio.run(exercise())
    assert response.status_code == 413
    with Session(postgres_engine) as session:
        scans = session.exec(select(GuardianScan).where(GuardianScan.user_id == user.id)).all()
    assert scans == []


def test_guardian_detection_error_is_completed_not_failed(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 中 Schemathesis 网络类 ERROR 仍是 completed，而非服务 failed."""
    user = _create_user(postgres_engine)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "phase3-error-jwt-key")
    monkeypatch.setattr(auth_module, "db_service", _ThreadedAuthDatabase(postgres_engine))
    service = _fake_service(
        postgres_engine,
        guardian_tmp_path / "runs",
        _test_database_url(),
        schemathesis_report="schemathesis_error.ndjson",
    )
    application = _test_app(service)

    async def exercise() -> tuple[httpx.Response, GuardianScanDetailResponse]:
        try:
            response = await _post_scan(application, user, "http://127.0.0.1:8000")
            detail = await service.get_scan(UUID(response.json()["scan_id"]), user.id)
            return response, detail
        finally:
            await service.close()

    response, detail = asyncio.run(exercise())
    assert response.status_code == 200
    assert response.json()["status"] == GuardianScanStatus.COMPLETED.value
    assert response.json()["regression_verdict"] == GuardianVerdict.ERROR.value
    assert response.json()["final_verdict"] == GuardianVerdict.ERROR.value
    assert detail.status == GuardianScanStatus.COMPLETED.value


def test_repository_internal_failure_marks_scan_failed(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
) -> None:
    """真正的 Repository 异常必须标记 failed，而不是业务 verdict ERROR."""
    user = _create_user(postgres_engine)
    base_repository = GuardianRepository(postgres_engine)
    service = _fake_service(
        postgres_engine,
        guardian_tmp_path / "runs",
        _test_database_url(),
        repository=_FailingCompleteRepository(postgres_engine),
    )

    async def exercise() -> None:
        try:
            with pytest.raises(RuntimeError, match="simulated repository failure"):
                await service.create_scan(
                    user_id=user.id,
                    base_spec=(FIXTURE_DIR / "baseline_openapi.yaml").read_bytes(),
                    head_spec=(FIXTURE_DIR / "head_breaking_openapi.yaml").read_bytes(),
                    base_extension=".yaml",
                    head_extension=".yaml",
                    target_base_url="http://127.0.0.1:8000",
                )
        finally:
            await service.close()

    asyncio.run(exercise())
    with Session(postgres_engine) as session:
        failed_scan = session.exec(select(GuardianScan).where(GuardianScan.user_id == user.id)).first()
    assert failed_scan is not None
    assert failed_scan.status == GuardianScanStatus.FAILED.value
    assert failed_scan.final_verdict is None
    assert failed_scan.error_code == "guardian_service_failed"
    assert asyncio.run(base_repository.has_active_scan(user.id)) is False


def test_postgres_checkpoint_interrupt_and_resume_matches_uninterrupted(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
) -> None:
    """真实 PostgreSQL checkpoint 在重建 Service 后继续，并保持 Pydantic DTO."""
    database_url = _test_database_url()
    baseline_user = _create_user(postgres_engine)
    interrupted_user = _create_user(postgres_engine)
    baseline_service = _fake_service(postgres_engine, guardian_tmp_path / "baseline-runs", database_url)
    interrupted_service = _fake_service(
        postgres_engine,
        guardian_tmp_path / "resume-runs",
        database_url,
        interrupt_after=["build_contract_verdict"],
    )

    async def exercise() -> tuple[
        GuardianScanResponse,
        GuardianScanResponse,
        GuardianReportResponse,
        GuardianReportResponse,
    ]:
        baseline_response = await baseline_service.create_scan(
            user_id=baseline_user.id,
            base_spec=(FIXTURE_DIR / "baseline_openapi.yaml").read_bytes(),
            head_spec=(FIXTURE_DIR / "head_breaking_openapi.yaml").read_bytes(),
            base_extension=".yaml",
            head_extension=".yaml",
            target_base_url="http://127.0.0.1:8000",
        )
        with pytest.raises(GuardianCheckpointInterrupted) as interrupted:
            await interrupted_service.create_scan(
                user_id=interrupted_user.id,
                base_spec=(FIXTURE_DIR / "baseline_openapi.yaml").read_bytes(),
                head_spec=(FIXTURE_DIR / "head_breaking_openapi.yaml").read_bytes(),
                base_extension=".yaml",
                head_extension=".yaml",
                target_base_url="http://127.0.0.1:8000",
            )
        interrupted_scan_id = interrupted.value.scan_id
        running = await GuardianRepository(postgres_engine).get_scan(interrupted_scan_id, interrupted_user.id)
        assert running is not None
        assert running.status == GuardianScanStatus.RUNNING.value
        assert running.phase == "build_contract_verdict"
        assert await asyncio.to_thread(_checkpoint_count, postgres_engine, interrupted_scan_id) > 0
        with pytest.raises(GuardianActiveScanError):
            await interrupted_service.create_scan(
                user_id=interrupted_user.id,
                base_spec=(FIXTURE_DIR / "baseline_openapi.yaml").read_bytes(),
                head_spec=(FIXTURE_DIR / "head_breaking_openapi.yaml").read_bytes(),
                base_extension=".yaml",
                head_extension=".yaml",
                target_base_url="http://127.0.0.1:8000",
            )
        await interrupted_service.close()

        resumed_service = _fake_service(postgres_engine, guardian_tmp_path / "resume-runs", database_url)
        try:
            resumed = await resumed_service.resume_scan(interrupted_scan_id, interrupted_user.id)
            baseline_report = await baseline_service.get_report(baseline_response.scan_id, baseline_user.id)
            resumed_report = await resumed_service.get_report(interrupted_scan_id, interrupted_user.id)
            return baseline_response, resumed, baseline_report, resumed_report
        finally:
            await resumed_service.close()
            await baseline_service.close()

    baseline_response, resumed, baseline_report, resumed_report = asyncio.run(exercise())
    assert resumed.status == GuardianScanStatus.COMPLETED.value
    assert resumed.final_verdict == baseline_response.final_verdict == GuardianVerdict.FAIL
    assert resumed_report.contract.changes == baseline_report.contract.changes
    assert resumed_report.scope == baseline_report.scope
    assert resumed_report.regression.results == baseline_report.regression.results
    assert resumed_report.final_verdict == baseline_report.final_verdict


def test_phase4_checkpoint_resumes_analysis_after_deterministic_verdict(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
) -> None:
    """确定性 core 后中断并重建 Service，Risk/Diagnosis 与 final verdict 均可恢复."""
    database_url = _test_database_url()
    baseline_user = _create_user(postgres_engine)
    resumed_user = _create_user(postgres_engine)
    analysis_policy = _policy().model_copy(update={"analysis_enabled": True})

    def build_service(path: Path, *, interrupt: bool = False) -> GuardianService:
        """构造带确定性 Mock LLM 的真实 PostgreSQL checkpoint Service."""
        return _fake_service(
            postgres_engine,
            path,
            database_url,
            policy=analysis_policy,
            risk_analyzer=RiskAnalyzer(FactDrivenLLM()),
            failure_diagnoser=FailureDiagnoser(FactDrivenLLM()),
            interrupt_after=["build_final_verdict"] if interrupt else None,
        )

    baseline_service = build_service(guardian_tmp_path / "phase4-baseline")
    interrupted_service = build_service(guardian_tmp_path / "phase4-resume", interrupt=True)

    async def exercise() -> tuple[
        GuardianScanResponse, GuardianScanResponse, GuardianReportResponse, GuardianReportResponse
    ]:
        baseline = await baseline_service.create_scan(
            user_id=baseline_user.id,
            base_spec=(FIXTURE_DIR / "baseline_openapi.yaml").read_bytes(),
            head_spec=(FIXTURE_DIR / "head_breaking_openapi.yaml").read_bytes(),
            base_extension=".yaml",
            head_extension=".yaml",
            target_base_url="http://127.0.0.1:8000",
        )
        with pytest.raises(GuardianCheckpointInterrupted) as interrupted:
            await interrupted_service.create_scan(
                user_id=resumed_user.id,
                base_spec=(FIXTURE_DIR / "baseline_openapi.yaml").read_bytes(),
                head_spec=(FIXTURE_DIR / "head_breaking_openapi.yaml").read_bytes(),
                base_extension=".yaml",
                head_extension=".yaml",
                target_base_url="http://127.0.0.1:8000",
            )
        scan_id = interrupted.value.scan_id
        assert interrupted.value.phase == "build_final_verdict"
        assert await asyncio.to_thread(_checkpoint_count, postgres_engine, scan_id) > 0
        await interrupted_service.close()
        restarted_service = build_service(guardian_tmp_path / "phase4-resume")
        try:
            resumed = await restarted_service.resume_scan(scan_id, resumed_user.id)
            baseline_report = await baseline_service.get_report(baseline.scan_id, baseline_user.id)
            resumed_report = await restarted_service.get_report(scan_id, resumed_user.id)
            return baseline, resumed, baseline_report, resumed_report
        finally:
            await restarted_service.close()
            await baseline_service.close()

    baseline, resumed, baseline_report, resumed_report = asyncio.run(exercise())
    assert resumed.final_verdict == baseline.final_verdict == GuardianVerdict.FAIL
    assert resumed.analysis_degraded is False
    assert resumed_report.analysis == baseline_report.analysis
    assert len(resumed_report.analysis.risk_explanations) == 1
    assert len(resumed_report.analysis.failure_diagnoses) == 1


def _free_port() -> int:
    """为真实本地 Schemathesis 集成选择 loopback 端口."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def _start_fixture_api() -> tuple[uvicorn.Server, Thread, str]:
    """启动只监听 loopback 的 runtime regression fixture."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(regression_fixture_app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("local fixture API failed to start")
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.mark.slow
def test_real_http_main_demo_contract_and_runtime_fail(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 主 Demo 真实调用 oasdiff 1.28.0 与 Schemathesis 4.24.3."""
    oasdiff_path = os.getenv("GUARDIAN_TEST_OASDIFF_PATH") or shutil.which("oasdiff")
    schemathesis_path = shutil.which("st")
    if not oasdiff_path or not schemathesis_path:
        pytest.skip("verified oasdiff and Schemathesis CLIs are required")
    user = _create_user(postgres_engine)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "phase3-real-http-jwt-key")
    monkeypatch.setattr(auth_module, "db_service", _ThreadedAuthDatabase(postgres_engine))
    policy = _policy()
    service = GuardianService(
        repository=GuardianRepository(postgres_engine),
        policy=policy,
        runs_dir=guardian_tmp_path / "runs",
        checkpoint_database_url=_test_database_url(),
        oasdiff=OasdiffAdapter(executable=oasdiff_path),
        schemathesis=SchemathesisAdapter(executable=schemathesis_path),
    )
    application = _test_app(service)
    server, thread, target_url = _start_fixture_api()

    async def exercise() -> tuple[httpx.Response, GuardianScanDetailResponse, GuardianReportResponse]:
        try:
            response = await _post_scan(application, user, target_url)
            scan_id = UUID(response.json()["scan_id"])
            detail = await service.get_scan(scan_id, user.id)
            report = await service.get_report(scan_id, user.id)
            return response, detail, report
        finally:
            await service.close()

    try:
        response, detail, report = asyncio.run(exercise())
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert response.json()["contract_verdict"] == "fail", report.model_dump_json()
        assert response.json()["regression_verdict"] == "fail", report.model_dump_json()
        assert response.json()["final_verdict"] == "fail", report.model_dump_json()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.slow
@pytest.mark.skipif(
    not (
        settings.GUARDIAN_LLM_ENABLED
        and settings.OPENAI_API_KEY
        and "deepseek" in settings.DEFAULT_LLM_MODEL.casefold()
    ),
    reason="real DeepSeek E2E requires local OpenAI-compatible Guardian LLM configuration",
)
def test_real_deepseek_full_http_main_demo(
    postgres_engine: Engine,
    guardian_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 oasdiff、Schemathesis、PostgreSQL、LangGraph 与 DeepSeek 完整 E2E."""
    oasdiff_path = os.getenv("GUARDIAN_TEST_OASDIFF_PATH") or shutil.which("oasdiff")
    schemathesis_path = shutil.which("st")
    if not oasdiff_path or not schemathesis_path:
        pytest.skip("verified oasdiff and Schemathesis CLIs are required")
    user = _create_user(postgres_engine)
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "phase4-real-deepseek-jwt-key")
    monkeypatch.setattr(auth_module, "db_service", _ThreadedAuthDatabase(postgres_engine))
    policy = _policy().model_copy(update={"analysis_enabled": True})
    service = GuardianService(
        repository=GuardianRepository(postgres_engine),
        policy=policy,
        runs_dir=guardian_tmp_path / "deepseek-runs",
        checkpoint_database_url=_test_database_url(),
        oasdiff=OasdiffAdapter(executable=oasdiff_path),
        schemathesis=SchemathesisAdapter(executable=schemathesis_path),
    )
    application = _test_app(service)
    server, thread, target_url = _start_fixture_api()

    async def exercise() -> tuple[httpx.Response, GuardianReportResponse]:
        try:
            response = await _post_scan(application, user, target_url)
            report = await service.get_report(UUID(response.json()["scan_id"]), user.id)
            return response, report
        finally:
            await service.close()

    try:
        response, report = asyncio.run(exercise())
        assert response.status_code == 200
        assert response.json()["contract_verdict"] == GuardianVerdict.FAIL.value
        assert response.json()["regression_verdict"] == GuardianVerdict.FAIL.value
        assert response.json()["final_verdict"] == GuardianVerdict.FAIL.value
        assert response.json()["analysis_degraded"] is False
        assert len(report.analysis.risk_explanations) == 1
        assert len(report.analysis.failure_diagnoses) == 1
        assert report.analysis.failure_diagnoses[0].category.value == "contract_mismatch"
        serialized = report.analysis.model_dump_json().casefold()
        for forbidden in (".py", "line 83", "users table", "users 表", "stack trace", "git commit"):
            assert forbidden not in serialized
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_guardian_graph_factory_still_supports_cli_without_checkpointer() -> None:
    """Phase 3 的 checkpointer 注入不能让 Core/CLI 强制依赖 PostgreSQL."""
    graph = create_guardian_graph(policy=_policy())
    assert graph.checkpointer is None
