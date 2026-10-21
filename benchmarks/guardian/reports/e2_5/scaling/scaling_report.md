# API Guardian Scaling and Break-even Benchmark

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: validate
- Verification Status: VERIFIED from persisted raw measurements
- Version Label: guardian_e2_5_scaling_v1

## Research Question

At what API surface size, if any, does the current change-aware Guardian pipeline become faster than optimized one-process Full Schemathesis while preserving the frozen execution semantics?

## Experimental Setup

- Run: `guardian-e2-5-scaling-20260906T091555Z-93818c9d`
- Local parameterized FastAPI SUT; no network or authentication
- Sizes: 8, 16, 32, 64, 128 operations
- One warm-up plus 10 measured repetitions per case/system
- Fixed random seed: 20260906
- Resource ceilings: 180.0 s/case and 10800.0 s/run

## Scaling Dataset

S1 changes exactly one operation. S2 changes approximately 5% of operations, rounded to an integer and recorded exactly. Every affected target returns a string while Head requires an integer; the mutation is therefore in scope for `response_schema_conformance`.

## Systems Compared

- Full Schemathesis: one unfiltered Schemathesis process over Head.
- oasdiff + Full Schemathesis: identical Full run plus the frozen contract phase.
- Oracle Scope: Dataset Ground Truth drives the same per-operation Schemathesis adapter.
- API Guardian deterministic: frozen oasdiff, scope selector, LangGraph and per-operation Schemathesis; LLM disabled.

## Fixed Parameters

Schemathesis 4.24.3; `response_schema_conformance`; phases `examples,coverage`; max examples 1; deterministic generation; redirects 0; request timeout 2.0 s. Full and scoped systems differ only in scope/filter plus Guardian's required oasdiff/graph overhead.

## Raw Sample Counts

- Measured: 400
- Warm-up (excluded from aggregates): 40
- Execution errors: 0
- Deterministic reproducibility: 400/400 (100.00%)

## Runtime by API Size

| Scenario | Total | Affected | Full median / p95 | Full+oasdiff median / p95 | Guardian median / p95 | Oracle median / p95 | Paired G-F median | Paired reduction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `s1_one_operation` | 8 | 1 | 3.343 s / 3.460 s | 3.452 s / 3.630 s | 3.379 s / 3.497 s | 3.200 s / 3.867 s | +0.048 s | -1.43% |
| `s2_five_percent` | 8 | 1 | 3.326 s / 3.602 s | 3.435 s / 3.552 s | 3.352 s / 3.399 s | 3.219 s / 3.305 s | +0.028 s | -0.85% |
| `s1_one_operation` | 16 | 1 | 3.573 s / 3.704 s | 3.634 s / 3.699 s | 3.438 s / 3.504 s | 3.242 s / 3.324 s | -0.149 s | 4.09% |
| `s2_five_percent` | 16 | 1 | 3.543 s / 3.700 s | 3.677 s / 3.744 s | 3.446 s / 3.550 s | 3.299 s / 3.365 s | -0.110 s | 3.13% |
| `s1_one_operation` | 32 | 1 | 3.878 s / 3.996 s | 3.995 s / 4.073 s | 3.521 s / 3.645 s | 3.279 s / 3.405 s | -0.378 s | 9.75% |
| `s2_five_percent` | 32 | 2 | 3.941 s / 4.071 s | 4.043 s / 4.117 s | 6.839 s / 6.912 s | 6.576 s / 6.902 s | +2.855 s | -72.45% |
| `s1_one_operation` | 64 | 1 | 4.618 s / 4.834 s | 4.697 s / 4.876 s | 3.641 s / 3.894 s | 3.313 s / 3.513 s | -0.987 s | 21.37% |
| `s2_five_percent` | 64 | 3 | 4.692 s / 4.825 s | 4.733 s / 4.900 s | 10.407 s / 11.240 s | 9.934 s / 10.068 s | +5.660 s | -121.70% |
| `s1_one_operation` | 128 | 1 | 6.012 s / 6.592 s | 6.203 s / 6.601 s | 3.942 s / 4.096 s | 3.298 s / 3.519 s | -2.087 s | 34.86% |
| `s2_five_percent` | 128 | 6 | 6.344 s / 7.310 s | 6.262 s / 6.590 s | 20.349 s / 22.839 s | 19.827 s / 20.744 s | +13.999 s | -226.53% |

## HTTP Requests by API Size

`summary.csv` includes observed Full and Guardian request medians for every scale. Request reduction is computed from SUT counters, not inferred from operation counts.

## Scope Reduction

Scope reduction is exactly `1 - affected_operations / total_operations` for this controlled Dataset. It is reported next to observed HTTP-request and runtime reductions in `summary.csv`.

## Fixed Overhead Analysis

Guardian component timing is intentionally labelled `end_to_end_wrapper_only`: production nodes were not instrumented. Full/Oracle wrapper boundaries expose Schemathesis time, and FullPipeline exposes oasdiff plus Schemathesis time. The reported Guardian fixed-overhead estimate is Guardian median minus Oracle median—an approximate combined oasdiff/LangGraph/normalization/report cost, not a direct internal telemetry measurement. Full fixed overhead is unavailable because no zero-operation control was run.

Descriptive linear fits over the five synthetic scale points give:

- Full S1: intercept 3.188 s, slope 0.022105 s/operation, R² 0.9996.
- Full S2: intercept 3.129 s, slope 0.025010 s/operation, R² 0.9997.
- Guardian S1: intercept 3.358 s, slope 0.004563 s/operation, R² 0.9968.
- Oracle S2 versus affected-operation count: intercept -0.047 s, slope 3.314655 s/selected operation, R² 1.0000.

The near-linear Oracle S2 fit is evidence that one independent Schemathesis process per selected operation dominates the current multi-operation scoped path. These fits are descriptive for this host, not universal performance laws.

## Break-even Analysis

Operational definition: the first of at least two consecutive size points where the Guardian cell median is lower than Full Schemathesis.

| Scenario | Observed | Region starts | Maximum tested |
| --- | --- | ---: | ---: |
| `s1_one_operation` | yes | 16 | 128 |
| `s2_five_percent` | no | N/A | 128 |

Each cell also stores a deterministic 10,000-sample paired-bootstrap 95% percentile interval for Guardian-minus-Full difference and runtime reduction. The operational decision uses observed medians and requires a sustained region; it does not promote a single isolated point.

## Negative Results

Cells with non-positive paired median reduction are retained: s1_one_operation@8 (-1.43%), s2_five_percent@8 (-0.85%), s2_five_percent@32 (-72.45%), s2_five_percent@64 (-121.70%), s2_five_percent@128 (-226.53%). S2 has no sustained break-even through 128 operations; at 128/6 affected, Guardian is -226.53% relative to Full by the paired reduction definition. No production batching, process reuse or graph optimization was introduced after seeing measurements.

## Threats to Validity

Synthetic homogeneous GET operations, localhost transport, no auth, a schema-only oracle, host-specific process startup, one Windows machine, and the fixed low-budget Schemathesis configuration limit generalization. The benchmark isolates scaling behavior; it is not a model of arbitrary production endpoint cost.

## Optimization Opportunities

If Guardian overhead remains dominated by per-operation Schemathesis startup, batching or process reuse is a future product hypothesis. It was not implemented or evaluated in E2.5.

## Reproducibility

`raw_results.jsonl` retains every warm-up and measured record. `run_manifest.json` records commit, individual frozen Core hashes, original E2 raw hashes, Dataset hash, generator version, environment, tool versions, command contracts and resource limits. No API key or environment Secret is recorded.
