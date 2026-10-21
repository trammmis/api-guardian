"""Stage E2 的 Contract、Runtime、Scope、Performance 与可靠性指标。."""

from collections import defaultdict
from statistics import fmean
from typing import Any

from benchmarks.guardian.metrics import ratio
from benchmarks.guardian.models import (
    BenchmarkSystem,
    ContractCase,
    ContractMeasurement,
    MissedCase,
    MutationCase,
    RawMeasurement,
    RatioMetric,
)
from benchmarks.guardian.statistics import describe

E2_PERFORMANCE_SYSTEMS = (
    BenchmarkSystem.FULL_SCHEMATHESIS,
    BenchmarkSystem.FULL_PIPELINE,
    BenchmarkSystem.ORACLE_SCOPE,
    BenchmarkSystem.GUARDIAN_DETERMINISTIC,
)


def aggregate_e2_metrics(
    mutation_cases: tuple[MutationCase, ...],
    measurements: tuple[RawMeasurement, ...],
    contract_cases: tuple[ContractCase, ...],
    contract_measurements: tuple[ContractMeasurement, ...],
) -> tuple[dict[str, Any], tuple[MissedCase, ...], tuple[dict[str, Any], ...]]:
    """聚合 E2；所有 accuracy 保留 numerator 和 denominator。."""
    measured = tuple(item for item in measurements if not item.warmup)
    first = _first_by_case_system(measured)
    mutation_by_id = {case.case_id: case for case in mutation_cases}
    systems: dict[str, Any] = {}
    for system in E2_PERFORMANCE_SYSTEMS:
        items = tuple(first[(case.case_id, system)] for case in mutation_cases)
        true_positives = sum(
            item.bug_detected and mutation_by_id[item.case_id].ground_truth_bug_present for item in items
        )
        false_positives = sum(
            item.bug_detected and not mutation_by_id[item.case_id].ground_truth_bug_present for item in items
        )
        bugs = sum(case.ground_truth_bug_present for case in mutation_cases)
        detectable = tuple(case for case in mutation_cases if case.expected_detectable_by_current_oracle)
        detectable_hits = sum(first[(case.case_id, system)].bug_detected for case in detectable)
        systems[system.value] = {
            "bug_detection_recall": ratio(true_positives, bugs).model_dump(mode="json"),
            "bug_detection_precision": ratio(true_positives, true_positives + false_positives).model_dump(mode="json"),
            "in_scope_mutation_recall": ratio(detectable_hits, len(detectable)).model_dump(mode="json"),
            "overall_mutation_recall": ratio(true_positives, bugs).model_dump(mode="json"),
            "false_positive_rate": ratio(
                false_positives, sum(not case.ground_truth_bug_present for case in mutation_cases)
            ).model_dump(mode="json"),
        }

    performance: dict[str, Any] = {}
    for system in E2_PERFORMANCE_SYSTEMS:
        items = tuple(item for item in measured if item.system == system)
        performance[system.value] = {
            "wall_clock_duration_seconds": describe([item.duration_seconds for item in items]).model_dump(mode="json"),
            "operations_tested": describe([float(item.operations_tested) for item in items]).model_dump(mode="json"),
            "http_requests_issued": describe([float(item.http_requests_issued) for item in items]).model_dump(
                mode="json"
            ),
        }

    contract_scope = _contract_scope(contract_cases, contract_measurements)
    runtime_scope = _runtime_scope(mutation_cases, first)
    contract_correct = sum(
        item.actual_contract_verdict == item.expected_contract_verdict for item in contract_measurements
    )
    missed = _missed_mutations(mutation_cases, first)
    contract_misses = tuple(
        {
            "case_id": item.case_id,
            "change_type": item.change_type,
            "expected_contract_verdict": item.expected_contract_verdict.value,
            "actual_contract_verdict": item.actual_contract_verdict.value,
            "expected_affected_operations": list(item.expected_affected_operations),
            "selected_operations": list(item.selected_operations),
            "fallback_to_all": item.fallback_to_all,
        }
        for item in contract_measurements
        if item.actual_contract_verdict != item.expected_contract_verdict
        or not set(item.expected_affected_operations).issubset(item.selected_operations)
    )
    return (
        {
            "contract_verdict_accuracy": ratio(contract_correct, len(contract_measurements)).model_dump(mode="json"),
            "systems": systems,
            "contract_scope": contract_scope,
            "runtime_scope": runtime_scope,
            "performance": performance,
            "paired_guardian_vs_full": {
                "runtime_reduction": describe(
                    _paired_reductions(measured, BenchmarkSystem.FULL_SCHEMATHESIS)
                ).model_dump(mode="json")
            },
            "paired_guardian_vs_full_pipeline": {
                "runtime_reduction": describe(_paired_reductions(measured, BenchmarkSystem.FULL_PIPELINE)).model_dump(
                    mode="json"
                )
            },
            "reliability": {
                "deterministic_reproducibility": _reproducibility(measured).model_dump(mode="json"),
                "error_record_count": sum(bool(item.error_codes) for item in measured),
            },
            "missed_mutation_count": len(missed),
            "contract_or_scope_miss_count": len(contract_misses),
        },
        missed,
        contract_misses,
    )


def _contract_scope(
    cases: tuple[ContractCase, ...],
    measurements: tuple[ContractMeasurement, ...],
) -> dict[str, Any]:
    """计算人工 Contract Ground Truth 对 Scope Selector 的指标。."""
    by_id = {item.case_id: item for item in measurements}
    intersection = 0
    expected_count = 0
    selected_count = 0
    reductions: list[float] = []
    overheads: list[float] = []
    fallback_count = 0
    for case in cases:
        if not case.expected_affected_operations:
            continue
        item = by_id[case.case_id]
        expected = set(case.expected_affected_operations)
        selected = set(item.selected_operations)
        intersection += len(expected & selected)
        expected_count += len(expected)
        selected_count += len(selected)
        reductions.append(1 - len(selected) / item.total_operations)
        overheads.append(float(max(len(selected) - len(expected), 0)))
        fallback_count += item.fallback_to_all
    return {
        "recall": ratio(intersection, expected_count).model_dump(mode="json"),
        "precision": ratio(intersection, selected_count).model_dump(mode="json"),
        "mean_scope_reduction": fmean(reductions) if reductions else None,
        "mean_oracle_overhead_operations": fmean(overheads) if overheads else None,
        "oracle_efficiency_ratio": expected_count / selected_count if selected_count else None,
        "fallback_to_all_cases": fallback_count,
    }


def _runtime_scope(
    cases: tuple[MutationCase, ...],
    first: dict[tuple[str, BenchmarkSystem], RawMeasurement],
) -> dict[str, Any]:
    """计算 Runtime Dataset 上实际被执行的 Guardian scope。."""
    intersection = 0
    expected_count = 0
    selected_count = 0
    reductions: list[float] = []
    for case in cases:
        item = first[(case.case_id, BenchmarkSystem.GUARDIAN_DETERMINISTIC)]
        expected = set(case.ground_truth_affected_operations)
        selected = set(item.selected_operations)
        intersection += len(expected & selected)
        expected_count += len(expected)
        selected_count += len(selected)
        reductions.append(1 - len(selected) / item.total_operations)
    return {
        "recall": ratio(intersection, expected_count).model_dump(mode="json"),
        "precision": ratio(intersection, selected_count).model_dump(mode="json"),
        "mean_scope_reduction": fmean(reductions) if reductions else None,
    }


def _first_by_case_system(
    measurements: tuple[RawMeasurement, ...],
) -> dict[tuple[str, BenchmarkSystem], RawMeasurement]:
    """为 accuracy 选择固定的第一个 measured repetition。."""
    result: dict[tuple[str, BenchmarkSystem], RawMeasurement] = {}
    for item in sorted(measurements, key=lambda value: (value.case_id, value.system, value.repetition)):
        result.setdefault((item.case_id, item.system), item)
    return result


def _paired_reductions(measurements: tuple[RawMeasurement, ...], baseline: BenchmarkSystem) -> list[float]:
    """按 case/repetition 配对 Guardian 与指定 Full baseline。."""
    by_key = {(item.case_id, item.system, item.repetition): item for item in measurements}
    reductions: list[float] = []
    for item in measurements:
        if item.system != baseline or item.duration_seconds <= 0:
            continue
        guardian = by_key.get((item.case_id, BenchmarkSystem.GUARDIAN_DETERMINISTIC, item.repetition))
        if guardian is not None:
            reductions.append(1 - guardian.duration_seconds / item.duration_seconds)
    return reductions


def _reproducibility(measurements: tuple[RawMeasurement, ...]) -> RatioMetric:
    """统计每个 case/system 的五次 verdict/scope 签名一致性。."""
    grouped: dict[tuple[str, BenchmarkSystem], list[RawMeasurement]] = defaultdict(list)
    for item in measurements:
        grouped[(item.case_id, item.system)].append(item)
    matches = 0
    total = 0
    for items in grouped.values():
        ordered = sorted(items, key=lambda item: item.repetition)
        reference = _signature(ordered[0])
        for item in ordered:
            matches += _signature(item) == reference
            total += 1
    return ratio(matches, total)


def _signature(item: RawMeasurement) -> tuple[Any, ...]:
    """排除时间，只保留确定性结果字段。."""
    return (
        item.contract_verdict,
        item.regression_verdict,
        item.final_verdict,
        item.selected_operations,
        item.operations_tested,
        item.bug_detected,
        item.error_codes,
    )


def _missed_mutations(
    cases: tuple[MutationCase, ...],
    first: dict[tuple[str, BenchmarkSystem], RawMeasurement],
) -> tuple[MissedCase, ...]:
    """输出每个 Guardian 漏检的真实 Bug，clean controls 不计为漏检。."""
    missed: list[MissedCase] = []
    for case in cases:
        item = first[(case.case_id, BenchmarkSystem.GUARDIAN_DETERMINISTIC)]
        if not case.ground_truth_bug_present or item.bug_detected:
            continue
        expected = set(case.ground_truth_affected_operations)
        selected = set(item.selected_operations)
        if item.error_codes:
            reason = f"Guardian execution ERROR: {', '.join(item.error_codes)}"
        elif not expected.issubset(selected):
            reason = "Ground-truth operation was not selected"
        elif not case.expected_detectable_by_current_oracle:
            reason = "Schema-oriented oracle cannot detect this business-semantic regression"
        else:
            reason = "Configured response schema check did not expose the mutation"
        missed.append(
            MissedCase(
                case_id=case.case_id,
                mutation=case.mutation_type,
                affected_operations=case.ground_truth_affected_operations,
                expected_detectable=case.expected_detectable_by_current_oracle,
                selected_operations=item.selected_operations,
                why_not_selected_or_detected=reason,
            )
        )
    return tuple(missed)
