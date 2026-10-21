"""Guardian OpenAPI 与 SSRF 安全边界测试."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.security import prepare_openapi_spec, validate_target_base_url
from app.services.guardian.state import GuardianState

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_prepare_openapi_spec_extracts_hash_and_operations() -> None:
    """合法 self-contained fixture 应产生 SHA256 和 operation."""
    prepared = prepare_openapi_spec(str(FIXTURE_DIR / "head_breaking_openapi.yaml"), 5 * 1024 * 1024)

    assert prepared.sha256 == "e645ebf416c2645920429fe2b15da8db6ee8f7c28966452dc32f3835f1502227"
    assert [operation.key for operation in prepared.operations] == ["GET /items/{item_id}"]


def test_external_ref_is_disabled() -> None:
    """V1 必须拒绝 external $ref."""
    with pytest.raises(GuardianException) as captured:
        prepare_openapi_spec(str(FIXTURE_DIR / "external_ref_openapi.yaml"), 5 * 1024 * 1024)

    assert captured.value.code == GuardianErrorCode.EXTERNAL_REF_DISABLED


def test_openapi_file_size_is_checked_before_full_read() -> None:
    """超过配置上限的 OpenAPI 必须在解析前失败."""
    with TemporaryDirectory(prefix="guardian-phase1-") as temp_dir:
        oversized = Path(temp_dir) / "oversized.yaml"
        oversized.write_bytes(b"openapi: 3.0.3\n" + b"x" * 32)

        with pytest.raises(GuardianException) as captured:
            prepare_openapi_spec(str(oversized), 16)

        assert captured.value.code == GuardianErrorCode.OPENAPI_TOO_LARGE


def test_public_allowlisted_target_is_accepted() -> None:
    """公开 IP 仍必须命中 host allowlist."""
    result = asyncio.run(validate_target_base_url("https://8.8.8.8/api/", ("8.8.8.8",), False))

    assert result == "https://8.8.8.8/api"


def test_target_outside_allowlist_is_rejected() -> None:
    """未命中 allowlist 的 target 必须拒绝."""
    with pytest.raises(GuardianException) as captured:
        asyncio.run(validate_target_base_url("https://8.8.8.8", ("1.1.1.1",), False))

    assert captured.value.code == GuardianErrorCode.TARGET_NOT_ALLOWED


def test_private_target_is_blocked_by_default() -> None:
    """即使在 allowlist 中，私网 target 默认仍被阻断."""
    with pytest.raises(GuardianException) as captured:
        asyncio.run(validate_target_base_url("http://127.0.0.1:8000", ("127.0.0.1",), False))

    assert captured.value.code == GuardianErrorCode.PRIVATE_TARGET_BLOCKED


def test_private_target_development_override() -> None:
    """development-only 开关允许显式 allowlist 中的本地 fixture."""
    result = asyncio.run(validate_target_base_url("http://127.0.0.1:8000/", ("127.0.0.1",), True))

    assert result == "http://127.0.0.1:8000"


def test_target_credentials_are_rejected_without_echo() -> None:
    """URL userinfo 中的 Secret 不得进入错误信息."""
    marker = "guardian-url-secret"
    with pytest.raises(GuardianException) as captured:
        asyncio.run(
            validate_target_base_url(
                f"https://user:{marker}@8.8.8.8",
                ("8.8.8.8",),
                False,
            )
        )

    assert captured.value.code == GuardianErrorCode.INVALID_INPUT
    assert marker not in str(captured.value)


def test_guardian_state_has_no_raw_or_secret_fields() -> None:
    """State schema 本身不提供 raw 输出和认证字段."""
    field_names = set(GuardianState.__annotations__)

    assert "stdout" not in field_names
    assert "stderr" not in field_names
    assert "base_source" not in field_names
    assert "revision_source" not in field_names
    assert "authorization" not in field_names
    assert "headers" not in field_names
    assert "ndjson" not in field_names
    assert "request" not in field_names
    assert "response" not in field_names
    assert "cli_argv" not in field_names
    assert "temporary_config" not in field_names
