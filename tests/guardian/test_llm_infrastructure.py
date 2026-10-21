"""Guardian 对现有 LLMService Structured Output/Callback 能力的复用测试."""

import asyncio
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig

from app.services.guardian.models import RiskExplanation, RiskLevel
from app.services.llm.registry import LLMRegistry
from app.services.llm.service import LLMService


class _RecordingRunnable:
    """记录 LLMService 传递的 Runnable config."""

    def __init__(self) -> None:
        """初始化调用记录."""
        self.config: object = None

    async def ainvoke(self, messages: object, config: object = None) -> RiskExplanation:
        """返回固定 Pydantic Structured Output."""
        del messages
        self.config = config
        return RiskExplanation(
            change_id="change-1",
            risk_level=RiskLevel.MEDIUM,
            impact="依赖旧契约的调用方可能出现兼容性问题。",
            affected_consumer="依赖旧契约的调用方",
            recommendation="通过版本化契约提供迁移窗口。",
            confidence=0.8,
        )


class _RecordingModel:
    """记录 with_structured_output 使用的方法和 schema."""

    def __init__(self, runnable: _RecordingRunnable) -> None:
        """保存下游 Runnable."""
        self.runnable = runnable
        self.schema: object = None
        self.method: str | None = None

    def with_structured_output(self, schema: object, *, method: str) -> _RecordingRunnable:
        """模拟 ChatOpenAI 的结构化输出绑定."""
        self.schema = schema
        self.method = method
        return self.runnable


def test_llm_service_forwards_function_calling_and_safe_runnable_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guardian 不建新 SDK，并由现有 LLMService 把 callback metadata 传给 Runnable."""
    runnable = _RecordingRunnable()
    model = _RecordingModel(runnable)

    def fake_get(model_name: str, **kwargs: object) -> _RecordingModel:
        del model_name, kwargs
        return model

    monkeypatch.setattr(LLMRegistry, "get", fake_get)
    service = LLMService()
    config: RunnableConfig = {"metadata": {"scan_id": "scan-1", "analysis_type": "risk"}}
    result = asyncio.run(
        service.call(
            [HumanMessage(content='{"change_id":"change-1"}')],
            model_name="qwen-plus",
            response_format=RiskExplanation,
            runnable_config=config,
            fallback_enabled=False,
            structured_output_method="function_calling",
            temperature=0,
        )
    )

    assert result.change_id == "change-1"
    assert model.schema is RiskExplanation
    assert model.method == "function_calling"
    assert runnable.config == config
