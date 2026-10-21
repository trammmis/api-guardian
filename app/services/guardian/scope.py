"""Recall 优先的测试范围选择与确定性 contract verdict."""

from typing import Final

from app.services.guardian.models import (
    ChangeSeverity,
    ContractChange,
    GuardianVerdict,
    OperationRef,
    ScopeSelection,
)

PROVEN_REMOVAL_CHANGE_IDS: Final = frozenset(
    {
        "api-path-removed-without-deprecation",
        "api-removed-without-deprecation",
    }
)


def select_test_scope(
    changes: tuple[ContractChange, ...],
    all_operations: tuple[OperationRef, ...],
    *,
    base_operations: tuple[OperationRef, ...] = (),
) -> ScopeSelection:
    """逐 change 解析；已证明 removal 贡献空范围，其他未解析变更回退全部."""
    if not changes:
        return ScopeSelection()

    selected_keys: set[str] = set()
    unresolved_change_ids: list[str] = []
    for change in changes:
        matches = _find_exact_matches(change, all_operations)
        if matches:
            selected_keys.update(operation.key for operation in matches)
            continue
        if _is_proven_removed_operation(change, base_operations, all_operations):
            continue
        unresolved_change_ids.append(change.change_id)

    if unresolved_change_ids:
        return ScopeSelection(
            operations=all_operations,
            fallback_to_all=True,
            unresolved_change_ids=tuple(unresolved_change_ids),
        )
    return ScopeSelection(
        operations=tuple(operation for operation in all_operations if operation.key in selected_keys),
    )


def _find_exact_matches(
    change: ContractChange,
    all_operations: tuple[OperationRef, ...],
) -> tuple[OperationRef, ...]:
    """只接受 method/path 或唯一 operationId 的精确匹配."""
    if change.method and change.path:
        direct = tuple(
            operation
            for operation in all_operations
            if operation.method == change.method.upper() and operation.path == change.path
        )
        if direct:
            return direct
    if change.operation_id:
        by_id = tuple(operation for operation in all_operations if operation.operation_id == change.operation_id)
        if len(by_id) == 1:
            return by_id
    return ()


def _is_proven_removed_operation(
    change: ContractChange,
    base_operations: tuple[OperationRef, ...],
    head_operations: tuple[OperationRef, ...],
) -> bool:
    """只根据 oasdiff 1.28.0 冻结 rule ID 与 Base/Head 结构证明 removal."""
    if (
        change.change_id not in PROVEN_REMOVAL_CHANGE_IDS
        or change.section != "paths"
        or not change.method
        or not change.path
    ):
        return False

    method = change.method.upper()
    base_contains = any(operation.method == method and operation.path == change.path for operation in base_operations)
    head_contains = any(operation.method == method and operation.path == change.path for operation in head_operations)
    if not base_contains or head_contains:
        return False

    if change.operation_id:
        by_id = tuple(operation for operation in head_operations if operation.operation_id == change.operation_id)
        if len(by_id) == 1:
            return False
    return True


def determine_contract_verdict(
    changes: tuple[ContractChange, ...],
    *,
    has_error: bool,
    fail_severities: tuple[ChangeSeverity, ...] = (ChangeSeverity.ERROR,),
) -> GuardianVerdict:
    """完全基于工具结果和策略生成 contract verdict."""
    if has_error:
        return GuardianVerdict.ERROR
    if any(change.severity in fail_severities for change in changes):
        return GuardianVerdict.FAIL
    return GuardianVerdict.PASS
