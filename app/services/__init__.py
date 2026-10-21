"""应用服务模块."""

import sys
from importlib import import_module


def _is_guardian_cli() -> bool:
    """识别 `python -m app.services.guardian`，避免加载无关 DB/LLM 服务."""
    return "-m" in sys.orig_argv and "app.services.guardian" in sys.orig_argv


if not _is_guardian_cli():
    # 正常应用路径保持原有 eager exports；仅 Guardian 独立 CLI 跳过无关初始化。
    database_service = import_module("app.services.database").database_service
    llm_module = import_module("app.services.llm")
    LLMRegistry = llm_module.LLMRegistry
    llm_service = llm_module.llm_service
    __all__ = ["database_service", "LLMRegistry", "llm_service"]
else:
    __all__: list[str] = []
