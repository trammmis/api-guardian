"""E3 真实世界 Ground Truth 审查与冻结清单模型。."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewStatus(StrEnum):
    """真实历史版本对的独立审查状态。."""

    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    AMBIGUOUS = "ambiguous"
    EXCLUDED = "excluded"


class ReviewConfidence(StrEnum):
    """审查证据的可信度。."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ContractVerdict(StrEnum):
    """Ground Truth 的契约判定；不复用 Guardian 生产模型以保持独立。."""

    PASS = "pass"
    FAIL = "fail"


class BreakingChangeEvidence(BaseModel):
    """一项由 OpenAPI 历史差异直接支持的 breaking change。."""

    model_config = ConfigDict(frozen=True)

    change_type: str
    operation: str | None = None
    schema_name: str | None = None
    base_fact: str
    head_fact: str
    compatibility_reason: str


class PreSystemReviewRecord(BaseModel):
    """在运行 oasdiff / Guardian 之前冻结的单个 Case 审查记录。."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "guardian-e3-b-pre-system-review-v1"
    case_id: str = Field(pattern=r"^rw_[a-z0-9_]+$")
    repo: str
    license: str
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    head_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    base_spec_path: str
    head_spec_path: str
    base_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    head_spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_spec_bytes: int = Field(ge=1, le=5 * 1024 * 1024)
    head_spec_bytes: int = Field(ge=1, le=5 * 1024 * 1024)
    base_openapi_version: str = Field(pattern=r"^3\.")
    head_openapi_version: str = Field(pattern=r"^3\.")
    base_operation_count: int = Field(ge=1)
    head_operation_count: int = Field(ge=1)
    commit_date_head: str
    commit_message_head: str
    reviewer: str = "ai_assisted_initial_review"
    review_status: ReviewStatus
    review_confidence: ReviewConfidence
    change_types: list[str] = Field(min_length=1)
    ground_truth_contract_verdict: ContractVerdict | None
    breaking_changes: list[BreakingChangeEvidence]
    contract_affected_operations: list[str]
    runtime_executable_operations: list[str]
    evidence: list[str] = Field(min_length=1)
    ambiguity_reason: str | None = None
    notes: list[str] = Field(default_factory=list)
    external_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_review_semantics(self) -> "PreSystemReviewRecord":
        """阻止 headline 与 ambiguous/excluded 语义互相污染。."""
        if self.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.PROBABLE}:
            if self.ground_truth_contract_verdict is None:
                raise ValueError("headline review requires a contract verdict")
            if self.review_confidence == ReviewConfidence.LOW:
                raise ValueError("headline review cannot use low confidence")
        elif self.ground_truth_contract_verdict is not None:
            raise ValueError("ambiguous/excluded review must not force a verdict")

        contract_scope = set(self.contract_affected_operations)
        executable_scope = set(self.runtime_executable_operations)
        if not executable_scope <= contract_scope:
            raise ValueError("runtime executable scope must be a contract scope subset")
        if self.external_refs:
            raise ValueError("E3-B eligible records cannot contain external refs")
        if self.ground_truth_contract_verdict == ContractVerdict.PASS and contract_scope:
            raise ValueError("compatible cases must not define breaking affected scope")
        if self.ground_truth_contract_verdict == ContractVerdict.FAIL and not contract_scope:
            raise ValueError("breaking cases require a contract affected scope")
        return self


class RepositorySnapshot(BaseModel):
    """获选仓库在 E3-B 开始时的固定 revision 元数据。."""

    model_config = ConfigDict(frozen=True)

    repo: str
    remote_url: str
    default_branch: str
    clone_timestamp: str
    clone_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    local_checkout_mode: str
    spec_path: str
    selected_pairs: int = Field(ge=1)
    runtime_candidate: bool


class FrozenArtifact(BaseModel):
    """Ground Truth freeze 覆盖的一个 artifact。."""

    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=1)


class GroundTruthFreezeManifest(BaseModel):
    """证明 Ground Truth 先于任何 Guardian System Output 的冻结清单。."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "guardian-e3-b-ground-truth-freeze-v1"
    stage: str = "E3-B Phase GT"
    frozen_at: str
    reviewer_basis: str
    material_passport: dict[str, str]
    repositories: list[RepositorySnapshot]
    review_artifacts: list[FrozenArtifact]
    aggregate_review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_counts: dict[str, int]
    repository_pair_counts: dict[str, int]
    production_core_hashes: dict[str, str]
    frozen_prior_artifact_hashes: dict[str, str]
    guardian_system_output_status: str
    guardian_output_paths_absent_at_freeze: list[str]
    guardian_execution_permitted: bool = False
