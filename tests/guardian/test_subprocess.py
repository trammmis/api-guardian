"""受控异步 subprocess 的离线测试."""

import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.subprocess import ControlledSubprocessRunner


def test_controlled_subprocess_captures_small_output() -> None:
    """正常命令应返回结构化结果."""
    runner = ControlledSubprocessRunner()
    result = asyncio.run(
        runner.run(
            (sys.executable, "-c", "print('guardian-ok')"),
            timeout_seconds=5,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
            max_total_bytes=2048,
        )
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == b"guardian-ok"


def test_controlled_subprocess_enforces_single_artifact_limit() -> None:
    """Stdout 超限时应终止进程并返回安全错误."""
    runner = ControlledSubprocessRunner()

    with pytest.raises(GuardianException) as captured:
        asyncio.run(
            runner.run(
                (sys.executable, "-c", "import sys; sys.stdout.write('x' * 64)"),
                timeout_seconds=5,
                max_stdout_bytes=16,
                max_stderr_bytes=16,
                max_total_bytes=128,
            )
        )

    assert captured.value.code == GuardianErrorCode.ARTIFACT_TOO_LARGE


def test_controlled_subprocess_enforces_scan_output_budget() -> None:
    """Stdout 与 stderr 共享剩余 Scan artifact 预算."""
    runner = ControlledSubprocessRunner()

    with pytest.raises(GuardianException) as captured:
        asyncio.run(
            runner.run(
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('x' * 12); sys.stderr.write('y' * 12)",
                ),
                timeout_seconds=5,
                max_stdout_bytes=32,
                max_stderr_bytes=32,
                max_total_bytes=16,
            )
        )

    assert captured.value.code == GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED


def test_controlled_subprocess_enforces_timeout() -> None:
    """超时进程必须被终止且错误不包含 argv."""
    marker = "guardian-timeout-secret-marker"
    runner = ControlledSubprocessRunner()

    with pytest.raises(GuardianException) as captured:
        asyncio.run(
            runner.run(
                (sys.executable, "-c", "import time; time.sleep(1)", marker),
                timeout_seconds=0.02,
                max_stdout_bytes=1024,
                max_stderr_bytes=1024,
                max_total_bytes=2048,
            )
        )

    assert captured.value.code == GuardianErrorCode.SUBPROCESS_TIMEOUT
    assert marker not in str(captured.value)


def test_controlled_subprocess_monitors_external_report_while_running() -> None:
    """外部工具写入 report path 时必须在运行中执行单 artifact 限制."""
    runner = ControlledSubprocessRunner()
    with TemporaryDirectory(prefix="guardian-report-monitor-") as temp_dir:
        report_path = Path(temp_dir) / "report.ndjson"
        report_path.touch()

        with pytest.raises(GuardianException) as captured:
            asyncio.run(
                runner.run(
                    (
                        sys.executable,
                        "-c",
                        "import sys,time; open(sys.argv[1], 'wb').write(b'x' * 64); time.sleep(1)",
                        str(report_path),
                    ),
                    timeout_seconds=5,
                    max_stdout_bytes=1024,
                    max_stderr_bytes=1024,
                    max_total_bytes=2048,
                    monitored_artifact_path=report_path,
                    max_monitored_artifact_bytes=16,
                )
            )

    assert captured.value.code == GuardianErrorCode.ARTIFACT_TOO_LARGE


def test_controlled_subprocess_report_shares_scan_budget_with_stdout() -> None:
    """NDJSON 文件与 terminal stdout 必须共同消费 Scan 剩余预算."""
    runner = ControlledSubprocessRunner()
    with TemporaryDirectory(prefix="guardian-report-budget-") as temp_dir:
        report_path = Path(temp_dir) / "report.ndjson"
        report_path.touch()

        with pytest.raises(GuardianException) as captured:
            asyncio.run(
                runner.run(
                    (
                        sys.executable,
                        "-c",
                        "import sys; open(sys.argv[1], 'wb').write(b'x' * 12); sys.stdout.write('y' * 12)",
                        str(report_path),
                    ),
                    timeout_seconds=5,
                    max_stdout_bytes=32,
                    max_stderr_bytes=32,
                    max_total_bytes=16,
                    monitored_artifact_path=report_path,
                    max_monitored_artifact_bytes=32,
                )
            )

    assert captured.value.code == GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED


def test_controlled_subprocess_monitors_tool_side_artifact_directory() -> None:
    """Schemathesis crash cache 等副产物也必须计入受监控 artifact."""
    runner = ControlledSubprocessRunner()
    with TemporaryDirectory(prefix="guardian-side-artifact-") as temp_dir:
        artifact_dir = Path(temp_dir)

        with pytest.raises(GuardianException) as captured:
            asyncio.run(
                runner.run(
                    (
                        sys.executable,
                        "-c",
                        (
                            "import os,sys,time; "
                            "open(os.path.join(sys.argv[1], 'report.ndjson'), 'wb').write(b'x' * 12); "
                            "os.makedirs(os.path.join(sys.argv[1], '.schemathesis')); "
                            "open(os.path.join(sys.argv[1], '.schemathesis', 'crash.json'), 'wb').write(b'y' * 12); "
                            "time.sleep(1)"
                        ),
                        str(artifact_dir),
                    ),
                    timeout_seconds=5,
                    max_stdout_bytes=1024,
                    max_stderr_bytes=1024,
                    max_total_bytes=2048,
                    monitored_artifact_path=artifact_dir,
                    max_monitored_artifact_bytes=16,
                )
            )

    assert captured.value.code == GuardianErrorCode.ARTIFACT_TOO_LARGE
