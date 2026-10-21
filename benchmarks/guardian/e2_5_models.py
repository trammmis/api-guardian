"""Stage E2.5 的 Miss Analysis 与 Scaling 领域模型。."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.guardian.models import GuardianVerdict
from benchmarks.guardian.models import BenchmarkSystem, ReproducibilityMetadata


class MissClassification(StrEnum):
    """Contract / Scope miss 的固定分类枚举。."""

    TRUE_SCOPE_FALSE_NEGATIVE = "true_scope_false_negative"
    TRUE_SCOPE_FALSE_POSITIVE = "true_scope_false_positive"
    FALLBACK_TO_ALL_OVER_SELECTION = "fallback_to_all_over_selection"
    REMOVED_OPERATION_NOT_EXECUTABLE = "removed_operation_not_executable"
    CONTRACT_VS_RUNTIME_SCOPE_MISMATCH = "contract_vs_runtime_scope_mismatch"
    OASDIFF_GROUND_TRUTH_DISAGREEMENT = "oasdiff_ground_truth_disagreement"
    COMPONENT_CHANGE_ATTRIBUTION_LIMITATION = "component_change_attribution_limitation"
    OPERATION_ID_MAPPING_LIMITATION = "operation_id_mapping_limitation"
    DATASET_GROUND_TRUTH_ERROR = "dataset_ground_truth_error"
    OTHER = "other"


class MissAnalysisRecord(BaseModel):
    """一个 E2 Contract / Scope miss 的派生证据记录。."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    change_type: str
    base_operations: tuple[str, ...]
    head_operations: tuple[str, ...]
    ground_truth_contract_verdict: GuardianVerdict
    guardian_contract_verdict: GuardianVerdict
    ground_truth_contract_affected_operations: tuple[str, ...]
    ground_truth_runtime_executable_operations: tuple[str, ...]
    guardian_selected_operations: tuple[str, ...]
    oasdiff_change_ids: tuple[str, ...]
    oasdiff_paths: tuple[str, ...]
    oasdiff_operation_ids: tuple[str, ...]
    fallback_to_all: bool
    classification: MissClassification
    primary_cause: str
    secondary_causes: tuple[MissClassification, ...] = ()
    is_true_guardian_scope_miss: bool
    is_ground_truth_definition_issue: bool
    is_non_executable_removed_operation: bool
    notes: str


class ScalingScenario(StrEnum):
    """Scaling Benchmark 的 affected-scope 场景。."""

    ONE_OPERATION = "s1_one_operation"
    FIVE_PERCENT = "s2_five_percent"


class ScalingCase(BaseModel):
    """由 generator 产生的一个可重复 Scaling Case。."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    total_operations: int = Field(ge=1)
    scenario: ScalingScenario
    affected_operations: tuple[str, ...] = Field(min_length=1)
    base_spec: str
    head_spec: str
    generator_version: str

    @model_validator(mode="after")
    def validate_affected_scope(self) -> "ScalingCase":
        """受影响 operation 数不能超过 API 总规模。."""
        if len(self.affected_operations) > self.total_operations:
            raise ValueError("affected operations exceed total operations")
        return self


class TimingSource(StrEnum):
    """明确区分端到端测量与 Benchmark wrapper 分解。."""

    END_TO_END_WRAPPER_ONLY = "end_to_end_wrapper_only"
    WRAPPER_COMPONENT_BOUNDARIES = "wrapper_component_boundaries"


class ScalingMeasurement(BaseModel):
    """单个 scale/scenario/system/repetition 的原始测量。."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    measured_at: datetime
    case_id: str
    total_operations: int = Field(ge=1)
    affected_operations: int = Field(ge=1)
    scenario: ScalingScenario
    system: BenchmarkSystem
    repetition: int = Field(ge=0)
    warmup: bool
    total_duration_seconds: float = Field(ge=0)
    oasdiff_duration_seconds: float | None = Field(default=None, ge=0)
    scope_selection_duration_seconds: float | None = Field(default=None, ge=0)
    schemathesis_duration_seconds: float | None = Field(default=None, ge=0)
    report_orchestration_duration_seconds: float | None = Field(default=None, ge=0)
    timing_source: TimingSource
    process_count: int = Field(ge=0)
    operations_tested: int = Field(ge=0)
    http_requests_issued: int = Field(ge=0)
    selected_operations: tuple[str, ...] = ()
    contract_verdict: GuardianVerdict | None = None
    regression_verdict: GuardianVerdict | None = None
    final_verdict: GuardianVerdict | None = None
    error_codes: tuple[str, ...] = ()
    metadata: ReproducibilityMetadata


class ScalingConfig(BaseModel):
    """Scaling 资源保护和固定实验参数。."""

    model_config = ConfigDict(frozen=True)

    sizes: tuple[int, ...] = (8, 16, 32, 64, 128)
    repetitions: int = Field(default=10, ge=5)
    warmup_runs: int = Field(default=1, ge=1)
    random_seed: int = 20260906
    max_scale_operations: int = Field(default=128, ge=8)
    max_case_runtime_seconds: float = Field(default=180.0, gt=0)
    total_benchmark_budget_seconds: float = Field(default=10800.0, gt=0)
    request_timeout_seconds: float = Field(default=2.0, gt=0)
    subprocess_timeout_seconds: float = Field(default=60.0, gt=0)
    max_examples: int = Field(default=1, ge=1)
    max_artifact_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_scan_artifact_bytes: int = Field(default=100 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def validate_sizes(self) -> "ScalingConfig":
        """规模必须递增、唯一并处于资源上限内。."""
        if not self.sizes or tuple(sorted(set(self.sizes))) != self.sizes:
            raise ValueError("scaling sizes must be unique and increasing")
        if self.sizes[-1] > self.max_scale_operations:
            raise ValueError("scaling size exceeds max_scale_operations")
        return self
