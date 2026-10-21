# E4 Closure — Automated LLM Quality & Failure Isolation Evaluation

## Final status

- E4 scope: `AUTOMATED_LLM_QUALITY_AND_FAILURE_ISOLATION_EVALUATION`
- Human evaluation: `NOT_CONDUCTED`
- Human-study reason: `OUT_OF_SCOPE_FOR_PERSONAL_PROJECT`
- Participant count: `0`
- Product Prompt/Schema/model changed after freeze: `false`
- Model output cherry-picked or regenerated for score improvement: `false`

## Frozen automated results

| Metric | Result |
|---|---:|
| Structured Output Success | 50/50 = 100% |
| Risk Category Accuracy | 25/25 = 100% |
| Diagnosis Category Accuracy | 19/25 = 76% |
| Combined Category Accuracy | 44/50 = 88% |
| Evidence Grounding | 50/50 = 100% |
| Recommendation Grounding | 50/50 = 100% |
| Unsupported Claim | 0/50 observed |
| Serious Hallucination | 0/50 observed |
| Provider Failure | 0/50 |
| Failure Isolation | 4/4 |

`Unsupported Claim` 与 `Serious Hallucination` 是冻结 50-case automated audit 中的观察结果，不是全局比率或人工验证结果。安全审查身份是 `ai_assisted_postfreeze_semantic_audit_not_human`。

Diagnosis Category Accuracy 的 6 个 miss 全部保留。E4 结束时没有修改 Prompt、重新调用模型、挑选输出或删除 miss。

## Allowed conclusions

- 当前结构化输出在冻结 50-case 数据集中成功 50/50。
- 当前 Risk/Diagnosis 分类、grounding 与 recommendation grounding 已在冻结自动数据集上评测。
- timeout、invalid structured output、provider failure 与 provider 429 safe fixture 未改变确定性 verdict，4/4 通过。

## Disallowed conclusions

- 不能声称 LLM 提升工程师效率或降低 triage time。
- 不能声称 LLM 提高真人 root-cause 或 next-action accuracy。
- 不能声称 human utility demonstrated。
- 不能把 0/50 observed serious hallucination 外推为所有输入下 0%。

Human protocol 的逻辑归档入口为 `archived_human_study_protocol/`。没有参与者数据需要保留或分析。
