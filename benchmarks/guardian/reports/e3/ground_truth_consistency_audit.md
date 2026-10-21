# E3-B Ground Truth Consistency Audit

> 状态：proposed v2，等待用户确认。未运行 oasdiff、Guardian、Schemathesis、LLM 或任何 accuracy metric。

## Evidence Boundary

本次审计只使用固定 commit 的 Base OpenAPI、Head OpenAPI 和既有 pre-system review evidence。
原始 v1 freeze 与 v1 review records 均未覆盖或删除。

## Twenty-case Narrow Audit

| Case | Frozen contract ops | New-operation candidates | Result |
|---|---:|---:|---|
| rw_immich_001_cluster_group_regenerate_added | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_immich_002_api_key_response_fields_added | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_immich_003_album_user_description_only | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_immich_004_tag_name_pattern_narrowed | 1 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_immich_005_server_theme_endpoint_removed | 1 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_immich_006_asset_face_response_removed | 24 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_immich_007_upload_duration_maximum_widened | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_supabase_001_oauth_grants_endpoints_added | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_supabase_002_oauth_client_update_endpoint_added | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_supabase_003_oauth_client_optional_fields_added | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_supabase_004_webauthn_schema_rework | 15 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_supabase_005_authorize_flow_type_removed | 1 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_netbird_001_provider_validation_422_ambiguous | 2 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_netbird_002_bundle_parameters_fields_added | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_netbird_003_policy_ports_documentation_only | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_netbird_004_agent_network_settings_rework | 5 | 1 | NEW_OPERATION_SCOPE_OVERINCLUSION |
| rw_netbird_005_proxy_cluster_response_reworked | 1 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_appwrite_001_empty_array_examples_preserved | 0 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_appwrite_002_migration_cutover_replaced | 2 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |
| rw_appwrite_003_usage_project_and_health_update | 27 | 0 | NO_MATCHING_CONSISTENCY_ISSUE |

20 个 Case 中只有 `rw_netbird_004_agent_network_settings_rework` 命中
`NEW_OPERATION_SCOPE_OVERINCLUSION`。

## NetBird 004 Finding

- `POST /api/agent-network/settings`：Base 不存在，Head 新增。
- Head 中该 operation 引用：`AgentNetworkSettings`, `AgentNetworkSettingsCreateRequest`。
- 与冻结 breaking evidence 重合的 component：`AgentNetworkSettings`。
- 因为它是纯新增 operation，不能仅因引用 changed component 就算作既有消费者被破坏的 operation。
- Case 仍为 Contract FAIL：其余四个 operation 在 Base/Head 均存在，且仍由已冻结的 request/response component 变化支持。

### Correct ContractAffectedScope

- `POST /api/agent-network/providers`
- `PUT /api/agent-network/providers/{providerId}`
- `GET /api/agent-network/settings`
- `PUT /api/agent-network/settings`

### Correct RuntimeExecutableScope

- `POST /api/agent-network/providers`
- `PUT /api/agent-network/providers/{providerId}`
- `GET /api/agent-network/settings`
- `PUT /api/agent-network/settings`

## Exact Proposed Amendments

| Case | Field | Old | New |
|---|---|---|---|
| rw_netbird_004_agent_network_settings_rework | `contract_affected_operations` | `["POST /api/agent-network/providers", "PUT /api/agent-network/providers/{providerId}", "GET /api/agent-network/settings", "POST /api/agent-network/settings", "PUT /api/agent-network/settings"]` | `["POST /api/agent-network/providers", "PUT /api/agent-network/providers/{providerId}", "GET /api/agent-network/settings", "PUT /api/agent-network/settings"]` |
| rw_netbird_004_agent_network_settings_rework | `runtime_executable_operations` | `["POST /api/agent-network/providers", "PUT /api/agent-network/providers/{providerId}", "GET /api/agent-network/settings", "POST /api/agent-network/settings", "PUT /api/agent-network/settings"]` | `["POST /api/agent-network/providers", "PUT /api/agent-network/providers/{providerId}", "GET /api/agent-network/settings", "PUT /api/agent-network/settings"]` |
| rw_netbird_004_agent_network_settings_rework | `evidence` | `["The component diff directly removes/renames request and response members.", "Independent transitive fragment-reference review maps the three breaking components to five Head operations.", "POST and DELETE /api/agent-network/settings are additions; only POST is also in scope because it uses changed settings components."]` | `["The component diff directly removes/renames request and response members.", "Independent transitive fragment-reference review maps the breaking components to four operations that exist in both Base and Head.", "POST and DELETE /api/agent-network/settings are Head additions. Under the E3 existing-consumer scope convention, neither new operation is counted as a broken existing operation; POST references AgentNetworkSettings but remains an additive operation."]` |

## Freeze Lineage

- v1 aggregate SHA256: `3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- proposed v2 aggregate SHA256: `7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94`
- Verdict labels changed: 0
- Headline eligible case count changed: 0
- proposed v2 reviewer state: `independent_review_pending_user_confirmation`
