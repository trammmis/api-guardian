"""One-shot E4 Condition-B and automated LLM execution harness."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from app.core.config import settings
from app.services.guardian.diagnosis import FailureDiagnoser
from app.services.guardian.models import (
    ChangeSeverity,
    ContractChange,
    OperationRef,
    RegressionCheck,
    RegressionFailure,
)
from app.services.guardian.risk import RiskAnalyzer

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "benchmarks" / "guardian" / "reports" / "e4"
CONSOLE = Console()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _operation(value: str | None) -> OperationRef | None:
    if value is None:
        return None
    method, path = value.split(" ", 1)
    return OperationRef(method=method, path=path, operation_id=None)


def _safe_error(exc: Exception) -> dict[str, str]:
    return {"error_type": type(exc).__name__, "safe_message": "production structured analysis call failed"}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


async def _generate_condition_b() -> None:
    output_path = REPORT_DIR / "condition_b_outputs.jsonl"
    manifest_path = REPORT_DIR / "condition_b_generation_manifest.json"
    if output_path.exists() or manifest_path.exists():
        raise RuntimeError("Condition B output already exists; one-shot generation refuses overwrite")

    input_path = REPORT_DIR / "condition_b_inputs.jsonl"
    inputs = _load_jsonl(input_path)
    risk_analyzer = RiskAnalyzer()
    diagnoser = FailureDiagnoser()
    started_at = datetime.now(UTC)
    records: list[dict[str, Any]] = []
    attempted = 0
    succeeded = 0
    failed = 0

    for case in inputs:
        changes = [ContractChange.model_validate(change) for change in case["changes"]]
        changes_by_id = {change.change_id: change for change in changes}
        risks: dict[str, Any] = {}
        risk_records: list[dict[str, Any]] = []
        diagnosis_records: list[dict[str, Any]] = []

        for change in changes:
            attempted += 1
            started = time.perf_counter()
            try:
                result = await risk_analyzer.analyze(change, scan_id=f"e4-condition-b-{case['case_id']}")
            except Exception as exc:
                failed += 1
                risk_records.append(
                    {
                        "change_id": change.change_id,
                        "status": "error",
                        "latency_seconds": time.perf_counter() - started,
                        **_safe_error(exc),
                    }
                )
            else:
                succeeded += 1
                risks[change.change_id] = result
                risk_records.append(
                    {
                        "change_id": change.change_id,
                        "status": "success",
                        "latency_seconds": time.perf_counter() - started,
                        "output": result.model_dump(mode="json"),
                    }
                )

        for item in case["failures"]:
            attempted += 1
            failure = RegressionFailure.model_validate(item["failure"])
            checks = tuple(RegressionCheck.model_validate(check) for check in item["checks"])
            related_change = changes_by_id.get(item.get("related_change_id"))
            risk = risks.get(item.get("related_change_id"))
            started = time.perf_counter()
            try:
                result = await diagnoser.diagnose(
                    failure,
                    related_change=related_change,
                    risk=risk,
                    checks=checks,
                    scan_id=f"e4-condition-b-{case['case_id']}",
                )
            except Exception as exc:
                failed += 1
                diagnosis_records.append(
                    {
                        "failure_id": failure.failure_id,
                        "status": "error",
                        "latency_seconds": time.perf_counter() - started,
                        **_safe_error(exc),
                    }
                )
            else:
                succeeded += 1
                diagnosis_records.append(
                    {
                        "failure_id": failure.failure_id,
                        "status": "success",
                        "latency_seconds": time.perf_counter() - started,
                        "output": result.model_dump(mode="json"),
                    }
                )

        records.append(
            {"case_id": case["case_id"], "risk_explanations": risk_records, "failure_diagnoses": diagnosis_records}
        )
        CONSOLE.print(f"[cyan]condition-b[/cyan] {case['case_id']} complete")

    payload = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    _atomic_write(output_path, payload)
    finished_at = datetime.now(UTC)
    manifest = {
        "stage": "E4_A_CONDITION_B_FREEZE",
        "generation_started_at_utc": started_at.isoformat(),
        "generation_finished_at_utc": finished_at.isoformat(),
        "model": settings.DEFAULT_LLM_MODEL,
        "temperature": 0,
        "structured_output_method": "function_calling",
        "thinking": "disabled",
        "fallback_enabled": False,
        "max_retry_attempts": settings.MAX_LLM_CALL_RETRIES,
        "total_timeout_seconds": settings.LLM_TOTAL_TIMEOUT,
        "case_count": len(records),
        "analysis_calls_attempted": attempted,
        "analysis_calls_succeeded": succeeded,
        "analysis_calls_failed": failed,
        "input_sha256": _sha256(input_path),
        "output_sha256": _sha256(output_path),
        "raw_provider_response_saved": False,
        "token_usage": None,
        "token_usage_reason": "Production structured-output abstraction does not expose provider usage metadata.",
        "regeneration_allowed": False,
    }
    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _related_change_for_automated(case: dict[str, Any]) -> ContractChange | None:
    evidence = case["input"].get("available_evidence", [])
    marker = next((item for item in evidence if item.startswith("contract_change=")), None)
    if marker is None:
        return None
    change_id, summary = marker.removeprefix("contract_change=").split(":", 1)
    operation = _operation(case["input"].get("operation"))
    return ContractChange(
        change_id=change_id,
        summary=summary,
        severity=ChangeSeverity.ERROR,
        method=operation.method if operation else None,
        path=operation.path if operation else None,
        operation_id=None,
        section="responses",
    )


async def _run_automated() -> None:
    output_path = REPORT_DIR / "automated_llm_eval.jsonl"
    existing = _load_jsonl(output_path) if output_path.exists() else []
    if existing and not (len(existing) == 1 and existing[0].get("status") == "not_run"):
        raise RuntimeError("Automated output already exists; one-shot evaluation refuses overwrite")

    cases_path = REPORT_DIR / "automated_eval_cases.jsonl"
    cases = _load_jsonl(cases_path)
    risk_analyzer = RiskAnalyzer()
    diagnoser = FailureDiagnoser()
    records: list[dict[str, Any]] = []

    for case in cases:
        started = time.perf_counter()
        try:
            if case["type"] == "risk":
                change = ContractChange.model_validate(case["input"])
                result = await risk_analyzer.analyze(change, scan_id=f"e4-auto-{case['case_id']}")
                record = {
                    "case_id": case["case_id"],
                    "type": "risk",
                    "status": "success",
                    "latency_seconds": time.perf_counter() - started,
                    "schema_valid": True,
                    "category_correct": result.risk_level.value in case["acceptable_risk_levels"],
                    "output": result.model_dump(mode="json"),
                    "token_usage": None,
                }
            else:
                data = case["input"]
                failure = RegressionFailure(
                    failure_id=data["failure_id"],
                    operation=_operation(data.get("operation")),
                    check_name=data["check_name"],
                    category=data["tool_category"],
                    safe_message=data["safe_message"],
                    status_code=data.get("status_code"),
                    phase=data.get("phase"),
                )
                related_change = _related_change_for_automated(case)
                result = await diagnoser.diagnose(
                    failure,
                    related_change=related_change,
                    risk=None,
                    checks=(),
                    scan_id=f"e4-auto-{case['case_id']}",
                )
                record = {
                    "case_id": case["case_id"],
                    "type": "diagnosis",
                    "status": "success",
                    "latency_seconds": time.perf_counter() - started,
                    "schema_valid": True,
                    "category_correct": result.category.value == case["expected_category"],
                    "evidence_subset_valid": set(result.evidence).issubset(set(data["available_evidence"])),
                    "output": result.model_dump(mode="json"),
                    "token_usage": None,
                }
        except Exception as exc:
            record = {
                "case_id": case["case_id"],
                "type": case["type"],
                "status": "error",
                "latency_seconds": time.perf_counter() - started,
                "schema_valid": False,
                **_safe_error(exc),
                "token_usage": None,
            }
        records.append(record)
        CONSOLE.print(f"[green]automated[/green] {case['case_id']} {record['status']}")

    payload = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    _atomic_write(output_path, payload)
    latencies = [float(record["latency_seconds"]) for record in records]
    summary = {
        "stage": "E4_A_AUTOMATED_LLM_EVAL",
        "status": "raw_execution_complete_review_pending",
        "case_count": len(records),
        "success_count": sum(record["status"] == "success" for record in records),
        "provider_error_count": sum(record["status"] == "error" for record in records),
        "structured_output_success_count": sum(bool(record.get("schema_valid")) for record in records),
        "category_correct_count": sum(bool(record.get("category_correct")) for record in records),
        "latency_seconds": {
            "mean": _mean(latencies),
            "median": _median(latencies),
            "p95": sorted(latencies)[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None,
        },
        "token_usage": None,
        "token_usage_reason": "Production structured-output abstraction does not expose provider usage metadata.",
        "cases_sha256": _sha256(cases_path),
        "raw_results_sha256": _sha256(output_path),
    }
    _atomic_write(REPORT_DIR / "automated_llm_raw_summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("condition-b", "automated"))
    args = parser.parse_args()
    if args.mode == "condition-b":
        await _generate_condition_b()
    else:
        await _run_automated()


if __name__ == "__main__":
    asyncio.run(_main())
