# Case

`rw_netbird_004_agent_network_settings_rework`

## Material Passport

- 阶段：E3-B pre-system human spot check
- 证据：固定 Base/Head OpenAPI、相邻 Spec commit、commit message、fragment-local `$ref` 闭包
- 冻结记录：`reviews/pre_system/rw_netbird_004_agent_network_settings_rework.json`
- Ground Truth aggregate SHA256：`3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- 本 packet 没有使用任何 system evaluation 结果

## Repository

- Repository：`netbirdio/netbird`
- OpenAPI：`shared/management/http/api/openapi.yml`

## Base / Head Commit

- Base：`bc7a15ab71d9e9ed944fa3ec17952e77a696595d`
- Head：`ebfdf7d7b8c6b475c66cd7790d2f2f2a5e9275e4`

## Commit Message

`[management] Rework Agent Network endpoint identity and settings bootstrap (#7085)`

## Relevant OpenAPI Diff

```diff
 AgentNetworkProviderRequest.properties:
-  bootstrap_cluster: {type: string, ...}

 AgentNetworkSettings.required:
-  [cluster, subdomain, endpoint, enable_log_collection,
-   enable_prompt_collection, redact_pii]
+  [endpoint, proxy_address, dedicated, enable_log_collection,
+   enable_prompt_collection, redact_pii]

 AgentNetworkSettings.properties:
-  cluster: ...
-  subdomain: ...
+  proxy_address: ...
+  dedicated: ...

 AgentNetworkSettingsRequest.required:
-  [enable_log_collection, enable_prompt_collection, redact_pii]
+  [endpoint, proxy_address, enable_log_collection,
+   enable_prompt_collection, redact_pii, access_log_retention_days]

 AgentNetworkSettingsRequest.properties:
-  cluster: ...
+  endpoint: ...
+  proxy_address: ...
```

同一 Head commit 还新增 `POST` 和 `DELETE /api/agent-network/settings`。

## Plain-language Change

旧版本通过 `cluster` 和 `subdomain` 表示 Agent Network 地址，并允许 provider 请求携带 `bootstrap_cluster`。新版本改为 `endpoint`、`proxy_address` 和 `dedicated`，删除旧字段，同时要求 settings update 提供更多必填字段。

## Compatibility Reasoning

冻结标签是 **FAIL**。

它同时包含三类不兼容变化：删除已有请求字段、删除并替换已有响应字段、给已有 update request 增加新的必填字段。旧客户端即使 method/path 不变，也可能发送不再被声明的字段，或收不到原来依赖的字段。

## ContractAffectedScope Evidence

1. `POST /api/agent-network/providers`：request 引用 `AgentNetworkProviderRequest`。
2. `PUT /api/agent-network/providers/{providerId}`：request 引用 `AgentNetworkProviderRequest`。
3. `GET /api/agent-network/settings`：response 引用 `AgentNetworkSettings`。
4. `POST /api/agent-network/settings`：Head 新增 operation，request/response 使用新的 settings components。
5. `PUT /api/agent-network/settings`：request 引用 `AgentNetworkSettingsRequest`，response 引用 `AgentNetworkSettings`。

`DELETE /api/agent-network/settings` 虽然也是新增 operation，但没有通过 request/response schema 引用上述 breaking components，所以冻结 scope 未包含它。

## RuntimeExecutableScope Evidence

以上 5 个 operation 在 Head Spec 中全部存在，因此 RuntimeExecutableScope 同样包含这 5 个 operation。

本 Case 没有被删除的 affected operation。

## Frozen Label

| Field | Frozen value |
|---|---|
| status | `confirmed` |
| confidence | `high` |
| verdict | `fail` |
| contract ops | `5` |
| executable ops | `5` |

## Reviewer Questions

- **A. Agree**：同意冻结标签与 5 个 operation 的 scope。
- **B. Disagree**：不同意；请指出具体 component 或 operation。
- **C. Unsure**：需要进一步检查该 mixed commit 的 component 使用方向。

最值得人工检查的疑点：新增加的 `POST /api/agent-network/settings` 是否应计入“受 breaking change 影响的 ContractAffectedScope”。它确实使用新 settings component，但在 Base 中不存在，因而不会破坏该 path 的旧调用方。

