# Material Passport

## Sources

- E2 frozen mutation records in `benchmarks/guardian/datasets/mutation/`.
- E2 frozen contract records in `benchmarks/guardian/datasets/contract/cases.jsonl`.
- E3-B review records v2 and system artifacts under `benchmarks/guardian/reports/e3/`.
- P1-I frozen before/after evidence under `benchmarks/guardian/reports/p1_integration/`.
- E3.5 Supabase tool diagnostics under `benchmarks/guardian/reports/e3_5/`.
- E3-C frozen runtime summary under `benchmarks/guardian/reports/e3_c/`.
- Current production LLM schemas, analyzers, prompts, and service configuration under `app/services/guardian/` and `app/services/llm/`.

## Transformations

- Selected a maximum-variation 13-case human-study sample.
- Rephrased safe deterministic facts into short case cards without adding raw bodies, headers, credentials, or source code claims.
- Defined independent answer categories and acceptable alternatives before model generation or participant exposure.
- Built a separate 50-case automated quality dataset: 25 risk and 25 diagnosis cases.
- Generated balanced A/B sequence assignments; no responses or scores were generated.

## Uncertainties and limits

- The participant consent text, data retention period, and recruiting channel still require user decision.
- Condition B production outputs do not yet exist and must be generated once, without cherry-picking, after approval.
- Some contract-only and tool-error cases intentionally have no applicable diagnosis DTO; A/B may therefore be identical.
- The human sample is purposeful, not a random sample of all API changes.

## Status

Suitable for protocol review only. Not suitable for claiming LLM utility.

