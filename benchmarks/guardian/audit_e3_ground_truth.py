"""审计 E3-B pre-system Ground Truth，并生成不覆盖 v1 的 proposed v2.

本脚本只读取冻结 review record、固定 revision 的 Base/Head OpenAPI 与原始
freeze manifest。它不会导入或执行 Guardian production Core，也不会读取任何
System Output。
"""

import hashlib
import json
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from benchmarks.guardian.real_world_models import (
    PreSystemReviewRecord,
    ReviewConfidence,
    ReviewStatus,
)

ROOT = Path(__file__).resolve().parents[2]
REVIEW_ROOT = ROOT / "benchmarks" / "guardian" / "datasets" / "real_world" / "reviews" / "pre_system"
PROPOSED_REVIEW_ROOT = (
    ROOT / "benchmarks" / "guardian" / "datasets" / "real_world" / "reviews" / "pre_system_v2_proposed"
)
REPORT_ROOT = ROOT / "benchmarks" / "guardian" / "reports" / "e3"
V1_MANIFEST_PATH = REPORT_ROOT / "ground_truth_freeze_manifest.json"
V1_ALIAS_PATH = REPORT_ROOT / "ground_truth_freeze_v1.json"
V2_MANIFEST_PATH = REPORT_ROOT / "ground_truth_freeze_v2_proposed.json"
V2_REVIEW_JSONL_PATH = REPORT_ROOT / "review_records_v2_proposed.jsonl"
AMENDMENTS_PATH = REPORT_ROOT / "ground_truth_amendments.jsonl"
AUDIT_JSONL_PATH = REPORT_ROOT / "ground_truth_consistency_audit.jsonl"
AUDIT_MARKDOWN_PATH = REPORT_ROOT / "ground_truth_consistency_audit.md"

EXPECTED_V1_AGGREGATE_SHA256 = "3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36"
EXPECTED_V1_MANIFEST_FILE_SHA256 = "65576075bc38149e5105106fc9401e1ad8f88b0a0f474144fad7185c3431194d"
TARGET_CASE_ID = "rw_netbird_004_agent_network_settings_rework"
TARGET_OPERATION = "POST /api/agent-network/settings"
MAX_SPEC_BYTES = 5 * 1024 * 1024
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
SYSTEM_OUTPUT_PATHS = (
    REPORT_ROOT / "raw_guardian_results.jsonl",
    REPORT_ROOT / "real_world_misses.jsonl",
    REPORT_ROOT / "summary.json",
    REPORT_ROOT / "summary.csv",
    REPORT_ROOT / "e3_contract_scope_report.md",
)


def sha256_bytes(data: bytes) -> str:
    """计算原始字节的 SHA256."""
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    """生成稳定、可审计的 UTF-8 JSON."""
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def jsonl_bytes(values: list[dict[str, Any]]) -> bytes:
    """生成稳定排序字段的 JSONL."""
    return b"".join(json.dumps(value, ensure_ascii=False, sort_keys=True).encode() + b"\n" for value in values)


def write_bytes(path: Path, data: bytes) -> None:
    """写入独立 Benchmark artifact，不触碰 v1 原文件."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def fetch_spec(record: PreSystemReviewRecord, side: str) -> tuple[bytes, dict[str, Any]]:
    """按固定 40-char revision 读取 Base 或 Head OpenAPI."""
    commit = record.base_commit if side == "base" else record.head_commit
    spec_path = record.base_spec_path if side == "base" else record.head_spec_path
    expected_hash = record.base_spec_sha256 if side == "base" else record.head_spec_sha256
    encoded_path = urllib.parse.quote(spec_path, safe="/")
    url = f"https://raw.githubusercontent.com/{record.repo}/{commit}/{encoded_path}"
    request = urllib.request.Request(url, headers={"User-Agent": "api-guardian-e3-ground-truth-audit"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        data = response.read(MAX_SPEC_BYTES + 1)
    if len(data) > MAX_SPEC_BYTES:
        raise ValueError(f"spec exceeds frozen 5 MiB limit: {record.case_id}:{side}")
    if sha256_bytes(data) != expected_hash:
        raise ValueError(f"materialized spec hash mismatch: {record.case_id}:{side}")

    parsed = json.loads(data) if spec_path.endswith(".json") else yaml.safe_load(data)
    if not isinstance(parsed, dict):
        raise ValueError(f"OpenAPI document must be an object: {record.case_id}:{side}")
    return data, parsed


def operation_set(spec: dict[str, Any]) -> set[str]:
    """从 OpenAPI paths 提取规范化 METHOD + path."""
    operations: set[str] = set()
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return operations
    for api_path, path_item in paths.items():
        if not isinstance(api_path, str) or not isinstance(path_item, dict):
            continue
        for method in path_item:
            if isinstance(method, str) and method.lower() in HTTP_METHODS:
                operations.add(f"{method.upper()} {api_path}")
    return operations


def schema_refs(value: Any) -> set[str]:
    """收集一个 operation 直接或嵌套引用的 component schema 名称."""
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            refs.add(ref.rsplit("/", maxsplit=1)[-1])
        for child in value.values():
            refs.update(schema_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(schema_refs(child))
    return refs


def operation_value(spec: dict[str, Any], operation: str) -> dict[str, Any] | None:
    """读取指定 METHOD + path 的 OpenAPI operation object."""
    method, api_path = operation.split(" ", maxsplit=1)
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return None
    path_item = paths.get(api_path)
    if not isinstance(path_item, dict):
        return None
    value = path_item.get(method.lower())
    return value if isinstance(value, dict) else None


def load_v1_records() -> list[PreSystemReviewRecord]:
    """只从原始 pre-system 目录加载 v1 records."""
    review_jsonl = REPORT_ROOT / "review_records.jsonl"
    records = [
        PreSystemReviewRecord.model_validate(json.loads(line))
        for line in review_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(records) != 20:
        raise ValueError(f"expected 20 frozen review records, found {len(records)}")
    for record in records:
        individual_path = REVIEW_ROOT / f"{record.case_id}.json"
        individual = PreSystemReviewRecord.model_validate(json.loads(individual_path.read_text(encoding="utf-8")))
        if individual != record:
            raise ValueError(f"v1 individual record differs from frozen JSONL: {record.case_id}")
    return records


def verify_preconditions() -> dict[str, Any]:
    """验证 v1 与“尚无 System Output”的边界仍然成立."""
    v1_bytes = V1_MANIFEST_PATH.read_bytes()
    if sha256_bytes(v1_bytes) != EXPECTED_V1_MANIFEST_FILE_SHA256:
        raise RuntimeError("original v1 freeze manifest file hash changed")
    manifest = json.loads(v1_bytes)
    if manifest.get("aggregate_review_sha256") != EXPECTED_V1_AGGREGATE_SHA256:
        raise RuntimeError("original v1 aggregate review hash changed")
    for artifact in manifest.get("review_artifacts", []):
        artifact_path = ROOT / artifact["path"]
        artifact_data = artifact_path.read_bytes()
        if len(artifact_data) != artifact["bytes"] or sha256_bytes(artifact_data) != artifact["sha256"]:
            raise RuntimeError(f"original v1 review artifact changed: {artifact['path']}")
    existing_outputs = [path.relative_to(ROOT).as_posix() for path in SYSTEM_OUTPUT_PATHS if path.exists()]
    if existing_outputs:
        raise RuntimeError(f"system output exists; pre-system audit must stop: {existing_outputs}")
    return manifest


def audit_records(
    records: list[PreSystemReviewRecord],
) -> tuple[list[dict[str, Any]], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    """审计所有冻结 scope，并返回每个 Case 的 Base/Head Spec 缓存."""
    audit_rows: list[dict[str, Any]] = []
    specs_by_case: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for record in records:
        _, base_spec = fetch_spec(record, "base")
        _, head_spec = fetch_spec(record, "head")
        specs_by_case[record.case_id] = (base_spec, head_spec)
        base_operations = operation_set(base_spec)
        head_operations = operation_set(head_spec)
        operation_rows: list[dict[str, Any]] = []
        candidates: list[str] = []
        for operation in record.contract_affected_operations:
            exists_in_base = operation in base_operations
            exists_in_head = operation in head_operations
            newly_added = not exists_in_base and exists_in_head
            if newly_added:
                candidates.append(operation)
            operation_rows.append(
                {
                    "operation": operation,
                    "exists_in_base": exists_in_base,
                    "exists_in_head": exists_in_head,
                    "presence_classification": (
                        "NEW_OPERATION_SCOPE_OVERINCLUSION"
                        if newly_added
                        else "EXISTING_OPERATION"
                        if exists_in_base and exists_in_head
                        else "REMOVED_OPERATION"
                        if exists_in_base and not exists_in_head
                        else "UNKNOWN_OPERATION"
                    ),
                }
            )
        audit_rows.append(
            {
                "case_id": record.case_id,
                "repo": record.repo,
                "base_commit": record.base_commit,
                "head_commit": record.head_commit,
                "contract_scope_count": len(record.contract_affected_operations),
                "scope_operations": operation_rows,
                "new_operation_scope_overinclusions": candidates,
                "classification": (
                    "NEW_OPERATION_SCOPE_OVERINCLUSION" if candidates else "NO_MATCHING_CONSISTENCY_ISSUE"
                ),
                "evidence_boundary": "Base OpenAPI, Head OpenAPI, existing pre-system review only",
                "system_output_used": False,
            }
        )
    return audit_rows, specs_by_case


def validate_target_evidence(
    records: list[PreSystemReviewRecord],
    specs_by_case: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """重新推导 NetBird 004 的新 operation 与四个既有受影响 operation."""
    record = next(item for item in records if item.case_id == TARGET_CASE_ID)
    base_spec, head_spec = specs_by_case[TARGET_CASE_ID]
    base_operations = operation_set(base_spec)
    head_operations = operation_set(head_spec)
    if TARGET_OPERATION in base_operations or TARGET_OPERATION not in head_operations:
        raise ValueError("NetBird 004 target operation is not a pure Head addition")

    changed_schemas = {item.schema_name for item in record.breaking_changes if item.schema_name is not None}
    target_value = operation_value(head_spec, TARGET_OPERATION)
    if target_value is None:
        raise ValueError("NetBird 004 target operation is missing from Head")
    target_refs = schema_refs(target_value)
    shared_changed_refs = sorted(target_refs & changed_schemas)
    if not shared_changed_refs:
        raise ValueError("NetBird 004 new operation is not linked to the changed component evidence")

    corrected_contract = [
        operation for operation in record.contract_affected_operations if operation != TARGET_OPERATION
    ]
    corrected_executable = [
        operation for operation in record.runtime_executable_operations if operation != TARGET_OPERATION
    ]
    for operation in corrected_contract:
        if operation not in base_operations or operation not in head_operations:
            raise ValueError(f"corrected NetBird 004 operation is not present in both versions: {operation}")
    return {
        "new_operation": TARGET_OPERATION,
        "new_operation_head_schema_refs": sorted(target_refs),
        "new_operation_changed_schema_refs": shared_changed_refs,
        "corrected_contract_scope": corrected_contract,
        "corrected_runtime_scope": corrected_executable,
    }


def build_v2_records(
    records: list[PreSystemReviewRecord],
    target_evidence: dict[str, Any],
) -> tuple[list[PreSystemReviewRecord], list[dict[str, Any]]]:
    """只修正被证实的 scope over-inclusion，并记录所有字段变化."""
    timestamp = datetime.now(UTC).isoformat()
    reviewer = "independent_review_pending_user_confirmation"
    corrected_evidence = [
        "The component diff directly removes/renames request and response members.",
        (
            "Independent transitive fragment-reference review maps the breaking components to four operations "
            "that exist in both Base and Head."
        ),
        (
            "POST and DELETE /api/agent-network/settings are Head additions. Under the E3 existing-consumer "
            "scope convention, neither new operation is counted as a broken existing operation; POST references "
            "AgentNetworkSettings but remains an additive operation."
        ),
    ]
    proposed: list[PreSystemReviewRecord] = []
    amendments: list[dict[str, Any]] = []
    common_reason = (
        "The operation is absent from Base and newly added in Head. A pure new operation is not an operation "
        "broken for existing consumers merely because it references a changed component."
    )
    pre_system_evidence = [
        f"Base does not contain {TARGET_OPERATION}.",
        f"Head contains {TARGET_OPERATION}.",
        (
            f"Head {TARGET_OPERATION} references changed component(s): "
            + ", ".join(target_evidence["new_operation_changed_schema_refs"])
            + "."
        ),
        "The remaining four scoped operations exist in both Base and Head and reference the changed components.",
    ]

    for record in records:
        if record.case_id != TARGET_CASE_ID:
            proposed.append(record)
            continue
        update = record.model_dump(mode="json")
        field_changes: dict[str, tuple[Any, Any, str]] = {
            "contract_affected_operations": (
                update["contract_affected_operations"],
                target_evidence["corrected_contract_scope"],
                common_reason,
            ),
            "runtime_executable_operations": (
                update["runtime_executable_operations"],
                target_evidence["corrected_runtime_scope"],
                (
                    common_reason
                    + " RuntimeExecutableScope is the affected subset of existing operations that remains in Head."
                ),
            ),
            "evidence": (
                update["evidence"],
                corrected_evidence,
                "The evidence text is aligned with the corrected existing-consumer scope convention.",
            ),
        }
        for field, (old_value, new_value, reason) in field_changes.items():
            amendments.append(
                {
                    "case_id": TARGET_CASE_ID,
                    "field": field,
                    "old_value": old_value,
                    "new_value": new_value,
                    "reason": reason,
                    "pre_system_evidence": pre_system_evidence,
                    "discovered_during": "pre_system_human_spotcheck",
                    "reviewer": reviewer,
                    "timestamp": timestamp,
                }
            )
            update[field] = new_value
        proposed.append(PreSystemReviewRecord.model_validate(update))
    return proposed, amendments


def scope_counts(records: list[PreSystemReviewRecord]) -> dict[str, int]:
    """只汇总 Ground Truth scope 数量，不计算任何 system accuracy metric."""
    headline = [
        record
        for record in records
        if record.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.PROBABLE}
        and record.review_confidence != ReviewConfidence.LOW
    ]
    return {
        "all_cases_contract_operations": sum(len(record.contract_affected_operations) for record in records),
        "all_cases_runtime_executable_operations": sum(
            len(record.runtime_executable_operations) for record in records
        ),
        "headline_contract_operations": sum(len(record.contract_affected_operations) for record in headline),
        "headline_runtime_executable_operations": sum(
            len(record.runtime_executable_operations) for record in headline
        ),
    }


def build_v2_manifest(
    v1_manifest: dict[str, Any],
    v2_records: list[PreSystemReviewRecord],
    amendments: list[dict[str, Any]],
    v1_scope_counts: dict[str, int],
    v2_scope_counts: dict[str, int],
) -> dict[str, Any]:
    """使用与 v1 相同的 canonical path 算法计算可比较的 proposed v2 hash."""
    artifact_rows: list[dict[str, Any]] = []
    for record in v2_records:
        data = json_bytes(record.model_dump(mode="json"))
        storage_path = PROPOSED_REVIEW_ROOT / f"{record.case_id}.json"
        write_bytes(storage_path, data)
        canonical_path = f"benchmarks/guardian/datasets/real_world/reviews/pre_system/{record.case_id}.json"
        artifact_rows.append(
            {
                "canonical_path": canonical_path,
                "storage_path": storage_path.relative_to(ROOT).as_posix(),
                "sha256": sha256_bytes(data),
                "bytes": len(data),
            }
        )

    review_rows = [record.model_dump(mode="json") for record in v2_records]
    review_data = jsonl_bytes(review_rows)
    write_bytes(V2_REVIEW_JSONL_PATH, review_data)
    artifact_rows.append(
        {
            "canonical_path": "benchmarks/guardian/reports/e3/review_records.jsonl",
            "storage_path": V2_REVIEW_JSONL_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256_bytes(review_data),
            "bytes": len(review_data),
        }
    )

    cases_path = REPORT_ROOT / "real_world_cases.jsonl"
    cases_data = cases_path.read_bytes()
    artifact_rows.append(
        {
            "canonical_path": "benchmarks/guardian/reports/e3/real_world_cases.jsonl",
            "storage_path": cases_path.relative_to(ROOT).as_posix(),
            "sha256": sha256_bytes(cases_data),
            "bytes": len(cases_data),
        }
    )
    sorted_artifacts = sorted(artifact_rows, key=lambda item: item["canonical_path"])
    aggregate = sha256_bytes(
        "\n".join(f"{item['canonical_path']}:{item['sha256']}" for item in sorted_artifacts).encode()
    )
    return {
        "schema_version": "guardian-e3-b-ground-truth-freeze-v2-proposed",
        "stage": "E3-B pre-system Ground Truth consistency amendment",
        "status": "proposed_pending_user_confirmation",
        "created_at": amendments[0]["timestamp"],
        "supersedes": {
            "version": "ground_truth_freeze_v1",
            "aggregate_review_sha256": EXPECTED_V1_AGGREGATE_SHA256,
            "manifest_file_sha256": EXPECTED_V1_MANIFEST_FILE_SHA256,
            "manifest_path": V1_MANIFEST_PATH.relative_to(ROOT).as_posix(),
        },
        "reviewer_basis": "independent_review_pending_user_confirmation",
        "discovered_during": "pre_system_human_spotcheck",
        "ground_truth_sources": "Base OpenAPI, Head OpenAPI, existing pre-system review evidence only",
        "system_output_used": "none",
        "guardian_execution_permitted": False,
        "review_artifacts": sorted_artifacts,
        "aggregate_review_sha256": aggregate,
        "review_counts": v1_manifest["review_counts"],
        "scope_counts": {"v1": v1_scope_counts, "proposed_v2": v2_scope_counts},
        "amendment_count": len(amendments),
        "amended_case_count": len({item["case_id"] for item in amendments}),
        "amendment_log_path": AMENDMENTS_PATH.relative_to(ROOT).as_posix(),
        "production_core_hashes": v1_manifest["production_core_hashes"],
        "frozen_prior_artifact_hashes": v1_manifest["frozen_prior_artifact_hashes"],
        "guardian_system_output_status": "not_generated_during_ground_truth_consistency_audit",
    }


def audit_markdown(
    audit_rows: list[dict[str, Any]],
    target_evidence: dict[str, Any],
    v1_manifest: dict[str, Any],
    v2_manifest: dict[str, Any],
    amendments: list[dict[str, Any]],
) -> str:
    """生成只包含 pre-system evidence 的一致性审计报告."""
    table_rows = [
        (
            f"| {row['case_id']} | {row['contract_scope_count']} | "
            f"{len(row['new_operation_scope_overinclusions'])} | {row['classification']} |"
        )
        for row in audit_rows
    ]
    contract_scope = target_evidence["corrected_contract_scope"]
    executable_scope = target_evidence["corrected_runtime_scope"]
    amendment_rows = [
        f"| {item['case_id']} | `{item['field']}` | `{json.dumps(item['old_value'], ensure_ascii=False)}` | "
        f"`{json.dumps(item['new_value'], ensure_ascii=False)}` |"
        for item in amendments
    ]
    return "\n".join(
        [
            "# E3-B Ground Truth Consistency Audit",
            "",
            "> 状态：proposed v2，等待用户确认。未运行 oasdiff、Guardian、Schemathesis、LLM 或任何 accuracy metric。",
            "",
            "## Evidence Boundary",
            "",
            "本次审计只使用固定 commit 的 Base OpenAPI、Head OpenAPI 和既有 pre-system review evidence。",
            "原始 v1 freeze 与 v1 review records 均未覆盖或删除。",
            "",
            "## Twenty-case Narrow Audit",
            "",
            "| Case | Frozen contract ops | New-operation candidates | Result |",
            "|---|---:|---:|---|",
            *table_rows,
            "",
            "20 个 Case 中只有 `rw_netbird_004_agent_network_settings_rework` 命中",
            "`NEW_OPERATION_SCOPE_OVERINCLUSION`。",
            "",
            "## NetBird 004 Finding",
            "",
            f"- `{TARGET_OPERATION}`：Base 不存在，Head 新增。",
            "- Head 中该 operation 引用："
            + ", ".join(f"`{item}`" for item in target_evidence["new_operation_head_schema_refs"])
            + "。",
            "- 与冻结 breaking evidence 重合的 component："
            + ", ".join(f"`{item}`" for item in target_evidence["new_operation_changed_schema_refs"])
            + "。",
            "- 因为它是纯新增 operation，不能仅因引用 changed component 就算作既有消费者被破坏的 operation。",
            "- Case 仍为 Contract FAIL：其余四个 operation 在 Base/Head 均存在，且仍由已冻结的 request/response component 变化支持。",
            "",
            "### Correct ContractAffectedScope",
            "",
            *[f"- `{operation}`" for operation in contract_scope],
            "",
            "### Correct RuntimeExecutableScope",
            "",
            *[f"- `{operation}`" for operation in executable_scope],
            "",
            "## Exact Proposed Amendments",
            "",
            "| Case | Field | Old | New |",
            "|---|---|---|---|",
            *amendment_rows,
            "",
            "## Freeze Lineage",
            "",
            f"- v1 aggregate SHA256: `{v1_manifest['aggregate_review_sha256']}`",
            f"- proposed v2 aggregate SHA256: `{v2_manifest['aggregate_review_sha256']}`",
            "- Verdict labels changed: 0",
            "- Headline eligible case count changed: 0",
            "- proposed v2 reviewer state: `independent_review_pending_user_confirmation`",
            "",
        ]
    )


def main() -> None:
    """执行 pre-system 一致性审计并生成 proposed v2 artifacts."""
    console = Console()
    v1_manifest = verify_preconditions()
    records = load_v1_records()
    audit_rows, specs_by_case = audit_records(records)
    target_evidence = validate_target_evidence(records, specs_by_case)

    candidates = [
        (row["case_id"], operation) for row in audit_rows for operation in row["new_operation_scope_overinclusions"]
    ]
    if candidates != [(TARGET_CASE_ID, TARGET_OPERATION)]:
        raise RuntimeError(f"unexpected over-inclusion candidates require independent review: {candidates}")

    v2_records, amendments = build_v2_records(records, target_evidence)
    v1_counts = scope_counts(records)
    v2_counts = scope_counts(v2_records)
    v2_manifest = build_v2_manifest(v1_manifest, v2_records, amendments, v1_counts, v2_counts)

    write_bytes(V1_ALIAS_PATH, V1_MANIFEST_PATH.read_bytes())
    write_bytes(AMENDMENTS_PATH, jsonl_bytes(amendments))
    write_bytes(AUDIT_JSONL_PATH, jsonl_bytes(audit_rows))
    write_bytes(V2_MANIFEST_PATH, json_bytes(v2_manifest))
    write_bytes(
        AUDIT_MARKDOWN_PATH,
        audit_markdown(audit_rows, target_evidence, v1_manifest, v2_manifest, amendments).encode(),
    )

    # 写出后再次验证 v1，确保原始 freeze 未被静默修改。
    verify_preconditions()
    status_counts = Counter(record.review_status.value for record in v2_records)
    console.print("[green]Audited 20 pre-system review records[/green]")
    console.print(f"[yellow]Proposed v2 aggregate: {v2_manifest['aggregate_review_sha256']}[/yellow]")
    console.print(
        "[cyan]No system evaluation was run. Awaiting user confirmation.[/cyan]",
        f"confirmed={status_counts['confirmed']}",
    )


if __name__ == "__main__":
    main()
