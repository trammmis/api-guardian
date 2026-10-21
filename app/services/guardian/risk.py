"""基于现有 LLMService 的 Contract Change 风险解释."""

import asyncio
import time
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlparse

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel

from app.core.config import settings
from app.core.metrics import guardian_llm_calls_total, guardian_llm_duration_seconds
from app.core.observability import langfuse_callback_handler
from app.services.guardian.models import ContractChange, RiskAnalysisFacts, RiskExplanation
from app.services.llm.service import llm_service

RISK_PROMPT = (Path(__file__).parent / "prompts" / "risk_explanation.md").read_text(encoding="utf-8")


class StructuredLLMPort(Protocol):
    """Guardian 所需的现有 LLMService 最小结构化调用表面."""

    async def call(
        self,
        messages: LanguageModelInput,
        model_name: str | None = None,
        response_format: type[BaseModel] | None = None,
        *,
        runnable_config: RunnableConfig | None = None,
        fallback_enabled: bool = True,
        structured_output_method: Literal["function_calling", "json_mode", "json_schema"] = "json_schema",
        **model_kwargs: Any,
    ) -> BaseMessage | BaseModel:
        """调用现有模型服务并返回已校验结构化对象."""
        ...


def risk_facts_from_change(change: ContractChange) -> RiskAnalysisFacts:
    """显式复制允许发送给 LLM 的 ContractChange 字段."""
    return RiskAnalysisFacts(
        change_id=change.change_id,
        summary=change.summary,
        severity=change.severity,
        method=change.method,
        path=change.path,
        operation_id=change.operation_id,
        section=change.section,
    )


def guardian_analysis_config(scan_id: str, analysis_type: str, entity_id: str) -> RunnableConfig:
    """构造不含身份、路径或 Secret 的 Langfuse callback 配置."""
    config: RunnableConfig = {
        "metadata": {
            "scan_id": scan_id,
            "analysis_type": analysis_type,
            "entity_id": entity_id,
            "model": settings.DEFAULT_LLM_MODEL,
            "provider": "openai_compatible",
        }
    }
    if (
        settings.LANGFUSE_TRACING_ENABLED
        and settings.LANGFUSE_PUBLIC_KEY
        and settings.LANGFUSE_SECRET_KEY
        and langfuse_callback_handler is not None
    ):
        config["callbacks"] = [langfuse_callback_handler]
    return config


def guardian_structured_output_kwargs() -> dict[str, Any]:
    """返回 Guardian Structured Output 所需的最小 provider 兼容参数."""
    model_kwargs: dict[str, Any] = {"temperature": 0}
    hostname = (urlparse(settings.OPENAI_BASE_URL).hostname or "").casefold()
    if hostname == "api.deepseek.com" or hostname.endswith(".deepseek.com"):
        # DeepSeek V4 默认启用思考模式，而指定函数的 tool_choice 仅支持非思考模式。
        model_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return model_kwargs


class RiskAnalyzer:
    """逐条生成 RiskExplanation，不接触原始 OpenAPI 或工具输出."""

    def __init__(self, llm: StructuredLLMPort | None = None) -> None:
        """默认复用项目级 LLMService，测试可注入结构化 Fake."""
        self._llm = llm or cast(StructuredLLMPort, llm_service)

    async def analyze(self, change: ContractChange, *, scan_id: str) -> RiskExplanation:
        """对一个已确认 change 生成并再次校验结构化风险解释."""
        facts = risk_facts_from_change(change)
        messages = [
            SystemMessage(content=RISK_PROMPT),
            HumanMessage(content=facts.model_dump_json()),
        ]
        started = time.perf_counter()
        try:
            raw_result = await self._llm.call(
                messages,
                model_name=settings.DEFAULT_LLM_MODEL,
                response_format=RiskExplanation,
                runnable_config=guardian_analysis_config(scan_id, "risk", change.change_id),
                fallback_enabled=False,
                structured_output_method="function_calling",
                **guardian_structured_output_kwargs(),
            )
            result = RiskExplanation.model_validate(raw_result)
            if result.change_id != change.change_id:
                raise ValueError("risk explanation change_id does not match deterministic input")
        except asyncio.CancelledError:
            guardian_llm_calls_total.labels(analysis_type="risk", status="error").inc()
            raise
        except Exception:
            guardian_llm_calls_total.labels(analysis_type="risk", status="error").inc()
            raise
        else:
            guardian_llm_calls_total.labels(analysis_type="risk", status="success").inc()
            return result
        finally:
            guardian_llm_duration_seconds.labels(analysis_type="risk").observe(time.perf_counter() - started)
