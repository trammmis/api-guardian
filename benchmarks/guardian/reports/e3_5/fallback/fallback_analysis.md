# Fallback root-cause analysis

## Resolution trace conclusion

All three fallbacks have the same immediate trigger: an ERROR-level `api-path-removed-without-deprecation`
record carries method, path and operationId from Base, but that operation no longer exists in Head. Exact
METHOD/path lookup fails, unique operationId lookup fails, and the frozen recall-first selector marks the
change unresolved. One unresolved change promotes the entire case to all Head operations.

The Appwrite shared-component changes did **not** directly trigger fallback. oasdiff expanded those facts
to operation-local changes with method/path/operationId, which the selector could map. The removed operation
in each mixed commit is the attribution loss that overrides those successful mappings.

## Static cost

| Case | Guardian selected | Oracle executable | Extra | Over-selection | Future narrowing assessment |
|---|---:|---:|---:|---:|---|
| rw_immich_005_server_theme_endpoint_removed | 239 | 0 | 239 | 100.00% | SAFE_TO_NARROW |
| rw_appwrite_002_migration_cutover_replaced | 783 | 1 | 782 | 99.87% | POSSIBLY_SAFE_TO_NARROW |
| rw_appwrite_003_usage_project_and_health_update | 637 | 26 | 611 | 95.92% | POSSIBLY_SAFE_TO_NARROW |

No E2 timing was projected onto these real-world cases.

## Removed operation feasibility

For Immich 005, the available evidence is sufficient to prove a pure removed operation and an empty Head
executable scope. A future **per-change** `REMOVED_OPERATION_EMPTY_EXECUTABLE_SCOPE` policy is safe for this
case. It must not be applied as a whole-commit shortcut: Appwrite 002/003 are mixed commits with other
breaking changes that still require regression scope.

## Appwrite component dependency diagnostic

The benchmark-only transitive local `$ref` graph achieved recall
27/27 (100.00%) and precision
27/27 (100.00%) against the two frozen
Appwrite executable scopes. This proves dependency mapping is technically feasible on these artifacts,
but it was not injected into production and is not evidence that it is safe for every OpenAPI graph.

## Safety assessment

- Immich 005: `SAFE_TO_NARROW` for the proven pure removal, to empty executable scope.
- Appwrite 002: `POSSIBLY_SAFE_TO_NARROW`; handle the removed operation per change and retain mapped facts.
- Appwrite 003: `POSSIBLY_SAFE_TO_NARROW`; same principle, but the mixed/generated component expansion raises regression risk.
