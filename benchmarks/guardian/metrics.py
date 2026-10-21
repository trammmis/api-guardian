"""Stage E1 的 correctness、scope、performance 与 missed-case 指标。."""

from collections import defaultdict
from collections.abc import Iterable
from statistics import fmean
from typing import Any

from app.services.guardian.models import GuardianVerdict
from benchmarks.guardian.models import (
    BenchmarkSystem,
    MissedCase,
    MutationCase,
    RatioMetric,
    RawMeasurement,
)
from benchmarks.guardian.statistics import describe

PERFORMANCE_SYSTEMS = (
    BenchmarkSystem.FULL_SCHEMATHESIS,
    BenchmarkSystem.ORACLE_SCOPE,
    BenchmarkSystem.GUARDIAN_DETERMINISTIC,
)


def ratio(numerator: int, denominator: int) -> RatioMetric:
    """构造保留分子和分母的比率。."""
    return RatioMetric(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator else None,
    )


def aggregate_metrics(
    cases: tuple[MutationCase, ...],
    measurements: tuple[RawMeasurement, ...],
) -> tuple[dict[str, Any], tuple[MissedCase, ...]]:
    """从 per-case raw measurements 生成透明聚合，不隐藏失败案例。."""
    measured = tuple(item for item in measurements if not item.warmup)
    first_by_case_system = _first_by_case_system(measured)
    case_by_id = {case.case_id: case for case in cases}

    contract_correct = 0
    for case in cases:
        item = first_by_case_system[(case.case_id, BenchmarkSystem.OASDIFF_ONLY)]
        contract_correct += item.contract_verdict == case.expected_contract_verdict

    system_metrics: dict[str, Any] = {}
    for system in PERFORMANCE_SYSTEMS:
        system_items = tuple(first_by_case_system[(case.case_id, system)] for case in cases)
        true_positives = sum(
            item.bug_detected and case_by_id[item.case_id].ground_truth_bug_present for item in system_items
        )
        false_positives = sum(
            item.bug_detected and not case_by_id[item.case_id].ground_truth_bug_present for item in system_items
        )
        all_bugs = sum(case.ground_truth_bug_present for case in cases)
        detectable_cases = tuple(case for case in cases if case.expected_detectable_by_current_oracle)
        detectable_hits = sum(first_by_case_system[(case.case_id, system)].bug_detected for case in detectable_cases)
        system_metrics[system.value] = {
            "bug_detection_recall": ratio(true_positives, all_bugs).model_dump(mode="json"),
            "bug_detection_precision": ratio(
                true_positives,
                true_positives + false_positives,
            ).model_dump(mode="json"),
            "in_scope_mutation_recall": ratio(detectable_hits, len(detectable_cases)).model_dump(mode="json"),
            "overall_mutation_recall": ratio(true_positives, all_bugs).model_dump(mode="json"),
        }

    scope_intersection = 0
    scope_selected = 0
    scope_expected = 0
    scope_reductions: list[float] = []
    oracle_overheads: list[float] = []
    for case in cases:
        item = first_by_case_system[(case.case_id, BenchmarkSystem.GUARDIAN_DETERMINISTIC)]
        selected = set(item.selected_operations)
        expected = set(case.ground_truth_affected_operations)
        scope_intersection += len(selected & expected)
        scope_selected += len(selected)
        scope_expected += len(expected)
        scope_reductions.append(1 - len(selected) / item.total_operations)
        oracle_overheads.append(float(len(selected) - len(expected)))

    performance: dict[str, Any] = {}
    for system in PERFORMANCE_SYSTEMS:
        items = tuple(item for item in measured if item.system == system)
        performance[system.value] = {
            "wall_clock_duration_seconds": describe([item.duration_seconds for item in items]).model_dump(mode="json"),
            "operations_tested": describe([float(item.operations_tested) for item in items]).model_dump(mode="json"),
            "http_requests_issued": describe([float(item.http_requests_issued) for item in items]).model_dump(
                mode="json"
            ),
        }

    paired_reductions = _paired_runtime_reductions(measured)
    reproducibility = _deterministic_reproducibility(measured)
    missed = _missed_cases(cases, first_by_case_system)
    summary = {
        "contract_verdict_accuracy": ratio(contract_correct, len(cases)).model_dump(mode="json"),
        "systems": system_metrics,
        "scope": {
            "recall": ratio(scope_intersection, scope_expected).model_dump(mode="json"),
            "precision": ratio(scope_intersection, scope_selected).model_dump(mode="json"),
            "mean_scope_reduction": fmean(scope_reductions) if scope_reductions else None,
            "mean_oracle_overhead_operations": fmean(oracle_overheads) if oracle_overheads else None,
            "oracle_efficiency_ratio": scope_expected / scope_selected if scope_selected else None,
        },
        "performance": performance,
        "paired_guardian_vs_full": {
            "runtime_reduction": describe(paired_reductions).model_dump(mode="json"),
            "paired_sample_size": len(paired_reductions),
        },
        "reliability": {
            "deterministic_reproducibility": reproducibility.model_dump(mode="json"),
        },
        "missed_case_count": len(missed),
    }
    return summary, missed


def _first_by_case_system(
    measurements: Iterable[RawMeasurement],
) -> dict[tuple[str, BenchmarkSystem], RawMeasurement]:
    """为 case-level accuracy 选择第一个非 warm-up 重复。."""
    result: dict[tuple[str, BenchmarkSystem], RawMeasurement] = {}
    for item in sorted(measurements, key=lambda value: (value.case_id, value.system, value.repetition)):
        result.setdefault((item.case_id, item.system), item)
    return result


def _paired_runtime_reductions(measurements: tuple[RawMeasurement, ...]) -> list[float]:
    """按同 case/repetition 配对 Full 与 Guardian runtime。."""
    by_key = {(item.case_id, item.system, item.repetition): item for item in measurements}
    reductions: list[float] = []
    for key, full in by_key.items():
        case_id, system, repetition = key
        if system != BenchmarkSystem.FULL_SCHEMATHESIS or full.duration_seconds <= 0:
            continue
        guardian = by_key.get((case_id, BenchmarkSystem.GUARDIAN_DETERMINISTIC, repetition))
        if guardian is not None:
            reductions.append(1 - guardian.duration_seconds / full.duration_seconds)
    return reductions


def _deterministic_reproducibility(measurements: tuple[RawMeasurement, ...]) -> RatioMetric:
    """计算同 case/system 重复运行与第一次结果签名一致的比例。."""
    grouped: dict[tuple[str, BenchmarkSystem], list[RawMeasurement]] = defaultdict(list)
    for item in measurements:
        if item.system in PERFORMANCE_SYSTEMS:
            grouped[(item.case_id, item.system)].append(item)
    matches = 0
    total = 0
    for items in grouped.values():
        ordered = sorted(items, key=lambda item: item.repetition)
        reference = _result_signature(ordered[0])
        for item in ordered:
            matches += _result_signature(item) == reference
            total += 1
    return ratio(matches, total)


def _result_signature(item: RawMeasurement) -> tuple[Any, ...]:
    """只比较确定性结果，不把 runtime 纳入结果签名。."""
    return (
        item.contract_verdict,
        item.regression_verdict,
        item.final_verdict,
        item.selected_operations,
        item.operations_tested,
        item.bug_detected,
        item.error_codes,
    )


def _missed_cases(
    cases: tuple[MutationCase, ...],
    first_by_case_system: dict[tuple[str, BenchmarkSystem], RawMeasurement],
) -> tuple[MissedCase, ...]:
    """输出每个未检出 bug，区分 scope miss、oracle miss 与基础设施错误。."""
    missed: list[MissedCase] = []
    for case in cases:
        item = first_by_case_system[(case.case_id, BenchmarkSystem.GUARDIAN_DETERMINISTIC)]
        if not case.ground_truth_bug_present or item.bug_detected:
            continue
        expected = set(case.ground_truth_affected_operations)
        selected = set(item.selected_operations)
        if item.error_codes:
            reason = f"Guardian execution ERROR: {', '.join(item.error_codes)}"
        elif not expected.issubset(selected):
            reason = "Ground-truth affected operation was not selected"
        elif not case.expected_detectable_by_current_oracle:
            reason = "Schema-oriented oracle cannot detect this business-semantic regression"
        elif item.regression_verdict == GuardianVerdict.PASS:
            reason = "Operation was selected, but the configured schema check did not expose the injected mutation"
        else:
            reason = "Mutation was not classified as a regression failure"
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
