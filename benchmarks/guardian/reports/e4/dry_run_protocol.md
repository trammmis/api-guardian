# E4 Dry-run Protocol

## Status

`READY_NOT_STARTED`。Dry run 不属于正式参与者收集。

## Participant and exclusion

- 使用 1 名非正式参与者，分配独立匿名 dry-run ID。
- 所有记录必须标记 `DRY_RUN_EXCLUDED`。
- 该数据永远不进入 `human_results`、primary analysis 或正式 participant count。

## Procedure

1. 展示 consent 草案和研究说明，确认没有暗示某种报告形式更先进。
2. 依照一个完整 13-report sequence 操作 Study UI。
3. 每题验证：完整报告出现时记录 T0；提交问题判断、固定 root-cause option、固定 next-action option 与 0–100 confidence 时记录 T1。
4. 验证 objective submit 后才出现四项 1–5 subjective rating，且其时间不计入 TriageTimeSeconds。
5. 验证不能回看同一 Case 的另一呈现形式，也不能返回修改已提交的 objective answer。
6. 完成后记录总耗时、疲劳感和任何中断，但不评价答案正确性。

## Checklist

- UI 顺序与按钮状态正确。
- timer 使用单调时钟或服务端可信时间；刷新/断线行为明确。
- packet 不含 Ground Truth、expected answer、source stage、difficulty、condition provenance 或生成来源信息。
- researcher-only mapping 不暴露给参与者。
- 无 raw tool output、Secret、路径或隐藏字段泄漏。
- 13 题在约 47–65 分钟内可完成；若明显超时，只记录发现，不改冻结 Case。
- submission flow 能产生匿名、完整、可审计记录。

## Stop rule

Dry run 发现 timer、顺序、泄漏或提交完整性问题时，正式收集保持禁止。只允许修复 Study UI/呈现基础设施；不得修改冻结 Case、Ground Truth、Prompt、Schema、模型或报告事实。
