"""Baseline C：相同 oasdiff 加 Full Schemathesis，用于隔离 Scope 收益。."""

from app.services.guardian.models import GuardianVerdict, OperationRef
from app.services.guardian.oasdiff import OasdiffPort
from app.services.guardian.schemathesis import SchemathesisPort, determine_final_verdict
from benchmarks.guardian.baselines.full_schemathesis import run_full_schemathesis
from benchmarks.guardian.baselines.oasdiff_only import run_oasdiff_only
from benchmarks.guardian.models import SystemResult


async def run_full_pipeline(
    oasdiff: OasdiffPort,
    schemathesis: SchemathesisPort,
    base_spec_path: str,
    head_spec_path: str,
    target_base_url: str,
    operations: tuple[OperationRef, ...],
    *,
    max_scan_artifact_bytes: int,
) -> SystemResult:
    """保持检测流程相同，只把 Guardian selected scope 替换成全量。."""
    contract = await run_oasdiff_only(
        oasdiff,
        base_spec_path,
        head_spec_path,
        max_scan_artifact_bytes=max_scan_artifact_bytes,
        total_operations=len(operations),
    )
    regression = await run_full_schemathesis(
        schemathesis,
        head_spec_path,
        target_base_url,
        operations,
        max_scan_artifact_bytes=max_scan_artifact_bytes,
    )
    final = determine_final_verdict(
        contract.contract_verdict,
        regression.regression_verdict,
        has_error=False,
    )
    return SystemResult(
        contract_verdict=contract.contract_verdict,
        regression_verdict=regression.regression_verdict,
        final_verdict=final,
        selected_operations=regression.selected_operations,
        total_operations=len(operations),
        operations_tested=regression.operations_tested,
        bug_detected=regression.bug_detected,
    )


def full_pipeline_error(total_operations: int, error_code: str) -> SystemResult:
    """把基础设施异常记录为 ERROR，不伪装成 API bug。."""
    return SystemResult(
        contract_verdict=GuardianVerdict.ERROR,
        regression_verdict=GuardianVerdict.ERROR,
        final_verdict=GuardianVerdict.ERROR,
        total_operations=total_operations,
        operations_tested=0,
        bug_detected=False,
        error_codes=(error_code,),
    )
