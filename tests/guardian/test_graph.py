"""Contract-only Guardian graph 的 PASS/FAIL/ERROR fixture 测试."""

import asyncio
from pathlib import Path

from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianPolicy, GuardianVerdict, RegressionOutcome
from app.services.guardian.oasdiff import OasdiffAdapter
from app.services.guardian.subprocess import ProcessResult
from tests.guardian.fakes import OutcomeSchemathesisAdapter, StaticProcessRunner

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _policy() -> GuardianPolicy:
    """返回只允许本地 fixture 的 development-only 策略."""
    return GuardianPolicy(
        allowed_target_hosts=("127.0.0.1",),
        allow_private_targets=True,
        max_openapi_bytes=5 * 1024 * 1024,
        max_artifact_bytes=50 * 1024 * 1024,
        max_scan_artifact_bytes=100 * 1024 * 1024,
    )


def _input_state() -> dict[str, str]:
    """返回 graph 的最小安全输入."""
    return {
        "scan_id": "phase1-fixture",
        "base_spec_path": str(FIXTURE_DIR / "baseline_openapi.yaml"),
        "head_spec_path": str(FIXTURE_DIR / "head_breaking_openapi.yaml"),
        "target_base_url": "http://127.0.0.1:8000",
    }


def _run_with_process_result(
    result: ProcessResult,
    regression_outcome: RegressionOutcome = RegressionOutcome.PASSED,
) -> tuple[dict, OutcomeSchemathesisAdapter]:
    """使用 fake runner 离线执行完整 Guardian graph."""
    adapter = OasdiffAdapter(runner=StaticProcessRunner(result))
    regression = OutcomeSchemathesisAdapter(regression_outcome)
    graph = create_guardian_graph(policy=_policy(), oasdiff=adapter, schemathesis=regression)
    return asyncio.run(graph.ainvoke(_input_state())), regression


def test_contract_graph_pass_fixture() -> None:
    """Oasdiff 无变更时应得到 PASS."""
    payload = (FIXTURE_DIR / "oasdiff_no_changes.json").read_bytes()

    state, regression = _run_with_process_result(ProcessResult(exit_code=0, stdout=payload, stderr=b""))

    assert state["contract_verdict"] == GuardianVerdict.PASS
    assert state["regression_verdict"] == GuardianVerdict.PASS
    assert state["final_verdict"] == GuardianVerdict.PASS
    assert state["contract_changes"] == ()
    assert state["selected_scope"].operations == ()
    assert regression.calls == [()]
    assert state["completed_nodes"] == (
        "prepare_scan",
        "contract_diff",
        "normalize_changes",
        "select_test_scope",
        "build_contract_verdict",
        "run_regression",
        "parse_test_results",
        "build_final_verdict",
        "analyze_risks",
        "build_enhanced_report",
    )


def test_contract_graph_fail_fixture() -> None:
    """真实 breaking fixture 应得到 FAIL 和精确 scope."""
    payload = (FIXTURE_DIR / "oasdiff_breaking.json").read_bytes()

    state, regression = _run_with_process_result(ProcessResult(exit_code=1, stdout=payload, stderr=b""))

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.PASS
    assert state["final_verdict"] == GuardianVerdict.FAIL
    assert state["contract_changes"][0].severity.value == "error"
    assert [operation.key for operation in state["selected_scope"].operations] == ["GET /items/{item_id}"]
    assert [[operation.key for operation in call] for call in regression.calls] == [["GET /items/{item_id}"]]
    assert "raw-oasdiff-source" not in repr(state)


def test_removed_operation_keeps_contract_fail_with_empty_regression_scope() -> None:
    """纯 operation removal 不 fallback，且 final verdict 仍由 Contract FAIL 保持 FAIL."""
    payload = (FIXTURE_DIR / "oasdiff_operation_removed.json").read_bytes()
    adapter = OasdiffAdapter(runner=StaticProcessRunner(ProcessResult(exit_code=1, stdout=payload, stderr=b"")))
    regression = OutcomeSchemathesisAdapter(RegressionOutcome.PASSED)
    graph = create_guardian_graph(policy=_policy(), oasdiff=adapter, schemathesis=regression)
    input_state = {
        "scan_id": "removed-operation-fixture",
        "base_spec_path": str(FIXTURE_DIR / "base_removed_operation_openapi.yaml"),
        "head_spec_path": str(FIXTURE_DIR / "head_removed_operation_openapi.yaml"),
        "target_base_url": "http://127.0.0.1:8000",
    }

    state = asyncio.run(graph.ainvoke(input_state))

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["selected_scope"].operations == ()
    assert state["selected_scope"].fallback_to_all is False
    assert regression.calls == [()]
    assert state["regression_verdict"] == GuardianVerdict.PASS
    assert state["final_verdict"] == GuardianVerdict.FAIL


def test_contract_graph_error_fixture() -> None:
    """Oasdiff 工具错误必须与 breaking FAIL 严格区分."""
    marker = b"Authorization: Bearer guardian-error-secret"

    state, regression = _run_with_process_result(ProcessResult(exit_code=102, stdout=b"", stderr=marker))

    assert state["contract_verdict"] == GuardianVerdict.ERROR
    assert state["final_verdict"] == GuardianVerdict.ERROR
    assert state["errors"][0].code.value == "oasdiff_execution_failed"
    assert state["completed_nodes"] == (
        "prepare_scan",
        "contract_diff",
        "build_contract_verdict",
        "build_final_verdict",
        "analyze_risks",
        "build_enhanced_report",
    )
    assert regression.calls == []
    assert "guardian-error-secret" not in repr(state)
    assert "stdout" not in state
    assert "stderr" not in state


def test_prepare_scan_security_error_short_circuits_tool() -> None:
    """SSRF 校验失败时不应执行 oasdiff."""
    runner = StaticProcessRunner(ProcessResult(exit_code=0, stdout=b"[]", stderr=b""))
    regression = OutcomeSchemathesisAdapter(RegressionOutcome.PASSED)
    graph = create_guardian_graph(
        policy=_policy(),
        oasdiff=OasdiffAdapter(runner=runner),
        schemathesis=regression,
    )
    input_state = _input_state()
    input_state["target_base_url"] = "http://10.0.0.1:8000"

    state = asyncio.run(graph.ainvoke(input_state))

    assert state["contract_verdict"] == GuardianVerdict.ERROR
    assert state["final_verdict"] == GuardianVerdict.ERROR
    assert state["errors"][0].code.value == "target_not_allowed"
    assert runner.commands == []
    assert state["completed_nodes"] == (
        "prepare_scan",
        "build_contract_verdict",
        "build_final_verdict",
        "analyze_risks",
        "build_enhanced_report",
    )
    assert regression.calls == []


def test_prepare_scan_scrubs_secret_bearing_target_from_error_state() -> None:
    """失败状态不得保留 target URL 中的 Secret."""
    marker = "guardian-url-state-secret"
    runner = StaticProcessRunner(ProcessResult(exit_code=0, stdout=b"[]", stderr=b""))
    regression = OutcomeSchemathesisAdapter(RegressionOutcome.PASSED)
    graph = create_guardian_graph(
        policy=_policy(),
        oasdiff=OasdiffAdapter(runner=runner),
        schemathesis=regression,
    )
    input_state = _input_state()
    input_state["target_base_url"] = f"http://user:{marker}@127.0.0.1:8000"

    state = asyncio.run(graph.ainvoke(input_state))

    assert state["contract_verdict"] == GuardianVerdict.ERROR
    assert state["target_base_url"] == ""
    assert marker not in repr(state)
    assert runner.commands == []
    assert regression.calls == []
