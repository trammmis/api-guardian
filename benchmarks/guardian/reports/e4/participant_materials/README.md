# Participant Materials — Pre-release

当前状态：`NOT_READY_FOR_PARTICIPANTS`。

这些文件已经固定题目、确定性证据、作答字段和 counterbalancing，但 Condition B 的 production LLM 分析尚未生成。不要把 master case cards 或 Ground Truth 发给参与者。

发布前步骤：

1. 用户确认 `E4_PRE_HUMAN_CHECKPOINT.md`。
2. 使用冻结 production configuration 生成一次 Condition B 结构化分析。
3. 验证 A/B 的 deterministic evidence 完全相同。
4. 将 `analysis_status=pending_generation` 替换为经过 Pydantic 校验的 `RiskExplanation` / `FailureDiagnosis`；不得人工润色或选择性重跑。
5. 生成按 `assignment_matrix.csv` 拆分的匿名 participant packets。
6. 从 participant packet 中排除 Ground Truth、source case ID 和 condition provenance。

文件：

- `participant_instructions.md`：参与者说明草案。
- `response_form.md`：单 case 作答表。
- `assignment_matrix.csv`：10 个预生成平衡序列。
- `case_cards.jsonl`：只含安全 deterministic evidence 的 master 数据；不是参与者直接文件。

