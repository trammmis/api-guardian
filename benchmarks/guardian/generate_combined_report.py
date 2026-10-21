"""生成 E1/E2 综合实验报告的命令行入口。."""

from rich.console import Console

from benchmarks.guardian.combined_reporting import generate_combined_report


def main() -> None:
    """生成综合 JSON、CSV、Markdown 并显示输出位置。."""
    report_path = generate_combined_report()
    Console().print(f"[green]Combined report generated:[/green] {report_path}")


if __name__ == "__main__":
    main()
