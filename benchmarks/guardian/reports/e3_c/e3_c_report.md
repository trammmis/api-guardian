# API Guardian E3-C Real-world Runtime Validation

## 1. Goal

This stage asked whether the frozen, P1-integrated production Guardian can execute real historical Supabase Auth implementations end to end on localhost: contract diff, scope selection, Schemathesis regression, and deterministic final verdict.

Final label: **PARTIALLY_VALIDATED**.

The label is deliberately narrow. Both exact historical services started and both production graphs completed reproducibly, but one of the two affected operations ended in a Schemathesis execution `ERROR`, so runtime conformance was conclusive for only one case.

## 2. Scope and Limitations

- One real repository: `supabase/auth`.
- Two immutable historical pairs selected by the E3-C0 feasibility gate.
- One warm-up and five measured repetitions per case/system.
- No seeded mutation and no change to third-party source or OpenAPI.
- No real credentials and no public production endpoint.
- Results are descriptive and cannot be generalized to all real-world APIs.

## 3. Environment

- OS: Windows 11 `10.0.22621`, AMD64.
- Python: `3.13.12`.
- Logical CPUs: `16`.
- Docker Server: `29.7.2`.
- PostgreSQL: `16`, fresh container for every system run.
- oasdiff: `1.28.0`.
- Schemathesis: `4.24.3`.
- Guardian Git commit at run time: `bcc2c8c1303a1eaae9a99dce73c0fe72dc2fc46d`.

Schemathesis parameters were identical across runtime systems: `response_schema_conformance`, phases `examples,coverage`, `max_examples=1`, deterministic generation, request timeout `2 s`, redirects `0`.

The Go binaries and PostgreSQL image were already available; build/download cost was excluded from scan latency. Each individual measurement did include the production system's actual oasdiff/graph/adapter overhead where applicable.

## 4. Cases

| Case | Head commit | Frozen executable operation | Head operation count |
|---|---|---|---:|
| `rw_supabase_005_authorize_flow_type_removed` | `f4c3cdb020e6808333ec460470f9e081f0d9bc93` | `GET /authorize` | 39 |
| `runtime_supabase_006_id_token_request_fields_removed` | `264696734fc922c8bbf563d44fcc59b26e7e6537` | `POST /token` | 38 |

Case 006 remains runtime-only and does not alter E3-B Ground Truth or metrics.

## 5. Spec / Runtime Alignment

Before the run, each Base Spec, Head Spec, and exact-commit binary was checked against its frozen SHA256. No invalid-alignment run entered the raw results. All service targets were dynamic `127.0.0.1` ports.

## 6. System Comparison

| Case | System | Contract | Regression | Final | Ops | Median requests | Median runtime | P95 runtime |
|---|---|---|---|---|---:|---:|---:|---:|
| 005 | Full Schemathesis | n/a | ERROR | ERROR | 39 | 1431 | 16.883 s | 17.139 s |
| 005 | oasdiff + Full | PASS | ERROR | ERROR | 39 | 1431 | 17.169 s | 17.437 s |
| 005 | Oracle Scope | n/a | PASS | PASS | 1 | 66 | 11.374 s | 11.505 s |
| 005 | API Guardian | PASS | PASS | PASS | 1 | 66 | 11.779 s | 11.875 s |
| 006 | Full Schemathesis | n/a | ERROR | ERROR | 38 | 1260 | 16.909 s | 19.913 s |
| 006 | oasdiff + Full | PASS | ERROR | ERROR | 38 | 1260 | 16.560 s | 16.969 s |
| 006 | Oracle Scope | n/a | ERROR | ERROR | 1 | 82 | 5.527 s | 5.599 s |
| 006 | API Guardian | PASS | ERROR | ERROR | 1 | 82 | 5.991 s | 6.195 s |

“Ops” is the count of distinct operation labels observed in Schemathesis scenarios, not an inferred count from exit code.

## 7. Supabase 005

Production Guardian selected only `GET /authorize`. Its five runtime results were all `PASS`; redirects remained disabled, so a redirect was not followed to an external provider.

The contract verdict was `PASS`, reflecting the already frozen E3-B oasdiff/Ground Truth disagreement for the removed authorize flow type. The E3-C result does not overwrite the E3-B breaking label.

Full-Spec execution ended in `ERROR` because unrelated operations produced execution errors. It therefore cannot serve as a clean PASS/FAIL oracle for `GET /authorize`; the frozen Oracle Scope run does, and it agreed with Guardian.

## 8. Supabase 006

Production Guardian selected only `POST /token`. Both Oracle Scope and Guardian produced `ERROR` in all five measured runs due to a structured Schemathesis `AttributeError` in the Examples phase. No schema-conformance failure was observed, and an HTTP 400 was not treated as a failure by status alone.

This is a stable error classification, not proof of a Supabase runtime defect.

## 9. Runtime Verdict Correctness

- End-to-end production graph completion: `10/10 = 100%` measured runs.
- Guardian/Oracle scoped regression-verdict agreement: `2/2 = 100%` cases.
- Conclusive affected-operation runtime verdict: `1/2 = 50%` cases (`GET /authorize` PASS).
- Correctly isolated tool execution ERROR: `1/2` cases (`POST /token`).
- Observed schema-conformance FAIL: `0` cases.

No Bug Detection Recall is reported because no bug was seeded and no natural defect was independently confirmed.

## 10. Scope and Request Reduction

| Case | Full ops | Guardian ops | Ops reduction | Full requests | Guardian requests | Request reduction |
|---|---:|---:|---:|---:|---:|---:|
| 005 | 39 | 1 | 97.44% | 1431 | 66 | 95.39% |
| 006 | 38 | 1 | 97.37% | 1260 | 82 | 93.49% |

Frozen executable-scope recall and precision were both `2/2 = 100%`: each production run selected exactly the one frozen operation.

## 11. Timing

| Case | Full median / P95 | Guardian median / P95 | Median reduction | Paired descriptive difference |
|---|---|---|---:|---:|
| 005 | 16.883 / 17.139 s | 11.779 / 11.875 s | 30.23% | Guardian 5.103 s faster |
| 006 | 16.909 / 19.913 s | 5.991 / 6.195 s | 64.57% | Guardian 10.918 s faster |

These reductions are specific to two Supabase cases. The Full runs also encountered errors in unrelated operations, so timing is not a pure request-work comparison.

Environment lifecycle cost was measured separately and excluded from scan wall-clock:

| Case | Fresh PostgreSQL setup median | Service startup median | Reset median |
|---|---:|---:|---:|
| 005 | 2.029 s | 0.541 s | 0.520 s |
| 006 | 2.035 s | 0.541 s | 0.525 s |

## 12. Deterministic Reproducibility

- Guardian selected-scope agreement: `10/10` measured runs.
- Guardian contract/regression/final tuple agreement: `10/10` measured runs.
- Case-level reproducibility: `2/2 = 100%`.
- Case 005 tuple: `PASS/PASS/PASS`.
- Case 006 tuple: `PASS/ERROR/ERROR`.

All per-run records, including the Case 006 errors and the Case 006 Full timing outlier, remain in `raw_runs.jsonl`.

## 13. Production Graph / Checkpoint Validation

A separate non-performance correctness run used the real `GuardianService`, production graph, real adapters, official Alembic migration, business tables, and `AsyncPostgresSaver` against an isolated PostgreSQL 16 container.

- Scan status: `completed`.
- `GuardianScan` rows: `1`.
- `GuardianReportRecord` rows: `1`.
- Checkpoint rows for the Guardian thread: `12`.
- Report leakage matches for Secret/raw artifact/body/absolute workspace path: `0`.

This run used Case 005 and returned `PASS/PASS/PASS`. It was not included in performance aggregates.

## 14. Unexpected Runtime Findings

1. Full-Spec runs were not clean regression baselines: unrelated authenticated or redirecting operations yielded `AttributeError` and, in Case 006, `TooManyRedirects` evidence despite redirects being disabled. The parser correctly elevated these to `ERROR`.
2. `POST /token` itself reproducibly triggered Schemathesis `AttributeError` in Examples phase under 4.24.3. That prevented a conclusive schema-conformance verdict.
3. Case 005 retained the known oasdiff 1.28.0 false-negative/policy disagreement: frozen Ground Truth is breaking while production contract verdict is PASS.

No raw response body, request body, header, raw NDJSON, or real credential was persisted.

## 15. Threats to Validity

- Only one repository and two pairs were evaluated.
- Both services ran on localhost with simplified local configuration and no real authentication flow.
- Full and scoped runs exercise different sets of operations; unrelated Full-Spec errors affect both verdict and timing.
- Schemathesis 4.24.3 execution errors limit runtime correctness conclusions for Case 006.
- Five measured repetitions describe this host only; they do not establish cross-platform performance.
- Response-schema conformance does not detect arbitrary business semantics.

## 16. Conclusions

Current evidence supports a narrow claim: the production Guardian can start exact historical Supabase Auth builds locally, preserve exact scope for two frozen affected operations, execute the real LangGraph/adapters, persist one real scan/checkpoint, and produce deterministic PASS or ERROR classifications. It also reduced executed scope and observed HTTP requests substantially in these two cases.

Current evidence does **not** support claims that Guardian validates real-world APIs generally, always produces conclusive PASS/FAIL, always outperforms Full Schemathesis, detects arbitrary runtime bugs, or resolves known oasdiff compatibility-policy disagreements.

## 17. Reproducibility

- Formal run ID: `e3-c-20260914T112607Z-6867d86b`.
- Raw records: `raw_runs.jsonl` (`48` total: `8` warm-up, `40` measured).
- Machine/config manifest: `environment.json` and `experiment_manifest.json`.
- Case summaries: `case_results.csv` and `summary.json`.
- Correctness persistence record: `persistence_checkpoint_result.json`.
- Production Core SHA256 values before and after were identical; see `experiment_manifest.json`.
