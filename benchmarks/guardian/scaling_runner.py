"""Stage E2.5 Scaling / Break-even 的真实实验编排器。."""

import asyncio
import hashlib
import json
import os
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.graph.state import CompiledStateGraph
from rich.console import Console

from app.services.guardian.errors import GuardianException
from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianPolicy, GuardianVerdict, OperationRef
from app.services.guardian.oasdiff import OASDIFF_VERSION, OasdiffAdapter
from app.services.guardian.schemathesis import SCHEMATHESIS_VERSION, SchemathesisAdapter, determine_final_verdict
from app.services.guardian.security import prepare_openapi_spec
from benchmarks.guardian.baselines.full_cli import FullSchemathesisCli
from benchmarks.guardian.baselines.guardian_deterministic import run_guardian_deterministic
from benchmarks.guardian.baselines.oasdiff_only import run_oasdiff_only
from benchmarks.guardian.baselines.oracle_scope import run_oracle_scope
from benchmarks.guardian.config import PROJECT_ROOT, REPORT_ROOT
from benchmarks.guardian.e2_5_models import ScalingCase, ScalingConfig, ScalingMeasurement, TimingSource
from benchmarks.guardian.models import BenchmarkSystem, ReproducibilityMetadata, SystemResult
from benchmarks.guardian.runner import LocalSUTServer
from benchmarks.guardian.scaling_dataset import affected_indexes, ensure_scaling_dataset, scaling_dataset_sha256
from benchmarks.guardian.scaling_metrics import aggregate_scaling_metrics
from benchmarks.guardian.scaling_reporting import render_scaling_report, write_scaling_summary_csv
from benchmarks.guardian.suts.scaling_api import create_scaling_app

SCALING_REPORT_ROOT = REPORT_ROOT / "e2_5" / "scaling"
PREFLIGHT_PATH = REPORT_ROOT / "e2_5" / "preflight_integrity.json"
_SYSTEMS = (
    BenchmarkSystem.FULL_SCHEMATHESIS,
    BenchmarkSystem.FULL_PIPELINE,
    BenchmarkSystem.ORACLE_SCOPE,
    BenchmarkSystem.GUARDIAN_DETERMINISTIC,
)


class ScalingBenchmarkRunner:
    """按规模和 affected ratio 运行四个公平系统。."""

    def __init__(
        self,
        *,
        oasdiff_path: Path,
        schemathesis_path: Path,
        config: ScalingConfig | None = None,
        report_root: Path = SCALING_REPORT_ROOT,
        oasdiff_archive_sha256: str | None = None,
        console: Console | None = None,
    ) -> None:
        """绑定固定工具、资源上限与报告目录。."""
        self._oasdiff_path = oasdiff_path.resolve()
        self._schemathesis_path = schemathesis_path.resolve()
        self._config = config or ScalingConfig()
        self._report_root = report_root.resolve()
        self._oasdiff_archive_sha256 = oasdiff_archive_sha256
        self._console = console or Console()

    async def run(self) -> dict[str, Any]:
        """执行全部测量，逐条持久化 raw，随后聚合与验证完整性。."""
        preflight = _read_object(PREFLIGHT_PATH)
        _assert_frozen_integrity(preflight)
        started_at = datetime.now(UTC)
        started_timer = time.monotonic()
        run_id = f"guardian-e2-5-scaling-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        self._report_root.mkdir(parents=True, exist_ok=True)
        metadata = await asyncio.to_thread(self._collect_metadata)
        cases = ensure_scaling_dataset(self._config)
        dataset_hash = scaling_dataset_sha256(cases)
        oasdiff, schemathesis, full_cli, graph = self._build_systems()
        raw_path = self._report_root / "raw_results.jsonl"
        raw_path.write_text("", encoding="utf-8")
        measurements: list[ScalingMeasurement] = []

        for case_index, case in enumerate(cases):
            if time.monotonic() - started_timer > self._config.total_benchmark_budget_seconds:
                raise TimeoutError("TOTAL_BENCHMARK_BUDGET exceeded before required scales completed")
            base_path = (PROJECT_ROOT / case.base_spec).resolve()
            head_path = (PROJECT_ROOT / case.head_spec).resolve()
            operations = prepare_openapi_spec(str(head_path), 5 * 1024 * 1024).operations
            if len(operations) != case.total_operations:
                raise RuntimeError(f"generated operation count mismatch: {case.case_id}")
            with LocalSUTServer(
                create_scaling_app(case.total_operations, affected_indexes(case)), log_level="critical"
            ) as server:
                for warmup in range(self._config.warmup_runs):
                    for system in self._rotated_systems(case_index + warmup):
                        item = await self._measure(
                            run_id,
                            case,
                            system,
                            warmup,
                            True,
                            metadata,
                            server,
                            base_path,
                            head_path,
                            operations,
                            oasdiff,
                            schemathesis,
                            full_cli,
                            graph,
                        )
                        measurements.append(item)
                        _append_measurement(raw_path, item)
                for repetition in range(self._config.repetitions):
                    for system in self._rotated_systems(case_index + repetition + self._config.random_seed):
                        item = await self._measure(
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
                            oasdiff,
                            schemathesis,
                            full_cli,
                            graph,
                        )
                        measurements.append(item)
                        _append_measurement(raw_path, item)
            measured_count = sum(not item.warmup and item.case_id == case.case_id for item in measurements)
            self._console.print(
                f"[cyan]E2.5 scaling[/cyan] {case_index + 1}/{len(cases)} {case.case_id}: {measured_count} measured"
            )

        completed_at = datetime.now(UTC)
        metrics = aggregate_scaling_metrics(cases, tuple(measurements), seed=self._config.random_seed)
        _assert_frozen_integrity(preflight)
        integrity_after = _current_integrity(preflight)
        manifest = self._manifest(
            run_id,
            started_at,
            completed_at,
            cases,
            metadata,
            dataset_hash,
            preflight,
            integrity_after,
        )
        summary: dict[str, Any] = {
            "benchmark": "API Guardian Scaling and Break-even Benchmark",
            "stage": "E2.5-scaling",
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "configuration": self._config.model_dump(mode="json"),
            "dataset": {
                "case_count": len(cases),
                "sizes": list(self._config.sizes),
                "scenarios": sorted({case.scenario.value for case in cases}),
                "dataset_sha256": dataset_hash,
            },
            "raw_sample_counts": {
                "all": len(measurements),
                "warmup": sum(item.warmup for item in measurements),
                "measured": sum(not item.warmup for item in measurements),
            },
            "metrics": metrics,
            "integrity": integrity_after,
        }
        _write_json(self._report_root / "run_manifest.json", manifest)
        _write_json(self._report_root / "summary.json", summary)
        write_scaling_summary_csv(self._report_root / "summary.csv", summary)
        (self._report_root / "scaling_report.md").write_text(render_scaling_report(summary), encoding="utf-8")
        return summary

    async def _measure(
        self,
        run_id: str,
        case: ScalingCase,
        system: BenchmarkSystem,
        repetition: int,
        warmup: bool,
        metadata: ReproducibilityMetadata,
        server: LocalSUTServer,
        base_path: Path,
        head_path: Path,
        operations: tuple[OperationRef, ...],
        oasdiff: OasdiffAdapter,
        schemathesis: SchemathesisAdapter,
        full_cli: FullSchemathesisCli,
        graph: CompiledStateGraph,
    ) -> ScalingMeasurement:
        """测量一次真实系统执行，并从本地 SUT 读取请求计数。."""
        await server.reset_counter()
        started = time.perf_counter()
        component: dict[str, float | None] = {
            "oasdiff": None,
            "scope": None,
            "schemathesis": None,
            "orchestration": None,
        }
        try:
            async with asyncio.timeout(self._config.max_case_runtime_seconds):
                result, component = await self._execute(
                    run_id,
                    case,
                    system,
                    repetition,
                    warmup,
                    server,
                    base_path,
                    head_path,
                    operations,
                    oasdiff,
                    schemathesis,
                    full_cli,
                    graph,
                )
        except GuardianException as exc:
            result = _error_result(system, len(operations), exc.code.value)
        except TimeoutError:
            result = _error_result(system, len(operations), "benchmark_case_timeout")
        duration = time.perf_counter() - started
        requests = await server.request_count()
        selected_count = len(result.selected_operations)
        if system == BenchmarkSystem.FULL_SCHEMATHESIS:
            process_count = 1
        elif system == BenchmarkSystem.FULL_PIPELINE:
            process_count = 2
        elif system == BenchmarkSystem.ORACLE_SCOPE:
            process_count = len(case.affected_operations)
        else:
            process_count = 1 + selected_count
        timing_source = (
            TimingSource.END_TO_END_WRAPPER_ONLY
            if system == BenchmarkSystem.GUARDIAN_DETERMINISTIC
            else TimingSource.WRAPPER_COMPONENT_BOUNDARIES
        )
        kind = "warmup" if warmup else "measured"
        return ScalingMeasurement(
            run_id=f"{run_id}:{case.case_id}:{system.value}:{kind}:{repetition}",
            measured_at=datetime.now(UTC),
            case_id=case.case_id,
            total_operations=case.total_operations,
            affected_operations=len(case.affected_operations),
            scenario=case.scenario,
            system=system,
            repetition=repetition,
            warmup=warmup,
            total_duration_seconds=duration,
            oasdiff_duration_seconds=component["oasdiff"],
            scope_selection_duration_seconds=component["scope"],
            schemathesis_duration_seconds=component["schemathesis"],
            report_orchestration_duration_seconds=component["orchestration"],
            timing_source=timing_source,
            process_count=process_count,
            operations_tested=result.operations_tested,
            http_requests_issued=requests,
            selected_operations=result.selected_operations,
            contract_verdict=result.contract_verdict,
            regression_verdict=result.regression_verdict,
            final_verdict=result.final_verdict,
            error_codes=result.error_codes,
            metadata=metadata,
        )

    async def _execute(
        self,
        run_id: str,
        case: ScalingCase,
        system: BenchmarkSystem,
        repetition: int,
        warmup: bool,
        server: LocalSUTServer,
        base_path: Path,
        head_path: Path,
        operations: tuple[OperationRef, ...],
        oasdiff: OasdiffAdapter,
        schemathesis: SchemathesisAdapter,
        full_cli: FullSchemathesisCli,
        graph: CompiledStateGraph,
    ) -> tuple[SystemResult, dict[str, float | None]]:
        """执行公平系统；分解时间只在 wrapper 能可靠测量时记录。."""
        component: dict[str, float | None] = {
            "oasdiff": None,
            "scope": None,
            "schemathesis": None,
            "orchestration": None,
        }
        if system == BenchmarkSystem.FULL_SCHEMATHESIS:
            started = time.perf_counter()
            result = await full_cli.run(
                str(head_path), server.base_url, operations, self._config.max_scan_artifact_bytes
            )
            component["schemathesis"] = time.perf_counter() - started
            return result, component
        if system == BenchmarkSystem.FULL_PIPELINE:
            started = time.perf_counter()
            contract = await run_oasdiff_only(
                oasdiff,
                str(base_path),
                str(head_path),
                max_scan_artifact_bytes=self._config.max_scan_artifact_bytes,
                total_operations=len(operations),
            )
            component["oasdiff"] = time.perf_counter() - started
            started = time.perf_counter()
            regression = await full_cli.run(
                str(head_path), server.base_url, operations, self._config.max_scan_artifact_bytes
            )
            component["schemathesis"] = time.perf_counter() - started
            final = determine_final_verdict(contract.contract_verdict, regression.regression_verdict, has_error=False)
            return regression.model_copy(
                update={"contract_verdict": contract.contract_verdict, "final_verdict": final}
            ), component
        if system == BenchmarkSystem.ORACLE_SCOPE:
            started = time.perf_counter()
            result = await run_oracle_scope(
                schemathesis,
                str(head_path),
                server.base_url,
                operations,
                case.affected_operations,
                max_scan_artifact_bytes=self._config.max_scan_artifact_bytes,
            )
            component["schemathesis"] = time.perf_counter() - started
            return result, component
        if system == BenchmarkSystem.GUARDIAN_DETERMINISTIC:
            result = await run_guardian_deterministic(
                graph,
                scan_id=f"{run_id}-{case.case_id}-{repetition}-{int(warmup)}",
                base_spec_path=str(base_path),
                head_spec_path=str(head_path),
                target_base_url=server.base_url,
                total_operations=len(operations),
            )
            return result, component
        raise ValueError(f"unsupported scaling system: {system}")

    def _build_systems(self) -> tuple[OasdiffAdapter, SchemathesisAdapter, FullSchemathesisCli, CompiledStateGraph]:
        """构造与 E2 参数相同的工具，并显式禁用 LLM。."""
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
        full_cli = FullSchemathesisCli(
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
        return (
            oasdiff,
            schemathesis,
            full_cli,
            create_guardian_graph(policy=policy, oasdiff=oasdiff, schemathesis=schemathesis),
        )

    def _collect_metadata(self) -> ReproducibilityMetadata:
        """验证锁定工具版本并记录不含 Secret 的运行环境。."""
        if OASDIFF_VERSION not in _command_version((str(self._oasdiff_path), "--version")):
            raise RuntimeError("oasdiff version mismatch")
        if SCHEMATHESIS_VERSION not in _command_version((str(self._schemathesis_path), "--version")):
            raise RuntimeError("Schemathesis version mismatch")
        return ReproducibilityMetadata(
            benchmark_version="E2.5-scaling-v1",
            guardian_git_commit=_git_output(("rev-parse", "HEAD")),
            guardian_worktree_dirty=bool(_git_output(("status", "--porcelain"))),
            python_version=platform.python_version(),
            operating_system=f"{platform.system()} {platform.release()} {platform.machine()}",
            cpu_logical_count=os.cpu_count(),
            random_seed=self._config.random_seed,
            oasdiff_version=OASDIFF_VERSION,
            oasdiff_archive_sha256=self._oasdiff_archive_sha256,
            schemathesis_version=SCHEMATHESIS_VERSION,
            schemathesis_parameters={
                "checks": "response_schema_conformance",
                "phases": "examples,coverage",
                "max_examples": self._config.max_examples,
                "generation_deterministic": True,
                "max_redirects": 0,
                "request_timeout_seconds": self._config.request_timeout_seconds,
            },
            deepseek_model=None,
        )

    def _rotated_systems(self, offset: int) -> tuple[BenchmarkSystem, ...]:
        rotation = offset % len(_SYSTEMS)
        return _SYSTEMS[rotation:] + _SYSTEMS[:rotation]

    def _manifest(
        self,
        run_id: str,
        started_at: datetime,
        completed_at: datetime,
        cases: tuple[ScalingCase, ...],
        metadata: ReproducibilityMetadata,
        dataset_hash: str,
        preflight: dict[str, Any],
        integrity_after: dict[str, Any],
    ) -> dict[str, Any]:
        """记录工具、硬件、数据、参数与冻结资产哈希。."""
        return {
            "run_id": run_id,
            "stage": "E2.5-scaling",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "git_commit": metadata.guardian_git_commit,
            "production_core_hashes_before": preflight["production_core"],
            "production_core_hashes_after": integrity_after["production_core"],
            "frozen_e2_raw_hashes_before": preflight["frozen_e2_raw"],
            "frozen_e2_raw_hashes_after": integrity_after["frozen_e2_raw"],
            "dataset_sha256": dataset_hash,
            "generator_version": cases[0].generator_version,
            "metadata": metadata.model_dump(mode="json"),
            "hardware": {"cpu_logical_count": os.cpu_count(), "ram_bytes": _ram_bytes()},
            "configuration": self._config.model_dump(mode="json"),
            "systems": [system.value for system in _SYSTEMS],
            "command_contracts": {
                "oasdiff": "oasdiff breaking --format json --allow-external-refs=false --fail-on ERR -- BASE HEAD",
                "full_schemathesis": "st run HEAD --url TARGET [fixed settings; no include filter]",
                "scoped_schemathesis": "st run HEAD --url TARGET --include-operation-id ID [same fixed settings]",
            },
            "cases": [
                {
                    **case.model_dump(mode="json"),
                    "base_spec_sha256": _file_sha256(PROJECT_ROOT / case.base_spec),
                    "head_spec_sha256": _file_sha256(PROJECT_ROOT / case.head_spec),
                }
                for case in cases
            ],
            "secret_fields_recorded": [],
        }


def _error_result(system: BenchmarkSystem, total_operations: int, error_code: str) -> SystemResult:
    return SystemResult(
        contract_verdict=GuardianVerdict.ERROR
        if system in {BenchmarkSystem.FULL_PIPELINE, BenchmarkSystem.GUARDIAN_DETERMINISTIC}
        else None,
        regression_verdict=GuardianVerdict.ERROR,
        final_verdict=GuardianVerdict.ERROR,
        total_operations=total_operations,
        operations_tested=0,
        bug_detected=False,
        error_codes=(error_code,),
    )


def _append_measurement(path: Path, measurement: ScalingMeasurement) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(measurement.model_dump(mode="json"), sort_keys=True) + "\n")
        handle.flush()


def _assert_frozen_integrity(preflight: dict[str, Any]) -> None:
    current = _current_integrity(preflight)
    if current["production_core"] != preflight["production_core"]:
        raise RuntimeError("production Guardian Core hash changed during E2.5")
    if current["frozen_e2_raw"] != preflight["frozen_e2_raw"]:
        raise RuntimeError("frozen E2 raw data changed during E2.5")


def _current_integrity(preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        group: {relative: _file_sha256(PROJECT_ROOT / relative) for relative in expected}
        for group, expected in (
            ("production_core", preflight["production_core"]),
            ("frozen_e2_raw", preflight["frozen_e2_raw"]),
        )
    }


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_version(command: tuple[str, ...]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"tool version command failed: {Path(command[0]).name}")
    return f"{result.stdout}\n{result.stderr}".strip()


def _git_output(arguments: tuple[str, ...]) -> str:
    result = subprocess.run(  # noqa: S603
        ("git", *arguments), cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError("unable to read Git metadata")
    return result.stdout.strip()


def _ram_bytes() -> int | None:
    """RAM 采集在当前 Windows runner 不可靠，因此按协议标为 unavailable。."""
    return None
