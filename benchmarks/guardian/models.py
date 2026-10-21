"""Benchmark Ground Truth、原始测量与聚合结果模型。."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.guardian.models import GuardianVerdict


class MutationGroup(StrEnum):
    """Mutation Case 的研究分组。."""

    SCHEMA_DETECTABLE = "schema_detectable"
    IMPLEMENTATION_LAG = "implementation_lag"
    NEGATIVE_CONTROL = "negative_control"


class BenchmarkSystem(StrEnum):
    """Benchmark 中实际比较的系统。."""

    OASDIFF_ONLY = "oasdiff_only"
    FULL_SCHEMATHESIS = "full_schemathesis"
    FULL_PIPELINE = "oasdiff_plus_full_schemathesis"
    ORACLE_SCOPE = "oracle_scope"
    GUARDIAN_DETERMINISTIC = "guardian_deterministic"


class PatchMode(StrEnum):
    """可审计 OpenAPI mutation 的最小操作集合。."""

    SET = "set"
    ADD = "add"
    DELETE = "delete"
    APPEND_UNIQUE = "append_unique"


class SpecPatch(BaseModel):
    """从 Base Spec 生成 Head Spec 的确定性单点变换。."""

    model_config = ConfigDict(frozen=True)

    path: tuple[str, ...] = Field(min_length=1)
    mode: PatchMode = PatchMode.SET
    value: Any


class MutationCase(BaseModel):
    """一个带人工 Ground Truth 的 Runtime Mutation Case。."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9_]+$")
    sut_id: str = Field(pattern=r"^[a-z0-9_]+$")
    mutation_type: str
    group: MutationGroup
    base_spec: str
    head_spec: str
    target_variant: str
    head_patch: SpecPatch
    selection_patch: SpecPatch | None = None
    ground_truth_affected_operations: tuple[str, ...] = Field(min_length=1)
    ground_truth_bug_present: bool
    expected_detectable_by_current_oracle: bool
    expected_contract_verdict: GuardianVerdict
    reason: str

    @field_validator("ground_truth_affected_operations")
    @classmethod
    def validate_operation_keys(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """要求 Ground Truth 使用 Guardian 的 METHOD PATH 稳定键。."""
        if any(" " not in value or value != value.strip() for value in values):
            raise ValueError("ground truth operations must use METHOD PATH keys")
        if len(values) != len(set(values)):
            raise ValueError("ground truth operations must be unique")
        return values

    @model_validator(mode="after")
    def validate_negative_control(self) -> "MutationCase":
        """Negative Control 必须明确标记为当前 oracle 不可发现。."""
        if self.group == MutationGroup.NEGATIVE_CONTROL and self.expected_detectable_by_current_oracle:
            raise ValueError("negative controls cannot be marked detectable")
        return self


class ContractCase(BaseModel):
    """一个带人工 Ground Truth 的 Contract Change Case。."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(pattern=r"^[a-z0-9_]+$")
    sut_id: str = Field(pattern=r"^[a-z0-9_]+$")
    change_type: str
    base_spec: str
    head_spec: str
    patches: tuple[SpecPatch, ...] = Field(min_length=1)
    expected_contract_verdict: GuardianVerdict
    expected_affected_operations: tuple[str, ...]
    reason: str

    @field_validator("expected_affected_operations")
    @classmethod
    def validate_expected_operations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """要求非空 operation key 使用 METHOD PATH 形式。."""
        if any(" " not in value or value != value.strip() for value in values):
            raise ValueError("expected operations must use METHOD PATH keys")
        if len(values) != len(set(values)):
            raise ValueError("expected operations must be unique")
        return values


class ReproducibilityMetadata(BaseModel):
    """每条原始测量附带的环境与工具元数据。."""

    model_config = ConfigDict(frozen=True)

    benchmark_version: str
    guardian_git_commit: str
    guardian_worktree_dirty: bool
    python_version: str
    operating_system: str
    cpu_logical_count: int | None
    random_seed: int
    oasdiff_version: str
    oasdiff_archive_sha256: str | None = None
    schemathesis_version: str
    schemathesis_parameters: dict[str, Any]
    deepseek_model: str | None = None


class SystemResult(BaseModel):
    """一个系统对单个 case 的标准化结果。."""

    model_config = ConfigDict(frozen=True)

    contract_verdict: GuardianVerdict | None = None
    regression_verdict: GuardianVerdict | None = None
    final_verdict: GuardianVerdict | None = None
    selected_operations: tuple[str, ...] = ()
    total_operations: int = Field(ge=0)
    operations_tested: int = Field(ge=0)
    bug_detected: bool
    error_codes: tuple[str, ...] = ()


class RawMeasurement(BaseModel):
    """单个 case、system、repetition 的不可聚合原始记录。."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    measured_at: datetime
    case_id: str
    sut_id: str
    system: BenchmarkSystem
    repetition: int = Field(ge=0)
    warmup: bool
    duration_seconds: float = Field(ge=0)
    contract_verdict: GuardianVerdict | None = None
    regression_verdict: GuardianVerdict | None = None
    final_verdict: GuardianVerdict | None = None
    selected_operations: tuple[str, ...] = ()
    total_operations: int = Field(ge=0)
    operations_tested: int = Field(ge=0)
    http_requests_issued: int = Field(ge=0)
    bug_detected: bool
    error_codes: tuple[str, ...] = ()
    metadata: ReproducibilityMetadata


class ContractMeasurement(BaseModel):
    """一个 Contract Case 的真实 oasdiff、normalization 与 scope 结果。."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    measured_at: datetime
    case_id: str
    sut_id: str
    change_type: str
    duration_seconds: float = Field(ge=0)
    expected_contract_verdict: GuardianVerdict
    actual_contract_verdict: GuardianVerdict
    expected_affected_operations: tuple[str, ...]
    selected_operations: tuple[str, ...]
    total_operations: int = Field(ge=0)
    fallback_to_all: bool
    normalized_change_count: int = Field(ge=0)
    error_codes: tuple[str, ...] = ()
    metadata: ReproducibilityMetadata


class DescriptiveStatistics(BaseModel):
    """Runtime 等连续指标的透明描述统计。."""

    model_config = ConfigDict(frozen=True)

    sample_size: int = Field(ge=0)
    median: float | None
    p95: float | None
    mean: float | None
    std: float | None


class RatioMetric(BaseModel):
    """保留 numerator/denominator 的准确率或召回率。."""

    model_config = ConfigDict(frozen=True)

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None


class MissedCase(BaseModel):
    """Guardian 未发现的 mutation 及其透明原因。."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    mutation: str
    affected_operations: tuple[str, ...]
    expected_detectable: bool
    selected_operations: tuple[str, ...]
    why_not_selected_or_detected: str
