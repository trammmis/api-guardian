"""验证 E3-B Ground Truth v1 保留与 human-confirmed v2 修订边界."""

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "benchmarks" / "guardian" / "reports" / "e3"
V1_REVIEW_ROOT = ROOT / "benchmarks" / "guardian" / "datasets" / "real_world" / "reviews" / "pre_system"
V2_REVIEW_ROOT = ROOT / "benchmarks" / "guardian" / "datasets" / "real_world" / "reviews" / "pre_system_v2"
V1_AGGREGATE = "3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36"
V1_MANIFEST_FILE_HASH = "65576075bc38149e5105106fc9401e1ad8f88b0a0f474144fad7185c3431194d"
V2_AGGREGATE = "7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94"
TARGET_CASE = "rw_netbird_004_agent_network_settings_rework"
TARGET_OPERATION = "POST /api/agent-network/settings"
EXPECTED_SCOPE = [
    "POST /api/agent-network/providers",
    "PUT /api/agent-network/providers/{providerId}",
    "GET /api/agent-network/settings",
    "PUT /api/agent-network/settings",
]


def sha256_bytes(data: bytes) -> str:
    """计算原始字节 SHA256."""
    return hashlib.sha256(data).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL artifact."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_records(root: Path) -> dict[str, dict[str, Any]]:
    """按 case_id 读取独立 review records."""
    return {
        value["case_id"]: value
        for path in sorted(root.glob("*.json"))
        if (value := json.loads(path.read_text(encoding="utf-8")))
    }


def test_v1_is_preserved_and_system_outputs_follow_v2_freeze() -> None:
    """原始 freeze 保持原字节，System Output 时间晚于 final v2."""
    original = (REPORT_ROOT / "ground_truth_freeze_manifest.json").read_bytes()
    alias = (REPORT_ROOT / "ground_truth_freeze_v1.json").read_bytes()
    manifest = json.loads(original)
    assert sha256_bytes(original) == V1_MANIFEST_FILE_HASH
    assert original == alias
    assert manifest["aggregate_review_sha256"] == V1_AGGREGATE
    for artifact in manifest["review_artifacts"]:
        artifact_data = (ROOT / artifact["path"]).read_bytes()
        assert len(artifact_data) == artifact["bytes"]
        assert sha256_bytes(artifact_data) == artifact["sha256"]
    for name in (
        "raw_guardian_results.jsonl",
        "real_world_misses.jsonl",
        "summary.json",
        "summary.csv",
        "e3_contract_scope_report.md",
    ):
        assert (REPORT_ROOT / name).is_file()
    integrity = json.loads((REPORT_ROOT / "integrity_evidence.json").read_text(encoding="utf-8"))
    assert integrity["system_output_created_after_v2_freeze"] is True


def test_only_netbird_004_has_new_operation_scope_overinclusion() -> None:
    """20 Case 审计只能识别出一个符合定义的 over-inclusion."""
    rows = load_jsonl(REPORT_ROOT / "ground_truth_consistency_audit.jsonl")
    assert len(rows) == 20
    matching = [row for row in rows if row["new_operation_scope_overinclusions"]]
    assert [(row["case_id"], row["new_operation_scope_overinclusions"]) for row in matching] == [
        (TARGET_CASE, [TARGET_OPERATION])
    ]
    assert all(row["system_output_used"] is False for row in rows)


def test_final_v2_changes_only_the_audited_record_fields() -> None:
    """Final v2 只能改变 NetBird 004 的两个 scope 与一致性 evidence."""
    v1_records = load_records(V1_REVIEW_ROOT)
    v2_records = load_records(V2_REVIEW_ROOT)
    assert len(v1_records) == len(v2_records) == 20
    assert set(v1_records) == set(v2_records)

    for case_id in v1_records.keys() - {TARGET_CASE}:
        assert v1_records[case_id] == v2_records[case_id]

    old = v1_records[TARGET_CASE]
    new = v2_records[TARGET_CASE]
    changed_fields = {field for field in old if old[field] != new[field]}
    assert changed_fields == {
        "contract_affected_operations",
        "runtime_executable_operations",
        "evidence",
    }
    assert new["contract_affected_operations"] == EXPECTED_SCOPE
    assert new["runtime_executable_operations"] == EXPECTED_SCOPE
    assert new["ground_truth_contract_verdict"] == old["ground_truth_contract_verdict"] == "fail"
    assert new["review_status"] == old["review_status"] == "confirmed"
    assert new["review_confidence"] == old["review_confidence"] == "high"


def test_amendment_log_and_v2_manifests_are_complete() -> None:
    """Proposed 记录保留，正式 Amendment 和 v2 显式标记用户确认."""
    amendments = load_jsonl(REPORT_ROOT / "ground_truth_amendments.jsonl")
    assert len(amendments) == 3
    assert {item["field"] for item in amendments} == {
        "contract_affected_operations",
        "runtime_executable_operations",
        "evidence",
    }
    assert {item["case_id"] for item in amendments} == {TARGET_CASE}
    assert {item["discovered_during"] for item in amendments} == {"pre_system_human_spotcheck"}
    assert {item["reviewer"] for item in amendments} == {"human_confirmed_by_user"}
    assert {item["confirmation_state"] for item in amendments} == {"human_confirmed"}
    proposed_amendments = load_jsonl(REPORT_ROOT / "ground_truth_amendments_proposed.jsonl")
    assert {item["reviewer"] for item in proposed_amendments} == {"independent_review_pending_user_confirmation"}

    proposed_manifest = json.loads((REPORT_ROOT / "ground_truth_freeze_v2_proposed.json").read_text(encoding="utf-8"))
    assert proposed_manifest["status"] == "proposed_pending_user_confirmation"
    manifest = json.loads((REPORT_ROOT / "ground_truth_freeze_v2.json").read_text(encoding="utf-8"))
    aggregate = sha256_bytes(
        "\n".join(
            f"{item['canonical_path']}:{item['sha256']}"
            for item in sorted(manifest["review_artifacts"], key=lambda item: item["canonical_path"])
        ).encode()
    )
    assert manifest["status"] == "human_confirmed"
    assert manifest["reviewer_basis"] == ("AI-assisted initial review with an explicit user-confirmed amendment")
    assert manifest["system_output_used"] == "none"
    assert manifest["guardian_execution_permitted"] is True
    assert manifest["supersedes"]["aggregate_review_sha256"] == V1_AGGREGATE
    assert manifest["aggregate_review_sha256"] == aggregate == V2_AGGREGATE
    assert manifest["review_counts"]["headline_eligible"] == 19
    assert manifest["scope_counts"] == {
        "all_cases_contract_operations": 78,
        "all_cases_runtime_executable_operations": 75,
        "headline_contract_operations": 76,
        "headline_runtime_executable_operations": 73,
    }
