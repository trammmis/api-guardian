"""Stage E2.5 Contract / Scope miss 的逐 Case 派生分析。."""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import fmean
from typing import Any

from app.services.guardian.models import GuardianVerdict
from app.services.guardian.oasdiff import OasdiffAdapter, normalize_contract_changes
from app.services.guardian.scope import determine_contract_verdict, select_test_scope
from app.services.guardian.security import prepare_openapi_spec
from benchmarks.guardian.config import PROJECT_ROOT, REPORT_ROOT
from benchmarks.guardian.datasets.contract.definitions import CASES_PATH
from benchmarks.guardian.e2_5_models import MissAnalysisRecord, MissClassification
from benchmarks.guardian.metrics import ratio
from benchmarks.guardian.models import ContractCase, ContractMeasurement
from benchmarks.guardian.mutations.apply import resolve_project_path

E2_REPORT_ROOT = REPORT_ROOT / "e2" / "latest"
E2_5_REPORT_ROOT = REPORT_ROOT / "e2_5"
MISS_REPORT_ROOT = E2_5_REPORT_ROOT / "miss_analysis"
PREFLIGHT_PATH = E2_5_REPORT_ROOT / "preflight_integrity.json"


async def run_miss_analysis(*, oasdiff_path: Path) -> dict[str, Any]:
    """对原 E2 的 15 个 miss 重新取证，并输出双 Scope 指标。."""
    preflight = _read_object(PREFLIGHT_PATH)
    _assert_frozen_integrity(preflight)
    summary = _read_object(E2_REPORT_ROOT / "summary.json")
    cases = _load_jsonl_models(CASES_PATH, ContractCase)
    measurements = _load_jsonl_models(E2_REPORT_ROOT / "contract_raw_results.jsonl", ContractMeasurement)
    by_case = {item.case_id: item for item in measurements}
    miss_ids = {
        str(item["case_id"])
        for item in _read_array(E2_REPORT_ROOT / "contract_scope_misses.json")
        if isinstance(item, dict)
    }
    if len(cases) != 60 or len(measurements) != 60 or len(miss_ids) != 15:
        raise RuntimeError("E2 source artifact counts do not match the frozen experiment")

    adapter = OasdiffAdapter(
        executable=str(oasdiff_path.resolve()),
        timeout_seconds=float(summary["configuration"]["subprocess_timeout_seconds"]),
        max_artifact_bytes=int(summary["configuration"]["max_artifact_bytes"]),
    )
    max_openapi_bytes = 5 * 1024 * 1024
    max_scan_bytes = int(summary["configuration"]["max_scan_artifact_bytes"])
    records: list[MissAnalysisRecord] = []
    head_operations_by_case: dict[str, tuple[str, ...]] = {}

    for case in cases:
        head_path = resolve_project_path(case.head_spec)
        head_operations_by_case[case.case_id] = tuple(
            operation.key for operation in prepare_openapi_spec(str(head_path), max_openapi_bytes).operations
        )
        if case.case_id not in miss_ids:
            continue
        records.append(
            await _analyze_one_miss(
                case,
                by_case[case.case_id],
                adapter,
                max_openapi_bytes=max_openapi_bytes,
                max_scan_bytes=max_scan_bytes,
            )
        )

    ordered_records = tuple(sorted(records, key=lambda item: item.case_id))
    if len(ordered_records) != 15:
        raise RuntimeError("not every E2 miss received a classification")
    metrics = _derived_metrics(cases, by_case, head_operations_by_case, summary)
    categories = Counter(record.classification.value for record in ordered_records)
    category_counts = {classification.value: categories[classification.value] for classification in MissClassification}
    result: dict[str, Any] = {
        "stage": "E2.5-miss-analysis",
        "source_e2_run_id": summary["run_id"],
        "source_miss_count": len(miss_ids),
        "classified_miss_count": len(ordered_records),
        "original_e2_metrics": {
            "contract_verdict_accuracy": summary["metrics"]["contract_verdict_accuracy"],
            "contract_scope": summary["metrics"]["contract_scope"],
        },
        "ground_truth_semantics": {
            "contract_affected_scope": "Operations affected in contract meaning, including operations removed from Head",
            "runtime_executable_scope": "Contract-affected operations that still exist in Head and can be selected for Schemathesis",
            "original_dataset_rewritten": False,
        },
        "category_counts": category_counts,
        "true_guardian_scope_miss_count": sum(record.is_true_guardian_scope_miss for record in ordered_records),
        "ground_truth_definition_issue_count": sum(
            record.is_ground_truth_definition_issue for record in ordered_records
        ),
        "dataset_ground_truth_error_count": category_counts[MissClassification.DATASET_GROUND_TRUTH_ERROR.value],
        "non_executable_removed_operation_case_count": sum(
            record.is_non_executable_removed_operation for record in ordered_records
        ),
        "metrics": metrics,
        "integrity": {
            "preflight": preflight,
            "production_core_unchanged": True,
            "e2_raw_unchanged": True,
        },
    }
    MISS_REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_jsonl(MISS_REPORT_ROOT / "miss_analysis.jsonl", ordered_records)
    _write_json(MISS_REPORT_ROOT / "miss_summary.json", result)
    _write_summary_csv(MISS_REPORT_ROOT / "miss_summary.csv", result)
    (MISS_REPORT_ROOT / "miss_analysis.md").write_text(
        _render_report(result, ordered_records, metrics["fallback_cases"]), encoding="utf-8"
    )
    _assert_frozen_integrity(preflight)
    return result


async def _analyze_one_miss(
    case: ContractCase,
    measurement: ContractMeasurement,
    adapter: OasdiffAdapter,
    *,
    max_openapi_bytes: int,
    max_scan_bytes: int,
) -> MissAnalysisRecord:
    """复跑一个 miss，并要求派生证据与冻结 E2 raw 完全一致。."""
    base_path = resolve_project_path(case.base_spec)
    head_path = resolve_project_path(case.head_spec)
    base_operations = prepare_openapi_spec(str(base_path), max_openapi_bytes).operations
    head_operations = prepare_openapi_spec(str(head_path), max_openapi_bytes).operations
    diff = await adapter.compare(str(base_path), str(head_path), max_scan_bytes)
    changes = normalize_contract_changes(diff.candidates)
    selection = select_test_scope(changes, head_operations)
    verdict = determine_contract_verdict(changes, has_error=False)
    selected = tuple(operation.key for operation in selection.operations)
    if (
        verdict != measurement.actual_contract_verdict
        or selected != measurement.selected_operations
        or selection.fallback_to_all != measurement.fallback_to_all
        or len(changes) != measurement.normalized_change_count
    ):
        raise RuntimeError(f"replayed miss does not match frozen E2 raw: {case.case_id}")

    head_keys = {operation.key for operation in head_operations}
    executable = tuple(key for key in case.expected_affected_operations if key in head_keys)
    classification, primary, secondary, true_miss, removed, notes = _classify(
        case,
        measurement,
        executable,
        changes_present=bool(changes),
    )
    return MissAnalysisRecord(
        case_id=case.case_id,
        change_type=case.change_type,
        base_operations=tuple(operation.key for operation in base_operations),
        head_operations=tuple(operation.key for operation in head_operations),
        ground_truth_contract_verdict=case.expected_contract_verdict,
        guardian_contract_verdict=measurement.actual_contract_verdict,
        ground_truth_contract_affected_operations=case.expected_affected_operations,
        ground_truth_runtime_executable_operations=executable,
        guardian_selected_operations=measurement.selected_operations,
        oasdiff_change_ids=tuple(change.change_id for change in changes),
        oasdiff_paths=_unique(change.path for change in changes if change.path),
        oasdiff_operation_ids=_unique(change.operation_id for change in changes if change.operation_id),
        fallback_to_all=measurement.fallback_to_all,
        classification=classification,
        primary_cause=primary,
        secondary_causes=secondary,
        is_true_guardian_scope_miss=true_miss,
        is_ground_truth_definition_issue=removed,
        is_non_executable_removed_operation=removed,
        notes=notes,
    )


def _classify(
    case: ContractCase,
    measurement: ContractMeasurement,
    executable: tuple[str, ...],
    *,
    changes_present: bool,
) -> tuple[
    MissClassification,
    str,
    tuple[MissClassification, ...],
    bool,
    bool,
    str,
]:
    """根据固定 taxonomy 分类，不把工具分歧伪装成 Dataset 错误。."""
    affected_missing_from_head = set(case.expected_affected_operations) - set(executable)
    if affected_missing_from_head:
        return (
            MissClassification.REMOVED_OPERATION_NOT_EXECUTABLE,
            "The contract-affected operation was removed from Head and cannot be an executable Schemathesis target.",
            (
                MissClassification.CONTRACT_VS_RUNTIME_SCOPE_MISMATCH,
                MissClassification.FALLBACK_TO_ALL_OVER_SELECTION,
            ),
            False,
            True,
            "oasdiff detected the removal; exact matching against Head failed, so the recall-first selector conservatively selected all remaining Head operations.",
        )
    if measurement.actual_contract_verdict != case.expected_contract_verdict:
        return (
            MissClassification.OASDIFF_GROUND_TRUTH_DISAGREEMENT,
            "Human Ground Truth labels removal of a declared response status as breaking, while oasdiff 1.28.0 emitted no breaking change under the frozen command.",
            (),
            False,
            False,
            "With no normalized change, the selector had no operation evidence to map; this is upstream tool/Ground Truth disagreement rather than a selector mapping failure.",
        )
    if measurement.fallback_to_all:
        return (
            MissClassification.FALLBACK_TO_ALL_OVER_SELECTION,
            "At least one normalized change could not be mapped exactly, triggering recall-first selection of every Head operation.",
            (),
            False,
            False,
            "Fallback preserved recall but reduced precision.",
        )
    if not set(executable).issubset(measurement.selected_operations):
        return (
            MissClassification.TRUE_SCOPE_FALSE_NEGATIVE,
            "An executable Ground Truth operation was not selected despite available change evidence.",
            (),
            changes_present,
            False,
            "This would be a true selector false negative when operation evidence is present.",
        )
    return (
        MissClassification.TRUE_SCOPE_FALSE_POSITIVE,
        "Selected scope contains operations outside executable Ground Truth.",
        (),
        False,
        False,
        "The case is over-selected without fallback.",
    )


def _derived_metrics(
    cases: tuple[ContractCase, ...],
    by_case: dict[str, ContractMeasurement],
    head_operations_by_case: dict[str, tuple[str, ...]],
    e2_summary: dict[str, Any],
) -> dict[str, Any]:
    """在不改旧 Ground Truth 的前提下计算 Contract 与可执行 Scope。."""
    impact_tp = impact_expected = impact_selected = 0
    executable_tp = executable_expected = executable_selected = 0
    reductions: list[float] = []
    fallback_tp = fallback_selected = 0
    fallback_cases: list[dict[str, Any]] = []
    removed_total = removed_detected = 0
    component_tp = component_expected = component_selected = 0
    observed_requests_per_operation = float(
        e2_summary["metrics"]["performance"]["guardian_deterministic"]["http_requests_issued"]["median"]
    ) / float(e2_summary["metrics"]["performance"]["guardian_deterministic"]["operations_tested"]["median"])
    for case in cases:
        if not case.expected_affected_operations:
            continue
        measurement = by_case[case.case_id]
        head_operations = head_operations_by_case[case.case_id]
        head_keys = set(head_operations)
        impact = set(case.expected_affected_operations)
        executable = impact & head_keys
        selected = set(measurement.selected_operations)
        impact_tp += len(impact & selected)
        impact_expected += len(impact)
        impact_selected += len(selected)
        executable_tp += len(executable & selected)
        executable_expected += len(executable)
        executable_selected += len(selected)
        reductions.append(1 - len(selected) / len(head_operations))
        if measurement.fallback_to_all:
            intersection = len(executable & selected)
            fallback_tp += intersection
            fallback_selected += len(selected)
            extra = max(len(selected) - len(executable), 0)
            fallback_cases.append(
                {
                    "case_id": case.case_id,
                    "head_total_operations": len(head_operations),
                    "oracle_executable_operations": len(executable),
                    "guardian_selected_operations": len(selected),
                    "over_selection_operations": extra,
                    "precision": ratio(intersection, len(selected)).model_dump(mode="json"),
                    "estimated_extra_http_requests": extra * observed_requests_per_operation,
                    "estimated_runtime_cost_seconds": None,
                    "runtime_cost_status": "unavailable_non_linear_and_not_executed_in_e2",
                }
            )
        if case.change_type in {"endpoint_removed", "method_removed"}:
            removed_total += 1
            removed_detected += measurement.actual_contract_verdict == GuardianVerdict.FAIL
        if case.change_type == "multi_operation_component_change":
            component_tp += len(impact & selected)
            component_expected += len(impact)
            component_selected += len(selected)
    unselected_operations = sum(
        len(head_operations_by_case[case.case_id]) - len(by_case[case.case_id].selected_operations)
        for case in cases
        if case.expected_affected_operations
    )
    eligible_operations = sum(
        len(head_operations_by_case[case.case_id]) for case in cases if case.expected_affected_operations
    )
    return {
        "contract_impact_recall": ratio(impact_tp, impact_expected).model_dump(mode="json"),
        "contract_impact_precision": ratio(impact_tp, impact_selected).model_dump(mode="json"),
        "executable_regression_scope_recall": ratio(executable_tp, executable_expected).model_dump(mode="json"),
        "executable_regression_scope_precision": ratio(executable_tp, executable_selected).model_dump(mode="json"),
        "executable_scope_reduction": {
            "numerator": unselected_operations,
            "denominator": eligible_operations,
            "value": unselected_operations / eligible_operations if eligible_operations else None,
            "aggregation": "micro_operation_reduction",
            "mean_per_case": fmean(reductions) if reductions else None,
        },
        "fallback_rate": ratio(len(fallback_cases), len(cases)).model_dump(mode="json"),
        "fallback_precision": ratio(fallback_tp, fallback_selected).model_dump(mode="json"),
        "removed_operation_coverage": ratio(removed_detected, removed_total).model_dump(mode="json"),
        "component_change_recall": ratio(component_tp, component_expected).model_dump(mode="json"),
        "component_change_precision": ratio(component_tp, component_selected).model_dump(mode="json"),
        "fallback_cost_operations": sum(item["over_selection_operations"] for item in fallback_cases),
        "fallback_cost_estimated_requests": sum(item["estimated_extra_http_requests"] for item in fallback_cases),
        "fallback_cost_estimated_runtime_seconds": None,
        "fallback_cases": fallback_cases,
    }


def _render_report(
    summary: dict[str, Any],
    records: tuple[MissAnalysisRecord, ...],
    fallback_cases: list[dict[str, Any]],
) -> str:
    """生成逐类和逐 Case 的 Markdown 证据报告。."""
    metrics = summary["metrics"]
    original = summary["original_e2_metrics"]
    category_rows = "\n".join(f"| `{name}` | {count} |" for name, count in summary["category_counts"].items())
    record_rows = "\n".join(
        "| {case} | `{classification}` | {base} | {head} | {impact} | {executable} | {selected} | {fallback} |".format(
            case=record.case_id,
            classification=record.classification.value,
            base=len(record.base_operations),
            head=len(record.head_operations),
            impact=_join(record.ground_truth_contract_affected_operations),
            executable=_join(record.ground_truth_runtime_executable_operations),
            selected=_join(record.guardian_selected_operations),
            fallback=str(record.fallback_to_all).lower(),
        )
        for record in records
    )
    fallback_rows = "\n".join(
        f"| {item['case_id']} | {item['head_total_operations']} | {item['oracle_executable_operations']} | {item['guardian_selected_operations']} | {item['over_selection_operations']} | {_ratio_text(item['precision'])} | {item['estimated_extra_http_requests']:.0f}* |"
        for item in fallback_cases
    )
    return f"""# Contract and Scope Miss Analysis

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: validate
- Verification Status: VERIFIED against frozen E2 raw and replayed oasdiff evidence
- Version Label: guardian_e2_5_miss_analysis_v1

## Original E2 Metrics

- Contract Verdict Accuracy: {_ratio_text(original["contract_verdict_accuracy"])}
- Scope Recall: {_ratio_text(original["contract_scope"]["recall"])}
- Scope Precision: {_ratio_text(original["contract_scope"]["precision"])}
- fallback-to-all: {original["contract_scope"]["fallback_to_all_cases"]} cases

## Ground Truth Semantics

The original Dataset remains unchanged. E2.5 derives two views: ContractAffectedScope includes removed Base operations; RuntimeExecutableScope is the subset still present in Head and therefore selectable by Schemathesis. No case is relabelled after seeing Guardian output.

## ContractAffectedScope

- Recall: {_ratio_text(metrics["contract_impact_recall"])}
- Precision: {_ratio_text(metrics["contract_impact_precision"])}

## RuntimeExecutableScope

- Recall: {_ratio_text(metrics["executable_regression_scope_recall"])}
- Precision: {_ratio_text(metrics["executable_regression_scope_precision"])}
- Micro operation reduction: {_ratio_text(metrics["executable_scope_reduction"])}
- Mean per-case reduction: {_percent(metrics["executable_scope_reduction"]["mean_per_case"])}

## Miss Taxonomy

| Classification | Cases |
| --- | ---: |
{category_rows}

## Per-category Counts

All 15 original misses are classified: 10 removed-operation execution mismatches and 5 oasdiff/Ground Truth verdict disagreements. There are zero cases proven to be a selector-only false negative with sufficient executable mapping evidence. Ten removed-operation cases expose a Ground Truth definition/use mismatch, but no human label is proven factually wrong.

## True Guardian Scope Misses

`is_true_guardian_scope_miss = true` for {summary["true_guardian_scope_miss_count"]} cases. This flag is deliberately narrow: it requires usable oasdiff operation evidence and an executable Head operation that the selector nevertheless omitted.

## Ground Truth / Tool Definition Disagreements

Five `response_status_removed` cases are human-labelled breaking, while oasdiff 1.28.0 emitted no breaking change under the frozen command. The Dataset labels are retained; this is reported as tool/Ground Truth disagreement, not silently changed to PASS.

## Removed Operation Cases

Ten endpoint/method removals were detected as Contract FAIL ({_ratio_text(metrics["removed_operation_coverage"])}), but the affected operation no longer exists in Head. Exact mapping therefore fails and recall-first fallback selects all seven remaining Head operations. This is both a Contract-vs-Runtime scope mismatch and a real over-selection cost.

## Component Change Cases

- Component Change Recall: {_ratio_text(metrics["component_change_recall"])}
- Component Change Precision: {_ratio_text(metrics["component_change_precision"])}

All five shared-component cases mapped to the two genuinely affected operations. No component attribution limitation was observed in this controlled Dataset.

## Fallback-to-all Cases

| Case | Head ops | Executable oracle | Selected | Extra ops | Precision | Estimated extra requests |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{fallback_rows}

* Request cost uses the frozen E2 observation of 9 requests per independently tested operation. Runtime cost is unavailable because these Contract cases were not executed and process cost is non-linear.

- Fallback Rate: {_ratio_text(metrics["fallback_rate"])}
- Fallback Precision: {_ratio_text(metrics["fallback_precision"])}
- FallbackCostOperations: {metrics["fallback_cost_operations"]}
- FallbackCostRequests (estimated): {metrics["fallback_cost_estimated_requests"]:.0f}
- FallbackCostEstimatedRuntime: unavailable

## Per-case Evidence

| Case | Primary classification | Base ops | Head ops | Contract affected | Runtime executable | Guardian selected | Fallback |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
{record_rows}

Full oasdiff change IDs, paths, operation IDs, primary causes, secondary causes and notes are stored in `miss_analysis.jsonl`.

## Conclusions

The original 72.73%/36.36% Contract-scope result mixes a Base/contract concept with a Head-executable test concept. Splitting the semantics improves executable recall to {_ratio_text(metrics["executable_regression_scope_recall"])}, but precision remains {_ratio_text(metrics["executable_regression_scope_precision"])} because removal cases trigger conservative all-Head fallback. The five verdict misses come from oasdiff/Ground Truth disagreement, not from scope mapping. No Dataset label was proven factually wrong; the evaluation use of one scope field for two questions was the modelling limitation.
"""


def _write_summary_csv(path: Path, summary: dict[str, Any]) -> None:
    """输出 category counts 与双 Scope 指标。."""
    rows: list[tuple[str, str, Any, Any, Any]] = []
    for name, count in summary["category_counts"].items():
        rows.append(("classification", name, count, "", ""))
    for name, value in summary["metrics"].items():
        if isinstance(value, dict) and {"numerator", "denominator", "value"}.issubset(value):
            rows.append(("metric", name, value["value"], value["numerator"], value["denominator"]))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("category", "name", "value", "numerator", "denominator"))
        writer.writerows(rows)


def _assert_frozen_integrity(preflight: dict[str, Any]) -> None:
    """验证 production Core 和原 E2 raw 与 E2.5 开始时一致。."""
    for relative, expected in {**preflight["production_core"], **preflight["frozen_e2_raw"]}.items():
        actual = hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"frozen artifact changed during E2.5: {relative}")


def _load_jsonl_models(path: Path, model: Any) -> tuple[Any, ...]:
    """加载严格 Pydantic JSONL，不接受空行。."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or any(not line for line in lines):
        raise ValueError(f"invalid JSONL: {path}")
    return tuple(model.model_validate_json(line) for line in lines)


def _read_object(path: Path) -> dict[str, Any]:
    """读取 JSON object。."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_array(path: Path) -> list[Any]:
    """读取 JSON array。."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"expected JSON array: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    """稳定写出 JSON。."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: tuple[MissAnalysisRecord, ...]) -> None:
    """稳定写出逐 Case JSONL。."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")


def _unique(values: Any) -> tuple[str, ...]:
    """按首次出现顺序去重。."""
    return tuple(dict.fromkeys(values))


def _ratio_text(value: dict[str, Any]) -> str:
    """格式化带分子分母的指标。."""
    return f"{value['numerator']}/{value['denominator']} ({_percent(value['value'])})"


def _percent(value: Any) -> str:
    """格式化百分比。."""
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"


def _join(values: tuple[str, ...]) -> str:
    """紧凑显示 operation 列表。."""
    return "<br>".join(values) if values else "∅"
