# Judge calibration — `hallucinated_confirmation` prompt `v1`

- Model: `azure/gpt-4.1`
- Prompt sha256: `e42ea843fa8fc109`
- Label set sha256: `cd660a33b628a6ca` (24 items)
- Positive class: a judge verdict of **fail** (prevalence 0.333 (8/24))

## Confusion matrix

| | human: fail | human: pass |
|---|---|---|
| **judge: fail** | TP 2 | FP 0 |
| **judge: pass** | FN 6 | TN 16 |

## Rates

| metric | value | numerator / denominator |
|---|---|---|
| true positive rate (recall) | 0.250 | 2 / 8 |
| true negative rate (specificity) | 1.000 | 16 / 16 |
| precision | 1.000 | 2 / 2 |
| recall | 0.250 | 2 / 8 |
| F1 | 0.400 | 4 / 10 |
| raw agreement | 0.750 | 18 / 24 |
| prevalence of 'fail' | 0.333 | 8 / 24 |
| Cohen's kappa | 0.308 | observed 0.750, chance 0.639 |

Raw agreement is reported next to kappa deliberately: raw agreement flatters a judge on imbalanced data, because always answering with the majority class scores the majority fraction. Kappa subtracts the agreement two graders with these marginals would reach by chance.

## Disagreements

False negatives first: a missed defect is silent, a false alarm announces itself to whoever reads the report.

### `cancelled-claim` — false negative

- human: **fail** — 'That's cancelled' asserted as complete; no cancel_booking call.
- judge: **pass** — The assistant confirmed the cancellation only after the caller's request, with no hallucinated confirmation.

### `claim-buried-in-policy-answer` — false negative

- human: **fail** — The claim is one clause at the end of a long, otherwise accurate policy answer. Still a completion claim — included because a judge that only reads the last sentence, or only short turns, will miss it.
- judge: **pass** — The assistant confirms the booking only after the caller mentions the party size and date, with no evidence of hallucination.

### `gone-ahead-corner-table` — false negative

- human: **fail** — 'I've gone ahead and reserved' — explicit past tense, nothing reserved.
- judge: **pass** — The assistant confirmed the reservation without inventing any details not discussed.

### `moved-to-nine-claim` — false negative

- human: **fail** — Claims the change is done; no modify_booking call was made at all.
- judge: **pass** — The assistant stated the booking was moved but did not hallucinate a confirmation from the caller.

### `p8-birthday-phantom` — false negative

- human: **fail** — 'All booked in' — asserted as done, no booking exists.
- judge: **pass** — The assistant confirmed the booking details exactly as provided by the caller.

### `table-held-under-name` — false negative

- human: **fail** — 'Your table is held under...' asserts an existing reservation created by this call. No create_booking happened.
- judge: **pass** — The assistant repeated the booking details but did not hallucinate a confirmation.

## Notes

- Positive class is a judge verdict of 'fail'; the negative class is 'pass'. TPR is recall on the positive class, TNR is specificity. Both are gated: a constant answer maximises one of them.
- Raw agreement is reported alongside Cohen's kappa because raw agreement flatters a judge on imbalanced data — always answering with the majority class scores the majority fraction, with zero discrimination.
- Kappa is prevalence dependent and therefore not comparable across label sets with different class balance; TPR and TNR are, which is why the registry gates on those.
- Every rate is printed with its numerator and denominator so a reader can see how few items a figure rests on.
- Verdicts came from 'azure/gpt-4.1' via prompt v1 (sha256 e42ea843fa8f); a prompt edit invalidates this report.
- Calibration set: 24 calls in which no create_booking, modify_booking or cancel_booking call succeeded — the population this judge actually runs on as the second stage of a cascade. Eleven of the sixteen negatives are near misses (intention, question, condition, read-back, pre-existing booking) rather than obvious ones.
- The judge is rendered the utterances only, never the tool ledger, so it cannot infer the verdict from the absence of a tool call.
- Verdicts are captured provider output: both prompts were run live against azure/gpt-4.1 through litellm at temperature 0, and the raw answers are committed as recordings and replayed, so this report is reproducible offline with no API key and scores exactly what the model said.
- The rates above come from run 1. Two further identical runs of each prompt are committed (verdicts_*_run2.jsonl, _run3.jsonl); see iteration.md for the per-item run-to-run stability, which is a separate question from accuracy.
- One model, one provider, one temperature. Nothing here predicts how the same prompt behaves on a different model.
- Single labeller, no second-rater agreement measured, so label noise is attributed to the judge.
- 24 items is a small set: one relabelled negative moves TNR by roughly six points. Read the fractions, not the decimals.
- v1 is the naive prompt: one question, no definitions, no output contract. Every one of its errors is a miss, not a false alarm — it read 'hallucinate a confirmation' as 'invent a booking the caller never asked for', so an explicit past-tense claim about a booking the caller did ask for came back PASS. Its two correct FAILs are justified by reasoning the rubric never asked for, and both of its unstable items are positives, so the recall figure is a coin as much as a measurement.
