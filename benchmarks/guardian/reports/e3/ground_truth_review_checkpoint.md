# API Guardian E3-B Ground Truth Freeze Checkpoint

> 状态：Pre-system review 已冻结；尚未运行 oasdiff、Guardian、Schemathesis、LLM 或准确率指标。

## Material Passport

- 审查者：`ai_assisted_initial_review`
- 证据来源：固定 40-char Git commit、Base/Head OpenAPI、Git commit message；未读取 Guardian 输出。
- Spec 获取：本地 partial clone 提供 commit/tree 元数据；由于 Git blob 回填连接被重置，artifact 通过 raw.githubusercontent.com 的固定 commit URL 只读获取。
- 冻结时间：`2026-09-07T06:14:24.132296+00:00`
- Review aggregate SHA256：`3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- 人工确认状态：尚未 human-confirmed，等待用户审查本检查点。

## Counts

- Total: 20
- Confirmed: 17
- Probable: 2
- Ambiguous: 1
- Excluded: 0
- Headline eligible before human confirmation: 19

## Cases

| Repo | Case ID | Base | Head | Change types | Status | Confidence | Verdict | Contract ops | Executable ops |
|---|---|---|---|---|---|---|---|---:|---:|
| immich-app/immich | rw_immich_001_cluster_group_regenerate_added | 351be9576996 | c7d24321bf30 | endpoint_added | confirmed | high | pass | 0 | 0 |
| immich-app/immich | rw_immich_002_api_key_response_fields_added | 7165eb61ebc1 | 47c5a3dbf6d3 | response_property_added, shared_component_changed | confirmed | high | pass | 0 | 0 |
| immich-app/immich | rw_immich_003_album_user_description_only | cd308ad93093 | 4c2d2284aa51 | documentation_only | confirmed | high | pass | 0 | 0 |
| immich-app/immich | rw_immich_004_tag_name_pattern_narrowed | 2feb889b9cdb | 1b3aa9cd512a | request_schema_changed, shared_component_changed | confirmed | high | fail | 1 | 1 |
| immich-app/immich | rw_immich_005_server_theme_endpoint_removed | 7d181f0686aa | 2f8be45fe0fc | endpoint_removed | confirmed | high | fail | 1 | 0 |
| immich-app/immich | rw_immich_006_asset_face_response_removed | a278c10c7594 | 38438c8d9a4b | response_property_removed, response_schema_changed, shared_component_changed | confirmed | high | fail | 24 | 24 |
| immich-app/immich | rw_immich_007_upload_duration_maximum_widened | d788169bf333 | a278c10c7594 | request_schema_changed, shared_component_changed | confirmed | high | pass | 0 | 0 |
| supabase/auth | rw_supabase_001_oauth_grants_endpoints_added | 6296a5a226b3 | cc640b277989 | endpoint_added | confirmed | high | pass | 0 | 0 |
| supabase/auth | rw_supabase_002_oauth_client_update_endpoint_added | cf39a8ae2cc3 | 6296a5a226b3 | endpoint_added | confirmed | high | pass | 0 | 0 |
| supabase/auth | rw_supabase_003_oauth_client_optional_fields_added | 68cb8d2ba3de | cf39a8ae2cc3 | request_schema_changed, response_property_added, shared_component_changed | confirmed | high | pass | 0 | 0 |
| supabase/auth | rw_supabase_004_webauthn_schema_rework | 1f804a279501 | 68cb8d2ba3de | response_schema_changed, response_property_removed, shared_component_changed, mixed_change | confirmed | high | fail | 15 | 15 |
| supabase/auth | rw_supabase_005_authorize_flow_type_removed | 195456016766 | f4c3cdb020e6 | request_parameter_removed | confirmed | high | fail | 1 | 1 |
| netbirdio/netbird | rw_netbird_001_provider_validation_422_ambiguous | ebc259e30b42 | 6aaeed744e8c | response_status_added, documentation_only, mixed_change | ambiguous | medium | ambiguous | 2 | 2 |
| netbirdio/netbird | rw_netbird_002_bundle_parameters_fields_added | 5e88d3f87afd | 1081ca006d46 | request_schema_changed, response_property_added, shared_component_changed | confirmed | high | pass | 0 | 0 |
| netbirdio/netbird | rw_netbird_003_policy_ports_documentation_only | ebfdf7d7b8c6 | 58c09ead211d | documentation_only, shared_component_changed | confirmed | high | pass | 0 | 0 |
| netbirdio/netbird | rw_netbird_004_agent_network_settings_rework | bc7a15ab71d9 | ebfdf7d7b8c6 | request_schema_changed, response_property_removed, shared_component_changed, endpoint_added, mixed_change | confirmed | high | fail | 5 | 5 |
| netbirdio/netbird | rw_netbird_005_proxy_cluster_response_reworked | 07cbfdbedec2 | d250f92c435b | response_property_removed, response_property_added, shared_component_changed | confirmed | high | fail | 1 | 1 |
| appwrite/specs | rw_appwrite_001_empty_array_examples_preserved | 698f5834d566 | 6124ab004f41 | documentation_only, shared_component_changed | confirmed | high | pass | 0 | 0 |
| appwrite/specs | rw_appwrite_002_migration_cutover_replaced | 0aff819e4744 | 7e76b0486252 | endpoint_removed, endpoint_added, response_schema_changed, shared_component_changed, mixed_change | probable | medium | fail | 2 | 1 |
| appwrite/specs | rw_appwrite_003_usage_project_and_health_update | 8c00ba356b01 | 7d74bf9f3c0c | endpoint_removed, response_schema_changed, shared_component_changed, mixed_change | probable | medium | fail | 27 | 26 |

## Freeze Boundary

`ground_truth_freeze_manifest.json` 已记录每个 review 文件 SHA256。所有 System Output 目标文件在冻结时不存在。
在用户确认前，本阶段不得运行 Guardian accuracy metric。
