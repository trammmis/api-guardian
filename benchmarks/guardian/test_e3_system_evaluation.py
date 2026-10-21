"""E3-B System Evaluation artifact 与指标完整性测试."""

import hashlib
import json
from pathlib import Path
from typing import Any

REPORT_ROOT = Path("benchmarks/guardian/reports/e3")
V1_AGGREGATE = "3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36"
V2_AGGREGATE = "7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94"


def read_json(path: Path) -> dict[str, Any]:
    """读取 JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL objects."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def sha256_file(path: Path) -> str:
    """计算 artifact SHA256."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v1_and_human_confirmed_v2_are_preserved() -> None:
    """v1 不覆盖，v2 使用用户确认的唯一修订."""
    v1 = read_json(REPORT_ROOT / "ground_truth_freeze_v1.json")
    v2 = read_json(REPORT_ROOT / "ground_truth_freeze_v2.json")
    assert v1["aggregate_review_sha256"] == V1_AGGREGATE
    assert v2["aggregate_review_sha256"] == V2_AGGREGATE
    assert v2["status"] == "human_confirmed"
    amendments = read_jsonl(REPORT_ROOT / "ground_truth_amendments.jsonl")
    assert {value["case_id"] for value in amendments} == {"rw_netbird_004_agent_network_settings_rework"}
    assert {value["confirmation_state"] for value in amendments} == {"human_confirmed"}


def test_primary_system_output_is_complete_and_frozen() -> None:
    """主结果恰好覆盖 20 个 Case，且最终化未改 raw."""
    results = read_jsonl(REPORT_ROOT / "raw_guardian_results.jsonl")
    integrity = read_json(REPORT_ROOT / "integrity_evidence.json")
    assert len(results) == 20
    assert len({value["case_id"] for value in results}) == 20
    assert sha256_file(REPORT_ROOT / "raw_guardian_results.jsonl") == integrity["primary_raw_system_output"]["sha256"]
    assert integrity["system_output_created_after_v2_freeze"] is True
    assert integrity["production_core_unchanged"] is True
    assert integrity["e2_e2_5_raw_unchanged"] is True


def test_e3_headline_metrics_preserve_errors_and_selector_view() -> None:
    """端到端指标保留 ERROR，selector-only 视图不把 ERROR 当缩减收益."""
    metrics = read_json(REPORT_ROOT / "summary.json")["metrics"]
    assert metrics["contract_verdict_accuracy"]["numerator"] == 14
    assert metrics["contract_verdict_accuracy"]["denominator"] == 19
    assert metrics["system_error_rate"]["numerator"] == 4
    assert metrics["system_error_rate"]["denominator"] == 19
    assert metrics["executable_regression_scope_recall"]["numerator"] == 58
    assert metrics["executable_regression_scope_recall"]["denominator"] == 73
    assert metrics["executable_regression_scope_recall_successful_pipeline"]["numerator"] == 58
    assert metrics["executable_regression_scope_recall_successful_pipeline"]["denominator"] == 58
    assert metrics["mean_executable_scope_reduction_successful_pipeline"]["denominator"] == 15


def test_supabase_diagnostics_are_isolated_and_sanitized() -> None:
    """诊断重放不进入 headline，且不保留本地绝对路径."""
    values = read_jsonl(REPORT_ROOT / "oasdiff_error_diagnostics.jsonl")
    assert len(values) == 4
    assert {value["exit_code"] for value in values} == {102}
    assert all(value["included_in_headline_metrics"] is False for value in values)
    serialized = json.dumps(values, ensure_ascii=False).lower()
    assert "appdata" not in serialized
    assert "guardian-e3-diagnostic-" not in serialized
    assert "#/components/responses/unauthorizedresponse" in serialized


def test_no_forbidden_e3_b_execution_was_recorded() -> None:
    """E3-B 未运行 Runtime、Schemathesis、LLM 或公网生产 API."""
    integrity = read_json(REPORT_ROOT / "integrity_evidence.json")
    assert integrity["public_production_api_called"] is False
    assert integrity["schemathesis_executed"] is False
    assert integrity["llm_executed"] is False
    assert integrity["e3_c_runtime_executed"] is False
    summary = read_json(REPORT_ROOT / "summary.json")
    assert summary["miss_analysis"]["true_selector_false_negatives"] == []
    assert summary["miss_analysis"]["normalization_error_cases"] == []
