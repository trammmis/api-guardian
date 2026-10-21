"""仅用于隔离 API surface size 的本地 FastAPI SUT。."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from benchmarks.guardian.suts.common import RequestCounter, attach_benchmark_control


def create_scaling_app(total_operations: int, affected_indexes: frozenset[int]) -> FastAPI:
    """生成 N 个等价 GET operation；受影响 operation 故意返回 string。."""
    if total_operations < 1:
        raise ValueError("total_operations must be positive")
    if any(index < 1 or index > total_operations for index in affected_indexes):
        raise ValueError("affected index is outside the generated API")
    app = FastAPI(title="Guardian Scaling SUT", version="1.0.0")
    counter = RequestCounter()
    attach_benchmark_control(app, counter)
    for index in range(1, total_operations + 1):
        app.add_api_route(
            _path(index),
            _endpoint_factory(index, index in affected_indexes),
            methods=["GET"],
            operation_id=_operation_id(index),
            response_model=None,
        )
    return app


def _endpoint_factory(index: int, mutated: bool) -> Callable[[], Any]:
    """用闭包固定响应，避免把控制参数暴露为 API query parameter。."""

    async def endpoint() -> dict[str, int | str]:
        return {"id": index, "value": str(index) if mutated else index}

    return endpoint


def _path(index: int) -> str:
    return f"/resources/{index:04d}"


def _operation_id(index: int) -> str:
    return f"getResource{index:04d}"
