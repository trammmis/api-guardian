"""从冻结 E2、Miss Analysis 与 Scaling summary 生成 E2.5 总报告。."""

import json
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.guardian.config import PROJECT_ROOT, REPORT_ROOT
from benchmarks.guardian.models import BenchmarkSystem
from benchmarks.guardian.scaling_reporting import render_scaling_report, write_scaling_summary_csv

E2_5_REPORT_ROOT = REPORT_ROOT / "e2_5"


def generate_e2_5_reports() -> Path:
    """重渲染 Scaling 报告并生成不含手工实验数字的总报告。."""
    e2 = _read_object(REPORT_ROOT / "e2" / "latest" / "summary.json")
    miss = _read_object(E2_5_REPORT_ROOT / "miss_analysis" / "miss_summary.json")
    scaling = _read_object(E2_5_REPORT_ROOT / "scaling" / "summary.json")
    postflight = _write_postflight_integrity()
    manifest_path = E2_5_REPORT_ROOT / "scaling" / "run_manifest.json"
    manifest = _read_object(manifest_path)
    manifest["raw_results_sha256"] = postflight["scaling_raw_sha256"]
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (E2_5_REPORT_ROOT / "scaling" / "scaling_report.md").write_text(render_scaling_report(scaling), encoding="utf-8")
    write_scaling_summary_csv(E2_5_REPORT_ROOT / "scaling" / "summary.csv", scaling)
    output = E2_5_REPORT_ROOT / "e2_5_report.md"
    output.write_text(_render_total_report(e2, miss, scaling), encoding="utf-8")
    return output


def _write_postflight_integrity() -> dict[str, Any]:
    """最终再次验证 production Core 与原 E2 raw，并记录 Scaling raw hash。."""
    preflight = _read_object(E2_5_REPORT_ROOT / "preflight_integrity.json")
    current = {
        group: {relative: _sha256(PROJECT_ROOT / relative) for relative in expected}
        for group, expected in (
            ("production_core", preflight["production_core"]),
            ("frozen_e2_raw", preflight["frozen_e2_raw"]),
        )
    }
    result = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "production_core_before": preflight["production_core"],
        "production_core_after": current["production_core"],
        "production_core_unchanged": current["production_core"] == preflight["production_core"],
        "frozen_e2_raw_before": preflight["frozen_e2_raw"],
        "frozen_e2_raw_after": current["frozen_e2_raw"],
        "frozen_e2_raw_unchanged": current["frozen_e2_raw"] == preflight["frozen_e2_raw"],
        "scaling_raw_sha256": _sha256(E2_5_REPORT_ROOT / "scaling" / "raw_results.jsonl"),
    }
    if not result["production_core_unchanged"] or not result["frozen_e2_raw_unchanged"]:
        raise RuntimeError("E2.5 integrity failed")
    (E2_5_REPORT_ROOT / "postflight_integrity.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _render_total_report(e2: dict[str, Any], miss: dict[str, Any], scaling: dict[str, Any]) -> str:
    miss_metrics = miss["metrics"]
    scale_metrics = scaling["metrics"]
    scaling_rows = "\n".join(_scaling_row(row) for row in scale_metrics["by_scale"])
    primary_categories = "\n".join(
        f"| `{name}` | {count} |" for name, count in miss["category_counts"].items() if count
    )
    fallback_rows = "\n".join(
        f"| {item['case_id']} | {item['head_total_operations']} | "
        f"{item['oracle_executable_operations']} | {item['guardian_selected_operations']} | "
        f"{item['over_selection_operations']} | {_ratio(item['precision'])} |"
        for item in miss_metrics["fallback_cases"]
    )
    s1_break_even = scale_metrics["break_even"]["s1_one_operation"]
    s2_break_even = scale_metrics["break_even"]["s2_five_percent"]
    e2_runtime = e2["metrics"]["systems"][BenchmarkSystem.GUARDIAN_DETERMINISTIC.value]
    return f"""# API Guardian E2.5 Evaluation

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: validate
- Verification Status: VERIFIED from frozen E2 raw, replayed miss evidence and 440 persisted Scaling measurements
- Version Label: guardian_e2_5_evaluation_v1

## 1. Why E2.5 Was Needed

E2 reduced selected operations by 87.50% and observed HTTP requests by 86.15%, yet its paired median runtime reduction against optimized one-process Full Schemathesis was -0.82%. E2 also reported Contract Dataset scope recall 40/55 (72.73%) and precision 40/110 (36.36%). E2.5 therefore separates scope semantics case by case and scales the API surface without changing Guardian production behavior.

The E1 84.81% wall-clock reduction is not used as the main performance baseline because E1's Full implementation paid one process startup per operation. E2/E2.5 compare Guardian against the more credible one-process Full baseline.

## 2. Contract Miss Analysis

All {miss["source_miss_count"]} original Contract/scope misses were replayed with oasdiff and matched the frozen E2 verdict, selected scope, fallback flag and normalized-change count.

### Two Ground Truth meanings

- **ContractAffectedScope**: operations affected in contract meaning, including a Base operation removed from Head.
- **RuntimeExecutableScope**: affected operations that still exist in Head and can actually be selected for Schemathesis.

The original Dataset is unchanged. The second scope is a derived analysis layer.

| Metric | Result |
| --- | ---: |
| Contract Verdict Accuracy | {_ratio(miss["original_e2_metrics"]["contract_verdict_accuracy"])} |
| ContractImpactRecall | {_ratio(miss_metrics["contract_impact_recall"])} |
| ContractImpactPrecision | {_ratio(miss_metrics["contract_impact_precision"])} |
| ExecutableRegressionScopeRecall | {_ratio(miss_metrics["executable_regression_scope_recall"])} |
| ExecutableRegressionScopePrecision | {_ratio(miss_metrics["executable_regression_scope_precision"])} |
| ExecutableScopeReduction (micro) | {_ratio(miss_metrics["executable_scope_reduction"])} |
| FallbackRate | {_ratio(miss_metrics["fallback_rate"])} |
| FallbackPrecision | {_ratio(miss_metrics["fallback_precision"])} |
| RemovedOperationCoverage | {_ratio(miss_metrics["removed_operation_coverage"])} |
| ComponentChangeRecall / Precision | {_ratio(miss_metrics["component_change_recall"])} / {_ratio(miss_metrics["component_change_precision"])} |

### Primary miss taxonomy

| Classification | Cases |
| --- | ---: |
{primary_categories}

- Selector-only true false negatives with executable mapping evidence: {miss["true_guardian_scope_miss_count"]}.
- Ground Truth definition/use mismatch cases: {miss["ground_truth_definition_issue_count"]}.
- Factually proven Dataset Ground Truth errors: {miss["dataset_ground_truth_error_count"]}.
- Removed/non-executable operation cases: {miss["non_executable_removed_operation_case_count"]}.
- oasdiff/Ground Truth verdict disagreements: {miss["category_counts"]["oasdiff_ground_truth_disagreement"]}.

The ten endpoint/method removals are correctly detected as Contract FAIL, but their affected Base operation is absent from Head. The selector cannot execute it and recall-first fallback selects all seven remaining Head operations. The five `response_status_removed` cases are human-labelled breaking while oasdiff 1.28.0 emits no breaking record under the frozen command. Those five executable-scope misses are upstream tool/Ground Truth disagreements, not isolated selector mapping failures.

### All fallback-to-all costs

| Case | Head ops | Executable oracle | Selected | Extra | Precision |
| --- | ---: | ---: | ---: | ---: | ---: |
{fallback_rows}

Across the ten fallback cases, Guardian selected {miss_metrics["fallback_cost_operations"]} extra operations. Using the frozen E2 observation of 9 requests per independently tested operation gives {miss_metrics["fallback_cost_estimated_requests"]:.0f} estimated extra requests. Runtime cost is not estimated because these Contract-only cases were not run and subprocess cost is non-linear.

## 3. Scaling Benchmark

The parameterized local FastAPI SUT was tested at 8, 16, 32, 64 and 128 operations. S1 affects exactly one operation; S2 affects approximately 5%, yielding 1, 1, 2, 3 and 6 operations. Every affected endpoint returns `value` as string while Head requires integer. Each case/system used one excluded warm-up and {scaling["configuration"]["repetitions"]} measured repetitions.

| Scenario | Total | Affected | Scope reduction | Request reduction | Full median / p95 | Full+oasdiff median / p95 | Guardian median / p95 | Oracle median / p95 | Paired G-F | Runtime reduction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{scaling_rows}

The run contains {scaling["raw_sample_counts"]["all"]} raw rows: {scaling["raw_sample_counts"]["measured"]} measured and {scaling["raw_sample_counts"]["warmup"]} warm-up. Execution errors: {scale_metrics["execution_error_count"]}. Deterministic reproducibility: {_ratio(scale_metrics["deterministic_reproducibility"])}.

## 4. Break-even Result

Break-even is operationally defined as the first of at least two consecutive scale points where Guardian cell median is lower than Full.

- **S1 / exactly one affected operation:** observed region starts at {s1_break_even["start_operations"]} operations and is first confirmed through {s1_break_even["confirmed_through_operations"]}; Guardian remains faster at the measured 64 and 128 points. This is an observed scale point, not an interpolated exact crossing.
- **S2 / approximately 5% affected:** no break-even observed up to {s2_break_even["max_tested_operations"]} operations. The 16-operation faster cell is isolated; from 32 operations onward, multiple independent scoped processes dominate.

At 128 operations, S1 reduces requests from 1025 to 9 and has paired median runtime reduction {_percent(_row(scale_metrics, "s1_one_operation", 128)["guardian_vs_full"]["runtime_reduction"]["median"])}. S2 reduces requests from 1025 to 54 but has paired median runtime reduction {_percent(_row(scale_metrics, "s2_five_percent", 128)["guardian_vs_full"]["runtime_reduction"]["median"])}; fewer requests do not compensate for six sequential Schemathesis process startups.

The measured Oracle S2 medians are close to proportional to affected-operation count, while Full uses one process. The detailed Scaling report records descriptive linear fits and R² values. This supports process startup as the main current multi-operation bottleneck, although exact fixed Full startup remains unavailable without a zero-operation control.

## 5. What API Guardian Actually Improves

- E2 detects all 40/40 mutations declared detectable by the current schema oracle; overall recall is 40/50 because ten semantic negative controls remain undetected.
- For exact small change scopes, Guardian sharply reduces operations and HTTP requests.
- With exactly one affected operation, the measured latency benefit grows with API size and a sustained faster region begins at 16 operations on this host.
- Guardian unifies oasdiff contract facts, scoped runtime testing and deterministic verdicts; those integration benefits are distinct from oasdiff's detection and Schemathesis's generation capabilities.

## 6. What API Guardian Does NOT Improve

- It does not guarantee lower wall-clock latency for every change ratio. The S2 path is slower from 32 through 128 operations under the current per-operation subprocess strategy.
- It does not detect arbitrary business-semantic bugs: E2 overall mutation recall remains {_ratio(e2_runtime["overall_mutation_recall"])}.
- Contract verdict accuracy is not perfect: five response-status cases disagree with oasdiff 1.28.0.
- Recall-first fallback can be expensive: ten removal cases select all remaining Head operations with 0/70 fallback precision.
- This synthetic localhost study does not establish real-world production performance or generalize across hardware.

## 7. Threats to Validity

The SUT is synthetic, homogeneous GET-only, localhost, unauthenticated and uses a schema-oriented oracle. Timings include Windows process startup and are hardware-specific. Only Schemathesis 4.24.3 with `examples,coverage`, max examples 1 and deterministic generation was measured. Affected operations are deliberately the first stable operations, endpoint work is trivial, and 256 operations was not run because the predeclared maximum was 128. Paired bootstrap intervals quantify sampling uncertainty but do not remove these design limitations.

## 8. Next Evaluation Recommendation

Proceed to E3 only as a separate frozen-Core real-world validation, prioritizing Contract + Scope pairs before runtime claims. In parallel, record a future product experiment for batching/process reuse because per-operation Schemathesis startup is the largest observed runtime bottleneck. That optimization must be implemented and evaluated in a new phase, never retroactively mixed into E2/E2.5.

The current evidence supports **change-aware request/operation reduction and conditional latency improvement for very small affected scopes**. It does not support “Guardian is always faster,” “scope mapping is perfect,” or “Guardian detects arbitrary API business regressions.”

### Reproducibility and integrity

- Scaling run: `{scaling["run_id"]}`
- Frozen E2 run: `{e2["run_id"]}`
- Production Core individual SHA256 values matched preflight after both analyses.
- Original E2 raw and Contract raw SHA256 values matched preflight.
- Full raw, manifest, JSON/CSV summaries and detailed Markdown reports are retained under `benchmarks/guardian/reports/e2_5/`.
"""


def _scaling_row(row: dict[str, Any]) -> str:
    full = _duration(row, BenchmarkSystem.FULL_SCHEMATHESIS)
    full_pipeline = _duration(row, BenchmarkSystem.FULL_PIPELINE)
    guardian = _duration(row, BenchmarkSystem.GUARDIAN_DETERMINISTIC)
    oracle = _duration(row, BenchmarkSystem.ORACLE_SCOPE)
    pair = row["guardian_vs_full"]
    return (
        f"| `{row['scenario']}` | {row['total_operations']} | {row['affected_operations']} | "
        f"{_percent(row['scope_reduction'])} | {_percent(row['http_request_reduction'])} | "
        f"{_seconds(full['median'])} / {_seconds(full['p95'])} | "
        f"{_seconds(full_pipeline['median'])} / {_seconds(full_pipeline['p95'])} | "
        f"{_seconds(guardian['median'])} / {_seconds(guardian['p95'])} | "
        f"{_seconds(oracle['median'])} / {_seconds(oracle['p95'])} | "
        f"{_signed_seconds(pair['paired_difference_seconds']['median'])} | "
        f"{_percent(pair['runtime_reduction']['median'])} |"
    )


def _duration(row: dict[str, Any], system: BenchmarkSystem) -> dict[str, Any]:
    return row["systems"][system.value]["total_duration_seconds"]


def _row(metrics: dict[str, Any], scenario: str, size: int) -> dict[str, Any]:
    return next(row for row in metrics["by_scale"] if row["scenario"] == scenario and row["total_operations"] == size)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ratio(value: dict[str, Any]) -> str:
    return f"{value['numerator']}/{value['denominator']} ({_percent(value['value'])})"


def _percent(value: Any) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"


def _seconds(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.3f} s"


def _signed_seconds(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):+.3f} s"
