"""Phase 4 LLM Structured Output、grounding 与降级测试."""

import asyncio
import json
from pathlib import Path
from typing import Any, cast, override

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables.config import RunnableConfig
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.metrics import guardian_llm_calls_total, guardian_llm_duration_seconds, guardian_scans_total
from app.services.guardian.diagnosis import FailureDiagnoser, diagnosis_facts
from app.services.guardian.graph import create_guardian_graph
from app.services.guardian.models import (
    ChangeSeverity,
    ContractChange,
    FailureCategory,
    FailureDiagnosis,
    GuardianPolicy,
    GuardianVerdict,
    OperationRef,
    RegressionCheck,
    RegressionFailure,
    RegressionOutcome,
    RiskExplanation,
    RiskLevel,
)
from app.services.guardian.nodes import GuardianNodes
from app.services.guardian.oasdiff import OasdiffAdapter
from app.services.guardian.risk import RiskAnalyzer, guardian_structured_output_kwargs
from app.services.guardian import risk as risk_module
from app.services.guardian.schemathesis import SchemathesisAdapter
from app.services.guardian.state import GuardianState
from app.services.guardian.subprocess import ProcessResult
from tests.guardian.fakes import StaticProcessRunner

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CHANGE = ContractChange(
    change_id="response-property-type-changed",
    summary="response.id changed from string to integer",
    severity=ChangeSeverity.ERROR,
    method="GET",
    path="/items/{item_id}",
    operation_id="getItem",
    section="paths",
    fingerprint=(
        "authorization=cookie=api_key=jwt=database_url=raw_ndjson="
        "request_body=response_body=C:/private/workspace/head.yaml"
    ),
)
OPERATION = OperationRef(method="GET", path="/items/{item_id}", operation_id="getItem")
CHECKS = (
    RegressionCheck(
        name="response_schema_conformance",
        status=RegressionOutcome.FAILED,
        message="Response violates the declared schema",
    ),
)
FAILURE = RegressionFailure(
    failure_id="failure-1",
    operation=OPERATION,
    check_name="response_schema_conformance",
    category="JsonSchemaError",
    safe_message="Response violates the declared schema",
    status_code=200,
    phase="Coverage",
)


class FakeStructuredLLM:
    """记录白名单消息并返回队列结果的结构化 LLM Fake."""

    def __init__(self, *responses: BaseModel | dict[str, Any] | Exception) -> None:
        """保存按调用顺序返回的结构化结果或异常."""
        self.responses = list(responses)
        self.calls: list[tuple[LanguageModelInput, RunnableConfig | None, type[BaseModel] | None]] = []

    async def call(
        self,
        messages: LanguageModelInput,
        model_name: str | None = None,
        response_format: type[BaseModel] | None = None,
        *,
        runnable_config: RunnableConfig | None = None,
        fallback_enabled: bool = True,
        **model_kwargs: Any,
    ) -> BaseMessage | BaseModel:
        """模拟现有 LLMService.call，不记录或制造 provider 原始响应."""
        del model_name, fallback_enabled, model_kwargs
        self.calls.append((messages, runnable_config, response_format))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if response_format is None:
            raise AssertionError("Guardian analysis must request structured output")
        return response_format.model_validate(response)


class FactDrivenLLM(FakeStructuredLLM):
    """根据白名单事实确定性生成输出，用于 graph 与 checkpoint 测试."""

    def __init__(self, failure: Exception | None = None) -> None:
        """配置所有调用是否统一失败."""
        super().__init__()
        self.failure = failure

    @override
    async def call(
        self,
        messages: LanguageModelInput,
        model_name: str | None = None,
        response_format: type[BaseModel] | None = None,
        *,
        runnable_config: RunnableConfig | None = None,
        fallback_enabled: bool = True,
        **model_kwargs: Any,
    ) -> BaseMessage | BaseModel:
        """从 HumanMessage JSON 读取 ID 与 available_evidence，避免自然语言匹配."""
        del model_name, fallback_enabled, model_kwargs
        self.calls.append((messages, runnable_config, response_format))
        if self.failure is not None:
            raise self.failure
        facts = json.loads(_message_text(messages))
        if response_format is RiskExplanation:
            return RiskExplanation(
                change_id=facts["change_id"],
                risk_level=RiskLevel.HIGH,
                impact="依赖旧 string 响应契约的调用方可能发生类型校验或反序列化失败。",
                affected_consumer="依赖旧契约的调用方",
                recommendation="在迁移窗口提供兼容字段或发布明确的版本化契约。",
                confidence=0.9,
            )
        if response_format is FailureDiagnosis:
            return FailureDiagnosis(
                failure_id=facts["failure_id"],
                category=FailureCategory.CONTRACT_MISMATCH,
                summary="实际响应未通过新的响应 Schema 校验。",
                possible_cause="接口实现可能尚未同步新的响应字段类型契约。",
                evidence=(facts["available_evidence"][0],),
                recommendation="核对运行实现返回类型与 Head OpenAPI 响应 Schema。",
                confidence=0.88,
            )
        raise AssertionError("unexpected structured output schema")


def _message_text(messages: LanguageModelInput) -> str:
    """读取测试调用最后一条 HumanMessage 的文本内容."""
    if not isinstance(messages, list) or not messages:
        raise AssertionError("expected a non-empty message list")
    last_message = messages[-1]
    if not isinstance(last_message, BaseMessage):
        raise AssertionError("expected BaseMessage input")
    content = last_message.content
    if not isinstance(content, str):
        raise AssertionError("expected text-only safe facts")
    return content


def _risk_output(change_id: str = CHANGE.change_id) -> RiskExplanation:
    """返回有效 RiskExplanation fixture."""
    return RiskExplanation(
        change_id=change_id,
        risk_level=RiskLevel.HIGH,
        impact="依赖旧 string 类型的调用方可能出现反序列化兼容问题。",
        affected_consumer="依赖旧契约的调用方",
        recommendation="采用版本化契约并在迁移窗口维持向后兼容。",
        confidence=0.9,
    )


def _diagnosis_output(
    *,
    category: FailureCategory = FailureCategory.CONTRACT_MISMATCH,
    evidence: tuple[str, ...] | None = None,
) -> FailureDiagnosis:
    """返回有效 FailureDiagnosis fixture."""
    facts = diagnosis_facts(FAILURE, related_change=CHANGE, risk=_risk_output(), checks=CHECKS)
    return FailureDiagnosis(
        failure_id=FAILURE.failure_id,
        category=category,
        summary="实际响应未通过 Head Schema 校验。",
        possible_cause="接口实现可能尚未同步新的响应类型契约。",
        evidence=evidence or (facts.available_evidence[0],),
        recommendation="核对运行实现返回类型与 Head OpenAPI。",
        confidence=0.88,
    )


def test_risk_analyzer_returns_valid_structured_output() -> None:
    """Risk 必须通过 Pydantic Structured Output 且保持 change ID."""
    fake = FakeStructuredLLM(_risk_output())
    result = asyncio.run(RiskAnalyzer(fake).analyze(CHANGE, scan_id="scan-1"))

    assert result.change_id == CHANGE.change_id
    assert result.risk_level == RiskLevel.HIGH
    assert fake.calls[0][2] is RiskExplanation


def test_deepseek_structured_output_disables_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    """DeepSeek V4 的强制 function tool 必须只对其子进程调用关闭思考模式."""
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.deepseek.com")

    assert guardian_structured_output_kwargs() == {
        "temperature": 0,
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def test_non_deepseek_structured_output_has_no_provider_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """其他 OpenAI-compatible provider 不接收 DeepSeek 私有扩展参数."""
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")

    assert guardian_structured_output_kwargs() == {"temperature": 0}


def test_risk_analyzer_rejects_invalid_structured_output_and_changed_id() -> None:
    """缺字段或擅自改写 change ID 都按分析失败处理."""
    malformed = FakeStructuredLLM({"change_id": CHANGE.change_id})
    with pytest.raises(ValidationError):
        asyncio.run(RiskAnalyzer(malformed).analyze(CHANGE, scan_id="scan-1"))

    changed = FakeStructuredLLM(_risk_output("invented-change"))
    with pytest.raises(ValueError, match="change_id"):
        asyncio.run(RiskAnalyzer(changed).analyze(CHANGE, scan_id="scan-1"))


@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), RuntimeError("provider unavailable"), ValueError("model API key is not configured")],
)
def test_risk_analyzer_propagates_safe_provider_failures(failure: Exception) -> None:
    """timeout/provider error 交给 Graph 转换为 degradation，不生成伪造解释."""
    with pytest.raises(type(failure)):
        asyncio.run(RiskAnalyzer(FakeStructuredLLM(failure)).analyze(CHANGE, scan_id="scan-1"))


def test_risk_prompt_uses_whitelist_and_hallucination_guard() -> None:
    """Prompt 不携带 fingerprint 绝对路径，并明确禁止无证据实体."""
    fake = FakeStructuredLLM(_risk_output())
    asyncio.run(RiskAnalyzer(fake).analyze(CHANGE, scan_id="scan-1"))
    serialized = repr(fake.calls)

    for forbidden in (
        "authorization=",
        "cookie=",
        "api_key=",
        "jwt=",
        "database_url=",
        "raw_ndjson=",
        "request_body=",
        "response_body=",
        "C:/private/workspace",
    ):
        assert forbidden not in serialized
    assert "数据库" in serialized
    assert "具体 consumer" in serialized


def test_guardian_langfuse_config_contains_only_safe_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """启用现有 callback 时 metadata 仍不包含用户、路径、DSN 或任何 key."""
    monkeypatch.setattr(settings, "LANGFUSE_TRACING_ENABLED", True)
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "public-test-marker")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "secret-test-marker")
    monkeypatch.setattr(risk_module, "langfuse_callback_handler", BaseCallbackHandler())
    fake = FakeStructuredLLM(_risk_output())
    asyncio.run(RiskAnalyzer(fake).analyze(CHANGE, scan_id="scan-1"))
    config = fake.calls[0][1]

    assert config is not None
    assert config.get("callbacks")
    metadata = config.get("metadata")
    assert metadata == {
        "scan_id": "scan-1",
        "analysis_type": "risk",
        "entity_id": CHANGE.change_id,
        "model": settings.DEFAULT_LLM_MODEL,
        "provider": "openai_compatible",
    }
    assert "public-test-marker" not in repr(metadata)
    assert "secret-test-marker" not in repr(metadata)


def test_guardian_prometheus_labels_are_fixed_low_cardinality_dimensions() -> None:
    """指标 label 不允许 scan/user/path/operation/failure 等高基数值."""
    assert tuple(cast(Any, guardian_scans_total)._labelnames) == ("final_verdict",)
    assert tuple(cast(Any, guardian_llm_calls_total)._labelnames) == ("analysis_type", "status")
    assert tuple(cast(Any, guardian_llm_duration_seconds)._labelnames) == ("analysis_type",)


def test_failure_diagnoser_returns_grounded_structured_output() -> None:
    """Diagnosis evidence 必须来自 available_evidence 精确集合."""
    output = _diagnosis_output()
    fake = FakeStructuredLLM(output)
    result = asyncio.run(
        FailureDiagnoser(fake).diagnose(
            FAILURE,
            related_change=CHANGE,
            risk=_risk_output(),
            checks=CHECKS,
            scan_id="scan-1",
        )
    )

    assert result.category == FailureCategory.CONTRACT_MISMATCH
    assert result.evidence == output.evidence
    assert fake.calls[0][2] is FailureDiagnosis


def test_failure_diagnoser_rejects_unseen_evidence_and_malformed_output() -> None:
    """模型不能把不存在的源码、数据库或日志声明为 evidence."""
    invented = FakeStructuredLLM(_diagnosis_output(evidence=("database table users",)))
    with pytest.raises(ValueError, match="evidence"):
        asyncio.run(
            FailureDiagnoser(invented).diagnose(
                FAILURE,
                related_change=CHANGE,
                risk=None,
                checks=CHECKS,
                scan_id="scan-1",
            )
        )

    malformed = FakeStructuredLLM({"failure_id": FAILURE.failure_id, "category": "unknown"})
    with pytest.raises(ValidationError):
        asyncio.run(
            FailureDiagnoser(malformed).diagnose(
                FAILURE,
                related_change=None,
                risk=None,
                checks=CHECKS,
                scan_id="scan-1",
            )
        )


def test_failure_diagnosis_allows_unknown_and_requires_uncertainty() -> None:
    """证据不足允许 UNKNOWN，但 possible_cause 不能冒充确定 root cause."""
    assert _diagnosis_output(category=FailureCategory.UNKNOWN).category == FailureCategory.UNKNOWN
    with pytest.raises(ValidationError, match="uncertainty"):
        FailureDiagnosis(
            failure_id=FAILURE.failure_id,
            category=FailureCategory.UNKNOWN,
            summary="证据有限。",
            possible_cause="数据库 users 表就是根因。",
            evidence=("check=response_schema_conformance",),
            recommendation="继续收集证据。",
            confidence=0.1,
        )


@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("malformed provider response")])
def test_failure_diagnoser_propagates_timeout_and_provider_error(failure: Exception) -> None:
    """Diagnosis timeout/malformed provider response 不得生成领域对象."""
    with pytest.raises(type(failure)):
        asyncio.run(
            FailureDiagnoser(FakeStructuredLLM(failure)).diagnose(
                FAILURE,
                related_change=CHANGE,
                risk=None,
                checks=CHECKS,
                scan_id="scan-1",
            )
        )


def _policy(*, enabled: bool) -> GuardianPolicy:
    """返回本地 graph 测试策略."""
    return GuardianPolicy(
        allowed_target_hosts=("127.0.0.1",),
        allow_private_targets=True,
        analysis_enabled=enabled,
    )


def _run_main_graph(
    *,
    risk_llm: FakeStructuredLLM,
    diagnosis_llm: FakeStructuredLLM,
    enabled: bool = True,
    oasdiff_fixture: str = "oasdiff_breaking.json",
    schemathesis_fixture: str = "schemathesis_fail.ndjson",
) -> dict[str, Any]:
    """使用真实 parser、确定性 Fake 工具与可控 LLM 运行完整 graph."""
    oasdiff_payload = (FIXTURE_DIR / oasdiff_fixture).read_bytes()
    oasdiff_exit = 0 if oasdiff_fixture != "oasdiff_breaking.json" else 1
    regression_exit = 0 if schemathesis_fixture == "schemathesis_pass.ndjson" else 1
    graph = create_guardian_graph(
        policy=_policy(enabled=enabled),
        oasdiff=OasdiffAdapter(
            runner=StaticProcessRunner(ProcessResult(exit_code=oasdiff_exit, stdout=oasdiff_payload, stderr=b""))
        ),
        schemathesis=SchemathesisAdapter(
            runner=StaticProcessRunner(
                ProcessResult(exit_code=regression_exit, stdout=b"", stderr=b""),
                report_payload=(FIXTURE_DIR / schemathesis_fixture).read_bytes(),
            ),
        ),
        risk_analyzer=RiskAnalyzer(risk_llm),
        failure_diagnoser=FailureDiagnoser(diagnosis_llm),
    )
    return asyncio.run(
        graph.ainvoke(
            {
                "scan_id": "phase4-analysis",
                "base_spec_path": str(FIXTURE_DIR / "baseline_openapi.yaml"),
                "head_spec_path": str(FIXTURE_DIR / "head_breaking_openapi.yaml"),
                "target_base_url": "http://127.0.0.1:8000",
            }
        )
    )


def test_graph_pass_without_analysis_work_is_not_degraded() -> None:
    """无 change/failure 时启用 LLM 也无需调用，且不是 degradation."""
    risk_llm = FactDrivenLLM()
    diagnosis_llm = FactDrivenLLM()
    state = _run_main_graph(
        risk_llm=risk_llm,
        diagnosis_llm=diagnosis_llm,
        oasdiff_fixture="oasdiff_no_changes.json",
        schemathesis_fixture="schemathesis_pass.ndjson",
    )

    assert state["final_verdict"] == GuardianVerdict.PASS
    assert state["analysis_degraded"] is False
    assert risk_llm.calls == []
    assert diagnosis_llm.calls == []


def test_graph_contract_and_regression_fail_with_successful_analysis() -> None:
    """主 Demo 的事实 verdict 先形成，随后附加 Risk 与 Diagnosis."""
    risk_llm = FactDrivenLLM()
    diagnosis_llm = FactDrivenLLM()
    state = _run_main_graph(risk_llm=risk_llm, diagnosis_llm=diagnosis_llm)

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.FAIL
    assert state["final_verdict"] == GuardianVerdict.FAIL
    assert state["analysis_degraded"] is False
    assert len(state["risk_explanations"]) == 1
    assert len(state["failure_diagnoses"]) == 1
    assert state["completed_nodes"].index("build_final_verdict") < state["completed_nodes"].index("analyze_risks")


@pytest.mark.parametrize(
    ("risk_failure", "diagnosis_failure"),
    [
        (RuntimeError("risk provider failed"), None),
        (None, TimeoutError()),
        (RuntimeError("risk failed"), RuntimeError("diagnosis failed")),
    ],
)
def test_graph_llm_failures_only_degrade_analysis(
    risk_failure: Exception | None,
    diagnosis_failure: Exception | None,
) -> None:
    """部分或全部 LLM 失败都不能改变 deterministic verdict/status 语义."""
    state = _run_main_graph(
        risk_llm=FactDrivenLLM(risk_failure),
        diagnosis_llm=FactDrivenLLM(diagnosis_failure),
    )

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.FAIL
    assert state["final_verdict"] == GuardianVerdict.FAIL
    assert state["analysis_degraded"] is True
    assert state["analysis_warnings"]


def test_graph_invalid_structured_output_only_degrades_analysis() -> None:
    """Structured Output 校验失败不得覆盖已经形成的 FAIL verdict."""
    state = _run_main_graph(
        risk_llm=FakeStructuredLLM({"change_id": CHANGE.change_id}),
        diagnosis_llm=FactDrivenLLM(),
    )

    assert state["contract_verdict"] == GuardianVerdict.FAIL
    assert state["regression_verdict"] == GuardianVerdict.FAIL
    assert state["final_verdict"] == GuardianVerdict.FAIL
    assert state["analysis_degraded"] is True


def test_graph_explicit_llm_disabled_is_not_degraded() -> None:
    """主动关闭 LLM 时 deterministic pipeline 正常，analysis 为空且不降级."""
    risk_llm = FactDrivenLLM(RuntimeError("must not be called"))
    diagnosis_llm = FactDrivenLLM(RuntimeError("must not be called"))
    state = _run_main_graph(risk_llm=risk_llm, diagnosis_llm=diagnosis_llm, enabled=False)

    assert state["final_verdict"] == GuardianVerdict.FAIL
    assert state["analysis_enabled"] is False
    assert state["analysis_degraded"] is False
    assert state["risk_explanations"] == ()
    assert state["failure_diagnoses"] == ()
    assert risk_llm.calls == []
    assert diagnosis_llm.calls == []


def test_risk_analysis_uses_stable_priority_limit_and_marks_truncated() -> None:
    """超过上限时按 error 优先稳定选择，记录 truncated 而不牺牲确定性结果."""
    lower_priority = CHANGE.model_copy(
        update={"change_id": "warning-change", "severity": ChangeSeverity.WARNING, "path": "/z"}
    )
    fake = FactDrivenLLM()
    policy = _policy(enabled=True).model_copy(update={"max_risk_analyses_per_scan": 1})
    nodes = GuardianNodes(
        policy, risk_analyzer=RiskAnalyzer(fake), failure_diagnoser=FailureDiagnoser(FactDrivenLLM())
    )
    state = asyncio.run(
        nodes.analyze_risks(
            {
                "scan_id": "stable-limit",
                "contract_changes": (lower_priority, CHANGE),
                "final_verdict": GuardianVerdict.FAIL,
            }
        )
    )

    assert [item.change_id for item in state.get("risk_explanations", ())] == [CHANGE.change_id]
    assert state.get("analysis_truncated") is True
    assert state.get("analysis_degraded") is False


def test_risk_analysis_partial_success_is_degraded_but_preserved() -> None:
    """多项分析部分成功时保留成功结果，并把整体标记为 degraded."""
    second = CHANGE.model_copy(update={"change_id": "second-change", "path": "/z"})
    fake = FakeStructuredLLM(_risk_output(), RuntimeError("second call failed"))
    nodes = GuardianNodes(
        _policy(enabled=True),
        risk_analyzer=RiskAnalyzer(fake),
        failure_diagnoser=FailureDiagnoser(FactDrivenLLM()),
    )
    original: GuardianState = {
        "scan_id": "partial-success",
        "contract_changes": (CHANGE, second),
        "final_verdict": GuardianVerdict.FAIL,
    }
    update = asyncio.run(nodes.analyze_risks(original))
    state = {**original, **update}

    assert len(state["risk_explanations"]) == 1
    assert state["analysis_degraded"] is True
    assert state["final_verdict"] == GuardianVerdict.FAIL
