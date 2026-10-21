"""Schemathesis 4.24.3 Adapter 与白名单 NDJSON parser."""

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final, Protocol

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.models import (
    GuardianVerdict,
    OperationRef,
    RegressionCheck,
    RegressionExecutionResult,
    RegressionFailure,
    RegressionOutcome,
    RegressionResult,
    RegressionRunSummary,
)
from app.services.guardian.subprocess import ControlledSubprocessRunner, ProcessResult, ProcessRunner

SCHEMATHESIS_VERSION: Final = "4.24.3"
SCHEMATHESIS_CHECK: Final = "response_schema_conformance"
SCHEMATHESIS_PHASES: Final = "examples,coverage"
SCHEMATHESIS_MAX_REDIRECTS: Final = "0"
_ALLOWED_EVENTS: Final = frozenset(
    {
        "Initialize",
        "LoadingStarted",
        "LoadingFinished",
        "EngineStarted",
        "PhaseStarted",
        "PhaseFinished",
        "SuiteStarted",
        "SuiteFinished",
        "ScenarioStarted",
        "ScenarioFinished",
        "NonFatalError",
        "EngineFinished",
    }
)
_EVENT_STATUSES: Final = frozenset({"success", "failure", "error", "skip"})
_SAFE_PHASES: Final = frozenset({"API probing", "Schema analysis", "Examples", "Coverage", "Fuzzing", "Stateful"})
_SAFE_CATEGORY_PATTERN: Final = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAX_SAFE_CHECKS: Final = 1000
_MAX_SAFE_FAILURES: Final = 100
_CHILD_ENVIRONMENT_KEYS: Final = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "VIRTUAL_ENV",
        "WINDIR",
    }
)


class SchemathesisPort(Protocol):
    """Guardian node 调用 Schemathesis 所需的最小协议."""

    async def run_regression(
        self,
        head_spec_path: str,
        target_base_url: str,
        operations: tuple[OperationRef, ...],
        remaining_scan_bytes: int,
    ) -> RegressionExecutionResult:
        """逐 operation 执行回归并返回安全领域对象."""
        ...


def build_schemathesis_command(
    *,
    executable: str,
    head_spec_path: str,
    target_base_url: str,
    operation: OperationRef,
    report_path: Path,
    max_examples: int,
    request_timeout_seconds: float,
) -> tuple[str, ...]:
    """构造固定参数的 Schemathesis 命令，不接受任意用户 CLI 参数."""
    operation_filter: tuple[str, ...]
    if operation.operation_id:
        operation_filter = ("--include-operation-id", operation.operation_id)
    else:
        # 4.24.3 的多个 --include-* 是 OR；单个 exact operation label 才能表达 method AND path。
        operation_filter = ("--include-name", operation.key)
    return (
        executable,
        "run",
        str(Path(head_spec_path).resolve()),
        "--url",
        target_base_url,
        *operation_filter,
        "--checks",
        SCHEMATHESIS_CHECK,
        "--phases",
        SCHEMATHESIS_PHASES,
        "--max-examples",
        str(max_examples),
        "--generation-deterministic",
        "--max-redirects",
        SCHEMATHESIS_MAX_REDIRECTS,
        "--request-timeout",
        str(request_timeout_seconds),
        "--report",
        "ndjson",
        "--report-ndjson-path",
        str(report_path),
        "--output-sanitize",
        "true",
        "--no-color",
    )


def parse_schemathesis_ndjson(payload: bytes, operation: OperationRef) -> RegressionResult:
    """只从白名单事件和字段构造单 operation 的安全结果."""
    if not payload:
        raise _invalid_report("Schemathesis 未产生有效 NDJSON")

    initialize_seen = False
    loading_finished_seen = False
    engine_finished_seen = False
    engine_completed = False
    selected_operations: int | None = None
    scenario_seen = False
    success_scenario_seen = False
    skipped_scenario_seen = False
    failure_status_seen = False
    error_status_seen = False
    related_non_fatal_error_seen = False
    duration_seconds = 0.0
    duration_seen = False
    checks: list[RegressionCheck] = []
    failures: list[RegressionFailure] = []

    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            raise _invalid_report("Schemathesis NDJSON 包含空事件行")
        event_name, event = _parse_event(line, line_number)

        if event_name == "Initialize":
            initialize_seen = True
            version = event.get("schemathesis_version")
            if version is not None and version != SCHEMATHESIS_VERSION:
                raise _invalid_report("Schemathesis report 版本与锁定版本不一致")
            # Initialize.command 可能包含敏感值，明确不读取。
        elif event_name == "LoadingFinished":
            loading_finished_seen = True
            selected_operations = _selected_operation_count(event)
        elif event_name == "ScenarioFinished":
            scenario_seen = True
            status = _event_status(event, event_name)
            phase = _safe_phase(event.get("phase"))
            recorder = _optional_mapping(event.get("recorder"))
            _validate_operation_label(recorder, operation)
            scenario_checks, scenario_failures = _parse_checks(recorder, operation, phase)
            checks.extend(scenario_checks)
            failures.extend(scenario_failures)
            if len(checks) > _MAX_SAFE_CHECKS or len(failures) > _MAX_SAFE_FAILURES:
                raise _invalid_report("Schemathesis report 的安全结果数量超过限制")

            if status == "success":
                success_scenario_seen = True
            elif status == "failure":
                failure_status_seen = True
            elif status == "error":
                error_status_seen = True
            else:
                skipped_scenario_seen = True

            elapsed = event.get("elapsed_time")
            if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
                elapsed_value = float(elapsed)
                if elapsed_value < 0 or not isfinite(elapsed_value):
                    raise _invalid_report("Schemathesis scenario duration 无效")
                duration_seconds += elapsed_value
                duration_seen = True
        elif event_name in {"SuiteFinished", "PhaseFinished"}:
            status = _event_status(event, event_name)
            if status == "failure":
                failure_status_seen = True
            elif status == "error":
                error_status_seen = True
        elif event_name == "NonFatalError":
            related = event.get("related_to_operation")
            if related is True:
                related_non_fatal_error_seen = True
            failures.append(_non_fatal_failure(event, operation))
        elif event_name == "EngineFinished":
            engine_finished_seen = True
            engine_completed = event.get("stop_reason") == "completed"

    if not initialize_seen or not loading_finished_seen or not engine_finished_seen:
        raise _invalid_report("Schemathesis NDJSON 缺少可信结束所需事件")
    if selected_operations is not None and selected_operations != 1:
        raise _invalid_report("Schemathesis operation filter 未精确选择一个 operation")
    if not scenario_seen:
        raise _invalid_report("Schemathesis NDJSON 没有 ScenarioFinished")

    failed_check_seen = any(check.status == RegressionOutcome.FAILED for check in checks)
    error_seen = error_status_seen or related_non_fatal_error_seen or not engine_completed
    if error_seen:
        if not any(failure.check_name == "schemathesis_execution" for failure in failures):
            failures.append(
                _safe_failure(
                    operation=operation,
                    check_name="schemathesis_execution",
                    category="execution_error",
                    safe_message="Schemathesis could not complete the target operation",
                    status_code=None,
                    phase=None,
                )
            )
        outcome = RegressionOutcome.ERROR
    elif failure_status_seen or failed_check_seen:
        if not failed_check_seen:
            raise _invalid_report("Schemathesis failure 状态缺少可信 check evidence")
        outcome = RegressionOutcome.FAILED
    elif success_scenario_seen:
        outcome = RegressionOutcome.PASSED
    elif skipped_scenario_seen:
        outcome = RegressionOutcome.SKIPPED
    else:
        raise _invalid_report("Schemathesis operation 没有可解释结果")

    return RegressionResult(
        operation=operation,
        outcome=outcome,
        checks=tuple(checks),
        failures=tuple(failures),
        duration_ms=duration_seconds * 1000 if duration_seen else None,
    )


def determine_regression_verdict(
    results: tuple[RegressionResult, ...],
    expected_operations: tuple[OperationRef, ...],
) -> GuardianVerdict:
    """按 ERROR > FAIL > PASS 聚合目标 operation 结果."""
    expected_keys = [operation.key for operation in expected_operations]
    actual_keys = [result.operation.key for result in results]
    if actual_keys != expected_keys or len(actual_keys) != len(set(actual_keys)):
        return GuardianVerdict.ERROR
    if any(result.outcome in {RegressionOutcome.ERROR, RegressionOutcome.SKIPPED} for result in results):
        return GuardianVerdict.ERROR
    if any(result.outcome == RegressionOutcome.FAILED for result in results):
        return GuardianVerdict.FAIL
    if all(result.outcome == RegressionOutcome.PASSED for result in results):
        return GuardianVerdict.PASS
    return GuardianVerdict.ERROR


def determine_final_verdict(
    contract_verdict: GuardianVerdict | None,
    regression_verdict: GuardianVerdict | None,
    *,
    has_error: bool,
) -> GuardianVerdict:
    """完全确定性地合并 contract 与 regression verdict."""
    if has_error or contract_verdict == GuardianVerdict.ERROR or regression_verdict == GuardianVerdict.ERROR:
        return GuardianVerdict.ERROR
    if contract_verdict == GuardianVerdict.FAIL or regression_verdict == GuardianVerdict.FAIL:
        return GuardianVerdict.FAIL
    if contract_verdict == GuardianVerdict.PASS and regression_verdict == GuardianVerdict.PASS:
        return GuardianVerdict.PASS
    return GuardianVerdict.ERROR


class SchemathesisAdapter:
    """逐 operation 执行 Schemathesis，并在临时目录内立即解析报告."""

    def __init__(
        self,
        *,
        executable: str = "st",
        runner: ProcessRunner | None = None,
        timeout_seconds: float = 30.0,
        request_timeout_seconds: float = 2.0,
        max_examples: int = 1,
        max_artifact_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        """初始化固定 Schemathesis 4.24.3 执行策略."""
        self._executable = executable
        self._runner = runner or ControlledSubprocessRunner()
        self._timeout_seconds = timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._max_examples = max_examples
        self._max_artifact_bytes = max_artifact_bytes

    async def run_regression(
        self,
        head_spec_path: str,
        target_base_url: str,
        operations: tuple[OperationRef, ...],
        remaining_scan_bytes: int,
    ) -> RegressionExecutionResult:
        """按 scope 顺序独立执行，不合并 operation filters."""
        if remaining_scan_bytes <= 0:
            raise GuardianException(
                GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED,
                "单次 Scan artifact 总量超过配置限制",
            )

        results: list[RegressionResult] = []
        exit_codes: list[int] = []
        artifact_bytes = 0
        for operation in operations:
            remaining = remaining_scan_bytes - artifact_bytes
            if remaining <= 0:
                raise GuardianException(
                    GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED,
                    "单次 Scan artifact 总量超过配置限制",
                )
            regression_result, process_result, operation_artifact_bytes = await self._run_operation(
                head_spec_path,
                target_base_url,
                operation,
                remaining,
            )
            results.append(regression_result)
            exit_codes.append(process_result.exit_code)
            artifact_bytes += operation_artifact_bytes

        result_tuple = tuple(results)
        return RegressionExecutionResult(
            results=result_tuple,
            tool=_build_run_summary(result_tuple, exit_codes, artifact_bytes),
        )

    async def _run_operation(
        self,
        head_spec_path: str,
        target_base_url: str,
        operation: OperationRef,
        remaining_scan_bytes: int,
    ) -> tuple[RegressionResult, ProcessResult, int]:
        """在受限临时目录运行并读取一个 operation 的 NDJSON."""
        with TemporaryDirectory(prefix="guardian-schemathesis-") as temp_dir:
            temp_path = Path(temp_dir)
            report_path = temp_path / "report.ndjson"
            _restrict_temporary_path(temp_path, report_path)
            command = build_schemathesis_command(
                executable=self._executable,
                head_spec_path=head_spec_path,
                target_base_url=target_base_url,
                operation=operation,
                report_path=report_path,
                max_examples=self._max_examples,
                request_timeout_seconds=self._request_timeout_seconds,
            )
            process_result = await self._runner.run(
                command,
                timeout_seconds=self._timeout_seconds,
                max_stdout_bytes=self._max_artifact_bytes,
                max_stderr_bytes=self._max_artifact_bytes,
                max_total_bytes=remaining_scan_bytes,
                cwd=temp_path,
                env=_schemathesis_environment(),
                monitored_artifact_path=temp_path,
                max_monitored_artifact_bytes=self._max_artifact_bytes,
            )
            if process_result.exit_code not in {0, 1}:
                raise GuardianException(
                    GuardianErrorCode.SCHEMATHESIS_EXECUTION_FAILED,
                    f"Schemathesis 执行失败，exit code={process_result.exit_code}",
                )
            report_size = report_path.stat().st_size if report_path.is_file() else 0
            if report_size <= 0:
                raise GuardianException(
                    GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT,
                    "Schemathesis 未产生有效 NDJSON",
                )
            if report_size > self._max_artifact_bytes:
                raise GuardianException(GuardianErrorCode.ARTIFACT_TOO_LARGE, "单个工具 artifact 超过配置限制")
            operation_artifact_bytes = (
                len(process_result.stdout) + len(process_result.stderr) + process_result.monitored_artifact_bytes
            )
            if operation_artifact_bytes > remaining_scan_bytes:
                raise GuardianException(
                    GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED,
                    "单次 Scan artifact 总量超过配置限制",
                )

            parsed = parse_schemathesis_ndjson(report_path.read_bytes(), operation)
            return (
                _apply_exit_code_evidence(parsed, process_result.exit_code),
                process_result,
                operation_artifact_bytes,
            )


def _parse_event(line: bytes, line_number: int) -> tuple[str, Mapping[str, Any]]:
    """解析单行 NDJSON，并拒绝非白名单事件或多键 envelope."""
    try:
        decoded = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid_report(f"Schemathesis NDJSON 第 {line_number} 行无法解析") from exc
    if not isinstance(decoded, dict) or len(decoded) != 1:
        raise _invalid_report("Schemathesis NDJSON event envelope 无效")
    event_name = next(iter(decoded))
    if event_name not in _ALLOWED_EVENTS:
        raise _invalid_report("Schemathesis NDJSON 包含未知事件")
    event = decoded[event_name]
    if not isinstance(event, dict):
        raise _invalid_report("Schemathesis NDJSON event payload 无效")
    return event_name, event


def _event_status(event: Mapping[str, Any], event_name: str) -> str:
    """读取已完成事件的有限状态集合."""
    status = event.get("status")
    if not isinstance(status, str) or status not in _EVENT_STATUSES:
        raise _invalid_report(f"Schemathesis {event_name} status 无法解释")
    return status


def _selected_operation_count(event: Mapping[str, Any]) -> int | None:
    """读取可选 selected 数量；字段缺失时不做猜测或强制要求."""
    statistic = _optional_mapping(event.get("statistic"))
    operations = _optional_mapping(statistic.get("operations"))
    selected = operations.get("selected")
    if selected is None:
        return None
    if not isinstance(selected, int) or isinstance(selected, bool) or selected < 0:
        raise _invalid_report("Schemathesis selected operation count 无效")
    return selected


def _validate_operation_label(recorder: Mapping[str, Any], operation: OperationRef) -> None:
    """如果报告提供 label，则必须与当前独立 operation 一致."""
    label = recorder.get("label")
    if label is not None and label != operation.key:
        raise _invalid_report("Schemathesis report operation 与 selected scope 不一致")


def _parse_checks(
    recorder: Mapping[str, Any],
    operation: OperationRef,
    phase: str | None,
) -> tuple[list[RegressionCheck], list[RegressionFailure]]:
    """只读取 check 与 status code，不读取 cases、headers、content 或 body."""
    check_groups = _optional_mapping(recorder.get("checks"))
    interactions = _optional_mapping(recorder.get("interactions"))
    checks: list[RegressionCheck] = []
    failures: list[RegressionFailure] = []
    for case_id, group in check_groups.items():
        if not isinstance(case_id, str) or not isinstance(group, list):
            raise _invalid_report("Schemathesis recorder.checks 结构无效")
        status_code = _status_code_for_case(interactions.get(case_id))
        for raw_check in group:
            if not isinstance(raw_check, dict):
                raise _invalid_report("Schemathesis check 结构无效")
            name = raw_check.get("name")
            status = raw_check.get("status")
            if name != SCHEMATHESIS_CHECK or not isinstance(status, str):
                raise _invalid_report("Schemathesis check 不在固定白名单中")
            outcome = _check_outcome(status)
            safe_message = "Response violates the declared schema" if outcome == RegressionOutcome.FAILED else None
            checks.append(RegressionCheck(name=name, status=outcome, message=safe_message))
            if outcome == RegressionOutcome.FAILED:
                failure_info = _optional_mapping(raw_check.get("failure_info"))
                failure = _optional_mapping(failure_info.get("failure"))
                category = _safe_category(failure.get("type"), "check_failure")
                failures.append(
                    _safe_failure(
                        operation=operation,
                        check_name=name,
                        category=category,
                        safe_message="Response violates the declared schema",
                        status_code=status_code,
                        phase=phase,
                    )
                )
    return checks, failures


def _check_outcome(status: str) -> RegressionOutcome:
    """把 Schemathesis check status 映射到领域 outcome."""
    mapping = {
        "success": RegressionOutcome.PASSED,
        "failure": RegressionOutcome.FAILED,
        "error": RegressionOutcome.ERROR,
        "skip": RegressionOutcome.SKIPPED,
    }
    try:
        return mapping[status]
    except KeyError as exc:
        raise _invalid_report("Schemathesis check status 无法解释") from exc


def _status_code_for_case(raw_interactions: Any) -> int | None:
    """从对应 case 的最后一次 interaction 仅读取 HTTP status code."""
    if isinstance(raw_interactions, dict):
        last_interaction = raw_interactions
    elif isinstance(raw_interactions, list) and raw_interactions:
        last_interaction = raw_interactions[-1]
    else:
        return None
    if not isinstance(last_interaction, dict):
        return None
    response = last_interaction.get("response")
    if not isinstance(response, dict):
        return None
    status_code = response.get("status_code")
    if isinstance(status_code, int) and not isinstance(status_code, bool) and 100 <= status_code <= 599:
        return status_code
    return None


def _non_fatal_failure(event: Mapping[str, Any], operation: OperationRef) -> RegressionFailure:
    """把 NonFatalError 转成固定安全消息，不读取原始 error message 或 URI."""
    value = _optional_mapping(event.get("value"))
    category = _safe_category(value.get("type"), "schemathesis_error")
    return _safe_failure(
        operation=operation,
        check_name="schemathesis_execution",
        category=category,
        safe_message="Schemathesis could not complete the target operation",
        status_code=None,
        phase=_safe_phase(event.get("phase")),
    )


def _safe_failure(
    *,
    operation: OperationRef,
    check_name: str,
    category: str,
    safe_message: str,
    status_code: int | None,
    phase: str | None,
) -> RegressionFailure:
    """生成不依赖动态 case id 的稳定 failure id."""
    fingerprint_source = "\x1f".join(
        (
            operation.key,
            check_name,
            category,
            safe_message,
            str(status_code or ""),
            phase or "",
        )
    )
    return RegressionFailure(
        failure_id=hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:16],
        operation=operation,
        check_name=check_name,
        category=category,
        safe_message=safe_message,
        status_code=status_code,
        phase=phase,
    )


def _safe_category(value: Any, fallback: str) -> str:
    """只接受短类型标识，防止把原始错误文本放入 DTO."""
    if isinstance(value, str) and _SAFE_CATEGORY_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_phase(value: Any) -> str | None:
    """只保留当前版本已验证的 phase 名称."""
    if isinstance(value, str) and value in _SAFE_PHASES:
        return value
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name in _SAFE_PHASES:
            return name
    return None


def _optional_mapping(value: Any) -> Mapping[str, Any]:
    """把可选 object 统一为空 mapping；非 object 视为无效报告."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid_report("Schemathesis NDJSON object 字段结构无效")
    return value


def _apply_exit_code_evidence(result: RegressionResult, exit_code: int) -> RegressionResult:
    """用 exit code 做一致性校验，但不直接把 exit code 映射为 verdict."""
    mismatch = (result.outcome == RegressionOutcome.PASSED and exit_code != 0) or (
        result.outcome == RegressionOutcome.FAILED and exit_code != 1
    )
    if not mismatch:
        return result
    failure = _safe_failure(
        operation=result.operation,
        check_name="schemathesis_execution",
        category="exit_code_mismatch",
        safe_message="Schemathesis exit code conflicts with structured report evidence",
        status_code=None,
        phase=None,
    )
    return result.model_copy(
        update={
            "outcome": RegressionOutcome.ERROR,
            "failures": (*result.failures, failure),
        }
    )


def _build_run_summary(
    results: tuple[RegressionResult, ...],
    exit_codes: Sequence[int],
    artifact_bytes: int,
) -> RegressionRunSummary:
    """聚合多进程结果；exit code 仅用于诊断，不参与 verdict 映射."""
    return RegressionRunSummary(
        tool_name="schemathesis",
        tool_version=SCHEMATHESIS_VERSION,
        exit_code=max(exit_codes, default=0),
        artifact_bytes=artifact_bytes,
        total_operations=len(results),
        passed_operations=sum(result.outcome == RegressionOutcome.PASSED for result in results),
        failed_operations=sum(result.outcome == RegressionOutcome.FAILED for result in results),
        error_operations=sum(result.outcome == RegressionOutcome.ERROR for result in results),
        skipped_operations=sum(result.outcome == RegressionOutcome.SKIPPED for result in results),
    )


def _schemathesis_environment() -> dict[str, str]:
    """只向子进程传递运行必需环境，并在 Windows 强制 UTF-8."""
    environment = {key: value for key, value in os.environ.items() if key.upper() in _CHILD_ENVIRONMENT_KEYS}
    environment["PYTHONUTF8"] = "1"
    return environment


def _restrict_temporary_path(directory: Path, report_path: Path) -> None:
    """创建最小权限临时报告；TemporaryDirectory 退出时自动清理."""
    try:
        directory.chmod(0o700)
        report_path.touch(exist_ok=False)
        report_path.chmod(0o600)
    except OSError as exc:
        raise GuardianException(
            GuardianErrorCode.SCHEMATHESIS_EXECUTION_FAILED,
            "无法创建 Schemathesis 临时报告",
        ) from exc


def _invalid_report(message: str) -> GuardianException:
    """构造不包含原始 NDJSON 的统一 parser 错误."""
    return GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, message)
