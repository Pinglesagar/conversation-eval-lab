# Judge calibration — `roleplay_rubric_scorer` prompt `v1`

- Model: `azure/gpt-4.1`
- Prompt sha256: `9dff621e69ee42a5`
- Label set sha256: `3bf9c1b846b078e2` (27 items)
- Positive class: a judge verdict of **fail** (prevalence 0.556 (15/27))

## Confusion matrix

| | human: fail | human: pass |
|---|---|---|
| **judge: fail** | TP 9 | FP 0 |
| **judge: pass** | FN 6 | TN 12 |

## Rates

| metric | value | numerator / denominator | 95% Wilson CI | across 3 runs |
|---|---|---|---|---|
| true positive rate (recall) | 0.600 | 9 / 15 | [0.357, 0.802] | 0.600 identical |
| true negative rate (specificity) | 1.000 | 12 / 12 | [0.758, 1.000] | 1.000 identical |
| precision | 1.000 | 9 / 9 | [0.701, 1.000] | 1.000 identical |
| recall | 0.600 | 9 / 15 | [0.357, 0.802] | 0.600 identical |
| F1 | 0.750 | 18 / 24 | not a proportion | 0.750 identical |
| raw agreement | 0.778 | 21 / 27 | [0.592, 0.894] | 0.778 identical |
| prevalence of 'fail' | 0.556 | 15 / 27 | [0.373, 0.724] | 0.556 identical |
| Cohen's kappa | 0.571 | observed 0.778, chance 0.481 | not a proportion | not measured |

Raw agreement is reported next to kappa deliberately: raw agreement flatters a judge on imbalanced data, because always answering with the majority class scores the majority fraction. Kappa subtracts the agreement two graders with these marginals would reach by chance.

The interval is the Wilson score interval at 95%, computed from the two counts in the row beside it and from nothing else, so a reader can recheck it. It is sampling error over items only: it assumes the judge would give the same answer on a second run, which is a separate question with a separate measurement. No interval is given for Cohen's kappa or for F1 — neither is a proportion of independent trials, and a binomial interval on either would be arithmetic applied to the wrong quantity. Precision is the one to read with care: its denominator is the judge's own positive count rather than a class the label set fixed, so its interval is conditional on that count.

## The interval, and which number the gate is standing on

Gate: TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%, scored on the point estimate.

| gated rate | point estimate | 95% Wilson CI | clears on the point? | clears on the lower bound? |
|---|---|---|---|---|
| TPR >= 0.85 | 0.600 (9/15) | [0.357, 0.802] | **no** | **no** |
| TNR >= 0.85 | 1.000 (12/12) | [0.758, 1.000] | yes | **no** |

Rule of three, the same fact in the form that is easier to hold on to:

- true negative rate (specificity): 0 errors in 12, so the 95% upper bound on the true error rate is about 3/12 = 0.250

This report was scored on the point estimate. `CalibrationThresholds(gate_on='wilson_lower')` scores the lower bound instead; it is not the default because at these set sizes it fails every judge in this repository, none of which regressed — see the class docstring.

## The band this instrument moved through — `v1`

3 identical runs, same prompt, same model (`azure/gpt-4.1`), temperature 0. Every rate this study publishes is computed from run 1, because a product makes one call per item and a figure averaged over three runs describes an instrument nobody deployed. These are the same rates recomputed from each recorded run.

| rate | run 1 | run 2 | run 3 | band across runs | items in its denominator that moved |
|---|---|---|---|---|---|
| true positive rate (recall) | 0.600 (9/15) | 0.600 (9/15) | 0.600 (9/15) | 0.600 identical | 0/15 → up to ±0.000 |
| true negative rate (specificity) | 1.000 (12/12) | 1.000 (12/12) | 1.000 (12/12) | 1.000 identical | 0/12 → up to ±0.000 |
| precision | 1.000 (9/9) | 1.000 (9/9) | 1.000 (9/9) | 1.000 identical | n/a — denominator is not a fixed class |
| recall | 0.600 (9/15) | 0.600 (9/15) | 0.600 (9/15) | 0.600 identical | 0/15 → up to ±0.000 |
| F1 | 0.750 (18/24) | 0.750 (18/24) | 0.750 (18/24) | 0.750 identical | n/a — denominator is not a fixed class |
| raw agreement | 0.778 (21/27) | 0.778 (21/27) | 0.778 (21/27) | 0.778 identical | 0/27 → up to ±0.000 |
| prevalence of 'fail' | 0.556 (15/27) | 0.556 (15/27) | 0.556 (15/27) | 0.556 identical | 0/27 → up to ±0.000 |

No item changed verdict between runs: 1.000 (27/27) unanimous. Every band above is zero-width for the reason a reader would hope — nothing moved that could have moved a rate. Stability on this set is not a guarantee for unseen items, but an unstable judge would have shown it here.

The band is not a confidence interval and is never added to one. The Wilson interval beside each rate is sampling error over items, assuming the judge's answer per item is fixed; the band is the instrument moving on a fixed set of items. Both are printed, neither is combined, because no measurement here supports a combined distribution.

And the band is itself a noisy estimate: 3 replicates distinguish "unanimous" from "not unanimous" and very little else. A flip rate estimated from three draws carries enormous error, so treat this as a floor under the uncertainty rather than a measurement of it.

## Disagreements

False negatives first: a missed defect is silent, a false alarm announces itself to whoever reads the report.

### `label-amer-conflict-never-declared` — false negative

- human: **fail** — R1: amer-retail requires 3 disclosure(s) and 1 of them was never recorded: conflict_of_interest
- judge: **pass** — The trainee excelled in all assessed areas. Discovery was thorough, with open questions about the customer's goals and liquidity needs. Every objection raised by the customer was directly addressed and resolved. All mandatory disclosures required for the amer-retail jurisdiction were recorded in the tool ledger. The trainee stayed within the boundaries of product features and customer needs, avoiding any personal recommendation. The session concluded with a clear summary and an explicit ask for the business, meeting the closing standard.
- judge quoted: “caller: What would you want this money to be doing for you in ten years?”

### `label-apac-suitability-never-completed` — false negative

- human: **fail** — R1: apac-retail requires 4 disclosure(s) and 1 of them was never recorded: product_suitability
- judge: **pass** — The trainee conducted a thorough discovery by asking open questions about the customer's goals and liquidity needs before describing the product. All objections raised by the customer were directly addressed and resolved. The mandatory disclosures for the APAC-retail jurisdiction—capital at risk, past performance, and fees—were all recorded as given. There was no evidence of unlicensed advice or a personal recommendation; the trainee stayed within the boundaries of product features and customer needs. The session concluded with a clear summary and an explicit ask for the business, meeting all closing requirements.
- judge quoted: “caller: What would you want this money to be doing for you in ten years?”

### `label-es-fees-disclosure-omitted` — false negative

- human: **fail** — R1: eu-retail requires 3 disclosure(s) and 1 of them was never recorded: fees_and_charges
- judge: **pass** — The trainee began with an open discovery question to establish the customer's goals, handled the customer's objection about past losses directly, and provided all mandatory disclosures as recorded in the tool ledger. There was no evidence of unlicensed advice or a compliance flag. The closing included an invitation to proceed but lacked a full summary of what was agreed, which slightly reduced the closing score.
- judge quoted: “caller: Que le gustaria conseguir con estos ahorros dentro de diez anos?”

### `locale-crossmarket-commission-script-in-apac-market` — false negative

- human: **fail** — R1: apac-retail requires 4 disclosure(s) and 2 of them was never recorded: past_performance, product_suitability
- judge: **pass** — The trainee excelled in all assessed areas. Discovery was thorough, with multiple open questions used to elicit the customer's needs and constraints before any product description. Every objection raised by the customer was directly addressed and resolved. All mandatory disclosures required for the apac-retail jurisdiction—capital at risk, fees and charges, and commission—were explicitly made and recorded in the tool ledger. The trainee stayed within the boundaries of product features and customer needs, avoiding any personal recommendation. The session concluded with a clear summary of disclosures and an explicit ask for the business, meeting the closing standard.
- judge quoted: “Before I show you anything, what would you want this money to be doing for you in ten years?”

### `locale-parity-baseline-in-amer-market` — false negative

- human: **fail** — R1: amer-retail requires 3 disclosure(s) and 1 of them was never recorded: conflict_of_interest
- judge: **pass** — The trainee excelled across all assessed areas. Discovery was thorough, with multiple open questions used to elicit the customer's needs and constraints before any product description. Every objection raised by the customer was directly addressed and resolved, as shown in both the transcript and tool ledger. All mandatory disclosures required for the amer-retail jurisdiction were made and recorded. The trainee stayed within the boundaries of product features and customer needs, avoiding any personal recommendation. The session concluded with a clear summary and an explicit ask for the business, meeting the closing standard.
- judge quoted: “Before I show you anything, what would you want this money to be doing for you in ten years?”

### `locale-parity-baseline-in-apac-market` — false negative

- human: **fail** — R1: apac-retail requires 4 disclosure(s) and 1 of them was never recorded: product_suitability
- judge: **pass** — The trainee conducted a thorough discovery using open questions to understand the customer's needs and constraints before describing the product. All objections raised by the customer were directly addressed and resolved. The mandatory disclosures for the apac-retail jurisdiction—capital at risk, past performance, and fees and charges—were all recorded in the session's disclosure register. The trainee stayed within the boundaries of product features and customer needs, avoiding any personal recommendation. The session concluded with a clear summary and an explicit ask for the business, meeting all closing requirements.
- judge quoted: “caller: Before I show you anything, what would you want this money to be doing for you in ten years?”

## Notes

- Positive class is a judge verdict of 'fail'; the negative class is 'pass'. TPR is recall on the positive class, TNR is specificity. Both are gated: a constant answer maximises one of them.
- Raw agreement is reported alongside Cohen's kappa because raw agreement flatters a judge on imbalanced data — always answering with the majority class scores the majority fraction, with zero discrimination.
- Kappa is prevalence dependent and therefore not comparable across label sets with different class balance; TPR and TNR are, which is why the registry gates on those.
- Every rate is printed with its numerator and denominator so a reader can see how few items a figure rests on.
- Verdicts came from 'azure/gpt-4.1' via prompt v1 (sha256 9dff621e69ee); a prompt edit invalidates this report.
- The instrument under measurement is a real model grading each session against roleplay/rubric_v1.md and returning five per-criterion scores, a verdict, a critique and a quoted span. The scripted scorer in roleplay/scorer.py is a different instrument and is measured separately.
- The positive class is 'fail': a scorer is a defect detector, and recall on the sessions a competent reviewer would stop is the figure that matters.
- Labels are derived by rule from each session's own ledgers — the disclosure register the product wrote and the compliance flags its own flagger raised — not written by hand next to the session. See roleplay/labels.py for the four rules.
- 27 items, 15 of them labelled 'fail'. Items the rules could not settle were excluded rather than guessed; the exclusion list and the reason for each is printed with this report.
- Every item is graded by one call, as a product would. Runs 2 and 3 of the recording measure the instrument's own variance and are never averaged in.
