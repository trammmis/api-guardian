"""Stage E2.5 Scaling 的描述统计、配对差异与 break-even 判定。."""

import random
import statistics
from collections import defaultdict
from typing import Any

from benchmarks.guardian.e2_5_models import ScalingCase, ScalingMeasurement, ScalingScenario
from benchmarks.guardian.models import BenchmarkSystem
from benchmarks.guardian.statistics import describe, percentile

_SYSTEMS = (
    BenchmarkSystem.FULL_SCHEMATHESIS,
    BenchmarkSystem.FULL_PIPELINE,
    BenchmarkSystem.ORACLE_SCOPE,
    BenchmarkSystem.GUARDIAN_DETERMINISTIC,
)


def aggregate_scaling_metrics(
    cases: tuple[ScalingCase, ...],
    measurements: tuple[ScalingMeasurement, ...],
    *,
    seed: int,
) -> dict[str, Any]:
    """从 measured raw data 生成 per-scale 聚合，warm-up 永不参与。."""
    measured = tuple(item for item in measurements if not item.warmup)
    _validate_completeness(cases, measured)
    rows = [_aggregate_case(case, measured, seed=seed + index) for index, case in enumerate(cases)]
    return {
        "by_scale": rows,
        "break_even": {
            scenario.value: _break_even(rows, scenario)
            for scenario in (ScalingScenario.ONE_OPERATION, ScalingScenario.FIVE_PERCENT)
        },
        "deterministic_reproducibility": _reproducibility(measured),
        "execution_error_count": sum(bool(item.error_codes) for item in measured),
        "fixed_overhead_method": (
            "Guardian median minus Oracle median; approximate combined oasdiff, LangGraph, "
            "normalization/scope/report overhead because Guardian component timings are wrapper-only"
        ),
        "full_fixed_overhead_estimate": None,
        "full_fixed_overhead_status": "unavailable_without_zero_operation_control",
    }


def _aggregate_case(case: ScalingCase, measured: tuple[ScalingMeasurement, ...], *, seed: int) -> dict[str, Any]:
    grouped = {
        system: tuple(item for item in measured if item.case_id == case.case_id and item.system == system)
        for system in _SYSTEMS
    }
    systems: dict[str, Any] = {}
    for system, items in grouped.items():
        systems[system.value] = {
            "total_duration_seconds": describe([item.total_duration_seconds for item in items]).model_dump(
                mode="json"
            ),
            "oasdiff_duration_seconds": _optional_describe(items, "oasdiff_duration_seconds"),
            "schemathesis_duration_seconds": _optional_describe(items, "schemathesis_duration_seconds"),
            "scope_selection_duration_seconds": _optional_describe(items, "scope_selection_duration_seconds"),
            "report_orchestration_duration_seconds": _optional_describe(
                items, "report_orchestration_duration_seconds"
            ),
            "operations_tested": describe([float(item.operations_tested) for item in items]).model_dump(mode="json"),
            "http_requests_issued": describe([float(item.http_requests_issued) for item in items]).model_dump(
                mode="json"
            ),
            "process_count": describe([float(item.process_count) for item in items]).model_dump(mode="json"),
            "error_count": sum(bool(item.error_codes) for item in items),
        }
    pairs = _paired(grouped[BenchmarkSystem.FULL_SCHEMATHESIS], grouped[BenchmarkSystem.GUARDIAN_DETERMINISTIC])
    differences = [guardian.total_duration_seconds - full.total_duration_seconds for full, guardian in pairs]
    reductions = [1 - guardian.total_duration_seconds / full.total_duration_seconds for full, guardian in pairs]
    difference_ci = _bootstrap_median_ci(differences, seed=seed)
    reduction_ci = _bootstrap_median_ci(reductions, seed=seed + 1000)
    full_requests = statistics.median(item.http_requests_issued for item in grouped[BenchmarkSystem.FULL_SCHEMATHESIS])
    guardian_requests = statistics.median(
        item.http_requests_issued for item in grouped[BenchmarkSystem.GUARDIAN_DETERMINISTIC]
    )
    guardian_median = systems[BenchmarkSystem.GUARDIAN_DETERMINISTIC.value]["total_duration_seconds"]["median"]
    oracle_median = systems[BenchmarkSystem.ORACLE_SCOPE.value]["total_duration_seconds"]["median"]
    return {
        "case_id": case.case_id,
        "scenario": case.scenario.value,
        "total_operations": case.total_operations,
        "affected_operations": len(case.affected_operations),
        "scope_reduction": 1 - len(case.affected_operations) / case.total_operations,
        "http_request_reduction": 1 - guardian_requests / full_requests if full_requests else None,
        "systems": systems,
        "guardian_vs_full": {
            "paired_sample_size": len(pairs),
            "paired_difference_seconds": describe(differences).model_dump(mode="json"),
            "paired_difference_bootstrap_95_ci": difference_ci,
            "runtime_reduction": describe(reductions).model_dump(mode="json"),
            "runtime_reduction_bootstrap_95_ci": reduction_ci,
            "guardian_faster_by_cell_median": guardian_median
            < systems[BenchmarkSystem.FULL_SCHEMATHESIS.value]["total_duration_seconds"]["median"],
        },
        "guardian_fixed_overhead_estimate_seconds": guardian_median - oracle_median,
    }


def _optional_describe(items: tuple[ScalingMeasurement, ...], field: str) -> dict[str, Any] | None:
    values = [getattr(item, field) for item in items if getattr(item, field) is not None]
    return describe(values).model_dump(mode="json") if values else None


def _paired(
    full: tuple[ScalingMeasurement, ...], guardian: tuple[ScalingMeasurement, ...]
) -> tuple[tuple[ScalingMeasurement, ScalingMeasurement], ...]:
    by_repetition = {item.repetition: item for item in guardian}
    return tuple((item, by_repetition[item.repetition]) for item in sorted(full, key=lambda value: value.repetition))


def _bootstrap_median_ci(values: list[float], *, seed: int, samples: int = 10_000) -> dict[str, Any]:
    """固定 seed 的 paired bootstrap；返回抽样方法和 95% percentile CI。."""
    if not values:
        return {"lower": None, "upper": None, "bootstrap_samples": 0, "seed": seed}
    generator = random.Random(seed)
    estimates = [statistics.median(generator.choice(values) for _ in range(len(values))) for _ in range(samples)]
    return {
        "lower": percentile(estimates, 0.025),
        "upper": percentile(estimates, 0.975),
        "bootstrap_samples": samples,
        "seed": seed,
    }


def _break_even(rows: list[dict[str, Any]], scenario: ScalingScenario) -> dict[str, Any]:
    """连续两个规模点的 cell median 都快于 Full 才认定 observed region。."""
    selected = sorted(
        (row for row in rows if row["scenario"] == scenario.value), key=lambda row: row["total_operations"]
    )
    faster = [bool(row["guardian_vs_full"]["guardian_faster_by_cell_median"]) for row in selected]
    for index in range(len(faster) - 1):
        if faster[index] and faster[index + 1]:
            return {
                "observed": True,
                "operational_definition": "first of at least two consecutive scale points where Guardian cell median < Full cell median",
                "start_operations": selected[index]["total_operations"],
                "confirmed_through_operations": selected[index + 1]["total_operations"],
                "max_tested_operations": selected[-1]["total_operations"],
            }
    return {
        "observed": False,
        "operational_definition": "first of at least two consecutive scale points where Guardian cell median < Full cell median",
        "start_operations": None,
        "confirmed_through_operations": None,
        "max_tested_operations": selected[-1]["total_operations"],
        "statement": f"No break-even observed up to {selected[-1]['total_operations']} operations.",
    }


def _validate_completeness(cases: tuple[ScalingCase, ...], measured: tuple[ScalingMeasurement, ...]) -> None:
    expected_repetitions = len({item.repetition for item in measured})
    grouped: dict[tuple[str, BenchmarkSystem], int] = defaultdict(int)
    for item in measured:
        grouped[(item.case_id, item.system)] += 1
    missing = [
        (case.case_id, system.value, grouped[(case.case_id, system)])
        for case in cases
        for system in _SYSTEMS
        if grouped[(case.case_id, system)] != expected_repetitions
    ]
    if missing:
        raise RuntimeError(f"incomplete scaling measurements: {missing}")


def _reproducibility(measured: tuple[ScalingMeasurement, ...]) -> dict[str, Any]:
    grouped: dict[tuple[str, BenchmarkSystem], list[ScalingMeasurement]] = defaultdict(list)
    for item in measured:
        grouped[(item.case_id, item.system)].append(item)
    numerator = denominator = 0
    for items in grouped.values():
        signatures = [_signature(item) for item in sorted(items, key=lambda value: value.repetition)]
        reference = signatures[0]
        numerator += sum(signature == reference for signature in signatures)
        denominator += len(signatures)
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator}


def _signature(item: ScalingMeasurement) -> tuple[Any, ...]:
    return (
        item.contract_verdict,
        item.regression_verdict,
        item.final_verdict,
        item.selected_operations,
        item.operations_tested,
        item.error_codes,
    )
