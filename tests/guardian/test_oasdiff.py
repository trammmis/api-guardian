"""oasdiff Adapter 与 severity normalizer 的离线测试."""

import asyncio
from pathlib import Path

import pytest

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.models import ChangeSeverity, ContractChangeCandidate
from app.services.guardian.oasdiff import OasdiffAdapter, normalize_contract_changes, normalize_oasdiff_severity
from app.services.guardian.subprocess import ProcessResult
from tests.guardian.fakes import StaticProcessRunner

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (1, ChangeSeverity.INFO),
        (2, ChangeSeverity.WARNING),
        (3, ChangeSeverity.ERROR),
    ],
)
def test_severity_normalizer_uses_single_versioned_mapping(level: int, expected: ChangeSeverity) -> None:
    """验证 v1.28.0 的三个 integer level 都集中映射."""
    assert normalize_oasdiff_severity(level) == expected


def test_severity_normalizer_rejects_unknown_integer() -> None:
    """未知 level 必须进入 ERROR，而不是猜测语义."""
    with pytest.raises(GuardianException) as captured:
        normalize_oasdiff_severity(99)

    assert captured.value.code == GuardianErrorCode.UNKNOWN_OASDIFF_SEVERITY


def test_normalization_creates_domain_change() -> None:
    """边界 DTO 应被归一化成稳定 ContractChange."""
    candidate = ContractChangeCandidate(
        change_id="response-property-type-changed",
        summary="type changed",
        severity_level=3,
        method="get",
        path="/items/{item_id}",
        operation_id="getItem",
    )

    change = normalize_contract_changes((candidate,))[0]

    assert change.severity == ChangeSeverity.ERROR
    assert change.method == "GET"
    assert change.path == "/items/{item_id}"


def test_severity_fixture_normalizes_all_integer_levels() -> None:
    """版本化 fixture 应覆盖 INFO、WARNING 与 ERROR 的完整映射."""
    payload = (FIXTURE_DIR / "oasdiff_breaking_severity_levels.json").read_bytes()
    adapter = OasdiffAdapter(runner=StaticProcessRunner(ProcessResult(exit_code=1, stdout=payload, stderr=b"")))

    result = asyncio.run(adapter.compare("base.yaml", "head.yaml", 1024 * 1024))
    changes = normalize_contract_changes(result.candidates)

    assert [change.severity for change in changes] == [
        ChangeSeverity.INFO,
        ChangeSeverity.WARNING,
        ChangeSeverity.ERROR,
    ]


def test_adapter_parses_breaking_fixture_and_drops_source_paths() -> None:
    """Adapter 输出不得保留 raw oasdiff source path."""
    payload = (FIXTURE_DIR / "oasdiff_breaking.json").read_bytes()
    runner = StaticProcessRunner(ProcessResult(exit_code=1, stdout=payload, stderr=b""))
    adapter = OasdiffAdapter(runner=runner)

    result = asyncio.run(adapter.compare("base.yaml", "head.yaml", 1024 * 1024))

    assert len(result.candidates) == 1
    assert result.candidates[0].change_id == "response-property-type-changed"
    assert result.tool.exit_code == 1
    assert "raw-oasdiff-source" not in result.model_dump_json()
    assert runner.commands[0] == (
        "oasdiff",
        "breaking",
        "--format",
        "json",
        "--allow-external-refs=false",
        "--fail-on",
        "ERR",
        "--",
        "base.yaml",
        "head.yaml",
    )


def test_adapter_accepts_no_change_fixture() -> None:
    """空 JSON array 应产生成功且无变更的结果."""
    payload = (FIXTURE_DIR / "oasdiff_no_changes.json").read_bytes()
    adapter = OasdiffAdapter(runner=StaticProcessRunner(ProcessResult(exit_code=0, stdout=payload, stderr=b"")))

    result = asyncio.run(adapter.compare("base.yaml", "head.yaml", 1024 * 1024))

    assert result.candidates == ()
    assert result.tool.exit_code == 0


def test_adapter_error_does_not_expose_stderr_secret() -> None:
    """工具错误不得把 raw stderr 或 Secret 写入异常."""
    secret = b"Bearer guardian-secret-marker"
    adapter = OasdiffAdapter(runner=StaticProcessRunner(ProcessResult(exit_code=102, stdout=b"", stderr=secret)))

    with pytest.raises(GuardianException) as captured:
        asyncio.run(adapter.compare("base.yaml", "head.yaml", 1024 * 1024))

    assert captured.value.code == GuardianErrorCode.OASDIFF_EXECUTION_FAILED
    assert "guardian-secret-marker" not in str(captured.value)


def test_adapter_rejects_invalid_json_without_echoing_output() -> None:
    """解析失败时不得回显 raw stdout."""
    marker = "raw-output-secret-marker"
    adapter = OasdiffAdapter(
        runner=StaticProcessRunner(ProcessResult(exit_code=0, stdout=marker.encode(), stderr=b""))
    )

    with pytest.raises(GuardianException) as captured:
        asyncio.run(adapter.compare("base.yaml", "head.yaml", 1024 * 1024))

    assert captured.value.code == GuardianErrorCode.OASDIFF_INVALID_OUTPUT
    assert marker not in str(captured.value)
