# API Guardian 外部工具契约（Phase 0）

本文记录 API Guardian Phase 0 在真实环境中的基线与外部工具契约。后续 Adapter、解析器和测试必须以本文记录的实际行为为准，不能依赖未经验证的 CLI 假设。

验证日期：2026-08-31（Asia/Shanghai）

## 1. 结论与版本锁定

| 工具 | 锁定版本 | 锁定位置 | 验证结果 |
| --- | --- | --- | --- |
| oasdiff | `1.28.0` | `Dockerfile` 的 `OASDIFF_VERSION` 和发布包 SHA256 | Windows、Linux 容器均通过 |
| Schemathesis | `4.24.3` | `pyproject.toml` 与 `uv.lock` | 本机与最终 Docker 镜像均通过 |

验证时 oasdiff 上游最新 release 为 `v1.30.0`。本项目没有自动追随最新版本，而是按计划验证并锁定 `v1.28.0`。升级必须重新执行本文中的 fixture 契约测试。

oasdiff 官方发布包校验值：

- Linux amd64：`e0ef076f2cf953d922addc04be9c3851cf3ec18f7678d2b94d44cea23dca51b5`
- Windows amd64：`cef8ec7cdd32ab4ce9c188f9dbb50452a91fa36cc11c8e1dcf340b19282c3ce1`

最终镜像内的实测命令与结果：

```text
$ oasdiff --version
oasdiff version 1.28.0

$ /app/.venv/bin/st --version
st, version 4.24.3
```

组合命令 exit code 为 `0`。

## 2. 上游仓库基线

### 2.1 验证环境

- 上游提交：`bcc2c8c1303a1eaae9a99dce73c0fe72dc2fc46d`
- 提交说明：`去除.claude下面的worktrees的提交`
- Python：`3.13.12`
- uv：`0.10.7`
- Docker Desktop：`4.88.1`
- Docker Client / Engine：`29.7.2 / 29.7.2`
- Docker Compose：`5.4.0`
- PostgreSQL 测试镜像：`pgvector/pgvector:pg16`

### 2.2 基线执行结果

| 验证项 | 命令 | Exit code | 结果 |
| --- | --- | ---: | --- |
| 安装全部依赖 | `uv sync --all-extras --all-groups` | 0 | 成功 |
| PostgreSQL 连接 | 容器内 `pg_isready` | 0 | 成功 |
| Alembic 迁移 | `uv run alembic upgrade head` | 0 | 成功，版本为 `c7f2a08e4b1d` |
| JWT 创建与校验 | 调用现有 `create_access_token` / `verify_token` | 0 | subject 往返成功，未输出 token |
| Ruff lint | `uv run ruff check .` | 1 | 上游已有 8 个问题 |
| Ruff format | `uv run ruff format --check .` | 1 | 上游已有 12 个文件未格式化 |
| Pyright | `uv run pyright` | 1 | 上游已有 6 个错误 |
| 非 slow 测试 | `uv run pytest -m "not slow" -q` | 1 | collection 阶段失败 |
| FastAPI 启动 | `uv run uvicorn app.main:app ...` | 1 | import 阶段失败 |

数据库迁移后存在以下表：

- `alembic_version`
- `chat_message`
- `session`
- `thread`
- `user`

上游当前不能完整启动的直接原因是 `app/core/langgraph/tools/__init__.py` 导入了仓库中不存在的 `app.core.langgraph.tools.calculator`。测试收集和 Uvicorn 启动都在同一处失败，因此本阶段没有为“让基线变绿”而修改现有 chatbot graph，也无法完成 `/health` 与 JWT HTTP 路由的端到端验证。JWT 工具本身已独立验证成功。

其余静态基线问题包括：MCP content 联合类型访问、MCP connections 参数类型、未使用 import、docstring 与遗留注释规则。它们均属于上游现状，不在 API Guardian Phase 0 中修复。

## 3. OpenAPI fixture 契约

V1 只接受 self-contained OpenAPI JSON/YAML，默认禁止 external `$ref`。Phase 1 必须在进入外部工具前执行大小检查；初始默认策略为：

- 单个 OpenAPI 文档最大 `5 MiB`；
- 单个工具 artifact 最大 `50 MiB`；
- 超限必须作为工具执行前或 artifact 读取时的确定性 `ERROR`，不能截断后继续判定。

fixture：

- `tests/guardian/fixtures/baseline_openapi.yaml`
- `tests/guardian/fixtures/head_breaking_openapi.yaml`
- `tests/guardian/fixtures/invalid_openapi.yaml`
- `tests/guardian/fixtures/external_ref_openapi.yaml`
- `tests/guardian/fixtures/external_schema.yaml`
- `tests/guardian/fixture_api.py`

`head_breaking_openapi.yaml` 将 `GET /items/{item_id}` 的 `200` 响应字段 `id` 从 `string` 改为 `integer`，用于制造 Contract Breaking Change。fixture API 仍返回字符串 `id`，用于制造相对于 head spec 的 Runtime Regression。因此同一组 fixture 同时覆盖了用户要求的两类变化。

本次 fixture Spec SHA256：

- Base：`99f0ef23b5b19ee5ffd7986751b04fa5037f4d2ccd829b69efe078d5aa7ddf72`
- Head：`e645ebf416c2645920429fe2b15da8db6ee8f7c28966452dc32f3835f1502227`

最终 `GuardianReport` 必须保存实际工具版本、Base/Head Spec SHA256 和当次 policy snapshot，不能只保存当前全局配置的引用。

## 4. oasdiff 1.28.0 契约

### 4.1 真实 CLI

版本命令是：

```text
oasdiff --version
```

结果为 `oasdiff version 1.28.0`，exit code 为 `0`。`oasdiff version` 不是有效子命令，会返回 exit code `1`。

contract diff 的受控命令模板为：

```text
oasdiff breaking \
  --format json \
  --allow-external-refs=false \
  --fail-on ERR \
  -- BASE_SPEC HEAD_SPEC
```

Adapter 必须使用参数数组调用，不得使用 `shell=True`。`--` 用于明确结束选项，Spec 路径不得拼接进 shell 字符串。

实测矩阵：

| 场景 | 关键参数 | Exit code | stdout / stderr 契约 |
| --- | --- | ---: | --- |
| 无变化 | Base 对 Base，不指定 `--fail-on` | 0 | stdout 为 `[]` |
| 存在 breaking | Base 对 Head，不指定 `--fail-on` | 0 | stdout 为非空 JSON array |
| 存在 breaking，门禁模式 | Base 对 Head，`--fail-on ERR` | 1 | stdout 仍为完整 JSON array |
| 无效 OpenAPI | Base 对 invalid fixture | 102 | 工具错误，不能解释为 breaking |
| external `$ref` | `--allow-external-refs=false` | 102 | 包含 `encountered disallowed external reference` |
| 输出 JSON Schema | `oasdiff schema` | 0 | JSON Schema draft 2020-12 |

注意：oasdiff 的默认值是 `--allow-external-refs=true`，所以 V1 必须显式传入 `false`，不能依赖默认值。

### 4.2 实际 breaking JSON

fixture 的实际输出为一个数组，示例中省略了仅与运行目录相关的绝对路径：

```json
[
  {
    "id": "response-property-type-changed",
    "text": "the `id` response's property `type` changed from `string` to `integer` for status `200`",
    "level": 3,
    "operation": "GET",
    "operationId": "getItem",
    "path": "/items/{item_id}",
    "section": "paths",
    "baseSource": {
      "file": ".../baseline_openapi.yaml",
      "line": 26,
      "column": 21
    },
    "revisionSource": {
      "file": ".../head_breaking_openapi.yaml",
      "line": 26,
      "column": 21
    },
    "fingerprint": "3fc82f02994a"
  }
]
```

`oasdiff schema` 返回的根类型是 `Changes` array，元素为 `Change`。`Change` 的已声明字段为：

- `id: string`
- `text: string`
- `comment: string`
- `level: integer`
- `operation: string`
- `operationId: string`
- `path: string`
- `section: string`
- `attributes: object`
- `baseSource: Source`
- `revisionSource: Source`
- `fingerprint: string`

Schema 中只有 `level` 是 required；`additionalProperties` 为 `false`。`Source` 可包含 `file`、`line`、`column`、`endLine`、`endColumn`，也都不是 required。fixture 中 `level: 3` 会被 `--fail-on ERR` 判为失败。

业务层不能直接暴露或依赖这个对象。Phase 1 的 Adapter 负责把它转换成稳定的 `ContractChange`；原始 source 文件路径不得进入最终报告。

### 4.3 Docker 安装方式

本机 Docker registry mirror 拒绝拉取 `tufin/oasdiff:1.28.0`，返回“镜像不在白名单”，Docker exit code 为 `125`。因此项目不依赖该镜像，`Dockerfile` 改为下载官方 Linux amd64 release，校验上述 SHA256 后解包。

该安装层和完整应用镜像均已构建成功。Linux 容器内对 breaking fixture 执行相同命令，JSON fingerprint 相同，exit code 为 `1`。

## 5. Schemathesis 4.24.3 契约

### 5.1 真实 CLI

版本命令：

```text
st --version
```

结果为 `st, version 4.24.3`，exit code 为 `0`。

本次验证使用的命令模板：

```text
st run SPEC \
  --url TARGET_BASE_URL \
  --include-operation-id getItem \
  --checks response_schema_conformance \
  --phases examples,coverage \
  --max-examples 1 \
  --generation-deterministic \
  --max-redirects 0 \
  --request-timeout 2 \
  --report ndjson \
  --report-ndjson-path REPORT_PATH \
  --output-sanitize true \
  --no-color
```

Windows 受控子进程还必须使用进程级环境变量 `PYTHONUTF8=1`。这只作用于子进程，不能修改系统全局编码。

`--generation-deterministic` 已隐含禁用 Hypothesis database，不能再同时传 `--generation-database :memory:`；两者并用会在测试前返回 exit code `2`。

### 5.2 PASS / FAIL / ERROR 实测

| 场景 | Spec / Target | Exit code | NDJSON 判断依据 |
| --- | --- | ---: | --- |
| PASS | Base / fixture API | 0 | `ScenarioFinished.status=success`，Coverage phase 与 suite 均为 `success` |
| FAIL | Head / fixture API | 1 | `ScenarioFinished.status=failure`，check 为 `response_schema_conformance/failure` |
| ERROR | Base / 不可达本地端口 | 1 | 存在 `NonFatalError`，scenario、suite、Coverage phase 均为 `error` |
| CLI 配置错误 | deterministic 与 database 同时传入 | 2 | 未产生有效测试报告 |
| 无效 Spec | invalid fixture | 1 | 触发 Schemathesis 内部 `AssertionError`，NDJSON 仅有初始化事件 |

`FAIL` 与工具 `ERROR` 都可能返回 exit code `1`，所以 verdict 绝不能只看进程退出码。至少要结合 NDJSON 事件；非零退出且报告不完整也必须归类为 `ERROR`。

### 5.3 NDJSON 实际结构

每一行都是独立 JSON object，并且最外层只有一个事件名键，不存在统一的 `event_type` 字段。例如：

```json
{"Initialize":{"command":"...","schemathesis_version":"4.24.3","seed":null}}
{"SuiteFinished":{"id":"...","timestamp":0,"phase":"Coverage","status":"failure"}}
{"EngineFinished":{"id":"...","timestamp":0,"running_time":0,"stop_reason":"completed","payload":{}}}
```

完整 PASS/FAIL fixture 都产生 25 行，观察到的事件顺序为：

```text
Initialize
LoadingStarted
LoadingFinished
EngineStarted
PhaseStarted / PhaseFinished
SuiteStarted
ScenarioStarted
ScenarioFinished
SuiteFinished
...
EngineFinished
```

核心 payload：

- `Initialize`：`command`、`schemathesis_version`、`seed`。
- `LoadingFinished`：`id`、`timestamp`、`duration`、`specification`、`statistic`。
- `ScenarioFinished`：`id`、`timestamp`、`suite_id`、`phase`、`status`、`elapsed_time`、`is_final`、`recorder`。
- `SuiteFinished`：`id`、`timestamp`、`phase`、`status`。
- `PhaseFinished`：`id`、`timestamp`、`phase`、`status`，部分 phase 还有 `payload`。
- `NonFatalError`：`id`、`timestamp`、`value`、`phase`、`label`、`related_to_operation`。
- `EngineFinished`：`id`、`timestamp`、`running_time`、`stop_reason`、`payload`。

FAIL 的 `ScenarioFinished.recorder` 中包含：

- `label`，例如 `GET /items/{item_id}`；
- `cases`，以动态 case ID 为键；
- `checks`，以 case ID 为键，每项包含 `name`、`status`，失败时还有 `failure_info.failure.type/message`；
- `interactions`，以 case ID 为键，包含 request、response、elapsed 与 timestamp；response body 使用 `{"$base64":"..."}` 表示。

fixture 的失败 check 为：

```json
{
  "name": "response_schema_conformance",
  "status": "failure",
  "failure_info": {
    "failure": {
      "type": "JsonSchemaError",
      "message": "Response violates schema ..."
    }
  }
}
```

网络 ERROR 的报告包含 `NonFatalError.value.type=ConnectTimeout`、`ScenarioFinished.status=error`、`SuiteFinished.status=error` 和 Coverage `PhaseFinished.status=error`。即使 FAIL 或 ERROR，`EngineFinished.stop_reason` 仍可能是 `completed`，不能用它单独判定 PASS。

### 5.4 Secret 兼容性问题

实测发现 `--output-sanitize true` 只把 `ScenarioFinished.recorder.interactions[*].request.headers.authorization` 处理成 `[Filtered]`，但以下位置仍会保留 Authorization 原值：

- `Initialize.command`，如果 secret 通过 `--header` CLI 参数传入；
- `ScenarioFinished.recorder.cases[*].value.headers`。

因此不能把原始 NDJSON 当成可持久化的安全报告，也不能把 secret 放入命令行参数。Phase 2 Adapter 必须满足：

1. 使用只对该子进程可见的环境变量，并通过临时 `schemathesis.toml` 的 `${ENV_NAME}` 插值读取 secret；
2. 临时配置和原始 NDJSON 使用受限临时目录，执行后清理；
3. 日志不记录完整 argv、环境变量、原始 NDJSON 或原始 stderr；
4. 解析时只生成白名单 DTO，并显式丢弃 `Initialize.command`、case headers、request headers 和可能包含凭据的 URI；
5. state、checkpoint、数据库、Langfuse 与最终报告只接收脱敏后的领域对象；
6. 即使报告文件尚未完成，也要持续执行 artifact 字节上限。

## 6. 后续阶段必须保持的判定规则

- oasdiff 决定 Contract Change 是否存在；LLM 不参与该判断。
- Schemathesis 与 NDJSON parser 决定 Regression 的 PASS / FAIL / ERROR；LLM 不参与该判断。
- `oasdiff --fail-on ERR` 的 exit code `1` 表示检测到 breaking；解析失败、超时、信号终止和工具错误必须单独处理。
- Schemathesis exit code `1` 可能是 FAIL，也可能是 ERROR；必须解析事件状态。
- LLM 失败只设置 `analysis_degraded`，不得改变 deterministic verdict。
- V1 默认禁止 external `$ref`。
- `target_base_url` 在执行 Schemathesis 前必须通过 host allowlist 和 SSRF 检查；`GUARDIAN_ALLOW_PRIVATE_TARGETS` 默认 `false`，只允许 development/local fixture 显式设为 `true`。
- 所有外部工具都经受控 Adapter 调用，参数数组执行，禁止 `shell=True`。
- Phase 3 必须包含 PostgreSQL checkpoint 中断恢复测试。

本文只完成 Phase 0 契约锁定，没有实现 GuardianState、Adapter、graph、route 或数据库模型。
