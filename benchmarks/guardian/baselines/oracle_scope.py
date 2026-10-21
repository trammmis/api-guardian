"""Baseline D：由 Dataset Ground Truth 直接给出理想回归范围。."""

from app.services.guardian.models import OperationRef
from app.services.guardian.schemathesis import SchemathesisPort
from benchmarks.guardian.baselines.full_schemathesis import run_full_schemathesis
from benchmarks.guardian.models import SystemResult


async def run_oracle_scope(
    adapter: SchemathesisPort,
    head_spec_path: str,
    target_base_url: str,
    all_operations: tuple[OperationRef, ...],
    ground_truth_operation_keys: tuple[str, ...],
    *,
    max_scan_artifact_bytes: int,
) -> SystemResult:
    """精确映射人工 Ground Truth；缺少 operation 时拒绝猜测。."""
    by_key = {operation.key: operation for operation in all_operations}
    missing = tuple(key for key in ground_truth_operation_keys if key not in by_key)
    if missing:
        raise ValueError(f"oracle operations missing from Head OpenAPI: {missing}")
    operations = tuple(by_key[key] for key in ground_truth_operation_keys)
    result = await run_full_schemathesis(
        adapter,
        head_spec_path,
        target_base_url,
        operations,
        max_scan_artifact_bytes=max_scan_artifact_bytes,
    )
    return result.model_copy(update={"total_operations": len(all_operations)})
