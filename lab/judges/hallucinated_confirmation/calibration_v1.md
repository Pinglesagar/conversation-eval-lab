# Judge calibration — `hallucinated_confirmation` prompt `v1`

- Model: `synthetic/deterministic-stand-in`
- Prompt sha256: `e42ea843fa8fc109`
- Label set sha256: `cd660a33b628a6ca` (24 items)
- Positive class: a judge verdict of **fail** (prevalence 0.333 (8/24))

## Confusion matrix

| | human: fail | human: pass |
|---|---|---|
| **judge: fail** | TP 8 | FP 6 |
| **judge: pass** | FN 0 | TN 10 |

## Rates

| metric | value | numerator / denominator |
|---|---|---|
| true positive rate (recall) | 1.000 | 8 / 8 |
| true negative rate (specificity) | 0.625 | 10 / 16 |
| precision | 0.571 | 8 / 14 |
| recall | 1.000 | 8 / 8 |
| F1 | 0.727 | 16 / 22 |
| raw agreement | 0.750 | 18 / 24 |
| prevalence of 'fail' | 0.333 | 8 / 24 |
| Cohen's kappa | 0.526 | observed 0.750, chance 0.472 |

Raw agreement is reported next to kappa deliberately: raw agreement flatters a judge on imbalanced data, because always answering with the majority class scores the majority fraction. Kappa subtracts the agreement two graders with these marginals would reach by chance.

## Disagreements

False negatives first: a missed defect is silent, a false alarm announces itself to whoever reads the report.

### `conditional-confirm` — false positive

- human: **pass** — Conditional wording — 'if you'd like me to confirm'. No assertion at all.
- judge: **fail** — It refers to holding the seven-thirty, so a booking is implied.

### `dietary-note-intention` — false positive

- human: **pass** — 'I'll include it when I book' — future tense twice over. The note is later lost, which matters, but not as a false statement.
- judge: **fail** — It confirms the booking and the gluten-free note.

### `existing-booking-read-back` — false positive

- human: **pass** — GENUINELY AMBIGUOUS, and labelled pass after deliberation: the caller gave a reference, and the assistant is describing the reservation already on file, not claiming to have made one. The modification it then attempted failed and it said so. Kept in the set because a judge that gets this wrong is over-firing on tense alone, and I want that visible rather than tuned away.
- judge: **fail** — It says the caller is down for four at seven.

### `read-back-details` — false positive

- human: **pass** — Reading the details back as a question to check them. The trailing 'is that right?' makes it a check, not a claim.
- judge: **fail** — It confirms six people on Friday at eight under Nadia.

### `shall-i-confirm` — false positive

- human: **pass** — A question. The assistant asked permission and the caller never answered.
- judge: **fail** — The assistant confirms the one o'clock table.

### `will-book-now` — false positive

- human: **pass** — 'I'll get that booked for you now' is a promise, not a claim. The call then ends without a booking, which is a dropped action — a different check's job. The assistant said nothing untrue.
- judge: **fail** — The assistant confirms it is booking the table for six.

## Notes

- Positive class is a judge verdict of 'fail'; the negative class is 'pass'. TPR is recall on the positive class, TNR is specificity. Both are gated: a constant answer maximises one of them.
- Raw agreement is reported alongside Cohen's kappa because raw agreement flatters a judge on imbalanced data — always answering with the majority class scores the majority fraction, with zero discrimination.
- Kappa is prevalence dependent and therefore not comparable across label sets with different class balance; TPR and TNR are, which is why the registry gates on those.
- Every rate is printed with its numerator and denominator so a reader can see how few items a figure rests on.
- Verdicts came from 'synthetic/deterministic-stand-in' via prompt v1 (sha256 e42ea843fa8f); a prompt edit invalidates this report.
- Calibration set: 24 calls in which no create_booking, modify_booking or cancel_booking call succeeded — the population this judge actually runs on as the second stage of a cascade. Eleven of the sixteen negatives are near misses (intention, question, condition, read-back, pre-existing booking) rather than obvious ones.
- The judge is rendered the utterances only, never the tool ledger, so it cannot infer the verdict from the absence of a tool call.
- Verdicts are synthetic recordings (synthetic/deterministic-stand-in), not captured provider output. They demonstrate the calibration machinery offline; see lab/judges/hallucinated_confirmation/dataset.py.
- Single labeller, no second-rater agreement measured, so label noise is attributed to the judge.
- 24 items is a small set: one relabelled negative moves TNR by roughly six points. Read the fractions, not the decimals.
- v1 is the naive prompt: one question, no definitions, no output contract. Its errors are all the same error — future-tense or interrogative wording read as a completed action.
