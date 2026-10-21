# API Guardian Intelligent Analysis（Phase 4）

本文记录 API Guardian Phase 4 的结构化智能解释、故障降级、Langfuse、Prometheus、Evaluation、PostgreSQL checkpoint 回归，以及真实 DeepSeek E2E 证据。Phase 4 没有改变 oasdiff、Schemathesis、Scope Selection 或 PASS/FAIL/ERROR 的任何确定性判定规则。

验证日期：2026-09-02（Asia/Shanghai）

## 1. 阶段结论

最终执行语义为：

```text
deterministic contract/regression pipeline
  -> build_final_verdict
  -> analyze_risks
  -> diagnose_failures（仅存在 regression failure 时）
  -> build_enhanced_report
  -> END
```

Final Verdict 在任何 LLM 调用之前形成。模型只能解释白名单事实，不能新增/删除 ContractChange、改变 Scope、改变 Regression 结果或改变 Final Verdict。

真实主 Demo 使用 oasdiff 1.28.0、Schemathesis 4.24.3、PostgreSQL、FastAPI/JWT、LangGraph checkpoint 和 DeepSeek `deepseek-v4-flash`：

```json
{
  "http_status": 200,
  "scan_status": "completed",
  "contract_verdict": "fail",
  "regression_verdict": "fail",
  "final_verdict": "fail",
  "analysis_degraded": false,
  "risk_explanations": 1,
  "failure_diagnoses": 1
}
```

## 2. Phase 4 新增/修改文件

### 2.1 新增

| 文件 | 作用 |
| --- | --- |
| `app/services/guardian/risk.py` | ContractChange 白名单输入、RiskAnalyzer、Structured Output、Langfuse 安全 metadata 与指标 |
| `app/services/guardian/diagnosis.py` | RegressionFailure 白名单证据、FailureDiagnoser、证据子集校验 |
| `app/services/guardian/prompts/risk_explanation.md` | 风险解释约束 Prompt |
| `app/services/guardian/prompts/failure_diagnosis.md` | 回归失败诊断约束 Prompt |
| `evals/guardian/llm_cases.jsonl` | 20 条人工 Risk/Diagnosis 评测样本 |
| `evals/guardian/llm_harness.py` | 非 exact-match 的结构/grounding/evidence/hallucination 评测 |
| `tests/guardian/test_analysis.py` | Structured Output、Prompt 白名单、降级、graph、截断与指标测试 |
| `tests/guardian/test_llm_infrastructure.py` | 现有 LLMService Structured Output 与 callback 复用验证 |
| `docs/guardian-phase4.md` | 本阶段开发与验收记录 |

### 2.2 修改

| 文件 | Phase 4 变化 |
| --- | --- |
| `app/services/guardian/models.py` | RiskLevel、RiskExplanation、FailureCategory、FailureDiagnosis、GuardianAnalysisReport |
| `app/services/guardian/state.py` | 增加结构化 analysis 字段及 checkpoint rehydration |
| `app/services/guardian/nodes.py` | analyze_risks、diagnose_failures、build_enhanced_report |
| `app/services/guardian/graph.py` | 在确定性 final verdict 后挂载三个 Phase 4 node |
| `app/services/guardian/service.py` | 注入 analyzer、持久化增强报告与低基数扫描指标 |
| `app/services/guardian/repository.py` | 保存真实 analysis_degraded，不新增表 |
| `app/services/guardian/__main__.py` | CLI 输出安全 analysis 摘要，默认可禁用 LLM |
| `app/schemas/guardian.py` | Scan/Detail 响应增加 analysis_degraded |
| `app/services/llm/registry.py` | 复用 ChatOpenAI 注册通用 OpenAI-compatible 默认模型 |
| `app/services/llm/service.py` | 复用现有 tenacity/总超时，增加结构化方法、callback config、禁用 fallback 与脱敏日志 |
| `app/core/config.py` | 通用 OpenAI-compatible 与 Guardian analysis 开关/上限/超时 |
| `app/core/observability.py` | 仅在 Langfuse 开启且 key 完整时创建 callback |
| `app/core/metrics.py` | Guardian 扫描和 LLM 低基数 Prometheus 指标 |
| `app/core/langgraph/graph.py` | 仅让既有 chatbot callback 在 handler 存在时传入，行为不变 |
| `tests/guardian/test_phase3.py` | 增强报告持久化、降级、Phase 4 checkpoint 和真实 DeepSeek E2E |
| `.env.example` / `README.md` | 配置、安全边界、LLM disabled 和运行说明 |

没有新增 Python 依赖、数据库表或 Alembic migration。

## 3. 领域 Schema

`RiskExplanation`：

- `change_id`
- `risk_level`: low / medium / high / critical
- `impact`
- `affected_consumer`
- `recommendation`
- `confidence`: 0.0～1.0

`FailureDiagnosis`：

- `failure_id`
- `category`: contract_mismatch / validation / server_error / auth / network / business_logic / unknown
- `summary`
- `possible_cause`
- `evidence`
- `recommendation`
- `confidence`: 0.0～1.0

两个模型均使用 `extra="forbid"`。Diagnosis 额外强制 `possible_cause` 包含不确定性语义；`failure_id` 必须与确定性输入一致；`evidence` 必须是 `available_evidence` 的严格子集。

## 4. LLM 输入与 Prompt 安全

Risk 输入仅由 ContractChange 的 change_id、summary、severity、method、path、operation_id、section 复制形成。Diagnosis 输入仅由安全 RegressionFailure、OperationRef、RegressionCheck、可选关联 change/risk 和显式 available_evidence 形成。

两个 Prompt 都禁止虚构源码文件、函数、行号、数据库/SQL、stack trace、日志、Git commit、部署事实、微服务、SDK 和具体 consumer。原始 OpenAPI、oasdiff JSON、Schemathesis NDJSON、HTTP headers/body、Secret、JWT、DSN、环境变量和本地路径不会进入 Prompt、state、checkpoint、Langfuse 或数据库。

## 5. 现有 LLMService 复用

Guardian 没有创建 DeepSeekClient 或新 SDK。它继续调用项目级 `LLMService.call`，复用：

- `ChatOpenAI` 的 OpenAI-compatible 接口；
- Pydantic `with_structured_output`；
- tenacity 指数退避与既有重试次数；
- `LLM_TOTAL_TIMEOUT` 总预算；
- LangChain RunnableConfig / Langfuse callback；
- 现有模型 registry。

Guardian 显式关闭跨模型 fallback，避免一个分析请求静默切换到未配置 provider。模型由服务端配置，用户不能传 model、temperature 或 Prompt。

DeepSeek V4 当前默认启用思考模式，真实调用返回过 `Thinking mode does not support this tool_choice`。按官方 Chat Completions 契约，只对 `api.deepseek.com` 的 Guardian Structured Output 添加：

```json
{"thinking": {"type": "disabled"}}
```

其他 OpenAI-compatible provider 不收到该扩展参数。兼容修复后，真实 Risk、Diagnosis 和完整 HTTP E2E 均通过。

## 6. Degradation 与数量上限

- 主动配置 `GUARDIAN_LLM_ENABLED=false`：`analysis.enabled=false`、`analysis.degraded=false`，无需分析不算故障。
- 应分析但任一 Risk/Diagnosis 出现 key 缺失、400/429/5xx、timeout、网络错误、解析错误、拒绝或 Pydantic 校验失败：保留已成功结果，写入稳定 warning code，并设置 `analysis.degraded=true`。
- LLM 或 Langfuse 故障不会修改 contract/regression/final verdict，也不会把 completed Scan 变成 failed。
- 每 Scan 默认最多 10 个 Risk 和 10 个 Diagnosis；按确定性顺序选取，超出时 `analysis.truncated=true`。
- 每个 analysis node 默认 30 秒预算。

## 7. GuardianReport

原有确定性报告字段不变，新增：

```json
{
  "analysis": {
    "enabled": true,
    "degraded": false,
    "risk_explanations": [],
    "failure_diagnoses": [],
    "warnings": [],
    "truncated": false
  }
}
```

`GuardianScan.analysis_degraded` 使用 Phase 3 已存在的列；增强结果继续保存在 `GuardianReportRecord.payload`。没有 raw model response、prompt log 或 llm_call 表。

## 8. Langfuse 与 Prometheus

Langfuse 复用现有 callback。Runnable metadata 只包含 `scan_id`、固定 `analysis_type`、change/failure ID、model、provider；callback 未配置或自身失败不影响主流程。本次机器已开启 tracing 开关，但未配置 Langfuse public/secret key，因此代码接入已验证，未产生真实远程 trace。

Prometheus 新增低基数指标：

```text
guardian_scans_total{final_verdict}
guardian_scan_duration_seconds
guardian_llm_calls_total{analysis_type,status}
guardian_llm_duration_seconds{analysis_type}
guardian_analysis_degraded_total
```

没有 scan_id、user_id、path、operation_id 或 failure_id label。

## 9. Evaluation

确定性 Evaluation（10 cases）：

```json
{
  "contract_verdict_accuracy": 1.0,
  "scope_recall": 1.0,
  "scope_precision": 0.6,
  "regression_verdict_accuracy": 1.0,
  "final_verdict_accuracy": 1.0,
  "error_classification_accuracy": 1.0
}
```

Scope Precision 仍是 Recall-first 策略的观察指标，没有为提高 Precision 牺牲 Recall。

LLM Evaluation（20 cases：10 Risk + 10 Diagnosis）：

```json
{
  "structured_output_validity": 1.0,
  "risk_groundedness": 1.0,
  "diagnosis_category_accuracy": 1.0,
  "evidence_faithfulness": 1.0,
  "recommendation_relevance": 1.0,
  "serious_hallucination_rate": 0.0
}
```

这是小型人工 fixture + 规则评测，不代表生产 SLA，也不是对自然语言做 exact match。

## 10. 真实 DeepSeek 输出摘要

真实 RiskExplanation 把 `response.id: string -> integer` 解释为依赖旧契约的调用方可能出现类型校验、反序列化和数据表示兼容风险，风险等级为 high，confidence 为 0.9；没有声称具体源码、服务、数据库或 consumer。

真实 FailureDiagnosis 将 `response_schema_conformance`、HTTP 200、JsonSchemaError 和相关 ContractChange 归类为 contract_mismatch；使用“可能”描述实现与新 integer schema 尚未同步，evidence 逐项来自白名单，confidence 为 0.7。

## 11. Checkpoint 与测试结果

Phase 4 PostgreSQL 恢复测试在确定性 verdict 完成后中断，确认 checkpoint 写入，关闭首个 service/checkpointer，再创建新 graph/service 并使用同一 thread ID 恢复执行 Risk、Diagnosis 和 enhanced report。恢复后的 final verdict 以及 Mock Risk/Diagnosis 与 uninterrupted baseline 一致。

最终质量门：

```text
Guardian pytest: 101 passed, 2 third-party deprecation warnings
Guardian Ruff: pass
Guardian Ruff format: pass（39 files）
Guardian Pyright: 0 errors, 0 warnings
Real DeepSeek full HTTP E2E: 1 passed
```

全仓 baseline 保持为：Ruff 8 个既有错误、format 12 个既有文件、Pyright 5 个既有 MCP 类型错误；Guardian 没有新增错误。未修改这些无关上游问题。

## 12. 已知限制

- LLM 输入只含 API/schema 级证据，Diagnosis 是排查假设，不是源码 root cause。
- 每个 change/failure 独立调用模型，虽然有限额，延迟和成本仍随数量增长。
- LLM Evaluation 数据集只有 20 条，后续收尾可扩充真实脱敏案例并增加人工复核。
- 本次未配置 Langfuse key，所以没有真实远程 trace 验证；配置 key 后会启用现有 callback。
- V1 仍不支持需要用户 Authorization 的目标 API、external `$ref`、完整 DNS pinning 或分布式扫描队列。
- 控制台中部分上游中文日志在当前 Windows code page 下显示乱码，不影响结构化报告和 UTF-8 持久化。

Phase 4 完成后核心功能冻结。后续只进入 README、架构图、Demo、Evaluation 报告、部署说明、简历描述和面试材料等收尾工作。
