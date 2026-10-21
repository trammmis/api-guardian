"""P1 removed-operation 实验策略的安全回归测试。."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.guardian.models import ContractChange, GuardianVerdict, OperationRef
from app.services.guardian.scope import determine_contract_verdict
from benchmarks.guardian.p1_removed_operation import select_test_scope_p1

ROOT = Path(__file__).resolve().parents[2]
SAFETY_CASES_PATH = ROOT / "benchmarks" / "guardian" / "reports" / "p1_removed_operation" / "safety_cases.jsonl"


def _load_cases() -> dict[str, dict[str, Any]]:
    """读取已在运行前冻结的 P1 safety cases。."""
    return {
        item["case_id"]: item
        for item in (json.loads(line) for line in SAFETY_CASES_PATH.read_text(encoding="utf-8").splitlines() if line)
    }


@pytest.mark.parametrize(
    "case_id",
    [
        "p1_safety_001_exact_removal",
        "p1_safety_002_multiple_removals",
        "p1_safety_003_removal_plus_mapped",
        "p1_safety_004_removal_plus_unresolved",
        "p1_safety_005_non_removal_unresolved",
        "p1_safety_006_operation_id_counterpart",
        "p1_safety_007_method_removed_path_remains",
        "p1_safety_008_entire_path_removed",
        "p1_safety_009_compatible_addition",
        "p1_safety_010_no_changes",
    ],
)
def test_frozen_safety_case(case_id: str) -> None:
    """十类安全场景必须精确符合运行前冻结的期望。."""
    case = _load_cases()[case_id]
    changes = tuple(ContractChange.model_validate(value) for value in case["changes"])
    base_operations = tuple(OperationRef.model_validate(value) for value in case["base_operations"])
    head_operations = tuple(OperationRef.model_validate(value) for value in case["head_operations"])

    selection = select_test_scope_p1(changes, base_operations, head_operations)
    verdict = determine_contract_verdict(changes, has_error=False)

    assert [operation.key for operation in selection.operations] == case["expected_p1_selected_operations"]
    assert selection.fallback_to_all is case["expected_p1_fallback"]
    assert verdict is GuardianVerdict(case["expected_contract_verdict"])
