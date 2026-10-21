# Contract and Scope Miss Analysis

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: validate
- Verification Status: VERIFIED against frozen E2 raw and replayed oasdiff evidence
- Version Label: guardian_e2_5_miss_analysis_v1

## Original E2 Metrics

- Contract Verdict Accuracy: 55/60 (91.67%)
- Scope Recall: 40/55 (72.73%)
- Scope Precision: 40/110 (36.36%)
- fallback-to-all: 10 cases

## Ground Truth Semantics

The original Dataset remains unchanged. E2.5 derives two views: ContractAffectedScope includes removed Base operations; RuntimeExecutableScope is the subset still present in Head and therefore selectable by Schemathesis. No case is relabelled after seeing Guardian output.

## ContractAffectedScope

- Recall: 40/55 (72.73%)
- Precision: 40/110 (36.36%)

## RuntimeExecutableScope

- Recall: 40/45 (88.89%)
- Precision: 40/110 (36.36%)
- Micro operation reduction: 280/390 (71.79%)
- Mean per-case reduction: 70.00%

## Miss Taxonomy

| Classification | Cases |
| --- | ---: |
| `true_scope_false_negative` | 0 |
| `true_scope_false_positive` | 0 |
| `fallback_to_all_over_selection` | 0 |
| `removed_operation_not_executable` | 10 |
| `contract_vs_runtime_scope_mismatch` | 0 |
| `oasdiff_ground_truth_disagreement` | 5 |
| `component_change_attribution_limitation` | 0 |
| `operation_id_mapping_limitation` | 0 |
| `dataset_ground_truth_error` | 0 |
| `other` | 0 |

## Per-category Counts

All 15 original misses are classified: 10 removed-operation execution mismatches and 5 oasdiff/Ground Truth verdict disagreements. There are zero cases proven to be a selector-only false negative with sufficient executable mapping evidence. Ten removed-operation cases expose a Ground Truth definition/use mismatch, but no human label is proven factually wrong.

## True Guardian Scope Misses

`is_true_guardian_scope_miss = true` for 0 cases. This flag is deliberately narrow: it requires usable oasdiff operation evidence and an executable Head operation that the selector nevertheless omitted.

## Ground Truth / Tool Definition Disagreements

Five `response_status_removed` cases are human-labelled breaking, while oasdiff 1.28.0 emitted no breaking change under the frozen command. The Dataset labels are retained; this is reported as tool/Ground Truth disagreement, not silently changed to PASS.

## Removed Operation Cases

Ten endpoint/method removals were detected as Contract FAIL (10/10 (100.00%)), but the affected operation no longer exists in Head. Exact mapping therefore fails and recall-first fallback selects all seven remaining Head operations. This is both a Contract-vs-Runtime scope mismatch and a real over-selection cost.

## Component Change Cases

- Component Change Recall: 10/10 (100.00%)
- Component Change Precision: 10/10 (100.00%)

All five shared-component cases mapped to the two genuinely affected operations. No component attribution limitation was observed in this controlled Dataset.

## Fallback-to-all Cases

| Case | Head ops | Executable oracle | Selected | Extra ops | Precision | Estimated extra requests |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| booking_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| booking_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| inventory_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| inventory_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| orders_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| orders_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| products_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| products_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| users_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |
| users_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) | 63* |

* Request cost uses the frozen E2 observation of 9 requests per independently tested operation. Runtime cost is unavailable because these Contract cases were not executed and process cost is non-linear.

- Fallback Rate: 10/60 (16.67%)
- Fallback Precision: 0/70 (0.00%)
- FallbackCostOperations: 70
- FallbackCostRequests (estimated): 630
- FallbackCostEstimatedRuntime: unavailable

## Per-case Evidence

| Case | Primary classification | Base ops | Head ops | Contract affected | Runtime executable | Guardian selected | Fallback |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
| booking_api_endpoint_removed | `removed_operation_not_executable` | 8 | 7 | GET /bookings/primary | ∅ | GET /bookings<br>GET /rooms/primary<br>GET /rooms<br>GET /guests/primary<br>GET /payments/latest<br>GET /availability/current<br>GET /status | true |
| booking_api_method_removed | `removed_operation_not_executable` | 8 | 7 | GET /bookings | ∅ | GET /bookings/primary<br>GET /rooms/primary<br>GET /rooms<br>GET /guests/primary<br>GET /payments/latest<br>GET /availability/current<br>GET /status | true |
| booking_api_response_status_removed | `oasdiff_ground_truth_disagreement` | 8 | 8 | GET /rooms/primary | GET /rooms/primary | ∅ | false |
| inventory_api_endpoint_removed | `removed_operation_not_executable` | 8 | 7 | GET /stock/primary | ∅ | GET /stock<br>GET /warehouses/primary<br>GET /warehouses<br>GET /reservations/primary<br>GET /movements/latest<br>GET /suppliers/primary<br>GET /status | true |
| inventory_api_method_removed | `removed_operation_not_executable` | 8 | 7 | GET /stock | ∅ | GET /stock/primary<br>GET /warehouses/primary<br>GET /warehouses<br>GET /reservations/primary<br>GET /movements/latest<br>GET /suppliers/primary<br>GET /status | true |
| inventory_api_response_status_removed | `oasdiff_ground_truth_disagreement` | 8 | 8 | GET /warehouses/primary | GET /warehouses/primary | ∅ | false |
| orders_api_endpoint_removed | `removed_operation_not_executable` | 8 | 7 | GET /orders | ∅ | GET /orders/{order_id}<br>GET /orders/{order_id}/summary<br>GET /customers/{customer_id}/orders<br>GET /invoices/{invoice_id}<br>GET /shipments/{shipment_id}<br>GET /currencies<br>GET /status | true |
| orders_api_method_removed | `removed_operation_not_executable` | 8 | 7 | GET /orders/{order_id} | ∅ | GET /orders<br>GET /orders/{order_id}/summary<br>GET /customers/{customer_id}/orders<br>GET /invoices/{invoice_id}<br>GET /shipments/{shipment_id}<br>GET /currencies<br>GET /status | true |
| orders_api_response_status_removed | `oasdiff_ground_truth_disagreement` | 8 | 8 | GET /orders/{order_id}/summary | GET /orders/{order_id}/summary | ∅ | false |
| products_api_endpoint_removed | `removed_operation_not_executable` | 8 | 7 | GET /products/primary | ∅ | GET /products<br>GET /categories/primary<br>GET /categories<br>GET /brands/primary<br>GET /prices/current<br>GET /reviews/latest<br>GET /status | true |
| products_api_method_removed | `removed_operation_not_executable` | 8 | 7 | GET /products | ∅ | GET /products/primary<br>GET /categories/primary<br>GET /categories<br>GET /brands/primary<br>GET /prices/current<br>GET /reviews/latest<br>GET /status | true |
| products_api_response_status_removed | `oasdiff_ground_truth_disagreement` | 8 | 8 | GET /categories/primary | GET /categories/primary | ∅ | false |
| users_api_endpoint_removed | `removed_operation_not_executable` | 8 | 7 | GET /users | ∅ | GET /users/{user_id}<br>GET /profiles/{user_id}<br>GET /teams<br>GET /teams/{team_id}<br>GET /roles<br>GET /permissions<br>GET /status | true |
| users_api_method_removed | `removed_operation_not_executable` | 8 | 7 | GET /users/{user_id} | ∅ | GET /users<br>GET /profiles/{user_id}<br>GET /teams<br>GET /teams/{team_id}<br>GET /roles<br>GET /permissions<br>GET /status | true |
| users_api_response_status_removed | `oasdiff_ground_truth_disagreement` | 8 | 8 | GET /profiles/{user_id} | GET /profiles/{user_id} | ∅ | false |

Full oasdiff change IDs, paths, operation IDs, primary causes, secondary causes and notes are stored in `miss_analysis.jsonl`.

## Conclusions

The original 72.73%/36.36% Contract-scope result mixes a Base/contract concept with a Head-executable test concept. Splitting the semantics improves executable recall to 40/45 (88.89%), but precision remains 40/110 (36.36%) because removal cases trigger conservative all-Head fallback. The five verdict misses come from oasdiff/Ground Truth disagreement, not from scope mapping. No Dataset label was proven factually wrong; the evaluation use of one scope field for two questions was the modelling limitation.
