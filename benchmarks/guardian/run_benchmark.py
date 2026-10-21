"""API Guardian Effectiveness Benchmark Stage E1 命令行入口。."""

import argparse
import asyncio
import shutil
from pathlib import Path

from rich.console import Console

from benchmarks.guardian.config import BenchmarkConfig, LATEST_REPORT_ROOT
from benchmarks.guardian.runner import BenchmarkRunner


def _parser() -> argparse.ArgumentParser:
    """构造不接受 Guardian 产品参数注入的 Benchmark CLI。."""
    parser = argparse.ArgumentParser(description="Run API Guardian Effectiveness Benchmark Stage E1")
    parser.add_argument("--oasdiff-path", type=Path, help="verified oasdiff 1.28.0 executable")
    parser.add_argument("--schemathesis-path", type=Path, help="Schemathesis 4.24.3 st executable")
    parser.add_argument("--oasdiff-archive-sha256", help="optional audited release archive SHA256")
    parser.add_argument("--report-root", type=Path, default=LATEST_REPORT_ROOT)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260903)
    return parser


def _resolve_executable(value: Path | None, default_name: str) -> Path:
    """解析显式路径或 PATH 中的工具，并确定性拒绝缺失工具。."""
    if value is not None:
        if not value.is_file():
            raise FileNotFoundError(f"executable not found: {value}")
        return value
    resolved = shutil.which(default_name)
    if resolved is None:
        raise FileNotFoundError(f"executable not found on PATH: {default_name}")
    return Path(resolved)


def main() -> None:
    """运行 E1 并打印只含聚合结果的 Rich JSON。."""
    args = _parser().parse_args()
    console = Console()
    runner = BenchmarkRunner(
        oasdiff_path=_resolve_executable(args.oasdiff_path, "oasdiff"),
        schemathesis_path=_resolve_executable(args.schemathesis_path, "st"),
        config=BenchmarkConfig(
            random_seed=args.seed,
            repetitions=args.repetitions,
            warmup_runs=args.warmup_runs,
        ),
        report_root=args.report_root,
        oasdiff_archive_sha256=args.oasdiff_archive_sha256,
        console=console,
    )
    summary = asyncio.run(runner.run())
    console.print_json(data=summary)


if __name__ == "__main__":
    main()
