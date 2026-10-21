"""Baseline B：对 Head OpenAPI 的全部 operations 执行相同 Schemathesis。."""

from app.services.guardian.models import GuardianVerdict, OperationRef
from app.services.guardian.schemathesis import SchemathesisPort, determine_regression_verdict
from benchmarks.guardian.models import SystemResult


async def run_full_schemathesis(
    adapter: SchemathesisPort,
    head_spec_path: str,
    target_base_url: str,
    operations: tuple[OperationRef, ...],
    *,
    max_scan_artifact_bytes: int,
) -> SystemResult:
    """用生产 Adapter 的固定参数测试全部 operation。."""
    execution = await adapter.run_regression(
        head_spec_path,
        target_base_url,
        operations,
        max_scan_artifact_bytes,
    )
    verdict = determine_regression_verdict(execution.results, operations)
    return SystemResult(
        regression_verdict=verdict,
        final_verdict=verdict,
        selected_operations=tuple(operation.key for operation in operations),
        total_operations=len(operations),
        operations_tested=execution.tool.total_operations,
        bug_detected=verdict == GuardianVerdict.FAIL,
    )
