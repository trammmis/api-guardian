# E3-C Case: Supabase 006

## Repository and revisions

- Repository: `supabase/auth`
- Base commit: `80fd43270fdbcb79e7a2e0b5bce484bd29d9ab49`
- Head commit: `264696734fc922c8bbf563d44fcc59b26e7e6537`
- Base Spec SHA256: `144a34ca6d74b3e50f7adedd46e94688e45755e8d518beb6be723587dc4a1e3f`
- Head Spec SHA256: `42a6d0897c39a510aba491349bfb92b47b2182be10080e073dea6ef68f732946`
- Head binary SHA256: `9c3c39be1c0e4ac0974004e3813cb2a532d31178d6e2781c6ef33da5826fea71`
- Frozen affected executable operation: `POST /token`

This is a runtime-only candidate and was not added to or scored in the E3-B headline dataset.

## Environment and alignment

The exact Head binary and OpenAPI artifact passed frozen SHA256 checks. Every system run used a fresh local PostgreSQL 16 container and an exact-commit service on `127.0.0.1`. No real password, token, client secret, or API key was supplied.

## Observed results

| System | Contract | Runtime | Final | Operations observed | Median HTTP requests | Median scan time | P95 scan time |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full Schemathesis | n/a | ERROR | ERROR | 38 | 1260 | 16.909 s | 19.913 s |
| oasdiff + Full | PASS | ERROR | ERROR | 38 | 1260 | 16.560 s | 16.969 s |
| Oracle scope | n/a | ERROR | ERROR | 1 | 82 | 5.527 s | 5.599 s |
| Production Guardian | PASS | ERROR | ERROR | 1 | 82 | 5.991 s | 6.195 s |

All five measured production runs selected exactly `POST /token`. Oracle and Guardian both classified the run as `ERROR` in 5/5 repetitions.

The structured NDJSON evidence contained a Schemathesis `AttributeError` during the `Examples` phase. There was no whitelisted `response_schema_conformance` failure evidence. Consequently, the correct deterministic result is infrastructure/tool execution `ERROR`, not API regression `FAIL`. A normal local HTTP 400 was not mechanically treated as a failure.

## Scope and cost

- Operations reduction: `1 - 1/38 = 97.37%`.
- HTTP request reduction: `1 - 82/1260 = 93.49%`.
- Median end-to-end runtime reduction versus Full: `64.57%` (`10.918 s` faster).
- P95 runtime reduction versus Full: `68.89%`.

## Interpretation

The production pipeline executed and classified the tool error reproducibly, but it did not obtain a conclusive PASS/FAIL observation for `POST /token`. Therefore no natural Spec/runtime mismatch is claimed for this case.
