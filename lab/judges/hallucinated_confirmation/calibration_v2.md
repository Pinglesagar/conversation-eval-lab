# Judge calibration — `hallucinated_confirmation` prompt `v2`

- Model: `azure/gpt-4.1`
- Prompt sha256: `38e34c2450a3673e`
- Label set sha256: `cd660a33b628a6ca` (24 items)
- Positive class: a judge verdict of **fail** (prevalence 0.333 (8/24))

## Confusion matrix

| | human: fail | human: pass |
|---|---|---|
| **judge: fail** | TP 8 | FP 0 |
| **judge: pass** | FN 0 | TN 16 |

## Rates

| metric | value | numerator / denominator |
|---|---|---|
| true positive rate (recall) | 1.000 | 8 / 8 |
| true negative rate (specificity) | 1.000 | 16 / 16 |
| precision | 1.000 | 8 / 8 |
| recall | 1.000 | 8 / 8 |
| F1 | 1.000 | 16 / 16 |
| raw agreement | 1.000 | 24 / 24 |
| prevalence of 'fail' | 0.333 | 8 / 24 |
| Cohen's kappa | 1.000 | observed 1.000, chance 0.556 |

Raw agreement is reported next to kappa deliberately: raw agreement flatters a judge on imbalanced data, because always answering with the majority class scores the majority fraction. Kappa subtracts the agreement two graders with these marginals would reach by chance.

## Disagreements

None — the judge matched every human label on this set.
## Notes

- Positive class is a judge verdict of 'fail'; the negative class is 'pass'. TPR is recall on the positive class, TNR is specificity. Both are gated: a constant answer maximises one of them.
- Raw agreement is reported alongside Cohen's kappa because raw agreement flatters a judge on imbalanced data — always answering with the majority class scores the majority fraction, with zero discrimination.
- Kappa is prevalence dependent and therefore not comparable across label sets with different class balance; TPR and TNR are, which is why the registry gates on those.
- Every rate is printed with its numerator and denominator so a reader can see how few items a figure rests on.
- Verdicts came from 'azure/gpt-4.1' via prompt v2 (sha256 38e34c2450a3); a prompt edit invalidates this report.
- Calibration set: 24 calls in which no create_booking, modify_booking or cancel_booking call succeeded — the population this judge actually runs on as the second stage of a cascade. Eleven of the sixteen negatives are near misses (intention, question, condition, read-back, pre-existing booking) rather than obvious ones.
- The judge is rendered the utterances only, never the tool ledger, so it cannot infer the verdict from the absence of a tool call.
- Verdicts are captured provider output: both prompts were run live against azure/gpt-4.1 through litellm at temperature 0, and the raw answers are committed as recordings and replayed, so this report is reproducible offline with no API key and scores exactly what the model said.
- The rates above come from run 1. Two further identical runs of each prompt are committed (verdicts_*_run2.jsonl, _run3.jsonl); see iteration.md for the per-item run-to-run stability, which is a separate question from accuracy.
- One model, one provider, one temperature. Nothing here predicts how the same prompt behaves on a different model.
- Single labeller, no second-rater agreement measured, so label noise is attributed to the judge.
- 24 items is a small set: one relabelled negative moves TNR by roughly six points. Read the fractions, not the decimals.
- v2 defines the target, enumerates what does not count, requires a quotable sentence, and declares dropped actions out of scope. It agrees with the labeller on all 24 items and is unanimous across three identical runs, which means this label set can no longer measure it: the next step is harder items, not a further prompt revision tuned against a set it already saturates.
