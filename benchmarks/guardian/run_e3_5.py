"""运行 E3.5 只读根因诊断."""

import argparse
from pathlib import Path

from rich.console import Console

from benchmarks.guardian.e3_5_diagnostic import run_diagnostic


def parser() -> argparse.ArgumentParser:
    """构造 E3.5 CLI 参数."""
    value = argparse.ArgumentParser(description="Run API Guardian E3.5 root-cause diagnostic")
    value.add_argument("--oasdiff-1-28", required=True, type=Path)
    value.add_argument("--oasdiff-1-27", required=True, type=Path)
    return value


def main() -> None:
    """执行诊断并用 Rich 输出结果位置."""
    args = parser().parse_args()
    summary = run_diagnostic(args.oasdiff_1_28, args.oasdiff_1_27)
    Console().print(
        "[green]E3.5 complete[/green] "
        f"root_causes={summary['root_cause_count']} "
        f"fallback_extra_operations={summary['supplemental_metrics']['fallback_over_selection_operations']}"
    )


if __name__ == "__main__":
    main()
