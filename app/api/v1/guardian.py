"""API Guardian 的 JWT 保护 HTTP 接口."""

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.user import User
from app.schemas.guardian import (
    GuardianErrorResponse,
    GuardianReportResponse,
    GuardianScanDetailResponse,
    GuardianScanResponse,
)
from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.service import (
    GuardianActiveScanError,
    GuardianScanNotFoundError,
    GuardianService,
    guardian_service,
)

router = APIRouter()

_ALLOWED_SPEC_CONTENT_TYPES = {
    "application/json": ".json",
    "application/yaml": ".yaml",
    "application/x-yaml": ".yaml",
    "text/yaml": ".yaml",
}
_UPLOAD_CHUNK_BYTES = 64 * 1024


def get_guardian_service() -> GuardianService:
    """提供可在测试中替换的 GuardianService 单例."""
    return guardian_service


async def _read_spec_upload(upload: UploadFile) -> tuple[bytes, str]:
    """受限流式读取单个 OpenAPI，不信任原始文件名."""
    content_type = (upload.content_type or "").split(";", maxsplit=1)[0].strip().lower()
    extension = _ALLOWED_SPEC_CONTENT_TYPES.get(content_type)
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="OpenAPI 文件 Content-Type 必须是 JSON 或 YAML",
        )
    payload = bytearray()
    try:
        while True:
            chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > settings.GUARDIAN_MAX_OPENAPI_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="OpenAPI 文件超过 5 MiB 限制",
                )
    finally:
        await upload.close()
    return bytes(payload), extension


def _guardian_http_error(exc: GuardianException) -> HTTPException:
    """把输入/安全边界错误映射为 4xx，不混入检测 verdict ERROR."""
    if exc.code == GuardianErrorCode.OPENAPI_TOO_LARGE:
        status_code = status.HTTP_413_CONTENT_TOO_LARGE
    elif exc.code in {GuardianErrorCode.TARGET_NOT_ALLOWED, GuardianErrorCode.PRIVATE_TARGET_BLOCKED}:
        status_code = status.HTTP_403_FORBIDDEN
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(status_code=status_code, detail={"code": exc.code.value, "message": exc.safe_message})


@router.post(
    "/scans",
    response_model=GuardianScanResponse,
    responses={
        409: {"model": GuardianErrorResponse},
        413: {"model": GuardianErrorResponse},
        415: {"model": GuardianErrorResponse},
        422: {"model": GuardianErrorResponse},
    },
)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["guardian_scan"][0])
async def create_guardian_scan(
    request: Request,
    base_spec: Annotated[UploadFile, File(...)],
    head_spec: Annotated[UploadFile, File(...)],
    target_base_url: Annotated[str, Form(...)],
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GuardianService, Depends(get_guardian_service)],
) -> GuardianScanResponse:
    """同步运行完整 Guardian graph；检测 ERROR 仍返回正常业务响应."""
    del request
    if user.id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    try:
        (base_payload, base_extension), (head_payload, head_extension) = await _read_uploads(base_spec, head_spec)
        return await service.create_scan(
            user_id=user.id,
            base_spec=base_payload,
            head_spec=head_payload,
            base_extension=base_extension,
            head_extension=head_extension,
            target_base_url=target_base_url,
        )
    except GuardianActiveScanError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前用户已有进行中的 Guardian Scan")
    except GuardianException as exc:
        raise _guardian_http_error(exc)
    except HTTPException:
        raise
    except Exception:
        logger.exception("guardian_scan_request_failed", user_id=user.id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Guardian 服务内部错误")


async def _read_uploads(base_spec: UploadFile, head_spec: UploadFile) -> tuple[tuple[bytes, str], tuple[bytes, str]]:
    """并发受限读取 Base/Head；单文件错误时也关闭另一个上传对象."""
    try:
        return await _gather_uploads(base_spec, head_spec)
    except Exception:
        await base_spec.close()
        await head_spec.close()
        raise


async def _gather_uploads(base_spec: UploadFile, head_spec: UploadFile) -> tuple[tuple[bytes, str], tuple[bytes, str]]:
    """保持类型清晰的双上传读取."""
    base_result, head_result = await asyncio.gather(_read_spec_upload(base_spec), _read_spec_upload(head_spec))
    return base_result, head_result


@router.get("/scans/{scan_id}", response_model=GuardianScanDetailResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["guardian_read"][0])
async def get_guardian_scan(
    request: Request,
    scan_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GuardianService, Depends(get_guardian_service)],
) -> GuardianScanDetailResponse:
    """返回当前 JWT 用户拥有的扫描摘要."""
    del request
    if user.id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    try:
        return await service.get_scan(scan_id, user.id)
    except GuardianScanNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guardian scan not found")


@router.get("/scans/{scan_id}/report", response_model=GuardianReportResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["guardian_read"][0])
async def get_guardian_report(
    request: Request,
    scan_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[GuardianService, Depends(get_guardian_service)],
) -> GuardianReportResponse:
    """返回当前 JWT 用户拥有的脱敏报告."""
    del request
    if user.id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    try:
        return await service.get_report(scan_id, user.id)
    except GuardianScanNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guardian report not found")
