"""API Guardian Effectiveness Benchmark Stage E2 命令行入口。."""

import argparse
import asyncio
import shutil
from pathlib import Path

from rich.console import Console

from benchmarks.guardian.config import BenchmarkConfig, E2_LATEST_REPORT_ROOT
from benchmarks.guardian.e2_runner import E2BenchmarkRunner


def _parser() -> argparse.ArgumentParser:
    """构造固定 E2 Benchmark CLI。."""
    parser = argparse.ArgumentParser(description="Run API Guardian Effectiveness Benchmark Stage E2")
    parser.add_argument("--oasdiff-path", type=Path)
    parser.add_argument("--schemathesis-path", type=Path)
    parser.add_argument("--oasdiff-archive-sha256")
    parser.add_argument("--report-root", type=Path, default=E2_LATEST_REPORT_ROOT)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260903)
    return parser


def _resolve(value: Path | None, name: str) -> Path:
    """解析显式路径或 PATH 中工具。."""
    if value is not None and value.is_file():
        return value
    resolved = shutil.which(name) if value is None else None
    if resolved is None:
        raise FileNotFoundError(f"executable not found: {value or name}")
    return Path(resolved)


def main() -> None:
    """执行 E2 并通过 Rich 输出聚合 JSON。."""
    args = _parser().parse_args()
    runner = E2BenchmarkRunner(
        oasdiff_path=_resolve(args.oasdiff_path, "oasdiff"),
        schemathesis_path=_resolve(args.schemathesis_path, "st"),
        config=BenchmarkConfig(
            random_seed=args.seed,
            repetitions=args.repetitions,
            warmup_runs=args.warmup_runs,
        ),
        report_root=args.report_root,
        oasdiff_archive_sha256=args.oasdiff_archive_sha256,
        console=Console(),
    )
    Console().print_json(data=asyncio.run(runner.run()))


if __name__ == "__main__":
    main()
