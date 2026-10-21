"""Guardian Evaluation Harness 测试."""

from evals.guardian.harness import evaluate_guardian_cases
from evals.guardian.llm_harness import evaluate_llm_cases, load_cases


def test_guardian_evaluation_metrics() -> None:
    """默认样本应保持 100% verdict/recall，并量化保守 scope 的 precision."""
    result = evaluate_guardian_cases()

    assert result.total_cases == 10
    assert result.contract_cases == 5
    assert result.regression_cases == 5
    assert result.contract_verdict_accuracy == 1.0
    assert result.scope_recall == 1.0
    assert result.scope_precision == 0.6
    assert result.regression_verdict_accuracy == 1.0
    assert result.final_verdict_accuracy == 1.0
    assert result.error_classification_accuracy == 1.0


def test_guardian_llm_evaluation_metrics() -> None:
    """Phase 4 人工小数据集必须保持结构、grounding 和 evidence 目标."""
    metrics = evaluate_llm_cases(load_cases())

    assert metrics["total_cases"] == 20
    assert metrics["risk_cases"] == 10
    assert metrics["diagnosis_cases"] == 10
    assert metrics["structured_output_validity"] >= 0.95
    assert metrics["risk_groundedness"] >= 0.9
    assert metrics["diagnosis_category_accuracy"] >= 0.8
    assert metrics["evidence_faithfulness"] >= 0.95
    assert metrics["serious_hallucination_rate"] < 0.05
