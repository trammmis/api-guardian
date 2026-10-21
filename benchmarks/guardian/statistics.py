"""无第三方统计依赖的透明描述统计函数。."""

import math
import statistics
from collections.abc import Sequence

from benchmarks.guardian.models import DescriptiveStatistics


def percentile(values: Sequence[float], probability: float) -> float | None:
    """使用线性插值计算百分位；空样本返回 None。."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * probability
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def describe(values: Sequence[float]) -> DescriptiveStatistics:
    """返回 sample size、median、p95、mean 与 population std。."""
    if not values:
        return DescriptiveStatistics(sample_size=0, median=None, p95=None, mean=None, std=None)
    return DescriptiveStatistics(
        sample_size=len(values),
        median=statistics.median(values),
        p95=percentile(values, 0.95),
        mean=statistics.fmean(values),
        std=statistics.pstdev(values),
    )
