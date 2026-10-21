"""Recall 优先的 scope selection 测试."""

from app.services.guardian.models import ChangeSeverity, ContractChange, OperationRef
from app.services.guardian.scope import select_test_scope

OPERATIONS = (
    OperationRef(method="GET", path="/items", operation_id="listItems"),
    OperationRef(method="POST", path="/items", operation_id="createItem"),
    OperationRef(method="GET", path="/health", operation_id="health"),
)


def test_scope_selects_exact_operation() -> None:
    """method/path 可精确归属时只选对应 operation."""
    change = ContractChange(
        change_id="response-changed",
        summary="response changed",
        severity=ChangeSeverity.ERROR,
        method="GET",
        path="/items",
    )

    scope = select_test_scope((change,), OPERATIONS)

    assert [operation.key for operation in scope.operations] == ["GET /items"]
    assert scope.fallback_to_all is False


def test_scope_uses_unique_operation_id_when_path_is_missing() -> None:
    """唯一 operationId 仍可作为精确归属依据."""
    change = ContractChange(
        change_id="operation-changed",
        summary="operation changed",
        severity=ChangeSeverity.WARNING,
        operation_id="createItem",
    )

    scope = select_test_scope((change,), OPERATIONS)

    assert [operation.key for operation in scope.operations] == ["POST /items"]
    assert scope.fallback_to_all is False


def test_unresolved_change_conservatively_selects_all_operations() -> None:
    """组件变更无法精确归属时必须 Recall 优先选择全部."""
    change = ContractChange(
        change_id="component-schema-changed",
        summary="component changed",
        severity=ChangeSeverity.ERROR,
        section="components",
    )

    scope = select_test_scope((change,), OPERATIONS)

    assert scope.operations == OPERATIONS
    assert scope.fallback_to_all is True
    assert scope.unresolved_change_ids == ("component-schema-changed",)


def test_any_unresolved_change_forces_global_fallback() -> None:
    """混合变更中只要一个无法归属，就不能缩小测试范围."""
    exact = ContractChange(
        change_id="exact",
        summary="exact",
        severity=ChangeSeverity.ERROR,
        method="GET",
        path="/items",
    )
    unresolved = ContractChange(
        change_id="unresolved",
        summary="unresolved",
        severity=ChangeSeverity.WARNING,
    )

    scope = select_test_scope((exact, unresolved), OPERATIONS)

    assert scope.operations == OPERATIONS
    assert scope.fallback_to_all is True


def test_no_change_selects_empty_scope() -> None:
    """无 contract change 时不选择 operation."""
    assert select_test_scope((), OPERATIONS).operations == ()


def _removal(
    *,
    method: str = "GET",
    path: str = "/legacy",
    operation_id: str | None = "legacyOperation",
    change_id: str = "api-path-removed-without-deprecation",
) -> ContractChange:
    """构造符合 oasdiff 1.28.0 冻结契约的 removal change."""
    return ContractChange(
        change_id=change_id,
        summary="operation removed",
        severity=ChangeSeverity.ERROR,
        method=method,
        path=path,
        operation_id=operation_id,
        section="paths",
    )


def test_exact_method_path_removal_selects_empty_scope() -> None:
    """Base 存在且 Head 不存在的明确 removal 不应 fallback."""
    base = (*OPERATIONS, OperationRef(method="GET", path="/legacy", operation_id="legacyOperation"))

    scope = select_test_scope((_removal(),), OPERATIONS, base_operations=base)

    assert scope.operations == ()
    assert scope.fallback_to_all is False


def test_path_completely_removed_selects_empty_scope() -> None:
    """整个参数化 path 被移除时执行范围为空."""
    base = (*OPERATIONS, OperationRef(method="DELETE", path="/legacy/{id}", operation_id="deleteLegacy"))
    change = _removal(method="DELETE", path="/legacy/{id}", operation_id="deleteLegacy")

    scope = select_test_scope((change,), OPERATIONS, base_operations=base)

    assert scope.operations == ()
    assert scope.fallback_to_all is False


def test_method_removed_while_path_remains_selects_empty_scope() -> None:
    """Path 仍存在但指定 method 消失时只把该 method 视为 removal."""
    base = (
        *OPERATIONS,
        OperationRef(method="GET", path="/resource", operation_id="getResource"),
        OperationRef(method="POST", path="/resource", operation_id="createResource"),
    )
    head = (*OPERATIONS, OperationRef(method="POST", path="/resource", operation_id="createResource"))
    change = _removal(
        path="/resource",
        operation_id="getResource",
        change_id="api-removed-without-deprecation",
    )

    scope = select_test_scope((change,), head, base_operations=base)

    assert scope.operations == ()
    assert scope.fallback_to_all is False


def test_multiple_independent_removals_select_empty_scope() -> None:
    """多个独立、可证明的 removal 都只贡献空执行范围."""
    base = (
        *OPERATIONS,
        OperationRef(method="GET", path="/legacy", operation_id="getLegacy"),
        OperationRef(method="DELETE", path="/legacy", operation_id="deleteLegacy"),
    )
    changes = (
        _removal(operation_id="getLegacy", change_id="api-removed-without-deprecation"),
        _removal(method="DELETE", operation_id="deleteLegacy", change_id="api-removed-without-deprecation"),
    )

    scope = select_test_scope(changes, OPERATIONS, base_operations=base)

    assert scope.operations == ()
    assert scope.fallback_to_all is False


def test_removal_plus_mapped_breaking_change_keeps_mapped_operation() -> None:
    """Mixed scan 只忽略 removal，不能丢失另一个可执行 breaking operation."""
    base = (*OPERATIONS, OperationRef(method="GET", path="/legacy", operation_id="legacyOperation"))
    mapped = ContractChange(
        change_id="response-changed",
        summary="response changed",
        severity=ChangeSeverity.ERROR,
        method="GET",
        path="/items",
    )

    scope = select_test_scope((_removal(), mapped), OPERATIONS, base_operations=base)

    assert [operation.key for operation in scope.operations] == ["GET /items"]
    assert scope.fallback_to_all is False


def test_removal_plus_unresolved_breaking_change_still_falls_back() -> None:
    """Removal 不得屏蔽另一个无法映射的 breaking change."""
    base = (*OPERATIONS, OperationRef(method="GET", path="/legacy", operation_id="legacyOperation"))
    unresolved = ContractChange(
        change_id="unknown-breaking",
        summary="unknown breaking change",
        severity=ChangeSeverity.ERROR,
    )

    scope = select_test_scope((_removal(), unresolved), OPERATIONS, base_operations=base)

    assert scope.operations == OPERATIONS
    assert scope.fallback_to_all is True
    assert scope.unresolved_change_ids == ("unknown-breaking",)


def test_removal_plus_unresolved_component_still_falls_back() -> None:
    """无法映射的 component change 继续遵守 Recall 优先 fallback."""
    base = (*OPERATIONS, OperationRef(method="GET", path="/legacy", operation_id="legacyOperation"))
    component = ContractChange(
        change_id="component-schema-changed",
        summary="component changed",
        severity=ChangeSeverity.ERROR,
        section="components",
    )

    scope = select_test_scope((_removal(), component), OPERATIONS, base_operations=base)

    assert scope.operations == OPERATIONS
    assert scope.fallback_to_all is True
    assert scope.unresolved_change_ids == ("component-schema-changed",)


def test_removal_with_unique_operation_id_counterpart_maps_head_operation() -> None:
    """Head 中唯一 operationId counterpart 优先于 removal 空范围判定."""
    base = (OperationRef(method="GET", path="/legacy", operation_id="stableOperation"),)
    head = (OperationRef(method="GET", path="/replacement", operation_id="stableOperation"),)

    scope = select_test_scope((_removal(operation_id="stableOperation"),), head, base_operations=base)

    assert [operation.key for operation in scope.operations] == ["GET /replacement"]
    assert scope.fallback_to_all is False


def test_non_removal_mapping_failure_still_falls_back_with_base_operations() -> None:
    """提供 Base operations 不能改变 non-removal 的保守 fallback."""
    change = ContractChange(
        change_id="unmapped-rule",
        summary="unmapped change",
        severity=ChangeSeverity.ERROR,
        method="GET",
        path="/missing",
        section="paths",
    )

    scope = select_test_scope((change,), OPERATIONS, base_operations=OPERATIONS)

    assert scope.operations == OPERATIONS
    assert scope.fallback_to_all is True


def test_compatible_endpoint_addition_has_no_breaking_scope() -> None:
    """兼容 endpoint addition 不产生 breaking change，因此 scope 为空."""
    head = (*OPERATIONS, OperationRef(method="GET", path="/new", operation_id="getNew"))

    scope = select_test_scope((), head, base_operations=OPERATIONS)

    assert scope.operations == ()
    assert scope.fallback_to_all is False


def test_no_change_with_base_operations_has_empty_scope() -> None:
    """Base/Head 相同时无 change，scope 仍为空."""
    scope = select_test_scope((), OPERATIONS, base_operations=OPERATIONS)

    assert scope.operations == ()
    assert scope.fallback_to_all is False


def test_normal_exact_mapping_unchanged_when_base_operations_are_present() -> None:
    """普通 operation-local change 保持既有精确映射行为."""
    change = ContractChange(
        change_id="response-changed",
        summary="response changed",
        severity=ChangeSeverity.ERROR,
        method="POST",
        path="/items",
    )

    scope = select_test_scope((change,), OPERATIONS, base_operations=OPERATIONS)

    assert [operation.key for operation in scope.operations] == ["POST /items"]
    assert scope.fallback_to_all is False
