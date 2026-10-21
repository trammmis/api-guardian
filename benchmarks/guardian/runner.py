"""Stage E1 真实实验编排器；不修改 Guardian production Core。."""

import asyncio
import hashlib
import json
import os
import platform
import socket
import subprocess
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from threading import Thread
from typing import Any, override
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI
from langgraph.graph.state import CompiledStateGraph
from rich.console import Console

from app.services.guardian.errors import GuardianException
from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianPolicy, GuardianVerdict, OperationRef
from app.services.guardian.oasdiff import OASDIFF_VERSION, OasdiffAdapter
from app.services.guardian.schemathesis import SCHEMATHESIS_VERSION, SchemathesisAdapter
from app.services.guardian.security import prepare_openapi_spec
from benchmarks.guardian.baselines.full_schemathesis import run_full_schemathesis
from benchmarks.guardian.baselines.guardian_deterministic import run_guardian_deterministic
from benchmarks.guardian.baselines.oasdiff_only import run_oasdiff_only
from benchmarks.guardian.baselines.oracle_scope import run_oracle_scope
from benchmarks.guardian.config import BENCHMARK_ROOT, BenchmarkConfig, LATEST_REPORT_ROOT
from benchmarks.guardian.metrics import aggregate_metrics
from benchmarks.guardian.models import (
    BenchmarkSystem,
    MutationCase,
    RawMeasurement,
    ReproducibilityMetadata,
    SystemResult,
)
from benchmarks.guardian.mutations.apply import materialize_all, resolve_project_path
from benchmarks.guardian.mutations.definitions import DEFAULT_CASES_PATH, load_mutation_cases
from benchmarks.guardian.reporting import (
    render_benchmark_report,
    render_experiment_log,
    write_json,
    write_raw_measurements,
    write_summary_csv,
)
from benchmarks.guardian.suts.orders_api import create_app as create_orders_app
from benchmarks.guardian.suts.users_api import create_app as create_users_app

_SUT_FACTORIES: dict[str, Callable[[str], FastAPI]] = {
    "users_api": create_users_app,
    "orders_api": create_orders_app,
}
_PERFORMANCE_SYSTEMS = (
    BenchmarkSystem.FULL_SCHEMATHESIS,
    BenchmarkSystem.ORACLE_SCOPE,
    BenchmarkSystem.GUARDIAN_DETERMINISTIC,
)


class LocalSUTServer(AbstractContextManager["LocalSUTServer"]):
    """在线程中把 Controlled FastAPI SUT 启动到随机 loopback port。."""

    def __init__(self, app: FastAPI, *, log_level: str = "warning") -> None:
        """准备 Uvicorn Server，但不提前绑定端口。."""
        self._port = _free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self._port,
                log_level=log_level,
                access_log=False,
            )
        )
        self._thread = Thread(target=self._server.run, daemon=True)

    @property
    def base_url(self) -> str:
        """返回只用于本轮 measurement 的本地 URL。."""
        return f"http://127.0.0.1:{self._port}"

    @override
    def __enter__(self) -> "LocalSUTServer":
        """启动服务并等待健康状态。."""
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started and self._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:
            raise RuntimeError("benchmark SUT failed to start")
        return self

    @override
    def __exit__(self, *args: object) -> None:
        """停止本地服务并等待线程退出。."""
        del args
        self._server.should_exit = True
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            raise RuntimeError("benchmark SUT failed to stop")

    async def reset_counter(self) -> None:
        """在计时前清零业务 request 计数。."""
        async with _control_client() as client:
            response = await client.post(f"{self.base_url}/__benchmark__/reset")
            response.raise_for_status()

    async def request_count(self) -> int:
        """读取本次 measurement 的真实业务 HTTP request 数。."""
        async with _control_client() as client:
            response = await client.get(f"{self.base_url}/__benchmark__/metrics")
            response.raise_for_status()
            value = response.json().get("http_requests")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError("benchmark SUT returned invalid request count")
        return value


def _control_client() -> httpx.AsyncClient:
    """控制通道仅访问 loopback，禁止继承系统 HTTP 代理。."""
    return httpx.AsyncClient(timeout=5, trust_env=False)


class BenchmarkRunner:
    """执行 2 SUT × 10 cases × warm-up/5 repetitions 的 E1。."""

    def __init__(
        self,
        *,
        oasdiff_path: Path,
        schemathesis_path: Path,
        config: BenchmarkConfig | None = None,
        report_root: Path = LATEST_REPORT_ROOT,
        oasdiff_archive_sha256: str | None = None,
        console: Console | None = None,
    ) -> None:
        """绑定固定工具与输出目录，不读取或保存任何 API key。."""
        self._oasdiff_path = oasdiff_path.resolve()
        self._schemathesis_path = schemathesis_path.resolve()
        self._config = config or BenchmarkConfig()
        self._report_root = report_root.resolve()
        self._oasdiff_archive_sha256 = _validate_optional_sha256(oasdiff_archive_sha256)
        self._console = console or Console()

    async def run(self) -> dict[str, Any]:
        """运行 Stage E1，并写出 raw、summary、CSV、Markdown 与 manifest。."""
        started_at = datetime.now(UTC)
        run_id = f"guardian-e1-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        self._report_root.mkdir(parents=True, exist_ok=True)
        cases = load_mutation_cases()
        if len(cases) < 10 or len({case.sut_id for case in cases}) < 2:
            raise ValueError("Stage E1 requires at least 2 SUTs and 10 mutation cases")
        generated_heads = materialize_all(cases)
        metadata = await asyncio.to_thread(self._collect_metadata)
        core_hash_before = _tree_sha256(BENCHMARK_ROOT.parents[1] / "app" / "services" / "guardian")
        measurements: list[RawMeasurement] = []
        raw_path = self._report_root / "raw_results.jsonl"
        raw_path.write_text("", encoding="utf-8")

        oasdiff = OasdiffAdapter(
            executable=str(self._oasdiff_path),
            timeout_seconds=self._config.subprocess_timeout_seconds,
            max_artifact_bytes=self._config.max_artifact_bytes,
        )
        schemathesis = SchemathesisAdapter(
            executable=str(self._schemathesis_path),
            timeout_seconds=self._config.subprocess_timeout_seconds,
            request_timeout_seconds=self._config.request_timeout_seconds,
            max_examples=self._config.max_examples,
            max_artifact_bytes=self._config.max_artifact_bytes,
        )
        policy = GuardianPolicy(
            allowed_target_hosts=("127.0.0.1",),
            allow_private_targets=True,
            max_artifact_bytes=self._config.max_artifact_bytes,
            max_scan_artifact_bytes=self._config.max_scan_artifact_bytes,
            subprocess_timeout_seconds=self._config.subprocess_timeout_seconds,
            regression_request_timeout_seconds=self._config.request_timeout_seconds,
            regression_max_examples=self._config.max_examples,
            analysis_enabled=False,
        )
        graph = create_guardian_graph(
            policy=policy,
            oasdiff=oasdiff,
            schemathesis=schemathesis,
        )

        for case_index, case in enumerate(cases):
            base_path = resolve_project_path(case.base_spec)
            head_path = generated_heads[case.case_id]
            operations = prepare_openapi_spec(str(head_path), policy.max_openapi_bytes).operations
            if not 8 <= len(operations) <= 20:
                raise ValueError(f"SUT operation count outside E1 boundary: {case.sut_id}")

            contract_measurement = await self._measure(
                run_id=run_id,
                case=case,
                system=BenchmarkSystem.OASDIFF_ONLY,
                repetition=0,
                warmup=False,
                metadata=metadata,
                server=None,
                operation=partial(
                    run_oasdiff_only,
                    oasdiff,
                    str(base_path),
                    str(head_path),
                    max_scan_artifact_bytes=self._config.max_scan_artifact_bytes,
                    total_operations=len(operations),
                ),
            )
            measurements.append(contract_measurement)
            _append_raw(raw_path, contract_measurement)

            app_factory = _SUT_FACTORIES.get(case.sut_id)
            if app_factory is None:
                raise ValueError(f"unknown benchmark SUT: {case.sut_id}")
            with LocalSUTServer(app_factory(case.target_variant)) as server:
                for warmup_index in range(self._config.warmup_runs):
                    systems = self._rotated_systems(case_index + warmup_index)
                    for system in systems:
                        measurement = await self._measure_system(
                            run_id,
                            case,
                            system,
                            warmup_index,
                            True,
                            metadata,
                            server,
                            base_path,
                            head_path,
                            operations,
                            schemathesis,
                            graph,
                        )
                        measurements.append(measurement)
                        _append_raw(raw_path, measurement)

                for repetition in range(self._config.repetitions):
                    systems = self._rotated_systems(case_index + repetition + self._config.random_seed)
                    for system in systems:
                        measurement = await self._measure_system(
                            run_id,
                            case,
                            system,
                            repetition,
                            False,
                            metadata,
                            server,
                            base_path,
                            head_path,
                            operations,
                            schemathesis,
                            graph,
                        )
                        measurements.append(measurement)
                        _append_raw(raw_path, measurement)
            self._console.print(f"[cyan]E1[/cyan] {case_index + 1}/{len(cases)} {case.case_id} complete")

        completed_at = datetime.now(UTC)
        metric_values, missed = aggregate_metrics(cases, tuple(measurements))
        core_hash_after = _tree_sha256(BENCHMARK_ROOT.parents[1] / "app" / "services" / "guardian")
        production_core_unchanged = core_hash_before == core_hash_after
        summary = {
            "benchmark": "API Guardian Effectiveness Benchmark",
            "stage": "E1",
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "configuration": self._config.model_dump(mode="json"),
            "metadata": metadata.model_dump(mode="json"),
            "dataset": {
                "case_count": len(cases),
                "sut_count": len({case.sut_id for case in cases}),
                "detectable_case_count": sum(case.expected_detectable_by_current_oracle for case in cases),
                "negative_control_count": sum(not case.expected_detectable_by_current_oracle for case in cases),
            },
            "metrics": metric_values,
            "production_guardian_core_unchanged_during_run": production_core_unchanged,
        }
        manifest = self._build_manifest(
            run_id,
            started_at,
            completed_at,
            cases,
            generated_heads,
            metadata,
            core_hash_before,
            core_hash_after,
        )
        write_raw_measurements(raw_path, tuple(measurements))
        write_json(self._report_root / "summary.json", summary)
        write_summary_csv(self._report_root / "summary.csv", summary)
        write_json(
            self._report_root / "missed_cases.json",
            [item.model_dump(mode="json") for item in missed],
        )
        write_json(self._report_root / "run_manifest.json", manifest)
        (self._report_root / "benchmark_report.md").write_text(
            render_benchmark_report(summary, cases),
            encoding="utf-8",
        )
        (self._report_root / "experiment_log.md").write_text(
            render_experiment_log(summary, production_core_unchanged),
            encoding="utf-8",
        )
        return summary

    async def _measure_system(
        self,
        run_id: str,
        case: MutationCase,
        system: BenchmarkSystem,
        repetition: int,
        warmup: bool,
        metadata: ReproducibilityMetadata,
        server: LocalSUTServer,
        base_path: Path,
        head_path: Path,
        operations: tuple[OperationRef, ...],
        schemathesis: SchemathesisAdapter,
        graph: CompiledStateGraph,
    ) -> RawMeasurement:
        """构造一个系统的公平运行函数并执行计时。."""
        return await self._measure(
            run_id=run_id,
            case=case,
            system=system,
            repetition=repetition,
            warmup=warmup,
            metadata=metadata,
            server=server,
            operation=partial(
                self._execute_system,
                system=system,
                run_id=run_id,
                case=case,
                repetition=repetition,
                warmup=warmup,
                server=server,
                base_path=base_path,
                head_path=head_path,
                operations=operations,
                schemathesis=schemathesis,
                graph=graph,
            ),
        )

    async def _execute_system(
        self,
        *,
        system: BenchmarkSystem,
        run_id: str,
        case: MutationCase,
        repetition: int,
        warmup: bool,
        server: LocalSUTServer,
        base_path: Path,
        head_path: Path,
        operations: tuple[OperationRef, ...],
        schemathesis: SchemathesisAdapter,
        graph: CompiledStateGraph,
    ) -> SystemResult:
        """执行一个 E1 performance system."""
        if system == BenchmarkSystem.FULL_SCHEMATHESIS:
            return await run_full_schemathesis(
                schemathesis,
                str(head_path),
                server.base_url,
                operations,
                max_scan_artifact_bytes=self._config.max_scan_artifact_bytes,
            )
        if system == BenchmarkSystem.ORACLE_SCOPE:
            return await run_oracle_scope(
                schemathesis,
                str(head_path),
                server.base_url,
                operations,
                case.ground_truth_affected_operations,
                max_scan_artifact_bytes=self._config.max_scan_artifact_bytes,
            )
        if system == BenchmarkSystem.GUARDIAN_DETERMINISTIC:
            return await run_guardian_deterministic(
                graph,
                scan_id=f"{run_id}-{case.case_id}-{repetition}-{int(warmup)}",
                base_spec_path=str(base_path),
                head_spec_path=str(head_path),
                target_base_url=server.base_url,
                total_operations=len(operations),
            )
        raise ValueError(f"unsupported E1 performance system: {system}")

    async def _measure(
        self,
        *,
        run_id: str,
        case: MutationCase,
        system: BenchmarkSystem,
        repetition: int,
        warmup: bool,
        metadata: ReproducibilityMetadata,
        server: LocalSUTServer | None,
        operation: Callable[[], Awaitable[SystemResult]],
    ) -> RawMeasurement:
        """把一次系统执行转换为可审计原始 measurement。."""
        if server is not None:
            await server.reset_counter()
        started = time.perf_counter()
        try:
            result = await operation()
        except GuardianException as exc:
            result = _guardian_error_result(system, exc, total_operations=8)
        duration = time.perf_counter() - started
        request_count = await server.request_count() if server is not None else 0
        kind = "warmup" if warmup else "measured"
        return RawMeasurement(
            run_id=f"{run_id}:{case.case_id}:{system.value}:{kind}:{repetition}",
            measured_at=datetime.now(UTC),
            case_id=case.case_id,
            sut_id=case.sut_id,
            system=system,
            repetition=repetition,
            warmup=warmup,
            duration_seconds=duration,
            contract_verdict=result.contract_verdict,
            regression_verdict=result.regression_verdict,
            final_verdict=result.final_verdict,
            selected_operations=result.selected_operations,
            total_operations=result.total_operations,
            operations_tested=result.operations_tested,
            http_requests_issued=request_count,
            bug_detected=result.bug_detected,
            error_codes=result.error_codes,
            metadata=metadata,
        )

    def _rotated_systems(self, offset: int) -> tuple[BenchmarkSystem, ...]:
        """用固定 seed 派生轮换顺序，避免永远由同一系统先运行。."""
        rotation = offset % len(_PERFORMANCE_SYSTEMS)
        return _PERFORMANCE_SYSTEMS[rotation:] + _PERFORMANCE_SYSTEMS[:rotation]

    def _collect_metadata(self) -> ReproducibilityMetadata:
        """验证工具版本并采集不含 Secret 的环境信息。."""
        oasdiff_output = _command_version((str(self._oasdiff_path), "--version"))
        schemathesis_output = _command_version((str(self._schemathesis_path), "--version"))
        if OASDIFF_VERSION not in oasdiff_output:
            raise RuntimeError(f"oasdiff version mismatch; expected {OASDIFF_VERSION}")
        if SCHEMATHESIS_VERSION not in schemathesis_output:
            raise RuntimeError(f"Schemathesis version mismatch; expected {SCHEMATHESIS_VERSION}")
        commit = _git_output(("rev-parse", "HEAD"))
        dirty = bool(_git_output(("status", "--porcelain")))
        return ReproducibilityMetadata(
            benchmark_version="E1.0",
            guardian_git_commit=commit,
            guardian_worktree_dirty=dirty,
            python_version=platform.python_version(),
            operating_system=f"{platform.system()} {platform.release()} {platform.machine()}",
            cpu_logical_count=os.cpu_count(),
            random_seed=self._config.random_seed,
            oasdiff_version=OASDIFF_VERSION,
            oasdiff_archive_sha256=self._oasdiff_archive_sha256,
            schemathesis_version=SCHEMATHESIS_VERSION,
            schemathesis_parameters={
                "checks": self._config.schemathesis_check,
                "phases": self._config.schemathesis_phases,
                "max_examples": self._config.max_examples,
                "generation_deterministic": self._config.generation_deterministic,
                "max_redirects": self._config.max_redirects,
                "request_timeout_seconds": self._config.request_timeout_seconds,
            },
            deepseek_model=None,
        )

    def _build_manifest(
        self,
        run_id: str,
        started_at: datetime,
        completed_at: datetime,
        cases: tuple[MutationCase, ...],
        generated_heads: dict[str, Path],
        metadata: ReproducibilityMetadata,
        core_hash_before: str,
        core_hash_after: str,
    ) -> dict[str, Any]:
        """生成输入、命令契约与 core hash 的审计 manifest。."""
        return {
            "run_id": run_id,
            "stage": "E1",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "metadata": metadata.model_dump(mode="json"),
            "configuration": self._config.model_dump(mode="json"),
            "executed_systems": [
                BenchmarkSystem.OASDIFF_ONLY.value,
                BenchmarkSystem.FULL_SCHEMATHESIS.value,
                BenchmarkSystem.ORACLE_SCOPE.value,
                BenchmarkSystem.GUARDIAN_DETERMINISTIC.value,
            ],
            "implemented_not_repeated_in_e1": [BenchmarkSystem.FULL_PIPELINE.value],
            "command_contracts": {
                "oasdiff": "oasdiff breaking --format json --allow-external-refs=false --fail-on ERR -- BASE HEAD",
                "schemathesis": (
                    "st run HEAD --url TARGET --include-operation-id ID "
                    "--checks response_schema_conformance --phases examples,coverage --max-examples 1 "
                    "--generation-deterministic --max-redirects 0 --request-timeout 2.0 "
                    "--report ndjson --report-ndjson-path REPORT --output-sanitize true --no-color"
                ),
            },
            "dataset_sha256": _file_sha256(DEFAULT_CASES_PATH),
            "cases": [
                {
                    "case_id": case.case_id,
                    "base_spec_sha256": _file_sha256(resolve_project_path(case.base_spec)),
                    "head_spec_sha256": _file_sha256(generated_heads[case.case_id]),
                }
                for case in cases
            ],
            "production_guardian_core_sha256_before": core_hash_before,
            "production_guardian_core_sha256_after": core_hash_after,
            "production_guardian_core_unchanged": core_hash_before == core_hash_after,
            "secret_fields_recorded": [],
        }


def _guardian_error_result(
    system: BenchmarkSystem,
    error: GuardianException,
    *,
    total_operations: int,
) -> SystemResult:
    """保留 FAIL 与基础设施 ERROR 的区别。."""
    contract_error = system == BenchmarkSystem.OASDIFF_ONLY
    return SystemResult(
        contract_verdict=GuardianVerdict.ERROR if contract_error else None,
        regression_verdict=None if contract_error else GuardianVerdict.ERROR,
        final_verdict=GuardianVerdict.ERROR,
        total_operations=total_operations,
        operations_tested=0,
        bug_detected=False,
        error_codes=(error.code.value,),
    )


def _free_port() -> int:
    """让操作系统选择当前可用的 loopback port。."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def _append_raw(path: Path, measurement: RawMeasurement) -> None:
    """每次测量立即 fsync 前写盘，异常时仍保留已完成记录。."""
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(measurement.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def _command_version(command: tuple[str, ...]) -> str:
    """不经过 shell 执行本地版本命令。."""
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"tool version command failed: {Path(command[0]).name}")
    return f"{result.stdout}\n{result.stderr}".strip()


def _git_output(arguments: tuple[str, ...]) -> str:
    """读取当前 Git 状态，不修改仓库。."""
    result = subprocess.run(  # noqa: S603
        ("git", *arguments),
        cwd=BENCHMARK_ROOT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("unable to read Git metadata")
    return result.stdout.strip()


def _file_sha256(path: Path) -> str:
    """计算一个审计输入文件的 SHA256。."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_optional_sha256(value: str | None) -> str | None:
    """拒绝缩写或非十六进制的审计 SHA256。."""
    if value is None:
        return None
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("oasdiff archive SHA256 must be 64 hexadecimal characters")
    return normalized


def _tree_sha256(root: Path) -> str:
    """按相对路径稳定计算 production Guardian Core 树 hash。."""
    digest = hashlib.sha256()
    files: Iterator[Path] = iter(sorted(path for path in root.rglob("*") if path.is_file()))
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
