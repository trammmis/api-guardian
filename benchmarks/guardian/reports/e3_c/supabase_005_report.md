# E3-C Case: Supabase 005

## Repository and revisions

- Repository: `supabase/auth`
- Base commit: `19545601676675a923bb9850499717e89cf91a0b`
- Head commit: `f4c3cdb020e6808333ec460470f9e081f0d9bc93`
- Base Spec SHA256: `c93e8e4ad65515e50b3babfc99cc07d5694249420e04858a64120c268a9238f4`
- Head Spec SHA256: `2aee71c180222191e49fb3df2edea8820c0bb49ab1909f20fe9b408cc731e6b6`
- Head binary SHA256: `920a366640cd742a0743f5423e16f79403bc8a3c3cc5a4f6decc7c38ce5fe4a7`
- Frozen affected executable operation: `GET /authorize`

## Environment and alignment

The exact Head source binary and Head OpenAPI artifact passed their frozen SHA256 checks before every experiment. The service and a fresh PostgreSQL 16 container ran on dynamic `127.0.0.1` ports. Redirects were disabled and no real credentials were used.

## Observed results

| System | Contract | Runtime | Final | Operations observed | Median HTTP requests | Median scan time | P95 scan time |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full Schemathesis | n/a | ERROR | ERROR | 39 | 1431 | 16.883 s | 17.139 s |
| oasdiff + Full | PASS | ERROR | ERROR | 39 | 1431 | 17.169 s | 17.437 s |
| Oracle scope | n/a | PASS | PASS | 1 | 66 | 11.374 s | 11.505 s |
| Production Guardian | PASS | PASS | PASS | 1 | 66 | 11.779 s | 11.875 s |

All five measured Guardian repetitions selected exactly `GET /authorize` and returned `PASS/PASS/PASS`. Oracle-scope runs agreed 5/5.

The frozen E3-B Ground Truth labels this contract pair breaking. Production oasdiff 1.28.0 still returned no ERROR-level breaking basis, so the observed Guardian contract verdict remained `PASS`. E3-C does not revise that frozen E3-B disagreement.

The Full runs returned `ERROR` in all repetitions because other operations in the complete API surface produced Schemathesis execution errors (safe categories included `AttributeError`). This is not evidence that `GET /authorize` failed schema conformance.

## Scope and cost

- Operations reduction: `1 - 1/39 = 97.44%`.
- HTTP request reduction: `1 - 66/1431 = 95.39%`.
- Median end-to-end runtime reduction versus Full: `30.23%` (`5.103 s` faster).
- P95 runtime reduction versus Full: `30.71%`.

## Interpretation

The affected route was executable and schema-conformant under the frozen check. The scoped run isolated that fact from unrelated Full-Spec execution errors. The result does not prove the historical contract change was compatible; that remains the known oasdiff/Ground Truth disagreement.
