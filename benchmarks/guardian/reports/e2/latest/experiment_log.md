# Stage E2 Experiment Log

## Run

- run_id: `guardian-e2-20260906T071336Z-2eacf61c`
- started_at: `2026-09-06T07:13:36.222214+00:00`
- completed_at: `2026-09-06T08:17:46.351399+00:00`
- random_seed: `20260903`
- measured repetitions: `5`
- warm-up repetitions: `1`
- production Guardian Core unchanged: `true`

## Reproducibility Environment

- Git commit: `bcc2c8c1303a1eaae9a99dce73c0fe72dc2fc46d`
- Worktree dirty at run start: `true`
- Python: `3.13.12`
- OS: `Windows 11 AMD64`
- CPU logical count: `16`
- oasdiff: `1.28.0`
- Schemathesis: `4.24.3`

## Method Decisions

1. E2 uses one unfiltered Schemathesis process for each Full baseline measurement. This removes E1's repeated process-startup amplification and is intentionally documented rather than rewriting E1.
2. Guardian and Oracle Scope keep the production per-operation strategy. In this Dataset each Runtime Case has one Ground Truth operation.
3. Five clean controls were added beyond the required 50 mutations so runtime false positives have a real denominator.
4. Contract response-status removal remains human-labelled breaking. If oasdiff disagrees, the mismatch is reported and not changed after observation.
5. Semantic negative controls remain in Overall Mutation Recall even though the configured schema oracle cannot detect them.

## Output Files

- `contract_raw_results.jsonl`: 60 per-case Contract measurements.
- `raw_results.jsonl`: all warm-up and measured Runtime executions.
- `summary.json` and `summary.csv`: aggregate values derived from raw data.
- `missed_cases.json`: every missed Runtime bug.
- `contract_scope_misses.json`: every Contract verdict or scope mismatch.
- `run_manifest.json`: hashes, versions, command contracts and environment.
- `benchmark_report.md`: RQ-oriented report generated from summary data.
- `metadata_correction_audit.md`: audit trail for the corrected full oasdiff archive SHA256.
- `../interrupted/`: incomplete runs retained for audit and excluded from aggregation.
