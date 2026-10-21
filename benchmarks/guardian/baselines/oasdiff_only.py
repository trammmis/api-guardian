"""Baseline A：仅执行与 Guardian 相同的 oasdiff Contract Detection。."""

from app.services.guardian.models import ChangeSeverity
from app.services.guardian.oasdiff import OasdiffPort, normalize_contract_changes
from app.services.guardian.scope import determine_contract_verdict
from benchmarks.guardian.models import SystemResult


async def run_oasdiff_only(
    adapter: OasdiffPort,
    base_spec_path: str,
    head_spec_path: str,
    *,
    max_scan_artifact_bytes: int,
    total_operations: int,
) -> SystemResult:
    """只回答 contract verdict，不执行 runtime regression。."""
    result = await adapter.compare(base_spec_path, head_spec_path, max_scan_artifact_bytes)
    changes = normalize_contract_changes(result.candidates)
    verdict = determine_contract_verdict(
        changes,
        has_error=False,
        fail_severities=(ChangeSeverity.ERROR,),
    )
    return SystemResult(
        contract_verdict=verdict,
        final_verdict=verdict,
        total_operations=total_operations,
        operations_tested=0,
        bug_detected=False,
    )
