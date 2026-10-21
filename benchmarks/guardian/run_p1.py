"""运行 P1 removed-operation no-fallback ablation。."""

from benchmarks.guardian.p1_runner import print_summary, run_p1


def main() -> None:
    """执行冻结 P1 实验并输出 Rich 摘要。."""
    print_summary(run_p1())


if __name__ == "__main__":
    main()
