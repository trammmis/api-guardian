"""生成 E2.5 Scaling 细化报告与最终总报告。."""

from rich.console import Console

from benchmarks.guardian.e2_5_reporting import generate_e2_5_reports


def main() -> None:
    """生成报告并显示最终文件路径。."""
    Console().print(f"[green]generated[/green] {generate_e2_5_reports()}")


if __name__ == "__main__":
    main()
