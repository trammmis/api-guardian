# Supported Claims

以下表述可以用于 README、简历或面试，但必须保留括号中的数据集边界和限制。

1. **受控 schema-detectable mutation recall 为 40/40。** 适用范围：E2 的 5 个本地 SUT、当前 Schemathesis `response_schema_conformance` oracle；不包含任意业务语义错误。

2. **受控数据集 overall mutation recall 为 40/50 = 80%。** 其余 10 个是刻意保留的 schema 正确、业务语义错误 negative controls，说明当前系统不是通用业务逻辑验证器。

3. **E2 的 change-aware scope 将 operation 数减少 87.50%、HTTP request 数减少 86.15%。** 但在优化后的 Full baseline 上 paired median runtime reduction 为 -0.82%，所以不能据此声称小型 API 更快。

4. **Exactly-one affected operation 的 scaling 实验从已测 16-operation 点开始出现持续 faster region，并保持到 128 operations。** 128 operations 时观察到 34.86% runtime reduction；约 5% affected scenario 截至 128 operations 没有持续 break-even。

5. **E3-B 真实 Git-history headline contract accuracy 为 14/19 = 73.68%。** 数据来自 Immich、Supabase Auth、NetBird 和 Appwrite 四个公开仓库；包含 4 个 oasdiff load ERROR 和 1 个 policy disagreement。

6. **E3-B breaking precision 为 7/7 = 100%，breaking recall 为 7/9 = 77.78%。** 仅适用于 19 个冻结、人工式 review 的真实 version pairs，不能推广为所有 OpenAPI 的表现。

7. **P1 production integration 在 E3-B 上保持 executable recall 58/73 不变，并将 executable precision 从 58/1690 = 3.43% 提升到 58/63 = 92.06%。** 该改进针对结构化、可证明的 operation removal；与 oasdiff 1.28.0 rule contract 绑定。

8. **P1 integration 将 E3-B selected operations 从 1690 降至 63、extra operations 从 1632 降至 5、fallback cases 从 3/19 降至 0/19。** Contract accuracy 仍为 14/19，没有通过修改 Ground Truth 获得改善。

9. **E3-C 在两个 immutable Supabase Auth historical pairs 上把 39 和 38 个 full-scope operations 分别缩小到 1 个。** 观察到 HTTP request reduction 分别为 95.39% 和 93.49%；只涉及一个仓库，结论是 narrow/partial validation。

10. **E3-C production graph 在 10/10 measured runs 中完成，Guardian 与 Oracle scoped regression 在 2/2 cases 上一致。** 其中只有 1/2 给出 conclusive runtime verdict，另一个稳定为 Schemathesis execution ERROR。

11. **E4 structured output success 为 50/50，combined category accuracy 为 44/50 = 88%。** 分项为 Risk 25/25、Diagnosis 19/25 = 76%；Diagnosis 的六个 miss 必须一并披露。

12. **E4 frozen automated audit 中 evidence grounding 和 recommendation grounding 均为 50/50。** Unsupported claim 与 serious hallucination 均为 0/50 observed；这是有限自动审计观察，不是普遍零幻觉保证。

13. **LLM failure isolation 为 4/4。** timeout、invalid structured output、provider failure 和模拟 429 均未改变 contract、regression 或 final verdict；只触发分析降级。

14. **Guardian 的门禁结论与 LLM 解耦。** oasdiff、Schemathesis 和 deterministic verdict 产生事实；LLM 只生成 Pydantic 校验后的 RiskExplanation/FailureDiagnosis。

15. **真实 PostgreSQL checkpoint 中断恢复与持久化路径已被集成测试验证。** 这是项目内受控环境结论，不代表任意部署环境均已验证。
