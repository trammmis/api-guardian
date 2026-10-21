"""Stage E2.5 Scaling / Break-even Benchmark 命令行入口。."""

import argparse
import asyncio
from pathlib import Path

from rich.console import Console

from benchmarks.guardian.e2_5_models import ScalingConfig
from benchmarks.guardian.scaling_runner import SCALING_REPORT_ROOT, ScalingBenchmarkRunner


def main() -> None:
    """解析固定工具路径并执行 smoke 或正式 Scaling run。."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oasdiff-path", type=Path, required=True)
    parser.add_argument("--schemathesis-path", type=Path, required=True)
    parser.add_argument("--oasdiff-archive-sha256")
    parser.add_argument("--smoke", action="store_true", help="仅跑 8 operations、5 repetitions 到独立目录")
    args = parser.parse_args()
    config = ScalingConfig(sizes=(8,), repetitions=5) if args.smoke else ScalingConfig()
    report_root = SCALING_REPORT_ROOT / "smoke" if args.smoke else SCALING_REPORT_ROOT
    runner = ScalingBenchmarkRunner(
        oasdiff_path=args.oasdiff_path,
        schemathesis_path=args.schemathesis_path,
        config=config,
        report_root=report_root,
        oasdiff_archive_sha256=args.oasdiff_archive_sha256,
    )
    result = asyncio.run(runner.run())
    Console().print_json(data=result)


if __name__ == "__main__":
    main()
