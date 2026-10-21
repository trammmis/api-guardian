# API Guardian E3-A Real-world Candidate Discovery

## Material Passport

- Origin Skill: `academic-research-suite / experiment-agent`
- Origin Mode: `validate`
- Verification Status: candidate metadata verified; version-pair Ground Truth has **not** been constructed
- Version Label: `guardian_e3_a_candidates_v1`
- Audit time: 2026-09-06 (Asia/Shanghai)
- Guardian commit: `bcc2c8c1303a1eaae9a99dce73c0fe72dc2fc46d`

## Scope and stop condition

This document records only Stage E3-A candidate discovery. No candidate repository was cloned, no final version pair was created, no oasdiff/Guardian accuracy metric was run, and no public API was called as a test target. E3-B must not begin until the repository selection is confirmed.

Selection first applies the frozen Guardian boundaries visible in `app/services/guardian/security.py`: a single Spec must be at most 5 MiB, contain a top-level OpenAPI 3.x `openapi` field, and contain no external `$ref`. This means a historically rich repository can still be rejected if its current artifact cannot enter the frozen production pipeline without conversion.

## Audit method

1. Public repository metadata and license files were inspected.
2. Candidate Spec paths were fetched read-only from `raw.githubusercontent.com`; no third-party Spec was persisted.
3. Accessible JSON/YAML was parsed to count HTTP operations, bytes, `$ref` values, and OpenAPI version.
4. For viable or high-value backup candidates, path-scoped GitHub commit history supplied the earliest/latest observed Spec commit and number of path changes.
5. Docker/startup evidence was used only to estimate optional runtime feasibility. E3 contract selection does not imply that a historical service can be started.

Anonymous GitHub REST rate limiting was reached after viable candidates had been fully audited. Candidates already rejected by a decisive earlier gate therefore retain `null` deep-history values in `candidates.json`; these values are not silently estimated.

## Candidate table

Stars are a descriptive snapshot observed on 2026-09-06 and do not affect selection.

| Status | Repository | License | Language | OpenAPI path | Version / bytes | Operations | History quality | External refs | Docker / start | Runtime feasibility | Selection reason |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| **Selected** | [immich-app/immich](https://github.com/immich-app/immich) (113,509★) | AGPL-3.0 | TypeScript | [`open-api/immich-openapi-specs.json`](https://github.com/immich-app/immich/blob/main/open-api/immich-openapi-specs.json) | 3.0.0 / 924,312 B | 275 | Rich: 567 path changes since 2024-01-12 | None; 720 local refs | Yes / hard | Contract first | Real maintained app, moderate-large surface, exceptionally rich recent history. |
| **Selected** | [supabase/auth](https://github.com/supabase/auth) (2,556★) | MIT | Go | [`openapi.yaml`](https://github.com/supabase/auth/blob/master/openapi.yaml) | 3.0.3 / 136,129 B | 59 | Medium: 47 path changes since 2023-02-01 | None; 193 local refs | Yes / medium | **Possible**, pending unauthenticated-operation audit | Small self-contained Spec tied directly to a real service; useful contrast to the large candidates. |
| **Selected** | [netbirdio/netbird](https://github.com/netbirdio/netbird) (28,945★) | BSD-3-Clause for audited `shared/` path; selected top-level service directories are AGPL-3.0 | Go | [`shared/management/http/api/openapi.yml`](https://github.com/netbirdio/netbird/blob/main/shared/management/http/api/openapi.yml) | 3.1.0 / 500,736 B | 240 | Rich recent: 68 path changes since 2025-08-05 | None; 1,389 local refs | Yes / hard | No initial runtime | Adds OpenAPI 3.1 and shared-schema-heavy real changes; contract-only avoids unsafe network-service setup. |
| **Selected** | [appwrite/specs](https://github.com/appwrite/specs) (2★) | BSD-3-Clause | Specs; source product mainly PHP | [`specs/1.9.x/open-api3-1.9.x-server.json`](https://github.com/appwrite/specs/blob/main/specs/1.9.x/open-api3-1.9.x-server.json) plus released version directories | 3.0.0 / 5,127,762 B | 783 | Rich across 0.6.x–1.9.x; current file has 28 changes | None; 1,025 local refs | No in spec repo / hard | No initial runtime | Official multi-version corpus and a valuable near-5-MiB boundary case. Every pair must recheck size. |
| **Backup** | [goauthentik/authentik](https://github.com/goauthentik/authentik) (25,380★) | MIT core with documented website/enterprise exceptions | Python | [`schema.yml`](https://github.com/goauthentik/authentik/blob/main/schema.yml) | 3.0.3 / 1,803,097 B | 1,193 | Very rich: 865 path changes since 2021-05-15 | None; 5,941 local refs | Yes / hard | No initial runtime | Strong very-large Python backup, but generated mixed core/enterprise diffs demand more Ground Truth effort. |
| Rejected | [gotify/server](https://github.com/gotify/server) (15,855★) | MIT | Go | [`docs/spec.json`](https://github.com/gotify/server/blob/master/docs/spec.json) | **Swagger 2.0** / 94,742 B | 42 | Rich: 61 path changes since 2018-02-18 | None; 165 local refs | Yes / easy | No | Frozen Guardian requires OpenAPI 3.x. Converting Swagger would alter the evaluated input. |
| Rejected | [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) (126,415★) | Apache-2.0 | Go | [`api/openapi-spec/swagger.json`](https://github.com/kubernetes/kubernetes/blob/master/api/openapi-spec/swagger.json) | **Swagger 2.0** / 4,475,944 B | 1,202 | Very rich: 1,916 path changes since 2016-10-25 | None; 10,647 local refs | Yes / hard | No | Monolithic file is Swagger 2.0; v3 is split across many documents and is not one V1 self-contained input. |
| Rejected | [stripe/openapi](https://github.com/stripe/openapi) (501★) | MIT | Specs | [`openapi/spec3.json`](https://github.com/stripe/openapi/blob/master/openapi/spec3.json) | OpenAPI 3.x / **8,028,700 B** | Not fully read | Rich, deep audit skipped | Not fully audited | No / unknown | No | Exceeds frozen 5 MiB limit. Audit stopped after proving the size failure. |
| Rejected | [github/rest-api-description](https://github.com/github/rest-api-description) (1,634★) | MIT | Specs | [`descriptions/api.github.com/api.github.com.json`](https://github.com/github/rest-api-description/blob/main/descriptions/api.github.com/api.github.com.json) | OpenAPI 3.x / **12,927,758 B** | Not fully read | Rich, deep audit skipped | Not fully audited | No / unknown | No | Exceeds frozen 5 MiB limit; public production GitHub API also cannot be a test target. |
| Rejected | [portainer/portainer](https://github.com/portainer/portainer) (38,442★) | Zlib | TypeScript | Documented `dist/docs/openapi.yaml` build output | Generated / absent on branch | Unknown | Insufficient path stability | Unknown | Yes / easy | No | Current documented Spec outputs and the older `api/swagger.yaml` path are not committed on the audited branch. |
| Rejected | [paperless-ngx/paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) (44,887★) | GPL-3.0 | Python | Runtime `/api/schema/view/` | Runtime generated | Unknown | No committed artifact found | Unknown | Yes / medium | No | The API is real, but E3 cannot restore a path-stable Spec directly from Git history. |
| Rejected | [meilisearch/open-api](https://github.com/meilisearch/open-api) (6★) | MIT | Spec documentation | Expected `openapi.yml` absent | None | Unknown | Archived; no current artifact found | Unknown | No in repo / unknown | No | Archived repository does not contain the expected current OpenAPI artifact; publication now depends on documentation/build infrastructure. |

## History evidence for viable candidates

| Repository | Earliest audited Spec commit | Latest audited Spec commit | Path changes | Evidence |
| --- | --- | --- | ---: | --- |
| Immich | `2439c5ab57e8c96f2db982f62b52edff4e723bbf` | `da8131d5c2ec32852d4b3d6d5a410eb3f48605e6` | 567 | [Path history](https://github.com/immich-app/immich/commits/main/open-api/immich-openapi-specs.json) |
| Supabase Auth | `80fd43270fdbcb79e7a2e0b5bce484bd29d9ab49` | `2d4680ef054bedc898da1dbac1b6a7cb9cf5b5af` | 47 | [Path history](https://github.com/supabase/auth/commits/master/openapi.yaml) |
| NetBird | `abd152ee5a66abbe2b04dbe1bbd9fa14fbdc1977` | `76ea72237f3346ff157aa8374b9fa800d6498976` | 68 | [Path history](https://github.com/netbirdio/netbird/commits/main/shared/management/http/api/openapi.yml) |
| Appwrite Specs 1.9.x server | `7bc9eb0603c176b1647aa846fe487f5017919387` | `7e76b048625236d2cfd129b4d66f899c7010f551` | 28 | [Path history](https://github.com/appwrite/specs/commits/main/specs/1.9.x/open-api3-1.9.x-server.json) |
| authentik backup | `1324d03815266a4237d2a718d57a75d6d53e7de4` | `c759b3a3de8ce4f128dd9271b303cfe697b5a138` | 865 | [Path history](https://github.com/goauthentik/authentik/commits/main/schema.yml) |

The “earliest” and “latest” values above mean the ends of the path-scoped history returned during this audit. They are discovery metadata, not approved E3 version-pair boundaries.

## Recommended repositories for E3-B

### 1. `immich-app/immich`

Best primary source of adjacent real changes. It combines a manageable 275-operation surface with 567 committed Spec changes. Recent pull requests visibly update the OpenAPI artifact alongside implementation changes, which improves the chance of independently grounding a pair through Git diff and code context.

### 2. `supabase/auth`

Best small-service contrast and the only selected repository currently worth carrying into an optional runtime feasibility audit. Its 59 operations reduce manual review cost, while the documented local Postgres/Docker flow makes historical startup at least plausible. Runtime remains unconfirmed because Guardian V1 does not accept user-supplied authentication headers.

### 3. `netbirdio/netbird`

Best OpenAPI 3.1 and shared-component candidate. The 240-operation surface and recent 68-change history should expose whether E2.5 component attribution and conservative fallback behavior also occur outside synthetic data. Runtime is intentionally excluded initially because the system manages networking and identity infrastructure.

### 4. `appwrite/specs`

Best released multi-version corpus and size-boundary stress case. The official repository explicitly stores OpenAPI 3 specifications for many product versions. Its current 1.9.x server artifact is only 115,118 bytes below Guardian's 5 MiB limit, so it also validates that E3 respects rather than silently bypasses product constraints.

### Backup: `goauthentik/authentik`

Use authentik if one recommended repository cannot supply at least three high-confidence adjacent pairs. Its 1,193 operations and 865 path changes provide excellent external-validity stress, but generated mixed core/enterprise changes make independent Ground Truth substantially more expensive.

## Why the attractive rejected repositories stay rejected

- Gotify and Kubernetes are not poor projects; their monolithic artifacts are Swagger 2.0, which frozen Guardian rejects before oasdiff. Pre-converting them would evaluate a new preprocessing pipeline, not the current product.
- Stripe and GitHub publish excellent real API descriptions, but the audited artifacts exceed the 5 MiB safety boundary. The Benchmark must not bypass a production limit to improve sample prestige.
- Portainer and Paperless-ngx expose real APIs, but current Specs are generated rather than stored at stable audited Git paths. Rebuilding every historical commit would introduce generator/toolchain reproducibility as a confounder.
- Meilisearch's archived candidate repository no longer provides the expected current artifact at a stable path.

## License and runtime cautions

- NetBird has split licensing. The audited Spec resides under `shared/`; the root license identifies selected top-level service directories as AGPL-3.0. E3-B must preserve path-level attribution.
- authentik documents core MIT licensing with website and enterprise exceptions; any selected diff touching enterprise-described endpoints needs explicit license review.
- Appwrite Specs is a separate BSD-3-Clause repository from the runnable Appwrite product. It is selected for Contract + Scope, not treated as proof of runtime reproducibility.
- No selection authorizes Schemathesis against a public deployment. Any E3-C work must use a pinned local checkout/container and isolated localhost target.

## Production freeze evidence

The machine-readable manifest stores SHA256 for `oasdiff.py`, `scope.py`, `schemathesis.py`, `nodes.py`, `graph.py`, `models.py`, `security.py`, and `errors.py`, plus hashes for frozen E2/E2.5 raw/summary artifacts. E3-A changed only this candidate Markdown and JSON. A final hash comparison is required before E3-B and again after E3 completes.

## E3-A decision

Recommended set: **Immich, Supabase Auth, NetBird, and Appwrite Specs**. This gives four public repositories, three implementation ecosystems, 59–783 operations per selected artifact, OpenAPI 3.0 and 3.1 coverage, direct-operation and shared-component opportunities, and no need to weaken Guardian's current security/input boundaries.

E3-A stops here. No version-pair Dataset, Ground Truth labels, Guardian output, or E3 metric exists yet.
