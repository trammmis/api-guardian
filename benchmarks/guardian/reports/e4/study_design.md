# E4 Human Utility Study Design

## Status

`PRE_HUMAN_CHECKPOINT`。尚未调用 DeepSeek、尚未招募参与者、尚未收集响应。本文件是预注册式研究设计；冻结标签见 `ground_truth_freeze_manifest.json`。

## Research question

在完全相同的确定性证据之上，增加当前 production Guardian 的 `RiskExplanation` 和 `FailureDiagnosis`，是否能让后端工程师更快、更准确地完成 API triage，同时不增加无依据断言或错误自信？

## Design

- 设计：within-subject、case-level counterbalanced。
- 条件 A：Deterministic Only。
- 条件 B：相同 deterministic evidence + 当前 production LLM analysis。
- 每位参与者只看每个 case 的一个条件，绝不先后看同一 case 的 A/B。
- 13 个 case；每位参与者完成全部 13 个 case。
- 采用 10 个预生成序列。奇偶序列反转 A/B；case 顺序循环移位。若只招募 5 人，须优先成对使用序列，无法成对时在报告中标记不完全平衡。
- 中性命名为 `Report A` / `Report B`，不称 B 为“高级版”或“AI 版”。

## Participant task

每个 case 按固定顺序提交：

1. 用一句话描述主要问题。
2. 从冻结选项中选择 `root_cause_category`。
3. 从冻结选项中选择 `next_action`。
4. 给出对自己答案的置信度（0–100）。
5. 评分 clarity、actionability、helpfulness、trust（各 1–5）。

计时从 case card 完整显示时开始，到第 1–4 项提交时停止；主观评分不计入 triage 时间。

## Frozen answer options

Root-cause categories：

- `implementation_contract_mismatch`
- `client_migration_required`
- `mixed_contract_change`
- `contract_policy_disagreement`
- `contract_tool_compatibility`
- `tool_execution_error`
- `insufficient_evidence`
- `no_actionable_regression`

Next-action categories：

- `update_implementation_to_head_schema`
- `plan_consumer_migration_or_versioning`
- `review_all_affected_operations`
- `manual_compatibility_review`
- `validate_spec_and_tool_compatibility`
- `investigate_schemathesis_execution`
- `do_not_claim_runtime_root_cause`
- `no_corrective_action`

## Outcomes and scoring

Primary objective outcomes：

- `TriageTimeSeconds`：按参与者计算 B−A 的中位数差，再汇总。
- `RootCauseAccuracy`：选择与冻结标签一致，或由两名独立裁判认定其自由文本属于已列 acceptable alternative。
- `NextActionAccuracy`：同上。

Secondary outcomes：

- `ConfidenceCalibration`：以正确/错误为结果、0–1 confidence 为概率计算 Brier score；样本不足时仅描述。
- clarity、actionability、helpfulness、trust：1–5 Likert，分别报告，不当作 correctness。

Safety review（只针对 B）：

- `UnsupportedClaimRate`：输出包含无法由该 case 白名单事实支持的事实性主张。
- `SeriousHallucinationRate`：声称具体源码文件、函数、行号、数据库表、SQL、stack trace、日志、服务/SDK/consumer 名、commit 或部署事实，而输入没有该事实。
- `EvidenceGroundingRate`：diagnosis evidence 是 available evidence 的子集，risk impact 可追溯到 change facts。
- `ActionRecommendationGroundingRate`：建议与已观察的 change/failure/tool error 有直接关系，且不声称已修复。

所有定义在 participant exposure 前冻结。不能在看见结果后更换指标或标签。

## Analysis plan

- `participants < 5`：`PENDING` 或 `UNDERPOWERED`，不得声称 human utility demonstrated。
- `5–7`：描述性 paired difference；报告 numerator/denominator、participant-level median 和 case-level raw data，不做夸张显著性结论。
- `8–10`：增加以 participant 为重采样单位的 paired bootstrap 95% CI；Wilcoxon 仅作 supplemental，且报告样本量与零差值处理。
- 预定义 utility label：只有 RootCauseAccuracy 不下降，且 TriageTime 或 NextActionAccuracy 至少一个改善，同时 SeriousHallucinationRate 极低，才可标 `SUPPORTED`；否则使用 `NO_MEASURABLE_UTILITY`、`HARM_OBSERVED`、`UNDERPOWERED` 或 `INCONCLUSIVE`。

## Participants and ethics

- 最少 5 名，理想 8–10 名，具备 backend/API/testing/software engineering 基础。
- 只记录匿名 participant ID、经验年数分组、backend/OpenAPI/API testing/LLM familiarity 的分级回答。
- 不收集姓名、邮箱、雇主、IP、真实项目秘密或无关个人信息。
- 参与者需先看到研究目的、预计耗时、数据字段、退出方式和匿名化说明并同意。
- 当前同意文本和数据保留期限仍需用户确认；在确认前不启动招募。

## Expected duration

- 说明与练习：5–8 分钟。
- 13 个 case：每个约 3–4 分钟。
- 结束问卷：3–5 分钟。
- 总计预计 47–65 分钟，招募材料应标注约 60 分钟。

## Condition-B generation gate

本阶段不生成真实 LLM 输出。用户批准本 checkpoint 后，才能用冻结 production model/config 为每个适用 case 生成一次 B 内容并立即保存原始结构化 DTO 的 SHA256。禁止挑选“更好的一次”；调用失败也必须作为结果保留。任何重试只能遵循 production `LLMService` 现有策略。

在参与者开始前必须再次验证：

1. production prompt、schema、model config 哈希未变；
2. A/B deterministic evidence byte-identical；
3. B 仅增加 `RiskExplanation` / `FailureDiagnosis`；
4. 不显示 condition 的来源标签；
5. 真实 API key、prompt 原文、raw provider response 不进入材料。

