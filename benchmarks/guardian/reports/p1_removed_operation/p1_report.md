# P1 Removed-operation No-fallback Ablation

## Material Passport

- 实验类型：冻结数据集上的确定性产品消融实验
- 被测系统：Frozen Baseline B0 与 benchmark-only P1 policy
- 输入：E2/E2.5 removal cases、E3-B Ground Truth v2 real-world fallback cases、运行前冻结 safety cases
- Ground Truth：E3-B v2 aggregate `7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94`；P1 safety SHA256 `13e7554740024fa63af35df00cf1417cf8b00bcb54f5dfce1eca2bffec972a3a`
- 生产行为：未修改；P1 模块不被生产 Graph 导入
- 未运行：oasdiff、Schemathesis、LLM、E3-C Runtime

## 1. Hypothesis

可由 Base/Head 结构和冻结 removal rule 明确证明的 operation removal 保持 Contract FAIL，但对 Head 的 RuntimeExecutableScope 贡献为空，且该 change 自身不触发 fallback-to-all。

## 2. Frozen Baseline

Baseline 直接调用生产 `select_test_scope`。任何 breaking change 无法映射到 Head operation 时，回退到 Head 全部 operation。Frozen E2/E2.5、E3-B raw 与 Production Core 哈希在运行前后均校验。

## 3. Experimental Policy

P1 逐 change 解析。method/path 或唯一 operationId 能映射时照常选中；只有已知 removal rule、Base 中存在、Head 中同 method/path 不存在、且无唯一 operationId counterpart 时，才判为 `PROVEN_REMOVED_OPERATION`，贡献空执行范围。其他 unresolved change 仍回退全量。

## 4. Safety Conditions

- removal 不改变 Contract verdict；
- mixed scan 中其他可映射 operation 必须保留；
- unresolved non-removal change 必须继续 fallback；
- operationId 能定位 Head counterpart 时优先映射，不能误判为空；
- 本次只识别两个冻结规则 ID，不做模糊文本或 component inference。

## 5. Dataset

- 总 Case：23
- Pure removal：15
- Mixed removal：5
- Non-removal controls：3
- 来源：{"e2_e2_5_frozen": 10, "e3_b_frozen_real_world": 3, "p1_safety": 10}

## 6. Baseline vs P1

| Metric | Frozen Baseline | P1 | Difference |
|---|---:|---:|---:|
| Executable Scope Recall | 31/31 (100.00%) | 31/31 (100.00%) | 0.00 pp |
| Executable Scope Precision | 31/1741 (1.78%) | 31/38 (81.58%) | 79.80 pp |
| Fallback Rate | 20/23 (86.96%) | 2/23 (8.70%) | -78.26 pp |
| Fallback Precision | 30/1740 (1.72%) | 2/4 (50.00%) | 48.28 pp |
| Selected Operations | 1741 | 38 | -1703 |
| Extra Selected Operations | 1710 | 7 | -1703 |
| Contract Verdict Agreement | 23/23 (100.00%) | 23/23 (100.00%) | 0 pp |

空 oracle 的 per-case recall 定义为 1.0；oracle 与 selected 都为空时 per-case precision 定义为 1.0。Micro precision 的分母仍是实际 selected operation 总数。

## 7. Pure Removal Results

15 个 pure-removal Case 的 P1 empty-scope correctness 为 `True`。

## 8. Mixed Removal Results

| Case | Baseline selected / fallback | P1 selected / fallback | Oracle | Extra before | Extra after |
|---|---:|---:|---:|---:|---:|
| rw_immich_005_server_theme_endpoint_removed | 239 / True | 0 / False | 0 | 239 | 0 |
| rw_appwrite_002_migration_cutover_replaced | 783 / True | 6 / False | 1 | 782 | 5 |
| rw_appwrite_003_usage_project_and_health_update | 637 / True | 26 / False | 26 | 611 | 0 |

Appwrite 002/003 均不是 pure removal。P1 只消除 removal 自身的 unresolved 状态，其他 operation-local breaking records 继续决定 scope。

## 9. Non-removal Controls

删除与无法映射 component 并存、纯 non-removal mapping failure、compatible addition、no-change 等控制场景均按冻结期望校验。`removal + unresolved component` 仍 fallback：`True`。

## 10. Recall Regression Check

- Baseline micro recall：31/31 (100.00%)
- P1 micro recall：31/31 (100.00%)
- P1 executable false-negative Case：0
- Mixed removal 保留 oracle scope：True

## 11. Precision / Over-selection Improvement

| Subgroup | Cases | Baseline precision | P1 precision | Extra before | Extra after |
|---|---:|---:|---:|---:|---:|
| mixed_removal | 5 | 30/1425 (2.11%) | 30/36 (83.33%) | 1395 | 6 |
| non_removal_control | 3 | 1/2 (50.00%) | 1/2 (50.00%) | 1 | 1 |
| removed_only | 15 | 0/314 (0.00%) | 0/0 (100.00%) | 314 | 0 |

## 12. Failure Cases

无 acceptance failure。

## 13. Threats to Validity

- 数据集聚焦 removal/fallback，不代表所有 OpenAPI change；
- removal rule ID 绑定 oasdiff 1.28.0 已验证契约；
- E3-B Ground Truth 为 AI-assisted initial review 加用户确认 amendment，并非大规模多人独立标注；
- 本实验只评价 scope semantics，不包含 Schemathesis 运行时间或实际 HTTP 请求；
- safety cases 是小型、人工构造的结构控制，用于验证边界而非估计真实分布。

## 14. Recommendation

**BENEFICIAL**

Acceptance checks：

- contract_verdict_unchanged: True
- extra_operations_reduced: True
- frozen_safety_expectations_pass: True
- mixed_removal_retains_oracle_scope: True
- no_p1_executable_false_negatives: True
- pure_removal_empty_scope_correct: True
- recall_not_decreased: True
- unresolved_non_removal_still_fallback: True

该结论只支持进入独立的 P1 production integration phase；本实验没有修改生产 Selector。

## Quality Gate

- Guardian + P1 pytest：99 passed，12 skipped，2 个第三方依赖 deprecation warnings。
- Ruff check：PASS。
- Ruff format check：PASS（20 files already formatted）。
- Pyright：0 errors，0 warnings。
- Production Core 与冻结 E2/E2.5/E3 artifact：运行前后 SHA256 一致。
