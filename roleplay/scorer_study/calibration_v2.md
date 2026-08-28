# Judge calibration — `roleplay_rubric_scorer` prompt `v2`

- Model: `azure/gpt-4.1`
- Prompt sha256: `aab09aecf0155a50`
- Label set sha256: `3bf9c1b846b078e2` (27 items)
- Positive class: a judge verdict of **fail** (prevalence 0.556 (15/27))

## Confusion matrix

| | human: fail | human: pass |
|---|---|---|
| **judge: fail** | TP 15 | FP 0 |
| **judge: pass** | FN 0 | TN 12 |

## Rates

| metric | value | numerator / denominator | 95% Wilson CI |
|---|---|---|---|
| true positive rate (recall) | 1.000 | 15 / 15 | [0.796, 1.000] |
| true negative rate (specificity) | 1.000 | 12 / 12 | [0.758, 1.000] |
| precision | 1.000 | 15 / 15 | [0.796, 1.000] |
| recall | 1.000 | 15 / 15 | [0.796, 1.000] |
| F1 | 1.000 | 30 / 30 | not a proportion |
| raw agreement | 1.000 | 27 / 27 | [0.875, 1.000] |
| prevalence of 'fail' | 0.556 | 15 / 27 | [0.373, 0.724] |
| Cohen's kappa | 1.000 | observed 1.000, chance 0.506 | not a proportion |

Raw agreement is reported next to kappa deliberately: raw agreement flatters a judge on imbalanced data, because always answering with the majority class scores the majority fraction. Kappa subtracts the agreement two graders with these marginals would reach by chance.

The interval is the Wilson score interval at 95%, computed from the two counts in the row beside it and from nothing else, so a reader can recheck it. It is sampling error over items only: it assumes the judge would give the same answer on a second run, which is a separate question with a separate measurement. No interval is given for Cohen's kappa or for F1 — neither is a proportion of independent trials, and a binomial interval on either would be arithmetic applied to the wrong quantity. Precision is the one to read with care: its denominator is the judge's own positive count rather than a class the label set fixed, so its interval is conditional on that count.

## The interval, and which number the gate is standing on

Gate: TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%, scored on the point estimate.

| gated rate | point estimate | 95% Wilson CI | clears on the point? | clears on the lower bound? |
|---|---|---|---|---|
| TPR >= 0.85 | 1.000 (15/15) | [0.796, 1.000] | yes | **no** |
| TNR >= 0.85 | 1.000 (12/12) | [0.758, 1.000] | yes | **no** |

Rule of three, the same fact in the form that is easier to hold on to:

- true positive rate (recall): 0 errors in 15, so the 95% upper bound on the true error rate is about 3/15 = 0.200
- true negative rate (specificity): 0 errors in 12, so the 95% upper bound on the true error rate is about 3/12 = 0.250

**The gate is cleared by the point estimate and not by the evidence.** That is stated rather than hidden, and it is not a reason to abandon the gate: it is the reason the interval is printed next to it. A perfect score clears a 0.85 threshold on its 95% lower bound only from **22** trials upward, so the fix is more labelled items in the class that falls short — not a weaker threshold, and not a better prompt.

This report was scored on the point estimate. `CalibrationThresholds(gate_on='wilson_lower')` scores the lower bound instead; it is not the default because at these set sizes it fails every judge in this repository, none of which regressed — see the class docstring.

## Disagreements

None — the judge matched every human label on this set.
## Notes

- Positive class is a judge verdict of 'fail'; the negative class is 'pass'. TPR is recall on the positive class, TNR is specificity. Both are gated: a constant answer maximises one of them.
- Raw agreement is reported alongside Cohen's kappa because raw agreement flatters a judge on imbalanced data — always answering with the majority class scores the majority fraction, with zero discrimination.
- Kappa is prevalence dependent and therefore not comparable across label sets with different class balance; TPR and TNR are, which is why the registry gates on those.
- Every rate is printed with its numerator and denominator so a reader can see how few items a figure rests on.
- Verdicts came from 'azure/gpt-4.1' via prompt v2 (sha256 aab09aecf015); a prompt edit invalidates this report.
- The instrument under measurement is a real model grading each session against roleplay/rubric_v2.md and returning five per-criterion scores, a verdict, a critique and a quoted span. The scripted scorer in roleplay/scorer.py is a different instrument and is measured separately.
- The positive class is 'fail': a scorer is a defect detector, and recall on the sessions a competent reviewer would stop is the figure that matters.
- Labels are derived by rule from each session's own ledgers — the disclosure register the product wrote and the compliance flags its own flagger raised — not written by hand next to the session. See roleplay/labels.py for the four rules.
- 27 items, 15 of them labelled 'fail'. Items the rules could not settle were excluded rather than guessed; the exclusion list and the reason for each is printed with this report.
- Every item is graded by one call, as a product would. Runs 2 and 3 of the recording measure the instrument's own variance and are never averaged in.
