"""E4 frozen safe-fixture checks for analysis failure isolation."""

import pytest

from app.services.guardian.models import GuardianVerdict
from tests.guardian.test_analysis import CHANGE, FactDrivenLLM, FakeStructuredLLM, _run_main_graph


@pytest.mark.parametrize(
    ("name", "risk_llm", "diagnosis_llm"),
    [
        ("timeout", FactDrivenLLM(TimeoutError()), FactDrivenLLM()),
        ("invalid_structured_output", FakeStructuredLLM({"change_id": CHANGE.change_id}), FactDrivenLLM()),
        ("provider_failure", FactDrivenLLM(RuntimeError("provider unavailable")), FactDrivenLLM()),
        ("provider_429", FactDrivenLLM(RuntimeError("provider returned 429")), FactDrivenLLM()),
    ],
)
def test_analysis_failure_does_not_change_frozen_verdicts(
    name: str,
    risk_llm: FactDrivenLLM | FakeStructuredLLM,
    diagnosis_llm: FactDrivenLLM,
) -> None:
    """Every safe failure fixture degrades analysis but preserves all verdicts."""
    del name
    state = _run_main_graph(risk_llm=risk_llm, diagnosis_llm=diagnosis_llm)

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.FAIL
    assert state["final_verdict"] == GuardianVerdict.FAIL
    assert state["analysis_degraded"] is True
    assert state["analysis_warnings"]
