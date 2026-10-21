"""Guardian 的 OpenAPI 边界和 SSRF 防护."""

import asyncio
import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.models import OperationRef

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


@dataclass(frozen=True)
class PreparedOpenAPISpec:
    """仅在 prepare_scan 节点内短暂存在的解析结果."""

    sha256: str
    size_bytes: int
    operations: tuple[OperationRef, ...]


def prepare_openapi_spec(path_value: str, max_bytes: int) -> PreparedOpenAPISpec:
    """校验 self-contained OpenAPI 文件并提取安全元数据."""
    path = Path(path_value)
    if path.suffix.lower() not in {".json", ".yaml", ".yml"}:
        raise GuardianException(GuardianErrorCode.INVALID_OPENAPI, "OpenAPI 文件必须是 JSON 或 YAML")
    if not path.is_file():
        raise GuardianException(GuardianErrorCode.INVALID_OPENAPI, "OpenAPI 文件不存在或不是普通文件")

    with path.open("rb") as file_handle:
        payload = file_handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise GuardianException(GuardianErrorCode.OPENAPI_TOO_LARGE, "OpenAPI 文件超过配置限制")

    try:
        document = yaml.safe_load(payload)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GuardianException(GuardianErrorCode.INVALID_OPENAPI, "OpenAPI 文件无法解析") from exc
    if not isinstance(document, dict) or not isinstance(document.get("openapi"), str):
        raise GuardianException(GuardianErrorCode.INVALID_OPENAPI, "文件不是有效的 OpenAPI 文档")
    if not isinstance(document.get("paths"), dict):
        raise GuardianException(GuardianErrorCode.INVALID_OPENAPI, "OpenAPI paths 必须是对象")

    _reject_external_refs(document)
    operations = _extract_operations(document)
    return PreparedOpenAPISpec(
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        operations=operations,
    )


def _reject_external_refs(document: dict[str, Any]) -> None:
    """以迭代方式拒绝 external ref，并避免 YAML anchor 循环."""
    stack: list[Any] = [document]
    visited: set[int] = set()
    while stack:
        value = stack.pop()
        if isinstance(value, (dict, list)):
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                raise GuardianException(
                    GuardianErrorCode.EXTERNAL_REF_DISABLED,
                    "V1 默认禁止 OpenAPI external $ref",
                )
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def _extract_operations(document: dict[str, Any]) -> tuple[OperationRef, ...]:
    """从 OpenAPI paths 提取有序 operation 列表."""
    operations: list[OperationRef] = []
    paths = document["paths"]
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(method, str) or method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            operations.append(
                OperationRef(
                    method=method,
                    path=path,
                    operation_id=operation_id if isinstance(operation_id, str) else None,
                )
            )
    return tuple(operations)


async def validate_target_base_url(
    target_base_url: str,
    allowed_hosts: tuple[str, ...],
    allow_private_targets: bool,
    dns_timeout_seconds: float = 3.0,
) -> str:
    """校验 target URL 的 allowlist、协议、凭据和解析地址."""
    try:
        parsed = urlsplit(target_base_url)
        port = parsed.port
    except ValueError as exc:
        raise GuardianException(GuardianErrorCode.INVALID_INPUT, "target_base_url 格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GuardianException(GuardianErrorCode.INVALID_INPUT, "target_base_url 只允许 HTTP 或 HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise GuardianException(GuardianErrorCode.INVALID_INPUT, "target_base_url 不允许携带用户凭据")
    if parsed.query or parsed.fragment:
        raise GuardianException(GuardianErrorCode.INVALID_INPUT, "target_base_url 不允许 query 或 fragment")

    host = _normalize_host(parsed.hostname)
    normalized_allowlist = {_normalize_host(item) for item in allowed_hosts}
    if host not in normalized_allowlist:
        raise GuardianException(GuardianErrorCode.TARGET_NOT_ALLOWED, "target host 不在 Guardian allowlist")

    addresses = await _resolve_host_addresses(
        host, port or (443 if parsed.scheme == "https" else 80), dns_timeout_seconds
    )
    for address in addresses:
        _validate_target_address(address, allow_private_targets)

    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _normalize_host(host: str) -> str:
    """规范化 allowlist 与 URL 中的 host."""
    value = host.strip().rstrip(".").lower()
    if not value:
        raise GuardianException(GuardianErrorCode.TARGET_NOT_ALLOWED, "target host 不能为空")
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise GuardianException(GuardianErrorCode.TARGET_NOT_ALLOWED, "target host 无法规范化") from exc


async def _resolve_host_addresses(host: str, port: int, timeout_seconds: float) -> tuple[str, ...]:
    """解析目标地址；IP literal 不执行 DNS 查询."""
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        pass

    loop = asyncio.get_running_loop()
    try:
        records = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
            timeout=timeout_seconds,
        )
    except (TimeoutError, OSError) as exc:
        raise GuardianException(GuardianErrorCode.TARGET_RESOLUTION_FAILED, "target host 解析失败") from exc
    addresses = tuple(sorted({record[4][0] for record in records}))
    if not addresses:
        raise GuardianException(GuardianErrorCode.TARGET_RESOLUTION_FAILED, "target host 未解析到地址")
    return addresses


def _validate_target_address(address: str, allow_private_targets: bool) -> None:
    """拒绝非全局地址；开发开关仅放行私网和回环地址."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise GuardianException(GuardianErrorCode.TARGET_RESOLUTION_FAILED, "target host 返回了无效地址") from exc
    if ip.is_multicast or ip.is_unspecified:
        raise GuardianException(GuardianErrorCode.PRIVATE_TARGET_BLOCKED, "target address 不可访问")
    if not allow_private_targets and not ip.is_global:
        raise GuardianException(GuardianErrorCode.PRIVATE_TARGET_BLOCKED, "默认禁止访问私网或回环 target")
