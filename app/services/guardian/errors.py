"""API Guardian 的安全错误类型."""

from enum import StrEnum


class GuardianErrorCode(StrEnum):
    """对外稳定的 Guardian 错误码."""

    INVALID_INPUT = "invalid_input"
    OPENAPI_TOO_LARGE = "openapi_too_large"
    INVALID_OPENAPI = "invalid_openapi"
    EXTERNAL_REF_DISABLED = "external_ref_disabled"
    TARGET_NOT_ALLOWED = "target_not_allowed"
    PRIVATE_TARGET_BLOCKED = "private_target_blocked"
    TARGET_RESOLUTION_FAILED = "target_resolution_failed"
    ARTIFACT_TOO_LARGE = "artifact_too_large"
    SCAN_ARTIFACT_LIMIT_EXCEEDED = "scan_artifact_limit_exceeded"
    SUBPROCESS_TIMEOUT = "subprocess_timeout"
    SUBPROCESS_START_FAILED = "subprocess_start_failed"
    OASDIFF_EXECUTION_FAILED = "oasdiff_execution_failed"
    OASDIFF_INVALID_OUTPUT = "oasdiff_invalid_output"
    UNKNOWN_OASDIFF_SEVERITY = "unknown_oasdiff_severity"
    SCHEMATHESIS_EXECUTION_FAILED = "schemathesis_execution_failed"
    SCHEMATHESIS_INVALID_REPORT = "schemathesis_invalid_report"
    INTERNAL_ERROR = "internal_error"


class GuardianException(Exception):
    """只携带可安全进入状态的错误信息."""

    def __init__(self, code: GuardianErrorCode, safe_message: str) -> None:
        """初始化不包含原始工具输出或 Secret 的异常."""
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
