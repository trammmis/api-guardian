# E4 Pre-human Checkpoint

## Status

`READY_FOR_PROTOCOL_REVIEW`，不是 `READY_FOR_PARTICIPANTS`。

已完成 case selection、独立 Ground Truth freeze、study design、participant-material master、counterbalancing 和 50-case automated evaluation setup。未调用 DeepSeek，未生成 Condition B 文本，未收集或伪造真人数据。

## Case set

- 13 个 human-study case。
- response schema breaking：3。
- request schema breaking：1。
- semantic negative control：1。
- implementation-aligned breaking contract：1。
- documentation-only PASS：1。
- removed operation：1。
- shared component：1。
- mixed change：1。
- oasdiff policy disagreement：1。
- runtime tool ERROR：1。
- contract-tool/OpenAPI load ERROR：1。

难度：easy 4、medium 6、hard 3。

## Frozen Ground Truth

- Case manifest SHA256：`c661f11b70a2ec7b61cc3390d7c17b23f565cc17322a4327024989d37e86b478`
- Ground Truth SHA256：`d481accc96a31ae175048ba8e8175ca37fe917143a29f228ae7df23423c22f4e`
- 冻结时间：`2026-09-14T12:20:50.2848348Z`
- 标签来源不包含 LLM 输出。

## Objective tasks

每个 case：一句话问题判断、root-cause category、next action、0–100 confidence；系统记录 triage time。提交后独立评 clarity/actionability/helpfulness/trust（1–5）。

## Conditions

- Report A：contract changes、scope、regression/tool evidence、deterministic verdict。
- Report B：与 A byte-identical 的 deterministic evidence，再增加 current production `RiskExplanation` / `FailureDiagnosis`。如果 production 对该 case 不产生分析，B 保持与 A 相同。

B 不允许获得 A 没有的原始事实。生成后必须用 canonical JSON hash 验证共享区一致。

## Counterbalancing

10 个预生成 sequence。每个 case 在 10 个 sequence 中恰好分配 A 5 次、B 5 次；case 顺序循环移位。参与者永远不会看到同一 case 的两个条件。

## Participants

- 最少 5 名；少于 5 名只能标 pilot/underpowered。
- 理想 8–10 名；届时可增加 participant-level paired bootstrap CI。
- 背景：backend/API/testing/software engineering 基础。
- 仅收集匿名 ID 和经验/熟悉度分级，不收集姓名、邮箱、雇主等无关信息。
- 预计每人约 47–65 分钟；招募时标约 60 分钟。

## Automated setup

- 50 cases：25 Risk + 25 Diagnosis。
- 记录 schema validity、risk/category accuracy、evidence grounding、unsupported claim、serious hallucination 和 recommendation grounding。
- 当前 production：`deepseek-v4-flash`、provider host `api.deepseek.com`、temperature `0`、structured output `function_calling`、thinking disabled。
- Provider 尚未调用，API key 未记录。

## Mandatory gates before collection

1. 用户确认 protocol、招募方式、同意文本和匿名数据保留期限。
2. 单次生成并冻结 Condition B；不 cherry-pick，不人工润色。
3. 复核 production hashes 未变。
4. 验证所有 A/B deterministic blocks 完全一致。
5. 生成实际匿名 participant packets，并确保不包含 Ground Truth/source condition metadata。

## Current conclusion

`human_evaluation_status = pending`。目前不能声称 LLM 有人类效用。

完整性复核：14 个 production Core/LLM 文件前后哈希 `0` 项不一致；13 个 case 在 10 个序列中均为 A=5、B=5；真人响应行数为 0。
