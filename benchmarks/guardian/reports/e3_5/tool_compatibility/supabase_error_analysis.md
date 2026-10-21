# Supabase oasdiff load-error analysis

## Conclusion

The four E3-B ERROR cases are classified **INVALID_OPENAPI_INPUT**, not a Guardian input transformation bug.
Across the eight Base/Head validations, `openapi-spec-validator` rejected 8/8 artifacts,
and Redocly found structural errors in 8/8 artifacts.

## Exact reference structure

`components.responses.UnauthorizedResponse` is a valid Response Object containing an
`application/json` schema reference to `#/components/schemas/ErrorSchema`. The invalid edge is elsewhere:
`DELETE /user/identities/{identityId} -> responses.401.content.application/json.schema.$ref`
points to `#/components/responses/UnauthorizedResponse`. A Schema Object slot therefore resolves to a
Response Object. This is the node represented by oasdiff's “expecting ref to schema object” message.

Observed wrong Schema-slot references across the fixed Supabase artifacts: 16 records
(duplicates across historical Base/Head materializations are retained for audit).

In the representative frozen Base artifact, `UnauthorizedResponse` has
25 total references, of which
1 occurs in a Schema slot. `ForbiddenResponse` has
21 total references and
1 Schema-slot misuse. Every location and the exact
component definitions are preserved in `validator_results.json`.

## Input integrity

Every diagnostic `git show` artifact matched the frozen E3-B SHA256 and byte count. E3-B wrote these
bytes directly to a temporary file. `prepare_openapi_spec` parsed the file for limits, refs and operation
metadata but did not serialize or rewrite it. Therefore Guardian did not create the invalid reference.

## Independent validator

Two independent validators were used without adding production dependencies:

- `openapi-spec-validator` 0.9.0 in an isolated `uv --with` environment;
- Redocly CLI 2.51.2 through isolated `npx`.

They disagree on the minimal cross-object `$ref`: `openapi-spec-validator` accepts it, while Redocly's
`struct` rule rejects `content` at the resolved Schema position. On the full documents, the former stops at
unquoted numeric response keys (`Dict key must be str`), whereas Redocly reaches and identifies the typed
reference error. The disagreement is retained in `validator_results.json`; it is not averaged away.

## oasdiff version matrix

Production 1.28.0 remains frozen. Diagnostic 1.27.0 was isolated and did not replace it. Both versions
reject the full artifacts and the minimal reproducer; both accept the corrected control. No newer stable
version was available because the official releases page still listed v1.28.0 as latest.

This rules out a regression unique to 1.28.0 among the tested versions. It does not prove every parser
behaves identically; it establishes that Redocly and the OpenAPI object-type boundary reject the reference,
while also preserving the validator disagreement above.

## Minimal reproducer

The `minimal_reproducer` directory preserves the smallest known structure that reproduced the load error.
Changing the operation-level Schema `$ref` to `#/components/schemas/ErrorSchema` makes the control load.

## Upstream evidence

No exact matching oasdiff issue was found. A related
[kin-openapi issue #1079](https://github.com/getkin/kin-openapi/issues/1079) documents the same class of
“expecting ref to schema object” loader failure for another reference location, but it is not evidence of
this exact Supabase case. The object-type interpretation follows the official
[OpenAPI 3.0.3 specification](https://github.com/OAI/OpenAPI-Specification/blob/main/versions/3.0.3.md).

## Frozen-result status

E3-B errors and headline metrics were not changed. Version-matrix details: 2
isolated executables tested.
