"""Phase 2 完整 Graph 的确定性 regression/final verdict 测试."""

import asyncio
from pathlib import Path

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianPolicy, GuardianVerdict
from app.services.guardian.oasdiff import OasdiffAdapter
from app.services.guardian.schemathesis import SchemathesisAdapter
from app.services.guardian.subprocess import ProcessResult
from tests.guardian.fakes import RaisingProcessRunner, StaticProcessRunner

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _policy(*, allow_private_targets: bool = True) -> GuardianPolicy:
    """返回本地 fixture 专用的显式开发策略."""
    return GuardianPolicy(
        allowed_target_hosts=("127.0.0.1",),
        allow_private_targets=allow_private_targets,
    )


def _input_state() -> dict[str, str]:
    """返回主 Demo 使用的最小 Graph 输入."""
    return {
        "scan_id": "phase2-offline",
        "base_spec_path": str(FIXTURE_DIR / "baseline_openapi.yaml"),
        "head_spec_path": str(FIXTURE_DIR / "head_breaking_openapi.yaml"),
        "target_base_url": "http://127.0.0.1:8000",
    }


def _run_graph(oasdiff_payload: bytes, oasdiff_exit: int, schemathesis: SchemathesisAdapter) -> dict:
    """使用真实 Adapter parser 与 fake subprocess 执行完整 Graph."""
    oasdiff = OasdiffAdapter(
        runner=StaticProcessRunner(ProcessResult(exit_code=oasdiff_exit, stdout=oasdiff_payload, stderr=b""))
    )
    graph = create_guardian_graph(policy=_policy(), oasdiff=oasdiff, schemathesis=schemathesis)
    return asyncio.run(graph.ainvoke(_input_state()))


def test_compatible_change_and_conforming_api_is_final_pass() -> None:
    """Warning contract + regression PASS 应得到 final PASS."""
    runner = StaticProcessRunner(
        ProcessResult(exit_code=0, stdout=b"", stderr=b""),
        report_payload=(FIXTURE_DIR / "schemathesis_pass.ndjson").read_bytes(),
    )

    state = _run_graph((FIXTURE_DIR / "oasdiff_warning.json").read_bytes(), 0, SchemathesisAdapter(runner=runner))

    assert state["contract_verdict"] == GuardianVerdict.PASS
    assert state["regression_verdict"] == GuardianVerdict.PASS
    assert state["final_verdict"] == GuardianVerdict.PASS
    assert len(runner.commands) == 1


def test_contract_and_runtime_regression_are_both_final_fail() -> None:
    """主 Demo 的 breaking change 与 schema failure 必须同时保留."""
    runner = StaticProcessRunner(
        ProcessResult(exit_code=1, stdout=b"terminal-must-be-dropped", stderr=b""),
        report_payload=(FIXTURE_DIR / "schemathesis_fail.ndjson").read_bytes(),
    )

    state = _run_graph((FIXTURE_DIR / "oasdiff_breaking.json").read_bytes(), 1, SchemathesisAdapter(runner=runner))

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.FAIL
    assert state["final_verdict"] == GuardianVerdict.FAIL
    assert state["regression_results"][0].failures[0].status_code == 200
    assert "terminal-must-be-dropped" not in repr(state)
    command = runner.commands[0]
    assert command[command.index("--include-operation-id") + 1] == "getItem"


def test_valid_network_error_report_is_final_error() -> None:
    """Exit 1 的网络 ERROR 不能被错误解释为真实 regression FAIL."""
    runner = StaticProcessRunner(
        ProcessResult(exit_code=1, stdout=b"", stderr=b"network-secret"),
        report_payload=(FIXTURE_DIR / "schemathesis_error.ndjson").read_bytes(),
    )

    state = _run_graph((FIXTURE_DIR / "oasdiff_breaking.json").read_bytes(), 1, SchemathesisAdapter(runner=runner))

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.ERROR
    assert state["final_verdict"] == GuardianVerdict.ERROR
    assert "network-secret" not in repr(state)


def test_malformed_ndjson_is_final_error() -> None:
    """损坏 report 必须短路为 ERROR，不能按 exit code 猜测."""
    runner = StaticProcessRunner(
        ProcessResult(exit_code=1, stdout=b"", stderr=b""),
        report_payload=b"not-ndjson",
    )

    state = _run_graph((FIXTURE_DIR / "oasdiff_breaking.json").read_bytes(), 1, SchemathesisAdapter(runner=runner))

    assert state["final_verdict"] == GuardianVerdict.ERROR
    assert state["errors"][0].code == GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT
    completed = state["completed_nodes"]
    assert completed[completed.index("build_final_verdict") - 1] == "run_regression"
    assert completed[-2:] == ("analyze_risks", "build_enhanced_report")


def test_missing_schemathesis_cli_is_final_error() -> None:
    """工具无法启动时返回安全 ERROR，且不产生 raw process state."""
    runner = RaisingProcessRunner(GuardianException(GuardianErrorCode.SUBPROCESS_START_FAILED, "外部工具启动失败"))

    state = _run_graph((FIXTURE_DIR / "oasdiff_breaking.json").read_bytes(), 1, SchemathesisAdapter(runner=runner))

    assert state["final_verdict"] == GuardianVerdict.ERROR
    assert state["errors"][0].code == GuardianErrorCode.SUBPROCESS_START_FAILED
    assert "stdout" not in state
    assert "stderr" not in state
    assert "regression_results" not in state
