"""Contract-only Guardian graph 的可独立测试节点."""

import asyncio

from app.services.guardian.diagnosis import FailureDiagnoser
from app.services.guardian.errors import GuardianErrorCode, GuardianException
from app.services.guardian.models import (
    ChangeSeverity,
    ContractChange,
    GuardianError,
    GuardianPolicy,
    GuardianVerdict,
    OperationRef,
    RegressionFailure,
    RegressionOutcome,
    RegressionResult,
    RiskExplanation,
    ScopeSelection,
)
from app.services.guardian.oasdiff import OasdiffAdapter, OasdiffPort, normalize_contract_changes
from app.services.guardian.schemathesis import (
    SchemathesisAdapter,
    SchemathesisPort,
    determine_final_verdict,
    determine_regression_verdict,
)
from app.services.guardian.scope import determine_contract_verdict, select_test_scope
from app.services.guardian.security import prepare_openapi_spec, validate_target_base_url
from app.services.guardian.risk import RiskAnalyzer
from app.services.guardian.state import GuardianState, rehydrate_guardian_state


class GuardianNodes:
    """通过构造器注入 Adapter，使每个节点可离线单元测试."""

    def __init__(
        self,
        policy: GuardianPolicy,
        oasdiff: OasdiffPort | None = None,
        schemathesis: SchemathesisPort | None = None,
        risk_analyzer: RiskAnalyzer | None = None,
        failure_diagnoser: FailureDiagnoser | None = None,
    ) -> None:
        """初始化节点集合，不执行任何外部调用."""
        self._policy = policy
        self._oasdiff = oasdiff or OasdiffAdapter(
            timeout_seconds=policy.subprocess_timeout_seconds,
            max_artifact_bytes=policy.max_artifact_bytes,
        )
        self._schemathesis = schemathesis or SchemathesisAdapter(
            timeout_seconds=policy.subprocess_timeout_seconds,
            request_timeout_seconds=policy.regression_request_timeout_seconds,
            max_examples=policy.regression_max_examples,
            max_artifact_bytes=policy.max_artifact_bytes,
        )
        self._risk_analyzer = risk_analyzer or RiskAnalyzer()
        self._failure_diagnoser = failure_diagnoser or FailureDiagnoser()

    async def prepare_scan(self, state: GuardianState) -> GuardianState:
        """校验输入、SSRF 策略和两个 self-contained OpenAPI 文件."""
        node = "prepare_scan"
        try:
            base_path = self._required_string(state, "base_spec_path")
            head_path = self._required_string(state, "head_spec_path")
            target_url = self._required_string(state, "target_base_url")
            normalized_target = await validate_target_base_url(
                target_url,
                self._policy.allowed_target_hosts,
                self._policy.allow_private_targets,
            )
            base_spec, head_spec = await asyncio.gather(
                asyncio.to_thread(prepare_openapi_spec, base_path, self._policy.max_openapi_bytes),
                asyncio.to_thread(prepare_openapi_spec, head_path, self._policy.max_openapi_bytes),
            )
            consumed = base_spec.size_bytes + head_spec.size_bytes
            if consumed > self._policy.max_scan_artifact_bytes:
                raise GuardianException(
                    GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED,
                    "单次 Scan artifact 总量超过配置限制",
                )
            return {
                "target_base_url": normalized_target,
                "policy": self._policy,
                "base_spec_sha256": base_spec.sha256,
                "head_spec_sha256": head_spec.sha256,
                "base_operations": base_spec.operations,
                "head_operations": head_spec.operations,
                "artifact_bytes_consumed": consumed,
                "completed_nodes": self._completed(state, node),
            }
        except GuardianException as exc:
            return self._error_update(state, node, exc)
        except Exception:
            return self._unexpected_error_update(state, node)

    async def contract_diff(self, state: GuardianState) -> GuardianState:
        """通过 Adapter 执行 oasdiff，不把 raw 输出写入状态."""
        node = "contract_diff"
        try:
            base_path = self._required_string(state, "base_spec_path")
            head_path = self._required_string(state, "head_spec_path")
            consumed = state.get("artifact_bytes_consumed", 0)
            remaining = self._policy.max_scan_artifact_bytes - consumed
            result = await self._oasdiff.compare(base_path, head_path, remaining)
            total = consumed + result.tool.artifact_bytes
            if total > self._policy.max_scan_artifact_bytes:
                raise GuardianException(
                    GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED,
                    "单次 Scan artifact 总量超过配置限制",
                )
            return {
                "contract_candidates": result.candidates,
                "contract_tool": result.tool,
                "artifact_bytes_consumed": total,
                "completed_nodes": self._completed(state, node),
            }
        except GuardianException as exc:
            return self._error_update(state, node, exc)
        except Exception:
            return self._unexpected_error_update(state, node)

    async def normalize_changes(self, state: GuardianState) -> GuardianState:
        """集中归一化 severity，并生成稳定 ContractChange."""
        node = "normalize_changes"
        try:
            changes = normalize_contract_changes(state.get("contract_candidates", ()))
            return {
                "contract_changes": changes,
                "completed_nodes": self._completed(state, node),
            }
        except GuardianException as exc:
            return self._error_update(state, node, exc)
        except Exception:
            return self._unexpected_error_update(state, node)

    async def select_test_scope(self, state: GuardianState) -> GuardianState:
        """以 Recall 优先策略选择 affected operations."""
        node = "select_test_scope"
        try:
            selection = select_test_scope(
                state.get("contract_changes", ()),
                state.get("head_operations", ()),
                base_operations=state.get("base_operations", ()),
            )
            return {
                "selected_scope": selection,
                "completed_nodes": self._completed(state, node),
            }
        except Exception:
            return self._unexpected_error_update(state, node)

    async def build_contract_verdict(self, state: GuardianState) -> GuardianState:
        """构造不依赖 LLM 的 PASS、FAIL 或 ERROR."""
        node = "build_contract_verdict"
        verdict = determine_contract_verdict(
            state.get("contract_changes", ()),
            has_error=bool(state.get("errors")),
            fail_severities=self._policy.contract_fail_severities,
        )
        return {
            "contract_verdict": verdict,
            "completed_nodes": self._completed(state, node),
        }

    async def run_regression(self, state: GuardianState) -> GuardianState:
        """执行前重校验 SSRF，再按 selected scope 独立执行 Schemathesis."""
        node = "run_regression"
        try:
            head_path = self._required_string(state, "head_spec_path")
            target_url = self._required_string(state, "target_base_url")
            # Phase 3 HTTP 暴露面在真实网络调用前再次解析，降低 DNS rebinding 时间窗。
            target_url = await validate_target_base_url(
                target_url,
                self._policy.allowed_target_hosts,
                self._policy.allow_private_targets,
            )
            selection = state.get("selected_scope")
            if not isinstance(selection, ScopeSelection):
                raise GuardianException(GuardianErrorCode.INVALID_INPUT, "缺少安全的 regression scope")
            consumed = state.get("artifact_bytes_consumed", 0)
            remaining = self._policy.max_scan_artifact_bytes - consumed
            execution = await self._schemathesis.run_regression(
                head_path,
                target_url,
                selection.operations,
                remaining,
            )
            total = consumed + execution.tool.artifact_bytes
            if total > self._policy.max_scan_artifact_bytes:
                raise GuardianException(
                    GuardianErrorCode.SCAN_ARTIFACT_LIMIT_EXCEEDED,
                    "单次 Scan artifact 总量超过配置限制",
                )
            return {
                "regression_results": execution.results,
                "regression_tool": execution.tool,
                "artifact_bytes_consumed": total,
                "completed_nodes": self._completed(state, node),
            }
        except GuardianException as exc:
            return self._error_update(state, node, exc)
        except Exception:
            return self._unexpected_error_update(state, node)

    async def parse_test_results(self, state: GuardianState) -> GuardianState:
        """聚合 Adapter 已白名单解析的 operation results，不读取 raw NDJSON."""
        node = "parse_test_results"
        try:
            selection = state.get("selected_scope")
            if not isinstance(selection, ScopeSelection):
                raise GuardianException(GuardianErrorCode.INVALID_INPUT, "缺少安全的 regression scope")
            verdict = determine_regression_verdict(
                state.get("regression_results", ()),
                selection.operations,
            )
            return {
                "regression_verdict": verdict,
                "completed_nodes": self._completed(state, node),
            }
        except GuardianException as exc:
            return self._error_update(state, node, exc)
        except Exception:
            return self._unexpected_error_update(state, node)

    async def build_final_verdict(self, state: GuardianState) -> GuardianState:
        """以 ERROR > FAIL > PASS 合并两个确定性 verdict."""
        node = "build_final_verdict"
        verdict = determine_final_verdict(
            state.get("contract_verdict"),
            state.get("regression_verdict"),
            has_error=bool(state.get("errors")),
        )
        return {
            "final_verdict": verdict,
            "completed_nodes": self._completed(state, node),
        }

    async def analyze_risks(self, state: GuardianState) -> GuardianState:
        """在确定性 verdict 完成后逐条解释 Contract Change 风险."""
        node = "analyze_risks"
        normalized = rehydrate_guardian_state(state)
        if not self._policy.analysis_enabled:
            return {
                "analysis_enabled": False,
                "risk_explanations": (),
                "analysis_degraded": False,
                "analysis_warnings": (),
                "analysis_truncated": False,
                "completed_nodes": self._completed(normalized, node),
            }
        changes = sorted(normalized.get("contract_changes", ()), key=self._change_sort_key)
        selected = changes[: self._policy.max_risk_analyses_per_scan]
        explanations: list[RiskExplanation] = []
        warnings: list[str] = list(normalized.get("analysis_warnings", ()))
        degraded = bool(normalized.get("analysis_degraded", False))
        truncated = len(changes) > len(selected)
        if truncated:
            warnings.append("risk_analysis_truncated")
        try:
            async with asyncio.timeout(self._policy.analysis_node_timeout_seconds):
                for change in selected:
                    try:
                        explanations.append(
                            await self._risk_analyzer.analyze(
                                change,
                                scan_id=normalized.get("scan_id", "guardian-scan"),
                            )
                        )
                    except Exception:
                        degraded = True
                        warnings.append(f"risk_analysis_failed:{change.change_id}")
        except TimeoutError:
            degraded = True
            warnings.append("risk_analysis_timeout")
        return {
            "analysis_enabled": True,
            "risk_explanations": tuple(explanations),
            "analysis_degraded": degraded,
            "analysis_warnings": tuple(dict.fromkeys(warnings)),
            "analysis_truncated": truncated,
            "completed_nodes": self._completed(normalized, node),
        }

    async def diagnose_failures(self, state: GuardianState) -> GuardianState:
        """基于安全 failure evidence 逐条生成可能原因，不改变 verdict."""
        node = "diagnose_failures"
        normalized = rehydrate_guardian_state(state)
        if not normalized.get("analysis_enabled", self._policy.analysis_enabled):
            return {
                "failure_diagnoses": (),
                "completed_nodes": self._completed(normalized, node),
            }
        failures = self._ordered_failures(normalized.get("regression_results", ()))
        selected = failures[: self._policy.max_failure_diagnoses_per_scan]
        diagnoses = []
        warnings = list(normalized.get("analysis_warnings", ()))
        degraded = bool(normalized.get("analysis_degraded", False))
        truncated = bool(normalized.get("analysis_truncated", False)) or len(failures) > len(selected)
        if len(failures) > len(selected):
            warnings.append("failure_diagnosis_truncated")
        changes = normalized.get("contract_changes", ())
        risks = {risk.change_id: risk for risk in normalized.get("risk_explanations", ())}
        try:
            async with asyncio.timeout(self._policy.analysis_node_timeout_seconds):
                for _, result, failure in selected:
                    related_change = self._related_change(failure.operation, changes)
                    risk = risks.get(related_change.change_id) if related_change is not None else None
                    try:
                        diagnoses.append(
                            await self._failure_diagnoser.diagnose(
                                failure,
                                related_change=related_change,
                                risk=risk,
                                checks=result.checks,
                                scan_id=normalized.get("scan_id", "guardian-scan"),
                            )
                        )
                    except Exception:
                        degraded = True
                        warnings.append(f"failure_diagnosis_failed:{failure.failure_id}")
        except TimeoutError:
            degraded = True
            warnings.append("failure_diagnosis_timeout")
        return {
            "failure_diagnoses": tuple(diagnoses),
            "analysis_degraded": degraded,
            "analysis_warnings": tuple(dict.fromkeys(warnings)),
            "analysis_truncated": truncated,
            "completed_nodes": self._completed(normalized, node),
        }

    async def build_enhanced_report(self, state: GuardianState) -> GuardianState:
        """补齐安全分析默认值；最终报告仍由 Service 白名单构建."""
        node = "build_enhanced_report"
        normalized = rehydrate_guardian_state(state)
        return {
            "analysis_enabled": normalized.get("analysis_enabled", self._policy.analysis_enabled),
            "risk_explanations": normalized.get("risk_explanations", ()),
            "failure_diagnoses": normalized.get("failure_diagnoses", ()),
            "analysis_degraded": normalized.get("analysis_degraded", False),
            "analysis_warnings": normalized.get("analysis_warnings", ()),
            "analysis_truncated": normalized.get("analysis_truncated", False),
            "completed_nodes": self._completed(normalized, node),
        }

    @staticmethod
    def _change_sort_key(change: ContractChange) -> tuple[int, str, str, str]:
        """按 severity 与稳定 operation 标识确定 Risk 调用顺序."""
        rank = {ChangeSeverity.ERROR: 0, ChangeSeverity.WARNING: 1, ChangeSeverity.INFO: 2}
        return (rank[change.severity], change.method or "", change.path or "", change.change_id)

    @staticmethod
    def _ordered_failures(
        results: tuple[RegressionResult, ...],
    ) -> list[tuple[int, RegressionResult, RegressionFailure]]:
        """按 error/failed、operation 与 failure ID 稳定排序失败证据."""
        ranked: list[tuple[int, RegressionResult, RegressionFailure]] = []
        outcome_rank = {RegressionOutcome.ERROR: 0, RegressionOutcome.FAILED: 1}
        for result in results:
            rank = outcome_rank.get(result.outcome, 2)
            for failure in result.failures:
                ranked.append((rank, result, failure))
        return sorted(
            ranked,
            key=lambda item: (item[0], item[1].operation.key, item[2].failure_id),
        )

    @staticmethod
    def _related_change(
        operation: OperationRef | None,
        changes: tuple[ContractChange, ...],
    ) -> ContractChange | None:
        """仅使用 operationId 或 method/path 确定关联 change，不让 LLM 猜测."""
        if operation is None:
            return None
        for change in changes:
            if operation.operation_id and change.operation_id == operation.operation_id:
                return change
            if change.method == operation.method and change.path == operation.path:
                return change
        return None

    def _required_string(self, state: GuardianState, field: str) -> str:
        """读取必填字符串且不把原值写入错误."""
        value = state.get(field)
        if not isinstance(value, str) or not value:
            raise GuardianException(GuardianErrorCode.INVALID_INPUT, f"缺少必填字段 {field}")
        return value

    def _completed(self, state: GuardianState, node: str) -> tuple[str, ...]:
        """返回追加当前节点后的执行轨迹."""
        return (*state.get("completed_nodes", ()), node)

    def _error_update(self, state: GuardianState, node: str, exc: GuardianException) -> GuardianState:
        """把受控异常转换为脱敏状态更新."""
        error = GuardianError(code=exc.code, node=node, message=exc.safe_message)
        update: GuardianState = {
            "errors": (*state.get("errors", ()), error),
            "completed_nodes": self._completed(state, node),
        }
        if node == "prepare_scan":
            # 输入 URL 可能在 userinfo/query 中携带 Secret，失败路径必须主动清空。
            update["target_base_url"] = ""
        return update

    def _unexpected_error_update(self, state: GuardianState, node: str) -> GuardianState:
        """隐藏可能含 Secret 的未知异常细节."""
        return self._error_update(
            state,
            node,
            GuardianException(GuardianErrorCode.INTERNAL_ERROR, "Guardian 节点发生内部错误"),
        )


def is_error_state(state: GuardianState) -> bool:
    """供 graph conditional edge 判断是否应提前收敛."""
    return bool(state.get("errors")) or state.get("contract_verdict") == GuardianVerdict.ERROR


def has_diagnosable_failures(state: GuardianState) -> bool:
    """只有启用分析且存在安全 RegressionFailure 时才进入 diagnosis node."""
    normalized = rehydrate_guardian_state(state)
    return bool(normalized.get("analysis_enabled")) and any(
        result.failures for result in normalized.get("regression_results", ())
    )
