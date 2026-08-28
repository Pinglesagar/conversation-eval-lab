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

| metric | value | numerator / denominator | 95% Wilson CI | across 3 runs |
|---|---|---|---|---|
| true positive rate (recall) | 1.000 | 8 / 8 | [0.676, 1.000] | 1.000 identical |
| true negative rate (specificity) | 1.000 | 16 / 16 | [0.806, 1.000] | 1.000 identical |
| precision | 1.000 | 8 / 8 | [0.676, 1.000] | 1.000 identical |
| recall | 1.000 | 8 / 8 | [0.676, 1.000] | 1.000 identical |
| F1 | 1.000 | 16 / 16 | not a proportion | 1.000 identical |
| raw agreement | 1.000 | 24 / 24 | [0.862, 1.000] | 1.000 identical |
| prevalence of 'fail' | 0.333 | 8 / 24 | [0.180, 0.533] | 0.333 identical |
| Cohen's kappa | 1.000 | observed 1.000, chance 0.556 | not a proportion | not measured |

Raw agreement is reported next to kappa deliberately: raw agreement flatters a judge on imbalanced data, because always answering with the majority class scores the majority fraction. Kappa subtracts the agreement two graders with these marginals would reach by chance.

The interval is the Wilson score interval at 95%, computed from the two counts in the row beside it and from nothing else, so a reader can recheck it. It is sampling error over items only: it assumes the judge would give the same answer on a second run, which is a separate question with a separate measurement. No interval is given for Cohen's kappa or for F1 — neither is a proportion of independent trials, and a binomial interval on either would be arithmetic applied to the wrong quantity. Precision is the one to read with care: its denominator is the judge's own positive count rather than a class the label set fixed, so its interval is conditional on that count.

## The interval, and which number the gate is standing on

Gate: TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%, scored on the point estimate.

| gated rate | point estimate | 95% Wilson CI | clears on the point? | clears on the lower bound? |
|---|---|---|---|---|
| TPR >= 0.85 | 1.000 (8/8) | [0.676, 1.000] | yes | **no** |
| TNR >= 0.85 | 1.000 (16/16) | [0.806, 1.000] | yes | **no** |

Rule of three, the same fact in the form that is easier to hold on to:

- true positive rate (recall): 0 errors in 8, so the 95% upper bound on the true error rate is about 3/8 = 0.375
- true negative rate (specificity): 0 errors in 16, so the 95% upper bound on the true error rate is about 3/16 = 0.188

**The gate is cleared by the point estimate and not by the evidence.** That is stated rather than hidden, and it is not a reason to abandon the gate: it is the reason the interval is printed next to it. A perfect score clears a 0.85 threshold on its 95% lower bound only from **22** trials upward, so the fix is more labelled items in the class that falls short — not a weaker threshold, and not a better prompt.

This report was scored on the point estimate. `CalibrationThresholds(gate_on='wilson_lower')` scores the lower bound instead; it is not the default because at these set sizes it fails every judge in this repository, none of which regressed — see the class docstring.

## The band this instrument moved through — `v2`

3 identical runs, same prompt, same model (`azure/gpt-4.1`), temperature 0. Every rate this study publishes is computed from run 1, because a product makes one call per item and a figure averaged over three runs describes an instrument nobody deployed. These are the same rates recomputed from each recorded run.

| rate | run 1 | run 2 | run 3 | band across runs | items in its denominator that moved |
|---|---|---|---|---|---|
| true positive rate (recall) | 1.000 (8/8) | 1.000 (8/8) | 1.000 (8/8) | 1.000 identical | 0/8 → up to ±0.000 |
| true negative rate (specificity) | 1.000 (16/16) | 1.000 (16/16) | 1.000 (16/16) | 1.000 identical | 0/16 → up to ±0.000 |
| precision | 1.000 (8/8) | 1.000 (8/8) | 1.000 (8/8) | 1.000 identical | n/a — denominator is not a fixed class |
| recall | 1.000 (8/8) | 1.000 (8/8) | 1.000 (8/8) | 1.000 identical | 0/8 → up to ±0.000 |
| F1 | 1.000 (16/16) | 1.000 (16/16) | 1.000 (16/16) | 1.000 identical | n/a — denominator is not a fixed class |
| raw agreement | 1.000 (24/24) | 1.000 (24/24) | 1.000 (24/24) | 1.000 identical | 0/24 → up to ±0.000 |
| prevalence of 'fail' | 0.333 (8/24) | 0.333 (8/24) | 0.333 (8/24) | 0.333 identical | 0/24 → up to ±0.000 |

No item changed verdict between runs: 1.000 (24/24) unanimous. Every band above is zero-width for the reason a reader would hope — nothing moved that could have moved a rate. Stability on this set is not a guarantee for unseen items, but an unstable judge would have shown it here.

The band is not a confidence interval and is never added to one. The Wilson interval beside each rate is sampling error over items, assuming the judge's answer per item is fixed; the band is the instrument moving on a fixed set of items. Both are printed, neither is combined, because no measurement here supports a combined distribution.

And the band is itself a noisy estimate: 3 replicates distinguish "unanimous" from "not unanimous" and very little else. A flip rate estimated from three draws carries enormous error, so treat this as a floor under the uncertainty rather than a measurement of it.

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
