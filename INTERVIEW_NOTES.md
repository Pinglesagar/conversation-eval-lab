# Interview notes

What each capability in this repository is *for*, in the form of the question it
answers. The answers are three lines each because that is roughly how long an
answer should be before the other person gets to ask the next thing; the longer
versions are below the table, and the code is the real answer in every case.

| capability | the question it answers | the answer |
| --- | --- | --- |
| Judge calibration and the registry gate (`lab/judges/`) | **How do you know your judge is any good?** | I measure it against hand labels and report TPR and TNR separately, with their counts, never a single accuracy figure. A judge with no calibration cannot be rendered — `JudgeSummary` requires a `JudgeCalibration` — and in CI the registry refuses to let it gate a build. The worked example moved TNR from 10/16 to 15/16 on the same 24 labels by changing only the prompt. |
| `pass^k` and the baseline gate (`lab/simulator/passk.py`, `lab/cli.py`) | **How do you handle non-determinism?** | Run every scenario k times and make stability a dimension of the verdict: `FLAKY` is not a pass, and no aggregation can round it into one. Report k with what it can support — under replay it measures harness determinism, and the run proves that (47/47 byte-identical repeats) rather than claiming variance it did not measure. Then gate on *change* against a committed baseline, so a flake shows up as a diff somebody has to explain. |
| The trace schema and handoff-aware contracts (`lab/trace/`, `lab/checks/`) | **How do you evaluate a multi-agent system rather than a single prompt?** | Evaluate the conversation, not the turn: one trace per session carrying utterances, tool calls, handoffs and engine attribution, and contracts that read across it. The three checks that matter are cross-agent — a spoken commitment must be backed by the tool call that would make it true, a fact supplied once must survive every handoff into the tool call, and a fact already given must never be asked for again. That is where multi-agent systems actually fail, and none of it is visible one prompt at a time. |
| The calibration gate, latency percentiles, WER and perturbations (`lab/voice/`) | **How do you eval voice?** | Timing first, because latency is the product: measure time to *first byte* from the trace, and prove the measurement by recovering a delay the harness does not know about, at every point from 100 ms to 2 s, before quoting any number. Then transcription (WER against a reference, harness-relative), silence attribution, and audio perturbations as a stratified suite rather than a single noisy clip. Barge-in is declared in the schema and not implemented — that is stated everywhere the schema is documented rather than left to be discovered. |
| The error-analysis loop as committed artefacts (`error_analysis/`) | **What would you do differently at 100× scale?** | Keep the loop and change the sampling: the expensive part is not running 4,700 scenarios, it is reading them, so stratify and sample rather than read in order. Automate the cascade — cheap deterministic checks first, judge only the sessions that survive them, human only the disagreements — and spend the review budget on the strata where the discovery curve is still moving. The thing I would not change is that the taxonomy stays hand-assigned and committed, because a mode nobody has named yet cannot be counted by a script. |
| The whole thing, honestly | **What are this project's weaknesses?** | The corpus is synthetic and the caller is scripted, so it under-samples phrasing — two of the five findings were only visible after probing the parser by hand. The judge's recorded verdicts are synthetic stand-ins rather than captured provider output, so what is demonstrated is the calibration machinery, not a real model's agreement. And the coding is one person with no second rater, on 47 traces, on one build. |

---

## Longer versions, for the three that always get a follow-up

### "You said the judge verdicts are synthetic. So the calibration numbers are fake?"

The *machinery* is real and the *agreement figure* is a demonstration. The
recorded answers in `lab/judges/hallucinated_confirmation/dataset.py` are written
by hand to model how a competent grader responds, and they are stamped with the
model id `synthetic/deterministic-stand-in` so no report generated from them can
be mistaken for a live measurement — the calibration report says so in its own
notes section. What that buys is the cardinal property of this repository: a
reviewer clones it and gets green tests with no API key. Pointing the same code at
a real provider is one call (`regenerate(live=True)` with `LAB_LIVE_JUDGE=1`),
through the identical code path, and nothing downstream changes.

The part I would defend as genuinely informative is the v1→v2 story. The naive
prompt fires on "I'll get that booked for you now" and on "shall I confirm?",
because nothing in it distinguishes intention from completion. That failure mode
is real, it is the first thing a naive confirmation judge does, and the fix — define
the target, enumerate what does not count, demand a quotable sentence — is the fix
you would apply to a real one. One false positive survives and was left alone
deliberately: it is a genuinely ambiguous utterance, and a prompt tuned until its
own calibration set comes back clean has been fitted to that set.

### "Your report says FAIL and your CI is green. Explain."

They answer different questions and both are printed. The report verdict is the
product's state: this build tells a party of six that their table is booked and
never books it, so anything other than FAIL would be a lie. The regression gate is
what CI acts on: nothing changed since the committed baseline, so this commit did
not make anything worse and the build is green.

The interesting half is that the gate also fails when a finding *disappears*. From
outside the harness, a fixed defect and a check that quietly stopped applying look
identical — one fewer failure — so both stop the build until somebody updates the
baseline in the same change and says which it was. That is `make reference`, and
the resulting diff is the only place where "this used to fail and now it does not"
becomes reviewable.

### "What did the error analysis actually buy you?"

Numbers, and a list of work. Reading 47 traces by hand produced 13 distinct
failure modes across 32 coded occurrences; 31 of those occurrences are product
defects, and the declarative checks caught **9 of the 31**. The two largest modes
have nothing checking them at all, and both are *absences* — a refusal that never
happened, a closing turn that never happened — whereas every contract in the
corpus asserts about something present. That is a structural gap I would not have
found by adding scenarios, because the scenarios were already passing.

It also caught the two most embarrassing possible results, which is the reason to
do it at all. One row fails because *my check* is wrong (a plural the matcher
cannot match). One row **passes** while the caller is told their booking was
changed and the diary still says four covers, because the contract requires
`modify_booking` to be called with a `changes` argument rather than with the change
the caller asked for. A suite that is green on that row is worse than no suite,
and nothing but reading the transcript was ever going to notice.
