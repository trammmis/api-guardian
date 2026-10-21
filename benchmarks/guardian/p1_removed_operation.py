"""P1 实验：已证明 operation removal 不触发全量回退。.

本模块只属于 Benchmark，不会被生产 Guardian Graph 导入。生产基线仍由
``app.services.guardian.scope.select_test_scope`` 提供。
"""

from app.services.guardian.models import ContractChange, OperationRef, ScopeSelection

PROVEN_REMOVAL_CHANGE_IDS = frozenset(
    {
        "api-path-removed-without-deprecation",
        "api-removed-without-deprecation",
    }
)


def select_test_scope_p1(
    changes: tuple[ContractChange, ...],
    base_operations: tuple[OperationRef, ...],
    head_operations: tuple[OperationRef, ...],
) -> ScopeSelection:
    """逐 change 解析范围，并仅忽略可证明 removal 的执行范围贡献。."""
    if not changes:
        return ScopeSelection()

    selected_keys: set[str] = set()
    unresolved_change_ids: list[str] = []
    for change in changes:
        matches = _find_head_matches(change, head_operations)
        if matches:
            selected_keys.update(operation.key for operation in matches)
            continue
        if is_proven_removed_operation(change, base_operations, head_operations):
            continue
        unresolved_change_ids.append(change.change_id)

    if unresolved_change_ids:
        return ScopeSelection(
            operations=head_operations,
            fallback_to_all=True,
            unresolved_change_ids=tuple(unresolved_change_ids),
        )
    return ScopeSelection(
        operations=tuple(operation for operation in head_operations if operation.key in selected_keys),
    )


def is_proven_removed_operation(
    change: ContractChange,
    base_operations: tuple[OperationRef, ...],
    head_operations: tuple[OperationRef, ...],
) -> bool:
    """根据冻结的结构事实判断一个 change 是否为可证明的 operation removal。."""
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
        head_operation_id_matches = tuple(
            operation for operation in head_operations if operation.operation_id == change.operation_id
        )
        if len(head_operation_id_matches) == 1:
            return False
    return True


def _find_head_matches(
    change: ContractChange,
    head_operations: tuple[OperationRef, ...],
) -> tuple[OperationRef, ...]:
    """保持生产基线的直接匹配优先级：method/path，其次唯一 operationId。."""
    if change.method and change.path:
        direct = tuple(
            operation
            for operation in head_operations
            if operation.method == change.method.upper() and operation.path == change.path
        )
        if direct:
            return direct
    if change.operation_id:
        by_id = tuple(operation for operation in head_operations if operation.operation_id == change.operation_id)
        if len(by_id) == 1:
            return by_id
    return ()
