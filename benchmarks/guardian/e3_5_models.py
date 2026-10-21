"""E3.5 工具兼容性与 fallback 根因诊断的审计模型."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ToolErrorClassification(StrEnum):
    """用户冻结的 Supabase 工具错误分类."""

    INVALID_OPENAPI_INPUT = "INVALID_OPENAPI_INPUT"
    OASDIFF_VERSION_COMPATIBILITY_LIMITATION = "OASDIFF_VERSION_COMPATIBILITY_LIMITATION"
    OASDIFF_PARSER_BUG_LIKELY = "OASDIFF_PARSER_BUG_LIKELY"
    GUARDIAN_INPUT_TRANSFORMATION_BUG = "GUARDIAN_INPUT_TRANSFORMATION_BUG"
    REFERENCE_STRUCTURE_EDGE_CASE = "REFERENCE_STRUCTURE_EDGE_CASE"
    INCONCLUSIVE = "INCONCLUSIVE"
    OTHER = "OTHER"


class NarrowingAssessment(StrEnum):
    """未来缩小 fallback 范围的安全性诊断标签."""

    SAFE_TO_NARROW = "SAFE_TO_NARROW"
    POSSIBLY_SAFE_TO_NARROW = "POSSIBLY_SAFE_TO_NARROW"
    NOT_SAFE_TO_NARROW = "NOT_SAFE_TO_NARROW"
    INCONCLUSIVE = "INCONCLUSIVE"


class FallbackTrace(BaseModel):
    """一个 normalized change 从工具证据到 fallback 决策的完整轨迹."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    oasdiff_change_id: str
    oasdiff_text: str
    change_level: int
    raw_path: str | None = None
    raw_method: str | None = None
    raw_operation_id: str | None = None
    normalized_path: str | None = None
    normalized_method: str | None = None
    normalized_operation_id: str | None = None
    component_name: str | None = None
    component_change: bool = False
    exact_head_operation_match: bool = False
    operation_id_match: bool = False
    component_dependency_match: bool = False
    resolution_strategy_attempted: tuple[str, ...]
    resolution_failure_reason: str | None = None
    fallback_triggered: bool = False
    guardian_selected_count: int = Field(ge=0)
    oracle_executable_count: int = Field(ge=0)
    extra_selected_count: int = Field(ge=0)


class ValidatorResult(BaseModel):
    """独立 OpenAPI validator 的单次结果."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    side: str
    commit: str
    spec_sha256: str
    spec_bytes: int = Field(ge=0)
    openapi_version: str
    external_ref_count: int = Field(ge=0)
    local_fragment_ref_count: int = Field(ge=0)
    valid: bool
    safe_error: str | None = None
