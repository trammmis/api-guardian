# Stage E1 Experiment Log

## Run

- run_id: `guardian-e1-20260903T075056Z-5e2ede67`
- started_at: `2026-09-03T07:50:56.845152+00:00`
- completed_at: `2026-09-03T08:29:58.152518+00:00`
- random_seed: `20260903`
- repetitions: `5`
- warmup_runs: `1`
- production_guardian_core_unchanged_during_run: `true`

## Environment

- guardian_git_commit: `bcc2c8c1303a1eaae9a99dce73c0fe72dc2fc46d`
- guardian_worktree_dirty: `true`
- Python: `3.13.12`
- OS: `Windows 11 AMD64`
- logical_cpu_count: `16`
- oasdiff: `1.28.0`
- oasdiff_archive_sha256: `cef8ec7cdd32ab4ce9c188f9dbb50452a91fa36cc11c8e1dcf340b19282c3ce1`
- Schemathesis: `4.24.3`
- DeepSeek: not used

## Method Decisions

1. Production Guardian Core remained frozen; the runner imports its public adapters/graph without patching their behavior.
2. Full, Oracle and Guardian runs share Head Spec, target variant, Schemathesis version/check/phases/max examples/deterministic mode/request timeout.
3. Warm-up measurements are retained in raw JSONL but excluded from aggregates.
4. Full/Oracle/Guardian order is deterministically rotated by case and repetition to reduce fixed ordering bias.
5. Pilot oasdiff inspection showed several response-side changes were not breaking in the consumer-compatibility direction. A separate breaking type change was added on the same operation for those cases; the runtime fault itself was not changed or removed.
6. The two semantic Negative Controls remain in all aggregate overall-recall calculations.
7. An invalid pilot exposed a Schemathesis 4.24.3 `CanonicalSchema.is_satisfiable` AttributeError when path parameter `example` values were present. E1 removed unnecessary examples from its SUT Specs; Guardian correctly classified the invalid pilot as ERROR, and none of those measurements enter the final run.

## Output Integrity

Dataset and generated Spec SHA256 values are stored in `run_manifest.json`. `raw_results.jsonl` contains every warm-up and measured repetition. No API key, environment dump, target response body or raw tool output is persisted.
