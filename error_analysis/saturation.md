# Saturation — when did reading stop teaching me anything?

Honest answer: **it had not, when I stopped.** The thirteenth failure mode
appeared in the forty-seventh and last trace I read. This file exists to say that
plainly, and to show the shape of the curve, because "we read traces until we
stopped finding new things" is a claim people make and almost nobody plots.

## The discovery curve

Traces were read in the order recorded in `open_coding.md` — the happy suite,
then edge, then adversarial. Only the rows where the count of distinct modes
changed are listed; the full order is reproducible from that file.

| trace # | trace | distinct modes | new mode |
| --- | --- | --- | --- |
| 1–7 | happy rows | 0 | — |
| 8 | happy-move-booking-later | 1 | WHOLE-RECORD-SENT-AS-CHANGE-SET |
| 12 | happy-read-back-confirmation | 2 | READBACK-AFTER-COMMIT |
| 13 | happy-saturday-lunch-four | 3 | VALUE-FORM-MISMATCH |
| 14–19 | rest of happy, start of edge | 3 | — |
| 20 | edge-correction-during-read-back | 5 | PHANTOM-CONFIRMATION, SLOT-MISREAD-AS-HEADCOUNT |
| 22 | edge-multi-intent-first-turn | 6 | SIGN-OFF-CONSUMED-BY-PENDING-QUESTION |
| 23 | edge-three-facts-one-turn | 7 | NOTE-LOST-AT-HANDOFF |
| 28 | edge-modification-after-booking | 8 | RE-ASK-AFTER-HANDOFF |
| 30 | edge-modify-across-group-threshold | 9 | UNPARSED-CHANGE-REPORTED-AS-NO-CHANGE |
| 31 | edge-no-availability-at-requested-time | 10 | NO-CLOSE-AFTER-TERMINAL-TURN |
| 32–37 | rest of edge, start of adversarial | 10 | — |
| 38 | adversarial-injection-in-dietary-note | 11 | NEGATED-REQUIREMENT-DROPPED |
| 39 | adversarial-disclosure-asks-for-instructions | 12 | NON-ENGAGEMENT-INSTEAD-OF-REFUSAL |
| 40–46 | rest of adversarial | 12 | — |
| 47 | adversarial-injection-fake-system-turn | 13 | NAME-INITIAL-DROPPED |

Reproduce the curve from the committed coding:

```bash
python -m error_analysis.pareto            # counts and the chart
```

## What the shape says

**Three flat stretches, and each one ends at a suite boundary.** Traces 14–19
looked like saturation — six consecutive traces, nothing new — and then the first
correction row produced two modes at once. Traces 32–37 looked like saturation
again, and then the first adversarial row with an injected instruction produced
another. Whatever the curve was flattening towards, it was the exhaustion of one
*kind* of call, not of the system's ways of failing. Sampling from a corpus that
is deliberately stratified by suite means the curve resets every time the
stratum changes, and reading the suites in order is the worst possible order for
telling saturation from a lull.

**Severity saturated much earlier than novelty.** Every mode I would block a
release on was visible by trace 31, and the two most serious (a spoken
confirmation with nothing behind it; a dietary requirement lost at a handoff)
by trace 26. The four modes discovered after trace 31 are one masked parser
defect, one dropped initial, one negated-phrasing loss and one whole class of
non-engagement. Only the last of those is important, and it is important for a
reason novelty counting cannot see: it is the largest mode in the corpus by
occurrence count, and it turned up late because it lives entirely in the
adversarial suite.

**The late arrivals were also the cheapest to find.** Nothing after trace 30
required a diff of two traces or a probe of the parser; each was visible in one
read of one transcript. If I had been sampling for cost rather than for coverage
I would have read the adversarial rows first.

## What I would do next, in order

1. **Read the eight voice rows.** They are the only stratum with no coverage at
   all here, and by the argument above a new stratum is exactly where the curve
   will move. They need the audio path rather than the text adapter.
2. **Re-read the five NON-ENGAGEMENT traces as a set**, not one at a time. Every
   one of them passes, and the interesting question — is this refusal or is it
   deafness — is answerable only by comparing them against the one row that
   refuses properly.
3. **Stop reading and write three checks.** Codes 6, 11 and 12 in
   `axial_coding.md` are one line of scenario YAML each; codes 1, 2 and 3 need a
   new contract shape. Six of the thirteen modes have a named fix. Reading more
   traces before those exist mostly re-finds what is already written down.
4. **Then sample differently.** The next pass should be stratified randomly
   across suites rather than read in suite order, so that a flat stretch means
   something.

## The honest limit

This is 47 traces of one build of one synthetic system, coded by one person with
no second rater. Every count in `codes.csv` is a judgement I made once, and the
two codes I withdrew (see the end of `open_coding.md`) are evidence that some of
the ones I kept are wrong too. What I would defend is the direction of the
argument — 9 of 31 product occurrences were caught by the suite — rather than the
third significant figure of any number in it.
