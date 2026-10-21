# Unsupported Claims

以下表述没有被当前证据支持，不得用于 README、简历、演示或面试结论。

| 禁止表述 | 为什么不支持 |
|---|---|
| Production ready / 已生产就绪 | 项目是个人练习与求职项目；真实部署规模、SLA、运维和安全边界未被全面验证。 |
| Universally validates APIs / 适用于所有 API | external `$ref`、5 MiB 输入限制、认证能力和 tool compatibility 都有限制。 |
| Detects arbitrary business bugs / 能检测任意业务 Bug | E2 的 10 个 semantic negative controls 表明 schema-oriented oracle 不验证任意业务语义。 |
| Always faster than full regression / 总是比全量回归快 | E2 optimized baseline 上 runtime reduction 为 -0.82%；约 5% affected scaling scenario 到 128 operations 也未观察到持续 break-even。 |
| Real-world bug recall is X% / 真实世界 Bug Recall 为 X | E3-C 没有 seeded bugs，且只有一个仓库、两个 pair；不适合计算 real-world bug recall。 |
| LLM improves developer productivity / LLM 提高开发效率 | 正式 Human Study 未执行，participant count 为 0。 |
| LLM reduces triage time / LLM 降低排障时间 | 没有真人 timing 数据。 |
| LLM improves root-cause accuracy / LLM 提高真人根因准确率 | E4 只评价模型输出质量，没有参与者对照实验。 |
| Human utility demonstrated / 已证明真人效用 | `human_evaluation_status=NOT_CONDUCTED`。 |
| Hallucination rate is universally 0% / 幻觉率为 0 | 只能说冻结 automated audit 中 0/50 observed；样本有限且审查不是正式真人研究。 |
| Supports all OpenAPI / 支持所有 OpenAPI | 4 个 Supabase real-world pairs 在 oasdiff spec load 阶段失败；external refs 默认拒绝。 |
| Supports arbitrary authenticated APIs / 支持任意认证 API | Guardian V1 不允许用户注入任意 Authorization/API key 测试参数。 |
| Breaking detection is our proprietary algorithm / breaking 检测为自研算法 | 基础 breaking detection 主要来自 oasdiff 1.28.0；Guardian 的增量在标准化、scope、编排、隔离和报告。 |
| Runtime generation is our proprietary fuzzer / runtime 生成是自研 Fuzzer | 请求生成与 schema check 主要由 Schemathesis 4.24.3 提供，项目没有实现自定义 Fuzzer。 |
| Real-world validation is general | E3-C 只有 Supabase Auth 一个仓库，最终标签为 `PARTIALLY_VALIDATED`。 |
| E3 accuracy rose after P1 | P1 改善的是 scope precision/fallback；E3-B contract accuracy 始终是 14/19。 |
| Diagnosis accuracy is 100% | 冻结 E4 Diagnosis category accuracy 是 19/25 = 76%。 |

## 可替代表述

- 不说“生产就绪”，说“完成了 JWT、PostgreSQL 持久化、checkpoint 恢复和安全边界的工程化 vertical slice”。
- 不说“检测任意 Bug”，说“在受控 schema-detectable mutations 上实现 40/40 recall，同时如实保留 10 个无法检测的业务语义 negative controls”。
- 不说“总是更快”，说“scope/request 数显著下降；wall-clock 收益取决于 API 规模、affected ratio 与 per-operation subprocess 开销”。
- 不说“LLM 提高工程师效率”，说“50-case automated audit 测得结构化输出、grounding、category accuracy 和 4/4 failure isolation；真人效用未评估”。
