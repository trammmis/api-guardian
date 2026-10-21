"""E2 Full Schemathesis Baseline：一次 CLI 覆盖整个 Head Spec。."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.models import GuardianVerdict, OperationRef
from app.services.guardian.schemathesis import (
    SCHEMATHESIS_CHECK,
    SCHEMATHESIS_MAX_REDIRECTS,
    SCHEMATHESIS_PHASES,
    SCHEMATHESIS_VERSION,
)
from app.services.guardian.subprocess import ControlledSubprocessRunner
from benchmarks.guardian.models import SystemResult

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


class FullSchemathesisCli:
    """保持生产参数不变，仅把测试范围设为完整 Head Spec。."""

    def __init__(
        self,
        *,
        executable: str,
        timeout_seconds: float,
        request_timeout_seconds: float,
        max_examples: int,
        max_artifact_bytes: int,
    ) -> None:
        """绑定固定版本 CLI 和资源限制。."""
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._request_timeout_seconds = request_timeout_seconds
        self._max_examples = max_examples
        self._max_artifact_bytes = max_artifact_bytes
        self._runner = ControlledSubprocessRunner()

    async def run(
        self,
        head_spec_path: str,
        target_base_url: str,
        operations: tuple[OperationRef, ...],
        remaining_scan_bytes: int,
    ) -> SystemResult:
        """一次运行完整 Spec，并只保留 verdict、scope 与计数。."""
        with TemporaryDirectory(prefix="guardian-benchmark-full-") as temp_dir:
            temp_path = Path(temp_dir)
            report_path = temp_path / "report.ndjson"
            report_path.touch(exist_ok=False)
            command = self._command(head_spec_path, target_base_url, report_path)
            result = await self._runner.run(
                command,
                timeout_seconds=self._timeout_seconds,
                max_stdout_bytes=self._max_artifact_bytes,
                max_stderr_bytes=self._max_artifact_bytes,
                max_total_bytes=remaining_scan_bytes,
                cwd=temp_path,
                env=_environment(),
                monitored_artifact_path=temp_path,
                max_monitored_artifact_bytes=self._max_artifact_bytes,
            )
            if result.exit_code not in {0, 1}:
                raise GuardianException(
                    GuardianErrorCode.SCHEMATHESIS_EXECUTION_FAILED,
                    f"Schemathesis full baseline 执行失败，exit code={result.exit_code}",
                )
            if not report_path.is_file() or report_path.stat().st_size <= 0:
                raise GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "Full baseline 缺少 NDJSON")
            if report_path.stat().st_size > self._max_artifact_bytes:
                raise GuardianException(GuardianErrorCode.ARTIFACT_TOO_LARGE, "Full baseline artifact 超限")
            verdict = _parse_full_report(report_path.read_bytes(), len(operations), result.exit_code)
        return SystemResult(
            regression_verdict=verdict,
            final_verdict=verdict,
            selected_operations=tuple(operation.key for operation in operations),
            total_operations=len(operations),
            operations_tested=len(operations) if verdict != GuardianVerdict.ERROR else 0,
            bug_detected=verdict == GuardianVerdict.FAIL,
        )

    def _command(self, head_spec_path: str, target_base_url: str, report_path: Path) -> tuple[str, ...]:
        """构造没有 include filter 的固定 Full Schemathesis 命令。."""
        return (
            self._executable,
            "run",
            str(Path(head_spec_path).resolve()),
            "--url",
            target_base_url,
            "--checks",
            SCHEMATHESIS_CHECK,
            "--phases",
            SCHEMATHESIS_PHASES,
            "--max-examples",
            str(self._max_examples),
            "--generation-deterministic",
            "--max-redirects",
            SCHEMATHESIS_MAX_REDIRECTS,
            "--request-timeout",
            str(self._request_timeout_seconds),
            "--report",
            "ndjson",
            "--report-ndjson-path",
            str(report_path),
            "--output-sanitize",
            "true",
            "--no-color",
        )


def _parse_full_report(payload: bytes, expected_operations: int, exit_code: int) -> GuardianVerdict:
    """解析多 operation NDJSON；exit code 不能单独决定 FAIL 或 ERROR。."""
    if not payload:
        raise GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "Full baseline NDJSON 为空")
    initialized = False
    loaded = False
    engine_finished = False
    engine_completed = False
    selected: int | None = None
    scenarios = 0
    failed_check = False
    error_seen = False
    for line in payload.splitlines():
        try:
            envelope = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GuardianException(
                GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "Full baseline NDJSON 无效"
            ) from exc
        if not isinstance(envelope, dict) or len(envelope) != 1:
            raise GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "Full baseline event envelope 无效")
        event_name, event = next(iter(envelope.items()))
        if event_name not in _ALLOWED_EVENTS or not isinstance(event, dict):
            raise GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "Full baseline 包含未知事件")
        if event_name == "Initialize":
            initialized = True
            version = event.get("schemathesis_version")
            if version is not None and version != SCHEMATHESIS_VERSION:
                raise GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "Schemathesis 版本不匹配")
        elif event_name == "LoadingFinished":
            loaded = True
            statistic = _mapping(event.get("statistic"))
            operations = _mapping(statistic.get("operations"))
            value = operations.get("selected")
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "selected operation 数量无效")
            selected = value
        elif event_name == "ScenarioFinished":
            scenarios += 1
            status = event.get("status")
            if status == "error":
                error_seen = True
            recorder = _mapping(event.get("recorder"))
            for group in _mapping(recorder.get("checks")).values():
                if not isinstance(group, list):
                    raise GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "check group 无效")
                for check in group:
                    if not isinstance(check, dict) or check.get("name") != SCHEMATHESIS_CHECK:
                        raise GuardianException(GuardianErrorCode.SCHEMATHESIS_INVALID_REPORT, "check 不在白名单")
                    if check.get("status") == "failure":
                        failed_check = True
        elif event_name in {"SuiteFinished", "PhaseFinished"} and event.get("status") == "error":
            error_seen = True
        elif event_name == "NonFatalError":
            error_seen = True
        elif event_name == "EngineFinished":
            engine_finished = True
            engine_completed = event.get("stop_reason") == "completed"
    if not initialized or not loaded or not engine_finished or not engine_completed or scenarios == 0:
        return GuardianVerdict.ERROR
    if selected is not None and selected != expected_operations:
        return GuardianVerdict.ERROR
    if error_seen:
        return GuardianVerdict.ERROR
    verdict = GuardianVerdict.FAIL if failed_check else GuardianVerdict.PASS
    if (verdict == GuardianVerdict.PASS and exit_code != 0) or (verdict == GuardianVerdict.FAIL and exit_code != 1):
        return GuardianVerdict.ERROR
    return verdict


def _mapping(value: Any) -> Mapping[str, Any]:
    """可选 mapping 缺失时返回空 mapping。."""
    return value if isinstance(value, dict) else {}


def _environment() -> dict[str, str]:
    """只向 baseline 子进程传递必要环境并强制 UTF-8。."""
    environment = {key: value for key, value in os.environ.items() if key.upper() in _CHILD_ENVIRONMENT_KEYS}
    environment["PYTHONUTF8"] = "1"
    return environment
