"""本地 fixture API 与 Schemathesis 4.24.3 的真实集成测试."""

import asyncio
import shutil
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Thread

import pytest
import uvicorn
from fastapi import FastAPI

from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import GuardianPolicy, GuardianVerdict, OperationRef, RegressionOutcome
from app.services.guardian.oasdiff import OasdiffAdapter
from app.services.guardian.schemathesis import SchemathesisAdapter
from app.services.guardian.subprocess import ProcessResult
from tests.guardian.fixture_api import app, conforming_app
from tests.guardian.fakes import StaticProcessRunner

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _free_port() -> int:
    """为仅监听 loopback 的测试服务选择临时端口."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def _start_server(application: FastAPI) -> tuple[uvicorn.Server, Thread, str]:
    """在线程中启动本地 Uvicorn，不访问公网."""
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("local fixture API failed to start")
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def fixture_urls() -> Iterator[tuple[str, str]]:
    """同时提供 runtime regression 与 conforming 两个本地目标."""
    regression_server, regression_thread, regression_url = _start_server(app)
    conforming_server, conforming_thread, conforming_url = _start_server(conforming_app)
    try:
        yield regression_url, conforming_url
    finally:
        regression_server.should_exit = True
        conforming_server.should_exit = True
        regression_thread.join(timeout=10)
        conforming_thread.join(timeout=10)


def _adapter() -> SchemathesisAdapter:
    """使用 uv 环境中锁定的真实 Schemathesis CLI."""
    executable = shutil.which("st")
    if executable is None:
        raise RuntimeError("Schemathesis 4.24.3 CLI is missing from the test environment")
    return SchemathesisAdapter(executable=executable, timeout_seconds=30)


def _policy() -> GuardianPolicy:
    """仅对精确 loopback allowlist 开启 development-only 策略."""
    return GuardianPolicy(
        allowed_target_hosts=("127.0.0.1",),
        allow_private_targets=True,
        subprocess_timeout_seconds=30,
    )


def _graph_state(target_url: str) -> dict[str, str]:
    """构造 Phase 0 主 Demo Graph 输入."""
    return {
        "scan_id": "phase2-real-integration",
        "base_spec_path": str(FIXTURE_DIR / "baseline_openapi.yaml"),
        "head_spec_path": str(FIXTURE_DIR / "head_breaking_openapi.yaml"),
        "target_base_url": target_url,
    }


def _breaking_oasdiff() -> OasdiffAdapter:
    """隔离 oasdiff，仅让本测试聚焦真实 Schemathesis 进程."""
    payload = (FIXTURE_DIR / "oasdiff_breaking.json").read_bytes()
    return OasdiffAdapter(runner=StaticProcessRunner(ProcessResult(exit_code=1, stdout=payload, stderr=b"")))


@pytest.mark.slow
def test_real_schemathesis_passes_and_filters_one_of_three_operations(
    fixture_urls: tuple[str, str],
) -> None:
    """缺少 operationId 时用 method/path 只选一个接口，不触碰无关 operation."""
    regression_url, _ = fixture_urls
    operation = OperationRef(method="GET", path="/items/{item_id}")

    execution = asyncio.run(
        _adapter().run_regression(
            str(FIXTURE_DIR / "multi_operation_openapi.yaml"),
            regression_url,
            (operation,),
            100 * 1024 * 1024,
        )
    )

    assert execution.results[0].outcome == RegressionOutcome.PASSED
    assert execution.tool.total_operations == 1
    assert execution.tool.passed_operations == 1


@pytest.mark.slow
def test_real_breaking_contract_with_conforming_api_is_regression_pass(
    fixture_urls: tuple[str, str],
) -> None:
    """Contract FAIL 不短路；符合 Head 的目标仍得到 regression PASS."""
    _, conforming_url = fixture_urls
    graph = create_guardian_graph(policy=_policy(), oasdiff=_breaking_oasdiff(), schemathesis=_adapter())

    state = asyncio.run(graph.ainvoke(_graph_state(conforming_url)))

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.PASS
    assert state["final_verdict"] == GuardianVerdict.FAIL


@pytest.mark.slow
def test_real_phase0_main_demo_is_contract_and_runtime_fail(
    fixture_urls: tuple[str, str],
) -> None:
    """主 Demo 真实运行 Schemathesis，证明 string response 违反 integer Head schema."""
    regression_url, _ = fixture_urls
    graph = create_guardian_graph(policy=_policy(), oasdiff=_breaking_oasdiff(), schemathesis=_adapter())

    state = asyncio.run(graph.ainvoke(_graph_state(regression_url)))

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.FAIL
    assert state["final_verdict"] == GuardianVerdict.FAIL
    assert state["regression_results"][0].failures[0].category == "JsonSchemaError"
    assert state["regression_results"][0].failures[0].status_code == 200


@pytest.mark.slow
def test_real_unreachable_target_is_regression_error() -> None:
    """真实 exit code 1 网络错误必须归类为 ERROR 而不是 FAIL."""
    unreachable_port = _free_port()
    operation = OperationRef(method="GET", path="/items/{item_id}", operation_id="getItem")

    execution = asyncio.run(
        _adapter().run_regression(
            str(FIXTURE_DIR / "baseline_openapi.yaml"),
            f"http://127.0.0.1:{unreachable_port}",
            (operation,),
            100 * 1024 * 1024,
        )
    )

    assert execution.tool.exit_code == 1
    assert execution.results[0].outcome == RegressionOutcome.ERROR
