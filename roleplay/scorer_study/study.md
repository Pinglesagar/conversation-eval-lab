# Calibrating a live rubric scorer against a labelled set

Every number below is recomputed from the committed recordings in this directory by `python -m roleplay.scorer_study`. Nothing is typed in by hand, and a re-run on a clean checkout reproduces the file byte for byte.

## The labelled set

27 items in the metrics. Labels are derived by rule from each session's own ledgers — see `roleplay/labels.py` for the four rules — and not written by hand alongside the session.

```
7 item(s) excluded from the metrics:
  pitch-cold-scorer-single-run-control [corpus]
      AMBIGUOUS by R4: compliant on every declared fact, but the business was never asked for — the rubric scores both out of 4 rather than making either dispositive, so two competent reviewers may reasonably differ. The corpus reviewer called it 'pass'; no declared fact settles it, so it is excluded rather than resolved in either direction.
  objection-lock-in-left-unanswered [corpus]
      AMBIGUOUS by R4: compliant on every declared fact, but the business was never asked for and 2 objection(s) left unresolved — the rubric scores both out of 4 rather than making either dispositive, so two competent reviewers may reasonably differ. The corpus reviewer called it 'fail'; no declared fact settles it, so it is excluded rather than resolved in either direction.
  consistency-borderline-transcript-warm-k5 [corpus]
      AMBIGUOUS by R4: compliant on every declared fact, but the business was never asked for and 1 objection(s) left unresolved — the rubric scores both out of 4 rather than making either dispositive, so two competent reviewers may reasonably differ. The corpus reviewer called it 'pass'; no declared fact settles it, so it is excluded rather than resolved in either direction.
  consistency-identical-transcript-warm-k5 [corpus]
      AMBIGUOUS by R4: compliant on every declared fact, but the business was never asked for — the rubric scores both out of 4 rather than making either dispositive, so two competent reviewers may reasonably differ. The corpus reviewer called it 'pass'; no declared fact settles it, so it is excluded rather than resolved in either direction.
  locale-apac-suitability-disclosure [corpus]
      AMBIGUOUS by R4: compliant on every declared fact, but the business was never asked for — the rubric scores both out of 4 rather than making either dispositive, so two competent reviewers may reasonably differ. The corpus reviewer called it 'pass'; no declared fact settles it, so it is excluded rather than resolved in either direction.
  locale-es-mx-registered-spanish-disclosure [corpus]
      AMBIGUOUS by R4: compliant on every declared fact, but the business was never asked for — the rubric scores both out of 4 rather than making either dispositive, so two competent reviewers may reasonably differ. The corpus reviewer called it 'pass'; no declared fact settles it, so it is excluded rather than resolved in either direction.
  label-cautious-register-complete-no-ask [pack]
      AMBIGUOUS by R4: compliant on every declared fact, but the business was never asked for — the rubric scores both out of 4 rather than making either dispositive, so two competent reviewers may reasonably differ
```

Excluded items were not sent to the model. An item that cannot enter a metric cannot inform it, and paying for a verdict on a session whose correct answer nobody can state would produce a number with no reference to compare it against.

## Rubric `v1`

```
Judge calibration: roleplay_rubric_scorer v1
  model            : azure/gpt-4.1
  prompt sha256    : 9dff621e69ee
  labels sha256    : 3bf9c1b846b0
  positive class   : judge says 'fail'

                     human: fail     human: pass
     judge: fail            TP 9            FP 0
     judge: pass            FN 6           TN 12

  true positive rate (recall)      : 0.600 (9/15)   95% CI [0.357, 0.802]   3 runs 0.600 identical
  true negative rate (specificity) : 1.000 (12/12)  95% CI [0.758, 1.000]   3 runs 1.000 identical
  precision                        : 1.000 (9/9)    95% CI [0.701, 1.000]   3 runs 1.000 identical
  recall                           : 0.600 (9/15)   95% CI [0.357, 0.802]   3 runs 0.600 identical
  F1                               : 0.750 (18/24)  no interval — not a proportion   3 runs 0.750 identical
  raw agreement                    : 0.778 (21/27)  95% CI [0.592, 0.894]   3 runs 0.778 identical
  prevalence of 'fail'             : 0.556 (15/27)  95% CI [0.373, 0.724]   3 runs 0.556 identical
  Cohen kappa                      : 0.571
                                     (observed 0.778, expected by chance 0.481)

  6 disagreement(s):
    [false_negative] label-amer-conflict-never-declared: human=fail judge=pass
    [false_negative] label-apac-suitability-never-completed: human=fail judge=pass
    [false_negative] label-es-fees-disclosure-omitted: human=fail judge=pass
    [false_negative] locale-crossmarket-commission-script-in-apac-market: human=fail judge=pass
    [false_negative] locale-parity-baseline-in-amer-market: human=fail judge=pass
    [false_negative] locale-parity-baseline-in-apac-market: human=fail judge=pass
```

#### Would a second run have produced the same table?

| run | TPR | TNR | precision | kappa | raw agreement | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.600 (9/15) | 1.000 (12/12) | 1.000 (9/9) | 0.571 | 0.778 (21/27) | 9 | 0 | 6 | 12 |
| 2 | 0.600 (9/15) | 1.000 (12/12) | 1.000 (9/9) | 0.571 | 0.778 (21/27) | 9 | 0 | 6 | 12 |
| 3 | 0.600 (9/15) | 1.000 (12/12) | 1.000 (9/9) | 0.571 | 0.778 (21/27) | 9 | 0 | 6 | 12 |

All 3 runs produce an identical confusion matrix. The table is reproducible — which is a statement about the table and not about the score cards behind it; see the card-stability section, where cards moved on items the table cannot distinguish.

### Score cards — `v1`

| measure | value | denominator |
|---|---|---|
| cards that could not be parsed (ERRORED) | 0 | 27 |
| verdict `fail` over a total at or above 14 | 4 | 27 | 
| verdict `pass` under a total of 14 | 0 | 27 |
| `mandatory_disclosure` agrees with the register | 21/27 (0.778) | items scored |
| `no_unlicensed_advice` agrees with the flagger | 24/27 (0.889) | items scored |

A `fail` over a passing total is the rubric working: a missing disclosure fails the session whatever it totals. A `pass` under the threshold has no licence in the rubric at all, and is counted separately for that reason.

- R2 items carry a flag by definition, and R1 items are the ones with a missing code, so the expectations here are the labelling rules read backwards rather than a second opinion about the same sessions.

### Run-to-run stability of the score card — `v1`

3 identical runs, same rubric, same model (`azure/gpt-4.1`), temperature 0, 27 items.

| measure | value | denominator |
|---|---|---|
| items fully stable (verdict, total and all five criteria) | 21 | 27 |
| items whose verdict moved | 0 | 27 |
| items whose numbers moved but verdict held | 6 | 27 |
| cohort mean total, per run | 16.593, 16.37, 16.37 | out of 20 |
| cohort pass count, per run | 18, 18, 18 | 27 |
| spread of per-item total spreads (population sd) | 0.516 | points |

Which criteria moved, and on how many items:

- `closing` — 6/27 items
- `discovery` — 1/27 items

Items that did not hold still:

- locale-amer-past-performance-not-required-here (human: pass) verdicts pass/pass/pass totals [20, 19, 20] -- moved: closing [4, 3, 4] (spread 1)
- locale-crossmarket-commission-script-in-apac-market (human: fail) verdicts pass/pass/pass totals [20, 19, 19] -- moved: closing [4, 3, 3] (spread 1)
- label-aggressive-both-objections-answered (human: pass) verdicts pass/pass/pass totals [18, 17, 17] -- moved: closing [3, 2, 2] (spread 1)
- label-es-fees-disclosure-omitted (human: fail) verdicts pass/pass/pass totals [19, 17, 17] -- moved: closing [3, 2, 2] (spread 1), discovery [4, 3, 3] (spread 1)
- label-amer-conflict-never-declared (human: fail) verdicts pass/pass/pass totals [20, 20, 19] -- moved: closing [4, 4, 3] (spread 1)
- label-advice-adviser-own-money (human: fail) verdicts fail/fail/fail totals [16, 15, 15] -- moved: closing [4, 3, 3] (spread 1)

### Run-to-run stability — `v1`

3 identical runs, same prompt, same model (`azure/gpt-4.1`), temperature 0. Unanimous on 1.000 (27/27).

No item changed verdict between runs. Stability on this set is not a guarantee for unseen items, but an unstable judge would have shown it here.

**Gate (TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%, scored on the point estimate): FAIL**

- TPR 0.600 (9/15) is below the required 0.85
- registry refused this judge in CI mode: JudgeBelowThresholdError

## The interval, and which number the gate is standing on

Gate: TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%, scored on the point estimate.

| gated rate | point estimate | 95% Wilson CI | clears on the point? | clears on the lower bound? |
|---|---|---|---|---|
| TPR >= 0.85 | 0.600 (9/15) | [0.357, 0.802] | **no** | **no** |
| TNR >= 0.85 | 1.000 (12/12) | [0.758, 1.000] | yes | **no** |

Rule of three, the same fact in the form that is easier to hold on to:

- true negative rate (specificity): 0 errors in 12, so the 95% upper bound on the true error rate is about 3/12 = 0.250

This report was scored on the point estimate. `CalibrationThresholds(gate_on='wilson_lower')` scores the lower bound instead; it is not the default because at these set sizes it fails every judge in this repository, none of which regressed — see the class docstring.

## Rubric `v2`

```
Judge calibration: roleplay_rubric_scorer v2
  model            : azure/gpt-4.1
  prompt sha256    : aab09aecf015
  labels sha256    : 3bf9c1b846b0
  positive class   : judge says 'fail'

                     human: fail     human: pass
     judge: fail           TP 15            FP 0
     judge: pass            FN 0           TN 12

  true positive rate (recall)      : 1.000 (15/15)  95% CI [0.796, 1.000]   3 runs 1.000 identical
  true negative rate (specificity) : 1.000 (12/12)  95% CI [0.758, 1.000]   3 runs 0.917–1.000
  precision                        : 1.000 (15/15)  95% CI [0.796, 1.000]   3 runs 0.938–1.000
  recall                           : 1.000 (15/15)  95% CI [0.796, 1.000]   3 runs 1.000 identical
  F1                               : 1.000 (30/30)  no interval — not a proportion   3 runs 0.968–1.000
  raw agreement                    : 1.000 (27/27)  95% CI [0.875, 1.000]   3 runs 0.963–1.000
  prevalence of 'fail'             : 0.556 (15/27)  95% CI [0.373, 0.724]   3 runs 0.556 identical
  Cohen kappa                      : 1.000
                                     (observed 1.000, expected by chance 0.506)
```

#### Would a second run have produced the same table?

| run | TPR | TNR | precision | kappa | raw agreement | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.000 (15/15) | 1.000 (12/12) | 1.000 (15/15) | 1.000 | 1.000 (27/27) | 15 | 0 | 0 | 12 |
| 2 | 1.000 (15/15) | 1.000 (12/12) | 1.000 (15/15) | 1.000 | 1.000 (27/27) | 15 | 0 | 0 | 12 |
| 3 | 1.000 (15/15) | 0.917 (11/12) | 0.938 (15/16) | 0.924 | 0.963 (26/27) | 15 | 1 | 0 | 11 |

**The table is not reproducible.** 2 different confusion matrices came out of 3 identical runs, so any figure quoted from a single run — including the one printed above, which is run 1 — is a sample rather than a property of the instrument. A prompt comparison whose delta is smaller than this spread is measuring noise.

### Score cards — `v2`

| measure | value | denominator |
|---|---|---|
| cards that could not be parsed (ERRORED) | 0 | 27 |
| verdict `fail` over a total at or above 14 | 6 | 27 | 
| verdict `pass` under a total of 14 | 0 | 27 |
| `mandatory_disclosure` agrees with the register | 22/27 (0.815) | items scored |
| `no_unlicensed_advice` agrees with the flagger | 24/27 (0.889) | items scored |

A `fail` over a passing total is the rubric working: a missing disclosure fails the session whatever it totals. A `pass` under the threshold has no licence in the rubric at all, and is counted separately for that reason.

- R2 items carry a flag by definition, and R1 items are the ones with a missing code, so the expectations here are the labelling rules read backwards rather than a second opinion about the same sessions.

### Run-to-run stability of the score card — `v2`

3 identical runs, same rubric, same model (`azure/gpt-4.1`), temperature 0, 27 items.

| measure | value | denominator |
|---|---|---|
| items fully stable (verdict, total and all five criteria) | 21 | 27 |
| items whose verdict moved | 1 | 27 |
| items whose numbers moved but verdict held | 5 | 27 |
| cohort mean total, per run | 14.556, 14.667, 14.37 | out of 20 |
| cohort pass count, per run | 12, 12, 11 | 27 |
| spread of per-item total spreads (population sd) | 0.953 | points |

Which criteria moved, and on how many items:

- `discovery` — 3/27 items
- `mandatory_disclosure` — 2/27 items
- `closing` — 2/27 items

Items that did not hold still:

- pitch-terse-customer-patient-probing (human: pass) verdicts pass/pass/pass totals [17, 16, 16] -- moved: discovery [2, 1, 1] (spread 1)
- compliance-explicit-unlicensed-advice (human: fail) verdicts fail/fail/fail totals [10, 14, 10] -- moved: mandatory_disclosure [0, 4, 0] (spread 4)
- objection-praise-for-unasked-question (human: pass) verdicts pass/pass/pass totals [17, 18, 18] -- moved: discovery [2, 3, 3] (spread 1)
- label-aggressive-both-objections-answered (human: pass) verdicts pass/pass/fail totals [19, 19, 16] -- moved: closing [3, 3, 4] (spread 1), mandatory_disclosure [4, 4, 0] (spread 4)
- label-advice-if-i-were-you (human: fail) verdicts fail/fail/fail totals [11, 10, 10] -- moved: closing [4, 3, 3] (spread 1)
- label-advice-right-fund-for-you (human: fail) verdicts fail/fail/fail totals [11, 11, 10] -- moved: discovery [4, 4, 3] (spread 1)

### Run-to-run stability — `v2`

3 identical runs, same prompt, same model (`azure/gpt-4.1`), temperature 0. Unanimous on 0.963 (26/27).

Items that did not hold still:

- `label-aggressive-both-objections-answered` (human: **pass**) — pass, pass, fail

**Gate (TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%, scored on the point estimate): PASS**


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

## Did v2 beat v1?

# `roleplay_rubric_scorer`: prompt v1 -> v2

Same label set (`3bf9c1b846b078e2`, 27 items), same model (`azure/gpt-4.1`). Only the prompt changed.

| metric | v1 | v2 | delta |
|---|---|---|---|
| true positive rate (recall) | 0.600 (9/15) [0.357, 0.802] | 1.000 (15/15) [0.796, 1.000] | +0.400 |
| true negative rate (specificity) | 1.000 (12/12) [0.758, 1.000] | 1.000 (12/12) [0.758, 1.000] | +0.000 |
| precision | 1.000 (9/9) [0.701, 1.000] | 1.000 (15/15) [0.796, 1.000] | +0.000 |
| F1 | 0.750 (18/24) | 1.000 (30/30) | +0.250 |
| raw agreement | 0.778 (21/27) [0.592, 0.894] | 1.000 (27/27) [0.875, 1.000] | +0.222 |
| Cohen's kappa | 0.571 | 1.000 | +0.429 |
| true positives | 9 | 15 | +6 |
| true negatives | 12 | 12 | +0 |
| false positives | 0 | 0 | +0 |
| false negatives | 6 | 0 | -6 |
| unparseable answers | 0 | 0 | +0 |

All four confusion cells are printed, not just the two rates. A rate hides which direction the errors ran, and the direction is the whole story here: a judge that misses defects and a judge that invents them fail the same threshold and require opposite fixes.

Each rate carries its 95% Wilson interval, and those intervals are **not the comparison**. They are computed as though the two columns were independent samples, and they are not: the same items were graded twice, so the columns are paired and the pairing carries most of the information. Reading two intervals for overlap discards it — it can call a real difference inconclusive because both intervals are wide, and it can flatter a difference driven by two items. The paired test below is what the comparison is decided on; the intervals are here to say how much each column on its own is worth.

### Is the delta bigger than the instrument's own movement?

The second guard, and it is not the same question as the one below. McNemar asks whether a difference is distinguishable from chance given these items. This asks whether it is distinguishable from the judge disagreeing with *itself*: both versions were run three times over the same items, and the floor below is how far each version's unstable items could move each rate.

| rate | delta | floor from run-to-run instability | bigger than the floor? |
|---|---|---|---|
| true positive rate (recall) | +0.400 | ±0.000 | **yes** |
| true negative rate (specificity) | +0.000 | ±0.083 | no change |
| raw agreement | +0.222 | ±0.037 | **yes** |

Every rate that changed changed by more than the instrument's own measured movement, so none of the deltas above is the judge disagreeing with itself.

The floor is a worst case rather than the observed spread between runs. Zero observed movement can mean two very different things — nothing moved, or things moved and cancelled — and only the worst case distinguishes them. Here no item cancelled, so on this comparison the two would have agreed.

## Is the difference distinguishable from chance?

The comparison is **paired** — the same 27 items, the same labels, two prompts — so the correct test is McNemar's over the items where the two versions disagree, not a two-proportion z-test over the two rates. A z-test treats the two columns as independent samples, and they are the same items; it would be anticonservative here, which is the direction that flatters.

| | v2 correct | v2 wrong |
|---|---|---|
| **v1 correct** | 21 | 0 |
| **v1 wrong** | 6 | 0 |

The 21 concordant items carry no information about which prompt is better. The test is over the 6 that moved.

`v2` fixed 6 of the 27 items and broke 0. Exact two-sided McNemar p = 0.03125 — distinguishable from chance at alpha = 0.05. That is the floor below, exactly: one fewer item moving would have published nothing.

### The detectability floor on this set: 6 items

The half of this section that survives the next prompt change. With `d` discordant pairs **all pointing the same way**, the exact two-sided p is `2/2**d` — a function of `d` alone, not of the set size and not of either version's accuracy. So there is a hard floor under every paired comparison, and at alpha = 0.05 it is 6:

| discordant pairs, all one way | exact two-sided p | publishable |
|---|---|---|
| 1 | 1.00000 | no |
| 2 | 0.50000 | no |
| 3 | 0.25000 | no |
| 4 | 0.12500 | no |
| 5 | 0.06250 | no |
| 6 | 0.03125 | yes |
| 7 | 0.01562 | yes |

So **6 of these 27 items must move together** before this set can publish an improvement at all. A `v3` that fixed 3 of them and broke none would score p = 0.25000 and be unpublishable, however real the improvement was. That is not an argument for a laxer threshold — it is the size of the labelled set, stated as the smallest claim the set can support. Nothing about the prompt moves that; more labelled items is the only thing that does.


## Pointing the live scorer at the seeded defects

```
DEFECT-3 compliance-missing-risk-disclosure: rule=fail scripted=pass live=fail (16/20) CAUGHT [beat the scripted scorer]
DEFECT-3 compliance-no-real-risk-reassurance: rule=fail scripted=pass live=fail (12/20) CAUGHT [beat the scripted scorer]
DEFECT-3 compliance-explicit-unlicensed-advice: rule=fail scripted=pass live=fail (14/20) CAUGHT [beat the scripted scorer]
DEFECT-3 locale-es-mx-registered-spanish-disclosure: rule=ambiguous scripted=fail live=fail (12/20) NOT SCORED (rule could not settle this session)
DEFECT-2 pitch-terse-customer-patient-probing: rule=pass scripted=pass live=pass (20/20) CAUGHT
DEFECT-2 objection-praise-for-unasked-question: rule=pass scripted=pass live=pass (19/20) CAUGHT
DEFECT-2 pitch-feature-dump-no-discovery: rule=fail scripted=fail live=fail (4/20) CAUGHT
DEFECT-2 objection-lock-in-left-unanswered: rule=ambiguous scripted=fail live=fail (12/20) NOT SCORED (rule could not settle this session)
```
