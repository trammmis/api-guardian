"""E3-B 真实世界 Contract/Scope System Evaluation 的审计模型."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class E3MissClassification(StrEnum):
    """用户冻结的 E3-B miss taxonomy."""

    OASDIFF_FALSE_NEGATIVE = "OASDIFF_FALSE_NEGATIVE"
    OASDIFF_FALSE_POSITIVE = "OASDIFF_FALSE_POSITIVE"
    GUARDIAN_NORMALIZATION_ERROR = "GUARDIAN_NORMALIZATION_ERROR"
    GUARDIAN_SCOPE_FALSE_NEGATIVE = "GUARDIAN_SCOPE_FALSE_NEGATIVE"
    GUARDIAN_SCOPE_OVER_SELECTION = "GUARDIAN_SCOPE_OVER_SELECTION"
    REMOVED_OPERATION_NON_EXECUTABLE = "REMOVED_OPERATION_NON_EXECUTABLE"
    COMPONENT_MAPPING_LIMITATION = "COMPONENT_MAPPING_LIMITATION"
    GROUND_TRUTH_AMBIGUOUS = "GROUND_TRUTH_AMBIGUOUS"
    OTHER = "OTHER"


class OasdiffChangeEvidence(BaseModel):
    """不含 source path 的 oasdiff Adapter 安全输出."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    summary: str
    severity_level: int
    method: str | None = None
    path: str | None = None
    operation_id: str | None = None
    section: str | None = None
    fingerprint: str | None = None


class GuardianChangeEvidence(BaseModel):
    """Guardian normalization 后的领域变更证据."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    summary: str
    severity: str
    method: str | None = None
    path: str | None = None
    operation_id: str | None = None
    section: str | None = None
    fingerprint: str | None = None


class HeadOperationEvidence(BaseModel):
    """Scope 复核所需的 Head operation 最小映射."""

    model_config = ConfigDict(frozen=True)

    key: str
    operation_id: str | None = None


class E3SystemResult(BaseModel):
    """一个真实 Version Pair 的冻结 deterministic system output."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "guardian-e3-b-system-result-v1"
    run_id: str
    measured_at: datetime
    case_id: str
    repo: str
    base_commit: str
    head_commit: str
    duration_seconds: float = Field(ge=0)
    base_spec_sha256: str
    head_spec_sha256: str
    base_operation_count: int = Field(ge=0)
    head_operation_count: int = Field(ge=0)
    oasdiff_exit_code: int | None = None
    oasdiff_artifact_bytes: int = Field(default=0, ge=0)
    oasdiff_output: tuple[OasdiffChangeEvidence, ...] = ()
    normalized_changes: tuple[GuardianChangeEvidence, ...] = ()
    guardian_contract_verdict: str
    selected_operations: tuple[str, ...] = ()
    head_operations: tuple[HeadOperationEvidence, ...] = ()
    fallback_to_all: bool = False
    unresolved_change_ids: tuple[str, ...] = ()
    error_code: str | None = None
    safe_error: str | None = None


class E3MissRecord(BaseModel):
    """Ground Truth 与 system output 不一致的逐类可审计记录."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "guardian-e3-b-miss-v1"
    miss_id: str
    case_id: str
    repo: str
    base_commit: str
    head_commit: str
    classification: E3MissClassification
    ground_truth_contract_verdict: str | None
    ground_truth_contract_affected_operations: tuple[str, ...]
    ground_truth_runtime_executable_operations: tuple[str, ...]
    oasdiff_exit_code: int | None
    oasdiff_output: tuple[OasdiffChangeEvidence, ...]
    guardian_contract_verdict: str
    guardian_selected_operations: tuple[str, ...]
    fallback_to_all: bool
    evidence: tuple[str, ...] = Field(min_length=1)
    likely_cause: str
    candidate_product_fix: bool = False
