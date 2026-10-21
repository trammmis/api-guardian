"""Stage E2.5 Miss Analysis 命令行入口。."""

import argparse
import asyncio
from pathlib import Path

from rich.console import Console

from benchmarks.guardian.miss_analysis import run_miss_analysis


def main() -> None:
    """运行逐 Case 分析并显示关键计数。."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oasdiff-path", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run_miss_analysis(oasdiff_path=args.oasdiff_path))
    Console().print_json(data=result)


if __name__ == "__main__":
    main()
