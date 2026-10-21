"""基于现有 LLMService 的 Regression Failure 诊断."""

import asyncio
import time
from pathlib import Path
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.metrics import guardian_llm_calls_total, guardian_llm_duration_seconds
from app.services.guardian.models import (
    ContractChange,
    FailureDiagnosis,
    FailureDiagnosisFacts,
    RegressionCheck,
    RegressionFailure,
    RiskExplanation,
)
from app.services.guardian.risk import (
    StructuredLLMPort,
    guardian_analysis_config,
    guardian_structured_output_kwargs,
    risk_facts_from_change,
)
from app.services.llm.service import llm_service

DIAGNOSIS_PROMPT = (Path(__file__).parent / "prompts" / "failure_diagnosis.md").read_text(encoding="utf-8")


def diagnosis_facts(
    failure: RegressionFailure,
    *,
    related_change: ContractChange | None,
    risk: RiskExplanation | None,
    checks: tuple[RegressionCheck, ...],
) -> FailureDiagnosisFacts:
    """把失败及关联对象转换成可审计的白名单证据集合."""
    evidence = [f"check={failure.check_name}", f"failure_category={failure.category}"]
    if failure.operation is not None:
        evidence.append(f"operation={failure.operation.key}")
    if failure.safe_message:
        evidence.append(f"failure_message={failure.safe_message}")
    if failure.status_code is not None:
        evidence.append(f"status_code={failure.status_code}")
    if failure.phase:
        evidence.append(f"phase={failure.phase}")
    for check in checks:
        evidence.append(f"check_result={check.name}:{check.status.value}")
    related_facts = risk_facts_from_change(related_change) if related_change is not None else None
    if related_facts is not None:
        evidence.append(f"contract_change={related_facts.change_id}:{related_facts.summary}")
    return FailureDiagnosisFacts(
        failure_id=failure.failure_id,
        operation=failure.operation,
        check_name=failure.check_name,
        tool_category=failure.category,
        safe_message=failure.safe_message,
        status_code=failure.status_code,
        phase=failure.phase,
        checks=checks[:10],
        related_change=related_facts,
        risk_explanation=risk,
        available_evidence=tuple(dict.fromkeys(evidence))[:12],
    )


class FailureDiagnoser:
    """逐条诊断安全 RegressionFailure，不声明源码级 root cause."""

    def __init__(self, llm: StructuredLLMPort | None = None) -> None:
        """默认复用项目级 LLMService，测试可注入结构化 Fake."""
        self._llm = llm or cast(StructuredLLMPort, llm_service)

    async def diagnose(
        self,
        failure: RegressionFailure,
        *,
        related_change: ContractChange | None,
        risk: RiskExplanation | None,
        checks: tuple[RegressionCheck, ...],
        scan_id: str,
    ) -> FailureDiagnosis:
        """生成结构化诊断，并强制 evidence 只能引用输入白名单."""
        facts = diagnosis_facts(
            failure,
            related_change=related_change,
            risk=risk,
            checks=checks,
        )
        messages = [
            SystemMessage(content=DIAGNOSIS_PROMPT),
            HumanMessage(content=facts.model_dump_json()),
        ]
        started = time.perf_counter()
        try:
            raw_result = await self._llm.call(
                messages,
                model_name=settings.DEFAULT_LLM_MODEL,
                response_format=FailureDiagnosis,
                runnable_config=guardian_analysis_config(scan_id, "diagnosis", failure.failure_id),
                fallback_enabled=False,
                structured_output_method="function_calling",
                **guardian_structured_output_kwargs(),
            )
            result = FailureDiagnosis.model_validate(raw_result)
            if result.failure_id != failure.failure_id:
                raise ValueError("failure diagnosis id does not match deterministic input")
            if not set(result.evidence).issubset(set(facts.available_evidence)):
                raise ValueError("failure diagnosis contains evidence not present in deterministic input")
        except asyncio.CancelledError:
            guardian_llm_calls_total.labels(analysis_type="diagnosis", status="error").inc()
            raise
        except Exception:
            guardian_llm_calls_total.labels(analysis_type="diagnosis", status="error").inc()
            raise
        else:
            guardian_llm_calls_total.labels(analysis_type="diagnosis", status="success").inc()
            return result
        finally:
            guardian_llm_duration_seconds.labels(analysis_type="diagnosis").observe(time.perf_counter() - started)
