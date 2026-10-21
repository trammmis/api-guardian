# API Guardian E1 + E2 完整实验报告

## 1. 报告范围

本报告合并 Stage E1 和 Stage E2 的真实执行结果，用于回答 API Guardian 是否在保持较高可检测 Bug Recall 的同时减少 API Regression 成本。它不包含 Real-world GitHub Dataset、LLM 正式 Evaluation 或 Human Evaluation。

生产 Guardian Core 在两个正式运行期间均保持哈希不变。结果来自保存的 per-case raw JSONL，不是手工填入的演示数字。

## 2. 两阶段实验设计

| 项目 | E1 | E2 |
| --- | ---: | ---: |
| Controlled SUTs | 2 | 5 |
| Runtime cases | 10 | 55 |
| Runtime mutations | 10 | 50 |
| Clean controls | 0 | 5 |
| Contract cases | E1 runtime pairs only | 60 |
| Measured repetitions | 5 | 5 |
| Warm-up | 1 | 1 |

E1 和 E2 使用相同的 oasdiff 1.28.0、Schemathesis 4.24.3、`response_schema_conformance`、`examples,coverage`、`max_examples=1`、deterministic generation、2 秒 request timeout 和 0 redirect。

### Runtime 方法差异

E1 的 Full baseline 复用了生产逐 operation Adapter，因此一个 8-operation Spec 启动 8 个 Schemathesis 进程。E2 将 Full baseline 修正为一次不带 include filter 的完整 Spec 运行。这个修正显著减少 Full baseline 的进程启动成本，因此两阶段 runtime 不应直接合并或用于趋势推断；报告分别展示。

## 3. RQ1 Detection Correctness

| 指标 | E1 | E2 |
| --- | ---: | ---: |
| Contract Verdict Accuracy | 10/10 (100.00%) | 55/60 (91.67%) |
| Guardian Bug Detection Recall | 8/10 (80.00%) | 40/50 (80.00%) |
| In-Scope Mutation Recall | 8/8 (100.00%) | 40/40 (100.00%) |
| Overall Mutation Recall | 8/10 (80.00%) | 40/50 (80.00%) |
| Bug Detection Precision | 8/8 (100.00%)* | 40/40 (100.00%) |

注：E1 没有 clean no-bug controls，不能用于正式 false-positive 结论。E2 增加了真实 clean controls。

E2 中 In-Scope Recall 与 Overall Recall 的差异量化了当前 Schema Oracle 的能力边界：结构错误可以检测，schema-valid 的金额、实体、库存或状态语义错误不能检测。

## 4. RQ2 Regression Efficiency

| Stage | Full median | Full p95 | Guardian median | Guardian p95 | Paired median reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| E1 | 29.4641 s | 35.8763 s | 4.5613 s | 5.4981 s | 84.81% |
| E2 | 2.6047 s | 3.8470 s | 2.6369 s | 3.8632 s | -0.82% |

E2 是更可信的 performance 结论，因为 Full baseline 使用单进程完整 Spec。North Star 结论应以 E2 的 In-Scope Recall、Scope Reduction 和 paired runtime reduction 联合表述，而不是只引用 E1 的较大时间差。

### E2 North Star

- In-Scope Bug Recall：40/40 (100.00%)
- Operations Reduction：87.50%（8 → 1）
- HTTP Request Reduction：86.15%（65 → 9）
- Paired Median Runtime Reduction：-0.82%

因此，E2 证明了测试操作数与 HTTP 请求数的下降，但没有证明小型 8-operation SUT 上的端到端时间收益。Guardian 的逐 operation 子进程与 oasdiff 编排成本抵消了请求减少；这是真实负结果，不应以 E1 的 84.81% 取代。

## 5. RQ3 Scope Quality

| 指标 | E1 Runtime Dataset | E2 Runtime Dataset | E2 Contract Dataset |
| --- | ---: | ---: | ---: |
| Scope Recall | 10/10 (100.00%) | 55/55 (100.00%) | 40/55 (72.73%) |
| Scope Precision | 10/10 (100.00%) | 55/55 (100.00%) | 40/110 (36.36%) |
| Mean Scope Reduction | 87.50% | 87.50% | 70.00% |
| Mean Oracle Overhead | 0.0000 | N/A | 1.2000 |

E2 Contract Dataset 包含 removed operations 与 component changes，因此比 E1 的直接单-operation changes 更能揭示 scope 的真实边界。removed endpoint/method 在 Head Spec 中已经不存在，不能成为 Schemathesis 的可执行 operation，这是 Contract affected scope 与 Runtime executable scope 之间的结构性差异。

## 6. RQ4 LLM Diagnosis Utility

E1/E2 均显式禁用 LLM。当前报告不能声称 RiskExplanation 或 FailureDiagnosis 提升了理解效率。LLM Evaluation 与匿名 Human Study 必须在后续独立阶段完成。

## 7. RQ5 Reliability and Cost

| 指标 | E1 | E2 |
| --- | ---: | ---: |
| Deterministic Reproducibility | 150/150 (100.00%) | 1100/1100 (100.00%) |
| LLM Calls | 0 | 0 |
| LLM Cost | N/A | N/A |

E1/E2 没有运行 LLM，因此不存在 token/cost 数据。Dedicated timeout、missing executable、malformed NDJSON 和 checkpoint interruption 的 Benchmark 仍需独立 Reliability Dataset；现有产品测试只能作为非回归证据，不能替代正式 Benchmark。

## 8. 可归因的工程价值

- oasdiff 提供 Breaking Change Detection，本项目不把这部分包装为自研算法。
- Schemathesis 提供基于 OpenAPI 的请求生成和响应 Schema 检查。
- API Guardian 本阶段被测的增量价值是：change-aware scope、两类事实的统一编排、确定性 verdict 和一致的可审计报告。

## 9. 主要限制

1. 五个 SUT 仍是本地 synthetic FastAPI 服务，外部有效性有限。
2. Runtime Dataset 仍以无认证 GET operations 为主。
3. 当前 oracle 无法发现任意业务语义 Bug。
4. Contract response-status removal 的人工 Ground Truth 与 oasdiff 可能存在定义差异，报告按原始结果保留。
5. E1/E2 runtime 方法不同，不能把样本直接合并计算一个总平均值。
6. 尚未执行 Real-world Dataset、LLM Evaluation 或真实 Human Evaluation。

## 10. 复现与审计

- E1 run: `guardian-e1-20260903T075056Z-5e2ede67`
- E2 run: `guardian-e2-20260906T071336Z-2eacf61c`
- E1 raw: `benchmarks/guardian/reports/latest/raw_results.jsonl`
- E2 Contract raw: `benchmarks/guardian/reports/e2/latest/contract_raw_results.jsonl`
- E2 Runtime raw: `benchmarks/guardian/reports/e2/latest/raw_results.jsonl`
- 两阶段各自的 `run_manifest.json` 保存输入哈希、工具版本、Python、OS、CPU、Git 状态和固定参数。
- E2 raw 共 1320 行（55 cases × 4 systems × 1 warm-up/5 measured），Contract raw 共 60 行。
- 被中断运行保存在 `benchmarks/guardian/reports/e2/interrupted/`，明确不参与聚合。
