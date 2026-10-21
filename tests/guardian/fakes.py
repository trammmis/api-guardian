"""Guardian 离线测试使用的受控 fake."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from app.services.guardian.errors import GuardianException
from app.services.guardian.models import (
    OperationRef,
    RegressionExecutionResult,
    RegressionOutcome,
    RegressionResult,
    RegressionRunSummary,
)
from app.services.guardian.subprocess import ProcessResult


class StaticProcessRunner:
    """返回预置结果并记录参数数组的 fake runner."""

    def __init__(
        self,
        result: ProcessResult,
        report_payload: bytes | None = None,
        report_payloads: tuple[bytes, ...] = (),
    ) -> None:
        """保存预置的子进程结果."""
        self.result = result
        self.report_payload = report_payload
        self.report_payloads = report_payloads
        self.commands: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []
        self.working_directories: list[Path | None] = []

    async def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
        max_total_bytes: int,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        monitored_artifact_path: Path | None = None,
        max_monitored_artifact_bytes: int | None = None,
    ) -> ProcessResult:
        """记录非 Secret 参数并返回预置结果."""
        del timeout_seconds, max_stdout_bytes, max_stderr_bytes, max_total_bytes, max_monitored_artifact_bytes
        self.commands.append(tuple(command))
        self.environments.append(dict(env or {}))
        self.working_directories.append(cwd)
        report_bytes = 0
        payload = self.report_payload
        if self.report_payloads:
            payload = self.report_payloads[len(self.commands) - 1]
        if payload is not None and monitored_artifact_path is not None:
            report_path = (
                monitored_artifact_path / "report.ndjson"
                if monitored_artifact_path.is_dir()
                else monitored_artifact_path
            )
            report_path.write_bytes(payload)
            report_bytes = len(payload)
        return ProcessResult(
            exit_code=self.result.exit_code,
            stdout=self.result.stdout,
            stderr=self.result.stderr,
            monitored_artifact_bytes=report_bytes,
        )


class StaticSchemathesisAdapter:
    """返回预置安全领域结果并记录 scope 的 Schemathesis fake."""

    def __init__(self, execution: RegressionExecutionResult) -> None:
        """保存预置执行结果."""
        self.execution = execution
        self.calls: list[tuple[str, str, tuple[OperationRef, ...], int]] = []

    async def run_regression(
        self,
        head_spec_path: str,
        target_base_url: str,
        operations: tuple[OperationRef, ...],
        remaining_scan_bytes: int,
    ) -> RegressionExecutionResult:
        """记录精确 scope 并返回结果."""
        self.calls.append((head_spec_path, target_base_url, operations, remaining_scan_bytes))
        return self.execution


class OutcomeSchemathesisAdapter:
    """按实际传入 scope 动态生成统一 outcome 的 Graph fake."""

    def __init__(self, outcome: RegressionOutcome) -> None:
        """保存期望 operation outcome."""
        self.outcome = outcome
        self.calls: list[tuple[OperationRef, ...]] = []

    async def run_regression(
        self,
        head_spec_path: str,
        target_base_url: str,
        operations: tuple[OperationRef, ...],
        remaining_scan_bytes: int,
    ) -> RegressionExecutionResult:
        """忽略非 scope 输入，生成与 scope 顺序一致的结果."""
        del head_spec_path, target_base_url, remaining_scan_bytes
        self.calls.append(operations)
        results = tuple(RegressionResult(operation=operation, outcome=self.outcome) for operation in operations)
        return RegressionExecutionResult(
            results=results,
            tool=RegressionRunSummary(
                tool_name="schemathesis",
                tool_version="4.24.3",
                exit_code=0 if self.outcome == RegressionOutcome.PASSED else 1,
                artifact_bytes=0,
                total_operations=len(results),
                passed_operations=sum(result.outcome == RegressionOutcome.PASSED for result in results),
                failed_operations=sum(result.outcome == RegressionOutcome.FAILED for result in results),
                error_operations=sum(result.outcome == RegressionOutcome.ERROR for result in results),
                skipped_operations=sum(result.outcome == RegressionOutcome.SKIPPED for result in results),
            ),
        )


class RaisingProcessRunner:
    """模拟外部工具无法启动、timeout 或 artifact ERROR."""

    def __init__(self, error: GuardianException) -> None:
        """保存待抛出的安全异常."""
        self.error = error
        self.commands: list[tuple[str, ...]] = []

    async def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
        max_total_bytes: int,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        monitored_artifact_path: Path | None = None,
        max_monitored_artifact_bytes: int | None = None,
    ) -> ProcessResult:
        """记录命令后抛出预置错误，不包含 argv 或环境详情."""
        del (
            timeout_seconds,
            max_stdout_bytes,
            max_stderr_bytes,
            max_total_bytes,
            cwd,
            env,
            monitored_artifact_path,
            max_monitored_artifact_bytes,
        )
        self.commands.append(tuple(command))
        raise self.error
