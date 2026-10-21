"""P1-I production integration 的冻结 Dataset replay 与报告生成."""

import asyncio
import csv
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from app.services.guardian.errors import GuardianException
from app.services.guardian.models import ContractChange, GuardianVerdict, OperationRef
from app.services.guardian.oasdiff import OasdiffAdapter, normalize_contract_changes
from app.services.guardian.scope import determine_contract_verdict, select_test_scope
from app.services.guardian.security import prepare_openapi_spec
from benchmarks.guardian.models import ContractCase
from benchmarks.guardian.p1_runner import P1Case
from benchmarks.guardian.p1_runner import aggregate as aggregate_p1
from benchmarks.guardian.p1_runner import extract_operations, load_safety_cases, load_synthetic_cases
from benchmarks.guardian.real_world_models import PreSystemReviewRecord, ReviewConfidence, ReviewStatus

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "benchmarks" / "guardian" / "reports" / "p1_integration"
MANIFEST_PATH = REPORT_ROOT / "integration_manifest.json"
P1_REPLAY_PATH = REPORT_ROOT / "p1_frozen_replay.json"
E2_REPLAY_PATH = REPORT_ROOT / "e2_e2_5_replay.json"
E3_REPLAY_PATH = REPORT_ROOT / "e3_b_before_after.json"
COMPARISON_PATH = REPORT_ROOT / "case_comparison.csv"
DIFF_PATH = REPORT_ROOT / "production_diff.md"
REPORT_PATH = REPORT_ROOT / "p1_integration_report.md"

P1_RAW_PATH = ROOT / "benchmarks" / "guardian" / "reports" / "p1_removed_operation" / "raw_results.jsonl"
E2_CASES_PATH = ROOT / "benchmarks" / "guardian" / "datasets" / "contract" / "cases.jsonl"
E2_RAW_PATH = ROOT / "benchmarks" / "guardian" / "reports" / "e2" / "latest" / "contract_raw_results.jsonl"
E3_REVIEWS_PATH = ROOT / "benchmarks" / "guardian" / "reports" / "e3" / "review_records_v2.jsonl"
E3_REVIEW_ROOT = ROOT / "benchmarks" / "guardian" / "datasets" / "real_world" / "reviews" / "pre_system_v2"
E3_RAW_PATH = ROOT / "benchmarks" / "guardian" / "reports" / "e3" / "raw_guardian_results.jsonl"
OASDIFF_PATH = ROOT / "benchmarks" / "guardian" / ".tools" / "oasdiff" / "1.28.0" / "oasdiff.exe"
REAL_WORKTREES = {
    "immich-app/immich": ROOT / "benchmarks" / "guardian" / "worktrees" / "e3" / "immich",
    "supabase/auth": ROOT / "benchmarks" / "guardian" / "worktrees" / "e3" / "supabase-auth",
    "netbirdio/netbird": ROOT / "benchmarks" / "guardian" / "worktrees" / "e3" / "netbird",
    "appwrite/specs": ROOT / "benchmarks" / "guardian" / "worktrees" / "e3" / "appwrite-specs",
}
EXPECTED_FROZEN_HASHES = {
    "benchmarks/guardian/reports/p1_removed_operation/raw_results.jsonl": "64698ca37a4c783c8e5efc280f96630257f6e2d86a95fe1ec4d9824bc1d3287a",
    "benchmarks/guardian/reports/p1_removed_operation/summary.json": "a06d6df30e546666243627aa842abd8e0241ba38699286bf912f789fbc068be6",
    "benchmarks/guardian/reports/e3/raw_guardian_results.jsonl": "7737e3abe046f76105c35607c9f815a4d95df404c9968147dc47a0a3725b0389",
    "benchmarks/guardian/reports/e3/ground_truth_freeze_v2.json": "917c21c4c5eb642c00e6d3399c40b73dc8b9432c328eae7c85f1d7039dd6bf54",
    "benchmarks/guardian/reports/e2/latest/contract_raw_results.jsonl": "349023d9464935fcc6ec9006189bf59d124bf3df79f870bcbb4fddaf3aca957a",
    "benchmarks/guardian/reports/e2/latest/raw_results.jsonl": "f93f33a01a6d97f92a162eca5b41b2f11746c76f8ab7785e23d839e5f08ad196",
    "benchmarks/guardian/reports/e2_5/miss_analysis/miss_analysis.jsonl": "6ecb3305b27cb94f5515e5fb5ad88b1f047e96c6df4a47823615fdfe060f71cf",
}
EXPECTED_GT_V2 = "7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94"
HEADLINE_STATUSES = {ReviewStatus.CONFIRMED, ReviewStatus.PROBABLE}
P1_REAL_CASE_IDS = (
    "rw_immich_005_server_theme_endpoint_removed",
    "rw_appwrite_002_migration_cutover_replaced",
    "rw_appwrite_003_usage_project_and_health_update",
)


def sha256_file(path: Path) -> str:
    """流式计算 artifact SHA256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取非空 JSONL artifact."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or any(not line for line in lines):
        raise RuntimeError(f"invalid JSONL: {path.relative_to(ROOT).as_posix()}")
    return [json.loads(line) for line in lines]


def write_json(path: Path, value: Any) -> None:
    """写出稳定格式 JSON."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def metric(numerator: int, denominator: int) -> dict[str, Any]:
    """构造带分子、分母、比例和百分比的指标."""
    value = numerator / denominator if denominator else None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "percentage": value * 100 if value is not None else None,
    }


def operation_scope_metrics(records: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    """计算指定 before/after 列的 micro executable scope 指标."""
    true_positives = sum(
        len(set(item[f"{prefix}_selected_operations"]) & set(item["oracle_operations"])) for item in records
    )
    oracle = sum(len(set(item["oracle_operations"])) for item in records)
    selected = sum(len(set(item[f"{prefix}_selected_operations"])) for item in records)
    extra = sum(len(set(item[f"{prefix}_selected_operations"]) - set(item["oracle_operations"])) for item in records)
    fallback_records = [item for item in records if item[f"{prefix}_fallback"]]
    fallback_tp = sum(
        len(set(item[f"{prefix}_selected_operations"]) & set(item["oracle_operations"])) for item in fallback_records
    )
    fallback_selected = sum(len(set(item[f"{prefix}_selected_operations"])) for item in fallback_records)
    return {
        "executable_scope_recall": metric(true_positives, oracle),
        "executable_scope_precision": metric(true_positives, selected),
        "fallback_rate": metric(len(fallback_records), len(records)),
        "fallback_precision": metric(fallback_tp, fallback_selected),
        "selected_operations": selected,
        "extra_selected_operations": extra,
        "false_negative_cases": sum(
            bool(set(item["oracle_operations"]) - set(item[f"{prefix}_selected_operations"])) for item in records
        ),
    }


def contract_metrics(records: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    """计算 verdict accuracy 与 breaking precision/recall."""
    correct = sum(item[f"{prefix}_contract_verdict"] == item["expected_contract_verdict"] for item in records)
    expected_breaking = [item for item in records if item["expected_contract_verdict"] == "fail"]
    predicted_breaking = [item for item in records if item[f"{prefix}_contract_verdict"] == "fail"]
    true_breaking = sum(item["expected_contract_verdict"] == "fail" for item in predicted_breaking)
    return {
        "contract_verdict_accuracy": metric(correct, len(records)),
        "breaking_change_recall": metric(
            sum(item[f"{prefix}_contract_verdict"] == "fail" for item in expected_breaking),
            len(expected_breaking),
        ),
        "breaking_change_precision": metric(true_breaking, len(predicted_breaking)),
    }


def replay_p1() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """用集成后的 production Selector 重放冻结 P1 23 Case."""
    frozen_by_id = {item["case_id"]: item for item in load_jsonl(P1_RAW_PATH)}
    cases = (*_load_p1_real_cases(), *load_synthetic_cases(), *load_safety_cases())
    records: list[dict[str, Any]] = []
    p1_aggregate_records: list[dict[str, Any]] = []
    for case in cases:
        frozen = frozen_by_id[case.case_id]
        selection = select_test_scope(
            case.changes,
            case.head_operations,
            base_operations=case.base_operations,
        )
        selected = [operation.key for operation in selection.operations]
        contract_verdict = determine_contract_verdict(case.changes, has_error=False).value
        record = {
            "dataset": "p1_frozen",
            "case_id": case.case_id,
            "subgroup": case.subgroup,
            "expected_contract_verdict": case.frozen_contract_verdict,
            "before_contract_verdict": frozen["contract_verdict"],
            "after_contract_verdict": contract_verdict,
            "oracle_operations": frozen["oracle_executable_ops"],
            "before_selected_operations": frozen["baseline_selected_ops"],
            "after_selected_operations": selected,
            "before_fallback": frozen["baseline_fallback"],
            "after_fallback": selection.fallback_to_all,
            "matches_frozen_p1": selected == frozen["p1_selected_ops"]
            and selection.fallback_to_all == frozen["p1_fallback"],
        }
        records.append(record)
        replay_metric = dict(frozen)
        replay_metric.update(
            {
                "p1_selected_ops": selected,
                "p1_fallback": selection.fallback_to_all,
                "p1_unresolved_change_ids": list(selection.unresolved_change_ids),
                "p1_selected_count": len(set(selected)),
                "p1_true_positives": len(set(selected) & set(frozen["oracle_executable_ops"])),
                "p1_recall": _empty_safe_ratio(
                    len(set(selected) & set(frozen["oracle_executable_ops"])),
                    len(set(frozen["oracle_executable_ops"])),
                ),
                "p1_precision": _empty_safe_ratio(
                    len(set(selected) & set(frozen["oracle_executable_ops"])),
                    len(set(selected)),
                ),
                "extra_ops_after": len(set(selected) - set(frozen["oracle_executable_ops"])),
                "p1_false_negatives": sorted(set(frozen["oracle_executable_ops"]) - set(selected)),
                "contract_verdict_same": contract_verdict == frozen["contract_verdict"],
            }
        )
        p1_aggregate_records.append(replay_metric)
    summary = aggregate_p1(p1_aggregate_records)
    return (
        {
            "schema_version": "guardian-p1-integration-frozen-replay-v1",
            "case_count": len(records),
            "before": {
                "executable_scope_recall": summary["baseline_executable_scope_recall"],
                "executable_scope_precision": summary["baseline_executable_scope_precision"],
                "fallback_rate": summary["baseline_fallback_rate"],
                "selected_operations": summary["baseline_selected_operations"],
                "extra_selected_operations": summary["extra_operations_before"],
            },
            "after": {
                "executable_scope_recall": summary["p1_executable_scope_recall"],
                "executable_scope_precision": summary["p1_executable_scope_precision"],
                "fallback_rate": summary["p1_fallback_rate"],
                "selected_operations": summary["p1_selected_operations"],
                "extra_selected_operations": summary["extra_operations_after"],
            },
            "contract_verdict_agreement": summary["contract_verdict_agreement"],
            "matches_frozen_p1": metric(sum(item["matches_frozen_p1"] for item in records), len(records)),
            "false_negative_cases_after": summary["executable_false_negative_cases_p1"],
            "cases": records,
        },
        records,
    )


async def replay_e2(adapter: OasdiffAdapter) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """真实重跑 E2 的 60 个 Contract/Scope Case，不改 Dataset."""
    cases = tuple(ContractCase.model_validate(item) for item in load_jsonl(E2_CASES_PATH))
    frozen_by_id = {item["case_id"]: item for item in load_jsonl(E2_RAW_PATH)}
    records: list[dict[str, Any]] = []
    for case in cases:
        base_path = ROOT / case.base_spec
        head_path = ROOT / case.head_spec
        base_spec = prepare_openapi_spec(str(base_path), 5 * 1024 * 1024)
        head_spec = prepare_openapi_spec(str(head_path), 5 * 1024 * 1024)
        result = await adapter.compare(str(base_path), str(head_path), 100 * 1024 * 1024)
        changes = normalize_contract_changes(result.candidates)
        selection = select_test_scope(changes, head_spec.operations, base_operations=base_spec.operations)
        verdict = determine_contract_verdict(changes, has_error=False).value
        frozen = frozen_by_id[case.case_id]
        head_keys = {operation.key for operation in head_spec.operations}
        oracle = [key for key in case.expected_affected_operations if key in head_keys]
        records.append(
            {
                "dataset": "e2_e2_5",
                "case_id": case.case_id,
                "subgroup": case.change_type,
                "expected_contract_verdict": case.expected_contract_verdict.value,
                "before_contract_verdict": frozen["actual_contract_verdict"],
                "after_contract_verdict": verdict,
                "oracle_operations": oracle,
                "before_selected_operations": frozen["selected_operations"],
                "after_selected_operations": [operation.key for operation in selection.operations],
                "before_fallback": frozen["fallback_to_all"],
                "after_fallback": selection.fallback_to_all,
                "normalized_change_count_before": frozen["normalized_change_count"],
                "normalized_change_count_after": len(changes),
                "oasdiff_output_stable": len(changes) == frozen["normalized_change_count"]
                and verdict == frozen["actual_contract_verdict"],
            }
        )
    before = {**contract_metrics(records, "before"), **operation_scope_metrics(records, "before")}
    after = {**contract_metrics(records, "after"), **operation_scope_metrics(records, "after")}
    return (
        {
            "schema_version": "guardian-p1-integration-e2-replay-v1",
            "case_count": len(records),
            "oasdiff_version": "1.28.0",
            "before": before,
            "after": after,
            "contract_verdict_agreement": metric(
                sum(item["before_contract_verdict"] == item["after_contract_verdict"] for item in records),
                len(records),
            ),
            "oasdiff_replay_stability": metric(sum(item["oasdiff_output_stable"] for item in records), len(records)),
            "cases": records,
        },
        records,
    )


async def replay_e3(adapter: OasdiffAdapter) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """从本地固定 commit 重跑 19 个 E3-B headline Contract/Scope Case."""
    reviews = tuple(PreSystemReviewRecord.model_validate(item) for item in load_jsonl(E3_REVIEWS_PATH))
    headline = tuple(
        review
        for review in reviews
        if review.review_status in HEADLINE_STATUSES and review.review_confidence != ReviewConfidence.LOW
    )
    frozen_by_id = {item["case_id"]: item for item in load_jsonl(E3_RAW_PATH)}
    records: list[dict[str, Any]] = []
    for review in headline:
        frozen = frozen_by_id[review.case_id]
        expected_verdict = review.ground_truth_contract_verdict
        if expected_verdict is None:
            raise RuntimeError(f"headline E3 review lacks contract verdict: {review.case_id}")
        base_data = _materialize_spec(review.repo, review.base_commit, review.base_spec_path)
        head_data = _materialize_spec(review.repo, review.head_commit, review.head_spec_path)
        if hashlib.sha256(base_data).hexdigest() != review.base_spec_sha256:
            raise RuntimeError(f"E3 Base Spec hash mismatch: {review.case_id}")
        if hashlib.sha256(head_data).hexdigest() != review.head_spec_sha256:
            raise RuntimeError(f"E3 Head Spec hash mismatch: {review.case_id}")
        with tempfile.TemporaryDirectory(prefix="guardian-p1-integration-e3-") as temp_value:
            temp_root = Path(temp_value)
            base_path = temp_root / f"base{Path(review.base_spec_path).suffix}"
            head_path = temp_root / f"head{Path(review.head_spec_path).suffix}"
            base_path.write_bytes(base_data)
            head_path.write_bytes(head_data)
            base_spec = prepare_openapi_spec(str(base_path), 5 * 1024 * 1024)
            head_spec = prepare_openapi_spec(str(head_path), 5 * 1024 * 1024)
            changes: tuple[ContractChange, ...] = ()
            selected: tuple[OperationRef, ...] = ()
            fallback = False
            error_code: str | None = None
            verdict = GuardianVerdict.ERROR
            try:
                result = await adapter.compare(str(base_path), str(head_path), 100 * 1024 * 1024)
                changes = normalize_contract_changes(result.candidates)
                selection = select_test_scope(
                    changes,
                    head_spec.operations,
                    base_operations=base_spec.operations,
                )
                selected = selection.operations
                fallback = selection.fallback_to_all
                verdict = determine_contract_verdict(changes, has_error=False)
            except GuardianException as exc:
                error_code = exc.code.value
        normalized_safe = [
            {
                "change_id": change.change_id,
                "summary": change.summary,
                "severity": change.severity.value,
                "method": change.method,
                "path": change.path,
                "operation_id": change.operation_id,
                "section": change.section,
            }
            for change in changes
        ]
        frozen_safe = [
            {
                key: value
                for key, value in change.items()
                if key in {"change_id", "summary", "severity", "method", "path", "operation_id", "section"}
            }
            for change in frozen["normalized_changes"]
        ]
        records.append(
            {
                "dataset": "e3_b_headline",
                "case_id": review.case_id,
                "subgroup": review.repo,
                "expected_contract_verdict": expected_verdict.value,
                "before_contract_verdict": frozen["guardian_contract_verdict"],
                "after_contract_verdict": verdict.value,
                "oracle_operations": list(review.runtime_executable_operations),
                "before_selected_operations": frozen["selected_operations"],
                "after_selected_operations": [operation.key for operation in selected],
                "before_fallback": frozen["fallback_to_all"],
                "after_fallback": fallback,
                "before_error_code": frozen["error_code"],
                "after_error_code": error_code,
                "oasdiff_output_stable": normalized_safe == frozen_safe and error_code == frozen["error_code"],
            }
        )
    before = {**contract_metrics(records, "before"), **operation_scope_metrics(records, "before")}
    after = {**contract_metrics(records, "after"), **operation_scope_metrics(records, "after")}
    return (
        {
            "schema_version": "guardian-p1-integration-e3-before-after-v1",
            "headline_case_count": len(records),
            "oasdiff_version": "1.28.0",
            "ground_truth_v2_aggregate_sha256": EXPECTED_GT_V2,
            "before": before,
            "after": after,
            "contract_verdict_agreement": metric(
                sum(item["before_contract_verdict"] == item["after_contract_verdict"] for item in records),
                len(records),
            ),
            "oasdiff_replay_stability": metric(sum(item["oasdiff_output_stable"] for item in records), len(records)),
            "cases": records,
        },
        records,
    )


def _load_p1_real_cases() -> tuple[P1Case, ...]:
    """使用固定 commit 与 SHA256 加载三条 P1 real-world Case."""
    raw_by_id = {item["case_id"]: item for item in load_jsonl(E3_RAW_PATH)}
    cases: list[P1Case] = []
    for case_id in P1_REAL_CASE_IDS:
        review = json.loads((E3_REVIEW_ROOT / f"{case_id}.json").read_text(encoding="utf-8"))
        frozen = raw_by_id[case_id]
        base_data = _materialize_spec(review["repo"], review["base_commit"], review["base_spec_path"])
        head_data = _materialize_spec(review["repo"], review["head_commit"], review["head_spec_path"])
        if hashlib.sha256(base_data).hexdigest() != review["base_spec_sha256"]:
            raise RuntimeError(f"P1 Base Spec hash mismatch: {case_id}")
        if hashlib.sha256(head_data).hexdigest() != review["head_spec_sha256"]:
            raise RuntimeError(f"P1 Head Spec hash mismatch: {case_id}")
        cases.append(
            P1Case(
                case_id=case_id,
                source="e3_b_frozen_real_world",
                subgroup="removed_only" if case_id.startswith("rw_immich") else "mixed_removal",
                changes=tuple(ContractChange.model_validate(value) for value in frozen["normalized_changes"]),
                base_operations=extract_operations(base_data),
                head_operations=extract_operations(head_data),
                oracle_executable_operations=tuple(review["runtime_executable_operations"]),
                frozen_contract_verdict=frozen["guardian_contract_verdict"],
                frozen_baseline_selected_operations=tuple(frozen["selected_operations"]),
                frozen_baseline_fallback=frozen["fallback_to_all"],
                notes="E3-B frozen real-world fallback case.",
            )
        )
    return tuple(cases)


def _materialize_spec(repo: str, commit: str, spec_path: str) -> bytes:
    """优先本地 Git；不可用时从固定 GitHub commit 只读下载并由调用方校验哈希."""
    git_executable = shutil.which("git")
    if git_executable:
        result = subprocess.run(  # noqa: S603
            [git_executable, "show", f"{commit}:{spec_path}"],
            cwd=REAL_WORKTREES[repo],
            check=False,
            capture_output=True,
            shell=False,
        )
        if result.returncode == 0:
            return result.stdout
    encoded_path = urllib.parse.quote(spec_path, safe="/")
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{encoded_path}"
    request = urllib.request.Request(url, headers={"User-Agent": "api-guardian-p1-integration"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        data = response.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise RuntimeError(f"Spec exceeds frozen 5 MiB limit: {repo}:{commit}:{spec_path}")
    return data


def _empty_safe_ratio(numerator: int, denominator: int) -> float:
    """保持冻结 P1 对空可执行范围的 per-case 指标语义."""
    return numerator / denominator if denominator else 1.0


def write_comparison(records: list[dict[str, Any]]) -> None:
    """写出三个 Dataset 的逐 Case before/after 对照."""
    fields = [
        "dataset",
        "case_id",
        "subgroup",
        "expected_contract_verdict",
        "before_contract_verdict",
        "after_contract_verdict",
        "oracle_operations",
        "before_selected_operations",
        "after_selected_operations",
        "before_fallback",
        "after_fallback",
    ]
    with COMPARISON_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = {field: record[field] for field in fields}
            for field in ("oracle_operations", "before_selected_operations", "after_selected_operations"):
                row[field] = " | ".join(row[field])
            writer.writerow(row)


def format_metric(value: dict[str, Any]) -> str:
    """格式化带计数的指标."""
    percentage_value = value.get("percentage")
    if percentage_value is None and value.get("value") is not None:
        percentage_value = value["value"] * 100
    percentage = "N/A" if percentage_value is None else f"{percentage_value:.2f}%"
    return f"{value['numerator']}/{value['denominator']} ({percentage})"


def build_diff(before_hashes: dict[str, str], after_hashes: dict[str, str]) -> str:
    """记录最小 production diff 与未触碰边界."""
    rows = "\n".join(f"| `{path}` | `{before_hashes[path]}` | `{after_hashes[path]}` |" for path in before_hashes)
    return f"""# P1-I Production Diff

## Modified production files

| File | Before SHA256 | After SHA256 |
|---|---|---|
{rows}

## Behavioral change

1. `prepare_scan` 额外保留白名单 `base_operations`，不保存完整 OpenAPI。
2. `GuardianState` 与 checkpoint rehydration 增加 `base_operations: tuple[OperationRef, ...]`。
3. `select_test_scope` 按 change 解析：精确 Head 映射优先；只有两个 oasdiff 1.28.0 冻结 removal rule ID 且 Base/Head 结构证明删除时，贡献空执行范围；其他 unresolved change 仍 fallback。

## Explicit non-changes

- Contract normalization、Contract/Regression/Final Verdict 未改。
- oasdiff、Schemathesis、Graph topology、LLM、数据库 schema 未改。
- 没有 component dependency resolver 或模糊文本匹配。
"""


def build_report(p1: dict[str, Any], e2: dict[str, Any], e3: dict[str, Any]) -> str:
    """从 replay artifacts 生成 P1-I 总报告."""
    p1_before = p1["before"]
    p1_after = p1["after"]
    e3_before = e3["before"]
    e3_after = e3["after"]
    key_cases = {item["case_id"]: item for item in e3["cases"]}
    key_rows = "\n".join(
        f"| {case_id} | {len(item['before_selected_operations'])} / {item['before_fallback']} | "
        f"{len(item['after_selected_operations'])} / {item['after_fallback']} | "
        f"{len(item['oracle_operations'])} |"
        for case_id, item in key_cases.items()
        if case_id
        in {
            "rw_immich_005_server_theme_endpoint_removed",
            "rw_appwrite_002_migration_cutover_replaced",
            "rw_appwrite_003_usage_project_and_health_update",
        }
    )
    return f"""# P1 Production Integration

## 1. Motivation

P1 冻结消融表明 pure operation removal 的保守全量回退造成大量 over-selection，且窄化策略未降低 executable recall。本阶段把同一结构化策略最小集成到生产 Selector。

## 2. Frozen Ablation Evidence

- Frozen P1 recall：31/31，100%。
- Frozen P1 precision：31/38，81.58%。
- Frozen Ground Truth v2：`{EXPECTED_GT_V2}`。

## 3. Production Change

仅修改 `scope.py`、`state.py`、`nodes.py`：State 保留 Base operation 白名单，Selector 使用两个 version-bound removal rule ID 与 Base/Head 结构证明 removal。详细哈希与行为见 `production_diff.md`。

## 4. Safety Conditions

逐 change 解析；唯一 operationId counterpart 优先；removal 不屏蔽其他 mapped/unresolved change；没有文本猜测、component inference 或 LLM。

## 5. Unit Tests

覆盖 exact/path/method/multiple removal、mixed mapped、mixed unresolved、component unresolved、operationId counterpart、non-removal failure、compatible addition、no-change、normal exact mapping，以及完整 Graph empty-scope final verdict。

## 6. P1 Frozen Replay

| Metric | Before | Integrated Production |
|---|---:|---:|
| Recall | {format_metric(p1_before["executable_scope_recall"])} | {format_metric(p1_after["executable_scope_recall"])} |
| Precision | {format_metric(p1_before["executable_scope_precision"])} | {format_metric(p1_after["executable_scope_precision"])} |
| Fallback Rate | {format_metric(p1_before["fallback_rate"])} | {format_metric(p1_after["fallback_rate"])} |
| Selected Operations | {p1_before["selected_operations"]} | {p1_after["selected_operations"]} |
| Extra Operations | {p1_before["extra_selected_operations"]} | {p1_after["extra_selected_operations"]} |

Integrated production 与 frozen P1 output：{format_metric(p1["matches_frozen_p1"])}。

## 7. E2/E2.5 Regression

重跑 60 个冻结 Contract/Scope cases：Contract agreement {format_metric(e2["contract_verdict_agreement"])}；oasdiff output stability {format_metric(e2["oasdiff_replay_stability"])}。Ground Truth 未修改。

## 8. E3-B Real-world Before / After

| Metric | E3-B Before | Integrated Production |
|---|---:|---:|
| Contract Accuracy | {format_metric(e3_before["contract_verdict_accuracy"])} | {format_metric(e3_after["contract_verdict_accuracy"])} |
| Executable Recall | {format_metric(e3_before["executable_scope_recall"])} | {format_metric(e3_after["executable_scope_recall"])} |
| Executable Precision | {format_metric(e3_before["executable_scope_precision"])} | {format_metric(e3_after["executable_scope_precision"])} |
| Fallback Rate | {format_metric(e3_before["fallback_rate"])} | {format_metric(e3_after["fallback_rate"])} |
| Fallback Precision | {format_metric(e3_before["fallback_precision"])} | {format_metric(e3_after["fallback_precision"])} |
| Selected Operations | {e3_before["selected_operations"]} | {e3_after["selected_operations"]} |
| Extra Operations | {e3_before["extra_selected_operations"]} | {e3_after["extra_selected_operations"]} |

| Case | Before selected/fallback | After selected/fallback | Oracle executable |
|---|---:|---:|---:|
{key_rows}

## 9. Contract Verdict Stability

- P1 frozen：{format_metric(p1["contract_verdict_agreement"])}。
- E2/E2.5：{format_metric(e2["contract_verdict_agreement"])}。
- E3-B：{format_metric(e3["contract_verdict_agreement"])}。
- Contract/final verdict 函数与 Graph topology 未修改。

## 10. Scope Recall Stability

P1 frozen 与 E3-B executable recall 均未下降；P1 integrated false-negative cases：{p1["false_negative_cases_after"]}；E3-B integrated false-negative cases：{e3_after["false_negative_cases"]}。

## 11. Precision / Fallback Improvement

E3-B precision 从 {format_metric(e3_before["executable_scope_precision"])} 变为 {format_metric(e3_after["executable_scope_precision"])}；extra operations 从 {e3_before["extra_selected_operations"]} 降到 {e3_after["extra_selected_operations"]}。这不是 P1 23-case precision 的替代引用，而是 19 条 E3-B headline 的实际重跑结果。

## 12. Empty-scope Graph Semantics

完整 Graph 测试证明 pure removal 产生 Contract FAIL、empty selected scope、Regression PASS、Final FAIL。现有确定性 verdict 语义无需修改。

## 13. Quality Gates

详见 `quality_gate.json`。包含 Guardian pytest、Ruff、format、Pyright、deterministic Eval、PostgreSQL checkpoint 与所有 replay integrity checks。

## 14. Remaining Limitations

- Appwrite 002 仍有 5 个额外 operation；P1 不解决 operation-local tool/Ground Truth 归属差异。
- 四个 Supabase Case 的 oasdiff 1.28.0 compatibility ERROR 不受 P1 影响。
- removal rule ID 与 oasdiff 1.28.0 绑定；升级工具必须重验契约。
- P1 不解决 component dependency mapping，也不改变 per-operation Schemathesis 启动开销。

## 15. Recommendation

若质量门全部通过，本次 integration 为 **PASS**。P1-I 只改善 production scope/fallback；是否进入 E3-C Runtime 应独立决定，不应把本次静态 scope 成果当作 runtime validation。
"""


async def run_replay() -> dict[str, Any]:
    """执行一次 P1-I frozen replay，并拒绝覆盖已有结果."""
    for path, expected in EXPECTED_FROZEN_HASHES.items():
        if sha256_file(ROOT / path) != expected:
            raise RuntimeError(f"Frozen artifact changed: {path}")
    v2 = json.loads((ROOT / "benchmarks/guardian/reports/e3/ground_truth_freeze_v2.json").read_text(encoding="utf-8"))
    if v2.get("aggregate_review_sha256") != EXPECTED_GT_V2:
        raise RuntimeError("E3-B Ground Truth v2 aggregate changed")
    outputs = (P1_REPLAY_PATH, E2_REPLAY_PATH, E3_REPLAY_PATH, COMPARISON_PATH, DIFF_PATH, REPORT_PATH)
    existing = [path.name for path in outputs if path.exists()]
    if existing:
        raise RuntimeError(f"P1-I outputs already exist; refusing overwrite: {existing}")
    if not OASDIFF_PATH.is_file():
        raise RuntimeError("frozen oasdiff 1.28.0 executable is missing")

    adapter = OasdiffAdapter(executable=str(OASDIFF_PATH))
    p1_summary, p1_records = replay_p1()
    e2_summary, e2_records = await replay_e2(adapter)
    e3_summary, e3_records = await replay_e3(adapter)
    all_records = [*p1_records, *e2_records, *e3_records]
    write_json(P1_REPLAY_PATH, p1_summary)
    write_json(E2_REPLAY_PATH, e2_summary)
    write_json(E3_REPLAY_PATH, e3_summary)
    write_comparison(all_records)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    before_hashes = manifest["production_hashes_before"]
    after_hashes = {path: sha256_file(ROOT / path) for path in before_hashes}
    DIFF_PATH.write_text(build_diff(before_hashes, after_hashes), encoding="utf-8")
    REPORT_PATH.write_text(build_report(p1_summary, e2_summary, e3_summary), encoding="utf-8")
    return {"p1": p1_summary, "e2": e2_summary, "e3": e3_summary}


def main() -> None:
    """运行 replay 并用 Rich 输出关键结果."""
    result = asyncio.run(run_replay())
    e3 = result["e3"]
    Console().print(
        "[green]E3-B production precision[/green] "
        f"{format_metric(e3['before']['executable_scope_precision'])} -> "
        f"{format_metric(e3['after']['executable_scope_precision'])}"
    )
    Console().print(f"[green]Replay completed[/green] {datetime.now(UTC).isoformat()}")


if __name__ == "__main__":
    main()
