"""Stage E2 真实实验编排器；生产 Guardian Core 在运行前后做树哈希校验。."""

import asyncio
import hashlib
import json
import os
import platform
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from langgraph.graph.state import CompiledStateGraph
from rich.console import Console

from app.services.guardian.errors import GuardianException
from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianPolicy, GuardianVerdict, OperationRef
from app.services.guardian.oasdiff import OASDIFF_VERSION, OasdiffAdapter, normalize_contract_changes
from app.services.guardian.schemathesis import SCHEMATHESIS_VERSION, SchemathesisAdapter, determine_final_verdict
from app.services.guardian.scope import determine_contract_verdict, select_test_scope
from app.services.guardian.security import prepare_openapi_spec
from benchmarks.guardian.baselines.full_cli import FullSchemathesisCli
from benchmarks.guardian.baselines.guardian_deterministic import run_guardian_deterministic
from benchmarks.guardian.baselines.oasdiff_only import run_oasdiff_only
from benchmarks.guardian.baselines.oracle_scope import run_oracle_scope
from benchmarks.guardian.config import BENCHMARK_ROOT, BenchmarkConfig, E2_LATEST_REPORT_ROOT
from benchmarks.guardian.datasets.contract.definitions import CASES_PATH, ensure_contract_dataset
from benchmarks.guardian.e2_metrics import E2_PERFORMANCE_SYSTEMS, aggregate_e2_metrics
from benchmarks.guardian.e2_reporting import render_e2_experiment_log, render_e2_report, write_e2_summary_csv
from benchmarks.guardian.models import (
    BenchmarkSystem,
    ContractCase,
    ContractMeasurement,
    MutationCase,
    RawMeasurement,
    ReproducibilityMetadata,
    SystemResult,
)
from benchmarks.guardian.mutations.apply import (
    materialize_all,
    materialize_all_contract_heads,
    resolve_project_path,
)
from benchmarks.guardian.mutations.definitions import load_e2_mutation_cases
from benchmarks.guardian.reporting import write_json, write_raw_measurements
from benchmarks.guardian.runner import (
    LocalSUTServer,
    _command_version,
    _file_sha256,
    _git_output,
    _tree_sha256,
    _validate_optional_sha256,
)
from benchmarks.guardian.suts.booking_api import create_app as create_booking_app
from benchmarks.guardian.suts.inventory_api import create_app as create_inventory_app
from benchmarks.guardian.suts.orders_api import create_app as create_orders_app
from benchmarks.guardian.suts.products_api import create_app as create_products_app
from benchmarks.guardian.suts.users_api import create_app as create_users_app

_SUT_FACTORIES: dict[str, Callable[[str], FastAPI]] = {
    "users_api": create_users_app,
    "orders_api": create_orders_app,
    "products_api": create_products_app,
    "inventory_api": create_inventory_app,
    "booking_api": create_booking_app,
}


class E2BenchmarkRunner:
    """执行 60 Contract Cases 与 55 Runtime Cases 的 Stage E2。."""

    def __init__(
        self,
        *,
        oasdiff_path: Path,
        schemathesis_path: Path,
        config: BenchmarkConfig | None = None,
        report_root: Path = E2_LATEST_REPORT_ROOT,
        oasdiff_archive_sha256: str | None = None,
        console: Console | None = None,
    ) -> None:
        """绑定固定工具、配置和报告目录。."""
        self._oasdiff_path = oasdiff_path.resolve()
        self._schemathesis_path = schemathesis_path.resolve()
        self._config = config or BenchmarkConfig()
        self._report_root = report_root.resolve()
        self._oasdiff_archive_sha256 = _validate_optional_sha256(oasdiff_archive_sha256)
        self._console = console or Console()

    async def run(self) -> dict[str, Any]:
        """执行 E2 并持续写 raw measurements，最后生成聚合报告。."""
        started_at = datetime.now(UTC)
        run_id = f"guardian-e2-{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        self._report_root.mkdir(parents=True, exist_ok=True)
        metadata = await asyncio.to_thread(self._collect_metadata)
        mutation_cases = load_e2_mutation_cases()
        contract_cases = ensure_contract_dataset()
        if len(mutation_cases) < 55 or len({case.sut_id for case in mutation_cases}) != 5:
            raise ValueError("Stage E2 requires 5 SUTs and at least 55 runtime cases including clean controls")
        if len(contract_cases) < 60:
            raise ValueError("Stage E2 requires at least 60 contract cases")
        mutation_heads = materialize_all(mutation_cases)
        contract_heads = materialize_all_contract_heads(contract_cases)
        core_root = BENCHMARK_ROOT.parents[1] / "app" / "services" / "guardian"
        core_hash_before = _tree_sha256(core_root)
        oasdiff, schemathesis, full_cli, graph = self._build_systems()

        contract_raw_path = self._report_root / "contract_raw_results.jsonl"
        runtime_raw_path = self._report_root / "raw_results.jsonl"
        contract_raw_path.write_text("", encoding="utf-8")
        runtime_raw_path.write_text("", encoding="utf-8")
        contract_measurements: list[ContractMeasurement] = []
        runtime_measurements: list[RawMeasurement] = []

        for index, case in enumerate(contract_cases):
            measurement = await self._measure_contract(run_id, case, contract_heads[case.case_id], metadata, oasdiff)
            contract_measurements.append(measurement)
            _append_model(contract_raw_path, measurement)
            if (index + 1) % 10 == 0:
                self._console.print(f"[cyan]E2 contract[/cyan] {index + 1}/{len(contract_cases)} complete")

        for case_index, case in enumerate(mutation_cases):
            base_path = resolve_project_path(case.base_spec)
            head_path = mutation_heads[case.case_id]
            operations = prepare_openapi_spec(str(head_path), self._config.max_artifact_bytes).operations
            if len(operations) != 8:
                raise ValueError(f"E2 SUT must expose exactly 8 operations: {case.sut_id}")
            factory = _SUT_FACTORIES[case.sut_id]
            with LocalSUTServer(factory(case.target_variant)) as server:
                for warmup in range(self._config.warmup_runs):
                    for system in self._rotated_systems(case_index + warmup):
                        item = await self._measure_runtime(
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
                        runtime_measurements.append(item)
                        _append_model(runtime_raw_path, item)
                for repetition in range(self._config.repetitions):
                    for system in self._rotated_systems(case_index + repetition + self._config.random_seed):
                        item = await self._measure_runtime(
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
                        runtime_measurements.append(item)
                        _append_model(runtime_raw_path, item)
            self._console.print(f"[cyan]E2 runtime[/cyan] {case_index + 1}/{len(mutation_cases)} {case.case_id}")

        completed_at = datetime.now(UTC)
        metrics, missed, contract_misses = aggregate_e2_metrics(
            mutation_cases,
            tuple(runtime_measurements),
            contract_cases,
            tuple(contract_measurements),
        )
        core_hash_after = _tree_sha256(core_root)
        summary = {
            "benchmark": "API Guardian Effectiveness Benchmark",
            "stage": "E2",
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "configuration": self._config.model_dump(mode="json"),
            "metadata": metadata.model_dump(mode="json"),
            "dataset": {
                "runtime_case_count": len(mutation_cases),
                "mutation_count": sum(case.ground_truth_bug_present for case in mutation_cases),
                "detectable_mutation_count": sum(
                    case.expected_detectable_by_current_oracle for case in mutation_cases
                ),
                "semantic_negative_control_count": sum(
                    case.ground_truth_bug_present and not case.expected_detectable_by_current_oracle
                    for case in mutation_cases
                ),
                "clean_control_count": sum(not case.ground_truth_bug_present for case in mutation_cases),
                "contract_case_count": len(contract_cases),
                "sut_count": len({case.sut_id for case in mutation_cases}),
            },
            "metrics": metrics,
            "production_guardian_core_unchanged_during_run": core_hash_before == core_hash_after,
        }
        manifest = self._manifest(
            run_id,
            started_at,
            completed_at,
            mutation_cases,
            mutation_heads,
            contract_cases,
            contract_heads,
            metadata,
            core_hash_before,
            core_hash_after,
        )
        write_raw_measurements(runtime_raw_path, tuple(runtime_measurements))
        _write_contract_measurements(contract_raw_path, tuple(contract_measurements))
        write_json(self._report_root / "summary.json", summary)
        write_e2_summary_csv(self._report_root / "summary.csv", summary)
        write_json(self._report_root / "missed_cases.json", [item.model_dump(mode="json") for item in missed])
        write_json(self._report_root / "contract_scope_misses.json", contract_misses)
        write_json(self._report_root / "run_manifest.json", manifest)
        (self._report_root / "benchmark_report.md").write_text(
            render_e2_report(summary, mutation_cases, contract_cases), encoding="utf-8"
        )
        (self._report_root / "experiment_log.md").write_text(
            render_e2_experiment_log(summary, core_hash_before == core_hash_after), encoding="utf-8"
        )
        return summary

    def _build_systems(
        self,
    ) -> tuple[OasdiffAdapter, SchemathesisAdapter, FullSchemathesisCli, CompiledStateGraph]:
        """构造共享固定工具和禁用 LLM 的真实 Guardian Graph。."""
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
        graph = create_guardian_graph(policy=policy, oasdiff=oasdiff, schemathesis=schemathesis)
        return oasdiff, schemathesis, full_cli, graph

    async def _measure_contract(
        self,
        run_id: str,
        case: ContractCase,
        head_path: Path,
        metadata: ReproducibilityMetadata,
        oasdiff: OasdiffAdapter,
    ) -> ContractMeasurement:
        """执行真实 oasdiff、normalization 与冻结 Scope Selector。."""
        started = time.perf_counter()
        errors: tuple[str, ...] = ()
        changes = ()
        selected: tuple[str, ...] = ()
        fallback = False
        try:
            result = await oasdiff.compare(
                str(resolve_project_path(case.base_spec)), str(head_path), self._config.max_scan_artifact_bytes
            )
            changes = normalize_contract_changes(result.candidates)
            head_operations = prepare_openapi_spec(str(head_path), self._config.max_artifact_bytes).operations
            selection = select_test_scope(changes, head_operations)
            selected = tuple(operation.key for operation in selection.operations)
            fallback = selection.fallback_to_all
            verdict = determine_contract_verdict(changes, has_error=False)
        except GuardianException as exc:
            head_operations = ()
            verdict = GuardianVerdict.ERROR
            errors = (exc.code.value,)
        return ContractMeasurement(
            run_id=f"{run_id}:{case.case_id}:contract",
            measured_at=datetime.now(UTC),
            case_id=case.case_id,
            sut_id=case.sut_id,
            change_type=case.change_type,
            duration_seconds=time.perf_counter() - started,
            expected_contract_verdict=case.expected_contract_verdict,
            actual_contract_verdict=verdict,
            expected_affected_operations=case.expected_affected_operations,
            selected_operations=selected,
            total_operations=len(head_operations),
            fallback_to_all=fallback,
            normalized_change_count=len(changes),
            error_codes=errors,
            metadata=metadata,
        )

    async def _measure_runtime(
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
        oasdiff: OasdiffAdapter,
        schemathesis: SchemathesisAdapter,
        full_cli: FullSchemathesisCli,
        graph: CompiledStateGraph,
    ) -> RawMeasurement:
        """计时一个 Runtime Case/System，并读取真实 SUT 请求计数。."""
        await server.reset_counter()
        operation = partial(
            self._execute_system,
            system,
            run_id,
            case,
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
        started = time.perf_counter()
        try:
            result = await operation()
        except GuardianException as exc:
            result = _error_result(system, len(operations), exc.code.value)
        duration = time.perf_counter() - started
        requests = await server.request_count()
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
            http_requests_issued=requests,
            bug_detected=result.bug_detected,
            error_codes=result.error_codes,
            metadata=metadata,
        )

    async def _execute_system(
        self,
        system: BenchmarkSystem,
        run_id: str,
        case: MutationCase,
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
    ) -> SystemResult:
        """运行四个公平 Runtime 系统之一。."""
        if system == BenchmarkSystem.FULL_SCHEMATHESIS:
            return await full_cli.run(
                str(head_path), server.base_url, operations, self._config.max_scan_artifact_bytes
            )
        if system == BenchmarkSystem.FULL_PIPELINE:
            contract = await run_oasdiff_only(
                oasdiff,
                str(base_path),
                str(head_path),
                max_scan_artifact_bytes=self._config.max_scan_artifact_bytes,
                total_operations=len(operations),
            )
            regression = await full_cli.run(
                str(head_path), server.base_url, operations, self._config.max_scan_artifact_bytes
            )
            final = determine_final_verdict(contract.contract_verdict, regression.regression_verdict, has_error=False)
            return regression.model_copy(
                update={"contract_verdict": contract.contract_verdict, "final_verdict": final}
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
        raise ValueError(f"unsupported E2 system: {system}")

    def _rotated_systems(self, offset: int) -> tuple[BenchmarkSystem, ...]:
        """固定轮换运行顺序，减少系统顺序偏差。."""
        rotation = offset % len(E2_PERFORMANCE_SYSTEMS)
        return E2_PERFORMANCE_SYSTEMS[rotation:] + E2_PERFORMANCE_SYSTEMS[:rotation]

    def _collect_metadata(self) -> ReproducibilityMetadata:
        """验证版本并采集不含凭据的复现环境。."""
        if OASDIFF_VERSION not in _command_version((str(self._oasdiff_path), "--version")):
            raise RuntimeError("oasdiff version mismatch")
        if SCHEMATHESIS_VERSION not in _command_version((str(self._schemathesis_path), "--version")):
            raise RuntimeError("Schemathesis version mismatch")
        return ReproducibilityMetadata(
            benchmark_version="E2.0",
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
                "checks": self._config.schemathesis_check,
                "phases": self._config.schemathesis_phases,
                "max_examples": self._config.max_examples,
                "generation_deterministic": self._config.generation_deterministic,
                "max_redirects": self._config.max_redirects,
                "request_timeout_seconds": self._config.request_timeout_seconds,
            },
            deepseek_model=None,
        )

    def _manifest(
        self,
        run_id: str,
        started_at: datetime,
        completed_at: datetime,
        mutation_cases: tuple[MutationCase, ...],
        mutation_heads: dict[str, Path],
        contract_cases: tuple[ContractCase, ...],
        contract_heads: dict[str, Path],
        metadata: ReproducibilityMetadata,
        core_hash_before: str,
        core_hash_after: str,
    ) -> dict[str, Any]:
        """记录输入哈希、工具契约、环境和生产 Core 哈希。."""
        return {
            "run_id": run_id,
            "stage": "E2",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "metadata": metadata.model_dump(mode="json"),
            "configuration": self._config.model_dump(mode="json"),
            "executed_systems": [system.value for system in E2_PERFORMANCE_SYSTEMS],
            "command_contracts": {
                "oasdiff": "oasdiff breaking --format json --allow-external-refs=false --fail-on ERR -- BASE HEAD",
                "full_schemathesis": "st run HEAD --url TARGET [fixed production settings; no include filter]",
                "scoped_schemathesis": "st run HEAD --url TARGET --include-operation-id ID [same fixed settings]",
            },
            "mutation_dataset_files": {
                path.name: _file_sha256(path)
                for path in sorted((BENCHMARK_ROOT / "datasets" / "mutation").glob("e2_*.jsonl"))
            },
            "contract_dataset_sha256": _file_sha256(CASES_PATH),
            "mutation_cases": [
                {
                    "case_id": case.case_id,
                    "base_spec_sha256": _file_sha256(resolve_project_path(case.base_spec)),
                    "head_spec_sha256": _file_sha256(mutation_heads[case.case_id]),
                }
                for case in mutation_cases
            ],
            "contract_cases": [
                {
                    "case_id": case.case_id,
                    "base_spec_sha256": _file_sha256(resolve_project_path(case.base_spec)),
                    "head_spec_sha256": _file_sha256(contract_heads[case.case_id]),
                }
                for case in contract_cases
            ],
            "production_guardian_core_sha256_before": core_hash_before,
            "production_guardian_core_sha256_after": core_hash_after,
            "production_guardian_core_unchanged": core_hash_before == core_hash_after,
            "secret_fields_recorded": [],
        }


def _error_result(system: BenchmarkSystem, total_operations: int, error_code: str) -> SystemResult:
    """把工具基础设施异常保留为 ERROR，而不是 runtime FAIL。."""
    return SystemResult(
        contract_verdict=GuardianVerdict.ERROR if system == BenchmarkSystem.FULL_PIPELINE else None,
        regression_verdict=GuardianVerdict.ERROR,
        final_verdict=GuardianVerdict.ERROR,
        total_operations=total_operations,
        operations_tested=0,
        bug_detected=False,
        error_codes=(error_code,),
    )


def _append_model(path: Path, value: RawMeasurement | ContractMeasurement) -> None:
    """每次 measurement 完成后立即写入 JSONL。."""
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def _write_contract_measurements(path: Path, values: tuple[ContractMeasurement, ...]) -> None:
    """以稳定顺序写回完整 Contract raw data。."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")


def e2_source_sha256() -> str:
    """返回当前 E2 runner 文件哈希，供外部审计辅助使用。."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
