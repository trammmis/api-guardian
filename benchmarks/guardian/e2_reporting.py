"""Stage E2 JSON 聚合结果的 CSV、Markdown 报告和实验日志。."""

import csv
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmarks.guardian.models import ContractCase, MutationCase


def write_e2_summary_csv(path: Path, summary: Mapping[str, Any]) -> None:
    """把 E2 核心指标输出为便于复核的扁平 CSV。."""
    metrics = summary["metrics"]
    rows: list[tuple[str, str, str, Any, Any, Any]] = []
    _append_ratio(rows, "contract", "contract_verdict_accuracy", "oasdiff_only", metrics["contract_verdict_accuracy"])
    for system, system_metrics in metrics["systems"].items():
        for name, value in system_metrics.items():
            _append_ratio(rows, "regression", name, system, value)
    for scope_name in ("contract_scope", "runtime_scope"):
        scope = metrics[scope_name]
        _append_ratio(rows, "scope", f"{scope_name}_recall", "guardian_deterministic", scope["recall"])
        _append_ratio(rows, "scope", f"{scope_name}_precision", "guardian_deterministic", scope["precision"])
        for name, value in scope.items():
            if name not in {"recall", "precision"}:
                rows.append(("scope", f"{scope_name}_{name}", "guardian_deterministic", value, "", ""))
    for system, values in metrics["performance"].items():
        for family, statistics in values.items():
            for statistic in ("median", "p95", "mean", "std"):
                rows.append(
                    (
                        "performance",
                        f"{family}_{statistic}",
                        system,
                        statistics[statistic],
                        "",
                        statistics["sample_size"],
                    )
                )
    for comparison in ("paired_guardian_vs_full", "paired_guardian_vs_full_pipeline"):
        value = metrics[comparison]["runtime_reduction"]
        for statistic in ("median", "p95", "mean", "std"):
            rows.append(
                ("performance", f"{comparison}_{statistic}", comparison, value[statistic], "", value["sample_size"])
            )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("category", "metric", "system", "value", "numerator", "denominator"))
        writer.writerows(rows)


def render_e2_report(
    summary: Mapping[str, Any],
    mutation_cases: tuple[MutationCase, ...],
    contract_cases: tuple[ContractCase, ...],
) -> str:
    """从真实 summary 生成不含营销推断的 E2 报告。."""
    metrics = summary["metrics"]
    dataset = summary["dataset"]
    systems = metrics["systems"]
    performance = metrics["performance"]
    guardian = systems["guardian_deterministic"]
    contract_scope = metrics["contract_scope"]
    runtime_scope = metrics["runtime_scope"]
    full_reduction = metrics["paired_guardian_vs_full"]["runtime_reduction"]
    pipeline_reduction = metrics["paired_guardian_vs_full_pipeline"]["runtime_reduction"]
    reproducibility = metrics["reliability"]["deterministic_reproducibility"]
    request_reduction = _reduction(
        performance["guardian_deterministic"]["http_requests_issued"]["median"],
        performance["full_schemathesis"]["http_requests_issued"]["median"],
    )
    return f"""# API Guardian Effectiveness Benchmark — Stage E2

## Experimental Setup

- Run ID: `{summary["run_id"]}`
- Controlled SUTs: {dataset["sut_count"]}
- Runtime cases: {dataset["runtime_case_count"]} = {dataset["mutation_count"]} mutations + {dataset["clean_control_count"]} clean controls
- Detectable mutations: {dataset["detectable_mutation_count"]}
- Semantic negative controls: {dataset["semantic_negative_control_count"]}
- Contract cases: {dataset["contract_case_count"]}
- Repetitions: {summary["configuration"]["repetitions"]} measured + {summary["configuration"]["warmup_runs"]} warm-up
- Random seed: {summary["configuration"]["random_seed"]}
- oasdiff: `{summary["metadata"]["oasdiff_version"]}`
- Schemathesis: `{summary["metadata"]["schemathesis_version"]}`
- LLM: disabled

The raw Contract and Runtime measurements, input hashes, tool contracts, OS, Python, CPU and Git state are preserved next to this report. No API key is recorded.

## Systems Compared

- Full Schemathesis: one unfiltered CLI invocation over all Head operations.
- oasdiff + Full Schemathesis: the same contract detection followed by the same full-spec invocation.
- Oracle Scope: Dataset Ground Truth selects the regression operations.
- API Guardian deterministic: the frozen production graph with LLM disabled.

E2 corrects an E1 measurement limitation: Full Schemathesis now runs the entire Spec in one process. E1 used the production per-operation adapter for the Full baseline and therefore included repeated process startup overhead. E1 raw data is not rewritten.

## Dataset

Runtime Ground Truth is defined by {len(mutation_cases)} explicit cases across users, orders, products, inventory and booking APIs. Contract Ground Truth contains {len(contract_cases)} cases covering removed endpoints/methods/response fields/statuses, request and response type changes, required parameters, enum narrowing, compatible additions, documentation-only changes and shared component changes.

## RQ1 Detection Correctness

- Contract verdict accuracy: **{_ratio(metrics["contract_verdict_accuracy"])}**.
- Guardian Bug Detection Recall: **{_ratio(guardian["bug_detection_recall"])}**.
- Guardian In-Scope Mutation Recall: **{_ratio(guardian["in_scope_mutation_recall"])}**.
- Guardian Overall Mutation Recall: **{_ratio(guardian["overall_mutation_recall"])}**.
- Guardian Bug Detection Precision: **{_ratio(guardian["bug_detection_precision"])}**.
- Guardian clean-control false-positive rate: **{_ratio(guardian["false_positive_rate"])}**.

## RQ2 Regression Efficiency

At In-Scope Mutation Recall **{_percent(guardian["in_scope_mutation_recall"]["value"])}**, Runtime Scope Reduction is **{_percent(runtime_scope["mean_scope_reduction"])}** and median HTTP-request reduction is **{_percent(request_reduction)}**. Paired median runtime reduction is **{_percent(full_reduction["median"])}** versus Full Schemathesis and **{_percent(pipeline_reduction["median"])}** versus oasdiff + Full Schemathesis.

This is the E2 North Star result: the scoped system preserved all mutations detectable by the configured schema oracle and substantially reduced operations and requests, but it did **not** reduce wall-clock latency versus the optimized one-process Full baseline. Process startup and orchestration overhead dominate these small 8-operation SUTs.

| Metric | Full Schemathesis | oasdiff + Full | Oracle Scope | API Guardian |
| --- | ---: | ---: | ---: | ---: |
{_performance_rows(performance, systems)}

## RQ3 Scope Quality

Contract Dataset:

- Scope Recall: **{_ratio(contract_scope["recall"])}**.
- Scope Precision: **{_ratio(contract_scope["precision"])}**.
- Mean Scope Reduction: **{_percent(contract_scope["mean_scope_reduction"])}**.
- Mean Oracle Overhead: **{_number(contract_scope["mean_oracle_overhead_operations"])} operations/case**.
- Oracle Efficiency Ratio: **{_number(contract_scope["oracle_efficiency_ratio"])}**.
- fallback-to-all cases: **{contract_scope["fallback_to_all_cases"]}**.

Runtime Dataset:

- Scope Recall: **{_ratio(runtime_scope["recall"])}**.
- Scope Precision: **{_ratio(runtime_scope["precision"])}**.
- Mean Scope Reduction: **{_percent(runtime_scope["mean_scope_reduction"])}**.

## RQ4 LLM Diagnosis Utility

Not evaluated in E2. LLM and Human Evaluation remain E4 work; no human scores are simulated.

## RQ5 Reliability and Cost

- Deterministic reproducibility: **{_ratio(reproducibility)}**.
- Runtime error records: **{metrics["reliability"]["error_record_count"]}**.
- LLM latency, tokens and cost: not applicable because LLM is disabled.
- Dedicated fault injection and checkpoint reliability experiments remain outside E2.

## Misses and Limitations

- Missed Runtime mutations: **{metrics["missed_mutation_count"]}**; see `missed_cases.json`.
- Contract verdict or scope misses: **{metrics["contract_or_scope_miss_count"]}**; see `contract_scope_misses.json`.
- Current test oracle is schema-oriented and does not detect arbitrary business-logic regressions.
- Controlled SUTs are local, unauthenticated and intentionally small; results do not establish real-world external validity.
- Removed operations cannot be executed from the Head Spec and therefore expose a representation gap between Contract affected operations and executable regression scope.
- Full baseline and Guardian share all Schemathesis generation/check parameters; only scope/filtering and unavoidable orchestration differ.

## Reproducibility

Run from the repository root with verified local tools:

```powershell
uv run python -m benchmarks.guardian.run_e2 --oasdiff-path <oasdiff-1.28.0.exe> --schemathesis-path <st-4.24.3.exe>
```

See `run_manifest.json`, `experiment_log.md`, `contract_raw_results.jsonl`, `raw_results.jsonl`, `summary.json`, `summary.csv`, `missed_cases.json` and `contract_scope_misses.json`.
"""


def render_e2_experiment_log(summary: Mapping[str, Any], core_unchanged: bool) -> str:
    """保存 E2 执行时间、配置、方法修正与产物清单。."""
    return f"""# Stage E2 Experiment Log

## Run

- run_id: `{summary["run_id"]}`
- started_at: `{summary["started_at"]}`
- completed_at: `{summary["completed_at"]}`
- random_seed: `{summary["configuration"]["random_seed"]}`
- measured repetitions: `{summary["configuration"]["repetitions"]}`
- warm-up repetitions: `{summary["configuration"]["warmup_runs"]}`
- production Guardian Core unchanged: `{str(core_unchanged).lower()}`

## Reproducibility Environment

- Git commit: `{summary["metadata"]["guardian_git_commit"]}`
- Worktree dirty at run start: `{str(summary["metadata"]["guardian_worktree_dirty"]).lower()}`
- Python: `{summary["metadata"]["python_version"]}`
- OS: `{summary["metadata"]["operating_system"]}`
- CPU logical count: `{summary["metadata"]["cpu_logical_count"]}`
- oasdiff: `{summary["metadata"]["oasdiff_version"]}`
- Schemathesis: `{summary["metadata"]["schemathesis_version"]}`

## Method Decisions

1. E2 uses one unfiltered Schemathesis process for each Full baseline measurement. This removes E1's repeated process-startup amplification and is intentionally documented rather than rewriting E1.
2. Guardian and Oracle Scope keep the production per-operation strategy. In this Dataset each Runtime Case has one Ground Truth operation.
3. Five clean controls were added beyond the required 50 mutations so runtime false positives have a real denominator.
4. Contract response-status removal remains human-labelled breaking. If oasdiff disagrees, the mismatch is reported and not changed after observation.
5. Semantic negative controls remain in Overall Mutation Recall even though the configured schema oracle cannot detect them.

## Output Files

- `contract_raw_results.jsonl`: 60 per-case Contract measurements.
- `raw_results.jsonl`: all warm-up and measured Runtime executions.
- `summary.json` and `summary.csv`: aggregate values derived from raw data.
- `missed_cases.json`: every missed Runtime bug.
- `contract_scope_misses.json`: every Contract verdict or scope mismatch.
- `run_manifest.json`: hashes, versions, command contracts and environment.
- `benchmark_report.md`: RQ-oriented report generated from summary data.
- `metadata_correction_audit.md`: audit trail for the corrected full oasdiff archive SHA256.
- `../interrupted/`: incomplete runs retained for audit and excluded from aggregation.
"""


def _append_ratio(
    rows: list[tuple[str, str, str, Any, Any, Any]],
    category: str,
    name: str,
    system: str,
    value: Mapping[str, Any],
) -> None:
    """向 CSV 行列表追加保留分子分母的比率。."""
    rows.append((category, name, system, value["value"], value["numerator"], value["denominator"]))


def _performance_rows(performance: Mapping[str, Any], systems: Mapping[str, Any]) -> str:
    """生成四系统核心对比表。."""
    order = ("full_schemathesis", "oasdiff_plus_full_schemathesis", "oracle_scope", "guardian_deterministic")
    recall = " | ".join(_percent(systems[name]["bug_detection_recall"]["value"]) for name in order)
    in_scope = " | ".join(_percent(systems[name]["in_scope_mutation_recall"]["value"]) for name in order)
    median = " | ".join(_number(performance[name]["wall_clock_duration_seconds"]["median"]) for name in order)
    p95 = " | ".join(_number(performance[name]["wall_clock_duration_seconds"]["p95"]) for name in order)
    operations = " | ".join(_number(performance[name]["operations_tested"]["median"]) for name in order)
    requests = " | ".join(_number(performance[name]["http_requests_issued"]["median"]) for name in order)
    return "\n".join(
        (
            f"| Bug Detection Recall | {recall} |",
            f"| In-Scope Mutation Recall | {in_scope} |",
            f"| Median runtime (s) | {median} |",
            f"| P95 runtime (s) | {p95} |",
            f"| Median operations tested | {operations} |",
            f"| Median HTTP requests | {requests} |",
        )
    )


def _ratio(value: Mapping[str, Any]) -> str:
    """格式化 numerator/denominator/value。."""
    return f"{value['numerator']}/{value['denominator']} ({_percent(value['value'])})"


def _percent(value: Any) -> str:
    """格式化百分比，缺失时明确标记 N/A。."""
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"


def _number(value: Any) -> str:
    """格式化连续值，缺失时明确标记 N/A。."""
    return "N/A" if value is None else f"{float(value):.4f}"


def _reduction(selected: Any, full: Any) -> float | None:
    """计算相对减少量；基线为零时不制造除零结论。."""
    full_value = float(full)
    return None if full_value == 0 else 1 - float(selected) / full_value
