# E3-C0 Real-world Runtime Feasibility

## 1. Goal

E3-C0 asks one gate question: are there at least two immutable, locally runnable, safely testable real-world historical OpenAPI pairs that justify a formal E3-C runtime validation?

This stage did not run a Guardian scan, a Schemathesis regression suite, or any public production API. It did not modify Guardian production code, P1 behavior, Ground Truth, or tool policy.

## 2. Candidates

Seven pairs were audited. Six came from frozen E3-B records; one additional Supabase Auth pair was registered as a separate runtime-only candidate and is not retroactively part of E3-B.

| Classification | Count | Cases |
|---|---:|---|
| `RUNTIME_FEASIBLE` | 2 | Supabase 005; Supabase runtime candidate 006 |
| `BLOCKED_AUTH` | 4 | Immich 004/006; NetBird 004/005 |
| `BLOCKED_SPEC_RUNTIME_ALIGNMENT` | 1 | Appwrite 002 |
| `BLOCKED_STARTUP` | 0 | — |

The detailed evidence is in `candidate_runtime_matrix.csv` and `startup_smoke_results.jsonl`.

## 3. Environment

- OS: Microsoft Windows 11 Home China, 10.0.22621.
- CPU: Intel Core i7-11800H, 16 logical processors.
- RAM: 16,893,382,656 bytes.
- Python: 3.13.12.
- Docker Server: 29.7.2.
- Guardian commit: `bcc2c8c1303a1eaae9a99dce73c0fe72dc2fc46d`.
- Schemathesis: 4.24.3.
- Frozen production oasdiff: 1.28.0.
- Exact historical Go builds: official Go 1.20.14 Windows toolchain.

Disk protection passed before downloads: both checked drives had more than 5 GiB free. No large third-party image set was pulled. The existing `postgres:16` image was reused. All E3-C0 temporary containers were removed, and the user's pre-existing `api-guardian-phase4-postgres` container was preserved.

## 4. Supabase 005

`rw_supabase_005_authorize_flow_type_removed` is feasible.

- Base: `19545601676675a923bb9850499717e89cf91a0b`.
- Head: `f4c3cdb020e6808333ec460470f9e081f0d9bc93`.
- Affected executable operation: `GET /authorize`.
- Head Spec hash matched the frozen E3-B hash byte-for-byte.
- Exact Head source built and started with PostgreSQL 16.
- `/health` returned 200.
- The affected handler was reached without a real credential.
- A controlled provider smoke returned 302; redirect following was disabled, so no external provider request occurred.
- Reset used a fresh `--rm` PostgreSQL container and service restart.

The complete 11-question audit is in `supabase_005_feasibility.md`.

## 5. Startup Results

### Feasible pair 1: Supabase 005

The exact Head implementation started successfully. Source, binary version, and Spec were aligned to the same full commit SHA. Startup was visible within the 10-second polling window. No production credential or public Supabase endpoint was used.

### Feasible pair 2: Supabase runtime candidate 006

This additional pair uses adjacent spec-changing commits:

- Base: `80fd43270fdbcb79e7a2e0b5bce484bd29d9ab49`.
- Head: `264696734fc922c8bbf563d44fcc59b26e7e6537`.
- Change: the Head removes request properties `client_id` and `issuer` from `POST /token`.
- Base/Head Spec hashes: `144a34ca…1e3f` / `42a6d089…2946`.
- Affected executable operation: `POST /token`.

The exact Head source built successfully. Its source-tree OpenAPI hash equals the independently downloaded Head hash. Schemathesis 4.24.3 loaded the self-contained, zero-external-ref 38-operation document. A feasibility-only oasdiff 1.28.0 invocation loaded both specs and emitted two `request-property-removed` records for `POST /token`; this observation is not an E3-B or E3-C metric.

With a fresh PostgreSQL 16 container, `/health` returned 200. A local `POST /token?grant_type=password` without any real credential returned a normal 400 validation/auth-flow response. No external service was contacted, no response body was persisted, and reset succeeded.

## 6. Authentication Constraints

Authentication is the dominant blocker outside Supabase:

- Immich 004: `POST /tags` requires bearer, cookie, or API-key authentication.
- Immich 006: all 24 audited affected executable operations require one of those credentials.
- NetBird 004: all four affected operations require BearerAuth or TokenAuth.
- NetBird 005: `GET /api/reverse-proxies/clusters` requires BearerAuth or TokenAuth.

Guardian V1 does not accept user-provided runtime Authorization headers, so these cases remain `BLOCKED_AUTH`. No auth bypass or project modification was attempted.

Supabase's Spec declares API-key security, but the exact affected route registrations are not protected by bearer-auth middleware and the service accepts local requests without real user credentials. This is why those pairs pass the gate without changing Guardian.

## 7. Spec / Runtime Alignment

Both feasible pairs have commit-level alignment:

| Pair | Head commit | Alignment evidence |
|---|---|---|
| Supabase 005 | `f4c3cdb020e6808333ec460470f9e081f0d9bc93` | codeload source, embedded binary version, source OpenAPI, frozen Head hash |
| Supabase runtime candidate 006 | `264696734fc922c8bbf563d44fcc59b26e7e6537` | codeload source, embedded binary version, source OpenAPI, downloaded Head hash |

Appwrite 002 failed this gate. `appwrite/specs` contains the contract artifact, not the runnable product. No auditable mapping from Spec commit `7e76b048…` to a fixed `appwrite/appwrite` implementation commit was established, and the affected endpoint also requires Project and Key security.

## 8. Resource Cost

The feasibility work added approximately:

- 114.27 MB compressed Go toolchain;
- 258.99 MB extracted Go toolchain;
- 939.57 MB shared Go module/build cache;
- about 35 MB per Supabase source/binary workspace.

The two feasible cases reuse the same toolchain and existing `postgres:16` image. Each smoke run needs one short-lived PostgreSQL container and one host process. Startup/reset is on the order of seconds, making 2-pair formal validation acceptable on this machine.

An official Dockerfile build was attempted once but stopped before build because the configured `docker.m.daocloud.io` mirror returned HTTP 401. This did not block feasibility because exact-commit local Go builds succeeded. Docker configuration was not changed.

## 9. Feasible Cases

| Case | Repo | Head commit | Operation | Start | Reset | User auth |
|---|---|---|---|---|---|---|
| `rw_supabase_005_authorize_flow_type_removed` | `supabase/auth` | `f4c3cdb020e6808333ec460470f9e081f0d9bc93` | `GET /authorize` | exact Go binary + local PostgreSQL 16 | stop process; recreate `--rm` DB container | none |
| `runtime_supabase_006_id_token_request_fields_removed` | `supabase/auth` | `264696734fc922c8bbf563d44fcc59b26e7e6537` | `POST /token` | exact Go binary + local PostgreSQL 16 | stop process; recreate `--rm` DB container | none |

Neither case is required to produce a runtime FAIL. Formal E3-C must preserve naturally observed PASS/FAIL/ERROR results.

## 10. Blocked Cases

| Case | Primary blocker | Secondary constraint |
|---|---|---|
| Immich 004 | `BLOCKED_AUTH` | historical multi-service stack |
| Immich 006 | `BLOCKED_AUTH` | 24-operation authenticated surface; multi-service stack |
| NetBird 004 | `BLOCKED_AUTH` | identity, public-domain, and network-service startup complexity |
| NetBird 005 | `BLOCKED_AUTH` | multi-service startup complexity |
| Appwrite 002 | `BLOCKED_SPEC_RUNTIME_ALIGNMENT` | Project + Key auth and separate product repository |

No candidate is classified `BLOCKED_STARTUP`, because static gates were applied before expensive startup attempts. That count must not be read as evidence that Immich or NetBird are easy to start.

## 11. Risks

1. Both feasible pairs come from the same Go service and share infrastructure. The minimum pair count is met, but repository diversity is not.
2. The formal Schemathesis runs may reveal response statuses or redirects not fully described by historical specs. Those results must be retained, not treated as feasibility failures after the fact.
3. `/authorize` can construct an external-provider redirect. Formal E3-C must keep `--max-redirects 0` and local target enforcement.
4. The additional candidate 006 is a runtime-only case and must remain separate from frozen E3-B Ground Truth and metrics.
5. Windows host timings and cached Go dependencies are environment-specific; E3-C should report setup and run timing separately.

## 12. Recommendation

**GO_E3_C**, narrowly scoped to the two Supabase Auth pairs.

The explicit threshold was at least two high-quality real-world pairs; both satisfy fixed commits, exact Spec/runtime alignment, localhost-only startup, health, reset, no real credentials, no paid cloud, and at least one affected executable operation.

This is a go decision for a small feasibility-backed validation, not for a broad multi-repository runtime benchmark. If E3-C requires cross-repository external-validity claims, the correct decision is still no-go until another repository yields an anonymous, aligned case.

## Production integrity

The P1-integrated production Guardian files have identical preflight and final SHA256 values. The E3-B Ground Truth v2 aggregate remains `7791a45bc20103f86ecfc099591035a476bfb414c3db08b4f6201284cd4ddb94`; its file and review-record hashes are unchanged. P1 Integration artifacts are unchanged. No public production API was called.
