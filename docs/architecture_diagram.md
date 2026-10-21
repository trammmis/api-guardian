# API Guardian Architecture Diagram Specification

本文只定义要画的架构图，不引入当前代码中不存在的组件。

## 图的目标

一张横向主流程图同时表达三件事：

1. 确定性工具发现事实。
2. LangGraph 编排状态、分支和恢复。
3. LLM 只解释证据，不参与 verdict。

## 主画布

从左到右分为六个区域。

### 1. Input / Client

节点：

- `Developer / CI / PR workflow`
- `Base OpenAPI`
- `Head OpenAPI`
- `target_base_url`

连线到 FastAPI，并标注：`multipart + JWT`。

不要画 GitHub Actions 自动评论；项目没有实现该能力。

### 2. HTTP and service boundary

外框：`FastAPI Backend`

内部节点：

- `Guardian Route`
  - JWT authentication
  - owner isolation
  - rate limit
  - 5 MiB bounded upload
- `GuardianService`
  - durable input workspace
  - scan lifecycle
  - safe report construction
- `GuardianRepository`
  - short async-facing DB transactions

主箭头：`Route -> Service -> Guardian Graph`。

### 3. LangGraph deterministic path

大外框：`Guardian StateGraph`

按真实 node 顺序画：

```text
prepare_scan
  -> contract_diff
  -> normalize_changes
  -> select_test_scope
  -> build_contract_verdict
  -> run_regression
  -> parse_test_results
  -> build_final_verdict
```

在 `prepare_scan` 旁标注：

- OpenAPI parse / self-contained check
- SHA256
- allowlist + SSRF validation/revalidation

在 `build_final_verdict` 旁用醒目标签：

```text
Deterministic
ERROR > FAIL > PASS
```

错误分支用虚线表示从 prepare/diff/normalize/run 节点进入 verdict 构建；不要让图看起来所有失败都继续调用 Schemathesis。

### 4. Tool lane

在 graph 下方画两个外部进程节点：

- `oasdiff 1.28.0`
  - receives Base + Head
  - returns adapter-safe ContractChangeCandidate
- `Schemathesis 4.24.3`
  - selected operation only
  - response_schema_conformance
  - examples, coverage
  - deterministic, max examples 1
  - redirects 0

`contract_diff -> oasdiff -> normalize_changes`。

`select_test_scope -> Schemathesis -> parse_test_results`。

在 tool lane 下方加安全注释：`raw stdout/stderr, raw oasdiff JSON and raw NDJSON stay outside GuardianState/Report`。

### 5. Optional LLM explanation path

在 `build_final_verdict` 之后画：

```text
analyze_risks
  -> [diagnose_failures when failures exist]
  -> build_enhanced_report
```

侧边外部节点：`Existing LLMService / DeepSeek-compatible provider`。

双向箭头仅连接 Risk/Diagnosis nodes，并标注：

- whitelisted DTO facts only
- Pydantic structured output
- Langfuse callback

在这一区域放一个禁止符号和文字：

```text
LLM cannot change
Contract / Regression / Final verdict
```

降级箭头：`LLM failure -> analysis_degraded=true -> report continues`。

### 6. Output and persistence

最终节点：`GuardianReport`

包含：

- spec SHA256
- tool summaries
- normalized contract changes
- selected scope
- normalized regression results
- deterministic verdicts
- safe errors
- optional analysis

旁边画 `PostgreSQL`，内部只列：

- `GuardianScan`
- `GuardianReportRecord`

再画独立小框 `AsyncPostgresSaver`：

- thread_id = scan_id
- checkpoint namespace = guardian
- graph interruption recovery

`GuardianService -> PostgreSQL` 表示业务历史持久化；`StateGraph <-> AsyncPostgresSaver` 表示执行存档。两种连线颜色不同，防止把 report 与 checkpoint 混为一谈。

## Observability side lane

画在主流程上方，不作为决策节点：

- `Langfuse`：仅接 LLM 白名单 trace；不可用不影响主流程。
- `Prometheus`：低基数 Guardian scan/LLM counters 与 durations。
- `structlog`：安全结构化日志。

不要画 Langfuse 为 verdict 输入。

## Trust boundaries

使用三种背景色：

1. 用户输入边界：浅橙色。
2. 确定性核心：蓝色。
3. 可降级解释层：紫色。

在边界线上列出：

- exact host allowlist
- private target disabled by default
- external `$ref` rejected
- no arbitrary CLI args
- no headers/body/secrets persistence

## 建议图例

- 实线箭头：正常数据流。
- 虚线箭头：错误/降级/恢复路径。
- 蓝色节点：deterministic fact or verdict。
- 紫色节点：LLM explanation only。
- 灰色节点：persistence/observability infrastructure。
- 红色小锁：安全边界。

## 图中必须出现的核心句

```text
Deterministic tools discover facts.
LangGraph orchestrates state and recovery.
LLM explains evidence.
```

## 图中不得出现

- Celery、Redis、Kafka、MQ
- Multi-Agent、MCP、RAG、pgvector Guardian memory
- GitHub Actions / PR Comment automation
- automatic fix / patch generation
- custom fuzzer
- frontend / WebSocket / SSE
- object storage
- arbitrary authenticated API testing
