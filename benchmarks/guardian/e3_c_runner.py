"""E3-C 两个冻结 Supabase 历史 Pair 的真实 Runtime 验证编排器."""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import platform
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import IO, Any, Final
from uuid import uuid4

from rich.console import Console

from app.services.guardian.errors import GuardianException
from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianPolicy, GuardianVerdict, OperationRef
from app.services.guardian.oasdiff import OASDIFF_VERSION, OasdiffAdapter, normalize_contract_changes
from app.services.guardian.schemathesis import (
    SCHEMATHESIS_CHECK,
    SCHEMATHESIS_MAX_REDIRECTS,
    SCHEMATHESIS_PHASES,
    SCHEMATHESIS_VERSION,
    SchemathesisAdapter,
    determine_final_verdict,
    determine_regression_verdict,
)
from app.services.guardian.scope import determine_contract_verdict
from app.services.guardian.security import prepare_openapi_spec
from app.services.guardian.subprocess import ControlledSubprocessRunner
from benchmarks.guardian.baselines.full_cli import _environment, _parse_full_report
from benchmarks.guardian.config import PROJECT_ROOT, REPORT_ROOT

REPORT_ROOT_E3_C: Final = REPORT_ROOT / "e3_c"
WORKTREE_ROOT: Final = PROJECT_ROOT / "benchmarks" / "guardian" / "worktrees" / "e3_c0"
OASDIFF_PATH: Final = PROJECT_ROOT / "benchmarks" / "guardian" / ".tools" / "oasdiff" / "1.28.0" / "oasdiff.exe"
SCHEMATHESIS_PATH: Final = PROJECT_ROOT / ".venv" / "Scripts" / "st.exe"
POSTGRES_IMAGE: Final = "postgres:16"
SYSTEMS: Final = ("full_schemathesis", "oasdiff_plus_full", "oracle_scope", "api_guardian")
CORE_PATHS: Final = (
    "app/services/guardian/oasdiff.py",
    "app/services/guardian/scope.py",
    "app/services/guardian/schemathesis.py",
    "app/services/guardian/nodes.py",
    "app/services/guardian/graph.py",
    "app/services/guardian/models.py",
    "app/services/guardian/security.py",
    "app/services/guardian/errors.py",
    "app/services/guardian/state.py",
)


@dataclass(frozen=True)
class RuntimeCase:
    """一个已通过 E3-C0 的不可变 Runtime Case."""

    case_id: str
    base_commit: str
    head_commit: str
    base_spec: Path
    head_spec: Path
    base_sha256: str
    head_sha256: str
    binary: Path
    binary_sha256: str
    affected_operation: str


CASES: Final = (
    RuntimeCase(
        case_id="rw_supabase_005_authorize_flow_type_removed",
        base_commit="19545601676675a923bb9850499717e89cf91a0b",
        head_commit="f4c3cdb020e6808333ec460470f9e081f0d9bc93",
        base_spec=WORKTREE_ROOT
        / "supabase-auth-f4c3cdb"
        / "base_openapi_19545601676675a923bb9850499717e89cf91a0b.yaml",
        head_spec=WORKTREE_ROOT
        / "supabase-auth-f4c3cdb"
        / "auth-f4c3cdb020e6808333ec460470f9e081f0d9bc93"
        / "openapi.yaml",
        base_sha256="c93e8e4ad65515e50b3babfc99cc07d5694249420e04858a64120c268a9238f4",
        head_sha256="2aee71c180222191e49fb3df2edea8820c0bb49ab1909f20fe9b408cc731e6b6",
        binary=WORKTREE_ROOT
        / "supabase-auth-f4c3cdb"
        / "auth-f4c3cdb020e6808333ec460470f9e081f0d9bc93"
        / "gotrue-e3c0.exe",
        binary_sha256="920a366640cd742a0743f5423e16f79403bc8a3c3cc5a4f6decc7c38ce5fe4a7",
        affected_operation="GET /authorize",
    ),
    RuntimeCase(
        case_id="runtime_supabase_006_id_token_request_fields_removed",
        base_commit="80fd43270fdbcb79e7a2e0b5bce484bd29d9ab49",
        head_commit="264696734fc922c8bbf563d44fcc59b26e7e6537",
        base_spec=WORKTREE_ROOT / "supabase_candidate_002" / "base_openapi.yaml",
        head_spec=WORKTREE_ROOT / "supabase_candidate_002" / "head_openapi.yaml",
        base_sha256="144a34ca6d74b3e50f7adedd46e94688e45755e8d518beb6be723587dc4a1e3f",
        head_sha256="42a6d0897c39a510aba491349bfb92b47b2182be10080e073dea6ef68f732946",
        binary=WORKTREE_ROOT / "supabase_candidate_002" / "gotrue-e3c0.exe",
        binary_sha256="9c3c39be1c0e4ac0974004e3813cb2a532d31178d6e2781c6ef33da5826fea71",
        affected_operation="POST /token",
    ),
)


class RequestCounter:
    """消费服务日志但只累计 request-start 数，不保存原始日志."""

    def __init__(self, stream: IO[str]) -> None:
        """初始化只计数不留存原始日志的消费器."""
        self._stream = stream
        self._count = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._consume, daemon=True)

    def start(self) -> None:
        """启动只计数的日志消费线程."""
        self._thread.start()

    def count(self) -> int:
        """返回已观察的 SUT 请求数."""
        with self._lock:
            return self._count

    def _consume(self) -> None:
        for line in self._stream:
            if '"msg":"request started"' in line or '"msg": "request started"' in line:
                with self._lock:
                    self._count += 1


class HistoricalService:
    """每次测量使用一个 fresh PostgreSQL 和 exact-commit Go 服务."""

    def __init__(self, case: RuntimeCase, run_token: str) -> None:
        """为单次测量分配独立端口与容器名."""
        self.case = case
        self.run_token = run_token
        self.database_port = _free_port()
        self.service_port = _free_port()
        self.container_name = f"guardian-e3c-{run_token}"[:63]
        self.process: subprocess.Popen[str] | None = None
        self.counter: RequestCounter | None = None
        self.environment_setup_seconds = 0.0
        self.service_startup_seconds = 0.0
        self.reset_seconds = 0.0

    @property
    def base_url(self) -> str:
        """返回只绑定 loopback 的目标 URL."""
        return f"http://127.0.0.1:{self.service_port}"

    def start(self) -> None:
        """启动 fresh DB 与 exact Head 服务并等待 health."""
        database_started = time.perf_counter()
        result = subprocess.run(  # noqa: S603
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                self.container_name,
                "-e",
                "POSTGRES_PASSWORD=e3c_local_only",
                "-p",
                f"127.0.0.1:{self.database_port}:5432",
                POSTGRES_IMAGE,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("SYSTEM_UNDER_TEST_SETUP_ERROR: PostgreSQL container start failed")
        _wait_postgres(self.container_name)
        self.environment_setup_seconds = time.perf_counter() - database_started

        service_started = time.perf_counter()
        environment = os.environ.copy()
        environment.update(
            {
                "GOTRUE_JWT_SECRET": "e3c-local-only-value",
                "GOTRUE_JWT_EXP": "3600",
                "GOTRUE_JWT_AUD": "authenticated",
                "GOTRUE_DB_DRIVER": "postgres",
                "DATABASE_URL": (
                    f"postgres://postgres:e3c_local_only@127.0.0.1:{self.database_port}/postgres?sslmode=disable"
                ),
                "API_EXTERNAL_URL": self.base_url,
                "GOTRUE_API_HOST": "127.0.0.1",
                "PORT": str(self.service_port),
                "GOTRUE_SITE_URL": self.base_url,
                "GOTRUE_MAILER_AUTOCONFIRM": "true",
            }
        )
        self.process = subprocess.Popen(  # noqa: S603
            [str(self.case.binary), "serve"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if self.process.stdout is None:
            raise RuntimeError("SYSTEM_UNDER_TEST_SETUP_ERROR: service log stream unavailable")
        self.counter = RequestCounter(self.process.stdout)
        self.counter.start()
        _wait_health(f"{self.base_url}/health", self.process)
        self.service_startup_seconds = time.perf_counter() - service_started

    def request_count(self) -> int:
        """读取当前累计请求数."""
        if self.counter is None:
            return 0
        return self.counter.count()

    def stop(self) -> None:
        """停止本阶段创建的服务与精确命名容器."""
        started = time.perf_counter()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        subprocess.run(  # noqa: S603
            ["docker", "stop", "--time", "3", self.container_name],
            capture_output=True,
            text=True,
            check=False,
        )
        self.reset_seconds = time.perf_counter() - started


class E3CRunner:
    """运行 Full、oasdiff+Full 与生产 Guardian 的公平对照."""

    def __init__(self, *, repetitions: int, warmups: int, report_root: Path = REPORT_ROOT_E3_C) -> None:
        """初始化冻结工具、公平参数与生产 Graph."""
        self.repetitions = repetitions
        self.warmups = warmups
        self.report_root = report_root.resolve()
        self.console = Console()
        self.runner = ControlledSubprocessRunner()
        self.policy = GuardianPolicy(
            allowed_target_hosts=("127.0.0.1",),
            allow_private_targets=True,
            subprocess_timeout_seconds=30.0,
            regression_request_timeout_seconds=2.0,
            regression_max_examples=1,
            analysis_enabled=False,
        )
        self.oasdiff = OasdiffAdapter(executable=str(OASDIFF_PATH), timeout_seconds=30.0)
        self.schemathesis = SchemathesisAdapter(executable=str(SCHEMATHESIS_PATH), timeout_seconds=30.0)
        self.graph = create_guardian_graph(
            policy=self.policy,
            oasdiff=self.oasdiff,
            schemathesis=self.schemathesis,
        )

    async def run(self) -> dict[str, Any]:
        """执行所有 warm-up/measured runs 并保存可审计原始数据."""
        _assert_tools()
        core_before = _core_hashes()
        _assert_alignments()
        self.report_root.mkdir(parents=True, exist_ok=True)
        raw_path = self.report_root / "raw_runs.jsonl"
        raw_path.write_text("", encoding="utf-8")
        started_at = datetime.now(UTC)
        run_id = f"e3-c-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        records: list[dict[str, Any]] = []

        for case_index, case in enumerate(CASES):
            total_rounds = self.warmups + self.repetitions
            for round_index in range(total_rounds):
                warmup = round_index < self.warmups
                repetition = round_index if warmup else round_index - self.warmups
                systems = _rotated_systems(case_index + round_index)
                for system in systems:
                    record = await self._measure(run_id, case, system, repetition, warmup)
                    records.append(record)
                    _append_jsonl(raw_path, record)
                    self.console.print(
                        "[cyan]E3-C[/cyan]",
                        case.case_id,
                        system,
                        "warmup" if warmup else f"measured-{repetition}",
                        record["regression_verdict"],
                        f"{record['wall_clock_seconds']:.3f}s",
                    )

        core_after = _core_hashes()
        if core_before != core_after:
            raise RuntimeError("production Guardian Core changed during E3-C")
        summary = _summarize(records)
        completed_at = datetime.now(UTC)
        manifest = _build_manifest(run_id, started_at, completed_at, core_before, core_after, self)
        _write_json(self.report_root / "experiment_manifest.json", manifest)
        _write_json(self.report_root / "environment.json", _environment_record())
        _write_json(self.report_root / "summary.json", summary)
        _write_case_csv(self.report_root / "case_results.csv", summary)
        return summary

    async def _measure(
        self,
        run_id: str,
        case: RuntimeCase,
        system: str,
        repetition: int,
        warmup: bool,
    ) -> dict[str, Any]:
        """用 fresh SUT 环境测量一次系统执行."""
        token = f"{case.case_id[-3:]}-{system[:3]}-{int(warmup)}-{repetition}-{uuid4().hex[:6]}"
        service = HistoricalService(case, token)
        setup_error: str | None = None
        result: dict[str, Any]
        try:
            await asyncio.to_thread(service.start)
            request_before = service.request_count()
            started = time.perf_counter()
            result = await self._execute(case, system, service.base_url)
            duration = time.perf_counter() - started
            await asyncio.sleep(0.25)
            requests = max(0, service.request_count() - request_before)
        except Exception as exc:
            duration = 0.0
            requests = 0
            setup_error = _classify_exception(exc)
            result = _error_result(system, setup_error)
        finally:
            await asyncio.to_thread(service.stop)

        return {
            "run_id": run_id,
            "case_id": case.case_id,
            "repo": "supabase/auth",
            "base_commit": case.base_commit,
            "head_commit": case.head_commit,
            "head_spec_sha256": case.head_sha256,
            "system": system,
            "warmup": warmup,
            "repetition": repetition,
            "measured_at": datetime.now(UTC).isoformat(),
            "alignment_valid": True,
            "affected_operation": case.affected_operation,
            "environment_setup_seconds": service.environment_setup_seconds,
            "service_startup_seconds": service.service_startup_seconds,
            "reset_seconds": service.reset_seconds,
            "wall_clock_seconds": duration,
            "http_requests": requests,
            "setup_error": setup_error,
            **result,
        }

    async def _execute(self, case: RuntimeCase, system: str, target: str) -> dict[str, Any]:
        operations = prepare_openapi_spec(str(case.head_spec), 5 * 1024 * 1024).operations
        if system == "full_schemathesis":
            return await self._run_full(case, target, operations, None)
        if system == "oasdiff_plus_full":
            contract_started = time.perf_counter()
            diff = await self.oasdiff.compare(str(case.base_spec), str(case.head_spec), 100 * 1024 * 1024)
            changes = normalize_contract_changes(diff.candidates)
            contract = determine_contract_verdict(changes, has_error=False)
            contract_duration = time.perf_counter() - contract_started
            full = await self._run_full(case, target, operations, contract)
            full["oasdiff_duration_seconds"] = contract_duration
            full["oasdiff_exit_code"] = diff.tool.exit_code
            return full
        if system == "oracle_scope":
            operation = _affected_operation(operations, case.affected_operation)
            started = time.perf_counter()
            execution = await self.schemathesis.run_regression(
                str(case.head_spec),
                target,
                (operation,),
                100 * 1024 * 1024,
            )
            schemathesis_duration = time.perf_counter() - started
            verdict = determine_regression_verdict(execution.results, (operation,))
            failures = [
                {
                    "operation": failure.operation.key if failure.operation else None,
                    "check_name": failure.check_name,
                    "category": failure.category,
                    "status_code": failure.status_code,
                    "phase": failure.phase,
                }
                for result in execution.results
                for failure in result.failures
            ]
            return {
                "contract_verdict": None,
                "regression_verdict": verdict.value,
                "final_verdict": verdict.value,
                "selected_operations": [operation.key],
                "fallback_to_all": False,
                "operations_tested": execution.tool.total_operations,
                "regression_pass": execution.tool.passed_operations,
                "regression_fail": execution.tool.failed_operations,
                "regression_error": execution.tool.error_operations,
                "schemathesis_exit_code": execution.tool.exit_code,
                "oasdiff_exit_code": None,
                "oasdiff_duration_seconds": None,
                "schemathesis_duration_seconds": schemathesis_duration,
                "safe_failure_evidence": failures[:20],
                "error_codes": [],
                "completed_nodes": [],
            }
        if system == "api_guardian":
            state: dict[str, Any] = await self.graph.ainvoke(
                {
                    "scan_id": f"e3-c-{case.case_id}-{uuid4().hex}",
                    "base_spec_path": str(case.base_spec),
                    "head_spec_path": str(case.head_spec),
                    "target_base_url": target,
                }
            )
            scope = state.get("selected_scope")
            regression_tool = state.get("regression_tool")
            errors = state.get("errors", ())
            safe_failures = []
            for item in state.get("regression_results", ()):
                for failure in item.failures:
                    safe_failures.append(
                        {
                            "operation": failure.operation.key if failure.operation else None,
                            "check_name": failure.check_name,
                            "category": failure.category,
                            "status_code": failure.status_code,
                            "phase": failure.phase,
                        }
                    )
            contract_tool = state.get("contract_tool")
            return {
                "contract_verdict": _verdict_value(state.get("contract_verdict")),
                "regression_verdict": _verdict_value(state.get("regression_verdict")),
                "final_verdict": _verdict_value(state.get("final_verdict")),
                "selected_operations": [operation.key for operation in scope.operations] if scope else [],
                "fallback_to_all": bool(scope.fallback_to_all) if scope else False,
                "operations_tested": regression_tool.total_operations if regression_tool else 0,
                "regression_pass": regression_tool.passed_operations if regression_tool else 0,
                "regression_fail": regression_tool.failed_operations if regression_tool else 0,
                "regression_error": regression_tool.error_operations if regression_tool else 0,
                "schemathesis_exit_code": regression_tool.exit_code if regression_tool else None,
                "oasdiff_exit_code": contract_tool.exit_code if contract_tool else None,
                "oasdiff_duration_seconds": None,
                "schemathesis_duration_seconds": None,
                "safe_failure_evidence": safe_failures[:20],
                "error_codes": [error.code.value for error in errors],
                "completed_nodes": list(state.get("completed_nodes", ())),
            }
        raise ValueError(f"unsupported system: {system}")

    async def _run_full(
        self,
        case: RuntimeCase,
        target: str,
        operations: tuple[OperationRef, ...],
        contract_verdict: GuardianVerdict | None,
    ) -> dict[str, Any]:
        """一次无 filter CLI 覆盖完整 Head Spec，并立即丢弃 raw NDJSON."""
        with TemporaryDirectory(prefix="guardian-e3-c-full-") as directory:
            report = Path(directory) / "report.ndjson"
            report.touch(exist_ok=False)
            command = (
                str(SCHEMATHESIS_PATH),
                "run",
                str(case.head_spec.resolve()),
                "--url",
                target,
                "--checks",
                SCHEMATHESIS_CHECK,
                "--phases",
                SCHEMATHESIS_PHASES,
                "--max-examples",
                "1",
                "--generation-deterministic",
                "--max-redirects",
                SCHEMATHESIS_MAX_REDIRECTS,
                "--request-timeout",
                "2.0",
                "--report",
                "ndjson",
                "--report-ndjson-path",
                str(report),
                "--output-sanitize",
                "true",
                "--no-color",
            )
            started = time.perf_counter()
            process = await self.runner.run(
                command,
                timeout_seconds=30.0,
                max_stdout_bytes=50 * 1024 * 1024,
                max_stderr_bytes=50 * 1024 * 1024,
                max_total_bytes=100 * 1024 * 1024,
                cwd=Path(directory),
                env=_environment(),
                monitored_artifact_path=Path(directory),
                max_monitored_artifact_bytes=50 * 1024 * 1024,
            )
            schemathesis_duration = time.perf_counter() - started
            if process.exit_code not in {0, 1} or not report.is_file() or report.stat().st_size == 0:
                raise RuntimeError("SCHEMATHESIS_ERROR: full report unavailable")
            payload = report.read_bytes()
            regression = _parse_full_report(payload, len(operations), process.exit_code)
            observation = _full_safe_observation(payload)
        final = (
            regression
            if contract_verdict is None
            else determine_final_verdict(
                contract_verdict,
                regression,
                has_error=False,
            )
        )
        return {
            "contract_verdict": _verdict_value(contract_verdict),
            "regression_verdict": regression.value,
            "final_verdict": final.value,
            "selected_operations": [operation.key for operation in operations],
            "fallback_to_all": False,
            "operations_tested": observation["observed_operations"],
            "regression_pass": int(regression == GuardianVerdict.PASS),
            "regression_fail": int(regression == GuardianVerdict.FAIL),
            "regression_error": int(regression == GuardianVerdict.ERROR),
            "schemathesis_exit_code": process.exit_code,
            "oasdiff_exit_code": None,
            "oasdiff_duration_seconds": None,
            "schemathesis_duration_seconds": schemathesis_duration,
            "safe_failure_evidence": observation["evidence"],
            "error_codes": [] if regression != GuardianVerdict.ERROR else ["SCHEMATHESIS_ERROR"],
            "completed_nodes": [],
        }


def _full_safe_observation(payload: bytes) -> dict[str, Any]:
    """从 Full NDJSON 仅提取 operation/check/error 类型，拒绝 body/header."""
    evidence: list[dict[str, Any]] = []
    observed_operations: set[str] = set()
    for line in payload.splitlines():
        event = json.loads(line.decode("utf-8"))
        if not isinstance(event, dict):
            continue
        if "NonFatalError" in event:
            value = event["NonFatalError"]
            if isinstance(value, dict):
                detail = value.get("value")
                category = detail.get("type") if isinstance(detail, dict) else None
                evidence.append(
                    {
                        "operation": None,
                        "check_name": "schemathesis_execution",
                        "category": _safe_category(category),
                        "phase": _safe_category(value.get("phase")),
                    }
                )
                if len(evidence) >= 20:
                    return {"observed_operations": len(observed_operations), "evidence": evidence}
            continue
        if "ScenarioFinished" not in event:
            continue
        value = event["ScenarioFinished"]
        if not isinstance(value, dict):
            continue
        recorder = value.get("recorder")
        if not isinstance(recorder, dict):
            continue
        label = recorder.get("label") if isinstance(recorder.get("label"), str) else None
        if label:
            observed_operations.add(label)
        if value.get("status") == "error":
            evidence.append(
                {
                    "operation": label,
                    "check_name": "schemathesis_execution",
                    "category": "scenario_error",
                    "phase": _safe_category(value.get("phase")),
                }
            )
        checks = recorder.get("checks")
        if not isinstance(checks, dict):
            continue
        for group in checks.values():
            if not isinstance(group, list):
                continue
            for check in group:
                if (
                    isinstance(check, dict)
                    and check.get("name") == SCHEMATHESIS_CHECK
                    and check.get("status") == "failure"
                ):
                    evidence.append(
                        {
                            "operation": label,
                            "check_name": SCHEMATHESIS_CHECK,
                            "status": "failure",
                        }
                    )
                    if len(evidence) >= 20:
                        return {"observed_operations": len(observed_operations), "evidence": evidence}
    return {"observed_operations": len(observed_operations), "evidence": evidence[:20]}


def _safe_category(value: Any) -> str | None:
    """仅保留短小的结构化类型名，不保留动态错误文本."""
    if not isinstance(value, str):
        return None
    safe = "".join(character for character in value if character.isalnum() or character in "._-")
    return safe[:80] or None


def _affected_operation(operations: tuple[OperationRef, ...], expected_key: str) -> OperationRef:
    """从 Head Spec 精确定位冻结 Oracle operation."""
    matches = [operation for operation in operations if operation.key == expected_key]
    if len(matches) != 1:
        raise RuntimeError(f"INVALID_ALIGNMENT: expected exactly one {expected_key}")
    return matches[0]


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [record for record in records if not record["warmup"]]
    by_case: dict[str, Any] = {}
    for case in CASES:
        systems: dict[str, Any] = {}
        for system in SYSTEMS:
            subset = [r for r in measured if r["case_id"] == case.case_id and r["system"] == system]
            durations = [float(r["wall_clock_seconds"]) for r in subset]
            requests = [int(r["http_requests"]) for r in subset]
            selected = [tuple(r["selected_operations"]) for r in subset]
            verdicts = [(r["contract_verdict"], r["regression_verdict"], r["final_verdict"]) for r in subset]
            systems[system] = {
                "measured_runs": len(subset),
                "completed_runs": sum(not r["setup_error"] for r in subset),
                "non_error_verdict_runs": sum(
                    not r["setup_error"] and r["regression_verdict"] != "error" for r in subset
                ),
                "median_runtime_seconds": _median(durations),
                "p95_runtime_seconds": _percentile(durations, 0.95),
                "median_http_requests": _median(requests),
                "operations_tested": sorted({r["operations_tested"] for r in subset}),
                "selected_operations": sorted({"|".join(value) for value in selected}),
                "verdict_tuples": sorted(
                    {"/".join("n/a" if v is None else str(v) for v in value) for value in verdicts}
                ),
                "deterministic_agreement": len(set(selected)) == 1 and len(set(verdicts)) == 1,
                "error_runs": sum(bool(r["setup_error"] or r["error_codes"]) for r in subset),
            }
        full = systems["full_schemathesis"]
        guardian = systems["api_guardian"]
        full_ops = max(full["operations_tested"], default=0)
        guardian_ops = max(guardian["operations_tested"], default=0)
        by_case[case.case_id] = {
            "affected_operation": case.affected_operation,
            "systems": systems,
            "operations_reduction": 1 - guardian_ops / full_ops if full_ops else None,
            "http_request_reduction": (
                1 - guardian["median_http_requests"] / full["median_http_requests"]
                if full["median_http_requests"]
                else None
            ),
            "regression_runtime_reduction": (
                1 - guardian["median_runtime_seconds"] / full["median_runtime_seconds"]
                if full["median_runtime_seconds"]
                else None
            ),
            "end_to_end_runtime_difference_seconds": (
                guardian["median_runtime_seconds"] - full["median_runtime_seconds"]
            ),
        }
    guardian_runs = [r for r in measured if r["system"] == "api_guardian"]
    return {
        "stage": "E3-C",
        "conclusion_label": None,
        "raw_run_count": len(records),
        "warmup_run_count": sum(r["warmup"] for r in records),
        "measured_run_count": len(measured),
        "cases": by_case,
        "metrics": {
            "end_to_end_execution_success_rate": _metric(
                sum(not r["setup_error"] and not r["error_codes"] for r in guardian_runs),
                len(guardian_runs),
            ),
            "final_verdict_reproducibility": _metric(
                sum(value["systems"]["api_guardian"]["deterministic_agreement"] for value in by_case.values()),
                len(by_case),
            ),
            "regression_verdict_agreement": _metric(
                sum(_case_regression_agreement(measured, case.case_id) for case in CASES),
                len(CASES),
            ),
            "scope_recall": _metric(
                sum(case.affected_operation in set(_guardian_selected(measured, case.case_id)) for case in CASES),
                len(CASES),
            ),
            "scope_precision": _metric(
                sum(case.affected_operation in set(_guardian_selected(measured, case.case_id)) for case in CASES),
                sum(len(_guardian_selected(measured, case.case_id)) for case in CASES),
            ),
        },
    }


def _guardian_selected(records: list[dict[str, Any]], case_id: str) -> list[str]:
    for record in records:
        if record["case_id"] == case_id and record["system"] == "api_guardian":
            return list(record["selected_operations"])
    return []


def _case_regression_agreement(records: list[dict[str, Any]], case_id: str) -> bool:
    """检查生产 Guardian 与冻结 Oracle scope 的 runtime verdict 是否一致."""
    guardian = {
        record["regression_verdict"]
        for record in records
        if record["case_id"] == case_id and record["system"] == "api_guardian"
    }
    oracle = {
        record["regression_verdict"]
        for record in records
        if record["case_id"] == case_id and record["system"] == "oracle_scope"
    }
    return len(guardian) == 1 and guardian == oracle


def _error_result(system: str, error: str) -> dict[str, Any]:
    return {
        "contract_verdict": None if system == "full_schemathesis" else "error",
        "regression_verdict": "error",
        "final_verdict": "error",
        "selected_operations": [],
        "fallback_to_all": False,
        "operations_tested": 0,
        "regression_pass": 0,
        "regression_fail": 0,
        "regression_error": 1,
        "schemathesis_exit_code": None,
        "oasdiff_exit_code": None,
        "oasdiff_duration_seconds": None,
        "schemathesis_duration_seconds": None,
        "safe_failure_evidence": [],
        "error_codes": [error],
        "completed_nodes": [],
    }


def _classify_exception(exc: Exception) -> str:
    if isinstance(exc, GuardianException):
        code = exc.code.value
        if "schemathesis" in code:
            return f"SCHEMATHESIS_ERROR:{code}"
        return f"GUARDIAN_ERROR:{code}"
    text = str(exc)
    if text.startswith("SCHEMATHESIS_ERROR"):
        return text
    if text.startswith("SYSTEM_UNDER_TEST_SETUP_ERROR"):
        return text
    return f"GUARDIAN_ERROR:{type(exc).__name__}"


def _wait_postgres(container_name: str) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        result = subprocess.run(  # noqa: S603
            ["docker", "exec", container_name, "pg_isready", "-U", "postgres"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("SYSTEM_UNDER_TEST_SETUP_ERROR: PostgreSQL health timeout")


def _wait_health(url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("SYSTEM_UNDER_TEST_SETUP_ERROR: service exited before health")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError("SYSTEM_UNDER_TEST_SETUP_ERROR: service health timeout")


def _assert_tools() -> None:
    for path in (OASDIFF_PATH, SCHEMATHESIS_PATH):
        if not path.is_file():
            raise FileNotFoundError(path)
    oasdiff = subprocess.run(  # noqa: S603
        [str(OASDIFF_PATH), "--version"], capture_output=True, text=True, check=False
    )
    schemathesis = subprocess.run(  # noqa: S603
        [str(SCHEMATHESIS_PATH), "--version"], capture_output=True, text=True, check=False
    )
    if oasdiff.returncode != 0 or OASDIFF_VERSION not in oasdiff.stdout:
        raise RuntimeError("oasdiff version mismatch")
    if schemathesis.returncode != 0 or SCHEMATHESIS_VERSION not in schemathesis.stdout:
        raise RuntimeError("Schemathesis version mismatch")


def _assert_alignments() -> None:
    for case in CASES:
        checks = (
            (case.base_spec, case.base_sha256),
            (case.head_spec, case.head_sha256),
            (case.binary, case.binary_sha256),
        )
        for path, expected in checks:
            if _sha256(path) != expected:
                raise RuntimeError(f"INVALID_ALIGNMENT: {case.case_id} {path.name}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _rotated_systems(offset: int) -> tuple[str, ...]:
    index = offset % len(SYSTEMS)
    return SYSTEMS[index:] + SYSTEMS[:index]


def _verdict_value(value: Any) -> str | None:
    if isinstance(value, GuardianVerdict):
        return value.value
    return None


def _median(values: Sequence[float | int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _metric(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
        handle.flush()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_case_csv(path: Path, summary: Mapping[str, Any]) -> None:
    rows: list[dict[str, Any]] = []
    for case_id, case in summary["cases"].items():
        for system, metrics in case["systems"].items():
            rows.append(
                {
                    "case_id": case_id,
                    "system": system,
                    "measured_runs": metrics["measured_runs"],
                    "completed_runs": metrics["completed_runs"],
                    "non_error_verdict_runs": metrics["non_error_verdict_runs"],
                    "operations_tested": "|".join(str(value) for value in metrics["operations_tested"]),
                    "median_http_requests": metrics["median_http_requests"],
                    "median_runtime_seconds": metrics["median_runtime_seconds"],
                    "p95_runtime_seconds": metrics["p95_runtime_seconds"],
                    "verdict_tuples": "|".join(metrics["verdict_tuples"]),
                    "deterministic_agreement": metrics["deterministic_agreement"],
                }
            )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _build_manifest(
    run_id: str,
    started_at: datetime,
    completed_at: datetime,
    core_before: Mapping[str, str],
    core_after: Mapping[str, str],
    runner: E3CRunner,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "stage": "E3-C",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "systems": list(SYSTEMS),
        "warmups": runner.warmups,
        "measured_repetitions": runner.repetitions,
        "reset_policy": "fresh PostgreSQL container and exact Head service for every system run",
        "production_core_hashes_before": dict(core_before),
        "production_core_hashes_after": dict(core_after),
        "production_core_unchanged": core_before == core_after,
        "tools": {"oasdiff": OASDIFF_VERSION, "schemathesis": SCHEMATHESIS_VERSION},
        "schemathesis_parameters": {
            "checks": SCHEMATHESIS_CHECK,
            "phases": SCHEMATHESIS_PHASES,
            "max_examples": 1,
            "generation_deterministic": True,
            "request_timeout_seconds": 2.0,
            "max_redirects": 0,
        },
        "cases": [
            {
                "case_id": case.case_id,
                "repo": "supabase/auth",
                "base_commit": case.base_commit,
                "head_commit": case.head_commit,
                "base_spec_sha256": case.base_sha256,
                "head_spec_sha256": case.head_sha256,
                "binary_sha256": case.binary_sha256,
                "affected_operation": case.affected_operation,
            }
            for case in CASES
        ],
        "safety": {
            "targets": "127.0.0.1 dynamic ports only",
            "real_credentials_used": False,
            "redirects_followed": False,
            "public_production_api_called": False,
            "response_bodies_persisted": False,
            "raw_ndjson_persisted": False,
        },
    }


def _environment_record() -> dict[str, Any]:
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
        "guardian_git_commit": _repository_head(),
        "docker_server_version": _command_output(["docker", "version", "--format", "{{.Server.Version}}"]),
        "cold_environment_cost_included_in_scan_latency": False,
    }


def _repository_head() -> str:
    head = (PROJECT_ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head
    reference = head[5:]
    reference_path = PROJECT_ROOT / ".git" / Path(reference)
    if reference_path.is_file():
        return reference_path.read_text(encoding="utf-8").strip()
    for line in (PROJECT_ROOT / ".git" / "packed-refs").read_text(encoding="utf-8").splitlines():
        if line.endswith(f" {reference}"):
            return line.split()[0]
    return "unavailable"


def _command_output(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _core_hashes() -> dict[str, str]:
    return {relative: _sha256(PROJECT_ROOT / relative) for relative in CORE_PATHS}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the frozen E3-C runtime validation")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    return parser


def main() -> None:
    """运行 E3-C，控制台只打印脱敏进度."""
    args = _parser().parse_args()
    if args.repetitions < 1 or args.warmups < 1:
        raise SystemExit("repetitions and warmups must be positive")
    summary = asyncio.run(E3CRunner(repetitions=args.repetitions, warmups=args.warmups).run())
    Console().print_json(json=json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
