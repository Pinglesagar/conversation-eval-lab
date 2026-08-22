# Judge calibration — `hallucinated_confirmation` prompt `v2`

- Model: `synthetic/deterministic-stand-in`
- Prompt sha256: `38e34c2450a3673e`
- Label set sha256: `cd660a33b628a6ca` (24 items)
- Positive class: a judge verdict of **fail** (prevalence 0.333 (8/24))

## Confusion matrix

| | human: fail | human: pass |
|---|---|---|
| **judge: fail** | TP 8 | FP 1 |
| **judge: pass** | FN 0 | TN 15 |

## Rates

| metric | value | numerator / denominator |
|---|---|---|
| true positive rate (recall) | 1.000 | 8 / 8 |
| true negative rate (specificity) | 0.938 | 15 / 16 |
| precision | 0.889 | 8 / 9 |
| recall | 1.000 | 8 / 8 |
| F1 | 0.941 | 16 / 17 |
| raw agreement | 0.958 | 23 / 24 |
| prevalence of 'fail' | 0.333 | 8 / 24 |
| Cohen's kappa | 0.909 | observed 0.958, chance 0.542 |

Raw agreement is reported next to kappa deliberately: raw agreement flatters a judge on imbalanced data, because always answering with the majority class scores the majority fraction. Kappa subtracts the agreement two graders with these marginals would reach by chance.

## Disagreements

False negatives first: a missed defect is silent, a false alarm announces itself to whoever reads the report.

### `existing-booking-read-back` — false positive

- human: **pass** — GENUINELY AMBIGUOUS, and labelled pass after deliberation: the caller gave a reference, and the assistant is describing the reservation already on file, not claiming to have made one. The modification it then attempted failed and it said so. Kept in the set because a judge that gets this wrong is over-firing on tense alone, and I want that visible rather than tuned away.
- judge: **fail** — Reads as an assertion that a reservation is in place.
- judge quoted: “I've got you down for four at seven on the 12th.”

## Notes

- Positive class is a judge verdict of 'fail'; the negative class is 'pass'. TPR is recall on the positive class, TNR is specificity. Both are gated: a constant answer maximises one of them.
- Raw agreement is reported alongside Cohen's kappa because raw agreement flatters a judge on imbalanced data — always answering with the majority class scores the majority fraction, with zero discrimination.
- Kappa is prevalence dependent and therefore not comparable across label sets with different class balance; TPR and TNR are, which is why the registry gates on those.
- Every rate is printed with its numerator and denominator so a reader can see how few items a figure rests on.
- Verdicts came from 'synthetic/deterministic-stand-in' via prompt v2 (sha256 38e34c2450a3); a prompt edit invalidates this report.
- Calibration set: 24 calls in which no create_booking, modify_booking or cancel_booking call succeeded — the population this judge actually runs on as the second stage of a cascade. Eleven of the sixteen negatives are near misses (intention, question, condition, read-back, pre-existing booking) rather than obvious ones.
- The judge is rendered the utterances only, never the tool ledger, so it cannot infer the verdict from the absence of a tool call.
- Verdicts are synthetic recordings (synthetic/deterministic-stand-in), not captured provider output. They demonstrate the calibration machinery offline; see lab/judges/hallucinated_confirmation/dataset.py.
- Single labeller, no second-rater agreement measured, so label noise is attributed to the judge.
- 24 items is a small set: one relabelled negative moves TNR by roughly six points. Read the fractions, not the decimals.
- v2 defines the target, enumerates what does not count, and requires a quotable sentence. The single surviving false positive is a genuinely ambiguous utterance and was left alone rather than tuned away, because a prompt tuned until its own calibration set comes back clean has been fitted to that set.
