"""评测 Contract Verdict Accuracy 与 Scope Recall/Precision."""

from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console

from app.services.guardian.models import (
    ChangeSeverity,
    ContractChange,
    GuardianVerdict,
    OperationRef,
    RegressionOutcome,
    RegressionResult,
)
from app.services.guardian.schemathesis import determine_final_verdict, determine_regression_verdict
from app.services.guardian.scope import determine_contract_verdict, select_test_scope


class GuardianEvaluationCase(BaseModel):
    """单个确定性 Guardian 评测样本."""

    model_config = ConfigDict(frozen=True)

    name: str
    operations: tuple[OperationRef, ...]
    changes: tuple[ContractChange, ...]
    has_error: bool = False
    expected_verdict: GuardianVerdict
    expected_affected_operation_keys: tuple[str, ...] = ()


class GuardianEvaluationResult(BaseModel):
    """Phase 1 与 Phase 2 的确定性核心评测指标."""

    model_config = ConfigDict(frozen=True)

    total_cases: int = Field(ge=0)
    contract_cases: int = Field(ge=0)
    regression_cases: int = Field(ge=0)
    contract_verdict_accuracy: float = Field(ge=0.0, le=1.0)
    scope_recall: float = Field(ge=0.0, le=1.0)
    scope_precision: float = Field(ge=0.0, le=1.0)
    regression_verdict_accuracy: float = Field(ge=0.0, le=1.0)
    final_verdict_accuracy: float = Field(ge=0.0, le=1.0)
    error_classification_accuracy: float = Field(ge=0.0, le=1.0)


class RegressionEvaluationCase(BaseModel):
    """独立评测 regression 聚合和 final verdict 的样本."""

    model_config = ConfigDict(frozen=True)

    name: str
    operations: tuple[OperationRef, ...]
    results: tuple[RegressionResult, ...]
    contract_verdict: GuardianVerdict
    expected_regression_verdict: GuardianVerdict
    expected_final_verdict: GuardianVerdict
    expected_error: bool = False


def default_evaluation_cases() -> tuple[GuardianEvaluationCase, ...]:
    """返回覆盖精确归属、保守回退、warning 与 ERROR 的离线样本."""
    operations = (
        OperationRef(method="GET", path="/users", operation_id="listUsers"),
        OperationRef(method="POST", path="/users", operation_id="createUser"),
        OperationRef(method="GET", path="/health", operation_id="health"),
    )
    return (
        GuardianEvaluationCase(
            name="no_change",
            operations=operations,
            changes=(),
            expected_verdict=GuardianVerdict.PASS,
        ),
        GuardianEvaluationCase(
            name="exact_error_change",
            operations=operations,
            changes=(
                ContractChange(
                    change_id="response-property-type-changed",
                    summary="response property type changed",
                    severity=ChangeSeverity.ERROR,
                    method="GET",
                    path="/users",
                    operation_id="listUsers",
                ),
            ),
            expected_verdict=GuardianVerdict.FAIL,
            expected_affected_operation_keys=("GET /users",),
        ),
        GuardianEvaluationCase(
            name="component_change_conservative_fallback",
            operations=operations,
            changes=(
                ContractChange(
                    change_id="schema-required-property-added",
                    summary="component schema changed",
                    severity=ChangeSeverity.ERROR,
                    section="components",
                ),
            ),
            expected_verdict=GuardianVerdict.FAIL,
            # Ground truth 只影响 createUser，但实现因无法精确归属而选择全部 operation。
            expected_affected_operation_keys=("POST /users",),
        ),
        GuardianEvaluationCase(
            name="warning_does_not_fail_default_policy",
            operations=operations,
            changes=(
                ContractChange(
                    change_id="ambiguous-change",
                    summary="potentially breaking change",
                    severity=ChangeSeverity.WARNING,
                    method="GET",
                    path="/health",
                    operation_id="health",
                ),
            ),
            expected_verdict=GuardianVerdict.PASS,
            expected_affected_operation_keys=("GET /health",),
        ),
        GuardianEvaluationCase(
            name="tool_error",
            operations=operations,
            changes=(),
            has_error=True,
            expected_verdict=GuardianVerdict.ERROR,
        ),
    )


def default_regression_evaluation_cases() -> tuple[RegressionEvaluationCase, ...]:
    """返回覆盖 PASS、FAIL、ERROR 与 contract/regression 组合的样本."""
    operation = OperationRef(method="GET", path="/items/{item_id}", operation_id="getItem")

    def result(outcome: RegressionOutcome) -> tuple[RegressionResult, ...]:
        return (RegressionResult(operation=operation, outcome=outcome),)

    return (
        RegressionEvaluationCase(
            name="regression_pass",
            operations=(operation,),
            results=result(RegressionOutcome.PASSED),
            contract_verdict=GuardianVerdict.PASS,
            expected_regression_verdict=GuardianVerdict.PASS,
            expected_final_verdict=GuardianVerdict.PASS,
        ),
        RegressionEvaluationCase(
            name="runtime_failure",
            operations=(operation,),
            results=result(RegressionOutcome.FAILED),
            contract_verdict=GuardianVerdict.PASS,
            expected_regression_verdict=GuardianVerdict.FAIL,
            expected_final_verdict=GuardianVerdict.FAIL,
        ),
        RegressionEvaluationCase(
            name="network_error",
            operations=(operation,),
            results=result(RegressionOutcome.ERROR),
            contract_verdict=GuardianVerdict.PASS,
            expected_regression_verdict=GuardianVerdict.ERROR,
            expected_final_verdict=GuardianVerdict.ERROR,
            expected_error=True,
        ),
        RegressionEvaluationCase(
            name="contract_fail_regression_pass",
            operations=(operation,),
            results=result(RegressionOutcome.PASSED),
            contract_verdict=GuardianVerdict.FAIL,
            expected_regression_verdict=GuardianVerdict.PASS,
            expected_final_verdict=GuardianVerdict.FAIL,
        ),
        RegressionEvaluationCase(
            name="contract_error_has_priority",
            operations=(operation,),
            results=result(RegressionOutcome.PASSED),
            contract_verdict=GuardianVerdict.ERROR,
            expected_regression_verdict=GuardianVerdict.PASS,
            expected_final_verdict=GuardianVerdict.ERROR,
            expected_error=True,
        ),
    )


def evaluate_guardian_cases(
    cases: tuple[GuardianEvaluationCase, ...] | None = None,
    regression_cases: tuple[RegressionEvaluationCase, ...] | None = None,
) -> GuardianEvaluationResult:
    """以 micro average 计算 Phase 1/2 的确定性 verdict 与 scope 指标."""
    selected_cases = cases or default_evaluation_cases()
    selected_regression_cases = regression_cases or default_regression_evaluation_cases()
    correct_verdicts = 0
    true_positive_operations = 0
    expected_operations = 0
    selected_operations = 0

    for case in selected_cases:
        verdict = determine_contract_verdict(case.changes, has_error=case.has_error)
        correct_verdicts += int(verdict == case.expected_verdict)

        scope = select_test_scope(case.changes, case.operations)
        actual_keys = {operation.key for operation in scope.operations}
        expected_keys = set(case.expected_affected_operation_keys)
        true_positive_operations += len(actual_keys & expected_keys)
        expected_operations += len(expected_keys)
        selected_operations += len(actual_keys)

    correct_regression_verdicts = 0
    correct_final_verdicts = 0
    correct_error_classifications = 0
    for case in selected_regression_cases:
        regression_verdict = determine_regression_verdict(case.results, case.operations)
        final_verdict = determine_final_verdict(
            case.contract_verdict,
            regression_verdict,
            has_error=False,
        )
        correct_regression_verdicts += int(regression_verdict == case.expected_regression_verdict)
        correct_final_verdicts += int(final_verdict == case.expected_final_verdict)
        correct_error_classifications += int((final_verdict == GuardianVerdict.ERROR) == case.expected_error)

    contract_total = len(selected_cases)
    regression_total = len(selected_regression_cases)
    return GuardianEvaluationResult(
        total_cases=contract_total + regression_total,
        contract_cases=contract_total,
        regression_cases=regression_total,
        contract_verdict_accuracy=correct_verdicts / contract_total if contract_total else 1.0,
        scope_recall=true_positive_operations / expected_operations if expected_operations else 1.0,
        scope_precision=true_positive_operations / selected_operations if selected_operations else 1.0,
        regression_verdict_accuracy=correct_regression_verdicts / regression_total if regression_total else 1.0,
        final_verdict_accuracy=correct_final_verdicts / regression_total if regression_total else 1.0,
        error_classification_accuracy=correct_error_classifications / regression_total if regression_total else 1.0,
    )


def main() -> None:
    """输出 JSON 格式的 Guardian Phase 1/2 评测结果."""
    result = evaluate_guardian_cases()
    Console().print_json(json=result.model_dump_json())


if __name__ == "__main__":
    main()
