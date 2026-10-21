# E2 中断运行审计记录

- Run ID：`guardian-e2-20260906T065341Z-250e0f58`
- Contract 原始记录：60 行（完整）
- Runtime 原始记录：329 行（13 个完整案例，另有 1 个部分案例）
- 聚合状态：未聚合，明确排除出正式 E2 结果
- 中断位置：读取本地 SUT 的 `__benchmark__/metrics` 控制端点
- 直接异常：`httpx.ConnectError: All connection attempts failed`
- 根因证据：异常栈进入 `httpcore._async.http_proxy`；Benchmark 控制客户端错误继承了系统代理
- 处理：控制客户端设置 `trust_env=False`；不修改 Guardian production Core、数据集或指标定义
- 数据保留：`raw_results.jsonl` 与 `contract_raw_results.jsonl` 均原样归档

本运行不能作为性能样本，因为运行未完成。修复 Harness 后从头连续重跑，避免混合不同时间段或不同 Harness 条件下的计时。
