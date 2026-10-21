"""不经过 shell 的受控异步子进程执行器."""

import asyncio
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.services.guardian.errors import GuardianErrorCode, GuardianException

READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProcessResult:
    """仅供 Adapter 短暂消费的原始进程结果."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    monitored_artifact_bytes: int = 0

    @property
    def artifact_bytes(self) -> int:
        """返回 stdout 与 stderr 的总字节数."""
        return len(self.stdout) + len(self.stderr) + self.monitored_artifact_bytes


class ProcessRunner(Protocol):
    """便于 Adapter 离线测试的最小执行协议."""

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
        """运行受控子进程并返回临时结果."""
        ...


@dataclass
class _SharedOutputBudget:
    """两个输出流共享的扫描剩余预算."""

    limit: int
    consumed: int = 0

    def consume(self, size: int) -> None:
        """原子地消费事件循环内的共享预算."""
        self.consumed += size
        if self.consumed > self.limit:
            raise GuardianException(
                GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED,
                "单次 Scan artifact 总量超过配置限制",
            )


class ControlledSubprocessRunner:
    """使用 create_subprocess_exec 且流式限制输出大小."""

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
        """运行命令；不记录 argv、环境变量或原始输出."""
        running_loop = asyncio.get_running_loop()
        if sys.platform == "win32" and isinstance(running_loop, asyncio.SelectorEventLoop):
            # psycopg async 在 Windows 要求 Selector，而 asyncio pipe 要求 Proactor；
            # HTTP 模式只把受控子进程协程放入专用 Proactor 线程。
            return await asyncio.to_thread(
                self._run_on_windows_proactor,
                tuple(command),
                timeout_seconds=timeout_seconds,
                max_stdout_bytes=max_stdout_bytes,
                max_stderr_bytes=max_stderr_bytes,
                max_total_bytes=max_total_bytes,
                cwd=cwd,
                env=env,
                monitored_artifact_path=monitored_artifact_path,
                max_monitored_artifact_bytes=max_monitored_artifact_bytes,
            )
        return await self._run_async(
            command,
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=max_stdout_bytes,
            max_stderr_bytes=max_stderr_bytes,
            max_total_bytes=max_total_bytes,
            cwd=cwd,
            env=env,
            monitored_artifact_path=monitored_artifact_path,
            max_monitored_artifact_bytes=max_monitored_artifact_bytes,
        )

    def _run_on_windows_proactor(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
        max_total_bytes: int,
        cwd: Path | None,
        env: Mapping[str, str] | None,
        monitored_artifact_path: Path | None,
        max_monitored_artifact_bytes: int | None,
    ) -> ProcessResult:
        """在专用 Windows Proactor loop 中执行同一受控异步实现."""
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
            return runner.run(
                self._run_async(
                    command,
                    timeout_seconds=timeout_seconds,
                    max_stdout_bytes=max_stdout_bytes,
                    max_stderr_bytes=max_stderr_bytes,
                    max_total_bytes=max_total_bytes,
                    cwd=cwd,
                    env=env,
                    monitored_artifact_path=monitored_artifact_path,
                    max_monitored_artifact_bytes=max_monitored_artifact_bytes,
                )
            )

    async def _run_async(
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
        """在当前兼容事件循环执行 create_subprocess_exec 和预算监控."""
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise GuardianException(GuardianErrorCode.INVALID_INPUT, "子进程命令无效")
        if min(timeout_seconds, max_stdout_bytes, max_stderr_bytes, max_total_bytes) <= 0:
            raise GuardianException(GuardianErrorCode.INVALID_INPUT, "子进程限制必须为正数")
        if monitored_artifact_path is not None and (
            max_monitored_artifact_bytes is None or max_monitored_artifact_bytes <= 0
        ):
            raise GuardianException(GuardianErrorCode.INVALID_INPUT, "受监控 artifact 限制必须为正数")

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=dict(env) if env is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise GuardianException(GuardianErrorCode.SUBPROCESS_START_FAILED, "外部工具启动失败") from exc

        budget = _SharedOutputBudget(max_total_bytes)
        stdout_task = asyncio.create_task(self._read_limited(process.stdout, max_stdout_bytes, budget))
        stderr_task = asyncio.create_task(self._read_limited(process.stderr, max_stderr_bytes, budget))
        wait_task = asyncio.create_task(process.wait())
        artifact_task: asyncio.Task[int] | None = None
        tasks: list[asyncio.Task[Any]] = [stdout_task, stderr_task, wait_task]
        if monitored_artifact_path is not None and max_monitored_artifact_bytes is not None:
            artifact_task = asyncio.create_task(
                self._watch_artifact(
                    process,
                    monitored_artifact_path,
                    max_monitored_artifact_bytes,
                    budget,
                )
            )
            tasks.append(artifact_task)
        group = asyncio.gather(*tasks)

        try:
            done, _ = await asyncio.wait({group}, timeout=timeout_seconds)
        except asyncio.CancelledError:
            await self._kill(process, tasks)
            await asyncio.gather(group, return_exceptions=True)
            raise
        if group not in done:
            await self._kill(process, tasks)
            await asyncio.gather(group, return_exceptions=True)
            raise GuardianException(GuardianErrorCode.SUBPROCESS_TIMEOUT, "外部工具执行超时")
        try:
            group.result()
        except GuardianException:
            await self._kill(process, tasks)
            raise
        except asyncio.CancelledError:
            await self._kill(process, tasks)
            raise

        # Windows Proactor 需要一个事件循环 tick 完成 pipe transport 的关闭回调。
        await asyncio.sleep(0)
        return ProcessResult(
            exit_code=wait_task.result(),
            stdout=stdout_task.result(),
            stderr=stderr_task.result(),
            monitored_artifact_bytes=artifact_task.result() if artifact_task is not None else 0,
        )

    async def _read_limited(
        self,
        stream: asyncio.StreamReader | None,
        stream_limit: int,
        budget: _SharedOutputBudget,
    ) -> bytes:
        """逐块读取单个输出流并执行单 artifact 与总量限制."""
        if stream is None:
            return b""
        chunks: list[bytes] = []
        consumed = 0
        while chunk := await stream.read(READ_CHUNK_BYTES):
            consumed += len(chunk)
            if consumed > stream_limit:
                raise GuardianException(GuardianErrorCode.ARTIFACT_TOO_LARGE, "单个工具 artifact 超过配置限制")
            budget.consume(len(chunk))
            chunks.append(chunk)
        return b"".join(chunks)

    async def _watch_artifact(
        self,
        process: asyncio.subprocess.Process,
        artifact_path: Path,
        artifact_limit: int,
        budget: _SharedOutputBudget,
    ) -> int:
        """运行中监控外部工具写入的文件，并共享 Scan 总预算."""
        last_size = 0
        while process.returncode is None:
            current_size = self._artifact_size(artifact_path, artifact_limit)
            if current_size > last_size:
                budget.consume(current_size - last_size)
            last_size = current_size
            await asyncio.sleep(0.01)

        final_size = self._artifact_size(artifact_path, artifact_limit)
        if final_size > last_size:
            budget.consume(final_size - last_size)
        return final_size

    def _artifact_size(self, artifact_path: Path, artifact_limit: int) -> int:
        """计算文件或目录树大小；目录内任一 symlink 都视为不安全 artifact."""
        if not artifact_path.exists():
            return 0
        if artifact_path.is_file():
            size = artifact_path.stat().st_size
            if size > artifact_limit:
                raise GuardianException(GuardianErrorCode.ARTIFACT_TOO_LARGE, "单个工具 artifact 超过配置限制")
            return size

        total = 0
        stack = [artifact_path]
        while stack:
            directory = stack.pop()
            try:
                children = tuple(directory.iterdir())
            except FileNotFoundError:
                continue
            for child in children:
                if child.is_symlink():
                    raise GuardianException(GuardianErrorCode.ARTIFACT_TOO_LARGE, "工具 artifact 包含不安全链接")
                if child.is_dir():
                    stack.append(child)
                    continue
                try:
                    total += child.stat().st_size
                except FileNotFoundError:
                    continue
                if total > artifact_limit:
                    raise GuardianException(
                        GuardianErrorCode.ARTIFACT_TOO_LARGE,
                        "单个工具 artifact 超过配置限制",
                    )
        return total

    async def _kill(
        self,
        process: asyncio.subprocess.Process,
        tasks: list[asyncio.Task[Any]],
    ) -> None:
        """终止进程并回收所有读取任务."""
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
