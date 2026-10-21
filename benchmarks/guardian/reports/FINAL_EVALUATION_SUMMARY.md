# API Guardian Final Evaluation Summary

## 项目最终状态

- `PRODUCT_CORE_COMPLETE`
- `EVALUATION_COMPLETE_FOR_PERSONAL_PROJECT`
- `HUMAN_UTILITY_NOT_EVALUATED`
- `REAL_WORLD_RUNTIME_PARTIALLY_VALIDATED`
- `FROZEN_FOR_RESUME_AND_INTERVIEW`

本文汇总 API Guardian 已完成的产品阶段和评估阶段。所有百分比都附带适用的数据集；不同实验的数据不合并为一个“总准确率”。E4 真人研究没有执行，参与者数量为 0。

## Phase 1 — Contract Gate

### Goal

建立确定性的 OpenAPI 契约门禁：比较 Base 与 Head Spec，标准化 breaking change，并选择受影响 operation。

### Dataset

项目内确定性 OpenAPI fixtures，包含 `response.id: string -> integer` 主案例。

### Main metrics

- Contract verdict accuracy：1.0。
- Scope recall：1.0。
- Scope precision：0.6，作为观察指标。

### Negative results and limitations

- Recall-first 策略可能多选 operation。
- external `$ref` 默认拒绝。
- breaking change 的基础检测能力主要来自固定版本 oasdiff 1.28.0，而不是项目自研 diff 算法。

### What changed because of the result

形成 `oasdiff -> ContractChange -> ScopeSelection -> deterministic verdict` 的稳定核心，并为 Phase 2 提供可执行 scope。

## Phase 2 — Runtime Regression

### Goal

用 Head OpenAPI 驱动真实 Schemathesis 4.24.3 回归，把 runtime 结果标准化为 PASS、FAIL 或 ERROR。

### Dataset

本地 fixture API。主案例中 Head 要求 integer，真实 API 仍返回 string。

### Main metrics

- Contract、regression、final verdict accuracy：均为 1.0。
- Error classification accuracy：1.0。
- Scope recall：1.0；scope precision：0.6。
- 主案例：Contract FAIL、Regression FAIL、Final FAIL。

### Negative results and limitations

- 当前 oracle 以 schema conformance 为主，不能检测任意业务语义错误。
- 每个 selected operation 独立启动 Schemathesis，正确性优先但存在固定进程开销。

### What changed because of the result

加入受控子进程、NDJSON 解析、结果脱敏和 `ERROR > FAIL > PASS` 的确定性合并规则。

## Phase 3 — Durable Backend and Checkpoint

### Goal

把 Guardian Core 包装为有 JWT 隔离、PostgreSQL 持久化和 LangGraph checkpoint 恢复能力的 FastAPI 服务。

### Dataset

本地 HTTP、PostgreSQL 与 checkpoint 集成测试；没有访问公网目标。

### Main metrics

- JWT、owner isolation、Scan/Report 持久化用例通过。
- 真实 PostgreSQL checkpoint 中断恢复通过；恢复结果与 uninterrupted run 一致。
- Guardian 工具 ERROR 被保存为 `completed + final_verdict=error`，与服务内部异常的 `failed` 分开。

### Negative results and limitations

- durable workspace 仍是单实例本地文件系统，不是对象存储。
- 完整 DNS pinning 未实现；V1 防御为初始验证、执行前重验证和禁用 redirect。
- 同步扫描语义不适合大规模并发任务。

### What changed because of the result

增加 Route、GuardianService、GuardianRepository、两张业务表与 AsyncPostgresSaver；核心 CLI 仍可脱离 HTTP/DB 运行。

## Phase 4 — LLM Analysis

### Goal

在 deterministic final verdict 之后生成结构化 RiskExplanation 和 FailureDiagnosis。LLM 只解释事实，不决定门禁。

### Dataset

Mock/fixture 测试、早期 curated evaluation，以及最终 E4 的 50-case frozen automated audit。

### Main metrics

- Structured output success：50/50 = 100%。
- Risk category accuracy：25/25 = 100%。
- Diagnosis category accuracy：19/25 = 76%。
- Combined category accuracy：44/50 = 88%。
- Evidence grounding：50/50 = 100%。
- Recommendation grounding：50/50 = 100%。
- Failure isolation：4/4。

### Negative results and limitations

- Diagnosis category accuracy 只有 76%，六个 miss 保留。
- 0/50 unsupported claim 和 0/50 serious hallucination 只是冻结自动审计中的观察值，不是普遍保证。
- 没有执行真人效用研究，因此不能声称提高工程师效率或准确率。

### What changed because of the result

GuardianReport 增加白名单分析字段；LLM timeout、provider error 或结构化输出失败只设置 `analysis_degraded=true`，不改变 deterministic verdict 或 completed 状态。

## E1 — Initial Timing Experiment

### Goal

建立第一版 Full Schemathesis、Oracle Scope 和 Guardian deterministic 对照。

### Dataset

2 个本地 SUT、10 个 runtime mutation，其中 8 个是当前 schema oracle 理论可检测 mutation。

### Main metrics

- Contract accuracy：10/10。
- In-scope mutation recall：8/8。
- Overall mutation recall：8/10。
- 初始结果显示约 84.81% wall-clock reduction。

### Negative results and limitations

E1 Full baseline 对每个 operation 启动一次 Schemathesis，包含不公平的重复 process-startup 开销。因此 84.81% 不是可信的主要性能结论。

### What changed because of the result

E2 把 Full baseline 改为一次无 filter 的 CLI invocation，并显式冻结公平参数。

## E2 — Controlled Benchmark

### Goal

在更多 SUT 与 mutation 上测量检测正确性、scope 成本和公平 wall-clock 对照。

### Dataset

5 个 SUT；55 个 runtime cases，其中 50 个 mutation、5 个 clean case；40 个 detectable mutation、10 个 semantic negative controls；60 个 contract cases；每个性能 case 5 次 measured 加 1 次 warm-up。

### Main metrics

- Contract verdict accuracy：55/60 = 91.67%。
- In-scope mutation recall：40/40 = 100%。
- Overall mutation recall：40/50 = 80%。
- Operations reduction：87.50%。
- HTTP request reduction：86.15%。
- Deterministic reproducibility：1100/1100 = 100%。
- Guardian vs optimized Full paired median runtime reduction：-0.82%。

### Negative results and limitations

- 10 个 schema 正确但语义错误的 negative controls 均不属于当前 oracle 能力范围。
- 大幅减少 operation/request 没有在 8-operation SUT 上转化为 wall-clock 优势；Guardian 反而略慢。

### What changed because of the result

停止使用 E1 性能结论，进入 E2.5 分析固定开销和 break-even。

## E2.5 — Miss and Scaling Diagnostic

### Goal

逐 case 解释 contract/scope miss，并寻找 change-aware regression 的规模 break-even。

### Dataset

E2 的 15 个 contract/scope miss；参数化 8、16、32、64、128 operations 的 scaling SUT，覆盖 exactly-one 和约 5% affected scope。

### Main metrics

- 15 个 miss：10 个 removed-operation-not-executable，5 个 oasdiff/人工 Ground Truth disagreement。
- 有明确 executable mapping evidence 的 selector-only false negative：0。
- Exactly-one scenario 从已测 16-operation 点开始出现持续 faster region，并持续到 128；128 operations 时 runtime reduction 为 34.86%。
- 约 5% affected scenario 在 128 operations 内没有持续 break-even。

### Negative results and limitations

- 原 contract scope recall 40/55 = 72.73%，precision 40/110 = 36.36%，混入了 contract impact 与 Head executable scope 的语义差异。
- 性能结论与机器、localhost 和固定 Schemathesis 参数相关。
- per-operation Schemathesis subprocess startup 是主要瓶颈。

### What changed because of the result

正式拆分 ContractAffectedScope 与 RuntimeExecutableScope；未修改生产 selector。

## E3 — Real-world Git-history Validation

### Goal

在真实公开 GitHub OpenAPI 历史中验证 contract detection、scope mapping 和 fallback 行为。

### Dataset

Immich、Supabase Auth、NetBird、Appwrite 四个仓库，19 个 headline-quality frozen version pairs。Ground Truth 在 system output 前冻结，包含 AI-assisted initial review 和人工 spot check amendment；不是从 oasdiff 反推。

### Main metrics

- Contract verdict accuracy：14/19 = 73.68%。
- Breaking recall：7/9 = 77.78%。
- Breaking precision：7/7 = 100%。
- System execution success：15/19 = 78.95%。
- E3-B 原始 executable scope recall：58/73 = 79.45%。
- E3-B 原始 executable scope precision：58/1690 = 3.43%。
- Successful-pipeline executable scope recall：58/58 = 100%。
- True executable selector false negatives：0。

### Negative results and limitations

- 4 个 Supabase pair 在 oasdiff spec load 阶段 ERROR。
- 另 1 个 Supabase breaking Ground Truth 没有得到 oasdiff ERROR-level basis。
- 3 个 removal 相关 fallback 导致 1632 个额外 operation，是 3.43% precision 的主要来源。
- 仅 4 个仓库，存在语言、spec-history 与人工 Ground Truth 偏差。

### What changed because of the result

没有在 E3 数据上直接修产品；先进入 E3.5 root-cause diagnostic。

## E3.5 — Root-cause Diagnostic

### Goal

解释 Supabase tool compatibility、Supabase 005 policy disagreement 和三次 fallback explosion。

### Dataset

E3-B 的 4 个 Supabase load error、1 个 false negative 和 3 个 fallback case；headline 指标保持冻结。

### Main metrics

- Supplemental non-fallback executable recall/precision：31/31、31/31。
- Supplemental fallback-only precision：27/1659；over-selection 1632。
- Supplemental tool-compatible conditional contract accuracy：14/15 = 93.33%。

### Negative results and limitations

- Supabase specs 的 schema slot 引用了 `components.responses` 下的 response object；独立 validators 对完整/最小输入有差异，oasdiff 1.28.0 无法加载。
- Supabase 005 属于 Ground Truth compatibility convention 与 oasdiff policy disagreement，不是 Guardian normalization 丢失。
- 三个 fallback 的直接触发点是 removed operation 在 Head 中不可 resolve；并非 Appwrite component attribution 本身。

### What changed because of the result

提出但没有直接实现 candidate fixes，并选择最窄的 P1 removed-operation no-fallback ablation。

## P1 — Removed-operation Ablation

### Goal

验证可确定证明的 operation removal 能否贡献空 executable scope，而不触发全量 fallback。

### Dataset

23 个冻结 case：15 pure removal、5 mixed removal、3 non-removal controls。

### Main metrics

- Executable recall：31/31 -> 31/31。
- Executable precision：31/1741 = 1.78% -> 31/38 = 81.58%。
- Fallback rate：20/23 = 86.96% -> 2/23 = 8.70%。
- Extra selected operations：1710 -> 7。
- Contract verdict agreement：23/23。

### Negative results and limitations

- 仍有 7 个 extra operations。
- 实验只覆盖两个冻结的 oasdiff 1.28.0 removal rule IDs；不能推广到任意文本或未来工具版本。

### What changed because of the result

结果满足 recall 不下降与 mixed-case safety gate，因此进入独立 P1-I production integration。

## P1-I — Production Integration

### Goal

把 P1 的逐 change、结构化 removed-operation policy 最小化接入生产 selector，再重放冻结数据。

### Dataset

P1 23 cases、E2/E2.5 relevant cases、E3-B 19 headline pairs。

### Main metrics

- E3-B contract accuracy：14/19 -> 14/19，不变。
- E3-B executable recall：58/73 -> 58/73，不变。
- E3-B executable precision：58/1690 = 3.43% -> 58/63 = 92.06%。
- E3-B fallback rate：3/19 -> 0/19。
- Selected operations：1690 -> 63。
- Extra operations：1632 -> 5。

### Negative results and limitations

- Appwrite 002 仍选择 6 个 operation，而 oracle 为 1，保留 5 个 extra operation。
- 策略与 oasdiff 1.28.0 的结构化 rule contract 绑定，升级工具需重新验证。

### What changed because of the result

只修改了 production scope resolution 所需的最小逻辑；contract、regression、final verdict 语义保持不变。

## E3-C — Narrow Real-world Runtime Validation

### Goal

在 exact historical source/spec 对齐的本地 Supabase Auth 服务上执行 production graph 和 scoped runtime regression。

### Dataset

1 个真实仓库、2 个 immutable historical pairs；每个 system/case 5 次 measured，localhost + fresh PostgreSQL，无真实 credential。

### Main metrics

- Production graph completion：10/10 measured runs。
- Guardian/Oracle scoped regression agreement：2/2。
- Scope recall/precision：2/2。
- Case 005：39 -> 1 operation，1431 -> 66 requests，request reduction 95.39%，Guardian regression PASS。
- Case 006：38 -> 1 operation，1260 -> 82 requests，request reduction 93.49%，Guardian regression ERROR。
- 一次 checkpoint/persistence correctness run 完成。
- Final label：`PARTIALLY_VALIDATED`。

### Negative results and limitations

- 只有一个仓库、两个 pair，不能一般化到所有真实 API。
- 只有 1/2 得到 conclusive runtime verdict；Case 006 稳定出现 Schemathesis execution ERROR，按要求未修复。
- Full Schemathesis 自身也在这些真实 specs 上遇到 unrelated operation errors。
- 这不是 mutation benchmark，不能报告真实世界 bug recall。

### What changed because of the result

没有修改生产代码；停止继续扩大 runtime benchmark。

## E4 — Automated LLM Quality and Failure Isolation

### Goal

评价当前生产 Prompt/Schema 下的结构化输出、category、grounding、unsupported claims 与 failure isolation。真人效用研究明确不执行。

### Dataset

50 个冻结 automated cases：25 Risk、25 Diagnosis。另有 13-case A/B 协议和 10 个 participant packets，仅作为未执行的研究协议归档。

### Main metrics

- Structured output success：50/50 = 100%。
- Risk category accuracy：25/25 = 100%。
- Diagnosis category accuracy：19/25 = 76%。
- Combined category accuracy：44/50 = 88%。
- Evidence grounding：50/50 = 100%。
- Recommendation grounding：50/50 = 100%。
- Unsupported claims：0/50 observed。
- Serious hallucinations：0/50 observed。
- Provider failure：0/50。
- Failure isolation：4/4。

### Negative results and limitations

- Diagnosis 6/25 分类错误。
- semantic audit 是 AI-assisted post-freeze audit，不是真人独立评级。
- `participant_count=0`，`human_evaluation_status=NOT_CONDUCTED`；不能声称 LLM 提高 triage time、root-cause accuracy 或开发者生产力。

### What changed because of the result

没有修改 Prompt、模型或产品代码。E4 关闭为 automated evaluation；Human Study 协议标记 `PROTOCOL_PREPARED_NOT_EXECUTED`。

## Final evidence boundary

当前证据支持：Guardian 能在受控 schema-detectable mutation 上保持 40/40 recall；能在冻结真实 Git-history 数据上暴露工具错误与 policy disagreement；P1 在不降低冻结 executable recall 的情况下大幅减少 removal fallback over-selection；LLM 能稳定输出结构化、grounded 的解释并与 verdict 隔离。

当前证据不支持：通用 production readiness、任意业务 bug 检测、所有 OpenAPI 兼容性、所有规模都比 full regression 快、真实世界 bug recall、任意认证 API，以及 LLM 的真人效率收益。
