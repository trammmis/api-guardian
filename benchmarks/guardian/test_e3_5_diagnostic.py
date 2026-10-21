"""E3.5 最终 artifact 与冻结边界的离线回归测试."""

import json
from pathlib import Path

from benchmarks.guardian.e3_5_models import FallbackTrace

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "benchmarks" / "guardian" / "reports" / "e3_5"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_supplemental_metrics_remain_explicitly_non_headline() -> None:
    """补充指标必须带非 headline 标签且分子分母稳定."""
    summary = _load_json(REPORT / "summary.json")
    metrics = summary["supplemental_metrics"]

    assert metrics["label"].startswith("SUPPLEMENTAL DIAGNOSTIC ONLY")
    assert metrics["nonfallback_executable_recall"]["numerator"] == 31
    assert metrics["nonfallback_executable_recall"]["denominator"] == 31
    assert metrics["fallback_only_precision"]["numerator"] == 27
    assert metrics["fallback_only_precision"]["denominator"] == 1659
    assert metrics["tool_compatible_conditional_contract_accuracy"]["numerator"] == 14
    assert metrics["tool_compatible_conditional_contract_accuracy"]["denominator"] == 15
    assert summary["e3_b_headline_metrics_changed"] is False


def test_only_removed_operations_trigger_the_three_fallbacks() -> None:
    """三个全量 fallback 都应由无法匹配 Head 的删除 operation 触发."""
    lines = (REPORT / "fallback" / "fallback_traces.jsonl").read_text(encoding="utf-8").splitlines()
    traces = [FallbackTrace.model_validate_json(line) for line in lines]
    triggers = [trace for trace in traces if trace.fallback_triggered]

    assert len(triggers) == 3
    assert {trace.oasdiff_change_id for trace in triggers} == {"api-path-removed-without-deprecation"}
    assert all(trace.exact_head_operation_match is False for trace in triggers)
    assert all(trace.operation_id_match is False for trace in triggers)


def test_dependency_graph_diagnostic_matches_frozen_appwrite_scope() -> None:
    """诊断依赖图应精确复现两个 Appwrite 冻结 executable scope."""
    diagnostic = _load_json(REPORT / "fallback" / "dependency_graph_diagnostic.json")
    aggregate = diagnostic["aggregate"]

    assert aggregate["dependency_graph_recall"]["numerator"] == 27
    assert aggregate["dependency_graph_recall"]["denominator"] == 27
    assert aggregate["dependency_graph_precision"]["numerator"] == 27
    assert aggregate["dependency_graph_precision"]["denominator"] == 27
    assert diagnostic["production_selector_modified"] is False


def test_validator_disagreement_and_oasdiff_matrix_are_preserved() -> None:
    """验证器分歧和两个 oasdiff 版本的复现证据不得丢失."""
    validators = _load_json(REPORT / "tool_compatibility" / "validator_results.json")
    matrix = _load_json(REPORT / "tool_compatibility" / "version_matrix.json")

    assert validators["validator_disagreement"]["present"] is True
    redocly = validators["redocly"]["results"]
    minimal = [item for item in redocly if item["case_id"] == "minimal_reproducer"]
    assert len(minimal) == 2
    assert all(item["structural_valid"] is False for item in minimal)
    assert all(version["minimal_reproducer"]["exit_code"] == 102 for version in matrix["diagnostic_versions"])
    assert all(version["corrected_control"]["load_succeeded"] for version in matrix["diagnostic_versions"])


def test_frozen_artifacts_and_production_core_are_unchanged() -> None:
    """前后哈希快照必须证明所有冻结输入未变化."""
    manifest = _load_json(REPORT / "run_manifest.json")
    integrity = manifest["integrity"]

    assert integrity["preflight"] == integrity["postflight"]
    assert integrity["production_core_unchanged"] is True
    assert integrity["e3_b_raw_unchanged"] is True
    assert integrity["e3_v1_unchanged"] is True
    assert integrity["e3_v2_unchanged"] is True
    assert integrity["e2_e2_5_frozen_artifacts_unchanged"] is True


def test_persisted_diagnostic_does_not_contain_ephemeral_absolute_paths() -> None:
    """持久化诊断输出不得包含临时绝对路径."""
    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in REPORT.rglob("*")
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md", ".csv"}
    )

    assert "AppData" not in persisted
    assert "/Users/" not in persisted
    assert "guardian-e3-5-oasdiff" not in persisted
