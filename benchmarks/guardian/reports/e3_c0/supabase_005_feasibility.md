# Supabase 005 Runtime Feasibility

## Case identity

- Case: `rw_supabase_005_authorize_flow_type_removed`
- Repository: `supabase/auth` (MIT)
- Base commit: `19545601676675a923bb9850499717e89cf91a0b`
- Head commit: `f4c3cdb020e6808333ec460470f9e081f0d9bc93`
- Head OpenAPI: `openapi.yaml`
- Frozen Head Spec SHA256: `2aee71c180222191e49fb3df2edea8820c0bb49ab1909f20fe9b408cc731e6b6`
- Materialized Head Spec SHA256: `2aee71c180222191e49fb3df2edea8820c0bb49ab1909f20fe9b408cc731e6b6`

## 1. What are the fixed commits?

The Base is the full 40-character commit `19545601676675a923bb9850499717e89cf91a0b`; the Head is `f4c3cdb020e6808333ec460470f9e081f0d9bc93`. The Head source was downloaded from GitHub codeload by this exact SHA, not from a moving branch or a latest image.

## 2. Can the Head service start from that commit?

Yes. The repository source at the exact Head commit built into a Windows Go binary using Go 1.20.14. The binary embeds the exact commit as its version. With a fresh local PostgreSQL 16 container, the service bound only to `127.0.0.1` and `/health` returned HTTP 200.

## 3. Which dependencies are required?

The smoke path required the exact-commit Go service and PostgreSQL. No Redis, mail server, paid cloud service, or public Supabase deployment was required.

## 4. Is PostgreSQL sufficient?

For startup, migration, health, and the selected `/authorize` smoke path, yes. A fake local provider configuration was supplied only so the handler could construct its redirect. No provider secret or account was used.

## 5. Are mail or external providers required?

They are not required to start the service. The affected endpoint normally constructs a redirect to an external OAuth provider. The smoke client disabled redirects, observed the local HTTP 302, and sent no request to that external host. Formal E3-C must retain Guardian's fixed `--max-redirects 0` behavior.

## 6. What is the affected operation?

The frozen E3-B Ground Truth identifies `GET /authorize`. The Base documents `flow_type`; the Head removes it. The operation still exists in the Head, so it is runtime-executable.

## 7. Does the affected operation require authentication?

The OpenAPI document declares `APIKeyAuth`, but the exact Head routing code does not place `/authorize` behind bearer-token authentication middleware. A first local request reached the handler without a real credential. A second request used only a dummy local value and received HTTP 302. No dynamic user token or production key is needed.

## 8. Can Schemathesis safely generate a request?

The Head OpenAPI is self-contained OpenAPI 3.0.3 and loads successfully in Schemathesis 4.24.3. The request is safe only against the local service with redirects disabled. Some generated providers may produce an ordinary 4xx; that is acceptable runtime evidence, not a startup blocker.

## 9. Does the Head Spec match the running implementation?

Yes at the revision level. The source archive, embedded binary version, and OpenAPI file all resolve to `f4c3cdb020e6808333ec460470f9e081f0d9bc93`. The source-tree OpenAPI hash exactly matches the frozen E3-B Head hash.

## 10. Does the toolchain accept the Head Spec?

Yes. E3-B already established that this pair does not have the Supabase Spec-load failure seen in cases 001–004. Schemathesis 4.24.3 independently loaded the Head document and counted 39 operations under `/auth/v1/`.

## 11. Is it suitable for formal E3-C?

Classification: **RUNTIME_FEASIBLE**.

It is suitable because revision alignment, local startup, health, reset, anonymous execution, and redirect containment are all demonstrated. It is not evidence that the endpoint will PASS; formal E3-C may observe a normal PASS, FAIL, or ERROR and must retain that result.

## Smoke and reset evidence

- Health: HTTP 200.
- Safe manual operation: local `GET /authorize`, HTTP 302 with redirect following disabled.
- External request made: no.
- Real credential used: no.
- Persistent test data: none; PostgreSQL used an `--rm` container without a named volume.
- Reset: stop the service, stop the temporary PostgreSQL container, and recreate a fresh database.
- Temporary E3-C0 containers remaining: none.

## Limitation

This is one of two feasible pairs, but both are from the same repository and share the same service startup mechanism. It proves feasibility for Supabase Auth, not runtime portability across all four E3-B repositories.
