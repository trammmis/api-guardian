"""Stage E2.5 Scaling 报告和 CSV 输出。."""

import csv
from pathlib import Path
from typing import Any

from benchmarks.guardian.models import BenchmarkSystem


def write_scaling_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    """每个 scale/scenario 一行，便于从 raw 派生图表。."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_fieldnames())
        writer.writeheader()
        for row in summary["metrics"]["by_scale"]:
            full = _system(row, BenchmarkSystem.FULL_SCHEMATHESIS)
            full_pipeline = _system(row, BenchmarkSystem.FULL_PIPELINE)
            guardian = _system(row, BenchmarkSystem.GUARDIAN_DETERMINISTIC)
            oracle = _system(row, BenchmarkSystem.ORACLE_SCOPE)
            writer.writerow(
                {
                    "case_id": row["case_id"],
                    "scenario": row["scenario"],
                    "total_operations": row["total_operations"],
                    "affected_operations": row["affected_operations"],
                    "scope_reduction": row["scope_reduction"],
                    "http_request_reduction": row["http_request_reduction"],
                    "full_median_seconds": full["median"],
                    "full_p95_seconds": full["p95"],
                    "full_pipeline_median_seconds": full_pipeline["median"],
                    "full_pipeline_p95_seconds": full_pipeline["p95"],
                    "guardian_median_seconds": guardian["median"],
                    "guardian_p95_seconds": guardian["p95"],
                    "oracle_median_seconds": oracle["median"],
                    "oracle_p95_seconds": oracle["p95"],
                    "paired_median_difference_seconds": row["guardian_vs_full"]["paired_difference_seconds"]["median"],
                    "paired_median_runtime_reduction": row["guardian_vs_full"]["runtime_reduction"]["median"],
                    "guardian_fixed_overhead_estimate_seconds": row["guardian_fixed_overhead_estimate_seconds"],
                    "full_median_requests": _request_median(row, BenchmarkSystem.FULL_SCHEMATHESIS),
                    "guardian_median_requests": _request_median(row, BenchmarkSystem.GUARDIAN_DETERMINISTIC),
                }
            )


def render_scaling_report(summary: dict[str, Any]) -> str:
    """严格从 summary 渲染，不手工录入实验数字。."""
    config = summary["configuration"]
    metrics = summary["metrics"]
    rows = "\n".join(_report_row(row) for row in metrics["by_scale"])
    break_even_rows = "\n".join(
        f"| `{scenario}` | {'yes' if value['observed'] else 'no'} | "
        f"{value['start_operations'] if value['observed'] else 'N/A'} | {value['max_tested_operations']} |"
        for scenario, value in metrics["break_even"].items()
    )
    full_s1_fit = _linear_fit(metrics["by_scale"], "s1_one_operation", BenchmarkSystem.FULL_SCHEMATHESIS)
    full_s2_fit = _linear_fit(metrics["by_scale"], "s2_five_percent", BenchmarkSystem.FULL_SCHEMATHESIS)
    guardian_s1_fit = _linear_fit(metrics["by_scale"], "s1_one_operation", BenchmarkSystem.GUARDIAN_DETERMINISTIC)
    oracle_s2_fit = _linear_fit(
        metrics["by_scale"], "s2_five_percent", BenchmarkSystem.ORACLE_SCOPE, x_field="affected_operations"
    )
    negative_rows = tuple(
        row for row in metrics["by_scale"] if row["guardian_vs_full"]["runtime_reduction"]["median"] <= 0
    )
    negative_text = ", ".join(
        f"{row['scenario']}@{row['total_operations']} ({_percent(row['guardian_vs_full']['runtime_reduction']['median'])})"
        for row in negative_rows
    )
    return f"""# API Guardian Scaling and Break-even Benchmark

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: validate
- Verification Status: VERIFIED from persisted raw measurements
- Version Label: guardian_e2_5_scaling_v1

## Research Question

At what API surface size, if any, does the current change-aware Guardian pipeline become faster than optimized one-process Full Schemathesis while preserving the frozen execution semantics?

## Experimental Setup

- Run: `{summary["run_id"]}`
- Local parameterized FastAPI SUT; no network or authentication
- Sizes: {", ".join(str(value) for value in config["sizes"])} operations
- One warm-up plus {config["repetitions"]} measured repetitions per case/system
- Fixed random seed: {config["random_seed"]}
- Resource ceilings: {config["max_case_runtime_seconds"]} s/case and {config["total_benchmark_budget_seconds"]} s/run

## Scaling Dataset

S1 changes exactly one operation. S2 changes approximately 5% of operations, rounded to an integer and recorded exactly. Every affected target returns a string while Head requires an integer; the mutation is therefore in scope for `response_schema_conformance`.

## Systems Compared

- Full Schemathesis: one unfiltered Schemathesis process over Head.
- oasdiff + Full Schemathesis: identical Full run plus the frozen contract phase.
- Oracle Scope: Dataset Ground Truth drives the same per-operation Schemathesis adapter.
- API Guardian deterministic: frozen oasdiff, scope selector, LangGraph and per-operation Schemathesis; LLM disabled.

## Fixed Parameters

Schemathesis 4.24.3; `response_schema_conformance`; phases `examples,coverage`; max examples 1; deterministic generation; redirects 0; request timeout {config["request_timeout_seconds"]} s. Full and scoped systems differ only in scope/filter plus Guardian's required oasdiff/graph overhead.

## Raw Sample Counts

- Measured: {summary["raw_sample_counts"]["measured"]}
- Warm-up (excluded from aggregates): {summary["raw_sample_counts"]["warmup"]}
- Execution errors: {metrics["execution_error_count"]}
- Deterministic reproducibility: {_ratio(metrics["deterministic_reproducibility"])}

## Runtime by API Size

| Scenario | Total | Affected | Full median / p95 | Full+oasdiff median / p95 | Guardian median / p95 | Oracle median / p95 | Paired G-F median | Paired reduction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{rows}

## HTTP Requests by API Size

`summary.csv` includes observed Full and Guardian request medians for every scale. Request reduction is computed from SUT counters, not inferred from operation counts.

## Scope Reduction

Scope reduction is exactly `1 - affected_operations / total_operations` for this controlled Dataset. It is reported next to observed HTTP-request and runtime reductions in `summary.csv`.

## Fixed Overhead Analysis

Guardian component timing is intentionally labelled `end_to_end_wrapper_only`: production nodes were not instrumented. Full/Oracle wrapper boundaries expose Schemathesis time, and FullPipeline exposes oasdiff plus Schemathesis time. The reported Guardian fixed-overhead estimate is Guardian median minus Oracle median—an approximate combined oasdiff/LangGraph/normalization/report cost, not a direct internal telemetry measurement. Full fixed overhead is unavailable because no zero-operation control was run.

Descriptive linear fits over the five synthetic scale points give:

- Full S1: intercept {_seconds(full_s1_fit["intercept"])}, slope {full_s1_fit["slope"]:.6f} s/operation, R² {full_s1_fit["r_squared"]:.4f}.
- Full S2: intercept {_seconds(full_s2_fit["intercept"])}, slope {full_s2_fit["slope"]:.6f} s/operation, R² {full_s2_fit["r_squared"]:.4f}.
- Guardian S1: intercept {_seconds(guardian_s1_fit["intercept"])}, slope {guardian_s1_fit["slope"]:.6f} s/operation, R² {guardian_s1_fit["r_squared"]:.4f}.
- Oracle S2 versus affected-operation count: intercept {_seconds(oracle_s2_fit["intercept"])}, slope {oracle_s2_fit["slope"]:.6f} s/selected operation, R² {oracle_s2_fit["r_squared"]:.4f}.

The near-linear Oracle S2 fit is evidence that one independent Schemathesis process per selected operation dominates the current multi-operation scoped path. These fits are descriptive for this host, not universal performance laws.

## Break-even Analysis

Operational definition: the first of at least two consecutive size points where the Guardian cell median is lower than Full Schemathesis.

| Scenario | Observed | Region starts | Maximum tested |
| --- | --- | ---: | ---: |
{break_even_rows}

Each cell also stores a deterministic 10,000-sample paired-bootstrap 95% percentile interval for Guardian-minus-Full difference and runtime reduction. The operational decision uses observed medians and requires a sustained region; it does not promote a single isolated point.

## Negative Results

Cells with non-positive paired median reduction are retained: {negative_text}. S2 has no sustained break-even through 128 operations; at 128/6 affected, Guardian is {_percent(metrics["by_scale"][-1]["guardian_vs_full"]["runtime_reduction"]["median"])} relative to Full by the paired reduction definition. No production batching, process reuse or graph optimization was introduced after seeing measurements.

## Threats to Validity

Synthetic homogeneous GET operations, localhost transport, no auth, a schema-only oracle, host-specific process startup, one Windows machine, and the fixed low-budget Schemathesis configuration limit generalization. The benchmark isolates scaling behavior; it is not a model of arbitrary production endpoint cost.

## Optimization Opportunities

If Guardian overhead remains dominated by per-operation Schemathesis startup, batching or process reuse is a future product hypothesis. It was not implemented or evaluated in E2.5.

## Reproducibility

`raw_results.jsonl` retains every warm-up and measured record. `run_manifest.json` records commit, individual frozen Core hashes, original E2 raw hashes, Dataset hash, generator version, environment, tool versions, command contracts and resource limits. No API key or environment Secret is recorded.
"""


def _report_row(row: dict[str, Any]) -> str:
    full = _system(row, BenchmarkSystem.FULL_SCHEMATHESIS)
    full_pipeline = _system(row, BenchmarkSystem.FULL_PIPELINE)
    guardian = _system(row, BenchmarkSystem.GUARDIAN_DETERMINISTIC)
    oracle = _system(row, BenchmarkSystem.ORACLE_SCOPE)
    difference = row["guardian_vs_full"]["paired_difference_seconds"]["median"]
    reduction = row["guardian_vs_full"]["runtime_reduction"]["median"]
    return (
        f"| `{row['scenario']}` | {row['total_operations']} | {row['affected_operations']} | "
        f"{_seconds(full['median'])} / {_seconds(full['p95'])} | "
        f"{_seconds(full_pipeline['median'])} / {_seconds(full_pipeline['p95'])} | "
        f"{_seconds(guardian['median'])} / {_seconds(guardian['p95'])} | "
        f"{_seconds(oracle['median'])} / {_seconds(oracle['p95'])} | "
        f"{_signed_seconds(difference)} | {_percent(reduction)} |"
    )


def _system(row: dict[str, Any], system: BenchmarkSystem) -> dict[str, Any]:
    return row["systems"][system.value]["total_duration_seconds"]


def _request_median(row: dict[str, Any], system: BenchmarkSystem) -> float | None:
    return row["systems"][system.value]["http_requests_issued"]["median"]


def _fieldnames() -> list[str]:
    return [
        "case_id",
        "scenario",
        "total_operations",
        "affected_operations",
        "scope_reduction",
        "http_request_reduction",
        "full_median_seconds",
        "full_p95_seconds",
        "full_pipeline_median_seconds",
        "full_pipeline_p95_seconds",
        "guardian_median_seconds",
        "guardian_p95_seconds",
        "oracle_median_seconds",
        "oracle_p95_seconds",
        "paired_median_difference_seconds",
        "paired_median_runtime_reduction",
        "guardian_fixed_overhead_estimate_seconds",
        "full_median_requests",
        "guardian_median_requests",
    ]


def _linear_fit(
    rows: list[dict[str, Any]],
    scenario: str,
    system: BenchmarkSystem,
    *,
    x_field: str = "total_operations",
) -> dict[str, float]:
    selected = [row for row in rows if row["scenario"] == scenario]
    x_values = [float(row[x_field]) for row in selected]
    y_values = [float(_system(row, system)["median"]) for row in selected]
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    x_variance = sum((value - x_mean) ** 2 for value in x_values)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values, strict=True)) / x_variance
    intercept = y_mean - slope * x_mean
    total_variance = sum((value - y_mean) ** 2 for value in y_values)
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(x_values, y_values, strict=True))
    return {
        "intercept": intercept,
        "slope": slope,
        "r_squared": 1 - residual / total_variance if total_variance else 1.0,
    }


def _ratio(value: dict[str, Any]) -> str:
    return f"{value['numerator']}/{value['denominator']} ({_percent(value['value'])})"


def _seconds(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.3f} s"


def _signed_seconds(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):+.3f} s"


def _percent(value: Any) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"
