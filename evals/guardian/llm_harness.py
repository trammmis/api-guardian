"""API Guardian Phase 4 结构化 LLM 输出离线评测."""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.services.guardian.models import FailureDiagnosis, RiskExplanation

DEFAULT_CASES_PATH = Path(__file__).with_name("llm_cases.jsonl")


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    """读取小型人工事实/禁止声明/Mock 输出数据集."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate_llm_cases(cases: list[dict[str, Any]]) -> dict[str, float | int]:
    """用 schema 与事实白名单计算非 exact-match 指标."""
    structured_valid = 0
    risk_total = 0
    risk_grounded = 0
    diagnosis_total = 0
    category_correct = 0
    evidence_faithful = 0
    recommendations_relevant = 0
    hallucinated = 0

    for case in cases:
        case_type = case.get("type")
        output = case.get("mock_output")
        forbidden = tuple(str(value).casefold() for value in case.get("forbidden_claims", ()))
        serialized = json.dumps(output, ensure_ascii=False).casefold()
        has_hallucination = any(claim in serialized for claim in forbidden)
        hallucinated += int(has_hallucination)
        try:
            if case_type == "risk":
                risk_total += 1
                result = RiskExplanation.model_validate(output)
                structured_valid += 1
                required_terms = tuple(str(value).casefold() for value in case.get("required_terms", ()))
                fact_supported = not required_terms or any(term in serialized for term in required_terms)
                input_facts = case.get("input", {})
                expected_id = str(input_facts.get("change_id", "")) if isinstance(input_facts, dict) else ""
                risk_grounded += int(not has_hallucination and fact_supported and result.change_id == expected_id)
                recommendations_relevant += int(len(result.recommendation.strip()) >= 8)
            elif case_type == "diagnosis":
                diagnosis_total += 1
                result = FailureDiagnosis.model_validate(output)
                structured_valid += 1
                category_correct += int(result.category.value == case.get("expected_category"))
                allowed = set(case.get("allowed_evidence", ()))
                evidence_faithful += int(bool(result.evidence) and set(result.evidence).issubset(allowed))
                recommendations_relevant += int(len(result.recommendation.strip()) >= 8)
        except (ValidationError, TypeError, ValueError):
            continue

    total = len(cases)
    return {
        "total_cases": total,
        "risk_cases": risk_total,
        "diagnosis_cases": diagnosis_total,
        "structured_output_validity": _ratio(structured_valid, total),
        "risk_groundedness": _ratio(risk_grounded, risk_total),
        "diagnosis_category_accuracy": _ratio(category_correct, diagnosis_total),
        "evidence_faithfulness": _ratio(evidence_faithful, diagnosis_total),
        "recommendation_relevance": _ratio(recommendations_relevant, total),
        "serious_hallucination_rate": _ratio(hallucinated, total),
    }


def _ratio(numerator: int, denominator: int) -> float:
    """安全计算 0 到 1 的评测比例."""
    return numerator / denominator if denominator else 0.0


def main() -> None:
    """输出可被 CI 或文档直接消费的 JSON 指标."""
    print(json.dumps(evaluate_llm_cases(load_cases()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
