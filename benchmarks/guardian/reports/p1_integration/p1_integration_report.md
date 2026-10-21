# P1 Production Integration

## 1. Motivation

P1 冻结消融表明 pure operation removal 的保守全量回退造成大量 over-selection，且窄化策略未降低 executable recall。本阶段把同一结构化策略最小集成到生产 Selector。

## 2. Frozen Ablation Evidence

- Frozen P1 recall：31/31，100%。
- Frozen P1 precision：31/38，81.58%。
- Frozen Ground Truth v2：`7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94`。

## 3. Production Change

仅修改 `scope.py`、`state.py`、`nodes.py`：State 保留 Base operation 白名单，Selector 使用两个 version-bound removal rule ID 与 Base/Head 结构证明 removal。详细哈希与行为见 `production_diff.md`。

## 4. Safety Conditions

逐 change 解析；唯一 operationId counterpart 优先；removal 不屏蔽其他 mapped/unresolved change；没有文本猜测、component inference 或 LLM。

## 5. Unit Tests

覆盖 exact/path/method/multiple removal、mixed mapped、mixed unresolved、component unresolved、operationId counterpart、non-removal failure、compatible addition、no-change、normal exact mapping，以及完整 Graph empty-scope final verdict。

## 6. P1 Frozen Replay

| Metric | Before | Integrated Production |
|---|---:|---:|
| Recall | 31/31 (100.00%) | 31/31 (100.00%) |
| Precision | 31/1741 (1.78%) | 31/38 (81.58%) |
| Fallback Rate | 20/23 (86.96%) | 2/23 (8.70%) |
| Selected Operations | 1741 | 38 |
| Extra Operations | 1710 | 7 |

Integrated production 与 frozen P1 output：23/23 (100.00%)。

## 7. E2/E2.5 Regression

重跑 60 个冻结 Contract/Scope cases：Contract agreement 60/60 (100.00%)；oasdiff output stability 60/60 (100.00%)。Ground Truth 未修改。

## 8. E3-B Real-world Before / After

| Metric | E3-B Before | Integrated Production |
|---|---:|---:|
| Contract Accuracy | 14/19 (73.68%) | 14/19 (73.68%) |
| Executable Recall | 58/73 (79.45%) | 58/73 (79.45%) |
| Executable Precision | 58/1690 (3.43%) | 58/63 (92.06%) |
| Fallback Rate | 3/19 (15.79%) | 0/19 (0.00%) |
| Fallback Precision | 27/1659 (1.63%) | 0/0 (N/A) |
| Selected Operations | 1690 | 63 |
| Extra Operations | 1632 | 5 |

| Case | Before selected/fallback | After selected/fallback | Oracle executable |
|---|---:|---:|---:|
| rw_immich_005_server_theme_endpoint_removed | 239 / True | 0 / False | 0 |
| rw_appwrite_002_migration_cutover_replaced | 783 / True | 6 / False | 1 |
| rw_appwrite_003_usage_project_and_health_update | 637 / True | 26 / False | 26 |

## 9. Contract Verdict Stability

- P1 frozen：23/23 (100.00%)。
- E2/E2.5：60/60 (100.00%)。
- E3-B：19/19 (100.00%)。
- Contract/final verdict 函数与 Graph topology 未修改。

## 10. Scope Recall Stability

P1 frozen 与 E3-B executable recall 均未下降；P1 integrated false-negative cases：0；E3-B integrated false-negative cases：1。

## 11. Precision / Fallback Improvement

E3-B precision 从 58/1690 (3.43%) 变为 58/63 (92.06%)；extra operations 从 1632 降到 5。这不是 P1 23-case precision 的替代引用，而是 19 条 E3-B headline 的实际重跑结果。

## 12. Empty-scope Graph Semantics

完整 Graph 测试证明 pure removal 产生 Contract FAIL、empty selected scope、Regression PASS、Final FAIL。现有确定性 verdict 语义无需修改。

## 13. Quality Gates

全部通过，详见 `quality_gate.json`：Guardian pytest 112 passed / 2 skipped；P1 policy pytest 10 passed；Ruff 与 format 通过；Pyright 0 errors；deterministic Eval 的 contract、scope recall、regression、final、error classification 均为 1.0；真实 PostgreSQL checkpoint 恢复测试 2/2 通过。

## 14. Remaining Limitations

- Appwrite 002 仍有 5 个额外 operation；P1 不解决 operation-local tool/Ground Truth 归属差异。
- 四个 Supabase Case 的 oasdiff 1.28.0 compatibility ERROR 不受 P1 影响。
- removal rule ID 与 oasdiff 1.28.0 绑定；升级工具必须重验契约。
- P1 不解决 component dependency mapping，也不改变 per-operation Schemathesis 启动开销。

## 15. Recommendation

本次 integration 为 **PASS**。P1-I 只改善 production scope/fallback；是否进入 E3-C Runtime 应独立决定，不应把本次静态 scope 成果当作 runtime validation。
