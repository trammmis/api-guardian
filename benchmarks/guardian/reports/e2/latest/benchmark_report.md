# API Guardian Effectiveness Benchmark — Stage E2

## Experimental Setup

- Run ID: `guardian-e2-20260906T071336Z-2eacf61c`
- Controlled SUTs: 5
- Runtime cases: 55 = 50 mutations + 5 clean controls
- Detectable mutations: 40
- Semantic negative controls: 10
- Contract cases: 60
- Repetitions: 5 measured + 1 warm-up
- Random seed: 20260903
- oasdiff: `1.28.0`
- Schemathesis: `4.24.3`
- LLM: disabled

The raw Contract and Runtime measurements, input hashes, tool contracts, OS, Python, CPU and Git state are preserved next to this report. No API key is recorded.

## Systems Compared

- Full Schemathesis: one unfiltered CLI invocation over all Head operations.
- oasdiff + Full Schemathesis: the same contract detection followed by the same full-spec invocation.
- Oracle Scope: Dataset Ground Truth selects the regression operations.
- API Guardian deterministic: the frozen production graph with LLM disabled.

E2 corrects an E1 measurement limitation: Full Schemathesis now runs the entire Spec in one process. E1 used the production per-operation adapter for the Full baseline and therefore included repeated process startup overhead. E1 raw data is not rewritten.

## Dataset

Runtime Ground Truth is defined by 55 explicit cases across users, orders, products, inventory and booking APIs. Contract Ground Truth contains 60 cases covering removed endpoints/methods/response fields/statuses, request and response type changes, required parameters, enum narrowing, compatible additions, documentation-only changes and shared component changes.

## RQ1 Detection Correctness

- Contract verdict accuracy: **55/60 (91.67%)**.
- Guardian Bug Detection Recall: **40/50 (80.00%)**.
- Guardian In-Scope Mutation Recall: **40/40 (100.00%)**.
- Guardian Overall Mutation Recall: **40/50 (80.00%)**.
- Guardian Bug Detection Precision: **40/40 (100.00%)**.
- Guardian clean-control false-positive rate: **0/5 (0.00%)**.

## RQ2 Regression Efficiency

At In-Scope Mutation Recall **100.00%**, Runtime Scope Reduction is **87.50%** and median HTTP-request reduction is **86.15%**. Paired median runtime reduction is **-0.82%** versus Full Schemathesis and **2.04%** versus oasdiff + Full Schemathesis.

This is the E2 North Star result: the scoped system preserved all mutations detectable by the configured schema oracle and substantially reduced operations and requests, but it did **not** reduce wall-clock latency versus the optimized one-process Full baseline. Process startup and orchestration overhead dominate these small 8-operation SUTs.

| Metric | Full Schemathesis | oasdiff + Full | Oracle Scope | API Guardian |
| --- | ---: | ---: | ---: | ---: |
| Bug Detection Recall | 80.00% | 80.00% | 80.00% | 80.00% |
| In-Scope Mutation Recall | 100.00% | 100.00% | 100.00% | 100.00% |
| Median runtime (s) | 2.6047 | 2.7053 | 2.5307 | 2.6369 |
| P95 runtime (s) | 3.8470 | 3.9140 | 3.7408 | 3.8632 |
| Median operations tested | 8.0000 | 8.0000 | 1.0000 | 1.0000 |
| Median HTTP requests | 65.0000 | 65.0000 | 9.0000 | 9.0000 |

## RQ3 Scope Quality

Contract Dataset:

- Scope Recall: **40/55 (72.73%)**.
- Scope Precision: **40/110 (36.36%)**.
- Mean Scope Reduction: **70.00%**.
- Mean Oracle Overhead: **1.2000 operations/case**.
- Oracle Efficiency Ratio: **0.5000**.
- fallback-to-all cases: **10**.

Runtime Dataset:

- Scope Recall: **55/55 (100.00%)**.
- Scope Precision: **55/55 (100.00%)**.
- Mean Scope Reduction: **87.50%**.

## RQ4 LLM Diagnosis Utility

Not evaluated in E2. LLM and Human Evaluation remain E4 work; no human scores are simulated.

## RQ5 Reliability and Cost

- Deterministic reproducibility: **1100/1100 (100.00%)**.
- Runtime error records: **0**.
- LLM latency, tokens and cost: not applicable because LLM is disabled.
- Dedicated fault injection and checkpoint reliability experiments remain outside E2.

## Misses and Limitations

- Missed Runtime mutations: **10**; see `missed_cases.json`.
- Contract verdict or scope misses: **15**; see `contract_scope_misses.json`.
- Current test oracle is schema-oriented and does not detect arbitrary business-logic regressions.
- Controlled SUTs are local, unauthenticated and intentionally small; results do not establish real-world external validity.
- Removed operations cannot be executed from the Head Spec and therefore expose a representation gap between Contract affected operations and executable regression scope.
- Full baseline and Guardian share all Schemathesis generation/check parameters; only scope/filtering and unavoidable orchestration differ.

## Reproducibility

Run from the repository root with verified local tools:

```powershell
uv run python -m benchmarks.guardian.run_e2 --oasdiff-path <oasdiff-1.28.0.exe> --schemathesis-path <st-4.24.3.exe>
```

See `run_manifest.json`, `experiment_log.md`, `contract_raw_results.jsonl`, `raw_results.jsonl`, `summary.json`, `summary.csv`, `missed_cases.json` and `contract_scope_misses.json`.
