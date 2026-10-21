"""Schemathesis Adapter、NDJSON parser 与判定规则测试."""

import asyncio
from pathlib import Path

import pytest

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.models import GuardianVerdict, OperationRef, RegressionOutcome
from app.services.guardian.schemathesis import (
    SchemathesisAdapter,
    build_schemathesis_command,
    determine_final_verdict,
    determine_regression_verdict,
    parse_schemathesis_ndjson,
)
from app.services.guardian.subprocess import ProcessResult
from tests.guardian.fakes import StaticProcessRunner

FIXTURE_DIR = Path(__file__).parent / "fixtures"
OPERATION = OperationRef(method="GET", path="/items/{item_id}", operation_id="getItem")


def _fixture(name: str) -> bytes:
    """读取不含真实 Secret 的离线 NDJSON fixture."""
    return (FIXTURE_DIR / name).read_bytes()


def test_command_uses_exact_operation_id_and_fixed_ci_options() -> None:
    """有 operationId 时只构造一个精确 filter，且不启用 fuzzing/stateful/database."""
    command = build_schemathesis_command(
        executable="st",
        head_spec_path="head.yaml",
        target_base_url="https://api.example.com",
        operation=OPERATION,
        report_path=Path("report.ndjson"),
        max_examples=1,
        request_timeout_seconds=2.0,
    )

    assert command[:2] == ("st", "run")
    assert ("--include-operation-id", "getItem") == command[5:7]
    assert "--generation-deterministic" in command
    assert command[command.index("--max-redirects") + 1] == "0"
    assert command[command.index("--checks") + 1] == "response_schema_conformance"
    assert command[command.index("--phases") + 1] == "examples,coverage"
    assert "--generation-database" not in command
    assert "fuzzing" not in command
    assert "stateful" not in command


def test_command_falls_back_to_exact_operation_label() -> None:
    """OperationId 缺失时使用单个 method/path label，避免多个 include 的 OR 语义."""
    operation = OperationRef(method="post", path="/users")

    command = build_schemathesis_command(
        executable="st",
        head_spec_path="head.yaml",
        target_base_url="https://api.example.com",
        operation=operation,
        report_path=Path("report.ndjson"),
        max_examples=1,
        request_timeout_seconds=2.0,
    )

    assert command[5:7] == ("--include-name", "POST /users")
    assert "--include-operation-id" not in command
    assert "--include-method" not in command
    assert "--include-path" not in command


def test_pass_report_tolerates_multiple_scenarios_and_optional_fields() -> None:
    """Examples skip 与 Coverage success 应归一化为 operation PASSED."""
    result = parse_schemathesis_ndjson(_fixture("schemathesis_pass.ndjson"), OPERATION)

    assert result.outcome == RegressionOutcome.PASSED
    assert result.duration_ms == pytest.approx(126.0)
    assert result.failures == ()


def test_fail_report_preserves_only_safe_failure_evidence() -> None:
    """多个 check 可解析，但只保存固定消息、类型和 HTTP status."""
    result = parse_schemathesis_ndjson(_fixture("schemathesis_fail.ndjson"), OPERATION)

    assert result.outcome == RegressionOutcome.FAILED
    assert [check.status for check in result.checks] == [RegressionOutcome.PASSED, RegressionOutcome.FAILED]
    assert len(result.failures) == 1
    assert result.failures[0].category == "JsonSchemaError"
    assert result.failures[0].safe_message == "Response violates the declared schema"
    assert result.failures[0].status_code == 200
    assert "raw value" not in repr(result)


def test_error_report_has_priority_over_engine_completed() -> None:
    """NonFatalError 与 error 状态优先于 EngineFinished.completed."""
    result = parse_schemathesis_ndjson(_fixture("schemathesis_error.ndjson"), OPERATION)

    assert result.outcome == RegressionOutcome.ERROR
    assert result.failures[0].category == "ConnectTimeout"
    assert "raw-network-detail" not in repr(result)


def test_failure_then_error_keeps_safe_failure_but_returns_error() -> None:
    """同一运行先 failure 后 error 时保留证据，但 outcome 必须为 ERROR."""
    failure_lines = _fixture("schemathesis_fail.ndjson").splitlines()
    error_lines = _fixture("schemathesis_error.ndjson").splitlines()
    combined = b"\n".join((*failure_lines[:-1], *error_lines[2:]))

    result = parse_schemathesis_ndjson(combined, OPERATION)

    assert result.outcome == RegressionOutcome.ERROR
    assert {failure.category for failure in result.failures} == {"JsonSchemaError", "ConnectTimeout"}


def test_parser_rejects_malformed_or_missing_completion_events() -> None:
    """坏 JSON 和缺少 EngineFinished 都不能猜测为 PASS."""
    with pytest.raises(GuardianException) as malformed:
        parse_schemathesis_ndjson(b"not-json", OPERATION)
    with pytest.raises(GuardianException) as incomplete:
        parse_schemathesis_ndjson(b"\n".join(_fixture("schemathesis_pass.ndjson").splitlines()[:-1]), OPERATION)

    assert malformed.value.code == GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT
    assert incomplete.value.code == GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT


def test_parser_drops_all_secret_bearing_ndjson_fields() -> None:
    """command、case/request headers、body、URI 与 response content 不进入 DTO."""
    result = parse_schemathesis_ndjson(_fixture("schemathesis_secret.ndjson"), OPERATION)
    serialized = result.model_dump_json()

    assert result.outcome == RegressionOutcome.FAILED
    for marker in (
        "guardian-initialize-secret",
        "guardian-case-secret",
        "guardian-cookie-secret",
        "guardian-request-secret",
        "guardian-request-body-secret",
        "guardian-response-cookie-secret",
        "guardian-response-body-secret",
        "guardian-response-value-secret",
        "guardian-uri-secret",
    ):
        assert marker not in serialized


def test_adapter_parses_report_sets_utf8_and_cleans_temporary_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter 不传应用 Secret，并在退出临时目录后删除 raw NDJSON."""
    marker = "guardian-environment-secret"
    monkeypatch.setenv("GUARDIAN_TEST_SECRET", marker)
    runner = StaticProcessRunner(
        ProcessResult(exit_code=1, stdout=b"terminal-output", stderr=b""),
        report_payload=_fixture("schemathesis_fail.ndjson"),
    )
    adapter = SchemathesisAdapter(runner=runner)

    execution = asyncio.run(
        adapter.run_regression(
            str(FIXTURE_DIR / "head_breaking_openapi.yaml"),
            "http://127.0.0.1:8000",
            (OPERATION,),
            1024 * 1024,
        )
    )

    command = runner.commands[0]
    report_path = Path(command[command.index("--report-ndjson-path") + 1])
    assert execution.results[0].outcome == RegressionOutcome.FAILED
    assert execution.tool.failed_operations == 1
    assert runner.environments[0]["PYTHONUTF8"] == "1"
    assert "GUARDIAN_TEST_SECRET" not in runner.environments[0]
    assert marker not in repr(execution)
    assert not report_path.exists()
    assert runner.working_directories[0] == report_path.parent
    working_directory = runner.working_directories[0]
    assert working_directory is not None
    assert not working_directory.exists()


def test_adapter_runs_each_selected_operation_in_its_own_process() -> None:
    """两个 affected operations 必须得到两个精确命令，不能合并或扩大 filter."""
    second_operation = OperationRef(method="POST", path="/users")
    pass_payload = _fixture("schemathesis_pass.ndjson")
    second_payload = pass_payload.replace(b"GET /items/{item_id}", b"POST /users")
    runner = StaticProcessRunner(
        ProcessResult(exit_code=0, stdout=b"", stderr=b""),
        report_payloads=(pass_payload, second_payload),
    )

    execution = asyncio.run(
        SchemathesisAdapter(runner=runner).run_regression(
            str(FIXTURE_DIR / "head_breaking_openapi.yaml"),
            "http://127.0.0.1:8000",
            (OPERATION, second_operation),
            1024 * 1024,
        )
    )

    assert execution.tool.total_operations == 2
    assert execution.tool.passed_operations == 2
    assert len(runner.commands) == 2
    assert "--include-operation-id" in runner.commands[0]
    assert "--include-method" not in runner.commands[0]
    assert runner.commands[1][5:7] == ("--include-name", "POST /users")


def test_regression_and_final_verdict_use_error_first_precedence() -> None:
    """聚合规则严格保持 ERROR > FAIL > PASS."""
    passed = parse_schemathesis_ndjson(_fixture("schemathesis_pass.ndjson"), OPERATION)
    failed = parse_schemathesis_ndjson(_fixture("schemathesis_fail.ndjson"), OPERATION)
    errored = parse_schemathesis_ndjson(_fixture("schemathesis_error.ndjson"), OPERATION)

    assert determine_regression_verdict((passed,), (OPERATION,)) == GuardianVerdict.PASS
    assert determine_regression_verdict((failed,), (OPERATION,)) == GuardianVerdict.FAIL
    assert determine_regression_verdict((errored,), (OPERATION,)) == GuardianVerdict.ERROR
    assert determine_final_verdict(GuardianVerdict.FAIL, GuardianVerdict.PASS, has_error=False) == GuardianVerdict.FAIL
    assert (
        determine_final_verdict(GuardianVerdict.FAIL, GuardianVerdict.ERROR, has_error=False) == GuardianVerdict.ERROR
    )
