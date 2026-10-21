# API Guardian Effectiveness Benchmark

本目录是独立于 production Guardian 和 `tests/guardian` 的实验系统。Stage E1 验证第一版方法，Stage E2 扩展到 5 个 SUT、50 个 Runtime Mutation、5 个 clean controls 和 60 个 Contract Cases。两阶段都不用于宣称生产级普适效果。

## Research Questions

- RQ1：Contract 与 Runtime Schema Regression 检测是否正确？
- RQ2：Change-aware scope 在高 in-scope bug recall 下，能减少多少 operation、HTTP request 和 wall-clock time？
- RQ3：Guardian scope 与 Full Scope、人工 Oracle Scope 的差距是多少？
- RQ4：LLM explanation/diagnosis 是否 grounded、faithful、actionable？E1 不执行，留到 E4。
- RQ5：错误、LLM failure、checkpoint interruption 下是否稳定，成本是多少？E1 只测重复运行稳定性。

## Stage E1 Dataset

两个完全本地、无认证、可重置的 FastAPI SUT：

| SUT | OpenAPI operations | 存储 |
| --- | ---: | --- |
| `users_api` | 8 | 固定内存响应 |
| `orders_api` | 8 | 固定内存响应 |

10 个 Runtime Mutation 中：

- 8 个违反当前启用的 `response_schema_conformance` oracle；
- 2 个是 schema 合法但业务语义错误的 Negative Control；
- 每个 case 都有独立 `case_id`、Base/Head、目标 variant、人工 affected operation、bug presence、当前 oracle 是否理论可发现及理由。

Dataset 位于 `datasets/mutation/cases.jsonl`。`mutations/apply.py` 根据固定 patch 生成 `datasets/mutation/generated/*.yaml`，并在 manifest 中保存 Spec SHA256。

### Pilot 设计修正

真实 oasdiff 1.28.0 检查发现：response required 字段新增、response enum 收窄、`number -> integer` 在客户端兼容方向不一定属于 breaking。因此这些 case 在同一 operation 增加了一个明确 `string -> integer` breaking change 作为 scope trigger，同时保留原 Runtime Mutation 不变。

这不是修改 Guardian，也不是删除难例。它确保 E1 测量的是“存在 oasdiff 可见 API Change 时，change-aware scope 能否找到真正需要执行的 operation”。原发现写入每次 `experiment_log.md`。

## Compared Systems

- A `oasdiff_only`：仅 Contract Detection。
- B `full_schemathesis`：Head 的全部 operations。
- C `oasdiff_plus_full_schemathesis`：已实现，用于未来更纯粹隔离 scope；E1 不做 5 次重复计时。
- D `oracle_scope`：Dataset 的人工 affected operations。
- E `guardian_deterministic`：真实冻结 Graph，LLM disabled。
- F Guardian + DeepSeek：E1 不运行。

Full、Oracle、Guardian 都复用 production `SchemathesisAdapter`，固定：

```text
Schemathesis 4.24.3
response_schema_conformance
phases=examples,coverage
max_examples=1
generation_deterministic=true
max_redirects=0
request_timeout=2.0s
```

唯一 scope 差异分别是 all operations、Ground Truth operations、Guardian selected operations。每个 performance case 先 warm-up 1 次，再测量 5 次；执行顺序按固定 seed 轮换。

## Run

从仓库根目录运行：

```powershell
uv run python -m benchmarks.guardian.run_benchmark `
  --oasdiff-path C:\path\to\oasdiff.exe `
  --oasdiff-archive-sha256 <official-release-sha256>
```

`st` 默认从当前 uv 环境 PATH 解析。工具版本不匹配会直接停止，不会产生伪结果。

## Outputs

每次运行写入 `reports/latest/`：

- `raw_results.jsonl`：每个 warm-up 和 measured repetition；
- `summary.json`：聚合指标及 numerator/denominator；
- `summary.csv`：适合电子表格的扁平指标；
- `benchmark_report.md`：按 RQ 组织的报告；
- `missed_cases.json`：所有漏检及原因；
- `run_manifest.json`：Git、环境、版本、命令契约、Dataset/Spec SHA256、production core 前后 hash；
- `experiment_log.md`：真实方法决策和执行记录。

报告不保存 API key、环境变量列表、raw oasdiff/Schemathesis output、请求/响应 body 或本地 target URL。

## Interpretation Boundary

`response_schema_conformance` 是 schema-oriented oracle。业务金额错误、排序错误、错误实体等 schema 合法问题属于真实漏检范围，必须计入 Overall Mutation Recall，但不应错误归咎于 scope selector。

oasdiff 提供 Breaking Change Detection，Schemathesis 提供 schema-based test generation/validation。API Guardian 在本实验中需要证明的增量主要是 change-aware scope、统一编排和确定性分类；报告不会把第三方工具能力包装成自研算法。

## Stage E2

E2 Runtime Dataset 位于 `datasets/mutation/e2_*.jsonl`，Contract Ground Truth 位于 `datasets/contract/cases.jsonl`。五个 SUT 分别是 users、orders、products、inventory 和 booking，每个提供 8 个 operation。

E2 使用以下四个 Runtime 系统：

- Full Schemathesis：一次无 include filter 的完整 Spec CLI；
- oasdiff + Full Schemathesis；
- Oracle Scope；
- API Guardian deterministic。

四个系统继续使用完全相同的 Schemathesis 版本、checks、phases、max examples、deterministic generation、redirect 和 timeout 参数。E2 对 Full baseline 使用单进程完整 Spec，修正 E1 中复用逐 operation Adapter 所造成的重复进程启动开销；E1 原始数据不会被重写。

运行：

```powershell
uv run python -m benchmarks.guardian.run_e2 `
  --oasdiff-path C:\path\to\oasdiff.exe `
  --schemathesis-path .venv\Scripts\st.exe
```

E2 结果写入 `reports/e2/latest/`。两阶段完成后运行：

```powershell
uv run python -m benchmarks.guardian.generate_combined_report
```

综合 JSON、CSV 和 Markdown 写入 `reports/combined/latest/`。

## Stage E2.5：Miss Analysis 与 Scaling / Break-even

E2.5 冻结 production Guardian Core 和原 E2 raw，不做产品优化。它回答两个问题：

1. 原 Contract Scope recall/precision 低，究竟来自 selector、oasdiff、removed operation，还是 Ground Truth 语义混用；
2. 在 optimized one-process Full baseline 下，API surface 扩大到何种规模时，change-aware regression 才出现持续 wall-clock 收益。

Miss Analysis 将原 `expected_affected_operations` 保留为 `ContractAffectedScope`，并派生 Head 中仍存在的 `RuntimeExecutableScope`。15 个原 miss 全部重放 oasdiff，并要求 verdict、selected scope、fallback 与 change count 和冻结 E2 raw 一致。

Scaling Dataset 由 `scaling_dataset.py` 确定性生成，包含 8、16、32、64、128 operations。S1 固定影响 1 个 operation；S2 约影响 5%，实际为 1、1、2、3、6。每个 case/system 运行 1 次 warm-up 和 10 次 measured；warm-up 保存在 raw，但不进入统计。

正式运行：

```powershell
uv run python -m benchmarks.guardian.run_miss_analysis `
  --oasdiff-path C:\path\to\oasdiff.exe

uv run python -m benchmarks.guardian.run_scaling `
  --oasdiff-path C:\path\to\oasdiff.exe `
  --schemathesis-path .venv\Scripts\st.exe `
  --oasdiff-archive-sha256 <official-release-sha256>

uv run python -m benchmarks.guardian.generate_e2_5_report
```

正式结果位于 `reports/e2_5/`：

- `miss_analysis/`：逐 Case 证据 JSONL、双 Scope 指标 JSON/CSV 和 Markdown；
- `scaling/raw_results.jsonl`：440 条 warm-up/measured 原始记录；
- `scaling/run_manifest.json`：工具、系统、Git、硬件、参数、Dataset/Spec/Core/raw SHA256；
- `scaling/summary.json` / `summary.csv`：per-scale median、p95、paired difference/reduction 和 bootstrap CI；
- `scaling/scaling_report.md`：break-even、固定开销和负结果；
- `e2_5_report.md`：E2.5 总结；
- `preflight_integrity.json` / `postflight_integrity.json`：production Core 与原 E2 raw 前后哈希。

Break-even 的 operational definition 是：Guardian cell median 首次低于 Full，且至少连续两个规模点成立。单个孤立 faster point 不计为 observed break-even。256 operations 因预先设定的 `MAX_SCALE_OPERATIONS=128` 未运行，不允许外推为已测结果。
