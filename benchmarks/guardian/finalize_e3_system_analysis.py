"""从冻结 raw System Output 完成 E3-B 分析，不重跑任何外部工具."""

import json
from datetime import UTC, datetime
from typing import Any

from rich.console import Console

from benchmarks.guardian.e3_models import E3MissClassification, E3MissRecord, E3SystemResult
from benchmarks.guardian.e3_system_evaluation import (
    EXPECTED_V1_AGGREGATE,
    EXPECTED_V2_AGGREGATE,
    MISSES_PATH,
    RAW_RESULTS_PATH,
    REPORT_PATH,
    REPORT_ROOT,
    ROOT,
    RUN_MANIFEST_PATH,
    SUMMARY_PATH,
    build_misses,
    integrity_snapshot,
    load_ground_truth,
    load_jsonl,
    render_report,
    sha256_file,
    summarize,
    verify_freezes_and_preflight,
    write_json,
    write_jsonl,
    write_summary_csv,
)

DIAGNOSTICS_PATH = REPORT_ROOT / "oasdiff_error_diagnostics.jsonl"
INTEGRITY_PATH = REPORT_ROOT / "integrity_evidence.json"


def load_diagnostics() -> dict[str, dict[str, Any]]:
    """读取非 headline 诊断重放结果并验证安全边界."""
    diagnostics = {value["case_id"]: value for value in load_jsonl(DIAGNOSTICS_PATH)}
    for value in diagnostics.values():
        excerpt = value["safe_stderr_excerpt"].lower()
        if any(token in excerpt for token in ("appdata", "\\temp\\", "c:\\")):
            raise RuntimeError("diagnostic artifact contains a local absolute path")
        if value["included_in_headline_metrics"]:
            raise RuntimeError("diagnostic replay must not enter headline metrics")
    return diagnostics


def enrich_error_misses(
    misses: tuple[E3MissRecord, ...],
    diagnostics: dict[str, dict[str, Any]],
) -> tuple[E3MissRecord, ...]:
    """给工具 ERROR miss 增加脱敏诊断证据，不改 primary System Output."""
    values: list[E3MissRecord] = []
    for item in misses:
        diagnostic = diagnostics.get(item.case_id)
        if diagnostic is None or item.classification != E3MissClassification.OTHER:
            values.append(item)
            continue
        excerpt = str(diagnostic["safe_stderr_excerpt"]).strip()
        values.append(
            item.model_copy(
                update={
                    "oasdiff_exit_code": diagnostic["exit_code"],
                    "evidence": item.evidence
                    + (
                        "Non-headline diagnostic replay retained the primary raw result unchanged.",
                        f"oasdiff exit code={diagnostic['exit_code']}; {excerpt}",
                    ),
                    "likely_cause": (
                        "oasdiff 1.28.0 could not load the Supabase Base Spec because "
                        "#/components/responses/UnauthorizedResponse was not represented as the "
                        "schema reference shape expected by oasdiff."
                    ),
                }
            )
        )
    return tuple(values)


def build_error_analysis(diagnostics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """构建 ERROR 诊断摘要."""
    cases = [
        {
            "case_id": case_id,
            "exit_code": value["exit_code"],
            "cause": (
                "oasdiff could not load Base #/components/responses/UnauthorizedResponse "
                "(expected a schema reference object)"
            ),
            "included_in_headline_metrics": False,
        }
        for case_id, value in sorted(diagnostics.items())
    ]
    return {
        "primary_raw_results_unchanged": True,
        "diagnostic_replay_in_headline_metrics": False,
        "case_count": len(cases),
        "cases": cases,
    }


def finalize() -> None:
    """仅重算派生报告与完整性证据."""
    v2_manifest = verify_freezes_and_preflight(require_system_outputs_absent=False)
    raw_hash_before = sha256_file(RAW_RESULTS_PATH)
    records = load_ground_truth()
    results = tuple(E3SystemResult.model_validate(value) for value in load_jsonl(RAW_RESULTS_PATH))
    results_by_case = {value.case_id: value for value in results}
    if len(results) != len(records) or set(results_by_case) != {value.case_id for value in records}:
        raise RuntimeError("frozen raw System Output is not complete")

    diagnostics = load_diagnostics()
    error_case_ids = {value.case_id for value in results if value.error_code == "oasdiff_execution_failed"}
    if set(diagnostics) != error_case_ids:
        raise RuntimeError("diagnostic Case set does not match primary oasdiff ERROR set")

    misses = enrich_error_misses(build_misses(records, results_by_case), diagnostics)
    summary = summarize(records, results, misses)
    summary["system_error_analysis"] = build_error_analysis(diagnostics)
    write_jsonl(MISSES_PATH, [item.model_dump(mode="json") for item in misses])
    write_json(SUMMARY_PATH, summary)
    write_summary_csv(summary)

    manifest = json.loads(RUN_MANIFEST_PATH.read_text(encoding="utf-8"))
    finalized_at = datetime.now(UTC)
    frozen_at = datetime.fromisoformat(manifest["ground_truth"]["frozen_at"])
    started_at = datetime.fromisoformat(manifest["started_at"])
    manifest["analysis_finalized_at"] = finalized_at.isoformat()
    manifest["oasdiff_error_diagnostics"] = {
        "artifact": DIAGNOSTICS_PATH.relative_to(ROOT).as_posix(),
        "case_count": len(diagnostics),
        "included_in_headline_metrics": False,
        "primary_raw_results_unchanged": True,
    }
    manifest["output_files"] = sorted(
        set(manifest["output_files"])
        | {
            DIAGNOSTICS_PATH.relative_to(ROOT).as_posix(),
            INTEGRITY_PATH.relative_to(ROOT).as_posix(),
        }
    )
    manifest["integrity"]["system_output_created_after_v2_freeze"] = started_at > frozen_at
    manifest["integrity"]["raw_guardian_results_sha256"] = raw_hash_before

    postflight = integrity_snapshot(v2_manifest)
    if postflight != manifest["integrity"]["postflight"]:
        raise RuntimeError("frozen artifacts changed during E3-B analysis finalization")
    if sha256_file(RAW_RESULTS_PATH) != raw_hash_before:
        raise RuntimeError("primary raw System Output changed during analysis finalization")

    write_json(RUN_MANIFEST_PATH, manifest)
    REPORT_PATH.write_text(render_report(summary, misses, manifest), encoding="utf-8")
    integrity = {
        "verified_at": finalized_at.isoformat(),
        "ground_truth_v1": {
            "preserved": True,
            "aggregate_review_sha256": EXPECTED_V1_AGGREGATE,
            "manifest_file_sha256": postflight["v1_manifest_file_sha256"],
        },
        "ground_truth_v2": {
            "preserved": True,
            "status": "human_confirmed",
            "aggregate_review_sha256": EXPECTED_V2_AGGREGATE,
            "manifest_file_sha256": postflight["v2_manifest_file_sha256"],
        },
        "system_output_created_after_v2_freeze": started_at > frozen_at,
        "primary_raw_system_output": {
            "path": RAW_RESULTS_PATH.relative_to(ROOT).as_posix(),
            "sha256": raw_hash_before,
            "case_count": len(results),
        },
        "production_core_unchanged": True,
        "production_core_hashes": postflight["production_core"],
        "e2_e2_5_raw_unchanged": True,
        "e2_e2_5_raw_hashes": postflight["prior_e2_e2_5"],
        "public_production_api_called": False,
        "schemathesis_executed": False,
        "llm_executed": False,
        "e3_c_runtime_executed": False,
    }
    write_json(INTEGRITY_PATH, integrity)
    Console().print(
        "[green]Finalized E3-B analysis from frozen raw output[/green] "
        f"({len(results)} pairs, {len(misses)} miss records)"
    )


if __name__ == "__main__":
    finalize()
