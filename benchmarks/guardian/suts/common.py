"""两个 Controlled SUT 共用的请求计数与 reset 控制。."""

from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from fastapi import FastAPI, Request


@dataclass(frozen=True)
class BenchmarkOperation:
    """一个本地 SUT operation 及其固定 mutation 响应。."""

    path: str
    operation_id: str
    default_payload: Any
    variant_payloads: dict[str, Any] = field(default_factory=dict)


class RequestCounter:
    """只统计 OpenAPI 业务 operation，不统计健康检查和控制请求。."""

    def __init__(self) -> None:
        """初始化线程安全计数器。."""
        self._count = 0
        self._lock = Lock()

    def increment(self) -> None:
        """记录一个由 Schemathesis 发出的业务 HTTP request。."""
        with self._lock:
            self._count += 1

    def reset(self) -> None:
        """在每个独立 measurement 前清零。."""
        with self._lock:
            self._count = 0

    def value(self) -> int:
        """读取当前计数。."""
        with self._lock:
            return self._count


def attach_benchmark_control(app: FastAPI, counter: RequestCounter) -> None:
    """附加不进入受测 OpenAPI 的健康、reset 与 metrics 接口。."""

    @app.middleware("http")
    async def count_business_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not request.url.path.startswith("/__benchmark__") and request.url.path != "/health":
            counter.increment()
        return await call_next(request)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/__benchmark__/reset", include_in_schema=False)
    async def reset() -> dict[str, int]:
        counter.reset()
        return {"http_requests": 0}

    @app.get("/__benchmark__/metrics", include_in_schema=False)
    async def metrics() -> dict[str, int]:
        return {"http_requests": counter.value()}


def create_catalog_app(
    title: str,
    variant: str,
    operations: tuple[BenchmarkOperation, ...],
) -> FastAPI:
    """根据固定 operation catalog 创建无公网依赖的独立 FastAPI SUT。."""
    app = FastAPI(title=title, version="1.0.0")
    counter = RequestCounter()
    attach_benchmark_control(app, counter)
    for operation in operations:
        payload = operation.variant_payloads.get(variant, operation.default_payload)

        async def endpoint(response_payload: Any = payload) -> Any:
            return deepcopy(response_payload)

        app.add_api_route(
            operation.path,
            endpoint,
            methods=["GET"],
            operation_id=operation.operation_id,
            response_model=None,
        )
    return app
