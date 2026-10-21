"""上游 calculator 缺失兼容修复测试."""

import pytest
from fastapi.routing import APIRoute

from app.core.langgraph.tools.calculator import calculator
from app.main import app


def test_calculator_static_tool_import_and_basic_arithmetic() -> None:
    """静态工具可导入且只执行基本算术."""
    assert calculator.invoke({"expression": "(2 + 3) * 4"}) == "20"


def test_calculator_rejects_python_execution() -> None:
    """兼容实现不允许名称、调用或属性访问."""
    with pytest.raises(ValueError, match="basic arithmetic"):
        calculator.invoke({"expression": "__import__('os').getcwd()"})


def test_app_main_imports_and_mounts_guardian_routes() -> None:
    """上游兼容修复后 app.main 可导入，且三条 Guardian route 已挂载."""
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    assert "/api/v1/guardian/scans" in paths
    assert "/api/v1/guardian/scans/{scan_id}" in paths
    assert "/api/v1/guardian/scans/{scan_id}/report" in paths
