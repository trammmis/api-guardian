# API Guardian Effectiveness Benchmark

## Experimental Setup

- Stage: E1
- Run ID: `guardian-e1-20260903T075056Z-5e2ede67`
- Cases: 10 (8 schema-detectable, 2 negative controls)
- Controlled SUTs: 2, each with 8 OpenAPI operations
- Measured repetitions per case/system: 5
- Warm-up runs per case/system: 1 (excluded from timing aggregates)
- Random seed: 20260903
- Schemathesis: `4.24.3`
- oasdiff: `1.28.0`
- LLM: disabled; E1 does not evaluate System F

Every raw measurement is in `raw_results.jsonl`; the manifest records Git, Python, OS, CPU, dataset hashes and fixed tool parameters. No API key is recorded.

## Systems Compared

- Baseline A — oasdiff only: contract verdict only.
- Baseline B — Full Schemathesis: all 8 Head operations, production-equivalent fixed test parameters.
- Baseline C — oasdiff + Full Schemathesis: implemented, but repeated timing is intentionally deferred beyond E1.
- Baseline D — Oracle Scope + Schemathesis: one human Ground Truth operation per case.
- System E — API Guardian deterministic: real frozen graph with LLM disabled.
- System F — API Guardian full: not run in E1.

Breaking detection is supplied by oasdiff and runtime schema checking by Schemathesis. The Guardian-specific comparison here concerns change-aware scope, orchestration and deterministic result classification.

## Dataset

The dataset contains 10 fixed mutations across `users_api` and `orders_api`. All cases contain a real injected bug. Eight violate the Head response schema; two deliberately return schema-valid but semantically wrong data. Ground Truth is dataset-authored rather than copied from oasdiff output.

An initial pilot found that response-required additions, response-enum narrowing and `number -> integer` are not necessarily breaking from the client-compatibility direction. Those cases retain their runtime mutation and add a separate, explicit breaking type change on the same operation solely to make the change-aware scope experiment well-defined. This decision is recorded rather than hidden.

## RQ1 Detection Correctness

- Contract verdict accuracy: **10/10 (100.00%)**.
- Guardian in-scope mutation recall: **8/8 (100.00%)**.
- Guardian overall mutation recall, including semantic negative controls: **8/10 (80.00%)**.

The difference between in-scope and overall recall is expected evidence of the current schema-oriented test oracle boundary, not a dataset exclusion.

## RQ2 Regression Efficiency

North Star result for this E1 sample: at in-scope bug recall **100.00%**, mean operation scope reduction is **87.50%** and paired median wall-clock reduction versus Full Schemathesis is **84.81%**.

| Metric | Full Schemathesis | Oracle Scope | API Guardian | Difference (Guardian - Full) |
| --- | ---: | ---: | ---: | ---: |
| Bug Detection Recall | 80.00% | 80.00% | 80.00% | +0.00 pp |
| In-Scope Mutation Recall | 100.00% | 100.00% | 100.00% | +0.00 pp |
| Median runtime (s) | 29.4641 | 4.3898 | 4.5613 | -24.9028 |
| P95 runtime (s) | 35.8763 | 5.3071 | 5.4981 | -30.3782 |
| Median operations tested | 8.0000 | 1.0000 | 1.0000 | -7.0000 |
| Median HTTP requests | 72.0000 | 9.0000 | 9.0000 | -63.0000 |

Guardian timing includes oasdiff and graph orchestration while Full Schemathesis timing does not; this makes the reported Guardian-vs-Full reduction conservative, but Baseline C repeated timing remains needed for a cleaner scope-only estimate in E2.

## RQ3 Scope Quality

- Scope Recall: **10/10 (100.00%)**.
- Scope Precision: **10/10 (100.00%)**.
- Mean Scope Reduction: **87.50%**.
- Mean Oracle Overhead: **0.0000 operations/case**.
- Oracle Efficiency Ratio: **1.0000**.

## RQ4 LLM Diagnosis Utility

Not evaluated in E1. LLM and Human Evaluation belong to E4. No human scores were simulated.

## RQ5 Reliability and Cost

- Deterministic reproducibility across repeated E1 runs: **150/150 (100.00%)**.
- LLM calls/tokens/cost: not applicable because LLM is disabled.
- Error injection and checkpoint recovery benchmark: deferred; existing product tests are rerun only as non-regression gates.

## Limitations

1. E1 has only 2 synthetic controlled SUTs and 10 mutations; it cannot support broad external-validity claims.
2. All business operations are GET endpoints without authentication or stateful workflows.
3. Bug detection precision has no true no-bug controls in E1; its observed value must not be interpreted as a false-positive study.
4. Current test oracle is schema-oriented and does not detect arbitrary business-logic regressions.
5. SUTs run on loopback in the same host; runtime figures primarily measure local process and Schemathesis overhead.
6. HTTP request counts come from SUT-side counters and exclude health/reset/metrics control endpoints.

## Reproducibility

Run from the repository root:

```powershell
uv run python -m benchmarks.guardian.run_benchmark --oasdiff-path <oasdiff-1.28.0.exe>
```

See `run_manifest.json`, `experiment_log.md`, `raw_results.jsonl`, `summary.json`, `summary.csv` and `missed_cases.json`. Generated Head Specs are kept under `datasets/mutation/generated/` for audit.
