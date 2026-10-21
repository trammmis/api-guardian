"""独立于 chatbot graph 的 GuardianState."""

from collections.abc import Mapping
from typing import Any, TypedDict, cast

from app.services.guardian.models import (
    ContractChange,
    ContractChangeCandidate,
    FailureDiagnosis,
    GuardianError,
    GuardianPolicy,
    GuardianVerdict,
    OperationRef,
    RegressionResult,
    RegressionRunSummary,
    RiskExplanation,
    ScopeSelection,
    ToolRunSummary,
)


class GuardianState(TypedDict, total=False):
    """API Guardian 确定性回归 graph 的状态.

    状态只保存领域 DTO 和脱敏摘要，不保存 raw 工具输出、request/response 或 Secret。
    """

    scan_id: str
    base_spec_path: str
    head_spec_path: str
    target_base_url: str
    policy: GuardianPolicy
    base_spec_sha256: str
    head_spec_sha256: str
    base_operations: tuple[OperationRef, ...]
    head_operations: tuple[OperationRef, ...]
    contract_candidates: tuple[ContractChangeCandidate, ...]
    contract_changes: tuple[ContractChange, ...]
    selected_scope: ScopeSelection
    contract_tool: ToolRunSummary
    contract_verdict: GuardianVerdict
    regression_results: tuple[RegressionResult, ...]
    regression_tool: RegressionRunSummary
    regression_verdict: GuardianVerdict
    final_verdict: GuardianVerdict
    analysis_enabled: bool
    risk_explanations: tuple[RiskExplanation, ...]
    failure_diagnoses: tuple[FailureDiagnosis, ...]
    analysis_degraded: bool
    analysis_warnings: tuple[str, ...]
    analysis_truncated: bool
    artifact_bytes_consumed: int
    errors: tuple[GuardianError, ...]
    completed_nodes: tuple[str, ...]


def rehydrate_guardian_state(value: Mapping[str, Any]) -> GuardianState:
    """在 checkpoint 边界把 JSON 兼容值恢复成稳定领域 DTO."""
    state = cast(GuardianState, dict(value))
    _rehydrate_model(state, "policy", GuardianPolicy)
    _rehydrate_tuple(state, "base_operations", OperationRef)
    _rehydrate_tuple(state, "head_operations", OperationRef)
    _rehydrate_tuple(state, "contract_candidates", ContractChangeCandidate)
    _rehydrate_tuple(state, "contract_changes", ContractChange)
    _rehydrate_model(state, "selected_scope", ScopeSelection)
    _rehydrate_model(state, "contract_tool", ToolRunSummary)
    _rehydrate_tuple(state, "regression_results", RegressionResult)
    _rehydrate_model(state, "regression_tool", RegressionRunSummary)
    _rehydrate_tuple(state, "risk_explanations", RiskExplanation)
    _rehydrate_tuple(state, "failure_diagnoses", FailureDiagnosis)
    _rehydrate_tuple(state, "errors", GuardianError)
    for field in ("contract_verdict", "regression_verdict", "final_verdict"):
        verdict = state.get(field)  # type: ignore[literal-required]
        if verdict is not None and not isinstance(verdict, GuardianVerdict):
            state[field] = GuardianVerdict(verdict)  # type: ignore[literal-required]
    completed_nodes = state.get("completed_nodes")
    if completed_nodes is not None and not isinstance(completed_nodes, tuple):
        state["completed_nodes"] = tuple(completed_nodes)
    analysis_warnings = state.get("analysis_warnings")
    if analysis_warnings is not None and not isinstance(analysis_warnings, tuple):
        state["analysis_warnings"] = tuple(analysis_warnings)
    return state


def _rehydrate_model(state: GuardianState, field: str, model_type: type[Any]) -> None:
    """恢复单个 Pydantic DTO；不存在的可选字段保持缺失."""
    value = state.get(field)  # type: ignore[literal-required]
    if value is not None and not isinstance(value, model_type):
        state[field] = model_type.model_validate(value)  # type: ignore[literal-required, attr-defined]


def _rehydrate_tuple(state: GuardianState, field: str, model_type: type[Any]) -> None:
    """恢复 DTO 序列并统一成 tuple."""
    values = state.get(field)  # type: ignore[literal-required]
    if values is not None:
        state[field] = tuple(  # type: ignore[literal-required]
            item if isinstance(item, model_type) else model_type.model_validate(item) for item in values
        )
