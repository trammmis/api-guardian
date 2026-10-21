# API Guardian E3.5 Root-Cause Diagnostic

## 1. Why E3.5 Was Needed

E3-B exposed four Supabase tool-load errors, one Supabase policy false negative, and three fallback cases
whose all-operation selection reduced executable-scope precision to 3.43%. E3.5 explains those frozen
observations; it does not replace E3-B results.

## 2. Frozen E3-B Facts

- ContractVerdictAccuracy: 14/19 (73.68%)
- BreakingChangeRecall: 7/9 (77.78%)
- SystemExecutionSuccessRate: 15/19 (78.95%)
- End-to-end executable scope: 58/73 recall; 58/1690 precision
- True executable selector false negatives: 0

## 3. Supabase OpenAPI Validity

Redocly rejects the affected historical documents because a Schema Object slot references a Response Object
(`#/components/responses/UnauthorizedResponse`). `openapi-spec-validator` instead stops on numeric YAML
response keys and accepts the minimal typed-reference case; this validator disagreement is retained.
Guardian materialization is byte-identical to the frozen upstream artifacts.

## 4. oasdiff Compatibility Diagnosis

oasdiff 1.28.0 and isolated 1.27.0 both reject the full affected documents and minimal reproducer, and both
accept the corrected control. The best-supported classification is `INVALID_OPENAPI_INPUT`, not a
version-specific 1.28.0 regression. The official
[release index](https://github.com/oasdiff/oasdiff/releases) listed no newer stable diagnostic release.

## 5. Minimal Reproducer

The minimal pair contains one operation whose response-body Schema `$ref` points to a Response Object.
Removing unrelated paths/components preserves the error; correcting the target object type removes it.

## 6. Supabase 005 Breaking False Negative

oasdiff detects optional `flow_type` query-parameter removal but assigns level 2 WARNING. Guardian retains
the normalized change and, by frozen policy, fails only on ERROR. This is a policy disagreement, not a
normalization loss.

## 7. Fallback Root Causes

Every fallback is triggered by an operation-removal record that cannot resolve in Head. Appwrite component
changes were already expanded to operation-local records; they are not the direct fallback trigger.

## 8. Removed-operation Analysis

Immich 005 is a pure removal and can support an empty executable scope in a future ablation. Mixed Appwrite
commits require per-change handling so other affected operations remain selected.

## 9. Shared-component Analysis

The benchmark-only dependency graph obtained 27/
27 recall and
27/
27 precision. This is feasibility evidence only.

## 10. Supplemental Diagnostic Metrics

**SUPPLEMENTAL ONLY**

- NonFallback executable recall: 31/31 (100.00%)
- NonFallback executable precision: 31/31 (100.00%)
- Fallback-only precision: 27/1659 (1.63%)
- Fallback over-selection: 1632 operations
- Tool-compatible conditional contract accuracy: 14/15 (93.33%)

These do not replace E3-B ContractVerdictAccuracy 14/19 or executable precision 58/1690.

## 11. Root Cause Matrix

`root_cause_matrix.csv` contains 8 per-finding records with layer, confidence, candidate fix and risk.

## 12. Candidate Future Fixes

1. **P1 Removed-operation no-fallback ablation**: test per-change empty executable scope for proven removals;
   primary metric executable precision, regression metric executable recall.
2. **P2 Component dependency mapping ablation**: compare transitive `$ref` mapping with frozen scope;
   primary metric fallback/precision, regression metric recall.
3. **P3 oasdiff upgrade ablation**: currently blocked as v1.28.0 is already latest; revisit only when a newer
   release documents relevant loader/rule changes.
4. **P4 Validator parity experiment**: compare preflight validator decisions with oasdiff loader failures.

## 13. What Was NOT Changed

Production Core, E3-B Ground Truth v1/v2, E3-B raw output, E2/E2.5 artifacts and all headline metrics.
No Schemathesis, LLM, public production API, product fix or optimization was run.

## 14. Threats to Validity

Only two oasdiff releases were executable; no release newer than 1.28.0 existed. The dependency graph covers
local fragment refs in two large Appwrite artifacts and is not a complete OpenAPI semantic resolver. The
independent validator is one implementation, although its result agrees with the OpenAPI object-type rule.

## 15. Recommended Next Stage

Run P1 as an isolated product experiment before E3-C Runtime. It has a narrow hypothesis, high-confidence
root cause and lower regression risk than immediately introducing a general component resolver.
