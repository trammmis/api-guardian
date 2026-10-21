"""Stage E1 Harness 的离线自检。."""

from datetime import UTC, datetime

from app.services.guardian.models import GuardianVerdict
from app.services.guardian.security import prepare_openapi_spec
from benchmarks.guardian.metrics import aggregate_metrics
from benchmarks.guardian.models import BenchmarkSystem, RawMeasurement, ReproducibilityMetadata
from benchmarks.guardian.mutations.apply import materialize_all, resolve_project_path
from benchmarks.guardian.mutations.definitions import load_mutation_cases


def test_dataset_has_two_suts_ten_ground_truth_cases_and_negative_controls() -> None:
    """E1 Dataset 数量、唯一性与 Negative Control 不可被静默删除。."""
    cases = load_mutation_cases()

    assert len(cases) == 10
    assert len({case.case_id for case in cases}) == 10
    assert {case.sut_id for case in cases} == {"users_api", "orders_api"}
    assert sum(case.expected_detectable_by_current_oracle for case in cases) == 8
    assert sum(not case.expected_detectable_by_current_oracle for case in cases) == 2
    assert all(case.ground_truth_bug_present for case in cases)


def test_materialized_specs_are_stable_and_each_sut_has_eight_operations() -> None:
    """Head 生成可重复，且两个 SUT 都满足 operation 数量边界。."""
    cases = load_mutation_cases()
    first = materialize_all(cases)
    first_payloads = {case_id: path.read_bytes() for case_id, path in first.items()}
    second = materialize_all(cases)

    assert first_payloads == {case_id: path.read_bytes() for case_id, path in second.items()}
    for case in cases:
        base = prepare_openapi_spec(str(resolve_project_path(case.base_spec)), 5 * 1024 * 1024)
        head = prepare_openapi_spec(str(second[case.case_id]), 5 * 1024 * 1024)
        assert len(base.operations) == 8
        assert len(head.operations) == 8


def test_metrics_keep_negative_controls_in_overall_recall() -> None:
    """8 个 schema hit 不能被错误报告成 10/10 Overall Recall。."""
    cases = load_mutation_cases()
    metadata = _metadata()
    measurements: list[RawMeasurement] = []
    for case in cases:
        detected = case.expected_detectable_by_current_oracle
        measurements.append(
            _measurement(
                case.case_id,
                case.sut_id,
                BenchmarkSystem.OASDIFF_ONLY,
                metadata,
                contract_verdict=GuardianVerdict.FAIL,
                bug_detected=False,
                selected_operations=(),
                operations_tested=0,
            )
        )
        for system in (
            BenchmarkSystem.FULL_SCHEMATHESIS,
            BenchmarkSystem.ORACLE_SCOPE,
            BenchmarkSystem.GUARDIAN_DETERMINISTIC,
        ):
            measurements.append(
                _measurement(
                    case.case_id,
                    case.sut_id,
                    system,
                    metadata,
                    regression_verdict=GuardianVerdict.FAIL if detected else GuardianVerdict.PASS,
                    final_verdict=GuardianVerdict.FAIL,
                    bug_detected=detected,
                    selected_operations=case.ground_truth_affected_operations
                    if system != BenchmarkSystem.FULL_SCHEMATHESIS
                    else tuple(f"GET /operation/{index}" for index in range(8)),
                    operations_tested=1 if system != BenchmarkSystem.FULL_SCHEMATHESIS else 8,
                )
            )

    summary, missed = aggregate_metrics(cases, tuple(measurements))
    guardian = summary["systems"][BenchmarkSystem.GUARDIAN_DETERMINISTIC.value]

    assert guardian["in_scope_mutation_recall"] == {"numerator": 8, "denominator": 8, "value": 1.0}
    assert guardian["overall_mutation_recall"] == {"numerator": 8, "denominator": 10, "value": 0.8}
    assert len(missed) == 2
    assert all("schema-oriented" in item.why_not_selected_or_detected.casefold() for item in missed)


def _measurement(
    case_id: str,
    sut_id: str,
    system: BenchmarkSystem,
    metadata: ReproducibilityMetadata,
    *,
    contract_verdict: GuardianVerdict | None = None,
    regression_verdict: GuardianVerdict | None = None,
    final_verdict: GuardianVerdict | None = None,
    bug_detected: bool,
    selected_operations: tuple[str, ...],
    operations_tested: int,
) -> RawMeasurement:
    """构造单重复 synthetic raw measurement。."""
    return RawMeasurement(
        run_id=f"test-{case_id}-{system.value}",
        measured_at=datetime.now(UTC),
        case_id=case_id,
        sut_id=sut_id,
        system=system,
        repetition=0,
        warmup=False,
        duration_seconds=1.0,
        contract_verdict=contract_verdict,
        regression_verdict=regression_verdict,
        final_verdict=final_verdict,
        selected_operations=selected_operations,
        total_operations=8,
        operations_tested=operations_tested,
        http_requests_issued=operations_tested,
        bug_detected=bug_detected,
        metadata=metadata,
    )


def _metadata() -> ReproducibilityMetadata:
    """构造不含 Secret 的测试元数据。."""
    return ReproducibilityMetadata(
        benchmark_version="test",
        guardian_git_commit="0" * 40,
        guardian_worktree_dirty=True,
        python_version="3.13",
        operating_system="test",
        cpu_logical_count=1,
        random_seed=1,
        oasdiff_version="1.28.0",
        schemathesis_version="4.24.3",
        schemathesis_parameters={},
    )
