"""Guardian graph node 的独立调用测试."""

import asyncio
from pathlib import Path

from app.services.guardian.models import GuardianPolicy, GuardianVerdict, RegressionOutcome
from app.services.guardian.nodes import GuardianNodes
from app.services.guardian.oasdiff import OasdiffAdapter
from app.services.guardian.subprocess import ProcessResult
from app.services.guardian.state import GuardianState
from tests.guardian.fakes import OutcomeSchemathesisAdapter, StaticProcessRunner

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_all_nodes_can_be_invoked_independently() -> None:
    """按状态更新逐个调用节点，不依赖 CompiledStateGraph."""
    payload = (FIXTURE_DIR / "oasdiff_breaking.json").read_bytes()
    nodes = GuardianNodes(
        GuardianPolicy(allowed_target_hosts=("127.0.0.1",), allow_private_targets=True),
        OasdiffAdapter(runner=StaticProcessRunner(ProcessResult(exit_code=1, stdout=payload, stderr=b""))),
        OutcomeSchemathesisAdapter(RegressionOutcome.PASSED),
    )
    state: GuardianState = {
        "base_spec_path": str(FIXTURE_DIR / "baseline_openapi.yaml"),
        "head_spec_path": str(FIXTURE_DIR / "head_breaking_openapi.yaml"),
        "target_base_url": "http://127.0.0.1:8000",
    }

    for node in (
        nodes.prepare_scan,
        nodes.contract_diff,
        nodes.normalize_changes,
        nodes.select_test_scope,
        nodes.build_contract_verdict,
        nodes.run_regression,
        nodes.parse_test_results,
        nodes.build_final_verdict,
        nodes.analyze_risks,
        nodes.diagnose_failures,
        nodes.build_enhanced_report,
    ):
        state.update(asyncio.run(node(state)))

    assert state.get("contract_verdict") == GuardianVerdict.FAIL
    assert state.get("regression_verdict") == GuardianVerdict.PASS
    assert state.get("final_verdict") == GuardianVerdict.FAIL
    completed_nodes = state.get("completed_nodes")
    assert completed_nodes
    assert completed_nodes[-1] == "build_enhanced_report"
