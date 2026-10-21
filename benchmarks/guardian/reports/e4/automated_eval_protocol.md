# E4 Automated LLM Evaluation Protocol

## Status

Setup complete; execution not started. The 50 cases in `automated_eval_cases.jsonl` contain 25 risk and 25 diagnosis inputs. They expand, rather than overwrite, the existing 20-case fixture at `evals/guardian/llm_cases.jsonl`.

## Frozen production settings

- Model source: server-side `DEFAULT_LLM_MODEL`; participants cannot choose it.
- Provider endpoint source: `OPENAI_BASE_URL`; no value or API key is written to this dataset.
- Guardian call temperature: `0` from `guardian_structured_output_kwargs()`.
- DeepSeek thinking: disabled through `extra_body={"thinking":{"type":"disabled"}}` when the configured host is DeepSeek.
- Structured output method: `function_calling`.
- Output schemas: production `RiskExplanation` and `FailureDiagnosis`.
- Retry/timeout: existing `LLMService`; the benchmark adds no second retry policy.

At execution time, record the non-secret model name, sanitized provider hostname, latency, call status, and token usage only if returned by the provider. Do not estimate tokens or cost from text length.

## Metrics

- Structured output success: valid Pydantic object / attempted calls.
- Risk category accuracy: output risk level is in the case's frozen `acceptable_risk_levels`.
- Diagnosis category accuracy: exact match to `expected_category`.
- Evidence grounding: diagnosis evidence is a literal subset of `available_evidence`; risk explanations contain all required concepts under a predeclared normalized-concept matcher.
- Unsupported claim rate: outputs with at least one fact not supported by input / valid outputs.
- Serious hallucination rate: outputs claiming a forbidden concrete source/database/service/deployment fact / valid outputs.
- Recommendation grounding: recommendation addresses the observed contract/failure without claiming a completed fix.

Every metric must include numerator and denominator. Schema-invalid outputs count as structured failures and remain in raw results.

## Review process

Automated checks handle schema validity, ID equality, evidence-subset validation, category matching, and exact forbidden substrings. Grounding/actionability judgments that cannot be decided mechanically receive two independent reviews with disagreements retained. An LLM judge may be explored only as a separately labeled supplemental result, never as human evidence or sole Ground Truth.

## Failure-isolation checks

Use existing safe fakes for timeout, invalid structured output, and provider/429 errors. For each, assert that contract, regression, and final verdicts are byte-for-byte unchanged and only `analysis_degraded`/warnings may change. Do not call a provider to manufacture a 429.

