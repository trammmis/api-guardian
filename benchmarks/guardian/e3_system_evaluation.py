"""E3-B 真实世界 Contract/Scope 冻结 System Evaluation."""

import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from app.services.guardian.errors import GuardianException
from app.services.guardian.models import GuardianVerdict
from app.services.guardian.oasdiff import OasdiffAdapter, normalize_contract_changes
from app.services.guardian.scope import determine_contract_verdict, select_test_scope
from app.services.guardian.security import prepare_openapi_spec
from benchmarks.guardian.e3_models import (
    E3MissClassification,
    E3MissRecord,
    E3SystemResult,
    GuardianChangeEvidence,
    HeadOperationEvidence,
    OasdiffChangeEvidence,
)
from benchmarks.guardian.real_world_models import (
    ContractVerdict,
    PreSystemReviewRecord,
    ReviewConfidence,
    ReviewStatus,
)

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "benchmarks" / "guardian" / "reports" / "e3"
V1_MANIFEST_PATH = REPORT_ROOT / "ground_truth_freeze_manifest.json"
V1_ALIAS_PATH = REPORT_ROOT / "ground_truth_freeze_v1.json"
V2_MANIFEST_PATH = REPORT_ROOT / "ground_truth_freeze_v2.json"
V2_HASH_PATH = REPORT_ROOT / "ground_truth_freeze_v2_file_sha256.txt"
V2_REVIEW_JSONL_PATH = REPORT_ROOT / "review_records_v2.jsonl"
AMENDMENTS_PATH = REPORT_ROOT / "ground_truth_amendments.jsonl"
RAW_RESULTS_PATH = REPORT_ROOT / "raw_guardian_results.jsonl"
MISSES_PATH = REPORT_ROOT / "real_world_misses.jsonl"
SUMMARY_PATH = REPORT_ROOT / "summary.json"
SUMMARY_CSV_PATH = REPORT_ROOT / "summary.csv"
RUN_MANIFEST_PATH = REPORT_ROOT / "run_manifest.json"
REPORT_PATH = REPORT_ROOT / "e3_contract_scope_report.md"

EXPECTED_V1_AGGREGATE = "3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36"
EXPECTED_V1_FILE_HASH = "65576075bc38149e5105106fc9401e1ad8f88b0a0f474144fad7185c3431194d"
EXPECTED_V2_AGGREGATE = "7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94"
OASDIFF_ARCHIVE_SHA256 = "cef8ec7cdd32ab4ce9c188f9dbb50452a91fa36cc11c8e1dcf340b19282c3ce1"
MAX_OPENAPI_BYTES = 5 * 1024 * 1024
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024
MAX_SCAN_ARTIFACT_BYTES = 100 * 1024 * 1024
SUBPROCESS_TIMEOUT_SECONDS = 30.0

SYSTEM_OUTPUT_PATHS = (
    RAW_RESULTS_PATH,
    MISSES_PATH,
    SUMMARY_PATH,
    SUMMARY_CSV_PATH,
    RUN_MANIFEST_PATH,
    REPORT_PATH,
)
HEADLINE_STATUSES = {ReviewStatus.CONFIRMED, ReviewStatus.PROBABLE}


def sha256_bytes(data: bytes) -> str:
    """计算原始字节 SHA256."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value: Any) -> bytes:
    """生成稳定、可审计 JSON."""
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_json(path: Path, value: Any) -> None:
    """写出 JSON artifact."""
    path.write_bytes(json_bytes(value))


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    """写出 JSONL artifact."""
    data = b"".join(json.dumps(value, ensure_ascii=False, sort_keys=True).encode() + b"\n" for value in values)
    path.write_bytes(data)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取非空 JSONL artifact."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or any(not line for line in lines):
        raise ValueError(f"invalid JSONL: {path.relative_to(ROOT).as_posix()}")
    return [json.loads(line) for line in lines]


def verify_file(path: Path, expected_hash: str, expected_bytes: int | None = None) -> None:
    """验证 artifact SHA256 和可选字节数."""
    if expected_bytes is not None and path.stat().st_size != expected_bytes:
        raise RuntimeError(f"artifact byte count mismatch: {path.relative_to(ROOT).as_posix()}")
    if sha256_file(path) != expected_hash:
        raise RuntimeError(f"artifact hash mismatch: {path.relative_to(ROOT).as_posix()}")


def verify_freezes_and_preflight(*, require_system_outputs_absent: bool) -> dict[str, Any]:
    """验证 v1/v2、Production Core 和 E2/E2.5 冻结边界."""
    verify_file(V1_MANIFEST_PATH, EXPECTED_V1_FILE_HASH)
    if V1_ALIAS_PATH.read_bytes() != V1_MANIFEST_PATH.read_bytes():
        raise RuntimeError("ground_truth_freeze_v1 alias differs from original v1")
    v1 = json.loads(V1_MANIFEST_PATH.read_text(encoding="utf-8"))
    if v1["aggregate_review_sha256"] != EXPECTED_V1_AGGREGATE:
        raise RuntimeError("v1 aggregate hash changed")
    for artifact in v1["review_artifacts"]:
        verify_file(ROOT / artifact["path"], artifact["sha256"], artifact["bytes"])

    expected_v2_file_hash = V2_HASH_PATH.read_text(encoding="utf-8").split()[0]
    verify_file(V2_MANIFEST_PATH, expected_v2_file_hash)
    v2 = json.loads(V2_MANIFEST_PATH.read_text(encoding="utf-8"))
    if v2["status"] != "human_confirmed" or v2["aggregate_review_sha256"] != EXPECTED_V2_AGGREGATE:
        raise RuntimeError("final v2 is not the human-confirmed Ground Truth")
    aggregate = sha256_bytes(
        "\n".join(
            f"{item['canonical_path']}:{item['sha256']}"
            for item in sorted(v2["review_artifacts"], key=lambda item: item["canonical_path"])
        ).encode()
    )
    if aggregate != EXPECTED_V2_AGGREGATE:
        raise RuntimeError("final v2 aggregate cannot be reproduced")
    for artifact in v2["review_artifacts"]:
        verify_file(ROOT / artifact["storage_path"], artifact["sha256"], artifact["bytes"])
    amendments = load_jsonl(AMENDMENTS_PATH)
    if not amendments or any(item.get("confirmation_state") != "human_confirmed" for item in amendments):
        raise RuntimeError("Ground Truth amendments are not human-confirmed")

    for path, expected in v2["production_core_hashes"].items():
        verify_file(ROOT / path, expected)
    for path, expected in v2["frozen_prior_artifact_hashes"].items():
        verify_file(ROOT / path, expected)
    if require_system_outputs_absent:
        present = [path.relative_to(ROOT).as_posix() for path in SYSTEM_OUTPUT_PATHS if path.exists()]
        if present:
            raise RuntimeError(f"System Output existed before E3-B evaluation: {present}")
    return v2


def load_ground_truth() -> tuple[PreSystemReviewRecord, ...]:
    """加载 human-confirmed v2 Ground Truth."""
    records = tuple(PreSystemReviewRecord.model_validate(item) for item in load_jsonl(V2_REVIEW_JSONL_PATH))
    if len(records) != 20:
        raise RuntimeError(f"expected 20 E3 records, found {len(records)}")
    return records


def fetch_spec(record: PreSystemReviewRecord, side: str) -> bytes:
    """从固定 Git commit URL 读取 OpenAPI source artifact."""
    commit = record.base_commit if side == "base" else record.head_commit
    spec_path = record.base_spec_path if side == "base" else record.head_spec_path
    expected_hash = record.base_spec_sha256 if side == "base" else record.head_spec_sha256
    encoded_path = urllib.parse.quote(spec_path, safe="/")
    url = f"https://raw.githubusercontent.com/{record.repo}/{commit}/{encoded_path}"
    request = urllib.request.Request(url, headers={"User-Agent": "api-guardian-e3-system-evaluation"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        data = response.read(MAX_OPENAPI_BYTES + 1)
    if len(data) > MAX_OPENAPI_BYTES:
        raise RuntimeError(f"Spec exceeds frozen 5 MiB limit: {record.case_id}:{side}")
    if sha256_bytes(data) != expected_hash:
        raise RuntimeError(f"Spec SHA256 differs from Ground Truth: {record.case_id}:{side}")
    return data


def safe_suffix(spec_path: str) -> str:
    """只保留 Guardian 支持的 OpenAPI 文件后缀."""
    suffix = Path(spec_path).suffix.lower()
    return suffix if suffix in {".json", ".yaml", ".yml"} else ".yaml"


async def run_case(
    record: PreSystemReviewRecord,
    adapter: OasdiffAdapter,
    run_id: str,
) -> E3SystemResult:
    """执行一个 Case 的 frozen contract/scope pipeline."""
    started = time.perf_counter()
    base_data = fetch_spec(record, "base")
    head_data = fetch_spec(record, "head")
    candidates: tuple[OasdiffChangeEvidence, ...] = ()
    normalized: tuple[GuardianChangeEvidence, ...] = ()
    head_operation_evidence: tuple[HeadOperationEvidence, ...] = ()
    selected: tuple[str, ...] = ()
    fallback = False
    unresolved: tuple[str, ...] = ()
    exit_code: int | None = None
    artifact_bytes = 0
    error_code: str | None = None
    verdict = GuardianVerdict.ERROR

    with tempfile.TemporaryDirectory(prefix="guardian-e3-") as temp_value:
        temp_root = Path(temp_value)
        base_path = temp_root / f"base{safe_suffix(record.base_spec_path)}"
        head_path = temp_root / f"head{safe_suffix(record.head_spec_path)}"
        base_path.write_bytes(base_data)
        head_path.write_bytes(head_data)
        try:
            base_spec = prepare_openapi_spec(str(base_path), MAX_OPENAPI_BYTES)
            head_spec = prepare_openapi_spec(str(head_path), MAX_OPENAPI_BYTES)
            if base_spec.sha256 != record.base_spec_sha256 or head_spec.sha256 != record.head_spec_sha256:
                raise RuntimeError(f"prepared Spec SHA256 mismatch: {record.case_id}")
            if len(base_spec.operations) != record.base_operation_count:
                raise RuntimeError(f"Base operation count mismatch: {record.case_id}")
            if len(head_spec.operations) != record.head_operation_count:
                raise RuntimeError(f"Head operation count mismatch: {record.case_id}")
            head_operation_evidence = tuple(
                HeadOperationEvidence(key=operation.key, operation_id=operation.operation_id)
                for operation in head_spec.operations
            )
            remaining = MAX_SCAN_ARTIFACT_BYTES - len(base_data) - len(head_data)
            result = await adapter.compare(str(base_path), str(head_path), remaining)
            exit_code = result.tool.exit_code
            artifact_bytes = result.tool.artifact_bytes
            candidates = tuple(
                OasdiffChangeEvidence(
                    change_id=item.change_id,
                    summary=item.summary,
                    severity_level=item.severity_level,
                    method=item.method,
                    path=item.path,
                    operation_id=item.operation_id,
                    section=item.section,
                    fingerprint=item.fingerprint,
                )
                for item in result.candidates
            )
            changes = normalize_contract_changes(result.candidates)
            normalized = tuple(
                GuardianChangeEvidence(
                    change_id=item.change_id,
                    summary=item.summary,
                    severity=item.severity.value,
                    method=item.method,
                    path=item.path,
                    operation_id=item.operation_id,
                    section=item.section,
                    fingerprint=item.fingerprint,
                )
                for item in changes
            )
            selection = select_test_scope(changes, head_spec.operations)
            selected = tuple(operation.key for operation in selection.operations)
            fallback = selection.fallback_to_all
            unresolved = selection.unresolved_change_ids
            verdict = determine_contract_verdict(changes, has_error=False)
        except GuardianException as exc:
            error_code = exc.code.value

    return E3SystemResult(
        run_id=f"{run_id}:{record.case_id}",
        measured_at=datetime.now(UTC),
        case_id=record.case_id,
        repo=record.repo,
        base_commit=record.base_commit,
        head_commit=record.head_commit,
        duration_seconds=time.perf_counter() - started,
        base_spec_sha256=record.base_spec_sha256,
        head_spec_sha256=record.head_spec_sha256,
        base_operation_count=record.base_operation_count,
        head_operation_count=record.head_operation_count,
        oasdiff_exit_code=exit_code,
        oasdiff_artifact_bytes=artifact_bytes,
        oasdiff_output=candidates,
        normalized_changes=normalized,
        guardian_contract_verdict=verdict.value,
        selected_operations=selected,
        head_operations=head_operation_evidence,
        fallback_to_all=fallback,
        unresolved_change_ids=unresolved,
        error_code=error_code,
    )


def is_headline(record: PreSystemReviewRecord) -> bool:
    """执行冻结的 E3 headline eligibility 规则."""
    return record.review_status in HEADLINE_STATUSES and record.review_confidence != ReviewConfidence.LOW


def ratio(numerator: int | float, denominator: int | float) -> dict[str, Any]:
    """保留 numerator、denominator、value 和 percentage."""
    value = numerator / denominator if denominator else None
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "percentage": value * 100 if value is not None else None,
    }


def exact_change_operations(result: E3SystemResult) -> set[str]:
    """提取 normalized change 的直接 METHOD + path 证据."""
    return {
        f"{change.method.upper()} {change.path}"
        for change in result.normalized_changes
        if change.method and change.path
    }


def aggregate_metrics(
    records: tuple[PreSystemReviewRecord, ...],
    results_by_case: dict[str, E3SystemResult],
) -> dict[str, Any]:
    """只用 human-confirmed v2 与 frozen System Output 计算指标."""
    eligible = tuple(record for record in records if is_headline(record))
    correct = true_positive = predicted_fail = actual_fail = 0
    impact_tp = impact_expected = impact_selected = 0
    executable_tp = executable_expected = executable_selected = 0
    fallback_cases = fallback_tp = fallback_selected = 0
    removed_total = removed_covered = 0
    component_tp = component_expected = component_selected = 0
    successful_cases = system_error_cases = 0
    successful_impact_tp = successful_impact_expected = successful_impact_selected = 0
    successful_executable_tp = successful_executable_expected = successful_executable_selected = 0
    successful_component_tp = successful_component_expected = successful_component_selected = 0
    reductions: list[float] = []
    breaking_reductions: list[float] = []
    successful_reductions: list[float] = []
    successful_breaking_reductions: list[float] = []

    for record in eligible:
        result = results_by_case[record.case_id]
        expected_verdict = record.ground_truth_contract_verdict
        expected_value = expected_verdict.value if expected_verdict is not None else None
        correct += result.guardian_contract_verdict == expected_value
        expected_breaking = expected_verdict == ContractVerdict.FAIL
        predicted_breaking = result.guardian_contract_verdict == GuardianVerdict.FAIL.value
        actual_fail += expected_breaking
        predicted_fail += predicted_breaking
        true_positive += expected_breaking and predicted_breaking

        contract_scope = set(record.contract_affected_operations)
        executable_scope = set(record.runtime_executable_operations)
        selected = set(result.selected_operations)
        impact_tp += len(contract_scope & selected)
        impact_expected += len(contract_scope)
        impact_selected += len(selected)
        executable_tp += len(executable_scope & selected)
        executable_expected += len(executable_scope)
        executable_selected += len(selected)

        reduction = 1 - len(selected) / result.head_operation_count
        reductions.append(reduction)
        if expected_breaking:
            breaking_reductions.append(reduction)
        if result.error_code is None:
            successful_cases += 1
            successful_impact_tp += len(contract_scope & selected)
            successful_impact_expected += len(contract_scope)
            successful_impact_selected += len(selected)
            successful_executable_tp += len(executable_scope & selected)
            successful_executable_expected += len(executable_scope)
            successful_executable_selected += len(selected)
            successful_reductions.append(reduction)
            if expected_breaking:
                successful_breaking_reductions.append(reduction)
            if "shared_component_changed" in record.change_types:
                successful_component_tp += len(executable_scope & selected)
                successful_component_expected += len(executable_scope)
                successful_component_selected += len(selected)
        else:
            system_error_cases += 1
        if result.fallback_to_all:
            fallback_cases += 1
            fallback_tp += len(executable_scope & selected)
            fallback_selected += len(selected)

        head_keys = {operation.key for operation in result.head_operations}
        removed = contract_scope - head_keys
        exact_changes = exact_change_operations(result)
        removed_total += len(removed)
        removed_covered += len(removed & exact_changes)

        if "shared_component_changed" in record.change_types:
            component_tp += len(executable_scope & selected)
            component_expected += len(executable_scope)
            component_selected += len(selected)

    return {
        "contract_verdict_accuracy": ratio(correct, len(eligible)),
        "breaking_change_recall": ratio(true_positive, actual_fail),
        "breaking_change_precision": ratio(true_positive, predicted_fail),
        "contract_impact_recall": ratio(impact_tp, impact_expected),
        "contract_impact_precision": ratio(impact_tp, impact_selected),
        "executable_regression_scope_recall": ratio(executable_tp, executable_expected),
        "executable_regression_scope_precision": ratio(executable_tp, executable_selected),
        "fallback_rate": ratio(fallback_cases, len(eligible)),
        "fallback_precision": ratio(fallback_tp, fallback_selected),
        "mean_executable_scope_reduction": ratio(sum(reductions), len(reductions)),
        "mean_executable_scope_reduction_breaking_cases": ratio(sum(breaking_reductions), len(breaking_reductions)),
        "removed_operation_coverage": ratio(removed_covered, removed_total),
        "component_change_recall": ratio(component_tp, component_expected),
        "component_change_precision": ratio(component_tp, component_selected),
        "system_execution_success_rate": ratio(successful_cases, len(eligible)),
        "system_error_rate": ratio(system_error_cases, len(eligible)),
        "contract_impact_recall_successful_pipeline": ratio(successful_impact_tp, successful_impact_expected),
        "contract_impact_precision_successful_pipeline": ratio(successful_impact_tp, successful_impact_selected),
        "executable_regression_scope_recall_successful_pipeline": ratio(
            successful_executable_tp, successful_executable_expected
        ),
        "executable_regression_scope_precision_successful_pipeline": ratio(
            successful_executable_tp, successful_executable_selected
        ),
        "fallback_rate_successful_pipeline": ratio(fallback_cases, successful_cases),
        "mean_executable_scope_reduction_successful_pipeline": ratio(
            sum(successful_reductions), len(successful_reductions)
        ),
        "mean_executable_scope_reduction_breaking_successful_pipeline": ratio(
            sum(successful_breaking_reductions), len(successful_breaking_reductions)
        ),
        "component_change_recall_successful_pipeline": ratio(successful_component_tp, successful_component_expected),
        "component_change_precision_successful_pipeline": ratio(
            successful_component_tp, successful_component_selected
        ),
    }


def change_categories(record: PreSystemReviewRecord) -> set[str]:
    """把原始 change types 投影到 non-exclusive 报告类别."""
    values = set(record.change_types)
    categories: set[str] = set()
    if values & {"endpoint_added", "method_added"}:
        categories.add("direct_operation")
    if values & {"endpoint_removed", "method_removed"}:
        categories.add("removed_operation")
    if any(value.startswith("request_") for value in values):
        categories.add("request_schema")
    if values & {
        "response_schema_changed",
        "response_property_added",
        "response_property_removed",
        "response_type_changed",
    }:
        categories.add("response_schema")
    if any(value.startswith("response_status_") for value in values):
        categories.add("response_status")
    if "shared_component_changed" in values:
        categories.add("shared_component")
    if "documentation_only" in values:
        categories.add("documentation_only")
    if "mixed_change" in values:
        categories.add("mixed")
    return categories or {"other"}


def subgroup_summaries(
    records: tuple[PreSystemReviewRecord, ...],
    results_by_case: dict[str, E3SystemResult],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成按 repository 和 change category 的同口径指标."""
    eligible = tuple(record for record in records if is_headline(record))
    by_repo: dict[str, Any] = {}
    for repo in sorted({record.repo for record in eligible}):
        subset = tuple(record for record in eligible if record.repo == repo)
        by_repo[repo] = {
            "pairs": len(subset),
            "breaking_cases": sum(record.ground_truth_contract_verdict == ContractVerdict.FAIL for record in subset),
            "metrics": aggregate_metrics(subset, results_by_case),
        }

    by_category: dict[str, Any] = {}
    categories = sorted({category for record in eligible for category in change_categories(record)})
    for category in categories:
        subset = tuple(record for record in eligible if category in change_categories(record))
        by_category[category] = {
            "cases": len(subset),
            "breaking_cases": sum(record.ground_truth_contract_verdict == ContractVerdict.FAIL for record in subset),
            "metrics": aggregate_metrics(subset, results_by_case),
        }
    return by_repo, by_category


def has_exact_mapping_evidence(operation: str, result: E3SystemResult) -> bool:
    """判断 missing operation 是否已有可供 selector 使用的精确证据."""
    if operation in exact_change_operations(result):
        return True
    operation_ids = [item.operation_id for item in result.head_operations if item.key == operation]
    if len(operation_ids) != 1 or operation_ids[0] is None:
        return False
    operation_id = operation_ids[0]
    matching_head = [item for item in result.head_operations if item.operation_id == operation_id]
    return len(matching_head) == 1 and any(change.operation_id == operation_id for change in result.normalized_changes)


def build_misses(
    records: tuple[PreSystemReviewRecord, ...],
    results_by_case: dict[str, E3SystemResult],
) -> tuple[E3MissRecord, ...]:
    """按冻结 taxonomy 生成逐 Case、逐分类的可审计 miss records."""
    misses: list[E3MissRecord] = []
    for record in records:
        result = results_by_case[record.case_id]
        expected_verdict = (
            record.ground_truth_contract_verdict.value if record.ground_truth_contract_verdict is not None else None
        )
        contract_scope = set(record.contract_affected_operations)
        executable_scope = set(record.runtime_executable_operations)
        selected = set(result.selected_operations)
        head_keys = {operation.key for operation in result.head_operations}
        removed = contract_scope - head_keys
        missing_executable = executable_scope - selected
        over_selected = selected - executable_scope
        common_evidence = tuple(record.evidence) + (
            f"Guardian verdict={result.guardian_contract_verdict}; fallback_to_all={result.fallback_to_all}.",
            f"Ground Truth contract scope={len(contract_scope)}; executable scope={len(executable_scope)}.",
            f"Guardian selected operations={len(selected)}; normalized changes={len(result.normalized_changes)}.",
        )

        def add(
            classification: E3MissClassification,
            likely_cause: str,
            evidence: tuple[str, ...] = common_evidence,
            *,
            candidate_product_fix: bool = False,
            current_record: PreSystemReviewRecord = record,
            current_result: E3SystemResult = result,
            current_expected_verdict: str | None = expected_verdict,
        ) -> None:
            misses.append(
                E3MissRecord(
                    miss_id=f"{current_record.case_id}:{classification.value}",
                    case_id=current_record.case_id,
                    repo=current_record.repo,
                    base_commit=current_record.base_commit,
                    head_commit=current_record.head_commit,
                    classification=classification,
                    ground_truth_contract_verdict=current_expected_verdict,
                    ground_truth_contract_affected_operations=tuple(current_record.contract_affected_operations),
                    ground_truth_runtime_executable_operations=tuple(current_record.runtime_executable_operations),
                    oasdiff_exit_code=current_result.oasdiff_exit_code,
                    oasdiff_output=current_result.oasdiff_output,
                    guardian_contract_verdict=current_result.guardian_contract_verdict,
                    guardian_selected_operations=current_result.selected_operations,
                    fallback_to_all=current_result.fallback_to_all,
                    evidence=evidence,
                    likely_cause=likely_cause,
                    candidate_product_fix=candidate_product_fix,
                )
            )

        if record.review_status == ReviewStatus.AMBIGUOUS:
            add(
                E3MissClassification.GROUND_TRUTH_AMBIGUOUS,
                "Independent pre-system review intentionally did not force a compatibility verdict.",
            )
            continue
        if result.error_code is not None:
            classification = (
                E3MissClassification.GUARDIAN_NORMALIZATION_ERROR
                if result.error_code == "unknown_oasdiff_severity"
                else E3MissClassification.OTHER
            )
            add(classification, f"The frozen pipeline returned deterministic ERROR: {result.error_code}.")
            continue
        if len(result.oasdiff_output) != len(result.normalized_changes):
            add(
                E3MissClassification.GUARDIAN_NORMALIZATION_ERROR,
                "The number of Adapter candidates differs from normalized ContractChange objects.",
                candidate_product_fix=True,
            )
        if expected_verdict == ContractVerdict.FAIL.value and result.guardian_contract_verdict != "fail":
            add(
                E3MissClassification.OASDIFF_FALSE_NEGATIVE,
                "The independent Ground Truth is breaking, but oasdiff 1.28.0 produced no ERROR-level basis for FAIL.",
            )
        elif expected_verdict == ContractVerdict.PASS.value and result.guardian_contract_verdict == "fail":
            add(
                E3MissClassification.OASDIFF_FALSE_POSITIVE,
                "oasdiff 1.28.0 emitted an ERROR-level change that disagrees with the frozen compatible label.",
            )
        if removed:
            add(
                E3MissClassification.REMOVED_OPERATION_NON_EXECUTABLE,
                "The affected Base operation was removed from Head and cannot appear in Head executable scope.",
                common_evidence + (f"Removed operations: {sorted(removed)}.",),
            )
        if missing_executable:
            exact_misses = {item for item in missing_executable if has_exact_mapping_evidence(item, result)}
            if exact_misses:
                add(
                    E3MissClassification.GUARDIAN_SCOPE_FALSE_NEGATIVE,
                    "Executable Ground Truth operations had exact normalized mapping evidence but were not selected.",
                    common_evidence + (f"Exact-evidence missing operations: {sorted(exact_misses)}.",),
                    candidate_product_fix=True,
                )
            elif result.guardian_contract_verdict == "fail":
                classification = (
                    E3MissClassification.COMPONENT_MAPPING_LIMITATION
                    if "shared_component_changed" in record.change_types
                    else E3MissClassification.OTHER
                )
                add(
                    classification,
                    "Executable affected operations were not selected because normalized output lacked exact mapping evidence.",
                    common_evidence + (f"Missing executable operations: {sorted(missing_executable)}.",),
                )
        if over_selected:
            add(
                E3MissClassification.GUARDIAN_SCOPE_OVER_SELECTION,
                "Guardian selected operations outside frozen RuntimeExecutableScope.",
                common_evidence + (f"Extra selected operations: {sorted(over_selected)}.",),
            )
        if "shared_component_changed" in record.change_types and result.fallback_to_all and over_selected:
            add(
                E3MissClassification.COMPONENT_MAPPING_LIMITATION,
                "An unscoped shared-component change triggered recall-first fallback-to-all.",
            )
    return tuple(misses)


def summarize(
    records: tuple[PreSystemReviewRecord, ...],
    results: tuple[E3SystemResult, ...],
    misses: tuple[E3MissRecord, ...],
) -> dict[str, Any]:
    """聚合 E3-B headline、分层和 miss 指标."""
    results_by_case = {result.case_id: result for result in results}
    metrics = aggregate_metrics(records, results_by_case)
    by_repository, by_change_type = subgroup_summaries(records, results_by_case)
    statuses = Counter(record.review_status.value for record in records)
    raw_change_types = Counter(value for record in records for value in record.change_types)
    classifications = Counter(item.classification.value for item in misses)
    headline = tuple(record for record in records if is_headline(record))
    headline_change_types = Counter(value for record in headline for value in record.change_types)
    removed_cases = [record.case_id for record in headline if "endpoint_removed" in record.change_types]
    component_cases = [record.case_id for record in headline if "shared_component_changed" in record.change_types]
    true_selector_false_negatives = sorted(
        {item.case_id for item in misses if item.classification == E3MissClassification.GUARDIAN_SCOPE_FALSE_NEGATIVE}
    )
    oasdiff_disagreements = sorted(
        {
            item.case_id
            for item in misses
            if item.classification
            in {E3MissClassification.OASDIFF_FALSE_NEGATIVE, E3MissClassification.OASDIFF_FALSE_POSITIVE}
        }
    )
    over_selection_cases = sorted(
        {item.case_id for item in misses if item.classification == E3MissClassification.GUARDIAN_SCOPE_OVER_SELECTION}
    )
    return {
        "stage": "E3-B Real-world Contract + Scope System Evaluation",
        "dataset": {
            "repositories": len({record.repo for record in records}),
            "total_pairs": len(records),
            "headline_pairs": len(headline),
            "review_status_counts": dict(sorted(statuses.items())),
            "raw_change_type_distribution": dict(sorted(raw_change_types.items())),
            "headline_change_type_distribution": dict(sorted(headline_change_types.items())),
            "change_category_membership_is_non_exclusive": True,
        },
        "metric_definitions": {
            "contract_impact": "Guardian selected Head operations compared with ContractAffectedScope, including removed Base operations.",
            "executable_scope": "Guardian selected Head operations compared with frozen RuntimeExecutableScope.",
            "mean_scope_reduction": "Mean of 1 - selected/head operations over headline cases; breaking-only value is supplemental.",
            "removed_operation_coverage": "Removed Ground Truth operations with exact normalized METHOD/path evidence.",
            "component_metrics": "Executable scope micro recall/precision for shared_component_changed headline cases.",
            "end_to_end_scope_metrics": "Headline metrics count deterministic pipeline ERROR as missing scope evidence.",
            "successful_pipeline_metrics": "Supplemental selector view excludes Cases where oasdiff failed before normalization/scope selection.",
        },
        "metrics": metrics,
        "by_repository": by_repository,
        "by_change_type": by_change_type,
        "miss_analysis": {
            "record_count": len(misses),
            "case_count": len({item.case_id for item in misses}),
            "classification_counts": dict(sorted(classifications.items())),
            "removed_operation_cases": removed_cases,
            "component_change_cases": component_cases,
            "true_selector_false_negatives": true_selector_false_negatives,
            "oasdiff_disagreements": oasdiff_disagreements,
            "normalization_error_cases": sorted(
                {
                    item.case_id
                    for item in misses
                    if item.classification == E3MissClassification.GUARDIAN_NORMALIZATION_ERROR
                }
            ),
            "system_error_cases": sorted(
                result.case_id
                for result in results
                if result.error_code is not None and result.case_id in {item.case_id for item in headline}
            ),
            "over_selection_cases": over_selection_cases,
        },
        "runtime_validation": {
            "status": "not_run_e3_b_contract_scope_only",
            "confirmed_suitable_pairs": [],
            "supabase_candidates": [
                {
                    "case_id": "rw_supabase_005_authorize_flow_type_removed",
                    "status": "plausible_pending_service_start_and_version_alignment",
                },
                {
                    "case_id": "rw_supabase_003_oauth_client_optional_fields_added",
                    "status": "blocked_by_oasdiff_spec_load_error_in_e3_b",
                },
                {
                    "case_id": "rw_supabase_001_oauth_grants_endpoints_added",
                    "status": "blocked_by_oasdiff_spec_load_error_in_e3_b",
                },
            ],
            "feasibility": "candidate_only_not_runtime_validated",
        },
    }


def write_summary_csv(summary: dict[str, Any]) -> None:
    """输出 global、repository 和 change-type 指标 CSV."""
    rows: list[dict[str, Any]] = []

    def append(scope: str, group: str, metrics: dict[str, Any]) -> None:
        for name, metric in metrics.items():
            rows.append(
                {
                    "scope": scope,
                    "group": group,
                    "metric": name,
                    "numerator": metric["numerator"],
                    "denominator": metric["denominator"],
                    "value": metric["value"],
                    "percentage": metric["percentage"],
                }
            )

    append("global", "all_headline", summary["metrics"])
    for name, value in summary["by_repository"].items():
        append("repository", name, value["metrics"])
    for name, value in summary["by_change_type"].items():
        append("change_type", name, value["metrics"])
    fieldnames = ("scope", "group", "metric", "numerator", "denominator", "value", "percentage")
    with SUMMARY_CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(tuple(row[field] for field in fieldnames) for row in rows)


def metric_text(metric: dict[str, Any]) -> str:
    """格式化 numerator/denominator/percentage."""
    percentage = metric["percentage"]
    rendered = "N/A" if percentage is None else f"{float(percentage):.2f}%"
    numerator = metric["numerator"]
    if isinstance(numerator, float):
        return f"{numerator:.6f}/{metric['denominator']} ({rendered})"
    return f"{numerator}/{metric['denominator']} ({rendered})"


def render_report(summary: dict[str, Any], misses: tuple[E3MissRecord, ...], manifest: dict[str, Any]) -> str:
    """生成 E3-B Markdown 实验报告."""
    metrics = summary["metrics"]
    repo_rows = []
    for repo, value in summary["by_repository"].items():
        item = value["metrics"]
        repo_rows.append(
            f"| {repo} | {value['pairs']} | {value['breaking_cases']} | "
            f"{metric_text(item['contract_verdict_accuracy'])} | "
            f"{metric_text(item['contract_impact_recall'])} | "
            f"{metric_text(item['contract_impact_precision'])} | "
            f"{metric_text(item['executable_regression_scope_recall'])} | "
            f"{metric_text(item['executable_regression_scope_precision'])} | "
            f"{metric_text(item['fallback_rate'])} |"
        )
    type_rows = []
    for name, value in summary["by_change_type"].items():
        item = value["metrics"]
        type_rows.append(
            f"| {name} | {value['cases']} | {value['breaking_cases']} | "
            f"{metric_text(item['contract_verdict_accuracy'])} | "
            f"{metric_text(item['executable_regression_scope_recall'])} | "
            f"{metric_text(item['executable_regression_scope_precision'])} | "
            f"{metric_text(item['fallback_rate'])} |"
        )
    classification_rows = [
        f"| `{name}` | {count} |" for name, count in summary["miss_analysis"]["classification_counts"].items()
    ]
    typical_rows = [
        f"| {item.case_id} | `{item.classification.value}` | {item.likely_cause} |" for item in misses[:15]
    ]
    removed_fallback = sorted(
        {
            item.case_id
            for item in misses
            if item.classification == E3MissClassification.REMOVED_OPERATION_NON_EXECUTABLE and item.fallback_to_all
        }
    )
    component_overselection = sorted(
        {item.case_id for item in misses if item.classification == E3MissClassification.COMPONENT_MAPPING_LIMITATION}
    )
    true_false_negatives = summary["miss_analysis"]["true_selector_false_negatives"]
    oasdiff_disagreements = summary["miss_analysis"]["oasdiff_disagreements"]
    mixed = summary["by_change_type"].get("mixed", {}).get("metrics", {})
    mixed_precision = metric_text(mixed["executable_regression_scope_precision"]) if mixed else "N/A"
    error_analysis = summary.get("system_error_analysis", {})
    error_rows = [
        f"| {item['case_id']} | {item['exit_code']} | {item['cause']} |" for item in error_analysis.get("cases", [])
    ]
    return "\n".join(
        [
            "# API Guardian E3-B Real-world Contract and Scope Validation",
            "",
            "## Material Passport",
            "",
            "- Origin Skill: experiment-agent",
            "- Origin Mode: run + validate",
            f"- Origin Date: {manifest['ended_at']}",
            "- Verification Status: VERIFIED",
            "- Version Label: guardian_e3_b_system_evaluation_v1",
            f"- Ground Truth: human-confirmed v2 `{EXPECTED_V2_AGGREGATE}`",
            "",
            "## 1. Research Questions",
            "",
            "E3-B tests real-world contract verdict and scope mapping on immutable Git history. Runtime, Schemathesis and LLM are outside this stage.",
            "",
            "## 2. Repository Selection",
            "",
            f"Four public repositories contributed {summary['dataset']['total_pairs']} pairs; "
            f"{summary['dataset']['headline_pairs']} confirmed/probable pairs enter headline metrics.",
            "",
            "## 3. Dataset Construction",
            "",
            "Ground Truth was reviewed and frozen before System Output. ContractAffectedScope includes removed Base operations; RuntimeExecutableScope contains only affected operations still present in Head. Change-category rows are non-exclusive.",
            "",
            "## 4. Contract Detection",
            "",
            f"- ContractVerdictAccuracy: {metric_text(metrics['contract_verdict_accuracy'])}",
            f"- BreakingChangeRecall: {metric_text(metrics['breaking_change_recall'])}",
            f"- BreakingChangePrecision: {metric_text(metrics['breaking_change_precision'])}",
            f"- SystemExecutionSuccessRate: {metric_text(metrics['system_execution_success_rate'])}",
            f"- SystemErrorRate: {metric_text(metrics['system_error_rate'])}",
            "",
            "## 5. Scope Mapping",
            "",
            f"- ContractImpactRecall: {metric_text(metrics['contract_impact_recall'])}",
            f"- ContractImpactPrecision: {metric_text(metrics['contract_impact_precision'])}",
            f"- ExecutableRegressionScopeRecall: {metric_text(metrics['executable_regression_scope_recall'])}",
            f"- ExecutableRegressionScopePrecision: {metric_text(metrics['executable_regression_scope_precision'])}",
            f"- FallbackRate: {metric_text(metrics['fallback_rate'])}",
            f"- FallbackPrecision: {metric_text(metrics['fallback_precision'])}",
            f"- MeanExecutableScopeReduction: {metric_text(metrics['mean_executable_scope_reduction'])}",
            f"- MeanExecutableScopeReduction (breaking cases only): {metric_text(metrics['mean_executable_scope_reduction_breaking_cases'])}",
            f"- RemovedOperationCoverage: {metric_text(metrics['removed_operation_coverage'])}",
            f"- ComponentChangeRecall: {metric_text(metrics['component_change_recall'])}",
            f"- ComponentChangePrecision: {metric_text(metrics['component_change_precision'])}",
            "",
            "The end-to-end scope figures above retain all headline Cases, including tool ERROR. Because a pre-scope ERROR selects zero operations, it must not be interpreted as successful scope reduction. The following supplemental metrics isolate runs that reached scope selection:",
            "",
            f"- Successful-pipeline ContractImpactRecall: {metric_text(metrics['contract_impact_recall_successful_pipeline'])}",
            f"- Successful-pipeline ContractImpactPrecision: {metric_text(metrics['contract_impact_precision_successful_pipeline'])}",
            f"- Successful-pipeline ExecutableScopeRecall: {metric_text(metrics['executable_regression_scope_recall_successful_pipeline'])}",
            f"- Successful-pipeline ExecutableScopePrecision: {metric_text(metrics['executable_regression_scope_precision_successful_pipeline'])}",
            f"- Successful-pipeline FallbackRate: {metric_text(metrics['fallback_rate_successful_pipeline'])}",
            f"- Successful-pipeline MeanExecutableScopeReduction: {metric_text(metrics['mean_executable_scope_reduction_successful_pipeline'])}",
            f"- Successful-pipeline ComponentChangeRecall: {metric_text(metrics['component_change_recall_successful_pipeline'])}",
            f"- Successful-pipeline ComponentChangePrecision: {metric_text(metrics['component_change_precision_successful_pipeline'])}",
            "",
            "### By Repository",
            "",
            "| Repo | Pairs | Breaking | Accuracy | Impact recall | Impact precision | Executable recall | Executable precision | Fallback rate |",
            "|---|---:|---:|---|---|---|---|---|---|",
            *repo_rows,
            "",
            "### By Change Category",
            "",
            "| Category | Cases | Breaking | Accuracy | Executable recall | Executable precision | Fallback rate |",
            "|---|---:|---:|---|---|---|---|",
            *type_rows,
            "",
            "## 6. Real-world Misses",
            "",
            "| Classification | Records |",
            "|---|---:|",
            *classification_rows,
            "",
            "| Case | Classification | Evidence-based likely cause |",
            "|---|---|---|",
            *typical_rows,
            "",
            "Complete Ground Truth, safe oasdiff Adapter output and Guardian scope evidence are stored in `real_world_misses.jsonl`.",
            "",
            "### Tool ERROR diagnostics",
            "",
            "The primary run is unchanged. A separate non-headline diagnostic replay was used only to recover the exit code and sanitized load error that the production Adapter intentionally does not expose as raw stderr.",
            "",
            "| Case | Exit code | Sanitized cause |",
            "|---|---:|---|",
            *error_rows,
            "",
            "## 7. Synthetic vs Real-world Comparison",
            "",
            f"- Removed-operation fallback-to-all reappeared in {len(removed_fallback)} case(s): {removed_fallback or 'none'}.",
            f"- Shared-component fallback/over-selection appeared in {len(component_overselection)} case(s): {component_overselection or 'none'}.",
            f"- True executable selector false negatives: {len(true_false_negatives)} case(s): {true_false_negatives or 'none'}.",
            f"- oasdiff/Ground Truth disagreements: {len(oasdiff_disagreements)} case(s): {oasdiff_disagreements or 'none'}.",
            f"- Mixed-change executable precision: {mixed_precision}. This is descriptive and is not pooled with E2/E2.5.",
            "",
            "## 8. Optional Runtime Validation",
            "",
            "`runtime_validation_status = not_run_e3_b_contract_scope_only`. No Supabase pair is yet confirmed suitable. `rw_supabase_005_authorize_flow_type_removed` remains plausible pending service startup and exact version/Spec alignment; candidates 001 and 003 are currently blocked by the E3-B oasdiff Spec-load error.",
            "",
            "## 9. Threats to Validity",
            "",
            "- Four repositories and 19 headline pairs do not represent all OpenAPI ecosystems.",
            "- Ground Truth is AI-assisted with a human-confirmed amendment, not a multi-reviewer consensus dataset.",
            "- Adjacent generated Specs can contain mixed changes and generator churn.",
            "- Guardian rejects external refs and artifacts above 5 MiB, which creates selection bias.",
            "- This stage validates static contract/scope behavior only; it gives no real-world runtime-regression result.",
            "",
            "## 10. Conclusions",
            "",
            "Supported claims are limited to the measured contract verdict, scope mapping, fallback behavior and tool/Ground Truth disagreements. The data do not establish universal coverage, production readiness, runtime bug recall, wall-clock superiority or LLM utility.",
            "",
            "## 11. Reproducibility",
            "",
            f"- oasdiff: `{manifest['oasdiff']['version']}`",
            f"- Guardian commit: `{manifest['guardian_git_commit']}`",
            f"- v1 aggregate: `{EXPECTED_V1_AGGREGATE}`",
            f"- v2 aggregate: `{EXPECTED_V2_AGGREGATE}`",
            "- Specs were materialized from exact commits through raw.githubusercontent.com; no production API target was called.",
            "- Schemathesis and LLM were not executed.",
            "",
        ]
    )


def git_output(*args: str) -> str:
    """使用 shell=False 读取 Git metadata."""
    executable = shutil.which("git")
    if executable is None:
        return "unavailable"
    result = subprocess.run(
        [executable, *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def verify_oasdiff(executable: Path) -> dict[str, str]:
    """验证官方 oasdiff 1.28.0 binary 与发布包哈希."""
    result = subprocess.run(
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    version = result.stdout.strip()
    if version != "oasdiff version 1.28.0":
        raise RuntimeError(f"oasdiff version mismatch: {version}")
    archive = executable.parent / "oasdiff_1.28.0_windows_amd64.tar.gz"
    verify_file(archive, OASDIFF_ARCHIVE_SHA256)
    return {
        "version": version,
        "executable_sha256": sha256_file(executable),
        "archive_sha256": OASDIFF_ARCHIVE_SHA256,
        "release_asset": "oasdiff_1.28.0_windows_amd64.tar.gz",
        "release_url": (
            "https://github.com/oasdiff/oasdiff/releases/download/v1.28.0/oasdiff_1.28.0_windows_amd64.tar.gz"
        ),
    }


def integrity_snapshot(v2_manifest: dict[str, Any]) -> dict[str, Any]:
    """记录 Core、freeze 与 prior artifact hashes."""
    return {
        "production_core": {path: sha256_file(ROOT / path) for path in sorted(v2_manifest["production_core_hashes"])},
        "prior_e2_e2_5": {
            path: sha256_file(ROOT / path) for path in sorted(v2_manifest["frozen_prior_artifact_hashes"])
        },
        "v1_manifest_file_sha256": sha256_file(V1_MANIFEST_PATH),
        "v1_aggregate_review_sha256": EXPECTED_V1_AGGREGATE,
        "v2_manifest_file_sha256": sha256_file(V2_MANIFEST_PATH),
        "v2_aggregate_review_sha256": EXPECTED_V2_AGGREGATE,
    }


async def run_e3_system_evaluation(oasdiff_path: Path) -> dict[str, Any]:
    """在 final v2 冻结后执行一次 E3-B System Evaluation."""
    console = Console()
    started_at = datetime.now(UTC)
    v2_manifest = verify_freezes_and_preflight(require_system_outputs_absent=True)
    preflight = integrity_snapshot(v2_manifest)
    tool = verify_oasdiff(oasdiff_path.resolve())
    records = load_ground_truth()
    run_id = f"guardian-e3-b-{started_at.strftime('%Y%m%dT%H%M%SZ')}"
    adapter = OasdiffAdapter(
        executable=str(oasdiff_path.resolve()),
        timeout_seconds=SUBPROCESS_TIMEOUT_SECONDS,
        max_artifact_bytes=MAX_ARTIFACT_BYTES,
    )
    results: list[E3SystemResult] = []
    RAW_RESULTS_PATH.write_bytes(b"")
    for index, record in enumerate(records, start=1):
        result = await run_case(record, adapter, run_id)
        results.append(result)
        with RAW_RESULTS_PATH.open("ab") as handle:
            handle.write(
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode() + b"\n"
            )
        console.print(
            f"[cyan][{index:02d}/{len(records):02d}][/cyan] {record.case_id}: "
            f"verdict={result.guardian_contract_verdict}, selected={len(result.selected_operations)}, "
            f"fallback={str(result.fallback_to_all).lower()}"
        )

    ordered_results = tuple(results)
    results_by_case = {result.case_id: result for result in ordered_results}
    if len(results_by_case) != len(records):
        raise RuntimeError("System Output case IDs are not unique and complete")
    misses = build_misses(records, results_by_case)
    summary = summarize(records, ordered_results, misses)
    write_jsonl(MISSES_PATH, [item.model_dump(mode="json") for item in misses])
    write_json(SUMMARY_PATH, summary)
    write_summary_csv(summary)

    ended_at = datetime.now(UTC)
    postflight = integrity_snapshot(v2_manifest)
    if preflight != postflight:
        raise RuntimeError("Frozen Core, Ground Truth or prior Benchmark artifact changed during E3-B")
    manifest = {
        "stage": "E3-B Real-world Contract + Scope System Evaluation",
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": (ended_at - started_at).total_seconds(),
        "command": (
            "uv run python -m benchmarks.guardian.run_e3 --oasdiff-path "
            "benchmarks/guardian/.tools/oasdiff/1.28.0/oasdiff.exe"
        ),
        "guardian_git_commit": git_output("rev-parse", "HEAD"),
        "guardian_worktree_dirty": bool(git_output("status", "--porcelain")),
        "python_version": platform.python_version(),
        "operating_system": platform.platform(),
        "cpu_logical_count": os.cpu_count(),
        "oasdiff": tool,
        "configuration": {
            "max_openapi_bytes": MAX_OPENAPI_BYTES,
            "max_artifact_bytes": MAX_ARTIFACT_BYTES,
            "max_scan_artifact_bytes": MAX_SCAN_ARTIFACT_BYTES,
            "subprocess_timeout_seconds": SUBPROCESS_TIMEOUT_SECONDS,
            "oasdiff_command": (
                "oasdiff breaking --format json --allow-external-refs=false --fail-on ERR -- BASE HEAD"
            ),
            "scope_policy": "frozen recall-first exact method/path or unique operationId; otherwise fallback-to-all",
        },
        "ground_truth": {
            "version": "v2",
            "status": "human_confirmed",
            "aggregate_review_sha256": EXPECTED_V2_AGGREGATE,
            "manifest_file_sha256": sha256_file(V2_MANIFEST_PATH),
            "frozen_at": v2_manifest["frozen_at"],
            "system_output_absent_at_freeze": True,
        },
        "integrity": {
            "preflight": preflight,
            "postflight": postflight,
            "production_core_unchanged": True,
            "e2_e2_5_raw_unchanged": True,
            "v1_preserved": True,
            "v2_preserved": True,
        },
        "network": {
            "source_materialization_host": "raw.githubusercontent.com",
            "source_materialization_purpose": "immutable public Git source artifacts only",
            "public_production_api_called": False,
        },
        "not_run": ["Schemathesis", "DeepSeek/LLM", "E3-C runtime", "product fixes", "optimization"],
        "output_files": [path.relative_to(ROOT).as_posix() for path in SYSTEM_OUTPUT_PATHS],
        "raw_result_count": len(ordered_results),
        "miss_record_count": len(misses),
    }
    write_json(RUN_MANIFEST_PATH, manifest)
    REPORT_PATH.write_text(render_report(summary, misses, manifest), encoding="utf-8")
    verify_freezes_and_preflight(require_system_outputs_absent=False)
    console.print(f"[green]E3-B complete: {len(records)} pairs, {len(misses)} miss records[/green]")
    return summary
