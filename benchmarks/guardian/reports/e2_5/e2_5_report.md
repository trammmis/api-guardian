# API Guardian E2.5 Evaluation

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: validate
- Verification Status: VERIFIED from frozen E2 raw, replayed miss evidence and 440 persisted Scaling measurements
- Version Label: guardian_e2_5_evaluation_v1

## 1. Why E2.5 Was Needed

E2 reduced selected operations by 87.50% and observed HTTP requests by 86.15%, yet its paired median runtime reduction against optimized one-process Full Schemathesis was -0.82%. E2 also reported Contract Dataset scope recall 40/55 (72.73%) and precision 40/110 (36.36%). E2.5 therefore separates scope semantics case by case and scales the API surface without changing Guardian production behavior.

The E1 84.81% wall-clock reduction is not used as the main performance baseline because E1's Full implementation paid one process startup per operation. E2/E2.5 compare Guardian against the more credible one-process Full baseline.

## 2. Contract Miss Analysis

All 15 original Contract/scope misses were replayed with oasdiff and matched the frozen E2 verdict, selected scope, fallback flag and normalized-change count.

### Two Ground Truth meanings

- **ContractAffectedScope**: operations affected in contract meaning, including a Base operation removed from Head.
- **RuntimeExecutableScope**: affected operations that still exist in Head and can actually be selected for Schemathesis.

The original Dataset is unchanged. The second scope is a derived analysis layer.

| Metric | Result |
| --- | ---: |
| Contract Verdict Accuracy | 55/60 (91.67%) |
| ContractImpactRecall | 40/55 (72.73%) |
| ContractImpactPrecision | 40/110 (36.36%) |
| ExecutableRegressionScopeRecall | 40/45 (88.89%) |
| ExecutableRegressionScopePrecision | 40/110 (36.36%) |
| ExecutableScopeReduction (micro) | 280/390 (71.79%) |
| FallbackRate | 10/60 (16.67%) |
| FallbackPrecision | 0/70 (0.00%) |
| RemovedOperationCoverage | 10/10 (100.00%) |
| ComponentChangeRecall / Precision | 10/10 (100.00%) / 10/10 (100.00%) |

### Primary miss taxonomy

| Classification | Cases |
| --- | ---: |
| `oasdiff_ground_truth_disagreement` | 5 |
| `removed_operation_not_executable` | 10 |

- Selector-only true false negatives with executable mapping evidence: 0.
- Ground Truth definition/use mismatch cases: 10.
- Factually proven Dataset Ground Truth errors: 0.
- Removed/non-executable operation cases: 10.
- oasdiff/Ground Truth verdict disagreements: 5.

The ten endpoint/method removals are correctly detected as Contract FAIL, but their affected Base operation is absent from Head. The selector cannot execute it and recall-first fallback selects all seven remaining Head operations. The five `response_status_removed` cases are human-labelled breaking while oasdiff 1.28.0 emits no breaking record under the frozen command. Those five executable-scope misses are upstream tool/Ground Truth disagreements, not isolated selector mapping failures.

### All fallback-to-all costs

| Case | Head ops | Executable oracle | Selected | Extra | Precision |
| --- | ---: | ---: | ---: | ---: | ---: |
| booking_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| booking_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| inventory_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| inventory_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| orders_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| orders_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| products_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| products_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| users_api_endpoint_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |
| users_api_method_removed | 7 | 0 | 7 | 7 | 0/7 (0.00%) |

Across the ten fallback cases, Guardian selected 70 extra operations. Using the frozen E2 observation of 9 requests per independently tested operation gives 630 estimated extra requests. Runtime cost is not estimated because these Contract-only cases were not run and subprocess cost is non-linear.

## 3. Scaling Benchmark

The parameterized local FastAPI SUT was tested at 8, 16, 32, 64 and 128 operations. S1 affects exactly one operation; S2 affects approximately 5%, yielding 1, 1, 2, 3 and 6 operations. Every affected endpoint returns `value` as string while Head requires integer. Each case/system used one excluded warm-up and 10 measured repetitions.

| Scenario | Total | Affected | Scope reduction | Request reduction | Full median / p95 | Full+oasdiff median / p95 | Guardian median / p95 | Oracle median / p95 | Paired G-F | Runtime reduction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `s1_one_operation` | 8 | 1 | 87.50% | 86.15% | 3.343 s / 3.460 s | 3.452 s / 3.630 s | 3.379 s / 3.497 s | 3.200 s / 3.867 s | +0.048 s | -1.43% |
| `s2_five_percent` | 8 | 1 | 87.50% | 86.15% | 3.326 s / 3.602 s | 3.435 s / 3.552 s | 3.352 s / 3.399 s | 3.219 s / 3.305 s | +0.028 s | -0.85% |
| `s1_one_operation` | 16 | 1 | 93.75% | 93.02% | 3.573 s / 3.704 s | 3.634 s / 3.699 s | 3.438 s / 3.504 s | 3.242 s / 3.324 s | -0.149 s | 4.09% |
| `s2_five_percent` | 16 | 1 | 93.75% | 93.02% | 3.543 s / 3.700 s | 3.677 s / 3.744 s | 3.446 s / 3.550 s | 3.299 s / 3.365 s | -0.110 s | 3.13% |
| `s1_one_operation` | 32 | 1 | 96.88% | 96.50% | 3.878 s / 3.996 s | 3.995 s / 4.073 s | 3.521 s / 3.645 s | 3.279 s / 3.405 s | -0.378 s | 9.75% |
| `s2_five_percent` | 32 | 2 | 93.75% | 93.00% | 3.941 s / 4.071 s | 4.043 s / 4.117 s | 6.839 s / 6.912 s | 6.576 s / 6.902 s | +2.855 s | -72.45% |
| `s1_one_operation` | 64 | 1 | 98.44% | 98.25% | 4.618 s / 4.834 s | 4.697 s / 4.876 s | 3.641 s / 3.894 s | 3.313 s / 3.513 s | -0.987 s | 21.37% |
| `s2_five_percent` | 64 | 3 | 95.31% | 94.74% | 4.692 s / 4.825 s | 4.733 s / 4.900 s | 10.407 s / 11.240 s | 9.934 s / 10.068 s | +5.660 s | -121.70% |
| `s1_one_operation` | 128 | 1 | 99.22% | 99.12% | 6.012 s / 6.592 s | 6.203 s / 6.601 s | 3.942 s / 4.096 s | 3.298 s / 3.519 s | -2.087 s | 34.86% |
| `s2_five_percent` | 128 | 6 | 95.31% | 94.73% | 6.344 s / 7.310 s | 6.262 s / 6.590 s | 20.349 s / 22.839 s | 19.827 s / 20.744 s | +13.999 s | -226.53% |

The run contains 440 raw rows: 400 measured and 40 warm-up. Execution errors: 0. Deterministic reproducibility: 400/400 (100.00%).

## 4. Break-even Result

Break-even is operationally defined as the first of at least two consecutive scale points where Guardian cell median is lower than Full.

- **S1 / exactly one affected operation:** observed region starts at 16 operations and is first confirmed through 32; Guardian remains faster at the measured 64 and 128 points. This is an observed scale point, not an interpolated exact crossing.
- **S2 / approximately 5% affected:** no break-even observed up to 128 operations. The 16-operation faster cell is isolated; from 32 operations onward, multiple independent scoped processes dominate.

At 128 operations, S1 reduces requests from 1025 to 9 and has paired median runtime reduction 34.86%. S2 reduces requests from 1025 to 54 but has paired median runtime reduction -226.53%; fewer requests do not compensate for six sequential Schemathesis process startups.

The measured Oracle S2 medians are close to proportional to affected-operation count, while Full uses one process. The detailed Scaling report records descriptive linear fits and R² values. This supports process startup as the main current multi-operation bottleneck, although exact fixed Full startup remains unavailable without a zero-operation control.

## 5. What API Guardian Actually Improves

- E2 detects all 40/40 mutations declared detectable by the current schema oracle; overall recall is 40/50 because ten semantic negative controls remain undetected.
- For exact small change scopes, Guardian sharply reduces operations and HTTP requests.
- With exactly one affected operation, the measured latency benefit grows with API size and a sustained faster region begins at 16 operations on this host.
- Guardian unifies oasdiff contract facts, scoped runtime testing and deterministic verdicts; those integration benefits are distinct from oasdiff's detection and Schemathesis's generation capabilities.

## 6. What API Guardian Does NOT Improve

- It does not guarantee lower wall-clock latency for every change ratio. The S2 path is slower from 32 through 128 operations under the current per-operation subprocess strategy.
- It does not detect arbitrary business-semantic bugs: E2 overall mutation recall remains 40/50 (80.00%).
- Contract verdict accuracy is not perfect: five response-status cases disagree with oasdiff 1.28.0.
- Recall-first fallback can be expensive: ten removal cases select all remaining Head operations with 0/70 fallback precision.
- This synthetic localhost study does not establish real-world production performance or generalize across hardware.

## 7. Threats to Validity

The SUT is synthetic, homogeneous GET-only, localhost, unauthenticated and uses a schema-oriented oracle. Timings include Windows process startup and are hardware-specific. Only Schemathesis 4.24.3 with `examples,coverage`, max examples 1 and deterministic generation was measured. Affected operations are deliberately the first stable operations, endpoint work is trivial, and 256 operations was not run because the predeclared maximum was 128. Paired bootstrap intervals quantify sampling uncertainty but do not remove these design limitations.

## 8. Next Evaluation Recommendation

Proceed to E3 only as a separate frozen-Core real-world validation, prioritizing Contract + Scope pairs before runtime claims. In parallel, record a future product experiment for batching/process reuse because per-operation Schemathesis startup is the largest observed runtime bottleneck. That optimization must be implemented and evaluated in a new phase, never retroactively mixed into E2/E2.5.

The current evidence supports **change-aware request/operation reduction and conditional latency improvement for very small affected scopes**. It does not support “Guardian is always faster,” “scope mapping is perfect,” or “Guardian detects arbitrary API business regressions.”

### Reproducibility and integrity

- Scaling run: `guardian-e2-5-scaling-20260906T091555Z-93818c9d`
- Frozen E2 run: `guardian-e2-20260906T071336Z-2eacf61c`
- Production Core individual SHA256 values matched preflight after both analyses.
- Original E2 raw and Contract raw SHA256 values matched preflight.
- Full raw, manifest, JSON/CSV summaries and detailed Markdown reports are retained under `benchmarks/guardian/reports/e2_5/`.
