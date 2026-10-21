"""API Guardian 的领域 DTO."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.guardian.errors import GuardianErrorCode


class ChangeSeverity(StrEnum):
    """业务层使用的变更严重级别."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class GuardianVerdict(StrEnum):
    """确定性的 Guardian 门禁结论."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class RegressionOutcome(StrEnum):
    """单个回归操作或 check 的确定性结果."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class RiskLevel(StrEnum):
    """LLM 解释层风险标签，不参与最终门禁判定."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FailureCategory(StrEnum):
    """LLM 对安全失败证据给出的排查分类."""

    CONTRACT_MISMATCH = "contract_mismatch"
    VALIDATION = "validation"
    SERVER_ERROR = "server_error"
    AUTH = "auth"
    NETWORK = "network"
    BUSINESS_LOGIC = "business_logic"
    UNKNOWN = "unknown"


class OperationRef(BaseModel):
    """OpenAPI operation 的稳定引用."""

    model_config = ConfigDict(frozen=True)

    method: str
    path: str
    operation_id: str | None = None

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        """统一将 HTTP method 规范成大写."""
        return value.upper()

    @property
    def key(self) -> str:
        """返回用于去重和评测的稳定键."""
        return f"{self.method} {self.path}"


class ContractChangeCandidate(BaseModel):
    """Adapter 产生、等待严重级别归一化的安全边界 DTO."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    summary: str
    severity_level: int
    method: str | None = None
    path: str | None = None
    operation_id: str | None = None
    section: str | None = None
    fingerprint: str | None = None

    @field_validator("method")
    @classmethod
    def normalize_optional_method(cls, value: str | None) -> str | None:
        """统一可选 HTTP method 的大小写."""
        return value.upper() if value else None


class ContractChange(BaseModel):
    """不依赖 oasdiff 原始结构的 Contract Change."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    summary: str
    severity: ChangeSeverity
    method: str | None = None
    path: str | None = None
    operation_id: str | None = None
    section: str | None = None
    fingerprint: str | None = None


class ToolRunSummary(BaseModel):
    """允许进入状态的脱敏工具执行摘要."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    exit_code: int
    artifact_bytes: int = Field(ge=0)


class ContractDiffResult(BaseModel):
    """oasdiff Adapter 的安全返回值."""

    model_config = ConfigDict(frozen=True)

    candidates: tuple[ContractChangeCandidate, ...] = ()
    tool: ToolRunSummary


class ScopeSelection(BaseModel):
    """回归测试范围选择结果."""

    model_config = ConfigDict(frozen=True)

    operations: tuple[OperationRef, ...] = ()
    fallback_to_all: bool = False
    unresolved_change_ids: tuple[str, ...] = ()


class RegressionCheck(BaseModel):
    """从 Schemathesis check 白名单字段归一化出的结果."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: RegressionOutcome
    message: str | None = None


class RegressionFailure(BaseModel):
    """不包含请求、响应 body 或 Secret 的安全失败证据."""

    model_config = ConfigDict(frozen=True)

    failure_id: str
    operation: OperationRef | None = None
    check_name: str
    category: str
    safe_message: str
    status_code: int | None = Field(default=None, ge=100, le=599)
    phase: str | None = None


class RegressionResult(BaseModel):
    """单个目标 operation 的稳定回归结果."""

    model_config = ConfigDict(frozen=True)

    operation: OperationRef
    outcome: RegressionOutcome
    checks: tuple[RegressionCheck, ...] = ()
    failures: tuple[RegressionFailure, ...] = ()
    duration_ms: float | None = Field(default=None, ge=0)


class RegressionRunSummary(BaseModel):
    """允许进入 State 的 Schemathesis 聚合摘要."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    tool_version: str
    exit_code: int
    artifact_bytes: int = Field(ge=0)
    total_operations: int = Field(ge=0)
    passed_operations: int = Field(ge=0)
    failed_operations: int = Field(ge=0)
    error_operations: int = Field(ge=0)
    skipped_operations: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_operation_counts(self) -> "RegressionRunSummary":
        """保证各 outcome 计数与目标 operation 总数一致."""
        counted = self.passed_operations + self.failed_operations + self.error_operations + self.skipped_operations
        if counted != self.total_operations:
            raise ValueError("Regression operation counts must equal total_operations")
        return self


class RegressionExecutionResult(BaseModel):
    """Schemathesis Adapter 返回给 Graph node 的安全结果."""

    model_config = ConfigDict(frozen=True)

    results: tuple[RegressionResult, ...] = ()
    tool: RegressionRunSummary


class RiskAnalysisFacts(BaseModel):
    """发送给 LLM 的 ContractChange 白名单事实."""

    model_config = ConfigDict(frozen=True)

    change_id: str
    summary: str
    severity: ChangeSeverity
    method: str | None = None
    path: str | None = None
    operation_id: str | None = None
    section: str | None = None


class RiskExplanation(BaseModel):
    """LLM 对一个已确认 ContractChange 的结构化风险解释."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    change_id: str
    risk_level: RiskLevel
    impact: str = Field(min_length=1, max_length=1000)
    affected_consumer: str = Field(min_length=1, max_length=500)
    recommendation: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)


class FailureDiagnosisFacts(BaseModel):
    """发送给 LLM 的 RegressionFailure 白名单事实与可引用证据."""

    model_config = ConfigDict(frozen=True)

    failure_id: str
    operation: OperationRef | None = None
    check_name: str
    tool_category: str
    safe_message: str
    status_code: int | None = Field(default=None, ge=100, le=599)
    phase: str | None = None
    checks: tuple[RegressionCheck, ...] = Field(default=(), max_length=10)
    related_change: RiskAnalysisFacts | None = None
    risk_explanation: RiskExplanation | None = None
    available_evidence: tuple[str, ...] = Field(min_length=1, max_length=12)


class FailureDiagnosis(BaseModel):
    """LLM 基于有限证据给出的结构化可能原因，而非源码级根因."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    failure_id: str
    category: FailureCategory
    summary: str = Field(min_length=1, max_length=1000)
    possible_cause: str = Field(min_length=1, max_length=1000)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=12)
    recommendation: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("possible_cause")
    @classmethod
    def require_uncertainty_language(cls, value: str) -> str:
        """阻止模型把有限证据包装成已确认源码根因."""
        normalized = value.casefold()
        markers = ("可能", "疑似", "或许", "may", "might", "possible", "possibly", "likely", "suspected")
        if not any(marker in normalized for marker in markers):
            raise ValueError("possible_cause must explicitly express uncertainty")
        return value


class GuardianError(BaseModel):
    """可持久化的脱敏错误."""

    model_config = ConfigDict(frozen=True)

    code: GuardianErrorCode
    node: str
    message: str


class GuardianPolicy(BaseModel):
    """单次扫描使用的非 Secret 策略快照."""

    model_config = ConfigDict(frozen=True)

    allowed_target_hosts: tuple[str, ...] = ()
    allow_private_targets: bool = False
    max_openapi_bytes: int = 5 * 1024 * 1024
    max_artifact_bytes: int = 50 * 1024 * 1024
    max_scan_artifact_bytes: int = 100 * 1024 * 1024
    subprocess_timeout_seconds: float = 30.0
    regression_request_timeout_seconds: float = 2.0
    regression_max_examples: int = 1
    contract_fail_severities: tuple[ChangeSeverity, ...] = (ChangeSeverity.ERROR,)
    analysis_enabled: bool = False
    max_risk_analyses_per_scan: int = 10
    max_failure_diagnoses_per_scan: int = 10
    analysis_node_timeout_seconds: float = 30.0

    @model_validator(mode="after")
    def validate_limits(self) -> "GuardianPolicy":
        """保证策略中的字节和超时限制有效."""
        if self.max_openapi_bytes <= 0 or self.max_artifact_bytes <= 0 or self.max_scan_artifact_bytes <= 0:
            raise ValueError("Guardian artifact limits must be positive")
        if self.max_scan_artifact_bytes < self.max_artifact_bytes:
            raise ValueError("Scan artifact limit must not be smaller than a single artifact limit")
        if self.subprocess_timeout_seconds <= 0:
            raise ValueError("Guardian subprocess timeout must be positive")
        if self.regression_request_timeout_seconds <= 0 or self.regression_max_examples <= 0:
            raise ValueError("Guardian regression limits must be positive")
        if self.max_risk_analyses_per_scan <= 0 or self.max_failure_diagnoses_per_scan <= 0:
            raise ValueError("Guardian analysis limits must be positive")
        if self.analysis_node_timeout_seconds <= 0:
            raise ValueError("Guardian analysis timeout must be positive")
        return self


class GuardianSpecSummary(BaseModel):
    """最终报告中的 Spec 指纹，不包含本地路径或原始内容."""

    model_config = ConfigDict(frozen=True)

    base_sha256: str
    head_sha256: str


class GuardianToolSummary(BaseModel):
    """最终报告中的固定工具版本与安全执行摘要."""

    model_config = ConfigDict(frozen=True)

    oasdiff_version: str
    schemathesis_version: str
    contract_run: ToolRunSummary | None = None
    regression_run: RegressionRunSummary | None = None


class GuardianContractReport(BaseModel):
    """最终报告的确定性 Contract 部分."""

    model_config = ConfigDict(frozen=True)

    verdict: GuardianVerdict
    changes: tuple[ContractChange, ...] = ()


class GuardianRegressionReport(BaseModel):
    """最终报告的确定性 Regression 部分."""

    model_config = ConfigDict(frozen=True)

    verdict: GuardianVerdict | None = None
    results: tuple[RegressionResult, ...] = ()


class GuardianAnalysisReport(BaseModel):
    """最终报告中的可降级智能解释部分."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    degraded: bool = False
    risk_explanations: tuple[RiskExplanation, ...] = ()
    failure_diagnoses: tuple[FailureDiagnosis, ...] = ()
    warnings: tuple[str, ...] = ()
    truncated: bool = False


class GuardianReport(BaseModel):
    """只能由 Guardian 白名单领域 DTO 构建的持久化报告."""

    model_config = ConfigDict(frozen=True)

    scan_id: UUID
    specs: GuardianSpecSummary
    tools: GuardianToolSummary
    policy: GuardianPolicy
    contract: GuardianContractReport
    scope: ScopeSelection
    regression: GuardianRegressionReport
    final_verdict: GuardianVerdict
    errors: tuple[GuardianError, ...] = ()
    analysis: GuardianAnalysisReport = Field(default_factory=GuardianAnalysisReport)
    created_at: datetime
    completed_at: datetime
