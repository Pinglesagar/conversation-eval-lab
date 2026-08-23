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

| metric | value | numerator / denominator |
|---|---|---|
| true positive rate (recall) | 1.000 | 15 / 15 |
| true negative rate (specificity) | 1.000 | 12 / 12 |
| precision | 1.000 | 15 / 15 |
| recall | 1.000 | 15 / 15 |
| F1 | 1.000 | 30 / 30 |
| raw agreement | 1.000 | 27 / 27 |
| prevalence of 'fail' | 0.556 | 15 / 27 |
| Cohen's kappa | 1.000 | observed 1.000, chance 0.506 |

Raw agreement is reported next to kappa deliberately: raw agreement flatters a judge on imbalanced data, because always answering with the majority class scores the majority fraction. Kappa subtracts the agreement two graders with these marginals would reach by chance.

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
