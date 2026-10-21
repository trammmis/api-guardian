# API Guardian Backend Service（Phase 3）

本文记录 API Guardian Phase 3 的实际实现、HTTP/JWT/SQLModel 边界、PostgreSQL LangGraph checkpoint、中断恢复证据、真实主 Demo 与质量基线。Phase 3 只把 Phase 2 Core 包装为可调用、可持久化、可恢复的后端服务，没有实现任何 Phase 4 LLM、Langfuse 或 Prometheus 功能。

验证日期：2026-09-01（Asia/Shanghai）

## 1. 阶段结论

Phase 2 Core 与 CLI 保持独立，新增 HTTP 调用路径为：

```text
JWT multipart request
  -> GuardianService
  -> durable .guardian_runs/<scan_id>/input
  -> GuardianRepository.create_scan / mark_running
  -> PostgreSQL-checkpointed guardian graph
  -> deterministic GuardianReport
  -> GuardianScan + GuardianReportRecord 同事务完成
  -> GuardianScanResponse
```

真实 HTTP 主 Demo 使用 oasdiff 1.28.0、Schemathesis 4.24.3、本地 fixture API 和 PostgreSQL 16.15：

```json
{
  "http_status": 200,
  "scan_status": "completed",
  "contract_verdict": "fail",
  "regression_verdict": "fail",
  "final_verdict": "fail"
}
```

Guardian 检测 ERROR 与服务失败已经分离：网络/工具 ERROR 保存为 `completed + final_verdict=error`；数据库等未处理内部异常保存为 `failed`，不伪造 final verdict。

## 2. Phase 3 新增/修改文件

### 2.1 新增

| 文件 | 作用 |
| --- | --- |
| `app/api/v1/guardian.py` | 三条 JWT/rate-limited Guardian HTTP route、受限流式上传和 4xx/5xx 边界 |
| `app/models/guardian.py` | GuardianScan、GuardianReportRecord 两张 SQLModel 表 |
| `app/schemas/guardian.py` | Scan/Detail/Report/Error HTTP schema；不直接暴露 ORM |
| `app/services/guardian/repository.py` | 在线程中执行同步 SQLModel 短事务的异步 Repository |
| `app/services/guardian/service.py` | workspace、DB 生命周期、graph、report、checkpoint 和 owner 查询协调 |
| `alembic/versions/e3a1b7c9d2f4_add_guardian_tables.py` | 两张 Guardian 业务表正式 migration |
| `app/core/langgraph/tools/calculator.py` | 阻塞 app.main 的上游缺失静态工具最小兼容实现 |
| `tests/guardian/test_phase3.py` | HTTP/JWT/owner/DB/migration/secret/checkpoint/真实工具验收 |
| `tests/guardian/test_calculator_compat.py` | calculator 安全算术和 app.main route mount 验证 |
| `docs/guardian-phase3.md` | 本阶段开发与验收记录 |

### 2.2 修改

| 文件 | Phase 3 变化 |
| --- | --- |
| `app/services/guardian/models.py` | 增加白名单 GuardianReport 及其嵌套 DTO |
| `app/services/guardian/state.py` | 保持 TypedDict，并增加 checkpoint 边界 rehydration |
| `app/services/guardian/graph.py` | 允许显式注入 checkpointer/test interrupt；默认 CLI 仍无 DB |
| `app/services/guardian/nodes.py` | Schemathesis 执行前再次做 target DNS/IP 校验 |
| `app/services/guardian/subprocess.py` | 兼容 Windows psycopg Selector 与 asyncio subprocess Proactor 冲突 |
| `app/api/v1/api.py` | 挂载 `/guardian` router |
| `app/main.py` | lifespan 预热/关闭独立 Guardian checkpoint service |
| `app/core/config.py` | runs dir 与 Guardian POST/GET rate limit 配置 |
| `app/api/v1/auth.py` | 非法 Bearer token 日志不再记录任何 token 片段 |
| `alembic/env.py` | 仅导入 Guardian SQLModel metadata |
| `.env.example` | runs dir 与 rate limit 示例 |
| `.gitignore` / `.dockerignore` | 忽略 `.guardian_runs/` |
| `README.md` | HTTP、durable volume、SSRF 和 V1 安全边界 |

Phase 3 没有新增 Python 依赖；SQLModel、Alembic、psycopg、langgraph-checkpoint-postgres、FastAPI multipart 和 slowapi 都来自现有项目。

## 3. 数据模型

### 3.1 GuardianScan

`guardian_scan` 保存：

- UUID `id`、JWT `user_id`
- `pending/running/completed/failed` status 与稳定 phase
- nullable contract/regression/final verdict
- Base/Head Spec SHA256
- oasdiff/Schemathesis 版本
- JSON policy snapshot
- Phase 3 固定为 false 的 `analysis_degraded`
- safe error code/summary
- created/started/completed/updated UTC timestamps

不保存 Spec 内容、workspace path、target header、raw stdout/stderr、raw oasdiff、raw NDJSON、request/response body 或 Secret。

### 3.2 GuardianReportRecord

`guardian_report_record` 保存 UUID `id`、唯一外键 `scan_id`、白名单 JSON `payload` 和 `generated_at`。完成 Scan 和 upsert Report 在同一数据库事务中提交。

payload 只由 Pydantic GuardianReport 构建：Spec SHA256、工具版本/安全摘要、policy、normalized ContractChange、selected scope、normalized RegressionResult、三个 verdict 和 safe errors。

## 4. Alembic

正式 revision：

```text
c7f2a08e4b1d -> e3a1b7c9d2f4
```

在空白 PostgreSQL 16.15 测试库真实执行 `alembic upgrade head` 后，业务表为：

```text
user
thread
session
chat_message
guardian_scan
guardian_report_record
```

`alembic_version=e3a1b7c9d2f4`。LangGraph 的 checkpoints/checkpoint_blobs/checkpoint_writes/checkpoint_migrations 继续由 `AsyncPostgresSaver.setup()` 管理，仍在 Alembic exclude list 中。

## 5. FastAPI 与 JWT ownership

已挂载：

```text
POST /api/v1/guardian/scans
GET  /api/v1/guardian/scans/{scan_id}
GET  /api/v1/guardian/scans/{scan_id}/report
```

三条 route 都复用现有 `get_current_user` 和 slowapi limiter。`user_id` 只来自 JWT User，POST body 不能指定 owner。GET Scan/Report 在 Repository SQL 查询中同时过滤 `scan_id + user_id`，跨用户访问统一返回 404。

POST 受限分块读取 multipart；允许 JSON/YAML Content-Type，但最终仍调用 OpenAPI parser 校验实际内容。单 Spec 超过 5 MiB 返回 413，不先无上限读入；服务端只写 `base.json|yaml` 和 `head.json|yaml`，不使用上传文件名。

同一进程使用 user lock，并在 Repository 检查该用户是否已有 pending/running Scan；存在时返回 409，防止同一用户并发启动大量 Schemathesis 进程。

## 6. GuardianService 与 Repository

GuardianService 负责：服务端 scan ID、durable Spec、初次输入/SSRF 校验、Scan 生命周期、独立 checkpoint graph 调用/恢复、白名单报告构造、结果持久化和 HTTP DTO。

GuardianRepository 只负责数据库。所有公开方法为 async，但每个同步 SQLModel `Session` 短事务都通过 `asyncio.to_thread` 执行，没有在 FastAPI/LangGraph event loop 直接运行同步事务，也没有改造上游 DatabaseService。

Graph node 仍是纯 state -> state delta，不直接写数据库。

## 7. PostgreSQL Checkpointer 与恢复

Guardian 使用自己的 psycopg AsyncConnectionPool 和 `AsyncPostgresSaver`，不复用或修改 chatbot graph 实例。当前 LangGraph 实测中，顶层设置非空 `checkpoint_ns="guardian"` 会被解释为子图路径并报：

```text
Subgraph guardian not found
```

因此采用当前版本可工作的稳定隔离方式：

```text
thread_id = guardian:<scan_id>
top-level checkpoint_ns = empty
```

这与 chatbot session ID 不重叠，并提供清晰的 Guardian key space。

真实恢复测试过程：

1. 不带中断运行 baseline，保存最终结果。
2. 新 Scan 在 `build_contract_verdict` 后触发 test-only interrupt。
3. 查询 PostgreSQL `checkpoints`，确认 `guardian:<scan_id>` 已有记录。
4. GuardianScan 保持 `running`，durable Spec 保留。
5. 关闭第一个 Service/连接池，创建新的 Service/graph/checkpointer。
6. 使用同一 scan ID 调用 `ainvoke(None, config)` 恢复。
7. 后续执行 regression 和 final verdict，Scan/Report 完成。
8. 恢复结果的 ContractChange、ScopeSelection、RegressionResult 和 final verdict 与 uninterrupted baseline 一致。

结果：PASS。

### 7.1 Pydantic checkpoint 实测

嵌套 Pydantic DTO 能被当前 checkpointer 保存并继续驱动节点，但 `StrEnum` verdict 往返后实测恢复为普通字符串。直接持久化时出现真实错误：

```text
AttributeError: 'str' object has no attribute 'value'
```

按照 Phase 3 约束，GuardianState 仍是 TypedDict；只在 checkpoint/service 边界使用 `model_validate` 和 `GuardianVerdict(...)` 恢复 DTO/enum，不改变领域对象或对外 schema。

## 8. Windows 事件循环兼容

真实 HTTP 工具测试发现：Windows 上 psycopg async 要求 Selector loop，而 `asyncio.create_subprocess_exec` 管道要求 Proactor loop。HTTP 主 Demo 首次因此在 `contract_diff` 得到受控 internal error。

最终实现保持同一 ControlledSubprocessRunner、`create_subprocess_exec` 和无 shell 策略：仅当 Windows HTTP 当前为 Selector loop 时，在专用 Proactor 线程运行原受控异步子进程协程；CLI 的默认 Proactor 路径不变。timeout、stdout/stderr limit、运行中 NDJSON 目录监控和 Scan artifact budget 均复用原实现。

## 9. GuardianReport 示例

```json
{
  "scan_id": "<server-uuid>",
  "specs": {
    "base_sha256": "99f0ef...",
    "head_sha256": "e645eb..."
  },
  "tools": {
    "oasdiff_version": "1.28.0",
    "schemathesis_version": "4.24.3",
    "contract_run": {
      "name": "oasdiff",
      "version": "1.28.0",
      "exit_code": 1,
      "artifact_bytes": 1351
    },
    "regression_run": {
      "tool_name": "schemathesis",
      "tool_version": "4.24.3",
      "exit_code": 1,
      "total_operations": 1,
      "passed_operations": 0,
      "failed_operations": 1,
      "error_operations": 0,
      "skipped_operations": 0
    }
  },
  "contract": {
    "verdict": "fail",
    "changes": [
      {
        "change_id": "response-property-type-changed",
        "severity": "error",
        "method": "GET",
        "path": "/items/{item_id}",
        "operation_id": "getItem"
      }
    ]
  },
  "scope": {
    "operations": [
      {
        "method": "GET",
        "path": "/items/{item_id}",
        "operation_id": "getItem"
      }
    ],
    "fallback_to_all": false
  },
  "regression": {
    "verdict": "fail",
    "results": [
      {
        "operation": {
          "method": "GET",
          "path": "/items/{item_id}",
          "operation_id": "getItem"
        },
        "outcome": "failed"
      }
    ]
  },
  "final_verdict": "fail",
  "errors": [],
  "analysis_degraded": false
}
```

示例省略时间及部分安全 DTO 字段以便阅读；真实 payload 通过完整 Pydantic schema 校验。

## 10. PASS / FAIL / ERROR 数据库语义

| 情况 | status | contract | regression | final |
| --- | --- | --- | --- | --- |
| 全部通过 | completed | pass | pass | pass |
| Contract breaking，runtime 通过 | completed | fail | pass | fail |
| Runtime regression | completed | pass/fail | fail | fail |
| target/工具执行 ERROR | completed | pass/fail/error | error/null | error |
| Repository/服务未处理异常 | failed | 已有值或 null | 已有值或 null | null |

`analysis_degraded` 在 Phase 3 始终为 false。

## 11. Security 验证

通过测试确认：

- 无 JWT 被拒绝。
- 非法 JWT 日志只包含固定事件名，不包含 token 或 token 片段。
- 用户 B 无法 GET 用户 A 的 Scan 或 Report。
- invalid OpenAPI 与不允许 target 在工具执行前返回 4xx。
- 超限 multipart 在流式读取阶段返回 413，数据库无 Scan。
- 上传文件名不作为磁盘文件名。
- completed report/DB payload 不包含 Authorization、Cookie、raw NDJSON、request_body、response_body 或本地绝对 workspace path。
- 初次 target 校验与 Schemathesis 执行前重解析均保留；redirect 固定为 0。
- raw Schemathesis report 仍只在 Phase 2 Adapter 的受限 TemporaryDirectory 中存在并清理。

V1 防御不是完整 DNS pinning；这是明确已知限制。

## 12. 验收结果

### 12.1 Guardian 局部质量门

| 项目 | 结果 |
| --- | --- |
| Guardian pytest（含真实 HTTP、oasdiff、Schemathesis、PostgreSQL checkpoint） | 71 passed，2 个第三方 websocket deprecation warnings |
| Guardian Ruff | PASS |
| Guardian Ruff format | PASS |
| Guardian Pyright | 0 errors / 0 warnings |
| Alembic upgrade | PASS，空白 PostgreSQL 到 `e3a1b7c9d2f4` |
| app.main import / Guardian route mount | PASS |
| 独立 Phase 2 CLI | contract FAIL / regression FAIL / final FAIL，exit code 1 |

### 12.2 Evaluation Harness

```json
{
  "total_cases": 10,
  "contract_cases": 5,
  "regression_cases": 5,
  "contract_verdict_accuracy": 1.0,
  "scope_recall": 1.0,
  "scope_precision": 0.6,
  "regression_verdict_accuracy": 1.0,
  "final_verdict_accuracy": 1.0,
  "error_classification_accuracy": 1.0
}
```

Phase 1/2 指标无退化；scope precision 仍为观察指标，没有以降低 recall 换取 precision。

### 12.3 全仓 baseline

| 检查 | Phase 0/1/2 baseline | Phase 3 | 差异 |
| --- | --- | --- | --- |
| Ruff lint | 8 个上游问题 | 相同 8 个 | 0 新增 |
| Ruff format | 12 个上游文件 | 相同 12 个 | 0 新增 |
| Pyright | 6 个上游错误 | 5 个上游 MCP 类型错误 | -1（calculator 缺失已兼容） |
| 非 slow pytest | 2 个 calculator collection errors | 66 passed / 12 deselected | collection 阻塞已消除，且新增 JWT 日志脱敏测试通过 |

未修复剩余 Guardian 无关 Ruff、format、MCP Pyright 问题。

## 13. 已知限制与 Phase 4 前建议

1. V1 不支持需要用户提供 Authorization、Cookie 或 API Key 的目标 API。
2. `.guardian_runs` 没有生命周期清理或对象存储；生产必须挂持久化 volume，并由运维制定容量/保留策略。
3. 同用户 active-scan gate 在单实例内有 asyncio lock，数据库查询提供跨实例可见性，但没有数据库 advisory lock；多实例极端竞态仍可能同时创建 Scan。
4. 同步 HTTP 请求没有队列；客户端断开不等同于自动恢复调度。`resume_scan` 已验证可恢复，但 Phase 3 不提供自动扫描 running records 的后台调度器。
5. 顶层 LangGraph 非空 checkpoint namespace 在当前版本不可用，实际隔离采用 `guardian:` thread 前缀。
6. 完整 DNS pinning 未实现；当前是 initial validation + pre-execution revalidation + redirect disabled。
7. Windows HTTP 模式的受控子进程使用专用 Proactor 线程；请求取消时仍受硬 timeout 限制，不引入常驻 worker 或任务队列。
8. GuardianReport 只保留安全失败 evidence；Phase 4 diagnosis 不能依赖原始 body/header。
9. Phase 4 开始前建议先决定 `.guardian_runs` 运维保留策略、running Scan 的人工/启动恢复入口，以及是否需要数据库级并发锁；这些不影响当前单实例 MVP 验收。

本文完成后立即停止，没有实现 Phase 4。
