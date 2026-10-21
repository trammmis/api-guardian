"""E3-B pre-system Ground Truth 冻结完整性测试。."""

import hashlib
import json
from pathlib import Path

from benchmarks.guardian.real_world_models import (
    GroundTruthFreezeManifest,
    PreSystemReviewRecord,
)

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "benchmarks" / "guardian" / "reports" / "e3"
P1_INTEGRATION_MANIFEST = ROOT / "benchmarks" / "guardian" / "reports" / "p1_integration" / "integration_manifest.json"


def _sha256(path: Path) -> str:
    """计算测试 artifact 的 SHA256。."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest() -> GroundTruthFreezeManifest:
    """读取已冻结 manifest。."""
    return GroundTruthFreezeManifest.model_validate_json(
        (REPORT_ROOT / "ground_truth_freeze_manifest.json").read_text(encoding="utf-8")
    )


def test_e3_ground_truth_freeze_is_complete_and_pre_system() -> None:
    """验证记录数量、分层与 System Output 边界。."""
    manifest = _load_manifest()
    assert manifest.guardian_execution_permitted is False
    assert manifest.guardian_system_output_status == "not_generated_before_ground_truth_freeze"
    assert manifest.review_counts == {
        "total": 20,
        "confirmed": 17,
        "probable": 2,
        "ambiguous": 1,
        "excluded": 0,
        "headline_eligible": 19,
    }
    assert manifest.repository_pair_counts == {
        "immich-app/immich": 7,
        "supabase/auth": 5,
        "netbirdio/netbird": 5,
        "appwrite/specs": 3,
    }
    assert "goauthentik/authentik" not in manifest.repository_pair_counts
    assert manifest.guardian_output_paths_absent_at_freeze
    integrity = json.loads((REPORT_ROOT / "integrity_evidence.json").read_text(encoding="utf-8"))
    assert integrity["system_output_created_after_v2_freeze"] is True
    for relative_path in manifest.guardian_output_paths_absent_at_freeze:
        assert (ROOT / relative_path).exists()


def test_e3_frozen_review_hashes_and_records_are_valid() -> None:
    """逐文件验证冻结哈希和 Pydantic Ground Truth schema。."""
    manifest = _load_manifest()
    aggregate_source = "\n".join(
        f"{artifact.path}:{artifact.sha256}"
        for artifact in sorted(manifest.review_artifacts, key=lambda item: item.path)
    ).encode()
    assert hashlib.sha256(aggregate_source).hexdigest() == manifest.aggregate_review_sha256
    individual_records: dict[str, PreSystemReviewRecord] = {}
    for artifact in manifest.review_artifacts:
        path = ROOT / artifact.path
        assert path.exists()
        assert path.stat().st_size == artifact.bytes
        assert _sha256(path) == artifact.sha256
        if "/reviews/pre_system/" in artifact.path:
            record = PreSystemReviewRecord.model_validate_json(path.read_text(encoding="utf-8"))
            individual_records[record.case_id] = record

    jsonl_records = [
        PreSystemReviewRecord.model_validate(json.loads(line))
        for line in (REPORT_ROOT / "review_records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(individual_records) == 20
    assert {record.case_id for record in jsonl_records} == set(individual_records)
    assert all(not record.external_refs for record in jsonl_records)
    assert all(record.base_spec_bytes <= 5 * 1024 * 1024 for record in jsonl_records)
    assert all(record.head_spec_bytes <= 5 * 1024 * 1024 for record in jsonl_records)


def test_e3_core_and_prior_artifacts_still_match_freeze() -> None:
    """确认旧证据不变，并只接受 P1-I 审计过的后续 Core 哈希。."""
    manifest = _load_manifest()
    p1_integration = json.loads(P1_INTEGRATION_MANIFEST.read_text(encoding="utf-8"))
    allowed_changes = set(p1_integration["allowed_production_changes"])
    production_hashes_after = p1_integration["production_hashes_after"]
    for relative_path, expected_hash in manifest.production_core_hashes.items():
        current_hash = _sha256(ROOT / relative_path)
        if relative_path in allowed_changes:
            assert current_hash == production_hashes_after[relative_path]
        else:
            assert current_hash == expected_hash
    for relative_path, expected_hash in manifest.frozen_prior_artifact_hashes.items():
        assert _sha256(ROOT / relative_path) == expected_hash
