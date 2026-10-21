"""将用户确认的 E3-B Ground Truth proposed v2 固化为 human-confirmed freeze.

本脚本只处理 Ground Truth 元数据与冻结 artifact，不导入或执行 Guardian Core。
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "benchmarks" / "guardian" / "datasets" / "real_world" / "reviews"
REPORT_ROOT = ROOT / "benchmarks" / "guardian" / "reports" / "e3"
V1_MANIFEST_PATH = REPORT_ROOT / "ground_truth_freeze_manifest.json"
V1_ALIAS_PATH = REPORT_ROOT / "ground_truth_freeze_v1.json"
PROPOSED_V2_MANIFEST_PATH = REPORT_ROOT / "ground_truth_freeze_v2_proposed.json"
FINAL_V2_MANIFEST_PATH = REPORT_ROOT / "ground_truth_freeze_v2.json"
FINAL_V2_HASH_PATH = REPORT_ROOT / "ground_truth_freeze_v2_file_sha256.txt"
PROPOSED_REVIEW_ROOT = DATASET_ROOT / "pre_system_v2_proposed"
FINAL_REVIEW_ROOT = DATASET_ROOT / "pre_system_v2"
PROPOSED_REVIEW_JSONL_PATH = REPORT_ROOT / "review_records_v2_proposed.jsonl"
FINAL_REVIEW_JSONL_PATH = REPORT_ROOT / "review_records_v2.jsonl"
AMENDMENTS_PATH = REPORT_ROOT / "ground_truth_amendments.jsonl"
PROPOSED_AMENDMENTS_PATH = REPORT_ROOT / "ground_truth_amendments_proposed.jsonl"
CONFIRMATION_PATH = REPORT_ROOT / "ground_truth_v2_confirmation.md"

EXPECTED_V1_AGGREGATE = "3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36"
EXPECTED_V1_FILE_HASH = "65576075bc38149e5105106fc9401e1ad8f88b0a0f474144fad7185c3431194d"
EXPECTED_V2_AGGREGATE = "7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94"
SYSTEM_OUTPUT_PATHS = (
    REPORT_ROOT / "raw_guardian_results.jsonl",
    REPORT_ROOT / "real_world_misses.jsonl",
    REPORT_ROOT / "summary.json",
    REPORT_ROOT / "summary.csv",
    REPORT_ROOT / "e3_contract_scope_report.md",
    REPORT_ROOT / "run_manifest.json",
)


def sha256_bytes(data: bytes) -> str:
    """计算字节 SHA256."""
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    """生成稳定、可审计 JSON."""
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def jsonl_bytes(values: list[dict[str, Any]]) -> bytes:
    """生成稳定 JSONL."""
    return b"".join(json.dumps(value, ensure_ascii=False, sort_keys=True).encode() + b"\n" for value in values)


def write_bytes(path: Path, data: bytes) -> None:
    """写入 final v2 artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def verify_file(path: Path, expected_hash: str, expected_bytes: int | None = None) -> None:
    """验证单个冻结 artifact 的大小与 SHA256."""
    data = path.read_bytes()
    if expected_bytes is not None and len(data) != expected_bytes:
        raise RuntimeError(f"artifact byte count changed: {path.relative_to(ROOT).as_posix()}")
    if sha256_bytes(data) != expected_hash:
        raise RuntimeError(f"artifact hash changed: {path.relative_to(ROOT).as_posix()}")


def verify_v1() -> dict[str, Any]:
    """确认 original v1 manifest 与全部 review artifact 未变化."""
    verify_file(V1_MANIFEST_PATH, EXPECTED_V1_FILE_HASH)
    if V1_ALIAS_PATH.read_bytes() != V1_MANIFEST_PATH.read_bytes():
        raise RuntimeError("ground_truth_freeze_v1 alias differs from original manifest")
    manifest = json.loads(V1_MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["aggregate_review_sha256"] != EXPECTED_V1_AGGREGATE:
        raise RuntimeError("v1 aggregate hash changed")
    for artifact in manifest["review_artifacts"]:
        verify_file(ROOT / artifact["path"], artifact["sha256"], artifact["bytes"])
    return manifest


def verify_preflight_hashes(v1_manifest: dict[str, Any]) -> None:
    """确认 production Core 与 E2/E2.5 冻结 artifact 未变化."""
    for path, expected in v1_manifest["production_core_hashes"].items():
        verify_file(ROOT / path, expected)
    for path, expected in v1_manifest["frozen_prior_artifact_hashes"].items():
        verify_file(ROOT / path, expected)


def verify_system_outputs_absent() -> list[str]:
    """证明 final v2 freeze 发生在任何 System Output 之前."""
    present = [path.relative_to(ROOT).as_posix() for path in SYSTEM_OUTPUT_PATHS if path.exists()]
    if present:
        raise RuntimeError(f"system output exists before final v2 freeze: {present}")
    return [path.relative_to(ROOT).as_posix() for path in SYSTEM_OUTPUT_PATHS]


def verify_proposed_v2() -> dict[str, Any]:
    """确认用户批准的 proposed v2 aggregate 与其全部 artifact 一致."""
    manifest = json.loads(PROPOSED_V2_MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["aggregate_review_sha256"] != EXPECTED_V2_AGGREGATE:
        raise RuntimeError("proposed v2 aggregate does not match user-confirmed hash")
    for artifact in manifest["review_artifacts"]:
        verify_file(ROOT / artifact["storage_path"], artifact["sha256"], artifact["bytes"])
    aggregate = sha256_bytes(
        "\n".join(
            f"{item['canonical_path']}:{item['sha256']}"
            for item in sorted(manifest["review_artifacts"], key=lambda item: item["canonical_path"])
        ).encode()
    )
    if aggregate != EXPECTED_V2_AGGREGATE:
        raise RuntimeError("proposed v2 aggregate cannot be reproduced")
    return manifest


def finalize_amendments(confirmed_at: str) -> list[dict[str, Any]]:
    """保留 pending log 副本并将正式 amendment 状态更新为 human_confirmed."""
    proposed_data = AMENDMENTS_PATH.read_bytes()
    write_bytes(PROPOSED_AMENDMENTS_PATH, proposed_data)
    proposed = [json.loads(line) for line in proposed_data.decode().splitlines() if line.strip()]
    finalized: list[dict[str, Any]] = []
    for amendment in proposed:
        updated = dict(amendment)
        updated.update(
            {
                "reviewer": "human_confirmed_by_user",
                "confirmation_state": "human_confirmed",
                "confirmation_source": "explicit_user_confirmation",
                "confirmed_at": confirmed_at,
            }
        )
        finalized.append(updated)
    write_bytes(AMENDMENTS_PATH, jsonl_bytes(finalized))
    return finalized


def finalize_review_artifacts(proposed_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """把 proposed review 内容按原字节固化到 final v2 路径."""
    FINAL_REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    final_artifacts: list[dict[str, Any]] = []
    for artifact in proposed_manifest["review_artifacts"]:
        source = ROOT / artifact["storage_path"]
        if artifact["canonical_path"].endswith("review_records.jsonl"):
            destination = FINAL_REVIEW_JSONL_PATH
        elif artifact["canonical_path"].endswith("real_world_cases.jsonl"):
            destination = source
        else:
            destination = FINAL_REVIEW_ROOT / Path(artifact["canonical_path"]).name
        data = source.read_bytes()
        if destination != source:
            write_bytes(destination, data)
        final_artifacts.append(
            {
                "canonical_path": artifact["canonical_path"],
                "storage_path": destination.relative_to(ROOT).as_posix(),
                "sha256": sha256_bytes(data),
                "bytes": len(data),
            }
        )
    return sorted(final_artifacts, key=lambda item: item["canonical_path"])


def finalize_manifest(
    v1_manifest: dict[str, Any],
    proposed_manifest: dict[str, Any],
    final_artifacts: list[dict[str, Any]],
    confirmed_at: str,
    absent_outputs: list[str],
    amendments: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成用户确认后的 final v2 manifest."""
    aggregate = sha256_bytes(
        "\n".join(f"{item['canonical_path']}:{item['sha256']}" for item in final_artifacts).encode()
    )
    if aggregate != EXPECTED_V2_AGGREGATE:
        raise RuntimeError("final v2 aggregate differs from the user-confirmed value")
    return {
        "schema_version": "guardian-e3-b-ground-truth-freeze-v2",
        "stage": "E3-B final Ground Truth freeze",
        "status": "human_confirmed",
        "frozen_at": confirmed_at,
        "reviewer_basis": "AI-assisted initial review with an explicit user-confirmed amendment",
        "confirmation": {
            "state": "human_confirmed",
            "source": "explicit_user_confirmation",
            "confirmed_aggregate_review_sha256": EXPECTED_V2_AGGREGATE,
        },
        "supersedes": proposed_manifest["supersedes"],
        "ground_truth_sources": proposed_manifest["ground_truth_sources"],
        "system_output_used": "none",
        "guardian_execution_permitted": True,
        "guardian_output_paths_absent_at_v2_freeze": absent_outputs,
        "guardian_system_output_status": "not_generated_before_final_v2_freeze",
        "review_artifacts": final_artifacts,
        "aggregate_review_sha256": aggregate,
        "review_counts": proposed_manifest["review_counts"],
        "scope_counts": proposed_manifest["scope_counts"]["proposed_v2"],
        "amendment_count": len(amendments),
        "amended_case_count": len({item["case_id"] for item in amendments}),
        "amendment_log_path": AMENDMENTS_PATH.relative_to(ROOT).as_posix(),
        "production_core_hashes": v1_manifest["production_core_hashes"],
        "frozen_prior_artifact_hashes": v1_manifest["frozen_prior_artifact_hashes"],
        "repositories": v1_manifest["repositories"],
        "repository_pair_counts": v1_manifest["repository_pair_counts"],
        "material_passport": {
            **v1_manifest["material_passport"],
            "ground_truth_version": "v2",
            "system_output_used_for_amendment": "none",
            "confirmation_source": "explicit user confirmation in Codex task",
        },
    }


def confirmation_markdown(manifest: dict[str, Any], manifest_file_hash: str) -> str:
    """记录 final v2 的确认与完整性证据."""
    return "\n".join(
        [
            "# E3-B Ground Truth v2 Confirmation",
            "",
            "## Material Passport",
            "",
            "- Origin: E3-B pre-system human spot-check",
            "- Confirmation state: `human_confirmed`",
            f"- Confirmation timestamp: `{manifest['frozen_at']}`",
            "- System output used: `none`",
            "",
            "## Freeze Evidence",
            "",
            f"- Preserved v1 aggregate SHA256: `{EXPECTED_V1_AGGREGATE}`",
            f"- Final v2 aggregate SHA256: `{manifest['aggregate_review_sha256']}`",
            f"- Final v2 manifest file SHA256: `{manifest_file_hash}`",
            "- Production Core hashes: verified against v1 preflight",
            "- E2/E2.5 artifact hashes: verified against v1 preflight",
            "- Guardian/oasdiff System Output before v2 freeze: absent",
            "",
            "## Approved Amendment",
            "",
            "`rw_netbird_004_agent_network_settings_rework` removes",
            "`POST /api/agent-network/settings` from both ContractAffectedScope and",
            "RuntimeExecutableScope. Contract verdict remains `fail`.",
            "",
        ]
    )


def main() -> None:
    """固化 final v2 并停在 System Evaluation 之前."""
    console = Console()
    v1_manifest = verify_v1()
    verify_preflight_hashes(v1_manifest)
    absent_outputs = verify_system_outputs_absent()
    proposed_manifest = verify_proposed_v2()
    confirmed_at = datetime.now(UTC).isoformat()
    amendments = finalize_amendments(confirmed_at)
    final_artifacts = finalize_review_artifacts(proposed_manifest)
    manifest = finalize_manifest(
        v1_manifest,
        proposed_manifest,
        final_artifacts,
        confirmed_at,
        absent_outputs,
        amendments,
    )
    manifest_data = json_bytes(manifest)
    write_bytes(FINAL_V2_MANIFEST_PATH, manifest_data)
    manifest_file_hash = sha256_bytes(manifest_data)
    write_bytes(FINAL_V2_HASH_PATH, f"{manifest_file_hash}  {FINAL_V2_MANIFEST_PATH.name}\n".encode())
    write_bytes(CONFIRMATION_PATH, confirmation_markdown(manifest, manifest_file_hash).encode())

    verify_v1()
    verify_preflight_hashes(v1_manifest)
    verify_file(FINAL_V2_MANIFEST_PATH, manifest_file_hash)
    console.print(f"[green]Finalized human-confirmed v2: {EXPECTED_V2_AGGREGATE}[/green]")
    console.print("[yellow]System Output was absent at the final v2 freeze boundary.[/yellow]")


if __name__ == "__main__":
    main()
