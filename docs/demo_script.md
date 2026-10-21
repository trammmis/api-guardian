# API Guardian 5-Minute Demo Script

## Demo 原则

使用仓库内稳定 fixture，不依赖公网 GitHub、真实 Supabase runtime 或现场 DeepSeek。默认设置 `GUARDIAN_LLM_ENABLED=false` 展示完整 deterministic path；LLM 页面使用已经冻结的安全示例，并明确标为预生成结果。

主 Demo：

- Base：`GET /items/{item_id}` 的 `response.id` 为 string。
- Head：同一字段改为 integer。
- Fixture API：仍返回 string。
- 预期：Contract FAIL、Regression FAIL、Final FAIL。

## 演示前准备

1. 安装依赖并确认 `oasdiff 1.28.0`、`st 4.24.3` 可用。
2. development/test 配置精确 allowlist：

   ```dotenv
   GUARDIAN_ALLOWED_TARGET_HOSTS="127.0.0.1"
   GUARDIAN_ALLOW_PRIVATE_TARGETS=true
   GUARDIAN_LLM_ENABLED=false
   ```

3. 启动 PostgreSQL、执行 Alembic、启动 API：

   ```bash
   docker compose --env-file .env.development up -d db
   uv run alembic upgrade head
   uv run uvicorn app.main:app --port 8000
   ```

4. 另开终端启动 fixture：

   ```bash
   uv run python -m uvicorn tests.guardian.fixture_api:app --host 127.0.0.1 --port 8765
   ```

5. 提前准备一个测试 JWT；不要在录屏、终端历史或文档中展示真实 secret。

## 0:00–0:40 — 解释问题

展示两份小型 OpenAPI diff：

```text
Base: response.id = string
Head: response.id = integer
Runtime: {"id": "123"}
```

讲解词：

> “Spec 变化是否 breaking，和实现是否跟上新 Spec，是两个不同问题。API Guardian 会把 contract fact、runtime evidence 和解释层分开。”

## 0:40–1:30 — 创建 Scan

在 FastAPI `/docs` 打开 `POST /api/v1/guardian/scans`：

- `base_spec`：`tests/guardian/fixtures/baseline_openapi.yaml`
- `head_spec`：`tests/guardian/fixtures/head_breaking_openapi.yaml`
- `target_base_url`：`http://127.0.0.1:8765`

带现有 JWT 提交。说明上传采用受限分块读取，文件名不会直接作为磁盘名，target 已经过 allowlist/SSRF 检查。

预期摘要：

```json
{
  "status": "completed",
  "contract_verdict": "fail",
  "regression_verdict": "fail",
  "final_verdict": "fail",
  "analysis_degraded": false
}
```

## 1:30–2:20 — Contract breaking 与 scope

调用 `GET /api/v1/guardian/scans/{scan_id}/report`。

只展开：

- `contract.verdict`
- `contract.changes`
- `scope.operations`
- `scope.fallback_to_all`

讲解词：

> “oasdiff 负责发现事实，Guardian 把第三方输出标准化为 ContractChange。Selector 只选中 GET /items/{item_id}，不会误测其他 operation。Contract verdict 不由 LLM 决定。”

## 2:20–3:10 — Runtime regression

展开：

- `regression.verdict`
- `regression.results[].operation`
- `checks`
- `failures[].check_name`
- `failures[].safe_message`

讲解词：

> “Schemathesis 按 Head 期望 integer 请求真实 localhost API。Fixture 返回 string，response_schema_conformance 失败。Parser 只读取 NDJSON 白名单字段，不保存 request/response body、headers 或 reproduction command。”

## 3:10–3:40 — Final verdict

指向 `final_verdict=fail`：

> “Final verdict 是确定性的。规则是 ERROR 优先于 FAIL，FAIL 优先于 PASS。即使 runtime 恰好 PASS，只要 contract 是 FAIL，final 仍是 FAIL。”

补充一个口头 ERROR 场景：目标不可达会得到 `completed + final_verdict=error`，不会错误地变成 scan `failed`。

## 3:40–4:20 — LLM explanation

不要现场调用 DeepSeek。展示项目中已冻结、经过白名单/Pydantic 校验的安全结果示例：

```json
{
  "risk_explanations": [
    {
      "risk_level": "high",
      "impact": "Consumers relying on the previous string response may fail type validation or deserialization.",
      "affected_consumer": "Consumers relying on the previous contract",
      "recommendation": "Coordinate the type migration or retain a compatibility window."
    }
  ],
  "failure_diagnoses": [
    {
      "category": "contract_mismatch",
      "possible_cause": "The implementation may not yet match the new integer response contract.",
      "evidence": ["response_schema_conformance failed for GET /items/{item_id}"],
      "recommendation": "Compare the deployed response serializer with the Head schema."
    }
  ]
}
```

说明这只是表达形式示例，实际 frozen 输出保存在 E4 artifacts；不要暗示自然语言逐字固定。

讲解词：

> “LLM 只能看到白名单事实。它可以说‘实现可能没同步’，不能说某个数据库表或第几行代码有 Bug。DeepSeek 挂了只会 analysis_degraded，不会改变 final verdict。”

## 4:20–5:00 — 工程与评估闭环

快速展示：

- PostgreSQL 中有 Scan summary 与安全 Report，两张业务表即可。
- LangGraph checkpoint 用独立 namespace 保存执行进度。
- README 的 `Failure -> diagnosis -> fix`：E3 precision 3.43% -> E3.5 定位 removal fallback -> P1 ablation -> P1-I 92.06%，recall 不下降。

结束语：

> “这个项目不把第三方 diff/test 能力包装成自研算法。Guardian 的增量价值是 change-aware 编排、确定性错误语义、恢复/持久化、安全证据边界，以及在负 benchmark 结果之后完成可验证的产品改进。”

## Demo 故障预案

- oasdiff 不可用：不要改结果，展示上一次本地 fixture 的冻结报告并解释会分类为 ERROR。
- PostgreSQL 未启动：切换到 deterministic CLI，明确这不演示 persistence。
- fixture 端口被占用：换端口并同步更新精确 allowlist。
- LLM 未配置：保持 disabled；绝不现场粘贴 API key。
- JWT 过期：使用预先准备的测试用户重新登录，不展示 token 内容。
