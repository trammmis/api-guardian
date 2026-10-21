# API Guardian Scaling and Break-even Benchmark

## Material Passport

- Origin Skill: experiment-agent
- Origin Mode: validate
- Verification Status: VERIFIED from persisted raw measurements
- Version Label: guardian_e2_5_scaling_v1

## Research Question

At what API surface size, if any, does the current change-aware Guardian pipeline become faster than optimized one-process Full Schemathesis while preserving the frozen execution semantics?

## Experimental Setup

- Run: `guardian-e2-5-scaling-20260906T090958Z-b015c7cc`
- Local parameterized FastAPI SUT; no network or authentication
- Sizes: 8 operations
- One warm-up plus 5 measured repetitions per case/system
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

- Measured: 40
- Warm-up (excluded from aggregates): 8
- Execution errors: 0
- Deterministic reproducibility: 40/40 (100.00%)

## Runtime by API Size

| Scenario | Total | Affected | Full median / p95 | Guardian median / p95 | Oracle median / p95 | Paired G-F median | Paired reduction |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `s1_one_operation` | 8 | 1 | 2.524 s / 2.690 s | 2.383 s / 2.874 s | 2.304 s / 2.501 s | -0.123 s | 4.92% |
| `s2_five_percent` | 8 | 1 | 3.427 s / 5.692 s | 3.656 s / 6.892 s | 3.289 s / 4.855 s | +0.157 s | -4.48% |

## HTTP Requests by API Size

`summary.csv` includes observed Full and Guardian request medians for every scale. Request reduction is computed from SUT counters, not inferred from operation counts.

## Scope Reduction

Scope reduction is exactly `1 - affected_operations / total_operations` for this controlled Dataset. It is reported next to observed HTTP-request and runtime reductions in `summary.csv`.

## Fixed Overhead Analysis

Guardian component timing is intentionally labelled `end_to_end_wrapper_only`: production nodes were not instrumented. Full/Oracle wrapper boundaries expose Schemathesis time, and FullPipeline exposes oasdiff plus Schemathesis time. The reported Guardian fixed-overhead estimate is Guardian median minus Oracle median—an approximate combined oasdiff/LangGraph/normalization/report cost, not a direct internal telemetry measurement. Full fixed overhead is unavailable because no zero-operation control was run.

## Break-even Analysis

Operational definition: the first of at least two consecutive size points where the Guardian cell median is lower than Full Schemathesis.

| Scenario | Observed | Region starts | Maximum tested |
| --- | --- | ---: | ---: |
| `s1_one_operation` | no | N/A | 8 |
| `s2_five_percent` | no | N/A | 8 |

Each cell also stores a deterministic 10,000-sample paired-bootstrap 95% percentile interval for Guardian-minus-Full difference and runtime reduction. The operational decision uses observed medians and requires a sustained region; it does not promote a single isolated point.

## Negative Results

Any cell where Guardian is slower remains in the raw and aggregate outputs. No production batching, process reuse or graph optimization was introduced after seeing measurements.

## Threats to Validity

Synthetic homogeneous GET operations, localhost transport, no auth, a schema-only oracle, host-specific process startup, one Windows machine, and the fixed low-budget Schemathesis configuration limit generalization. The benchmark isolates scaling behavior; it is not a model of arbitrary production endpoint cost.

## Optimization Opportunities

If Guardian overhead remains dominated by per-operation Schemathesis startup, batching or process reuse is a future product hypothesis. It was not implemented or evaluated in E2.5.

## Reproducibility

`raw_results.jsonl` retains every warm-up and measured record. `run_manifest.json` records commit, individual frozen Core hashes, original E2 raw hashes, Dataset hash, generator version, environment, tool versions, command contracts and resource limits. No API key or environment Secret is recorded.
