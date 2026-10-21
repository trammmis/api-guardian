# E4 Pre-collection Checkpoint

## Status

`EXECUTION_FREEZE_COMPLETE_COLLECTION_BLOCKED`。

E4-A 已完成真实 Condition B 一次性冻结、A/B evidence parity、50-case 自动评测、failure isolation、评分规则、中文 consent/recruitment 草案、dry-run protocol 和 10 个匿名 sequence packet。正式参与者数量仍为 0，未收集或伪造真人数据。

正式收集仍被以下事项阻止：

1. 用户确认 consent draft；
2. 用户确认 recruitment text；
3. 用户选择 data retention；
4. 用户批准并完成 1 名非正式参与者的 excluded dry run；
5. dry run 未发现 timer、顺序、泄漏或提交完整性问题。

## Frozen inputs

- Case manifest SHA256：`c661f11b70a2ec7b61cc3390d7c17b23f565cc17322a4327024989d37e86b478`
- Ground Truth SHA256：`d481accc96a31ae175048ba8e8175ca37fe917143a29f228ae7df23423c22f4e`
- Condition B input SHA256：`7dd0426bf26b8dde221907bcefa1ac5bc8f62c4f99dda19650907d2f6750e435`
- Condition B output SHA256：`65b099453a72768f72f06e6df86623f8031d0c3de151b7549dc53463fb444120`
- 50-case raw output SHA256：`d4b4d393288725bd8dbab862f890214713eabcd7ed322f0534049b523f6f95d4`

## Condition B generation

- 模型：`deepseek-v4-flash`
- Temperature：`0`
- Structured output：`function_calling`
- Thinking：disabled
- Production retry：最多 3 attempts，只使用现有 retry policy
- 适用分析调用：18
- 成功：18
- 失败：0
- Regeneration：禁止；没有 cherry-pick、重写或润色
- Raw provider response：未保存
- Token usage：production abstraction 未暴露，因此记为 unavailable

前两次命令失败均发生在 provider 初始化之前，未产生任何模型输出，已记录于 `execution_amendments.jsonl`。随后按冻结的 module invocation 完成唯一一次正式生成。

## A/B evidence parity

13/13 Case 通过。A 和 B 的共享 `deterministic_evidence` 块均使用 UTF-8、sorted keys、compact separators 的 canonical JSON 计算 SHA256；B 只追加 `Additional analysis`。

任何适用分析为空的 B 都按 ITT 保留为 B==A；不得从主分析移除。`LLM_PRESENT_SUBGROUP` 只能作为 supplemental。

## Automated 50-case evaluation

该结果来自冻结输出后的规则与语义审查，没有为改善结果重新调用模型。

| Metric | Result |
|---|---:|
| Structured output success | 50/50 = 100% |
| Risk category accuracy | 25/25 = 100% |
| Diagnosis category accuracy | 19/25 = 76% |
| Combined category accuracy | 44/50 = 88% |
| Evidence grounding | 50/50 = 100% |
| Unsupported claim rate | 0/50 = 0% |
| Serious hallucination rate | 0/50 = 0% |
| Recommendation grounding | 50/50 = 100% |
| Provider failure rate | 0/50 = 0% |

Latency：mean `2.686s`，median `2.690s`，p95 `3.182s`。Token usage unavailable。

安全与 grounding 审查标记为 `ai_assisted_postfreeze_semantic_audit_not_human`，不能表述为 human-confirmed。六个 Diagnosis category miss 已保留，不调 Prompt、不重跑。

## Failure isolation

安全 Fake 覆盖 timeout、invalid structured output、provider failure、provider 429。4/4 均保持：

- Contract verdict = fail
- Regression verdict = fail
- Final verdict = fail
- analysis_degraded = true

该 429 Case 验证 Guardian graph 的隔离语义，不声称单独验证生产 retry 调度。

## Frozen endpoints and rules

Primary objective endpoints：RootCauseAccuracy、NextActionAccuracy、TriageTimeSeconds。Secondary objective：ConfidenceCalibration。Subjective secondary：Clarity、Actionability、Helpfulness、Trust。

ITT 包含全部预冻结 assignment，包括 B==A。T0 是完整报告首次呈现；T1 是 objective answers 与 confidence 提交；主观评分时间不计入 triage time。RootCauseAccuracy 和 NextActionAccuracy 均按冻结 option set 做 0/1 评分。Confidence 使用 0–100，并报告 Brier-style error 与正确/错误答案平均置信度。

Utility 决策规则已在 participant result 前冻结：RootCauseAccuracy 不下降，并且 NextActionAccuracy 至少提高 5 percentage points 或 median TriageTime 至少缩短 10%，同时 participant-visible 和 automated SeriousHallucinationRate 不超过 5%，才能标记 `LLM_UTILITY_SUPPORTED`。只有主观评分改善最多标记 `SUBJECTIVE_PREFERENCE_ONLY`；无客观改善标记 `NO_MEASURABLE_OBJECTIVE_UTILITY`；少于 5 人标记 `PILOT_UNDERPOWERED`。

## Participant materials

- 10 个匿名 sequence packet；每个 13 个 Report。
- 每个 Case 在 10 个序列中 A=5、B=5。
- 可见 packet 中不存在 Ground Truth、source stage、condition provenance、expected answer、difficulty 或生成来源术语。
- Condition mapping 只在 researcher-only `participant_packet_manifest.json`。
- Dry run 状态：`READY_NOT_STARTED`，其数据必须永久标记 `DRY_RUN_EXCLUDED`。

## Consent, recruitment, retention

- Consent：`DRAFT_PENDING_USER_CONFIRMATION`
- Recruitment：`DRAFT_PENDING_USER_CONFIRMATION`
- Retention：`PENDING_USER_CONFIRMATION`
- 候选 A：3 months
- 候选 B：6 months
- 候选 C：until project evaluation is finalized, then delete raw participant data
- 任何公开报告只保留 aggregated + anonymized 数据。

## Integrity

14/14 个 Guardian Core、Prompt、Schema 和 LLMService 文件与 preflight SHA256 一致。Case manifest 与 Ground Truth 哈希均与用户确认值一致。没有修改 production Prompt、Schema、DeepSeek model、deterministic report 或 verdict logic。

## Current conclusion

`human_evaluation_status = pending`。当前结果只能说明研究执行材料已冻结和自动质量检查已完成，不能声称 LLM 的人类效用已被证明。
