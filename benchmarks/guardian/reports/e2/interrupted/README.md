# E2 未完成运行索引

本目录只保存未进入正式聚合的中断运行。每个子目录包含原始 JSONL 与独立 `interruption_audit.md`。

| Run ID | Contract rows | Runtime rows | 排除原因 |
| --- | ---: | ---: | --- |
| `guardian-e2-20260904T044630Z-3293ae43` | 60 | 867 | 跨日期中断，不能维持连续计时条件 |
| `guardian-e2-20260906T065341Z-250e0f58` | 60 | 329 | Benchmark localhost 控制请求误用系统代理 |

正式 E2 结果只来自连续完成的 `guardian-e2-20260906T071336Z-2eacf61c`，未拼接本目录中的任何记录。
