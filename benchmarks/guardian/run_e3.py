"""运行 E3-B Contract/Scope System Evaluation."""

import argparse
import asyncio
from pathlib import Path

from rich.console import Console

from benchmarks.guardian.e3_system_evaluation import run_e3_system_evaluation


def parser() -> argparse.ArgumentParser:
    """构造 E3-B CLI 参数."""
    value = argparse.ArgumentParser(description="Run frozen E3-B contract/scope evaluation")
    value.add_argument("--oasdiff-path", required=True, type=Path)
    return value


def main() -> None:
    """执行 E3-B System Evaluation."""
    args = parser().parse_args()
    summary = asyncio.run(run_e3_system_evaluation(args.oasdiff_path))
    metric = summary["metrics"]["contract_verdict_accuracy"]
    Console().print(
        "[green]ContractVerdictAccuracy[/green] "
        f"{metric['numerator']}/{metric['denominator']} ({metric['percentage']:.2f}%)"
    )


if __name__ == "__main__":
    main()
