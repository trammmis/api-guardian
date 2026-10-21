"""把 E1 原始测量写成 JSON、CSV、Markdown 与实验日志。."""

import csv
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmarks.guardian.models import MutationCase, RawMeasurement


def write_json(path: Path, value: Any) -> None:
    """以稳定、UTF-8、可审计格式写 JSON。."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_raw_measurements(path: Path, measurements: tuple[RawMeasurement, ...]) -> None:
    """完整保存 per-run JSONL，不只保存聚合比例。."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for measurement in measurements:
            handle.write(json.dumps(measurement.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")


def write_summary_csv(path: Path, summary: Mapping[str, Any]) -> None:
    """输出便于电子表格比较的核心 E1 指标。."""
    rows: list[dict[str, Any]] = []
    contract = summary["metrics"]["contract_verdict_accuracy"]
    rows.append(_ratio_row("contract", "contract_verdict_accuracy", "oasdiff_only", contract))
    for system, metrics in summary["metrics"]["systems"].items():
        for metric_name in (
            "bug_detection_recall",
            "bug_detection_precision",
            "in_scope_mutation_recall",
            "overall_mutation_recall",
        ):
            rows.append(_ratio_row("regression", metric_name, system, metrics[metric_name]))
    scope = summary["metrics"]["scope"]
    rows.extend(
        (
            _ratio_row("scope", "scope_recall", "guardian_deterministic", scope["recall"]),
            _ratio_row("scope", "scope_precision", "guardian_deterministic", scope["precision"]),
            _value_row("scope", "mean_scope_reduction", "guardian_deterministic", scope["mean_scope_reduction"]),
            _value_row(
                "scope",
                "mean_oracle_overhead_operations",
                "guardian_deterministic",
                scope["mean_oracle_overhead_operations"],
            ),
            _value_row(
                "scope",
                "oracle_efficiency_ratio",
                "guardian_deterministic",
                scope["oracle_efficiency_ratio"],
            ),
        )
    )
    for system, values in summary["metrics"]["performance"].items():
        for family in ("wall_clock_duration_seconds", "operations_tested", "http_requests_issued"):
            statistics = values[family]
            for statistic in ("median", "p95", "mean", "std"):
                rows.append(
                    {
                        "category": "performance",
                        "metric": f"{family}_{statistic}",
                        "system": system,
                        "value": statistics[statistic],
                        "numerator": "",
                        "denominator": statistics["sample_size"],
                    }
                )
    runtime_reduction = summary["metrics"]["paired_guardian_vs_full"]["runtime_reduction"]
    rows.append(
        {
            "category": "performance",
            "metric": "paired_runtime_reduction_median",
            "system": "guardian_vs_full",
            "value": runtime_reduction["median"],
            "numerator": "",
            "denominator": runtime_reduction["sample_size"],
        }
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        fieldnames = ("category", "metric", "system", "value", "numerator", "denominator")
        writer.writerow(fieldnames)
        writer.writerows([[row[field] for field in fieldnames] for row in rows])


def render_benchmark_report(summary: Mapping[str, Any], cases: tuple[MutationCase, ...]) -> str:
    """生成直接由数据驱动、不过度营销的 E1 报告。."""
    metrics = summary["metrics"]
    scope = metrics["scope"]
    systems = metrics["systems"]
    performance = metrics["performance"]
    paired = metrics["paired_guardian_vs_full"]["runtime_reduction"]
    in_scope = systems["guardian_deterministic"]["in_scope_mutation_recall"]
    overall = systems["guardian_deterministic"]["overall_mutation_recall"]
    contract = metrics["contract_verdict_accuracy"]
    reproducibility = metrics["reliability"]["deterministic_reproducibility"]
    detectable = sum(case.expected_detectable_by_current_oracle for case in cases)
    negative = len(cases) - detectable
    rows = _comparison_table(performance, systems)
    return f"""# API Guardian Effectiveness Benchmark

## Experimental Setup

- Stage: E1
- Run ID: `{summary["run_id"]}`
- Cases: {len(cases)} ({detectable} schema-detectable, {negative} negative controls)
- Controlled SUTs: 2, each with 8 OpenAPI operations
- Measured repetitions per case/system: {summary["configuration"]["repetitions"]}
- Warm-up runs per case/system: {summary["configuration"]["warmup_runs"]} (excluded from timing aggregates)
- Random seed: {summary["configuration"]["random_seed"]}
- Schemathesis: `{summary["metadata"]["schemathesis_version"]}`
- oasdiff: `{summary["metadata"]["oasdiff_version"]}`
- LLM: disabled; E1 does not evaluate System F

Every raw measurement is in `raw_results.jsonl`; the manifest records Git, Python, OS, CPU, dataset hashes and fixed tool parameters. No API key is recorded.

## Systems Compared

- Baseline A — oasdiff only: contract verdict only.
- Baseline B — Full Schemathesis: all 8 Head operations, production-equivalent fixed test parameters.
- Baseline C — oasdiff + Full Schemathesis: implemented, but repeated timing is intentionally deferred beyond E1.
- Baseline D — Oracle Scope + Schemathesis: one human Ground Truth operation per case.
- System E — API Guardian deterministic: real frozen graph with LLM disabled.
- System F — API Guardian full: not run in E1.

Breaking detection is supplied by oasdiff and runtime schema checking by Schemathesis. The Guardian-specific comparison here concerns change-aware scope, orchestration and deterministic result classification.

## Dataset

The dataset contains 10 fixed mutations across `users_api` and `orders_api`. All cases contain a real injected bug. Eight violate the Head response schema; two deliberately return schema-valid but semantically wrong data. Ground Truth is dataset-authored rather than copied from oasdiff output.

An initial pilot found that response-required additions, response-enum narrowing and `number -> integer` are not necessarily breaking from the client-compatibility direction. Those cases retain their runtime mutation and add a separate, explicit breaking type change on the same operation solely to make the change-aware scope experiment well-defined. This decision is recorded rather than hidden.

## RQ1 Detection Correctness

- Contract verdict accuracy: **{_ratio(contract)}**.
- Guardian in-scope mutation recall: **{_ratio(in_scope)}**.
- Guardian overall mutation recall, including semantic negative controls: **{_ratio(overall)}**.

The difference between in-scope and overall recall is expected evidence of the current schema-oriented test oracle boundary, not a dataset exclusion.

## RQ2 Regression Efficiency

North Star result for this E1 sample: at in-scope bug recall **{_percent(in_scope["value"])}**, mean operation scope reduction is **{_percent(scope["mean_scope_reduction"])}** and paired median wall-clock reduction versus Full Schemathesis is **{_percent(paired["median"])}**.

| Metric | Full Schemathesis | Oracle Scope | API Guardian | Difference (Guardian - Full) |
| --- | ---: | ---: | ---: | ---: |
{rows}

Guardian timing includes oasdiff and graph orchestration while Full Schemathesis timing does not; this makes the reported Guardian-vs-Full reduction conservative, but Baseline C repeated timing remains needed for a cleaner scope-only estimate in E2.

## RQ3 Scope Quality

- Scope Recall: **{_ratio(scope["recall"])}**.
- Scope Precision: **{_ratio(scope["precision"])}**.
- Mean Scope Reduction: **{_percent(scope["mean_scope_reduction"])}**.
- Mean Oracle Overhead: **{_number(scope["mean_oracle_overhead_operations"])} operations/case**.
- Oracle Efficiency Ratio: **{_number(scope["oracle_efficiency_ratio"])}**.

## RQ4 LLM Diagnosis Utility

Not evaluated in E1. LLM and Human Evaluation belong to E4. No human scores were simulated.

## RQ5 Reliability and Cost

- Deterministic reproducibility across repeated E1 runs: **{_ratio(reproducibility)}**.
- LLM calls/tokens/cost: not applicable because LLM is disabled.
- Error injection and checkpoint recovery benchmark: deferred; existing product tests are rerun only as non-regression gates.

## Limitations

1. E1 has only 2 synthetic controlled SUTs and 10 mutations; it cannot support broad external-validity claims.
2. All business operations are GET endpoints without authentication or stateful workflows.
3. Bug detection precision has no true no-bug controls in E1; its observed value must not be interpreted as a false-positive study.
4. Current test oracle is schema-oriented and does not detect arbitrary business-logic regressions.
5. SUTs run on loopback in the same host; runtime figures primarily measure local process and Schemathesis overhead.
6. HTTP request counts come from SUT-side counters and exclude health/reset/metrics control endpoints.

## Reproducibility

Run from the repository root:

```powershell
uv run python -m benchmarks.guardian.run_benchmark --oasdiff-path <oasdiff-1.28.0.exe>
```

See `run_manifest.json`, `experiment_log.md`, `raw_results.jsonl`, `summary.json`, `summary.csv` and `missed_cases.json`. Generated Head Specs are kept under `datasets/mutation/generated/` for audit.
"""


def render_experiment_log(summary: Mapping[str, Any], production_core_unchanged: bool) -> str:
    """记录运行过程、设计修正和结果文件，不伪造实验步骤。."""
    metadata = summary["metadata"]
    config = summary["configuration"]
    return f"""# Stage E1 Experiment Log

## Run

- run_id: `{summary["run_id"]}`
- started_at: `{summary["started_at"]}`
- completed_at: `{summary["completed_at"]}`
- random_seed: `{config["random_seed"]}`
- repetitions: `{config["repetitions"]}`
- warmup_runs: `{config["warmup_runs"]}`
- production_guardian_core_unchanged_during_run: `{str(production_core_unchanged).lower()}`

## Environment

- guardian_git_commit: `{metadata["guardian_git_commit"]}`
- guardian_worktree_dirty: `{str(metadata["guardian_worktree_dirty"]).lower()}`
- Python: `{metadata["python_version"]}`
- OS: `{metadata["operating_system"]}`
- logical_cpu_count: `{metadata["cpu_logical_count"]}`
- oasdiff: `{metadata["oasdiff_version"]}`
- oasdiff_archive_sha256: `{metadata["oasdiff_archive_sha256"]}`
- Schemathesis: `{metadata["schemathesis_version"]}`
- DeepSeek: not used

## Method Decisions

1. Production Guardian Core remained frozen; the runner imports its public adapters/graph without patching their behavior.
2. Full, Oracle and Guardian runs share Head Spec, target variant, Schemathesis version/check/phases/max examples/deterministic mode/request timeout.
3. Warm-up measurements are retained in raw JSONL but excluded from aggregates.
4. Full/Oracle/Guardian order is deterministically rotated by case and repetition to reduce fixed ordering bias.
5. Pilot oasdiff inspection showed several response-side changes were not breaking in the consumer-compatibility direction. A separate breaking type change was added on the same operation for those cases; the runtime fault itself was not changed or removed.
6. The two semantic Negative Controls remain in all aggregate overall-recall calculations.
7. An invalid pilot exposed a Schemathesis 4.24.3 `CanonicalSchema.is_satisfiable` AttributeError when path parameter `example` values were present. E1 removed unnecessary examples from its SUT Specs; Guardian correctly classified the invalid pilot as ERROR, and none of those measurements enter the final run.

## Output Integrity

Dataset and generated Spec SHA256 values are stored in `run_manifest.json`. `raw_results.jsonl` contains every warm-up and measured repetition. No API key, environment dump, target response body or raw tool output is persisted.
"""


def _comparison_table(performance: Mapping[str, Any], systems: Mapping[str, Any]) -> str:
    """构造包含 detection、scope cost 与 runtime 的系统对比表。."""
    names = ("full_schemathesis", "oracle_scope", "guardian_deterministic")
    duration = [performance[name]["wall_clock_duration_seconds"] for name in names]
    operations = [performance[name]["operations_tested"] for name in names]
    requests = [performance[name]["http_requests_issued"] for name in names]
    bug_recall = [systems[name]["bug_detection_recall"]["value"] for name in names]
    in_scope_recall = [systems[name]["in_scope_mutation_recall"]["value"] for name in names]
    return "\n".join(
        (
            f"| Bug Detection Recall | {_percent(bug_recall[0])} | {_percent(bug_recall[1])} | {_percent(bug_recall[2])} | {_percentage_point_difference(bug_recall[2], bug_recall[0])} |",
            f"| In-Scope Mutation Recall | {_percent(in_scope_recall[0])} | {_percent(in_scope_recall[1])} | {_percent(in_scope_recall[2])} | {_percentage_point_difference(in_scope_recall[2], in_scope_recall[0])} |",
            f"| Median runtime (s) | {_number(duration[0]['median'])} | {_number(duration[1]['median'])} | {_number(duration[2]['median'])} | {_difference(duration[2]['median'], duration[0]['median'])} |",
            f"| P95 runtime (s) | {_number(duration[0]['p95'])} | {_number(duration[1]['p95'])} | {_number(duration[2]['p95'])} | {_difference(duration[2]['p95'], duration[0]['p95'])} |",
            f"| Median operations tested | {_number(operations[0]['median'])} | {_number(operations[1]['median'])} | {_number(operations[2]['median'])} | {_difference(operations[2]['median'], operations[0]['median'])} |",
            f"| Median HTTP requests | {_number(requests[0]['median'])} | {_number(requests[1]['median'])} | {_number(requests[2]['median'])} | {_difference(requests[2]['median'], requests[0]['median'])} |",
        )
    )


def _ratio_row(category: str, metric: str, system: str, value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "category": category,
        "metric": metric,
        "system": system,
        "value": value["value"],
        "numerator": value["numerator"],
        "denominator": value["denominator"],
    }


def _value_row(category: str, metric: str, system: str, value: Any) -> dict[str, Any]:
    return {
        "category": category,
        "metric": metric,
        "system": system,
        "value": value,
        "numerator": "",
        "denominator": "",
    }


def _ratio(value: Mapping[str, Any]) -> str:
    return f"{value['numerator']}/{value['denominator']} ({_percent(value['value'])})"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def _number(value: float | int | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _difference(guardian: float | None, full: float | None) -> str:
    """显示 Guardian 减 Full 的绝对差。."""
    if guardian is None or full is None:
        return "n/a"
    return f"{guardian - full:+.4f}"


def _percentage_point_difference(guardian: float | None, full: float | None) -> str:
    """显示 recall 的百分点差。."""
    if guardian is None or full is None:
        return "n/a"
    return f"{(guardian - full) * 100:+.2f} pp"
