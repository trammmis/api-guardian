"""运行 P1 removed-operation no-fallback 对照实验。."""

import csv
import hashlib
import json
import os
import platform
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from rich.console import Console

from app.services.guardian.models import ChangeSeverity, ContractChange, OperationRef
from app.services.guardian.scope import determine_contract_verdict, select_test_scope
from benchmarks.guardian.p1_removed_operation import select_test_scope_p1

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "benchmarks" / "guardian" / "reports" / "p1_removed_operation"
SAFETY_CASES_PATH = REPORT_ROOT / "safety_cases.jsonl"
MANIFEST_PATH = REPORT_ROOT / "experiment_manifest.json"
RAW_RESULTS_PATH = REPORT_ROOT / "raw_results.jsonl"
COMPARISON_PATH = REPORT_ROOT / "case_comparison.csv"
SUMMARY_PATH = REPORT_ROOT / "summary.json"
REPORT_PATH = REPORT_ROOT / "p1_report.md"

E2_5_MISSES_PATH = ROOT / "benchmarks" / "guardian" / "reports" / "e2_5" / "miss_analysis" / "miss_analysis.jsonl"
E3_RAW_PATH = ROOT / "benchmarks" / "guardian" / "reports" / "e3" / "raw_guardian_results.jsonl"
E3_REVIEW_ROOT = ROOT / "benchmarks" / "guardian" / "datasets" / "real_world" / "reviews" / "pre_system_v2"

CORE_HASHES = {
    "app/services/guardian/errors.py": "137cfd87b19484cb5fed3d4f3763e35e92f07be143f7b036ccfe5b65f2fcbce4",
    "app/services/guardian/graph.py": "9151a5cf7616902bb6201041b3d7c547ca737b5763601d1c7b38abce69c307fd",
    "app/services/guardian/models.py": "b75110edb4ca89bf4a012a7831078b209983145e96915ade767042e8b92d925c",
    "app/services/guardian/nodes.py": "5bb6868a9d32c1c80fda5adc5b8d5f1def9f6fac8d2408bda96fba7b22142001",
    "app/services/guardian/oasdiff.py": "f2628a647de1fe7e9bc20796f49fb2df0c673b258c6fd0d9ee58d55b00a77043",
    "app/services/guardian/schemathesis.py": "6ab64de45e57ddb2e02dca7ad250059df1fef64b7c4add3fdbbcb5cde1af39e5",
    "app/services/guardian/scope.py": "9c93058ec5b3d358f5dfe2ddc81415cd276fe7cf448b527124cd629e856f2787",
    "app/services/guardian/security.py": "0c7e2ce96e0b8e7e8573085f72d13ae2161c6874c1db653a323087a860d01527",
}
FROZEN_ARTIFACT_HASHES = {
    "benchmarks/guardian/reports/e2/latest/contract_raw_results.jsonl": "349023d9464935fcc6ec9006189bf59d124bf3df79f870bcbb4fddaf3aca957a",
    "benchmarks/guardian/reports/e2/latest/raw_results.jsonl": "f93f33a01a6d97f92a162eca5b41b2f11746c76f8ab7785e23d839e5f08ad196",
    "benchmarks/guardian/reports/e2_5/miss_analysis/miss_summary.json": "6c29218475ca29a0cfbd648a287806b3da43e59973d92ae748989fad972cede5",
    "benchmarks/guardian/reports/e2_5/scaling/raw_results.jsonl": "ec2426b72dd927ae1ae04f7ee59c17081ee164f04964e2a842f4f1bd17bca858",
    "benchmarks/guardian/reports/e2_5/scaling/summary.json": "bfb088d25f9739f2ddd4fbb10738ebb5fb3cf664f00f46c46b9f4f44acad0b3f",
    "benchmarks/guardian/reports/e3/raw_guardian_results.jsonl": "7737e3abe046f76105c35607c9f815a4d95df404c9968147dc47a0a3725b0389",
    "benchmarks/guardian/reports/e3/ground_truth_freeze_v2.json": "917c21c4c5eb642c00e6d3399c40b73dc8b9432c328eae7c85f1d7039dd6bf54",
}
E3_GROUND_TRUTH_V2_AGGREGATE = "7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94"
REMOVAL_TYPES = {"endpoint_removed", "method_removed"}
REAL_CASE_IDS = (
    "rw_immich_005_server_theme_endpoint_removed",
    "rw_appwrite_002_migration_cutover_replaced",
    "rw_appwrite_003_usage_project_and_health_update",
)
REAL_WORKTREES = {
    "immich-app/immich": ROOT / "benchmarks" / "guardian" / "worktrees" / "e3" / "immich",
    "appwrite/specs": ROOT / "benchmarks" / "guardian" / "worktrees" / "e3" / "appwrite-specs",
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


class P1Case(BaseModel):
    """一个冻结的 P1 scope semantics Case。."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    source: str
    subgroup: str
    changes: tuple[ContractChange, ...]
    base_operations: tuple[OperationRef, ...]
    head_operations: tuple[OperationRef, ...]
    oracle_executable_operations: tuple[str, ...]
    frozen_contract_verdict: str
    frozen_baseline_selected_operations: tuple[str, ...] | None = None
    frozen_baseline_fallback: bool | None = None
    expected_p1_selected_operations: tuple[str, ...] | None = None
    expected_p1_fallback: bool | None = None
    notes: str


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA256。."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    """写入稳定格式 JSON。."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    """写入稳定格式 JSONL。."""
    data = "".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values)
    path.write_text(data, encoding="utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL，拒绝空行。."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or any(not line for line in lines):
        raise RuntimeError(f"invalid JSONL: {path.relative_to(ROOT).as_posix()}")
    return [json.loads(line) for line in lines]


def operation_from_key(key: str, operation_id: str | None = None) -> OperationRef:
    """把冻结 artifact 中的 ``METHOD /path`` 转成 OperationRef。."""
    method, path = key.split(" ", 1)
    return OperationRef(method=method, path=path, operation_id=operation_id)


def calculate_integrity() -> dict[str, dict[str, str]]:
    """计算 Production Core 与冻结 Benchmark artifact 哈希。."""
    return {
        "production_core": {path: sha256_file(ROOT / path) for path in CORE_HASHES},
        "frozen_artifacts": {path: sha256_file(ROOT / path) for path in FROZEN_ARTIFACT_HASHES},
    }


def verify_integrity(snapshot: dict[str, dict[str, str]]) -> None:
    """验证所有冻结输入与已知哈希完全一致。."""
    if snapshot["production_core"] != CORE_HASHES:
        raise RuntimeError("Production Guardian Core hash mismatch")
    if snapshot["frozen_artifacts"] != FROZEN_ARTIFACT_HASHES:
        raise RuntimeError("Frozen E2/E2.5/E3 artifact hash mismatch")
    v2 = json.loads((ROOT / "benchmarks/guardian/reports/e3/ground_truth_freeze_v2.json").read_text(encoding="utf-8"))
    if v2.get("aggregate_review_sha256") != E3_GROUND_TRUTH_V2_AGGREGATE:
        raise RuntimeError("E3-B Ground Truth v2 aggregate mismatch")


def load_safety_cases() -> tuple[P1Case, ...]:
    """加载运行前冻结的十个 safety cases。."""
    cases: list[P1Case] = []
    for item in load_jsonl(SAFETY_CASES_PATH):
        cases.append(
            P1Case(
                case_id=item["case_id"],
                source="p1_safety",
                subgroup=item["subgroup"],
                changes=tuple(ContractChange.model_validate(value) for value in item["changes"]),
                base_operations=tuple(OperationRef.model_validate(value) for value in item["base_operations"]),
                head_operations=tuple(OperationRef.model_validate(value) for value in item["head_operations"]),
                oracle_executable_operations=tuple(item["oracle_executable_operations"]),
                frozen_contract_verdict=item["expected_contract_verdict"],
                expected_p1_selected_operations=tuple(item["expected_p1_selected_operations"]),
                expected_p1_fallback=item["expected_p1_fallback"],
                notes=item["notes"],
            )
        )
    if len(cases) != 10:
        raise RuntimeError(f"expected 10 safety cases, found {len(cases)}")
    return tuple(cases)


def load_synthetic_cases() -> tuple[P1Case, ...]:
    """从 E2.5 冻结 miss analysis 提取十个 removal Case。."""
    cases: list[P1Case] = []
    for item in load_jsonl(E2_5_MISSES_PATH):
        if item["change_type"] not in REMOVAL_TYPES:
            continue
        removal_key = item["ground_truth_contract_affected_operations"][0]
        operation_id = item["oasdiff_operation_ids"][0]
        base_operations = tuple(
            operation_from_key(key, operation_id if key == removal_key else None) for key in item["base_operations"]
        )
        head_operations = tuple(operation_from_key(key) for key in item["head_operations"])
        change_id = item["oasdiff_change_ids"][0]
        method, path = removal_key.split(" ", 1)
        cases.append(
            P1Case(
                case_id=f"e2_{item['case_id']}",
                source="e2_e2_5_frozen",
                subgroup="removed_only",
                changes=(
                    ContractChange(
                        change_id=change_id,
                        summary=change_id.replace("-", " "),
                        severity=ChangeSeverity.ERROR,
                        method=method,
                        path=path,
                        operation_id=operation_id,
                        section="paths",
                    ),
                ),
                base_operations=base_operations,
                head_operations=head_operations,
                oracle_executable_operations=tuple(item["ground_truth_runtime_executable_operations"]),
                frozen_contract_verdict=item["guardian_contract_verdict"],
                frozen_baseline_selected_operations=tuple(item["guardian_selected_operations"]),
                frozen_baseline_fallback=item["fallback_to_all"],
                notes="E2/E2.5 frozen synthetic removed-operation case.",
            )
        )
    if len(cases) != 10:
        raise RuntimeError(f"expected 10 E2/E2.5 removal cases, found {len(cases)}")
    return tuple(cases)


def git_show(repo_root: Path, commit: str, spec_path: str) -> bytes:
    """从只读外部 worktree 的固定 commit materialize Spec。."""
    result = subprocess.run(  # noqa: S603
        ["git", "show", f"{commit}:{spec_path}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git show failed for {commit}:{spec_path}")
    return result.stdout


def extract_operations(data: bytes) -> tuple[OperationRef, ...]:
    """从只读 OpenAPI artifact 提取 method/path/operationId。."""
    document = yaml.safe_load(data)
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise RuntimeError("invalid OpenAPI paths while loading P1 real-world case")
    operations: list[OperationRef] = []
    for path, path_item in document["paths"].items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            operations.append(
                OperationRef(
                    method=method,
                    path=path,
                    operation_id=operation_id if isinstance(operation_id, str) else None,
                )
            )
    return tuple(operations)


def load_real_cases() -> tuple[P1Case, ...]:
    """加载三条 E3-B 冻结 fallback Case，并从固定 commit 验证 Base/Head 结构。."""
    raw_by_id = {item["case_id"]: item for item in load_jsonl(E3_RAW_PATH)}
    cases: list[P1Case] = []
    for case_id in REAL_CASE_IDS:
        review = json.loads((E3_REVIEW_ROOT / f"{case_id}.json").read_text(encoding="utf-8"))
        raw = raw_by_id[case_id]
        repo_root = REAL_WORKTREES[review["repo"]]
        base_data = git_show(repo_root, review["base_commit"], review["base_spec_path"])
        head_data = git_show(repo_root, review["head_commit"], review["head_spec_path"])
        if hashlib.sha256(base_data).hexdigest() != review["base_spec_sha256"]:
            raise RuntimeError(f"Base Spec hash mismatch: {case_id}")
        if hashlib.sha256(head_data).hexdigest() != review["head_spec_sha256"]:
            raise RuntimeError(f"Head Spec hash mismatch: {case_id}")
        base_operations = extract_operations(base_data)
        head_operations = extract_operations(head_data)
        if tuple(operation.key for operation in head_operations) != tuple(
            operation["key"] for operation in raw["head_operations"]
        ):
            raise RuntimeError(f"Head operation order/content mismatch: {case_id}")
        subgroup = "removed_only" if case_id.startswith("rw_immich") else "mixed_removal"
        cases.append(
            P1Case(
                case_id=case_id,
                source="e3_b_frozen_real_world",
                subgroup=subgroup,
                changes=tuple(ContractChange.model_validate(value) for value in raw["normalized_changes"]),
                base_operations=base_operations,
                head_operations=head_operations,
                oracle_executable_operations=tuple(review["runtime_executable_operations"]),
                frozen_contract_verdict=raw["guardian_contract_verdict"],
                frozen_baseline_selected_operations=tuple(raw["selected_operations"]),
                frozen_baseline_fallback=raw["fallback_to_all"],
                notes="E3-B frozen real-world fallback case; Spec structure re-materialized from fixed commits.",
            )
        )
    return tuple(cases)


def ratio(numerator: int, denominator: int, *, empty_value: float = 1.0) -> float:
    """计算比例，并显式定义空集合为完全正确。."""
    return numerator / denominator if denominator else empty_value


def evaluate_case(case: P1Case) -> dict[str, Any]:
    """对同一冻结输入运行 Production B0 与实验 P1。."""
    baseline = select_test_scope(case.changes, case.head_operations)
    experimental = select_test_scope_p1(case.changes, case.base_operations, case.head_operations)
    contract_verdict = determine_contract_verdict(case.changes, has_error=False).value
    baseline_keys = tuple(operation.key for operation in baseline.operations)
    p1_keys = tuple(operation.key for operation in experimental.operations)

    if (
        case.frozen_baseline_selected_operations is not None
        and baseline_keys != case.frozen_baseline_selected_operations
    ):
        raise RuntimeError(f"Frozen baseline selected scope mismatch: {case.case_id}")
    if case.frozen_baseline_fallback is not None and baseline.fallback_to_all != case.frozen_baseline_fallback:
        raise RuntimeError(f"Frozen baseline fallback mismatch: {case.case_id}")
    if contract_verdict != case.frozen_contract_verdict:
        raise RuntimeError(f"Frozen contract verdict mismatch: {case.case_id}")

    oracle = set(case.oracle_executable_operations)
    baseline_set = set(baseline_keys)
    p1_set = set(p1_keys)
    baseline_tp = len(baseline_set & oracle)
    p1_tp = len(p1_set & oracle)
    expected_match = True
    if case.expected_p1_selected_operations is not None:
        expected_match = p1_keys == case.expected_p1_selected_operations
    if case.expected_p1_fallback is not None:
        expected_match = expected_match and experimental.fallback_to_all == case.expected_p1_fallback

    return {
        "case_id": case.case_id,
        "source": case.source,
        "subgroup": case.subgroup,
        "baseline_selected_ops": list(baseline_keys),
        "p1_selected_ops": list(p1_keys),
        "oracle_executable_ops": list(case.oracle_executable_operations),
        "baseline_fallback": baseline.fallback_to_all,
        "p1_fallback": experimental.fallback_to_all,
        "baseline_unresolved_change_ids": list(baseline.unresolved_change_ids),
        "p1_unresolved_change_ids": list(experimental.unresolved_change_ids),
        "baseline_true_positives": baseline_tp,
        "p1_true_positives": p1_tp,
        "oracle_count": len(oracle),
        "baseline_selected_count": len(baseline_set),
        "p1_selected_count": len(p1_set),
        "baseline_recall": ratio(baseline_tp, len(oracle)),
        "p1_recall": ratio(p1_tp, len(oracle)),
        "baseline_precision": ratio(baseline_tp, len(baseline_set)),
        "p1_precision": ratio(p1_tp, len(p1_set)),
        "extra_ops_before": len(baseline_set - oracle),
        "extra_ops_after": len(p1_set - oracle),
        "baseline_false_negatives": sorted(oracle - baseline_set),
        "p1_false_negatives": sorted(oracle - p1_set),
        "contract_verdict": contract_verdict,
        "contract_verdict_same": contract_verdict == case.frozen_contract_verdict,
        "safety_expectation_match": expected_match,
        "notes": case.notes,
    }


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """生成 micro 指标与各 subgroup 指标。."""
    oracle_count = sum(item["oracle_count"] for item in records)
    baseline_tp = sum(item["baseline_true_positives"] for item in records)
    p1_tp = sum(item["p1_true_positives"] for item in records)
    baseline_selected = sum(item["baseline_selected_count"] for item in records)
    p1_selected = sum(item["p1_selected_count"] for item in records)
    baseline_fallback = [item for item in records if item["baseline_fallback"]]
    p1_fallback = [item for item in records if item["p1_fallback"]]

    def fallback_precision(items: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
        true_positives = sum(item[f"{prefix}_true_positives"] for item in items)
        selected = sum(item[f"{prefix}_selected_count"] for item in items)
        return {"numerator": true_positives, "denominator": selected, "value": ratio(true_positives, selected)}

    return {
        "cases": len(records),
        "baseline_executable_scope_recall": {
            "numerator": baseline_tp,
            "denominator": oracle_count,
            "value": ratio(baseline_tp, oracle_count),
        },
        "p1_executable_scope_recall": {
            "numerator": p1_tp,
            "denominator": oracle_count,
            "value": ratio(p1_tp, oracle_count),
        },
        "baseline_executable_scope_precision": {
            "numerator": baseline_tp,
            "denominator": baseline_selected,
            "value": ratio(baseline_tp, baseline_selected),
        },
        "p1_executable_scope_precision": {
            "numerator": p1_tp,
            "denominator": p1_selected,
            "value": ratio(p1_tp, p1_selected),
        },
        "baseline_fallback_rate": {
            "numerator": len(baseline_fallback),
            "denominator": len(records),
            "value": ratio(len(baseline_fallback), len(records), empty_value=0.0),
        },
        "p1_fallback_rate": {
            "numerator": len(p1_fallback),
            "denominator": len(records),
            "value": ratio(len(p1_fallback), len(records), empty_value=0.0),
        },
        "baseline_fallback_precision": fallback_precision(baseline_fallback, "baseline"),
        "p1_fallback_precision": fallback_precision(p1_fallback, "p1"),
        "baseline_selected_operations": baseline_selected,
        "p1_selected_operations": p1_selected,
        "extra_operations_before": sum(item["extra_ops_before"] for item in records),
        "extra_operations_after": sum(item["extra_ops_after"] for item in records),
        "executable_false_negative_cases_baseline": sum(bool(item["baseline_false_negatives"]) for item in records),
        "executable_false_negative_cases_p1": sum(bool(item["p1_false_negatives"]) for item in records),
        "contract_verdict_agreement": {
            "numerator": sum(item["contract_verdict_same"] for item in records),
            "denominator": len(records),
            "value": ratio(sum(item["contract_verdict_same"] for item in records), len(records)),
        },
        "per_case_macro_recall_baseline": sum(item["baseline_recall"] for item in records) / len(records),
        "per_case_macro_recall_p1": sum(item["p1_recall"] for item in records) / len(records),
        "per_case_macro_precision_baseline": sum(item["baseline_precision"] for item in records) / len(records),
        "per_case_macro_precision_p1": sum(item["p1_precision"] for item in records) / len(records),
    }


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """应用冻结 acceptance rule，生成对照汇总。."""
    overall = aggregate(records)
    subgroups = {
        subgroup: aggregate([item for item in records if item["subgroup"] == subgroup])
        for subgroup in sorted({item["subgroup"] for item in records})
    }
    pure_cases = [item for item in records if item["subgroup"] == "removed_only"]
    mixed_cases = [item for item in records if item["subgroup"] == "mixed_removal"]
    unresolved_safety = next(item for item in records if item["case_id"] == "p1_safety_004_removal_plus_unresolved")
    acceptance = {
        "recall_not_decreased": (
            overall["p1_executable_scope_recall"]["value"] >= overall["baseline_executable_scope_recall"]["value"]
        ),
        "no_p1_executable_false_negatives": overall["executable_false_negative_cases_p1"] == 0,
        "extra_operations_reduced": overall["extra_operations_after"] < overall["extra_operations_before"],
        "pure_removal_empty_scope_correct": all(
            not item["oracle_executable_ops"] and not item["p1_selected_ops"] and not item["p1_fallback"]
            for item in pure_cases
        ),
        "mixed_removal_retains_oracle_scope": all(not item["p1_false_negatives"] for item in mixed_cases),
        "frozen_safety_expectations_pass": all(item["safety_expectation_match"] for item in records),
        "unresolved_non_removal_still_fallback": unresolved_safety["p1_fallback"],
        "contract_verdict_unchanged": overall["contract_verdict_agreement"]["numerator"] == len(records),
    }
    recommendation = "BENEFICIAL" if all(acceptance.values()) else "UNSAFE"
    return {
        "schema_version": "guardian-p1-summary-v1",
        "experiment": "P1 Removed-operation No-fallback Ablation",
        "case_count": len(records),
        "subgroup_counts": dict(Counter(item["subgroup"] for item in records)),
        "source_counts": dict(Counter(item["source"] for item in records)),
        "metric_semantics": {
            "micro": "operation-level sums across cases",
            "empty_scope_precision": "defined as 1.0 when both oracle and selected scope are empty",
            "empty_scope_recall": "defined as 1.0 when oracle executable scope is empty",
            "fallback_precision": "true executable operations divided by all selected operations in fallback cases",
        },
        "overall": overall,
        "subgroups": subgroups,
        "acceptance": acceptance,
        "recommendation": recommendation,
        "production_integration_recommended": recommendation == "BENEFICIAL",
        "key_cases": {
            case_id: next(item for item in records if item["case_id"] == case_id) for case_id in REAL_CASE_IDS
        },
    }


def write_comparison(records: list[dict[str, Any]]) -> None:
    """写出逐 Case 可审计 CSV。."""
    fields = [
        "case_id",
        "source",
        "subgroup",
        "baseline_selected_ops",
        "p1_selected_ops",
        "oracle_executable_ops",
        "baseline_fallback",
        "p1_fallback",
        "baseline_recall",
        "p1_recall",
        "baseline_precision",
        "p1_precision",
        "extra_ops_before",
        "extra_ops_after",
        "contract_verdict_same",
        "notes",
    ]
    with COMPARISON_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = {field: record[field] for field in fields}
            for field in ("baseline_selected_ops", "p1_selected_ops", "oracle_executable_ops"):
                row[field] = " | ".join(row[field])
            writer.writerow(row)


def percent(metric: dict[str, Any]) -> str:
    """格式化带分子分母的百分比。."""
    return f"{metric['numerator']}/{metric['denominator']} ({metric['value'] * 100:.2f}%)"


def build_report(summary: dict[str, Any], records: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    """从 raw records 自动生成实验报告。."""
    overall = summary["overall"]
    key_cases = summary["key_cases"]
    failures = [item for item in records if item["p1_false_negatives"] or not item["safety_expectation_match"]]
    subgroup_rows = []
    for name, metric in summary["subgroups"].items():
        subgroup_rows.append(
            f"| {name} | {metric['cases']} | {percent(metric['baseline_executable_scope_precision'])} | "
            f"{percent(metric['p1_executable_scope_precision'])} | {metric['extra_operations_before']} | "
            f"{metric['extra_operations_after']} |"
        )
    key_rows = []
    for case_id in REAL_CASE_IDS:
        item = key_cases[case_id]
        key_rows.append(
            f"| {case_id} | {item['baseline_selected_count']} / {item['baseline_fallback']} | "
            f"{item['p1_selected_count']} / {item['p1_fallback']} | {item['oracle_count']} | "
            f"{item['extra_ops_before']} | {item['extra_ops_after']} |"
        )
    return f"""# P1 Removed-operation No-fallback Ablation

## Material Passport

- 实验类型：冻结数据集上的确定性产品消融实验
- 被测系统：Frozen Baseline B0 与 benchmark-only P1 policy
- 输入：E2/E2.5 removal cases、E3-B Ground Truth v2 real-world fallback cases、运行前冻结 safety cases
- Ground Truth：E3-B v2 aggregate `{E3_GROUND_TRUTH_V2_AGGREGATE}`；P1 safety SHA256 `{manifest["safety_cases_sha256"]}`
- 生产行为：未修改；P1 模块不被生产 Graph 导入
- 未运行：oasdiff、Schemathesis、LLM、E3-C Runtime

## 1. Hypothesis

可由 Base/Head 结构和冻结 removal rule 明确证明的 operation removal 保持 Contract FAIL，但对 Head 的 RuntimeExecutableScope 贡献为空，且该 change 自身不触发 fallback-to-all。

## 2. Frozen Baseline

Baseline 直接调用生产 `select_test_scope`。任何 breaking change 无法映射到 Head operation 时，回退到 Head 全部 operation。Frozen E2/E2.5、E3-B raw 与 Production Core 哈希在运行前后均校验。

## 3. Experimental Policy

P1 逐 change 解析。method/path 或唯一 operationId 能映射时照常选中；只有已知 removal rule、Base 中存在、Head 中同 method/path 不存在、且无唯一 operationId counterpart 时，才判为 `PROVEN_REMOVED_OPERATION`，贡献空执行范围。其他 unresolved change 仍回退全量。

## 4. Safety Conditions

- removal 不改变 Contract verdict；
- mixed scan 中其他可映射 operation 必须保留；
- unresolved non-removal change 必须继续 fallback；
- operationId 能定位 Head counterpart 时优先映射，不能误判为空；
- 本次只识别两个冻结规则 ID，不做模糊文本或 component inference。

## 5. Dataset

- 总 Case：{summary["case_count"]}
- Pure removal：{summary["subgroup_counts"]["removed_only"]}
- Mixed removal：{summary["subgroup_counts"]["mixed_removal"]}
- Non-removal controls：{summary["subgroup_counts"]["non_removal_control"]}
- 来源：{json.dumps(summary["source_counts"], ensure_ascii=False, sort_keys=True)}

## 6. Baseline vs P1

| Metric | Frozen Baseline | P1 | Difference |
|---|---:|---:|---:|
| Executable Scope Recall | {percent(overall["baseline_executable_scope_recall"])} | {percent(overall["p1_executable_scope_recall"])} | {(overall["p1_executable_scope_recall"]["value"] - overall["baseline_executable_scope_recall"]["value"]) * 100:.2f} pp |
| Executable Scope Precision | {percent(overall["baseline_executable_scope_precision"])} | {percent(overall["p1_executable_scope_precision"])} | {(overall["p1_executable_scope_precision"]["value"] - overall["baseline_executable_scope_precision"]["value"]) * 100:.2f} pp |
| Fallback Rate | {percent(overall["baseline_fallback_rate"])} | {percent(overall["p1_fallback_rate"])} | {(overall["p1_fallback_rate"]["value"] - overall["baseline_fallback_rate"]["value"]) * 100:.2f} pp |
| Fallback Precision | {percent(overall["baseline_fallback_precision"])} | {percent(overall["p1_fallback_precision"])} | {(overall["p1_fallback_precision"]["value"] - overall["baseline_fallback_precision"]["value"]) * 100:.2f} pp |
| Selected Operations | {overall["baseline_selected_operations"]} | {overall["p1_selected_operations"]} | {overall["p1_selected_operations"] - overall["baseline_selected_operations"]} |
| Extra Selected Operations | {overall["extra_operations_before"]} | {overall["extra_operations_after"]} | {overall["extra_operations_after"] - overall["extra_operations_before"]} |
| Contract Verdict Agreement | {percent(overall["contract_verdict_agreement"])} | {percent(overall["contract_verdict_agreement"])} | 0 pp |

空 oracle 的 per-case recall 定义为 1.0；oracle 与 selected 都为空时 per-case precision 定义为 1.0。Micro precision 的分母仍是实际 selected operation 总数。

## 7. Pure Removal Results

{summary["subgroups"]["removed_only"]["cases"]} 个 pure-removal Case 的 P1 empty-scope correctness 为 `{summary["acceptance"]["pure_removal_empty_scope_correct"]}`。

## 8. Mixed Removal Results

| Case | Baseline selected / fallback | P1 selected / fallback | Oracle | Extra before | Extra after |
|---|---:|---:|---:|---:|---:|
{"\n".join(key_rows)}

Appwrite 002/003 均不是 pure removal。P1 只消除 removal 自身的 unresolved 状态，其他 operation-local breaking records 继续决定 scope。

## 9. Non-removal Controls

删除与无法映射 component 并存、纯 non-removal mapping failure、compatible addition、no-change 等控制场景均按冻结期望校验。`removal + unresolved component` 仍 fallback：`{summary["acceptance"]["unresolved_non_removal_still_fallback"]}`。

## 10. Recall Regression Check

- Baseline micro recall：{percent(overall["baseline_executable_scope_recall"])}
- P1 micro recall：{percent(overall["p1_executable_scope_recall"])}
- P1 executable false-negative Case：{overall["executable_false_negative_cases_p1"]}
- Mixed removal 保留 oracle scope：{summary["acceptance"]["mixed_removal_retains_oracle_scope"]}

## 11. Precision / Over-selection Improvement

| Subgroup | Cases | Baseline precision | P1 precision | Extra before | Extra after |
|---|---:|---:|---:|---:|---:|
{"\n".join(subgroup_rows)}

## 12. Failure Cases

{("无 acceptance failure。" if not failures else "\n".join(f"- {item['case_id']}" for item in failures))}

## 13. Threats to Validity

- 数据集聚焦 removal/fallback，不代表所有 OpenAPI change；
- removal rule ID 绑定 oasdiff 1.28.0 已验证契约；
- E3-B Ground Truth 为 AI-assisted initial review 加用户确认 amendment，并非大规模多人独立标注；
- 本实验只评价 scope semantics，不包含 Schemathesis 运行时间或实际 HTTP 请求；
- safety cases 是小型、人工构造的结构控制，用于验证边界而非估计真实分布。

## 14. Recommendation

**{summary["recommendation"]}**

Acceptance checks：

{"\n".join(f"- {name}: {value}" for name, value in summary["acceptance"].items())}

该结论只支持进入独立的 P1 production integration phase；本实验没有修改生产 Selector。
"""


def run_p1() -> dict[str, Any]:
    """执行一次不可静默覆盖的 P1 对照实验。."""
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    existing_outputs = [
        path for path in (MANIFEST_PATH, RAW_RESULTS_PATH, COMPARISON_PATH, SUMMARY_PATH, REPORT_PATH) if path.exists()
    ]
    if existing_outputs:
        names = ", ".join(path.name for path in existing_outputs)
        raise RuntimeError(f"P1 outputs already exist; refusing silent overwrite: {names}")

    started_at = datetime.now(UTC)
    before = calculate_integrity()
    verify_integrity(before)
    safety_hash = sha256_file(SAFETY_CASES_PATH)
    manifest: dict[str, Any] = {
        "schema_version": "guardian-p1-manifest-v1",
        "experiment": "P1 Removed-operation No-fallback Ablation",
        "status": "preflight_completed",
        "started_at": started_at.isoformat(),
        "preflight_completed_at": datetime.now(UTC).isoformat(),
        "safety_cases_frozen_before_system_output": True,
        "safety_cases_sha256": safety_hash,
        "ground_truth": {
            "e3_b_v2_aggregate_sha256": E3_GROUND_TRUTH_V2_AGGREGATE,
            "e2_e2_5_artifacts": "frozen by recorded SHA256",
        },
        "production_core_hashes_before": before["production_core"],
        "frozen_artifact_hashes_before": before["frozen_artifacts"],
        "policy": {
            "name": "PROVEN_REMOVED_OPERATION",
            "change_ids": sorted(("api-path-removed-without-deprecation", "api-removed-without-deprecation")),
            "resolution_order": ["exact method/path", "unique operationId", "proven removal", "fallback"],
        },
        "excluded_execution": ["oasdiff", "Schemathesis", "LLM", "E3-C runtime"],
        "preflight_anomalies": [
            {
                "timestamp": "2026-09-08T16:43:05+08:00",
                "stage": "before_manifest_and_system_output",
                "result": "aborted",
                "reason": (
                    "Runner referenced the nonexistent E2 alias runtime_raw_results.jsonl; "
                    "corrected to the frozen raw_results.jsonl with the same pre-recorded SHA256."
                ),
            }
        ],
        "environment": {
            "python": platform.python_version(),
            "os": platform.platform(),
            "cpu_logical_count": os.cpu_count(),
            "git_commit": _git_text(["git", "rev-parse", "HEAD"]),
            "git_status_porcelain": _git_text(["git", "status", "--porcelain"]),
        },
    }
    write_json(MANIFEST_PATH, manifest)

    cases = (*load_real_cases(), *load_synthetic_cases(), *load_safety_cases())
    records = [evaluate_case(case) for case in cases]
    summary = build_summary(records)
    write_jsonl(RAW_RESULTS_PATH, records)
    write_comparison(records)
    write_json(SUMMARY_PATH, summary)

    after = calculate_integrity()
    verify_integrity(after)
    manifest.update(
        {
            "status": "completed",
            "system_output_started_after_preflight": True,
            "completed_at": datetime.now(UTC).isoformat(),
            "case_count": len(records),
            "production_core_hashes_after": after["production_core"],
            "frozen_artifact_hashes_after": after["frozen_artifacts"],
            "production_core_unchanged": before["production_core"] == after["production_core"],
            "frozen_artifacts_unchanged": before["frozen_artifacts"] == after["frozen_artifacts"],
            "output_files": [
                RAW_RESULTS_PATH.relative_to(ROOT).as_posix(),
                COMPARISON_PATH.relative_to(ROOT).as_posix(),
                SUMMARY_PATH.relative_to(ROOT).as_posix(),
                REPORT_PATH.relative_to(ROOT).as_posix(),
            ],
        }
    )
    write_json(MANIFEST_PATH, manifest)
    REPORT_PATH.write_text(build_report(summary, records, manifest), encoding="utf-8")
    return summary


def _git_text(command: list[str]) -> str:
    """读取本地 Git 元数据，不执行任何写操作。."""
    result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True, shell=False)  # noqa: S603
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def print_summary(summary: dict[str, Any]) -> None:
    """使用 Rich 输出最小实验摘要。."""
    overall = summary["overall"]
    console = Console()
    console.print(f"[bold]P1 cases[/bold]: {summary['case_count']}")
    console.print(f"[bold]Baseline recall[/bold]: {percent(overall['baseline_executable_scope_recall'])}")
    console.print(f"[bold]P1 recall[/bold]: {percent(overall['p1_executable_scope_recall'])}")
    console.print(f"[bold]Recommendation[/bold]: {summary['recommendation']}")
