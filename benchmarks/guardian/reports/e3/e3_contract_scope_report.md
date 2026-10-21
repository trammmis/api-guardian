# API Guardian E3-B Real-world Contract and Scope Validation

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: run + validate
- Origin Date: 2026-09-07T11:55:50.470987+00:00
- Verification Status: VERIFIED
- Version Label: guardian_e3_b_system_evaluation_v1
- Ground Truth: human-confirmed v2 `7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94`

## 1. Research Questions

E3-B tests real-world contract verdict and scope mapping on immutable Git history. Runtime, Schemathesis and LLM are outside this stage.

## 2. Repository Selection

Four public repositories contributed 20 pairs; 19 confirmed/probable pairs enter headline metrics.

## 3. Dataset Construction

Ground Truth was reviewed and frozen before System Output. ContractAffectedScope includes removed Base operations; RuntimeExecutableScope contains only affected operations still present in Head. Change-category rows are non-exclusive.

## 4. Contract Detection

- ContractVerdictAccuracy: 14/19 (73.68%)
- BreakingChangeRecall: 7/9 (77.78%)
- BreakingChangePrecision: 7/7 (100.00%)
- SystemExecutionSuccessRate: 15/19 (78.95%)
- SystemErrorRate: 4/19 (21.05%)

## 5. Scope Mapping

- ContractImpactRecall: 58/76 (76.32%)
- ContractImpactPrecision: 58/1690 (3.43%)
- ExecutableRegressionScopeRecall: 58/73 (79.45%)
- ExecutableRegressionScopePrecision: 58/1690 (3.43%)
- FallbackRate: 3/19 (15.79%)
- FallbackPrecision: 27/1659 (1.63%)
- MeanExecutableScopeReduction: 15.848929/19 (83.42%)
- MeanExecutableScopeReduction (breaking cases only): 5.848929/9 (64.99%)
- RemovedOperationCoverage: 3/3 (100.00%)
- ComponentChangeRecall: 57/72 (79.17%)
- ComponentChangePrecision: 57/1450 (3.93%)

The end-to-end scope figures above retain all headline Cases, including tool ERROR. Because a pre-scope ERROR selects zero operations, it must not be interpreted as successful scope reduction. The following supplemental metrics isolate runs that reached scope selection:

- Successful-pipeline ContractImpactRecall: 58/61 (95.08%)
- Successful-pipeline ContractImpactPrecision: 58/1690 (3.43%)
- Successful-pipeline ExecutableScopeRecall: 58/58 (100.00%)
- Successful-pipeline ExecutableScopePrecision: 58/1690 (3.43%)
- Successful-pipeline FallbackRate: 3/15 (20.00%)
- Successful-pipeline MeanExecutableScopeReduction: 11.848929/15 (78.99%)
- Successful-pipeline ComponentChangeRecall: 57/57 (100.00%)
- Successful-pipeline ComponentChangePrecision: 57/1450 (3.93%)

### By Repository

| Repo | Pairs | Breaking | Accuracy | Impact recall | Impact precision | Executable recall | Executable precision | Fallback rate |
|---|---:|---:|---|---|---|---|---|---|
| appwrite/specs | 3 | 2 | 3/3 (100.00%) | 27/29 (93.10%) | 27/1420 (1.90%) | 27/27 (100.00%) | 27/1420 (1.90%) | 2/3 (66.67%) |
| immich-app/immich | 7 | 3 | 7/7 (100.00%) | 25/26 (96.15%) | 25/264 (9.47%) | 25/25 (100.00%) | 25/264 (9.47%) | 1/7 (14.29%) |
| netbirdio/netbird | 4 | 2 | 4/4 (100.00%) | 5/5 (100.00%) | 5/5 (100.00%) | 5/5 (100.00%) | 5/5 (100.00%) | 0/4 (0.00%) |
| supabase/auth | 5 | 2 | 0/5 (0.00%) | 1/16 (6.25%) | 1/1 (100.00%) | 1/16 (6.25%) | 1/1 (100.00%) | 0/5 (0.00%) |

### By Change Category

| Category | Cases | Breaking | Accuracy | Executable recall | Executable precision | Fallback rate |
|---|---:|---:|---|---|---|---|
| direct_operation | 5 | 2 | 3/5 (60.00%) | 5/5 (100.00%) | 5/787 (0.64%) | 1/5 (20.00%) |
| documentation_only | 3 | 0 | 3/3 (100.00%) | 0/0 (N/A) | 0/0 (N/A) | 0/3 (0.00%) |
| mixed | 4 | 4 | 3/4 (75.00%) | 31/46 (67.39%) | 31/1424 (2.18%) | 2/4 (50.00%) |
| removed_operation | 3 | 3 | 3/3 (100.00%) | 27/27 (100.00%) | 27/1659 (1.63%) | 3/3 (100.00%) |
| request_schema | 6 | 3 | 4/6 (66.67%) | 6/6 (100.00%) | 6/6 (100.00%) | 0/6 (0.00%) |
| response_schema | 9 | 6 | 7/9 (77.78%) | 56/71 (78.87%) | 56/1449 (3.86%) | 2/9 (22.22%) |
| shared_component | 13 | 7 | 11/13 (84.62%) | 57/72 (79.17%) | 57/1450 (3.93%) | 2/13 (15.38%) |

## 6. Real-world Misses

| Classification | Records |
|---|---:|
| `COMPONENT_MAPPING_LIMITATION` | 2 |
| `GROUND_TRUTH_AMBIGUOUS` | 1 |
| `GUARDIAN_SCOPE_OVER_SELECTION` | 3 |
| `OASDIFF_FALSE_NEGATIVE` | 1 |
| `OTHER` | 4 |
| `REMOVED_OPERATION_NON_EXECUTABLE` | 3 |

| Case | Classification | Evidence-based likely cause |
|---|---|---|
| rw_immich_005_server_theme_endpoint_removed | `REMOVED_OPERATION_NON_EXECUTABLE` | The affected Base operation was removed from Head and cannot appear in Head executable scope. |
| rw_immich_005_server_theme_endpoint_removed | `GUARDIAN_SCOPE_OVER_SELECTION` | Guardian selected operations outside frozen RuntimeExecutableScope. |
| rw_supabase_001_oauth_grants_endpoints_added | `OTHER` | oasdiff 1.28.0 could not load the Supabase Base Spec because #/components/responses/UnauthorizedResponse was not represented as the schema reference shape expected by oasdiff. |
| rw_supabase_002_oauth_client_update_endpoint_added | `OTHER` | oasdiff 1.28.0 could not load the Supabase Base Spec because #/components/responses/UnauthorizedResponse was not represented as the schema reference shape expected by oasdiff. |
| rw_supabase_003_oauth_client_optional_fields_added | `OTHER` | oasdiff 1.28.0 could not load the Supabase Base Spec because #/components/responses/UnauthorizedResponse was not represented as the schema reference shape expected by oasdiff. |
| rw_supabase_004_webauthn_schema_rework | `OTHER` | oasdiff 1.28.0 could not load the Supabase Base Spec because #/components/responses/UnauthorizedResponse was not represented as the schema reference shape expected by oasdiff. |
| rw_supabase_005_authorize_flow_type_removed | `OASDIFF_FALSE_NEGATIVE` | The independent Ground Truth is breaking, but oasdiff 1.28.0 produced no ERROR-level basis for FAIL. |
| rw_netbird_001_provider_validation_422_ambiguous | `GROUND_TRUTH_AMBIGUOUS` | Independent pre-system review intentionally did not force a compatibility verdict. |
| rw_appwrite_002_migration_cutover_replaced | `REMOVED_OPERATION_NON_EXECUTABLE` | The affected Base operation was removed from Head and cannot appear in Head executable scope. |
| rw_appwrite_002_migration_cutover_replaced | `GUARDIAN_SCOPE_OVER_SELECTION` | Guardian selected operations outside frozen RuntimeExecutableScope. |
| rw_appwrite_002_migration_cutover_replaced | `COMPONENT_MAPPING_LIMITATION` | An unscoped shared-component change triggered recall-first fallback-to-all. |
| rw_appwrite_003_usage_project_and_health_update | `REMOVED_OPERATION_NON_EXECUTABLE` | The affected Base operation was removed from Head and cannot appear in Head executable scope. |
| rw_appwrite_003_usage_project_and_health_update | `GUARDIAN_SCOPE_OVER_SELECTION` | Guardian selected operations outside frozen RuntimeExecutableScope. |
| rw_appwrite_003_usage_project_and_health_update | `COMPONENT_MAPPING_LIMITATION` | An unscoped shared-component change triggered recall-first fallback-to-all. |

Complete Ground Truth, safe oasdiff Adapter output and Guardian scope evidence are stored in `real_world_misses.jsonl`.

### Tool ERROR diagnostics

The primary run is unchanged. A separate non-headline diagnostic replay was used only to recover the exit code and sanitized load error that the production Adapter intentionally does not expose as raw stderr.

| Case | Exit code | Sanitized cause |
|---|---:|---|
| rw_supabase_001_oauth_grants_endpoints_added | 102 | oasdiff could not load Base #/components/responses/UnauthorizedResponse (expected a schema reference object) |
| rw_supabase_002_oauth_client_update_endpoint_added | 102 | oasdiff could not load Base #/components/responses/UnauthorizedResponse (expected a schema reference object) |
| rw_supabase_003_oauth_client_optional_fields_added | 102 | oasdiff could not load Base #/components/responses/UnauthorizedResponse (expected a schema reference object) |
| rw_supabase_004_webauthn_schema_rework | 102 | oasdiff could not load Base #/components/responses/UnauthorizedResponse (expected a schema reference object) |

## 7. Synthetic vs Real-world Comparison

- Removed-operation fallback-to-all reappeared in 3 case(s): ['rw_appwrite_002_migration_cutover_replaced', 'rw_appwrite_003_usage_project_and_health_update', 'rw_immich_005_server_theme_endpoint_removed'].
- Shared-component fallback/over-selection appeared in 2 case(s): ['rw_appwrite_002_migration_cutover_replaced', 'rw_appwrite_003_usage_project_and_health_update'].
- True executable selector false negatives: 0 case(s): none.
- oasdiff/Ground Truth disagreements: 1 case(s): ['rw_supabase_005_authorize_flow_type_removed'].
- Mixed-change executable precision: 31/1424 (2.18%). This is descriptive and is not pooled with E2/E2.5.

## 8. Optional Runtime Validation

`runtime_validation_status = not_run_e3_b_contract_scope_only`. No Supabase pair is yet confirmed suitable. `rw_supabase_005_authorize_flow_type_removed` remains plausible pending service startup and exact version/Spec alignment; candidates 001 and 003 are currently blocked by the E3-B oasdiff Spec-load error.

## 9. Threats to Validity

- Four repositories and 19 headline pairs do not represent all OpenAPI ecosystems.
- Ground Truth is AI-assisted with a human-confirmed amendment, not a multi-reviewer consensus dataset.
- Adjacent generated Specs can contain mixed changes and generator churn.
- Guardian rejects external refs and artifacts above 5 MiB, which creates selection bias.
- This stage validates static contract/scope behavior only; it gives no real-world runtime-regression result.

## 10. Conclusions

Supported claims are limited to the measured contract verdict, scope mapping, fallback behavior and tool/Ground Truth disagreements. The data do not establish universal coverage, production readiness, runtime bug recall, wall-clock superiority or LLM utility.

## 11. Reproducibility

- oasdiff: `oasdiff version 1.28.0`
- Guardian commit: `bcc2c8c1303a1eaae9a99dce73c0fe72dc2fc46d`
- v1 aggregate: `3f59cb7e2ad535cfced0941a2847b87f402ef36b25bdc84cd5fa251471be9f36`
- v2 aggregate: `7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94`
- Specs were materialized from exact commits through raw.githubusercontent.com; no production API target was called.
- Schemathesis and LLM were not executed.
