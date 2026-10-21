# API Guardian

> 一个基于 LangGraph 的 API 变更门禁：组合确定性 Contract Diff、change-aware runtime regression 与结构化 LLM diagnosis。

API Guardian 回答两个工程问题：这次 OpenAPI 修改会不会破坏已有调用方，以及真实 API 是否已经符合新契约。它接收 Base OpenAPI、Head OpenAPI 和目标服务 URL，输出可持久化、可审计的 PASS / FAIL / ERROR 报告。

项目由生产化 LangGraph/FastAPI 模板演进而来；仓库中的 chatbot 保持独立，Guardian 是新增且有单独状态、图、持久化和 checkpoint namespace 的 vertical slice。

## 模板来源与项目贡献范围

项目在 [fastapi-langgraph-agent-zh](https://github.com/jarry126/fastapi-langgraph-agent-zh) 基础工程上扩展，保留原 MIT 许可证及原作者版权声明。模板提供通用 FastAPI、认证、LLM 接入和观测等基础设施；本项目围绕 Guardian 新增接口变更分析流程、测试范围选择、删除接口策略、工具结果归一化与确定性判定、解释降级、专用 API/数据模型/持久化及回归评测。通用基础设施的现有能力不作为从零独立研发成果。

oasdiff 与 Schemathesis 分别承担契约差异检测和运行时测试；本项目实现工具适配、流程集成、范围规则与结果处理。当前安装元数据仍沿用模板包名，以保持现有锁文件一致。

该发布副本保留评测脚本、冻结输入和报告，排除本地工具二进制、完整工作树、运行日志和真实凭据；模板 Docker Hub 自动部署工作流未纳入本次发布。示例配置中的口令仅供本地演示，部署时需自行替换。

## Problem

假设旧版本声明：

```yaml
GET /items/{item_id}
response.id: string
```

新版本把它改成：

```yaml
GET /items/{item_id}
response.id: integer
```

但部署中的真实接口仍返回 `{"id": "123"}`。单看代码评审，团队可能漏掉两件事：旧调用方会不会因类型变化而破坏，以及实现是否真的跟上新 Spec。

API Guardian 会得到：

- Contract：`FAIL`，因为 `string -> integer` 是 breaking change。
- Scope：只选择受影响的 `GET /items/{item_id}`。
- Regression：`FAIL`，因为真实响应不符合 Head schema。
- Final：`FAIL`。
- Analysis：LLM 可以解释兼容性风险和可能原因，但不能修改上述结论。

## Architecture

```text
Base OpenAPI ─┐
              ├─> prepare_scan
Head OpenAPI ─┘        │
                       v
                 oasdiff 1.28.0
                       │
                       v
                ContractChange
                       │
                       v
             recall-first Scope Selector
                       │ selected operations
                       v
              Schemathesis 4.24.3 ──> normalized RegressionResult
                       │
                       v
         deterministic PASS / FAIL / ERROR
                       │
                       v
      RiskExplanation + FailureDiagnosis (optional LLM)
                       │
                       v
                 GuardianReport
```

完整 LangGraph node flow：

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
  -> analyze_risks
  -> [diagnose_failures when failures exist]
  -> build_enhanced_report
  -> END
```

错误分支会提前进入 deterministic verdict 构建，工具执行问题不会被误报为 API regression FAIL。

## Why not pure LLM

LLM 不适合担当可重复的门禁裁判。API Guardian 明确分工：

- oasdiff 决定已检测到的 contract facts。
- deterministic selector 决定 runtime scope。
- Schemathesis 与 NDJSON parser 决定 runtime PASS / FAIL / ERROR。
- deterministic code 按 `ERROR > FAIL > PASS` 决定 final verdict。
- LLM 只把白名单证据转换成 RiskExplanation 和 FailureDiagnosis。

LLM 超时、429、provider error 或结构化输出失败时，扫描仍保持原 verdict，只设置 `analysis_degraded=true`。API key、raw prompt、raw model response、headers、body 和本地路径不会进入 GuardianState 或 GuardianReport。

## Key engineering decisions

### PASS / FAIL / ERROR 分离

- `PASS`：没有发现契约或运行时违规。
- `FAIL`：确定性证据证明存在 contract breaking 或 runtime schema failure。
- `ERROR`：工具缺失、目标不可达、超时、artifact 损坏等使检测无法可靠完成。

工具基础设施故障不能冒充产品 Bug，因此 exit code 不能直接等价为 FAIL。

### Recall-first change-aware scope

Scope Selector 优先避免漏测。无法安全定位的 breaking change 会 fallback-to-all。对结构化证明的 removed operation，P1 policy 保留 Contract FAIL，但 removal 本身贡献空 executable scope，不再单独触发全量 fallback；mixed/unresolved changes 仍逐 change 处理。

### Durable service boundary

```text
FastAPI Route -> GuardianService -> LangGraph -> GuardianRepository
```

- Route：JWT、上传限制、HTTP 错误映射和 rate limit。
- Service：workspace、scan lifecycle、graph invocation、安全 Report 构造。
- Repository：短事务保存 `GuardianScan` 与 `GuardianReportRecord`。
- AsyncPostgresSaver：以 `scan_id` 为 thread ID，在独立 `guardian` namespace 保存图执行进度。

业务历史报告与 checkpoint 的目的不同：前者供用户查询，后者供中断恢复。

### Security boundary

- 单个 OpenAPI 最大 5 MiB，且 external `$ref` 默认拒绝。
- target URL 必须精确命中 allowlist；production 默认拒绝 private/loopback。
- URL 禁止 userinfo、query 和 fragment；redirect 固定为 0。
- 子进程不使用 `shell=True`，用户不能注入任意 CLI 参数。
- raw oasdiff、raw NDJSON、request/response body、headers、Authorization、Cookie 和 API key 不进入 State、DB 或公开报告。

## Evaluation

下表只保留最有代表性的冻结结果。完整上下文见 [Final Evaluation Summary](benchmarks/guardian/reports/FINAL_EVALUATION_SUMMARY.md)。

| Evidence | Result | Boundary |
|---|---:|---|
| E2 schema-detectable mutation recall | 40/40 = 100% | 5 个本地 SUT，schema-oriented oracle |
| E2 overall mutation recall | 40/50 = 80% | 含 10 个无法由当前 oracle 检测的业务语义 controls |
| E3-B real-world contract accuracy | 14/19 = 73.68% | 4 个公开仓库、19 个冻结 Git-history pairs |
| P1-I E3-B executable scope precision | 3.43% -> 92.06% | Recall 保持 58/73；removed-operation policy |
| P1-I E3-B extra selected operations | 1632 -> 5 | Contract accuracy 仍为 14/19 |
| E3-C real historical scope | 39 -> 1；38 -> 1 ops | 仅 Supabase Auth，一个仓库、两个 pairs |
| E3-C request reduction | 95.39%；93.49% | 一个 runtime PASS，一个稳定 Schemathesis ERROR |
| E4 structured output / combined category | 50/50；44/50 = 88% | Risk 25/25，Diagnosis 19/25 = 76% |
| E4 LLM failure isolation | 4/4 | timeout、invalid output、provider failure、429 fixture |

### Failure -> diagnosis -> fix

E3-B 的 real-world executable scope precision 一度只有 `58/1690 = 3.43%`。项目没有直接调整规则，而是按以下顺序处理：

1. E3.5 逐 case 追踪 raw change、normalization、Head lookup 与 fallback。
2. 证明主要成本来自 removed operation 在 Head 中不可 resolve，3 个 fallback 造成 1632 个 extra operations。
3. P1 在独立 frozen 23-case dataset 上验证“可证明 removal 不单独 fallback”，recall 保持 31/31。
4. P1-I 才最小化集成生产规则并重放 E2/E3 数据。
5. E3-B precision 提升到 `58/63 = 92.06%`，recall `58/73` 与 contract accuracy `14/19` 均不变。

这是项目最重要的工程闭环：负结果先被保留和定位，再通过独立 ablation 证明修改不会牺牲 recall，最后才进入生产代码。

### Performance result is conditional

Scope reduction 不自动等于 wall-clock reduction。E2 的优化 Full baseline 下，Guardian paired median runtime reduction 是 `-0.82%`。E2.5 scaling 中，exactly-one affected operation 从已测 16-operation 点开始出现持续 faster region，并保持到 128；128 operations 时 reduction 为 34.86%。约 5% affected scenario 截至 128 operations 未观察到持续 break-even。当前主要瓶颈是 per-operation Schemathesis subprocess startup。

详细可用表述见 [Supported Claims](benchmarks/guardian/reports/SUPPORTED_CLAIMS.md)，禁止表述见 [Unsupported Claims](benchmarks/guardian/reports/UNSUPPORTED_CLAIMS.md)。

## Limitations

- external `$ref` 默认不支持，OpenAPI 单文件限制为 5 MiB。
- oasdiff 固定为 1.28.0；真实 Supabase specs 暴露了 loader compatibility/error cases。
- Runtime oracle 主要验证 schema conformance，不检测任意金额、排序、身份或状态机语义错误。
- V1 不允许用户注入任意 Authorization、Cookie 或 API key，不能覆盖任意认证 API。
- Guardian 为每个 selected operation 独立启动 Schemathesis，小 scope 的固定进程开销明显。
- durable workspace 是单实例 volume；完整 DNS pinning、分布式队列和对象存储不在当前范围。
- E3-C 仅对 Supabase Auth 一个仓库、两个历史 pairs 做了 runtime 验证，结果为 `PARTIALLY_VALIDATED`。
- E4 正式 Human Study 未执行，参与者为 0；不能声称 LLM 提高开发者效率、triage time 或真人准确率。

## Tech stack

- FastAPI、Pydantic v2、SQLModel
- LangGraph `StateGraph`、AsyncPostgresSaver
- PostgreSQL、Alembic
- oasdiff 1.28.0
- Schemathesis 4.24.3
- 现有 OpenAI-compatible LLMService（DeepSeek E2E 配置经过验证）
- Langfuse、Prometheus、structlog、slowapi
- pytest、Ruff、Pyright

## Run locally

### 1. Install

```bash
uv sync
```

另外需要让固定契约的 `oasdiff` 1.28.0 binary 可通过 `GUARDIAN_OASDIFF_PATH` 找到。Schemathesis 4.24.3 已锁定在项目依赖中。

### 2. Configure

复制 `.env.example` 为当前环境使用的 `.env.development`，至少检查：

```dotenv
POSTGRES_HOST=localhost
POSTGRES_DB=mydb
POSTGRES_USER=myuser
POSTGRES_PASSWORD=mypassword

GUARDIAN_OASDIFF_PATH=oasdiff
GUARDIAN_SCHEMATHESIS_PATH=st
GUARDIAN_ALLOWED_TARGET_HOSTS="127.0.0.1"
GUARDIAN_ALLOW_PRIVATE_TARGETS=true
GUARDIAN_LLM_ENABLED=false
```

`GUARDIAN_ALLOW_PRIVATE_TARGETS=true` 只用于 development/test。生产环境应保持 `false`。

如启用当前 OpenAI-compatible LLM 层，使用仓库已有的 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `DEFAULT_LLM_MODEL` 配置；不要把 key 写进代码或报告。

### 3. Database and API

```bash
docker compose --env-file .env.development up -d db
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

交互文档位于 `http://127.0.0.1:8000/docs`。

### 4. Deterministic CLI

CLI 不需要 FastAPI 或 Guardian 业务数据库：

```bash
uv run python -m uvicorn tests.guardian.fixture_api:app --host 127.0.0.1 --port 8765
```

另开终端：

```bash
uv run python -m app.services.guardian \
  --base tests/guardian/fixtures/baseline_openapi.yaml \
  --head tests/guardian/fixtures/head_breaking_openapi.yaml \
  --url http://127.0.0.1:8765
```

主 fixture 预期为 Contract FAIL、Regression FAIL、Final FAIL。命令退出码为 PASS=0、FAIL=1、ERROR=2。

## API endpoints

所有 Guardian endpoints 均要求现有 JWT：

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/guardian/scans` | multipart 上传 Base/Head、同步执行扫描 |
| GET | `/api/v1/guardian/scans/{scan_id}` | 查询当前用户拥有的 Scan |
| GET | `/api/v1/guardian/scans/{scan_id}/report` | 查询当前用户拥有的脱敏 GuardianReport |

POST 字段为 `base_spec`、`head_spec` 与 `target_base_url`。检测结果 ERROR 仍是一个正常完成的业务结果；只有 Guardian 服务内部未处理异常才映射为 5xx。

## Repository structure

```text
app/api/v1/guardian.py             HTTP/JWT boundary
app/services/guardian/
  graph.py                         LangGraph topology
  nodes.py                         state -> state delta nodes
  oasdiff.py                       fixed oasdiff adapter
  scope.py                         recall-first scope + P1 removal policy
  schemathesis.py                  fixed Schemathesis adapter / NDJSON parser
  risk.py                          structured RiskExplanation
  diagnosis.py                     structured FailureDiagnosis
  service.py                       scan lifecycle / report boundary
  repository.py                    Guardian database operations
app/models/guardian.py             GuardianScan / GuardianReportRecord
app/schemas/guardian.py            HTTP response DTOs
tests/guardian/                    unit and integration tests
evals/guardian/                    deterministic and LLM fixtures
benchmarks/guardian/               frozen E1-E4 evaluation artifacts
docs/                              design, interview and demo material
```

## Project status

```text
PRODUCT_CORE_COMPLETE
EVALUATION_COMPLETE_FOR_PERSONAL_PROJECT
HUMAN_UTILITY_NOT_EVALUATED
REAL_WORLD_RUNTIME_PARTIALLY_VALIDATED
FROZEN_FOR_RESUME_AND_INTERVIEW
```

核心功能与主要 Evaluation 已冻结。后续范围仅限 README、架构图、Demo、部署说明、简历和面试材料。

## 发布验证

本次测试结果、跳过项与已知限制见 [发布验证记录](docs/RELEASE_CHECKS.md)。

## 公开展示安全边界

本仓库适用于项目学习与评测展示，尚未完成公网服务部署加固。启动方式、凭据要求与已知风险见 [安全说明](SECURITY.md)。Compose 默认仅监听本机回环地址。
