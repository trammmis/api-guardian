"""Stage E1 Benchmark 的固定配置与路径。."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

BENCHMARK_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_ROOT.parents[1]
DATASET_ROOT = BENCHMARK_ROOT / "datasets"
REPORT_ROOT = BENCHMARK_ROOT / "reports"
LATEST_REPORT_ROOT = REPORT_ROOT / "latest"
E2_LATEST_REPORT_ROOT = REPORT_ROOT / "e2" / "latest"

DEFAULT_RANDOM_SEED = 20260903
DEFAULT_REPETITIONS = 5
DEFAULT_WARMUP_RUNS = 1


class BenchmarkConfig(BaseModel):
    """一次 Benchmark 的可审计运行配置。."""

    model_config = ConfigDict(frozen=True)

    random_seed: int = DEFAULT_RANDOM_SEED
    repetitions: int = Field(default=DEFAULT_REPETITIONS, ge=5)
    warmup_runs: int = Field(default=DEFAULT_WARMUP_RUNS, ge=1)
    request_timeout_seconds: float = Field(default=2.0, gt=0)
    subprocess_timeout_seconds: float = Field(default=30.0, gt=0)
    max_examples: int = Field(default=1, ge=1)
    max_artifact_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_scan_artifact_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    schemathesis_check: str = "response_schema_conformance"
    schemathesis_phases: str = "examples,coverage"
    generation_deterministic: bool = True
    max_redirects: int = 0
