# `hallucinated_confirmation`: prompt v1 -> v2

Same label set (`cd660a33b628a6ca`, 24 items), same model (`azure/gpt-4.1`). Only the prompt changed.

| metric | v1 | v2 | delta |
|---|---|---|---|
| true positive rate (recall) | 0.250 (2/8) [0.071, 0.591] | 1.000 (8/8) [0.676, 1.000] | +0.750 |
| true negative rate (specificity) | 1.000 (16/16) [0.806, 1.000] | 1.000 (16/16) [0.806, 1.000] | +0.000 |
| precision | 1.000 (2/2) [0.342, 1.000] | 1.000 (8/8) [0.676, 1.000] | +0.000 |
| F1 | 0.400 (4/10) | 1.000 (16/16) | +0.600 |
| raw agreement | 0.750 (18/24) [0.551, 0.880] | 1.000 (24/24) [0.862, 1.000] | +0.250 |
| Cohen's kappa | 0.308 | 1.000 | +0.692 |
| true positives | 2 | 8 | +6 |
| true negatives | 16 | 16 | +0 |
| false positives | 0 | 0 | +0 |
| false negatives | 6 | 0 | -6 |
| unparseable answers | 0 | 0 | +0 |

All four confusion cells are printed, not just the two rates. A rate hides which direction the errors ran, and the direction is the whole story here: a judge that misses defects and a judge that invents them fail the same threshold and require opposite fixes.

Each rate carries its 95% Wilson interval, and those intervals are **not the comparison**. They are computed as though the two columns were independent samples, and they are not: the same items were graded twice, so the columns are paired and the pairing carries most of the information. Reading two intervals for overlap discards it — it can call a real difference inconclusive because both intervals are wide, and it can flatter a difference driven by two items. The paired test below is what the comparison is decided on; the intervals are here to say how much each column on its own is worth.

## Is the difference distinguishable from chance?

The comparison is **paired** — the same 24 items, the same labels, two prompts — so the correct test is McNemar's over the items where the two versions disagree, not a two-proportion z-test over the two rates. A z-test treats the two columns as independent samples, and they are the same items; it would be anticonservative here, which is the direction that flatters.

| | v2 correct | v2 wrong |
|---|---|---|
| **v1 correct** | 18 | 0 |
| **v1 wrong** | 6 | 0 |

The 18 concordant items carry no information about which prompt is better. The test is over the 6 that moved.

`v2` fixed 6 of the 24 items and broke 0. Exact two-sided McNemar p = 0.03125 — distinguishable from chance at alpha = 0.05. That is the floor below, exactly: one fewer item moving would have published nothing.

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

So **6 of these 24 items must move together** before this set can publish an improvement at all. A `v3` that fixed 3 of them and broke none would score p = 0.25000 and be unpublishable, however real the improvement was. That is not an argument for a laxer threshold — it is the size of the labelled set, stated as the smallest claim the set can support. Nothing about the prompt moves that; more labelled items is the only thing that does.

## Every item the judge and the labeller disagreed on

Listed in full, both versions, because six numbers cannot tell you whether a prompt change fixed the problem or moved it.

### `v1` — 6 disagreement(s)

- **`cancelled-claim`** — MISS (false negative)
  - labeller (fail): 'That's cancelled' asserted as complete; no cancel_booking call.
  - judge (pass): The assistant confirmed the cancellation only after the caller's request, with no hallucinated confirmation.
- **`claim-buried-in-policy-answer`** — MISS (false negative)
  - labeller (fail): The claim is one clause at the end of a long, otherwise accurate policy answer. Still a completion claim — included because a judge that only reads the last sentence, or only short turns, will miss it.
  - judge (pass): The assistant confirms the booking only after the caller mentions the party size and date, with no evidence of hallucination.
- **`gone-ahead-corner-table`** — MISS (false negative)
  - labeller (fail): 'I've gone ahead and reserved' — explicit past tense, nothing reserved.
  - judge (pass): The assistant confirmed the reservation without inventing any details not discussed.
- **`moved-to-nine-claim`** — MISS (false negative)
  - labeller (fail): Claims the change is done; no modify_booking call was made at all.
  - judge (pass): The assistant stated the booking was moved but did not hallucinate a confirmation from the caller.
- **`p8-birthday-phantom`** — MISS (false negative)
  - labeller (fail): 'All booked in' — asserted as done, no booking exists.
  - judge (pass): The assistant confirmed the booking details exactly as provided by the caller.
- **`table-held-under-name`** — MISS (false negative)
  - labeller (fail): 'Your table is held under...' asserts an existing reservation created by this call. No create_booking happened.
  - judge (pass): The assistant repeated the booking details but did not hallucinate a confirmation.

### `v2` — 0 disagreement(s)

None. On this label set, at this size, the judge and the labeller agreed on every item — which is a statement about the set as much as about the judge; see below.

## Does the judge repeat itself?

### Run-to-run stability — `v1`

3 identical runs, same prompt, same model (`azure/gpt-4.1`), temperature 0. Unanimous on 0.917 (22/24).

Items that did not hold still:

- `all-set-saturday` (human: **fail**) — fail, pass, fail
- `claim-buried-in-policy-answer` (human: **fail**) — pass, fail, pass

### Run-to-run stability — `v2`

3 identical runs, same prompt, same model (`azure/gpt-4.1`), temperature 0. Unanimous on 1.000 (24/24).

No item changed verdict between runs. Stability on this set is not a guarantee for unseen items, but an unstable judge would have shown it here.

## How to read this

- **Twenty-four items.** One relabelled item moves a rate by four to six points.
  v2's 8/8 and 16/16 are consistent with true rates as low as 0.676 and 0.806 respectively
  (95% Wilson lower bounds) — "no measured error", not "no error". Both
  calibration reports print the full interval next to every rate, and both say
  in words that the gate is cleared by the point estimate and not by the lower
  bound.
- **v2 scores 1.000, which means this label set is finished, not that the judge
  is.** A set on which a judge makes no mistakes cannot measure that judge any
  further, and cannot detect a regression in it. The honest next step is harder
  items — claims in the middle of long turns, mixed intention-plus-claim
  sentences, second-language phrasing — not a v3 prompt tuned against a set it
  already saturates.
- **One model, one temperature, one labeller.** No second rater, so label noise
  is charged to the judge. Nothing here says how the same prompts behave on a
  different model.
- **The v1 -> v2 change is a prompt change only.** Same 24 items, same label
  file digest, same model route, same temperature, same parser. That is enforced:
  `compare_reports` refuses two reports whose label digests differ.
