# E4 Automated LLM Quality & Failure Isolation Evaluation

## 1. Goal

Evaluate the current production `RiskExplanation` and `FailureDiagnosis` for structured output, category accuracy, evidence/recommendation grounding, unsupported claims and failure isolation. A Human Study protocol was prepared but is out of scope for this personal project and was not executed.

## 2. Frozen Product

Production Core, prompts, schemas, model configuration and evidence whitelist were hashed before material construction. No production file was modified for E4.

## 3. Dataset

Fifty frozen automated cases were executed: 25 risk and 25 diagnosis. Thirteen A/B cases and ten participant sequence packets were prepared only as a research-design exercise.

## 4. Study Design

The archived protocol used a within-subject, case-level counterbalanced Report A/B design. Each planned participant would have seen each case once, and Ground Truth was frozen before participant materials. The design was not executed.

## 5. Participants

Status: `NOT_CONDUCTED`.

Reason: `OUT_OF_SCOPE_FOR_PERSONAL_PROJECT`.

Participants: 0. Recruitment, consent execution, dry run, formal collection and human-utility statistics did not occur. No participant data was fabricated.

## 6. Objective Metrics

Not run: triage time, human root-cause accuracy, next-action accuracy and confidence calibration.

## 7. Subjective Metrics

Not run: clarity, actionability, helpfulness and trust.

## 8. Hallucination / Grounding

In the frozen automated audit:

- Evidence grounding: 50/50 = 100%.
- Recommendation grounding: 50/50 = 100%.
- Unsupported claims: 0/50 observed.
- Serious hallucinations: 0/50 observed.

The semantic review was AI-assisted and post-freeze, not a formal independent human rating. The two zero-observation rates are bounded dataset observations, not universal guarantees.

## 9. Automated LLM Evaluation

Production DeepSeek configuration generated the frozen outputs once, without cherry-picking or prompt changes:

- Structured output success: 50/50 = 100%.
- Risk category accuracy: 25/25 = 100%.
- Diagnosis category accuracy: 19/25 = 76%.
- Combined category accuracy: 44/50 = 88%.
- Provider failure: 0/50.

Diagnosis accuracy is intentionally preserved at 76%; the six misses were not removed or regenerated.

## 10. LLM Failure Isolation

Four frozen safe scenarios passed: timeout, invalid structured output, provider failure and simulated 429. In all 4/4 cases, contract, regression and final verdicts were unchanged; only analysis degradation/warnings changed.

## 11. Threats to Validity

The 50 cases are curated rather than randomly sampled from production traffic. Semantic review was AI-assisted. No human-utility outcome exists. Provider success in this run does not establish availability under other models, regions or times. Category labels and grounding checks are bounded by the frozen rubric.

## 12. Conclusions

E4 is closed as an automated quality and failure-isolation evaluation. Human utility is `NOT_EVALUATED`; the project will not execute the prepared Human Study.

## 13. What the Evidence Supports

- The current model produced schema-valid structured output for 50/50 frozen cases.
- Automated category accuracy was 44/50 overall, including Diagnosis 19/25.
- The frozen audit observed grounded evidence/recommendations in 50/50 cases and no unsupported/serious-hallucination claim in 50 cases.
- Four modeled LLM failure modes were isolated from deterministic verdicts.

## 14. What the Evidence Does Not Support

It does not support claims that LLM analysis improves engineer speed, human root-cause accuracy, next-action accuracy, clarity, actionability, trust or productivity. It also does not establish a universal zero hallucination rate.
