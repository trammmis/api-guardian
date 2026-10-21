"""独立的 API Guardian 确定性回归 LangGraph."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.config import settings
from app.services.guardian.diagnosis import FailureDiagnoser
from app.services.guardian.models import GuardianPolicy
from app.services.guardian.nodes import GuardianNodes, has_diagnosable_failures, is_error_state
from app.services.guardian.oasdiff import OasdiffAdapter, OasdiffPort
from app.services.guardian.risk import RiskAnalyzer
from app.services.guardian.schemathesis import SchemathesisAdapter, SchemathesisPort
from app.services.guardian.state import GuardianState


def guardian_policy_from_settings() -> GuardianPolicy:
    """从现有 Settings 构造非 Secret 的扫描策略快照."""
    return GuardianPolicy(
        allowed_target_hosts=tuple(settings.GUARDIAN_ALLOWED_TARGET_HOSTS),
        allow_private_targets=settings.GUARDIAN_ALLOW_PRIVATE_TARGETS,
        max_openapi_bytes=settings.GUARDIAN_MAX_OPENAPI_BYTES,
        max_artifact_bytes=settings.GUARDIAN_MAX_ARTIFACT_BYTES,
        max_scan_artifact_bytes=settings.GUARDIAN_MAX_SCAN_ARTIFACT_BYTES,
        subprocess_timeout_seconds=settings.GUARDIAN_SUBPROCESS_TIMEOUT_SECONDS,
        analysis_enabled=settings.GUARDIAN_LLM_ENABLED,
        max_risk_analyses_per_scan=settings.GUARDIAN_MAX_RISK_ANALYSES_PER_SCAN,
        max_failure_diagnoses_per_scan=settings.GUARDIAN_MAX_FAILURE_DIAGNOSES_PER_SCAN,
        analysis_node_timeout_seconds=settings.GUARDIAN_ANALYSIS_NODE_TIMEOUT_SECONDS,
    )


def create_guardian_graph(
    *,
    policy: GuardianPolicy | None = None,
    oasdiff: OasdiffPort | None = None,
    schemathesis: SchemathesisPort | None = None,
    risk_analyzer: RiskAnalyzer | None = None,
    failure_diagnoser: FailureDiagnoser | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    interrupt_after: list[str] | None = None,
) -> CompiledStateGraph:
    """创建 Guardian graph；CLI 默认无 checkpointer，HTTP 可显式注入."""
    selected_policy = policy or guardian_policy_from_settings()
    selected_oasdiff = oasdiff
    if selected_oasdiff is None:
        selected_oasdiff = OasdiffAdapter(
            executable=settings.GUARDIAN_OASDIFF_PATH,
            timeout_seconds=selected_policy.subprocess_timeout_seconds,
            max_artifact_bytes=selected_policy.max_artifact_bytes,
        )
    selected_schemathesis = schemathesis
    if selected_schemathesis is None:
        selected_schemathesis = SchemathesisAdapter(
            executable=settings.GUARDIAN_SCHEMATHESIS_PATH,
            timeout_seconds=selected_policy.subprocess_timeout_seconds,
            request_timeout_seconds=selected_policy.regression_request_timeout_seconds,
            max_examples=selected_policy.regression_max_examples,
            max_artifact_bytes=selected_policy.max_artifact_bytes,
        )
    nodes = GuardianNodes(
        selected_policy,
        selected_oasdiff,
        selected_schemathesis,
        risk_analyzer,
        failure_diagnoser,
    )
    builder = StateGraph(GuardianState)
    builder.add_node("prepare_scan", nodes.prepare_scan)
    builder.add_node("contract_diff", nodes.contract_diff)
    builder.add_node("normalize_changes", nodes.normalize_changes)
    builder.add_node("select_test_scope", nodes.select_test_scope)
    builder.add_node("build_contract_verdict", nodes.build_contract_verdict)
    builder.add_node("run_regression", nodes.run_regression)
    builder.add_node("parse_test_results", nodes.parse_test_results)
    builder.add_node("build_final_verdict", nodes.build_final_verdict)
    builder.add_node("analyze_risks", nodes.analyze_risks)
    builder.add_node("diagnose_failures", nodes.diagnose_failures)
    builder.add_node("build_enhanced_report", nodes.build_enhanced_report)
    builder.set_entry_point("prepare_scan")
    builder.add_conditional_edges(
        "prepare_scan",
        is_error_state,
        {True: "build_contract_verdict", False: "contract_diff"},
    )
    builder.add_conditional_edges(
        "contract_diff",
        is_error_state,
        {True: "build_contract_verdict", False: "normalize_changes"},
    )
    builder.add_conditional_edges(
        "normalize_changes",
        is_error_state,
        {True: "build_contract_verdict", False: "select_test_scope"},
    )
    builder.add_edge("select_test_scope", "build_contract_verdict")
    builder.add_conditional_edges(
        "build_contract_verdict",
        is_error_state,
        {True: "build_final_verdict", False: "run_regression"},
    )
    builder.add_conditional_edges(
        "run_regression",
        is_error_state,
        {True: "build_final_verdict", False: "parse_test_results"},
    )
    builder.add_edge("parse_test_results", "build_final_verdict")
    builder.add_edge("build_final_verdict", "analyze_risks")
    builder.add_conditional_edges(
        "analyze_risks",
        has_diagnosable_failures,
        {True: "diagnose_failures", False: "build_enhanced_report"},
    )
    builder.add_edge("diagnose_failures", "build_enhanced_report")
    builder.add_edge("build_enhanced_report", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_after=interrupt_after,
        name="API Guardian Regression Graph",
    )


# CLI 使用的独立 graph 保持无数据库依赖；HTTP Service 会创建带 PostgreSQL checkpointer 的实例。
guardian_graph = create_guardian_graph()
