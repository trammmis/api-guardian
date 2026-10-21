# Report rendering specification

## Shared deterministic block

For a given case, Report A and Report B must render the exact same serialized `deterministic_evidence` object from `case_cards.jsonl`. A pre-release validator must compare the canonical JSON SHA256 for the shared block.

Display order:

1. Contract verdict
2. Regression verdict
3. Final verdict
4. Normalized contract changes
5. Selected operations
6. Safe regression/tool evidence
7. Explicit limitations or scope note, when present

Never render source-stage labels, Ground Truth, raw tool output, paths, headers, bodies, credentials, prompts, model name, or condition provenance.

## Report A

Render only the shared deterministic block.

## Report B

Render the same shared deterministic block, followed by a neutral section titled `Additional analysis` containing only production Pydantic DTO fields:

- zero or more `RiskExplanation` objects;
- zero or more `FailureDiagnosis` objects.

If production would not run LLM analysis for the case, B stays identical to A. This sham/control behavior is preserved rather than adding synthetic prose.

## Blinding

The visible card title is `API scan NNN`, and the visible condition label is `Report A` or `Report B`. Participants are not told which condition contains machine-generated analysis. The analyst keeps the mapping separately.

