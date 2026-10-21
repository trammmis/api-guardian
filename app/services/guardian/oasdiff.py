"""oasdiff 1.28.0 的受控 Adapter 与严重级别归一化."""

import json
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.models import (
    ChangeSeverity,
    ContractChange,
    ContractChangeCandidate,
    ContractDiffResult,
    ToolRunSummary,
)
from app.services.guardian.subprocess import ControlledSubprocessRunner, ProcessRunner

OASDIFF_VERSION: Final = "1.28.0"

# oasdiff v1.28.0 checker/level.go 的唯一整数语义映射点。
OASDIFF_LEVEL_TO_SEVERITY: Final = MappingProxyType(
    {
        1: ChangeSeverity.INFO,
        2: ChangeSeverity.WARNING,
        3: ChangeSeverity.ERROR,
    }
)


class _OasdiffChange(BaseModel):
    """只在 Adapter 内部存在的 oasdiff JSON 结构."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    change_id: str = Field(alias="id")
    text: str
    level: int
    operation: str | None = None
    operation_id: str | None = Field(default=None, alias="operationId")
    path: str | None = None
    section: str | None = None
    fingerprint: str | None = None


_OASDIFF_CHANGES_ADAPTER = TypeAdapter(list[_OasdiffChange])


class OasdiffPort(Protocol):
    """Guardian node 所需的最小 oasdiff 协议."""

    async def compare(self, base_spec_path: str, head_spec_path: str, remaining_scan_bytes: int) -> ContractDiffResult:
        """执行 contract diff 并返回安全边界 DTO."""
        ...


def normalize_oasdiff_severity(level: int) -> ChangeSeverity:
    """集中把 oasdiff integer level 转换成领域严重级别."""
    try:
        return OASDIFF_LEVEL_TO_SEVERITY[level]
    except KeyError as exc:
        raise GuardianException(
            GuardianErrorCode.UNKNOWN_OASDIFF_SEVERITY,
            "oasdiff 返回了未识别的 severity level",
        ) from exc


def normalize_contract_changes(candidates: tuple[ContractChangeCandidate, ...]) -> tuple[ContractChange, ...]:
    """把 Adapter 边界 DTO 转换为业务层 ContractChange."""
    return tuple(
        ContractChange(
            change_id=candidate.change_id,
            summary=candidate.summary,
            severity=normalize_oasdiff_severity(candidate.severity_level),
            method=candidate.method,
            path=candidate.path,
            operation_id=candidate.operation_id,
            section=candidate.section,
            fingerprint=candidate.fingerprint,
        )
        for candidate in candidates
    )


class OasdiffAdapter:
    """封装 oasdiff CLI，不向业务层暴露 raw JSON 或 source path."""

    def __init__(
        self,
        *,
        executable: str = "oasdiff",
        runner: ProcessRunner | None = None,
        timeout_seconds: float = 30.0,
        max_artifact_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        """初始化已锁定版本的 Adapter."""
        self._executable = executable
        self._runner = runner or ControlledSubprocessRunner()
        self._timeout_seconds = timeout_seconds
        self._max_artifact_bytes = max_artifact_bytes

    async def compare(self, base_spec_path: str, head_spec_path: str, remaining_scan_bytes: int) -> ContractDiffResult:
        """执行 breaking diff，并立即解析和丢弃原始输出."""
        if remaining_scan_bytes <= 0:
            raise GuardianException(
                GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED,
                "单次 Scan artifact 总量超过配置限制",
            )
        command = (
            self._executable,
            "breaking",
            "--format",
            "json",
            "--allow-external-refs=false",
            "--fail-on",
            "ERR",
            "--",
            str(Path(base_spec_path)),
            str(Path(head_spec_path)),
        )
        result = await self._runner.run(
            command,
            timeout_seconds=self._timeout_seconds,
            max_stdout_bytes=self._max_artifact_bytes,
            max_stderr_bytes=self._max_artifact_bytes,
            max_total_bytes=remaining_scan_bytes,
        )
        if result.exit_code not in {0, 1}:
            raise GuardianException(
                GuardianErrorCode.OASDIFF_EXECUTION_FAILED,
                f"oasdiff 执行失败，exit code={result.exit_code}",
            )

        changes = self._parse_changes(result.stdout)
        candidates = tuple(
            ContractChangeCandidate(
                change_id=change.change_id,
                summary=change.text,
                severity_level=change.level,
                method=change.operation,
                path=change.path,
                operation_id=change.operation_id,
                section=change.section,
                fingerprint=change.fingerprint,
            )
            for change in changes
        )
        return ContractDiffResult(
            candidates=candidates,
            tool=ToolRunSummary(
                name="oasdiff",
                version=OASDIFF_VERSION,
                exit_code=result.exit_code,
                artifact_bytes=result.artifact_bytes,
            ),
        )

    def _parse_changes(self, stdout: bytes) -> list[_OasdiffChange]:
        """验证 JSON array；错误信息不包含原始 stdout."""
        try:
            payload = json.loads(stdout.decode("utf-8"))
            return _OASDIFF_CHANGES_ADAPTER.validate_python(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            raise GuardianException(
                GuardianErrorCode.OASDIFF_INVALID_OUTPUT,
                "oasdiff 返回了无效的 structured output",
            ) from exc
