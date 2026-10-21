# API Guardian Regression Vertical Slice（Phase 2）

本文记录 API Guardian Phase 2 的实际实现、Schemathesis 4.24.3 命令契约、NDJSON 白名单 parser、确定性 PASS / FAIL / ERROR 算法、本地真实集成结果与质量基线。Phase 2 只完成 API Regression Vertical Slice，没有进入数据库、API route、checkpointer、LLM 或观测阶段。

验证日期：2026-09-01（Asia/Shanghai）

## 1. 阶段结论

Phase 2 已形成完整可运行闭环：

```text
Base Spec + Head Spec + target_base_url
  -> prepare_scan
  -> contract_diff
  -> normalize_changes
  -> select_test_scope
  -> build_contract_verdict
  -> run_regression
  -> parse_test_results
  -> build_final_verdict
  -> END
```

主 Demo 已使用真实 oasdiff 1.28.0、真实 Schemathesis 4.24.3 和本地 fixture API 运行：

```text
Contract:   string -> integer  => FAIL
Runtime:    Head 要求 integer，API 返回 string => FAIL
Final:      FAIL
```

本阶段保持 Phase 1 的 TypedDict State、Pydantic DTO、Recall 优先 scope、SSRF、self-contained Spec、oasdiff Adapter 和 deterministic contract verdict，没有修改 chatbot graph。

## 2. 新增与修改文件

### 2.1 业务实现

| 文件 | Phase 2 变化 |
| --- | --- |
| `app/services/guardian/models.py` | 增加 RegressionOutcome、Check、Failure、Result、RunSummary、ExecutionResult |
| `app/services/guardian/state.py` | 只增加 regression results/tool/verdict 和 final verdict |
| `app/services/guardian/errors.py` | 增加 Schemathesis execution/report 安全错误码 |
| `app/services/guardian/subprocess.py` | 复用原 runner，增加外部 report path 运行中监控和共享 Scan 预算 |
| `app/services/guardian/schemathesis.py` | Schemathesis 4.24.3 Adapter、命令构造、NDJSON parser 和 verdict 函数 |
| `app/services/guardian/nodes.py` | 增加 run_regression、parse_test_results、build_final_verdict |
| `app/services/guardian/graph.py` | 从 contract graph 扩展为完整 regression graph |
| `app/services/guardian/__main__.py` | 无 FastAPI/DB 调用的结构化 JSON CLI |
| `app/core/config.py` | 增加 `GUARDIAN_SCHEMATHESIS_PATH`，并为 Guardian CLI 静默配置提示 |
| `app/services/__init__.py` | 仅在明确运行 Guardian CLI 时跳过无关 DB/LLM eager initialization |
| `.env.example` | 增加 Schemathesis executable 配置示例 |
| `README.md` | 增加 Phase 2 边界、无认证 MVP 限制和 CLI 用法 |

正常 FastAPI/chatbot 路径仍保留原 `app.services` eager exports；CLI 窄化守卫只识别 `python -m app.services.guardian`。

### 2.2 测试与 Evaluation

新增/扩展：

- `tests/guardian/test_schemathesis.py`
- `tests/guardian/test_schemathesis_integration.py`
- `tests/guardian/test_phase2_graph.py`
- `tests/guardian/test_subprocess.py`
- `tests/guardian/test_graph.py`
- `tests/guardian/test_nodes.py`
- `tests/guardian/test_security.py`
- `tests/guardian/fakes.py`
- `evals/guardian/harness.py`

新增离线 fixtures：

- `schemathesis_pass.ndjson`
- `schemathesis_fail.ndjson`
- `schemathesis_error.ndjson`
- `schemathesis_secret.ndjson`
- `multi_operation_openapi.yaml`
- `oasdiff_warning.json`

## 3. Phase 2 领域对象与 State

### 3.1 Regression DTO

`RegressionOutcome`：

- `passed`
- `failed`
- `error`
- `skipped`

`RegressionCheck` 只保存：

- check name；
- normalized status；
- 固定安全 message 或 `null`。

`RegressionFailure` 只保存：

- 稳定 failure ID；
- `OperationRef`；
- check name；
- 短 failure category；
- 固定安全 message；
- HTTP status code；
- 已验证 phase 名称。

`RegressionResult` 按 operation 保存 outcome、checks、failures 和 duration。`RegressionRunSummary` 保存 Schemathesis 版本、聚合 exit code、artifact bytes 和各 outcome operation 数量。

### 3.2 GuardianState 新增字段

```text
regression_results
regression_tool
regression_verdict
final_verdict
```

没有增加 raw NDJSON、report path、CLI argv、request、response、headers、body 或临时配置字段。Phase 2 仍然没有 checkpointer。

## 4. Schemathesis 真实命令

固定版本：`4.24.3`

每个 selected operation 独立执行：

```text
st run HEAD_SPEC
  --url TARGET_BASE_URL
  --include-operation-id OPERATION_ID
  --checks response_schema_conformance
  --phases examples,coverage
  --max-examples 1
  --generation-deterministic
  --max-redirects 0
  --request-timeout 2.0
  --report ndjson
  --report-ndjson-path REPORT_PATH
  --output-sanitize true
  --no-color
```

operationId 缺失时使用当前 4.24.3 CLI 已验证的精确 operation label：

```text
--include-name "METHOD PATH"
```

Schemathesis 4.24.3 的多个 `--include-*` 条件实际是 OR 语义，不能组合 `--include-method` 与 `--include-path` 表达 AND。单个 exact name 就是其稳定的 `METHOD PATH` operation label。本地真实 integration 使用包含三个 operations 的 Spec，通过该 filter 得到 `1 selected / 3 total`，只执行 `GET /items/{item_id}`。两个目标 operation 会产生两个独立进程。

命令不包含 fuzzing、stateful、Hypothesis database、header/auth 或任意用户附加参数。Windows 子进程环境固定加入 `PYTHONUTF8=1`，且 Adapter 只转发运行必需的系统环境变量，不转发应用 Secret 或 proxy/auth 环境变量。

## 5. NDJSON Parser

Parser 只接受锁定版本观察到的 envelope，每行必须是只有一个顶层事件键的 JSON object。

实际白名单事件：

- `Initialize`
- `LoadingStarted`
- `LoadingFinished`
- `EngineStarted`
- `PhaseStarted`
- `PhaseFinished`
- `SuiteStarted`
- `SuiteFinished`
- `ScenarioStarted`
- `ScenarioFinished`
- `NonFatalError`
- `EngineFinished`

可信正常结束至少要求 `Initialize`、`LoadingFinished`、`ScenarioFinished`、`EngineFinished`。如果 `LoadingFinished.statistic.operations.selected` 存在，独立 operation 运行必须等于 1。

Parser 容忍当前版本可选字段缺失、动态 case ID、多个 Scenario、多个 check，以及 `recorder.interactions[case_id]` 为单 object 或 list 两种真实结构。未知事件、未知 status、多键 envelope、坏 JSON、缺少可信结束事件或 operation label/filter 不一致都返回 ERROR，不做猜测。

明确不读取或不持久化：

- `Initialize.command`；
- `recorder.cases[*].value.headers/body`；
- `interactions[*].request`；
- request/response headers；
- response content/body；
- reproduction command；
- error 中可能含 credential 的 URI；
- raw failure message 中可能包含的 response value。

真实 `JsonSchemaError.message` 会包含响应值，因此领域 `safe_message` 固定归一化为 `Response violates the declared schema`，不复制原始 message。

## 6. Secret 与 Artifact 处理

每个 operation 使用独立 `TemporaryDirectory`：

1. 目录权限尽量设为 `0700`；
2. report 文件预创建并尽量设为 `0600`；
3. subprocess 运行期间每 10 ms 监控文件大小；
4. report 与 stdout/stderr 共享 Scan 剩余预算；
5. 执行结束再次检查完整文件大小；
6. 不截断；超限直接 ERROR；
7. 在临时目录退出前解析成白名单 DTO；
8. Schemathesis subprocess 的 `cwd` 也固定到该临时目录，crash cache 等副产物不会落到工作区；
9. raw NDJSON 与所有 Schemathesis 副产物随临时目录立即清理。

限制保持：

| Artifact | 上限 |
| --- | ---: |
| 单 OpenAPI | 5 MiB |
| 单工具 artifact | 50 MiB |
| 单 Scan 总 artifact | 100 MiB |

测试中的 Secret marker 已覆盖 Initialize command、case Authorization/Cookie、request URI/headers/body、response Set-Cookie/content 和 raw failure message；全部不会出现在 RegressionResult、GuardianState 或 CLI JSON。

## 7. PASS / FAIL / ERROR 算法

### 7.1 单 operation

优先级固定为 `ERROR > FAIL > PASS`：

1. subprocess 无法启动、timeout 或 artifact 超限：ERROR；
2. report 不存在、为空、坏 JSON 或缺少可信事件：ERROR；
3. operation filter 没有精确选择一个 operation：ERROR；
4. NonFatalError 与对应 error 状态、Scenario/Suite/Phase error、非正常结束：ERROR；
5. failure 后又发生 error：ERROR，同时保留已经归一化的安全 failure evidence；
6. 存在真实 `response_schema_conformance/failure`：FAIL；
7. Coverage 正常 success、无 failure/error 且可信结束：PASS；
8. 只有 skipped、目标未真正完成：聚合为 ERROR。

exit code 只做一致性校验，不直接映射 verdict。PASS report 配 exit 1、FAIL report 配 exit 0 等矛盾会升级为 ERROR。

`EngineFinished.stop_reason=completed` 不能覆盖此前的 failure 或 error。

### 7.2 Regression 聚合

```text
任一 operation ERROR/SKIPPED -> regression ERROR
否则任一 operation FAILED   -> regression FAIL
否则所有目标 PASSED         -> regression PASS
结果数量/顺序/scope 不一致   -> regression ERROR
```

空 selected scope 会产生零进程、零 operation summary，并确定性得到 regression PASS。

### 7.3 Final Verdict

```text
任意 state/tool/contract/regression ERROR -> final ERROR
否则 contract FAIL 或 regression FAIL    -> final FAIL
否则 contract PASS 且 regression PASS    -> final PASS
其余不完整状态                            -> final ERROR
```

Contract FAIL 不会短路 regression；只有 ERROR 或无法安全生成 scope 才短路。

## 8. Graph 实际 Flow

正常流程：

```text
START
  -> prepare_scan
  -> contract_diff
  -> normalize_changes
  -> select_test_scope
  -> build_contract_verdict
  -> run_regression
  -> parse_test_results
  -> build_final_verdict
  -> END
```

Contract 阶段 ERROR：

```text
failed contract node
  -> build_contract_verdict(ERROR)
  -> build_final_verdict(ERROR)
  -> END
```

Schemathesis process/parser ERROR：

```text
run_regression
  -> build_final_verdict(ERROR)
  -> END
```

有效网络 ERROR report 会先转换为 `RegressionResult(outcome=error)`，再经过 `parse_test_results` 得到 regression ERROR。

## 9. Integration Test 覆盖

| Case | 覆盖方式 | 结果 |
| --- | --- | --- |
| compatible/warning contract + conforming API | 离线完整 Graph | final PASS |
| breaking contract + API 符合 Head | 真实 Schemathesis + 本地 conforming API | contract FAIL / regression PASS / final FAIL |
| breaking contract + response 不符合 Head | 真实 Schemathesis + 主 fixture API | FAIL / FAIL / FAIL |
| oasdiff ERROR | 离线 Graph | final ERROR，不调用 Schemathesis |
| target 不可达 | 真实 Schemathesis | exit 1 / regression ERROR |
| NDJSON 损坏 | 离线 Adapter + Graph | final ERROR |
| Schemathesis CLI missing | failing runner + Graph | final ERROR |
| private targets disabled | Phase 1 security/Graph 测试 | prepare ERROR |
| private enabled + exact allowlist | 真实 loopback integration | 可调用 fixture API |
| 主 Demo 完整真实 CLI | oasdiff 1.28.0 + Schemathesis 4.24.3 | FAIL / FAIL / FAIL |

本地 integration 不访问公网。测试中的 oasdiff 部分使用 Phase 0 raw fixture 以隔离 Schemathesis；最终 CLI 验证则同时使用真实两个工具。

## 10. 主 Demo 实际结果

真实 CLI：

```text
python -m app.services.guardian \
  --base tests/guardian/fixtures/baseline_openapi.yaml \
  --head tests/guardian/fixtures/head_breaking_openapi.yaml \
  --url http://127.0.0.1:<fixture-port>
```

实际摘要：

```json
{
  "contract_verdict": "fail",
  "regression_verdict": "fail",
  "final_verdict": "fail",
  "selected_operations": [
    {
      "method": "GET",
      "path": "/items/{item_id}",
      "operation_id": "getItem"
    }
  ],
  "regression_summary": {
    "tool_name": "schemathesis",
    "tool_version": "4.24.3",
    "exit_code": 1,
    "total_operations": 1,
    "passed_operations": 0,
    "failed_operations": 1,
    "error_operations": 0
  }
}
```

失败 evidence 为 `response_schema_conformance / JsonSchemaError / HTTP 200 / Coverage`，没有响应 body、headers 或 reproduction command。CLI stdout 只有该脱敏 JSON，exit code 为 `1`。

本次 Windows oasdiff 真实运行使用 Phase 0 已验证官方包，SHA256 为 `cef8ec7cdd32ab4ce9c188f9dbb50452a91fa36cc11c8e1dcf340b19282c3ce1`，版本输出为 `oasdiff version 1.28.0`。

## 11. Evaluation Harness

执行：

```text
uv run python -m evals.guardian.harness
```

结果：

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

Phase 1 三个指标保留。Scope Precision 仍为 0.6，是 Recall 优先全量回退的预期代价。

## 12. 质量门与 Baseline

### 12.1 Guardian 局部

| 验证项 | 命令 | Exit code | 结果 |
| --- | --- | ---: | --- |
| pytest | `uv run pytest tests/guardian -q` | 0 | 58 passed，2 个第三方 deprecation warnings |
| Ruff | `uv run ruff check app/services/guardian evals/guardian tests/guardian` | 0 | All checks passed |
| Pyright | `uv run pyright app/services/guardian evals/guardian` | 0 | 0 errors / 0 warnings |
| Evaluation | `uv run python -m evals.guardian.harness` | 0 | 所有 accuracy 与 recall 为 1.0 |

两个 warning 来自 Uvicorn 间接依赖的 `websockets.legacy` / `WebSocketServerProtocol` deprecation，不是 Guardian failure。

### 12.2 全仓 Phase 0/1 baseline 对比

| 验证项 | Baseline | Phase 2 | 新增 |
| --- | --- | --- | ---: |
| Ruff lint | 8 个上游问题 | 相同 8 个 | 0 |
| Ruff format | 12 个上游文件 | 相同 12 个 | 0 |
| Pyright | 6 个上游错误 | 相同 6 个 | 0 |
| 非 slow pytest | 2 个 collection errors | 相同 2 个 | 0 |

Phase 2 没有修复缺失 `app.core.langgraph.tools.calculator` 或既有 MCP 类型问题。

## 13. 已知限制与 Phase 3 前注意事项

1. Phase 2 MVP 不支持用户提供 Authorization、Cookie 或 API Key，因此不能测试需要认证的目标 API。
2. 每个 operation 顺序启动一个 Schemathesis 进程，正确性优先；尚未做并发、批量 filter 或性能优化。
3. 固定只运行 examples/coverage、`response_schema_conformance` 和 `max-examples=1`；不包含 fuzzing 或 stateful testing。
4. V1 仍只支持 self-contained OpenAPI，external `$ref` 默认禁用。
5. SSRF 校验发生在执行前，但 hostname 在 Schemathesis 请求时会再次解析，尚未实现 DNS pinning；在 Phase 3 暴露远程 API route 前应评估 DNS rebinding 风险。
6. 安全 failure message 故意较粗粒度，不保存响应值或 body；Phase 4 diagnosis 也只能基于这些安全 DTO。
7. Phase 3 持久化必须只保存现有白名单 DTO、工具版本、Spec SHA256 和完整 policy snapshot，并增加 PostgreSQL checkpoint 中断恢复测试。
8. Phase 3 route 必须继续执行 JWT、rate limit、SSRF/allowlist 和 async database 规则，不能允许调用方注入 Schemathesis 参数。
9. 最终验证时 Docker Desktop Linux Engine pipe 暂时不可用；因此完整真实 CLI 改用 SHA256 校验后的官方 Windows oasdiff 1.28.0 与本机 Schemathesis 4.24.3 完成。该环境问题不影响两个工具的实际 Vertical Slice 结果。

本文完成后立即停止，没有实现 Phase 3 或 Phase 4 内容。
