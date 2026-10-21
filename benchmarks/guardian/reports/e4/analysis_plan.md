# E4-A Human Study Analysis Plan

## Material Passport

- Origin: E4 approved pre-human protocol and frozen Guardian benchmark evidence
- Mode: human-study execution freeze
- Date: 2026-09-15
- Verification status: pre-registered before Condition B generation and participant collection
- Version: `e4_analysis_plan_v1`

## Primary endpoints

The three co-primary endpoints are:

1. `RootCauseAccuracy` — binary 0/1 per assigned case.
2. `NextActionAccuracy` — binary 0/1 per assigned case.
3. `TriageTimeSeconds` — `T1 - T0` in seconds.

No primary claim may be based only on Likert scores.

Secondary objective endpoint: `ConfidenceCalibration`.

Subjective secondary endpoints: `Clarity`, `Actionability`, `Helpfulness`, `Trust` (all 1–5).

## Intention-to-treat population

The primary analysis includes every pre-frozen case assignment for every consented participant who starts the study. A Condition B assignment remains B in ITT even when production produces no `RiskExplanation` or `FailureDiagnosis` and the visible report is therefore identical to A.

An `LLM_PRESENT_SUBGROUP` may include only B assignments where at least one production analysis DTO is visible. It is `SUPPLEMENTAL ONLY` and cannot replace ITT.

Dry-run records and participants who never reach T0 for any case are excluded. Partial formal participants remain in ITT for all cases actually presented at T0; unpresented cases are missing, not scored incorrect or imputed.

## Timing

- `T0`: the complete case report is first rendered and visible.
- `T1`: submission of problem judgment, root-cause option, next-action option, and confidence.
- `TriageTimeSeconds = T1 - T0`.
- Likert forms appear only after T1 and their time is excluded.
- Browser/tab visibility loss is recorded as a timing-quality flag. The observation remains in ITT; a flag-excluded timing analysis may be supplemental.

## Correctness

Primary correctness is option-based. Root-cause and next-action choices receive 1 only when they equal a pre-frozen correct option or pre-frozen acceptable alternative in `scoring_rules.json`; otherwise 0. Optional free text is never used to change primary correctness.

## Confidence calibration

Confidence is normalized from 0–100 to `[0,1]`.

- `brier_error = (confidence_probability - correctness)^2`, reported separately for root-cause and next-action correctness.
- Also report mean confidence on correct and incorrect answers.
- Calibration is secondary and does not control the utility label.

## Estimands

For binary endpoints, report condition numerator/denominator, percentage, and B−A percentage-point difference. For time, report condition median, p95, participant-level paired median difference, and relative median reduction `(median_A - median_B) / median_A`.

For 5–7 participants, results are descriptive. For 8–10, add participant-cluster paired bootstrap 95% intervals. Do not infer broad population effects from this convenience sample.

## Meaningful improvement thresholds

Frozen before results:

- RootCauseAccuracy non-decline: `B accuracy >= A accuracy` in ITT.
- Meaningful NextActionAccuracy improvement: `B - A >= 5 percentage points`.
- Meaningful timing improvement: `median_B <= 0.90 * median_A` (at least 10% median reduction).
- Low-risk participant-visible serious hallucination: `<= 5%` of distinct B analysis DTOs.
- Low-risk automated serious hallucination: `<= 5%` of valid automated outputs.

## Human utility decision rule

Precedence:

1. Fewer than 5 completed formal participants: `PILOT_UNDERPOWERED`.
2. RootCauseAccuracy declines, or either serious-hallucination threshold is exceeded: `NO_MEASURABLE_OBJECTIVE_UTILITY` (with harm/safety warning where applicable).
3. RootCauseAccuracy does not decline, at least one meaningful objective improvement threshold is met, and both serious-hallucination thresholds are met: `LLM_UTILITY_SUPPORTED`.
4. Only subjective ratings improve: `SUBJECTIVE_PREFERENCE_ONLY`.
5. No objective threshold is met: `NO_MEASURABLE_OBJECTIVE_UTILITY`.
6. Incomplete/invalid execution preventing interpretation: `INCONCLUSIVE`.

## Missing and anomalous data

- No post-hoc acceptable answers.
- No outcome-driven participant exclusion.
- Record duplicate submission, impossible/negative time, timer visibility flags, and missing options.
- Impossible time is missing for timing only; available correctness remains.
- Report raw denominators for every endpoint.

## Safety metrics

Condition B outputs are reviewed once before collection for unsupported claims, serious hallucinations, evidence grounding, and recommendation grounding. These fixed outputs are not rescored per participant.

