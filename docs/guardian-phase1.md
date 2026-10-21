# API Guardian Contract Vertical Slice（Phase 1）

本文记录 API Guardian Phase 1 的实际实现、确定性判定规则、安全边界、离线 fixture、Evaluation Harness 和质量基线。后续阶段必须继续以这些领域 DTO 和行为契约为边界，不能让业务层重新依赖 oasdiff 原始结构。

验证日期：2026-08-31（Asia/Shanghai）

## 1. 阶段结论

Phase 1 已形成可独立运行的 contract-only Vertical Slice：

```text
Base Spec + Head Spec + target_base_url
  -> 输入与安全校验
  -> oasdiff breaking
  -> ContractChange 归一化
  -> affected operation 选择
  -> deterministic PASS / FAIL / ERROR
```

本阶段结论：

- 独立 `GuardianState` 和 `guardian_graph` 已实现，没有修改原 chatbot graph 的行为；
- oasdiff 通过受控异步 subprocess Adapter 调用，参数数组执行，不使用 `shell=True`；
- oasdiff `level` 的 integer 语义只在一个版本化 normalizer 中维护；
- Contract Verdict 完全由工具结果和 policy 决定，不依赖 LLM；
- Scope Selection 以 Recall 为优先，无法精确归属时回退到全部 operation；
- raw stdout/stderr、oasdiff raw source path、Authorization 和其他 Secret 不进入状态；
- Guardian 局部 Ruff、Pyright 和 pytest 全部通过；
- 全仓检查与 Phase 0 baseline 的失败数量和签名相同，没有新增 Guardian 回归。

本阶段没有实现 Schemathesis、数据库、FastAPI route、checkpointer、LLM、Langfuse 或 Prometheus 集成。Phase 1 完成后已停止，没有自动进入 Phase 2。

## 2. 实现文件

### 2.1 Guardian 业务域

| 文件 | 职责 |
| --- | --- |
| `app/services/guardian/__init__.py` | Guardian 业务域入口 |
| `app/services/guardian/errors.py` | 稳定、可持久化的安全错误码与异常 |
| `app/services/guardian/models.py` | Pydantic 领域 DTO、verdict、policy snapshot |
| `app/services/guardian/state.py` | TypedDict `GuardianState` |
| `app/services/guardian/security.py` | OpenAPI 校验、SHA256、operation 提取、external `$ref` 和 SSRF 防护 |
| `app/services/guardian/subprocess.py` | 受控异步 subprocess runner 与 artifact 限制 |
| `app/services/guardian/oasdiff.py` | oasdiff 1.28.0 Adapter、边界解析与 severity normalizer |
| `app/services/guardian/scope.py` | Recall 优先的 affected operation 选择和 deterministic verdict |
| `app/services/guardian/nodes.py` | 可独立注入和单元测试的 Guardian nodes |
| `app/services/guardian/graph.py` | 独立 contract-only LangGraph |

没有为了“未来可能复用”增加 `adapters/base.py` 等抽象；当前只通过 `Protocol` 定义 node 实际需要的最小 oasdiff 接口。

### 2.2 配置、文档与评测

| 文件 | 变化 |
| --- | --- |
| `.env.example` | 增加 Guardian allowlist、development-only 私网开关、大小与超时配置 |
| `app/core/config.py` | 复用现有 Settings 读取 Guardian 配置 |
| `README.md` | 说明 API Guardian 定位、V1 self-contained Spec 边界和默认安全限制 |
| `evals/guardian/__init__.py` | Evaluation Harness 包入口 |
| `evals/guardian/harness.py` | 输出 Contract Verdict Accuracy、Scope Recall、Scope Precision |

Phase 1 新增的直接解析依赖为 `PyYAML>=6.0.2`。LangGraph、Pydantic 和现有配置基础设施均直接复用项目已有依赖。`schemathesis==4.24.3` 已在 Phase 0 锁定，但 Phase 1 没有调用或实现 Schemathesis。

### 2.3 测试与 fixture

Phase 1 测试位于 `tests/guardian/`：

- `test_graph.py`：完整 contract-only Graph 的 PASS / FAIL / ERROR 和错误短路；
- `test_nodes.py`：所有 node 可独立调用；
- `test_oasdiff.py`：Adapter、CLI 参数、severity 映射、原始路径与 Secret 丢弃；
- `test_scope.py`：精确 scope、operationId 和保守全量回退；
- `test_security.py`：OpenAPI、external `$ref`、大小限制、allowlist、私网与状态字段；
- `test_subprocess.py`：正常执行、单 artifact、Scan 总预算和 timeout；
- `test_evaluation.py`：三个 Evaluation 指标的固定回归断言；
- `fakes.py`：离线可控的 subprocess fake。

继续复用 Phase 0 的 `breaking` 命名 fixture，测试不依赖真实公网：

- `baseline_openapi.yaml`
- `head_breaking_openapi.yaml`
- `oasdiff_breaking.json`
- `oasdiff_breaking_severity_levels.json`
- `oasdiff_no_changes.json`
- `external_ref_openapi.yaml`
- `external_schema.yaml`
- `invalid_openapi.yaml`

## 3. 领域模型与 GuardianState

### 3.1 Pydantic 领域对象

| 对象 | 用途 |
| --- | --- |
| `OperationRef` | 稳定引用 `method + path + operation_id`，method 自动转为大写 |
| `ContractChangeCandidate` | Adapter 到 normalizer 的安全边界 DTO，暂存 integer `severity_level` |
| `ContractChange` | 业务层稳定变更对象，不包含 oasdiff source 对象或原始路径 |
| `ToolRunSummary` | 只保存工具名、版本、exit code 和 artifact 字节数 |
| `ContractDiffResult` | Adapter 返回的 candidates 与脱敏工具摘要 |
| `ScopeSelection` | selected operations、是否全量回退、未归属 change IDs |
| `GuardianError` | 稳定错误码、发生 node 和安全消息 |
| `GuardianPolicy` | 单次 Scan 使用的非 Secret policy snapshot |

枚举：

- `ChangeSeverity`：`info`、`warning`、`error`；
- `GuardianVerdict`：`pass`、`fail`、`error`。

Phase 2 的 `RegressionResult`，以及 Phase 4 的 `RiskExplanation`、`FailureDiagnosis`、`GuardianReport` 没有提前实现。

### 3.2 TypedDict State

`GuardianState` 当前字段为：

```text
scan_id
base_spec_path
head_spec_path
target_base_url
policy
base_spec_sha256
head_spec_sha256
head_operations
contract_candidates
contract_changes
selected_scope
contract_tool
contract_verdict
artifact_bytes_consumed
errors
completed_nodes
```

State 只承载输入引用、领域 DTO 和脱敏摘要。以下内容没有状态字段，也不得通过额外键写入：

- raw stdout / stderr；
- oasdiff `baseSource.file` / `revisionSource.file`；
- Authorization、Cookie 或其他 headers；
- CLI argv 中可能存在的 Secret；
- 原始工具 JSON。

输入 URL 如果通过 userinfo 携带凭据会被拒绝；失败路径还会主动把 `target_base_url` 清空，避免 Secret 随错误状态进入未来 checkpoint。

## 4. Guardian Graph 实际 Node Flow

正常流程：

```text
START
  -> prepare_scan
  -> contract_diff
  -> normalize_changes
  -> select_test_scope
  -> build_contract_verdict
  -> END
```

错误短路：

```text
prepare_scan / contract_diff / normalize_changes
  -> 检测到 errors
  -> build_contract_verdict(ERROR)
  -> END
```

| Node | 确定性职责 | 主要安全输出 |
| --- | --- | --- |
| `prepare_scan` | 校验输入、target、两份 self-contained Spec，计算 SHA256，提取 Head operations | policy snapshot、Spec SHA256、operations、累计字节数 |
| `contract_diff` | 通过 Adapter 执行 oasdiff | candidates、工具摘要、累计字节数 |
| `normalize_changes` | 集中解释 integer level，构造 `ContractChange` | 稳定 changes |
| `select_test_scope` | 精确选择或 Recall 优先回退 | `ScopeSelection` |
| `build_contract_verdict` | 按 errors、severity 和 policy 得出结论 | PASS / FAIL / ERROR |

`GuardianNodes` 通过构造器注入 `GuardianPolicy` 和 `OasdiffPort`，因此每个 node 可以独立单元测试。Phase 1 graph 编译时不带 checkpointer；PostgreSQL checkpoint 与中断恢复测试属于 Phase 3。

## 5. OpenAPI 与 target 安全边界

### 5.1 OpenAPI V1 边界

V1 只接受单个 self-contained OpenAPI JSON/YAML：

- 允许同一文档内部的 `#...` 引用；
- 默认拒绝文件路径、HTTP URL 等 external `$ref`；
- 解析前先通过文件元数据检查大小；
- 文档必须是对象，且 `paths` 必须是对象；
- 计算 Base / Head Spec SHA256；
- 只从 Head Spec 提取标准 HTTP operation。

默认大小策略：

| 限制 | 默认值 | Phase 1 执行点 |
| --- | ---: | --- |
| 单个 OpenAPI | 5 MiB | 完整读取与 YAML/JSON 解析前 |
| 单个工具 artifact | 50 MiB | subprocess stdout/stderr 流式读取时 |
| 单次 Scan artifact 总量 | 100 MiB | 两份 Spec 加所有工具输出的累计预算 |

Base fixture SHA256：`99f0ef23b5b19ee5ffd7986751b04fa5037f4d2ccd829b69efe078d5aa7ddf72`

Head fixture SHA256：`e645ebf416c2645920429fe2b15da8db6ee8f7c28966452dc32f3835f1502227`

### 5.2 SSRF 防护

`target_base_url` 必须同时满足：

1. scheme 只能是 HTTP 或 HTTPS；
2. 禁止 URL userinfo、query 和 fragment；
3. hostname 规范化后必须精确命中 allowlist；
4. hostname/IP 必须实际解析；
5. 解析得到的所有地址都必须通过地址类别检查；
6. loopback、private、link-local、reserved、multicast 或 unspecified 地址默认拒绝。

`GUARDIAN_ALLOW_PRIVATE_TARGETS=true` 只放宽显式 allowlist 内的 private/loopback 地址，用于 localhost/local fixture API；默认值必须为 `false`。即使开启 development-only 配置，也不会绕过 host allowlist。

## 6. Controlled Subprocess 与 oasdiff Adapter

### 6.1 受控执行

`ControlledSubprocessRunner` 使用 `asyncio.create_subprocess_exec(*command)`：

- 不构造 shell command string；
- 不设置 `shell=True`；
- stdout/stderr 并发、分块读取；
- 读取过程中执行单 artifact 和 Scan 剩余预算限制；
- timeout 或超限时终止子进程；
- 启动失败、timeout 和超限异常不包含 argv 或原始输出。

原始 `ProcessResult` 只在 Adapter 内短暂存在；Adapter 立即解析为白名单 DTO，Graph state 只接收 `ContractDiffResult` 中的领域数据。

### 6.2 真实 CLI

Phase 1 Adapter 使用 Phase 0 已锁定的 oasdiff `1.28.0`：

```text
oasdiff breaking \
  --format json \
  --allow-external-refs=false \
  --fail-on ERR \
  -- BASE_SPEC HEAD_SPEC
```

参数以 tuple/argv 形式传入。exit code 处理规则：

| Exit code | Adapter 解释 |
| ---: | --- |
| 0 | 命令成功；JSON 可能是空数组 |
| 1 | 命令成功且命中 `--fail-on ERR`；仍必须解析完整 JSON |
| 其他 | 工具 ERROR，返回 `oasdiff_execution_failed`，不回显 stderr |

JSON 解析失败返回 `oasdiff_invalid_output`。Adapter 只读取白名单字段并丢弃 `baseSource`、`revisionSource` 和未知字段。

## 7. Severity Normalization

oasdiff 1.28.0 的 `level` 实际为 integer。数字语义只存在于 `OASDIFF_LEVEL_TO_SEVERITY`：

| oasdiff level | 领域 severity |
| ---: | --- |
| 1 | `info` |
| 2 | `warning` |
| 3 | `error` |

未知 level 不做猜测，确定性返回 `unknown_oasdiff_severity` ERROR。

breaking fixture 的归一化过程：

```json
{
  "id": "response-property-type-changed",
  "level": 3,
  "operation": "GET",
  "operationId": "getItem",
  "path": "/items/{item_id}",
  "baseSource": {"file": "已丢弃"},
  "revisionSource": {"file": "已丢弃"}
}
```

转换为业务对象：

```json
{
  "change_id": "response-property-type-changed",
  "summary": "the `id` response's property `type` changed from `string` to `integer` for status `200`",
  "severity": "error",
  "method": "GET",
  "path": "/items/{item_id}",
  "operation_id": "getItem",
  "section": "paths",
  "fingerprint": "3fc82f02994a"
}
```

版本化 fixture 同时覆盖 1、2、3 三个整数；`99` 等未知整数有独立 ERROR 测试。

## 8. Scope Selection

选择顺序：

1. 优先使用 `method + path` 精确匹配 Head operations；
2. 缺少精确 path 时，只接受唯一的 `operationId` 匹配；
3. 任意 change 无法精确归属时，整个 scope 回退为 Head Spec 的全部 operations；
4. 无 change 时 scope 为空。

只要混合 changes 中存在一个 unresolved change，也必须全量回退。`ScopeSelection` 会同时记录 `fallback_to_all=true` 和 `unresolved_change_ids`，使后续报告可以解释 precision 降低的原因。

breaking fixture 可精确归属，选择结果为：

```text
GET /items/{item_id}
```

## 9. Deterministic Contract Verdict

默认 policy 只把 `error` severity 视为 contract failure：

| 状态 | Verdict |
| --- | --- |
| 任一 node 或工具发生 ERROR | `ERROR` |
| 至少一个 `error` ContractChange | `FAIL` |
| 无 error change，包括只有 warning/info | `PASS` |
| 无 change | `PASS` |

这是纯确定性函数。LLM 不判断 change 是否存在、不决定 PASS/FAIL，也不改变最终 CI verdict。

离线 Graph fixture 结果：

| Fixture | oasdiff exit code | Change | Scope | Verdict | 实际 nodes |
| --- | ---: | --- | --- | --- | --- |
| `oasdiff_no_changes.json` | 0 | 0 | 空 | PASS | 全部 5 个 nodes |
| `oasdiff_breaking.json` | 1 | 1 个 error | `GET /items/{item_id}` | FAIL | 全部 5 个 nodes |
| 模拟工具错误 | 102 | 不解析 stderr | 不执行 scope | ERROR | `prepare_scan -> contract_diff -> build_contract_verdict` |

SSRF 输入错误会在调用工具前短路：

```text
prepare_scan -> build_contract_verdict(ERROR)
```

### 9.1 容器内真实 oasdiff 验证

使用 Phase 0 构建的 `api-guardian-phase0:local` 镜像，把当前工作区只读挂载后执行真实 contract-only Graph：

```json
{
  "verdict": "fail",
  "change_count": 1,
  "scope": ["GET /items/{item_id}"],
  "tool": {
    "name": "oasdiff",
    "version": "1.28.0",
    "exit_code": 1,
    "artifact_bytes": 479
  },
  "artifact_bytes_consumed": 1612,
  "completed_nodes": [
    "prepare_scan",
    "contract_diff",
    "normalize_changes",
    "select_test_scope",
    "build_contract_verdict"
  ]
}
```

真实运行结果与离线 breaking fixture 一致。

## 10. Evaluation Harness

执行命令：

```text
uv run python -m evals.guardian.harness
```

2026-08-31 实测输出：

```json
{
  "total_cases": 5,
  "contract_verdict_accuracy": 1.0,
  "scope_recall": 1.0,
  "scope_precision": 0.6
}
```

五个离线样本覆盖：无变化、精确 error change、组件级未归属 change、默认不失败的 warning，以及工具 ERROR。

Scope Precision 为 0.6 是 Recall 优先策略的预期代价，不是测试失败：组件级 change 的 ground truth 只影响一个 operation，但实现无法安全证明其归属，因此保守选择全部三个 operation。micro average 下选中 5 个 operation，其中 3 个为 true positive；Recall 保持 1.0。

## 11. 安全测试结果

安全相关测试覆盖：

- external `$ref` 拒绝；
- OpenAPI 在解析前执行 5 MiB 限制；
- Spec SHA256 和 Head operations 提取；
- target host allowlist；
- 私网/回环地址默认拒绝；
- development-only 私网放行仍要求 allowlist；
- URL userinfo Secret 被拒绝且不回显；
- `GuardianState` 不提供 raw、source 或 header/authorization 字段；
- 单 artifact 50 MiB 和 Scan 总计 100 MiB 的流式预算；
- subprocess timeout 终止；
- oasdiff stderr、无效 stdout 和 raw source path 不进入异常或 state；
- SSRF 错误在工具执行前短路；
- Secret-bearing target 在错误状态中被清空。

全部测试使用本地 fixture 或 fake runner，不访问真实公网。

## 12. Ruff、Pyright 与 pytest

### 12.1 Guardian 局部质量门

| 验证项 | 命令 | Exit code | 2026-08-31 结果 |
| --- | --- | ---: | --- |
| Guardian pytest | `uv run pytest tests/guardian -q` | 0 | `35 passed in 3.93s` |
| Guardian Ruff | `uv run ruff check app/services/guardian evals/guardian tests/guardian` | 0 | `All checks passed!` |
| Guardian Pyright | `uv run pyright app/services/guardian evals/guardian` | 0 | `0 errors, 0 warnings, 0 informations` |
| Evaluation Harness | `uv run python -m evals.guardian.harness` | 0 | Accuracy 1.0 / Recall 1.0 / Precision 0.6 |

### 12.2 与 Phase 0 全仓 baseline 对比

| 验证项 | Phase 0 | Phase 1 复测 | 差异 |
| --- | --- | --- | --- |
| `uv run ruff check .` | 8 个上游问题 | 8 个相同问题 | 0 个新增问题 |
| `uv run ruff format --check .` | 12 个上游文件 | 相同 12 个文件 | 0 个新增文件 |
| `uv run pyright` | 6 个上游错误 | 相同 6 个错误 | 0 个新增错误 |
| `uv run pytest -m "not slow" -q` | 2 个 collection error | 相同 2 个 collection error | 0 个新增失败签名 |

全仓 pytest 仍因缺少 `app.core.langgraph.tools.calculator` 而无法收集 `tests/test_mcp_client.py` 和 `tests/test_mcp_with_llm.py`。Pyright 的 6 个错误仍来自同一缺失 import 和既有 MCP 类型问题。Phase 1 按约束没有修复这些与 Guardian 无关的上游问题。

## 13. 已知兼容性与运行说明

1. 当前 Windows PowerShell 会话的 `PATH` 中没有 `oasdiff`，直接执行会得到 command not found。Phase 0 最终 Docker 镜像已经安装并校验 oasdiff 1.28.0；生产/CI 应使用该镜像，或通过 `GUARDIAN_OASDIFF_PATH` 指向经过校验的可执行文件。
2. 直接 import `app.services.guardian` 会经过现有 `app.services` 包初始化，从而打印现有环境、数据库和默认模型相关启动日志。为遵守“不重构上游基础设施”，Phase 1 没有改变该行为；局部测试和 verdict 不受影响。
3. Evaluation Harness 的最终指标是 JSON，但在当前 Windows 终端中，其前置的现有中文启动日志可能受控制台编码影响。指标 JSON 本身可正常解析。
4. `guardian_graph` 当前不带数据库 checkpointer，也未挂入 FastAPI。独立运行时仍需满足现有项目 Settings 的必填环境变量约束。

## 14. 后续阶段边界

Phase 2 才会实现：

```text
selected_scope
  -> Schemathesis Adapter
  -> NDJSON parser
  -> run_regression
  -> parse_test_results
  -> deterministic PASS / FAIL / ERROR
  -> local fixture API integration test
```

Phase 2 必须继续保持：FAIL 与工具 ERROR 严格区分；raw NDJSON、request/response Secret 和 Authorization 不进入 state；业务层只依赖 `RegressionResult` 等白名单领域 DTO。fixture API 必须同时保留 Contract Breaking Change 和 Runtime Regression。

Phase 3 才增加 SQLModel、Alembic、Repository、FastAPI routes、PostgreSQL Checkpointer 和 checkpoint 中断恢复测试。Phase 4 才增加 LLM RiskExplanation、FailureDiagnosis、Langfuse、Prometheus 与最终报告。最终报告必须保存实际工具版本、Base/Head Spec SHA256 和当次 policy snapshot。

