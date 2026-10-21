"""API Guardian 的本地回归测试服务."""

from fastapi import FastAPI

app = FastAPI(title="API Guardian Fixture API", version="1.0.0")
conforming_app = FastAPI(title="API Guardian Conforming Fixture API", version="1.0.0")


@app.get("/items/{item_id}", operation_id="getItem")
async def get_item(item_id: str) -> dict[str, str]:
    """故意返回字符串 ID，以制造相对于 head spec 的运行时回归."""
    return {"id": item_id}


@app.get("/health")
async def health() -> dict[str, str]:
    """返回本地 fixture API 的健康状态."""
    return {"status": "healthy"}


@conforming_app.get("/items/{item_id}", operation_id="getItem")
async def get_conforming_item(item_id: str) -> dict[str, int]:
    """返回 integer ID，用于证明 breaking contract 下 regression 仍可 PASS."""
    del item_id
    return {"id": 1}


@conforming_app.get("/health")
async def conforming_health() -> dict[str, str]:
    """返回符合目标 API 的本地健康状态."""
    return {"status": "healthy"}
