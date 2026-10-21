"""Stage E2.5 派生分析、generator 与统计判定测试。."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.services.guardian.models import GuardianVerdict
from benchmarks.guardian.config import PROJECT_ROOT, REPORT_ROOT
from benchmarks.guardian.e2_5_models import (
    ScalingCase,
    ScalingConfig,
    ScalingMeasurement,
    ScalingScenario,
    TimingSource,
)
from benchmarks.guardian.models import BenchmarkSystem, ReproducibilityMetadata
from benchmarks.guardian.scaling_dataset import affected_indexes, ensure_scaling_dataset
from benchmarks.guardian.scaling_metrics import _bootstrap_median_ci, _break_even
from benchmarks.guardian.suts.scaling_api import create_scaling_app


def test_scaling_generator_has_required_sizes_and_scopes() -> None:
    """正式 Dataset 必须含 8--128 与 S1/S2，并记录实际 affected 数。."""
    cases = ensure_scaling_dataset(ScalingConfig())
    assert len(cases) == 10
    assert {case.total_operations for case in cases} == {8, 16, 32, 64, 128}
    expected = {8: 1, 16: 1, 32: 2, 64: 3, 128: 6}
    for case in cases:
        expected_count = 1 if case.scenario == ScalingScenario.ONE_OPERATION else expected[case.total_operations]
        assert len(case.affected_operations) == expected_count
        document = json.loads((PROJECT_ROOT / case.head_spec).read_text(encoding="utf-8"))
        assert len(document["paths"]) == case.total_operations


def test_scaling_sut_only_mutates_declared_operations() -> None:
    """SUT 只让 declared target 返回 string，其余 operation 保持 integer。."""
    case = ScalingCase(
        case_id="scale_8_s1_one_operation",
        total_operations=8,
        scenario=ScalingScenario.ONE_OPERATION,
        affected_operations=("GET /resources/0001",),
        base_spec="base.json",
        head_spec="head.json",
        generator_version="test",
    )
    asyncio.run(_assert_scaling_sut(case))


async def _assert_scaling_sut(case: ScalingCase) -> None:
    app = create_scaling_app(8, affected_indexes(case))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        mutated = await client.get("/resources/0001")
        clean = await client.get("/resources/0002")
    assert mutated.json()["value"] == "1"
    assert clean.json()["value"] == 2


def test_bootstrap_is_seeded_and_break_even_requires_two_points() -> None:
    """置信区间可重复，孤立 faster 点不能被宣传成 break-even。."""
    assert _bootstrap_median_ci([1.0, 2.0, 3.0], seed=7) == _bootstrap_median_ci([1.0, 2.0, 3.0], seed=7)
    rows = [
        _break_even_row(8, False),
        _break_even_row(16, True),
        _break_even_row(32, False),
        _break_even_row(64, True),
        _break_even_row(128, True),
    ]
    result = _break_even(rows, ScalingScenario.ONE_OPERATION)
    assert result["observed"] is True
    assert result["start_operations"] == 64
    assert result["confirmed_through_operations"] == 128


def test_miss_analysis_preserves_expected_derived_counts() -> None:
    """Miss 报告必须保留 E2 原指标并显式拆分可执行 Scope。."""
    path = REPORT_ROOT / "e2_5" / "miss_analysis" / "miss_summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["source_miss_count"] == 15
    assert summary["category_counts"]["removed_operation_not_executable"] == 10
    assert summary["category_counts"]["oasdiff_ground_truth_disagreement"] == 5
    assert summary["true_guardian_scope_miss_count"] == 0
    assert summary["metrics"]["contract_impact_recall"]["numerator"] == 40
    assert summary["metrics"]["contract_impact_recall"]["denominator"] == 55
    assert summary["metrics"]["executable_regression_scope_recall"]["numerator"] == 40
    assert summary["metrics"]["executable_regression_scope_recall"]["denominator"] == 45


def test_scaling_measurement_rejects_negative_duration() -> None:
    """Raw measurement 不接受负时延。."""
    with pytest.raises(ValueError):
        ScalingMeasurement(
            run_id="test",
            measured_at=datetime.now(UTC),
            case_id="case",
            total_operations=8,
            affected_operations=1,
            scenario=ScalingScenario.ONE_OPERATION,
            system=BenchmarkSystem.FULL_SCHEMATHESIS,
            repetition=0,
            warmup=False,
            total_duration_seconds=-1,
            timing_source=TimingSource.WRAPPER_COMPONENT_BOUNDARIES,
            process_count=1,
            operations_tested=8,
            http_requests_issued=65,
            regression_verdict=GuardianVerdict.FAIL,
            final_verdict=GuardianVerdict.FAIL,
            metadata=_metadata(),
        )


def _break_even_row(size: int, faster: bool) -> dict[str, object]:
    return {
        "scenario": ScalingScenario.ONE_OPERATION.value,
        "total_operations": size,
        "guardian_vs_full": {"guardian_faster_by_cell_median": faster},
    }


def _metadata() -> ReproducibilityMetadata:
    return ReproducibilityMetadata(
        benchmark_version="test",
        guardian_git_commit="test",
        guardian_worktree_dirty=True,
        python_version="test",
        operating_system="test",
        cpu_logical_count=1,
        random_seed=1,
        oasdiff_version="1.28.0",
        schemathesis_version="4.24.3",
        schemathesis_parameters={},
    )
