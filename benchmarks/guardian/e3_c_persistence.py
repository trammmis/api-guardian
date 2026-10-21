"""E3-C 真实 runtime case 的 PostgreSQL 业务持久化与 checkpoint 验证."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlmodel import Session

from app.models.user import User
from app.services.guardian.models import GuardianPolicy
from app.services.guardian.oasdiff import OasdiffAdapter
from app.services.guardian.repository import GuardianRepository
from app.services.guardian.schemathesis import SchemathesisAdapter
from app.services.guardian.service import GuardianService
from benchmarks.guardian.config import PROJECT_ROOT
from benchmarks.guardian.e3_c_runner import (
    CASES,
    OASDIFF_PATH,
    POSTGRES_IMAGE,
    REPORT_ROOT_E3_C,
    SCHEMATHESIS_PATH,
    HistoricalService,
    _core_hashes,
    _free_port,
)


def _start_database(container_name: str, port: int) -> None:
    """启动仅用于 E3-C correctness run 的业务/checkpoint PostgreSQL."""
    result = subprocess.run(  # noqa: S603
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
            "-e",
            "POSTGRES_PASSWORD=e3c_persistence_local_only",
            "-p",
            f"127.0.0.1:{port}:5432",
            POSTGRES_IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("E3-C persistence PostgreSQL failed to start")
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        health = subprocess.run(  # noqa: S603
            ["docker", "exec", container_name, "pg_isready", "-U", "postgres"],
            capture_output=True,
            text=True,
            check=False,
        )
        if health.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("E3-C persistence PostgreSQL health timeout")


def _run_migration(port: int) -> None:
    """使用正式 Alembic migration 创建 Guardian 业务表."""
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": str(port),
            "POSTGRES_DB": "postgres",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": "e3c_persistence_local_only",
        }
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("E3-C persistence Alembic migration failed")


async def _exercise(
    database_url: str,
    checkpoint_database_url: str,
    service_target: str,
    workspace: Path,
) -> dict[str, object]:
    """通过真实 GuardianService 保存 scan/report/checkpoint 并查回."""
    engine = create_engine(database_url, pool_pre_ping=True)
    with Session(engine, expire_on_commit=False) as session:
        user = User(
            email=f"guardian-e3c-{uuid4().hex}@example.test",
            hashed_password=User.hash_password("e3c-local-test-password"),
            username="guardian-e3c",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id

    repository = GuardianRepository(engine)
    policy = GuardianPolicy(
        allowed_target_hosts=("127.0.0.1",),
        allow_private_targets=True,
        subprocess_timeout_seconds=30.0,
        regression_request_timeout_seconds=2.0,
        regression_max_examples=1,
        analysis_enabled=False,
    )
    service = GuardianService(
        repository=repository,
        policy=policy,
        runs_dir=workspace,
        checkpoint_database_url=checkpoint_database_url,
        oasdiff=OasdiffAdapter(executable=str(OASDIFF_PATH), timeout_seconds=30.0),
        schemathesis=SchemathesisAdapter(executable=str(SCHEMATHESIS_PATH), timeout_seconds=30.0),
    )
    case = CASES[0]
    try:
        response = await service.create_scan(
            user_id=user_id,
            base_spec=case.base_spec.read_bytes(),
            head_spec=case.head_spec.read_bytes(),
            base_extension=".yaml",
            head_extension=".yaml",
            target_base_url=service_target,
        )
        detail = await service.get_scan(response.scan_id, user_id)
        report = await service.get_report(response.scan_id, user_id)
        report_json = json.dumps(report.model_dump(mode="json"), sort_keys=True).lower()
        forbidden = (
            "authorization",
            "cookie",
            "raw_ndjson",
            "request_body",
            "response_body",
            str(workspace.resolve()).lower(),
        )
        leakage = [value for value in forbidden if value in report_json]
        with engine.connect() as connection:
            checkpoint_count = int(
                connection.execute(
                    text("SELECT count(*) FROM checkpoints WHERE thread_id = :thread_id"),
                    {"thread_id": f"guardian:{response.scan_id}"},
                ).scalar_one()
            )
            scan_count = int(
                connection.execute(
                    text("SELECT count(*) FROM guardian_scan WHERE id = :scan_id"),
                    {"scan_id": response.scan_id},
                ).scalar_one()
            )
            report_count = int(
                connection.execute(
                    text("SELECT count(*) FROM guardian_report_record WHERE scan_id = :scan_id"),
                    {"scan_id": response.scan_id},
                ).scalar_one()
            )
        return {
            "case_id": case.case_id,
            "scan_id": str(response.scan_id),
            "status": response.status,
            "contract_verdict": response.contract_verdict.value if response.contract_verdict else None,
            "regression_verdict": response.regression_verdict.value if response.regression_verdict else None,
            "final_verdict": response.final_verdict.value if response.final_verdict else None,
            "detail_status": detail.status,
            "scan_rows": scan_count,
            "report_rows": report_count,
            "checkpoint_rows": checkpoint_count,
            "analysis_degraded": response.analysis_degraded,
            "secret_or_raw_artifact_leakage": leakage,
            "passed": (
                response.status == "completed"
                and scan_count == 1
                and report_count == 1
                and checkpoint_count > 0
                and not leakage
            ),
        }
    finally:
        await service.close()
        engine.dispose()


def main() -> None:
    """运行一次不计入性能统计的真实持久化验证."""
    REPORT_ROOT_E3_C.mkdir(parents=True, exist_ok=True)
    core_before = _core_hashes()
    database_port = _free_port()
    token = uuid4().hex[:8]
    database_container = f"guardian-e3c-persistence-{token}"
    sut = HistoricalService(CASES[0], f"persist-{token}")
    workspace = REPORT_ROOT_E3_C / "_persistence_workspace"
    shutil.rmtree(workspace, ignore_errors=True)
    database_url = f"postgresql+psycopg://postgres:e3c_persistence_local_only@127.0.0.1:{database_port}/postgres"
    checkpoint_database_url = f"postgresql://postgres:e3c_persistence_local_only@127.0.0.1:{database_port}/postgres"
    try:
        _start_database(database_container, database_port)
        _run_migration(database_port)
        sut.start()
        result = asyncio.run(_exercise(database_url, checkpoint_database_url, sut.base_url, workspace))
        result["production_core_unchanged"] = core_before == _core_hashes()
        output = REPORT_ROOT_E3_C / "persistence_checkpoint_result.json"
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["passed"] or not result["production_core_unchanged"]:
            raise SystemExit(1)
    finally:
        sut.stop()
        subprocess.run(  # noqa: S603
            ["docker", "stop", "--time", "3", database_container],
            capture_output=True,
            text=True,
            check=False,
        )
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
