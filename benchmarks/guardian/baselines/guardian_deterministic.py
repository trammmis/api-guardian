"""System E：真实 API Guardian graph，显式禁用 LLM。."""

from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.services.guardian.models import GuardianVerdict
from benchmarks.guardian.models import SystemResult


async def run_guardian_deterministic(
    graph: CompiledStateGraph,
    *,
    scan_id: str,
    base_spec_path: str,
    head_spec_path: str,
    target_base_url: str,
    total_operations: int,
) -> SystemResult:
    """调用冻结的 production graph，并只抽取稳定白名单结果。."""
    state: dict[str, Any] = await graph.ainvoke(
        {
            "scan_id": scan_id,
            "base_spec_path": base_spec_path,
            "head_spec_path": head_spec_path,
            "target_base_url": target_base_url,
        }
    )
    selection = state.get("selected_scope")
    selected_operations = tuple(operation.key for operation in selection.operations) if selection else ()
    errors = tuple(error.code.value for error in state.get("errors", ()))
    regression_tool = state.get("regression_tool")
    regression_verdict = state.get("regression_verdict")
    return SystemResult(
        contract_verdict=state.get("contract_verdict"),
        regression_verdict=regression_verdict,
        final_verdict=state.get("final_verdict"),
        selected_operations=selected_operations,
        total_operations=total_operations,
        operations_tested=regression_tool.total_operations if regression_tool else 0,
        bug_detected=regression_verdict == GuardianVerdict.FAIL,
        error_codes=errors,
    )
