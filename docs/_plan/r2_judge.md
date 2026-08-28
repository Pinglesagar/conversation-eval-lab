# R2 — Making LLM-as-judge more deterministic and more trustworthy

**Status: research and plan. Nothing here has been built. No production code was written or
modified to produce this document.**

Owner decides what, if anything, ships. Every recommendation in Part 9 is ranked, costed, and
tested against the repository's cardinal rule: *a clean clone runs green with zero API keys.*

House rules observed throughout: every rate carries its denominator; every number is either
reproducible by a command recorded below or attributed to a cited source; anything that is neither
is labelled **ASSUMPTION**.

---

## 0. What I ran, and what I read

### Commands executed (read-only; `git status --porcelain` was empty before and after each)

| command | result |
|---|---|
| `make calibrate PYTHON=.venv/bin/python` | exit 0, 114 lines of output |
| `make roleplay-demo PYTHON=.venv/bin/python` | exit 0, 365 lines of output |
| `.venv/bin/python -m roleplay.scorer_study` | exit 0, regenerated `study.md` byte-identically |
| `.venv/bin/python -m pytest tests/ -q --collect-only` | 1,980 tests collected |
| `.venv/bin/python -m pytest tests/test_judges*.py tests/test_roleplay_live_scorer.py -q` | 214 passed in 17.19s |
| ad-hoc script re-deriving the per-run confusion matrices from `verdicts_v*.jsonl` via `lab.judges.judge.parse_raw_verdict` | reproduced the numbers in §1.5 |
| ad-hoc script computing Wilson intervals and an exact McNemar test | numbers in §6 |

All three `make`/module targets regenerate committed artefacts and leave the working tree clean,
which is itself the first trustworthiness property worth naming: **the calibration report is not a
number somebody once saw, it is a build product.**

### Files read

`lab/judges/judge.py` (1,339), `lab/judges/calibration.py` (1,088), `lab/judges/registry.py` (387),
`lab/judges/__init__.py` (152), `lab/judges/hallucinated_confirmation/` (`__init__.py` 700,
`dataset.py` 602, `prompt_v1.md`, `prompt_v2.md`, `iteration.md`, `labels.jsonl`,
`verdicts_v{1,2}{,_run2,_run3}.jsonl`, `calibration_v{1,2}.md`), `roleplay/livescorer.py` (669),
`roleplay/calibration.py` (304), `roleplay/consistency.py` (325), `roleplay/scorer.py` (505),
`roleplay/labels.py`, `roleplay/scorer_study/` (`__init__.py` 996, `stability.py` 340, `study.md`),
`lab/report/interop.py`, `Makefile`.

---

## 1. Honest inventory: what this repository already does

This section is deliberately unflattering where the evidence is unflattering. It is the baseline
every proposal in Part 9 has to beat.

### 1.1 The judge's shape

`lab/judges/judge.py` fixes six design decisions, all of them defensible and all of them stated in
the module docstring rather than inferred:

1. **Binary verdict, not a 1–5 scale.** One question, one bit. Severity lives in the critique.
2. **A mandatory written critique**, which is the audit trail and the prompt-improvement instrument.
3. **The prompt is versioned and digested.** `Judge.with_prompt()` returns a judge with *no*
   calibration attached; `ReplayJudge` refuses a recording whose prompt digest no longer matches.
4. **Parse failures fail closed and are counted.** `strict=True` raises; otherwise the item is
   recorded FAIL with `parse_error=True`. It never defaults to pass.
5. **`Status` ("pass" | "fail" | "error") is a separate type from `Label` ("pass" | "fail")**, so
   "the model returned junk" cannot be rounded into a real answer.
6. **No default model.** `model` is required, or comes from `LAB_JUDGE_MODEL`.

Decoding parameters, from source: `temperature: float = 0.0` and `max_tokens: int = 512`
(`lab/judges/judge.py:425–426`, mirrored at `:1113–1114`); the live rubric scorer uses
`temperature 0.0`, `max_tokens 900` (`roleplay/livescorer.py:465–466`).

A repo-wide grep for `seed=` returns **zero** hits outside the voice/simulator layer. There is no
seed, no `response_format`/JSON-schema constraint, no logprob use, no `top_p` pinning, and no record
of the provider's backend fingerprint. That is the single largest determinism gap and Part 3 is
mostly about it.

### 1.2 Calibration

`lab/judges/calibration.py` produces a `CalibrationReport` containing the full 2×2 confusion matrix,
TPR, TNR, precision, recall, F1, raw agreement, prevalence, Cohen's kappa (with observed and
chance-expected agreement printed alongside), the parse-error rate, and **every individual
disagreement with the judge's critique next to the human's note**.

`Rate` refuses to render without its fraction, and prints `undefined (0/0)` rather than 0.0 on an
empty denominator. That is the house rule enforced in a type.

The module is explicit about kappa's own failure mode — kappa is prevalence-dependent and therefore
not comparable across differently-balanced label sets — which is exactly why the gate keys on TPR
and TNR and there is no default `min_kappa`.

### 1.3 The gate that raises

`lab/judges/registry.py::require_calibrated()` raises `UncalibratedJudgeError` (never measured) or
`JudgeBelowThresholdError` (measured, too weak). The two errors are separate because they call for
opposite responses: one needs labels, the other needs a better prompt.

Default `CalibrationThresholds`: **TPR ≥ 0.85, TNR ≥ 0.85, n ≥ 10, parse-error rate ≤ 0%**,
`min_kappa = None`. The override is `allow_uncalibrated=True`, must be written at the call site, and
logs a warning — deliberately un-settable from config or environment. In CI the gate raises;
interactively it warns and returns. CI is detected from `LAB_JUDGE_CI`, else `CI`.

The gate genuinely bites. From `make roleplay-demo`, section 4, the repository's own scripted rubric
scorer is refused:

```
                     human: fail     human: pass
     judge: fail            TP 9            FP 2
     judge: pass           FN 23           TN 36

  true positive rate (recall)      : 0.281 (9/32)
  true negative rate (specificity) : 0.947 (36/38)
  raw agreement                    : 0.643 (45/70)
  prevalence of 'fail'             : 0.457 (32/70)
  Cohen kappa                      : 0.241

calibration gate (TPR >= 0.85, TNR >= 0.85, n >= 10, parse errors <= 0%): REFUSED
  - TPR 0.281 (9/32) is below the required 0.85
  - registry refused the judge in CI mode: JudgeBelowThresholdError
```

Note what raw agreement would have said on its own: 0.643 (45/70) reads as "not great". Kappa 0.241
and TPR 0.281 (9/32) say the truth — the instrument misses roughly seven of every ten sessions a
reviewer would stop. This is the chance-correction argument made on the repository's own data rather
than in the abstract.

### 1.4 The worked v1 → v2 study

`lab/judges/hallucinated_confirmation/` is the reference artefact. Same 24 items, same label digest
(`cd660a33b628`), same model route (`azure/gpt-4.1`), same parser, same temperature. **Only the
prompt changed** — and `compare_reports` refuses two reports whose label digests differ, so that
constraint is enforced rather than promised.

| metric | v1 | v2 | delta |
|---|---|---|---|
| TPR (recall) | 0.250 (2/8) | 1.000 (8/8) | +0.750 |
| TNR (specificity) | 1.000 (16/16) | 1.000 (16/16) | ±0 |
| precision | 1.000 (2/2) | 1.000 (8/8) | ±0 |
| raw agreement | 0.750 (18/24) | 1.000 (24/24) | +0.250 |
| Cohen's kappa | 0.308 | 1.000 | +0.692 |
| FN | 6 | 0 | −6 |
| unparseable | 0 | 0 | ±0 |
| gate | FAIL | PASS | — |

What the v1 → v2 prompt diff actually did, read from `diff prompt_v1.md prompt_v2.md`, is four
distinct interventions that the literature names separately (Parts 2 and 7):

- **Stipulated the ground truth outside the judge's remit** ("no reservation was ever actually
  created… you do not need to work it out"). This removes an inference the judge is bad at and code
  answers for free.
- **Enumerated the positive class and the negative class with examples** — six FAIL exemplars, five
  labelled PASS categories. This is rubric decomposition.
- **Required quotable evidence, with an explicit default**: *"You must be able to quote one sentence
  from the assistant that makes the claim. If you cannot quote it, the answer is PASS."* That single
  line converts an unbounded judgement into a bounded extraction task with a safe fallback.
- **Constrained the output to one JSON object** with `verdict` / `quote` / `critique`.

v1's prompt was 11 lines. v2's is 68. The +0.750 TPR came from prompt engineering that is entirely
conventional — which is the point: **the repository's contribution is not the prompt, it is that the
+0.750 is a measured number attached to a digest.**

### 1.5 The finding that matters most, reproduced

`make calibrate` prints:

```
hallucinated_confirmation v1: 0.917 (22/24) items unanimous across 3 identical runs of azure/gpt-4.1
    unstable: all-set-saturday: fail -> pass -> fail
    unstable: claim-buried-in-policy-answer: pass -> fail -> pass
hallucinated_confirmation v2: 1.000 (24/24) items unanimous across 3 identical runs
```

I re-derived the confusion matrix for each individual run directly from the committed raw model
outputs, parsing with `lab.judges.judge.parse_raw_verdict`:

```
v1_run1: TP=2 FP=0 FN=6 TN=16   TPR=2/8   raw agreement=18/24
v1_run2: TP=2 FP=0 FN=6 TN=16   TPR=2/8   raw agreement=18/24
v1_run3: TP=2 FP=0 FN=6 TN=16   TPR=2/8   raw agreement=18/24
v2_run1: TP=8 FP=0 FN=0 TN=16   TPR=8/8   raw agreement=24/24
v2_run2: TP=8 FP=0 FN=0 TN=16   TPR=8/8   raw agreement=24/24
v2_run3: TP=8 FP=0 FN=0 TN=16   TPR=8/8   raw agreement=24/24
```

**v1's confusion matrix is byte-identical across three runs, and it is a lie.** Two items moved
between run 1 and run 2 — `all-set-saturday` (human `fail`) went fail→pass→fail, and
`claim-buried-in-policy-answer` (human `fail`) went pass→fail→pass. Both items are in the positive
class, so one left the TP cell exactly as the other entered it. TP stayed 2. FN stayed 6. Every
published rate held.

Three consequences, and they generalise well beyond this repository:

1. **Aggregate stability is not instrument stability.** A confusion matrix repeated three times is
   evidence of nothing on its own. Only the per-item view (`calibration.self_consistency`) sees the
   churn.
2. **8.3% (2/24) of items were coin flips and the report said 100% reproducible.** Any prompt
   comparison whose delta is one or two items is reading this noise.
3. **The cancellation was luck.** Nothing about temperature 0 caused the two flips to offset; they
   offset because both unstable items happened to share a human label.

### 1.6 The second, stronger finding — the same failure with the opposite outcome

`roleplay/scorer_study/study.md` (regenerated by `python -m roleplay.scorer_study`, exit 0, tree
clean) runs the same three-replicate design on a *live model* grading a five-criterion rubric, 27
items after exclusions. For rubric v2:

| run | TPR | TNR | precision | kappa | raw agreement | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.000 (15/15) | 1.000 (12/12) | 1.000 (15/15) | 1.000 | 1.000 (27/27) | 15 | 0 | 0 | 12 |
| 2 | 1.000 (15/15) | 1.000 (12/12) | 1.000 (15/15) | 1.000 | 1.000 (27/27) | 15 | 0 | 0 | 12 |
| 3 | 1.000 (15/15) | 0.917 (11/12) | 0.938 (15/16) | 0.924 | 0.963 (26/27) | 15 | 1 | 0 | 11 |

The file's own verdict: *"2 different confusion matrices came out of 3 identical runs, so any figure
quoted from a single run — including the one printed above, which is run 1 — is a sample rather than
a property of the instrument."*

And beneath the verdict, the score card is far less stable than the verdict is. For v2:

| measure | value | denominator |
|---|---|---|
| items fully stable (verdict, total and all five criteria) | 21 | 27 |
| items whose verdict moved | 1 | 27 |
| items whose numbers moved but verdict held | 5 | 27 |
| cohort mean total, per run | 16.593 / 16.37 / 16.37 (v1); 14.556 / 14.667 / 14.37 (v2) | out of 20 |
| spread of per-item total spreads (population sd) | 0.953 (v2), 0.516 (v1) | points |

The worst single item: `compliance-explicit-unlicensed-advice`, whose `mandatory_disclosure`
criterion scored **[0, 4, 0]** across three identical runs at temperature 0 — a four-point swing on a
five-point criterion, on the item the whole compliance rubric exists to catch. The verdict happened
to hold at `fail` all three times, so a binary-only harness would have called this item stable.

**The binary collapse in `lab/judges` is protective, and it also hides this.** Both statements are
true and the repo currently only argues the first.

### 1.7 Score consistency, with a control arm

`roleplay/consistency.py` runs k=5 identical repeats two ways — warm (one long-lived scoring
service, the production shape) and cold (a fresh scorer per repeat, the control) — and reports both:

```
warm  consistency-identical-transcript-warm-k5: [16, 15, 14, 13, 12] sd 1.414, spread 4 pt, 1 flip at threshold 14
cold  consistency-identical-transcript-warm-k5: [16, 16, 16, 16, 16] sd 0.0,   spread 0 pt, 0 flips
warm  consistency-borderline-transcript-warm-k5: [14, 13, 14, 13, 14] sd 0.49, spread 1 pt, 4 flips at threshold 14
cold  consistency-borderline-transcript-warm-k5: [14, 14, 14, 14, 14] sd 0.0,  spread 0 pt, 0 flips
```

The lesson stated in the module, which I have not seen articulated in any of the tooling surveyed in
Part 8: **a stability harness that resets more state than the deployment does cannot see state-leak
instability.** Choosing what to reset between repeats is a measurement decision and belongs next to
the number.

Note also the second row: `[14, 13, 14, 13, 14]` has a spread of only 1 point and produces **4
pass/fail flips out of 5** because the threshold sits inside the noise band. Magnitude and verdict
stability are genuinely independent and the demo prints both.

### 1.8 Labels derived by rule, and abstention on the label side

`roleplay/labels.py` derives the human column mechanically from ledgers the product itself wrote
(`record_disclosure`, `flag_compliance_risk`) under four stated rules — R1/R2 (outright-failure
clauses transcribed from the rubric) → fail, R3 (conjunction of everything the rubric rewards) →
pass, **R4 (anything the declared facts do not settle) → AMBIGUOUS, excluded from the metrics**.

The study excluded 7 items on R4 and printed each exclusion with its reason. The rationale is
correct and worth quoting as the repo's own: *"An ambiguous item guessed becomes a permanent,
invisible error term… A visible smaller number beats an invisible wrong one."*

This is selective prediction (Part 4) implemented on the **label** side. It is not implemented on
the **judge** side — the judge has no way to say "I do not know". That asymmetry is the largest
conceptual gap in the current design.

### 1.9 Abstention that does exist, and the withheld-evidence decision

Two other pieces of relevant machinery already exist:

- **`lab/cli.py` makes the judge abstain rather than guess** when no recording covers a trace, and
  `lab/report/report.py` carries `abstained` as a first-class field with an `abstention_rate` and a
  validator asserting `flagged + abstained <= judged`. Denominator-safe abstention reporting is
  already built.
- **`render_transcript(include_tools=False)` is the default**, and the docstring gives the reason: a
  judge shown the tool ledger can answer "was this claim true" by lookup, which "is a question code
  answers for free and with no variance." Withholding the ledger keeps the judge on the half that
  needs judgement and keeps its verdict composable with a deterministic check over the same trace.
  This is a deliberate, documented **information-partition** decision, and it is better reasoning
  than most eval frameworks apply to the same choice.

### 1.10 What the repository explicitly declines to do

Stated in the docstrings, not discovered by me:

- No ensembling, no self-consistency **voting**, no chain-of-thought scaffolding, no few-shot
  selection. Rationale: none can be believed before the single-call case has a measured TPR/TNR, and
  each multiplies cost per item. Voting specifically is rejected as spending three calls to make
  instability *invisible*.
- **No confidence intervals in code.** Rationale in `calibration.py:85–88`: *"a Wilson interval on
  8/8 would imply a precision the set cannot support."*
- **No inter-human agreement.** One labeller. Label noise is charged to the judge.
- No pairwise comparison, no position swapping, no verbosity control, no logprobs, no seeds.

I disagree with exactly one of these — the confidence-interval one — and Part 6 says why. The rest
are defensible as scoped decisions, and Part 9 ranks which are worth revisiting.

---

## 2. Literature: the systematic biases in LLM judges

### 2.1 The canonical three

Zheng et al., *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena* (arXiv:2306.05685) is the
paper that named them, and it also reported that strong judges reach **>80% agreement with humans —
the same level humans reach with each other**, which is the ceiling result Part 5 depends on.

- **Position bias** — the judge prefers the response in a particular slot. Measured by swapping the
  order of two candidates and counting preference reversals. Only applies to pairwise/comparative
  grading.
- **Verbosity bias** — longer answers score higher independent of quality. Measured with a
  "verbosity attack": pad an answer without adding content and count score change. The paper reports
  GPT-4 defends better than weaker judges but no judge passed the harder "repetitive list" attack.
- **Self-enhancement bias** — a judge favours text produced by itself or its own family. Studied
  further in *Self-Preference Bias in LLM-as-a-Judge* (arXiv:2410.21819).

### 2.2 Accepted mitigations

| bias | mitigation | evidence |
|---|---|---|
| position | pairwise with position swapping; count only order-consistent verdicts, treat reversals as ties or as abstentions | arXiv:2306.05685 |
| position | avoid pairwise entirely — grade each item against a fixed reference, which has no slots | arXiv:2306.05685 (reference-guided grading) |
| verbosity | rubric decomposition into atomic checkable criteria; require quoted evidence per criterion | arXiv:2306.05685; FActScore arXiv:2305.14251 |
| self-enhancement | use a judge from a different model family than the system under test; or a panel spanning disjoint families | arXiv:2404.18796 (PoLL) |

### 2.3 How this applies here

**This repository is largely immune to position bias by construction, and that is worth stating as
a designed property rather than an accident.** `lab.judges.Judge` is a single-item binary classifier
against a fixed rubric — there are no two candidates and therefore no slots. `roleplay` grades one
session against one rubric. Neither surface does pairwise comparison. If a pairwise surface is ever
added (Part 9, rank 9), position swapping becomes mandatory on day one.

**Verbosity bias is live and unmeasured.** The judge reads a rendered transcript whose length varies
with the conversation, and the rubric scorer's five ordinal criteria are exactly the shape verbosity
bias exploits. Nothing in the repo tests it. It is cheap to test offline (Part 9, rank 3).

**Self-enhancement bias is live and unmeasured and structurally hard to avoid here**, because both
the live agent (`--live-agent`) and the live judge (`--live-judge`) can be routed to the same model
via litellm. `make live-replay` runs agent, caller *and* judge as models. There is no assertion
anywhere that the judge route differs from the agent route. See Part 10, finding B.

---

## 3. Determinism: why temperature 0 is not it

### 3.1 The claim in the repo

`lab/judges/judge.py:1130` — *"temperature: Defaults to 0.0. A judge is a measuring instrument, and
sampling temperature is variance injected into the instrument."* And at `:706`, on retries — *"a
retried call is a second sample, and if sampling were noisy the retry would silently change the
verdict rather than recover it."*

The reasoning is right. The conclusion — that temperature 0 delivers a deterministic instrument — is
falsified by the repository's own data (§1.5, §1.6). Both studies ran at temperature 0 and both
observed item-level flips.

### 3.2 Why temperature 0 does not give determinism

Temperature 0 makes decoding *greedy*: at each step pick the argmax token. It does nothing about
whether the logits are the same number twice. Four independent sources of drift:

1. **Floating-point non-associativity under variable batching.** This is the dominant cause and it
   is now well characterised. Thinking Machines Lab, *Defeating Nondeterminism in LLM Inference*
   (https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/), identifies the
   root cause as **lack of batch invariance** in normalization, matmul and attention kernels:
   production servers use dynamic batching, so a request is grouped with whatever other traffic is
   live, and batch size / padding / position within the batch change the reduction order and
   therefore the last bits of the logits. Near an argmax tie, a last-bit difference flips a token,
   and one flipped token can flip a verdict. Their `batch_invariant_ops` implementation
   (https://github.com/thinking-machines-lab/batch_invariant_ops) restores bitwise reproducibility —
   1,000 identical runs producing 1,000 identical outputs — **at a throughput cost, and only if you
   control the server**. A hosted API user cannot apply it.
2. **Backend heterogeneity.** Different GPU types, kernel versions, tensor-parallel degrees, and
   quantisations produce different numerics for the same weights.
3. **Silent model updates.** A route like `azure/gpt-4.1` or `anthropic/claude-sonnet-5` is a
   pointer, not a pin.
4. **Argmax ties.** Where two tokens are genuinely near-equal, tie-breaking is implementation
   detail.

The observable consequence in this repo: `azure/gpt-4.1` at temperature 0 flipped 2/24 items on one
prompt (§1.5) and 1/27 verdicts plus 6/27 score cards on another (§1.6).

Independent corroboration that this is general and not a local artefact: *Rating Roulette:
Self-Inconsistency in LLM-As-A-Judge Frameworks* (arXiv:2510.27106, EMNLP 2025) — abstract read —
finds *"LLM judges have low intra-rater reliability in their assigned scores across different runs"*
and that the variance can render ratings *"almost arbitrary in the worst case."*

### 3.3 The technique ledger

| technique | what it actually buys | what it costs | fits the zero-key rule? |
|---|---|---|---|
| **temperature 0** | removes *sampling* variance only; leaves numerical, batching and version variance | free | already done |
| **`seed` parameter** | OpenAI/Azure describe it as *"a best effort to sample deterministically"* with determinism explicitly **not guaranteed**, and direct callers to watch `system_fingerprint` for backend changes ([Azure docs](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/reproducible-output?view=foundry-classic), [OpenAI cookbook](https://developers.openai.com/cookbook/examples/reproducible_outputs_with_the_seed_parameter)). Not supported by every provider litellm routes to. Buys partial variance reduction and, more usefully, **a recorded intent** | ~0; one field | yes — it is recorded metadata offline |
| **recording `system_fingerprint` / model version** | turns "the judge changed" from a mystery into a diff. Does not reduce variance; makes variance *attributable* | ~0 | yes — pure metadata |
| **constrained / structured decoding** (JSON schema, grammar) | removes the entire class of parse-shape variance and unparseable output. Does **not** make the *content* deterministic | provider-dependent; slight quality risk if schema fights the model | yes, and it composes with the existing digest/replay |
| **batch-invariant kernels** | genuine bitwise determinism | requires owning the inference server; throughput cost | **no** — out of reach for a hosted-route harness |
| **majority voting over k samples** | variance reduction ~1/√k on the aggregate; converts a 2/24 flip rate into a much smaller one | k× cost per item, and it **hides** per-item instability, which the repo correctly identifies as the opposite of what an eval harness is for | only if the per-item spread is reported *alongside* the vote |
| **self-consistency measurement (no voting)** | does not reduce variance at all; **quantifies** it, per item | k× cost at record time only; zero at replay time | yes — already built, `REPLICATES = 3` |
| **logprob-based scoring** | a continuous score (e.g. `P("fail")`) instead of a bit; lets you see *how close* a flip was, and gives a free confidence signal for abstention | needs `logprobs` support (not all routes/providers); more parsing | partly — the values are recorded, so replay stays offline |
| **pinning the model route to a dated version** | eliminates source (3) | operational; dated snapshots get retired | yes |

**The single highest-value insight for this repo:** the three replicates already recorded are a
*variance measurement*, and the honest use of a measured flip rate is not to average it away — it is
to **widen the error bar on every rate derived from a single run** and to refuse comparisons smaller
than the band. `lab/simulator` already ships a "measured flake band" concept for scenario pass rates.
The judge does not have one. That is rank 1 in Part 9.

---

## 4. Calibration beyond accuracy

### 4.1 Agreement metrics: kappa vs Krippendorff's alpha

| | Cohen's kappa | Krippendorff's alpha |
|---|---|---|
| raters | exactly 2 | any number |
| missing data | not allowed | handled |
| data type | nominal (binary here) | nominal, ordinal, interval, ratio |
| degenerate case | — | reduces to Cohen / Fleiss / Pearson under their assumptions |

Sources: [Label Studio](https://labelstud.io/blog/how-to-use-krippendorff-s-alpha-to-measure-annotation-agreement/),
[Encord](https://encord.com/blog/interrater-reliability-krippendorffs-alpha/); a 2026 survey
*Counting on Consensus: Selecting the Right Inter-annotator Agreement Metric for NLP Annotation and
Evaluation* (arXiv:2603.06865) was surfaced by search but I did not read it.

**When each is right here:**

- **`lab/judges` binary verdicts, one judge vs one label column: Cohen's kappa is correct.** Two
  raters, complete data, nominal. No change needed. The repo's own caveat — kappa is prevalence
  dependent, so don't compare kappas across differently-balanced sets — is the right one and is
  already printed.
- **The moment a second human labeller is added, or a panel of judges, or an abstention option
  (which creates structurally missing data), Cohen's kappa stops applying and Krippendorff's alpha
  becomes the right metric.** All three of those are on the roadmap in Part 9, so alpha is a
  prerequisite for ranks 4, 5 and 6 rather than an independent nice-to-have.
- **The five ordinal criteria in `roleplay` (0–4 each) are ordinal, not nominal.** Cohen's kappa is
  the wrong instrument for them today; nobody applies it there, so nothing is currently wrong — but
  if the score card ever gets an agreement number it must be **ordinal** alpha or weighted kappa,
  not plain kappa, because scoring 4 when the label is 3 is not the same error as scoring 0.

### 4.2 Confidence and selective prediction

The state of the art is unambiguous that judges are **overconfident**. *Overconfidence in
LLM-as-a-Judge: Diagnosis and Confidence-Driven Solution* (arXiv:2508.06225) — abstract read —
reports that *"predicted confidence significantly overstates actual correctness, undermining
reliability in practical deployment"*, introduces TH-Score to quantify confidence/accuracy
alignment, and argues for a shift from accuracy-centric to risk-aware evaluation. Related work
surfaced by search but not read: *Analyzing Uncertainty of LLM-as-a-Judge: Interval Evaluations with
Conformal Prediction* (arXiv:2509.18658) and *Calibrating LLM Judges: Linear Probes for Fast and
Reliable Uncertainty Estimation* (arXiv:2512.22245).

Three ways to get a usable confidence signal, in increasing order of trustworthiness:

1. **Verbalised confidence** ("how sure are you, 0–100"). Cheapest. Least trustworthy — this is
   precisely what arXiv:2508.06225 shows is miscalibrated.
2. **Sample dispersion.** Run k=3 and use unanimity as the confidence. This repo **already records
   exactly this data** and currently uses it only for a headline unanimity rate. Converting
   per-item unanimity into a per-item confidence is nearly free.
3. **Logprob of the verdict token.** A genuine continuous signal, provider permitting.

**Selective prediction / abstention.** The right shape for this repo is not a third verdict value —
that would break `Label` and the whole no-inversion property. It is a **coverage/risk curve**: keep
the binary verdict, attach a confidence, and report

> at coverage 1.00 (24/24) TPR is 8/8; at coverage 0.92 (22/24), abstaining on the two items where
> the three replicates disagreed, TPR is *x/y* and the two abstentions are named.

`lab/report/report.py` already has `abstained` and `abstention_rate` with a denominator validator,
and `roleplay/labels.py` R4 already establishes the repo's philosophical position ("a visible
smaller number beats an invisible wrong one"). The pieces are there; nothing wires them together.

### 4.3 The ceiling nobody can exceed

Zheng et al. (arXiv:2306.05685) report GPT-4 reaching **>80% agreement with human preference — the
same level humans reach with each other.** That is the ceiling result, and it bounds every number in
this repository:

> **A judge cannot be measured above the reliability of the labels it is measured against. When one
> person labels a set once, the measured TPR is a joint measurement of judge quality and labeller
> consistency, and the two cannot be separated.**

The repo says this itself, in `hallucinated_confirmation/__init__.py`: *"One model, one temperature,
one labeller. No second rater, so label noise is charged to the judge."* It is stated as a caveat
and never quantified.

Concretely: `hallucinated_confirmation` v2 scores 1.000 on every rate. If the labeller's own
test-retest agreement were, say, 0.95, then a judge scoring 1.000 against those labels is either
lucky or agreeing with the labeller's errors. **We cannot currently distinguish those two cases**,
and this is the deepest trustworthiness gap in the repository — deeper than determinism, because it
is not fixable by any amount of prompt or decoding work.

The cheapest fix is not a second person. It is **the same person, blind, later** — a test-retest
pass over the same 24 items with the ids shuffled, producing an intra-rater agreement figure that
becomes the printed ceiling next to every judge rate. Cost: one afternoon, zero API keys, zero
dollars. That is rank 2 in Part 9.

---

## 5. Statistical rigour

### 5.1 The honest confidence intervals

The repo grades `hallucinated_confirmation` on 24 items with 8 positives. Computed with the standard
Wilson score interval at 95% (script run above; the two starred values match the 0.68 / 0.81 figures
the repo already quotes in prose, which is a good cross-check):

| quantity | point | 95% Wilson interval | width |
|---|---|---|---|
| HC v2 TPR | 8/8 = 1.000 | [**0.676**, 1.000] * | 0.324 |
| HC v2 TNR | 16/16 = 1.000 | [**0.806**, 1.000] * | 0.194 |
| HC v1 TPR | 2/8 = 0.250 | [0.071, 0.591] | 0.519 |
| scorer_study v2 TPR | 15/15 = 1.000 | [0.796, 1.000] | 0.204 |
| scorer_study v2 TNR | 12/12 = 1.000 | [0.757, 1.000] | 0.243 |
| scorer_study v1 TPR | 9/15 = 0.600 | [0.357, 0.802] | 0.444 |
| scripted rubric TPR | 9/32 = 0.281 | [0.156, 0.454] | 0.298 |
| scripted rubric TNR | 36/38 = 0.947 | [0.827, 0.985] | 0.158 |

Rule of three, for the zero-error cells: with 0 observed errors in *n* trials the 95% upper bound on
the true error rate is ≈ 3/*n*. So 8/8 is consistent with a true miss rate up to **37.5%**; 16/16
with up to **18.8%**; 15/15 with up to **20.0%**.

**The repo's stated reason for omitting CIs is the best argument *for* printing them.** The
docstring says a Wilson interval on 8/8 "would imply a precision the set cannot support." The
opposite is true: `TPR 1.000` is the number that implies unsupportable precision. `TPR 1.000 (8/8),
95% CI [0.676, 1.000]` is the number that tells the reader the truth, and it says it in the same
glance rather than requiring them to open a docstring. A gate at TPR ≥ 0.85 that is cleared by a
point estimate whose lower bound is 0.676 is not clearing the bar it claims to clear.

This is the one place where I think the repository's stated position is wrong, and it is a cheap fix
(pure arithmetic, no dependency, no keys, ~40 lines).

### 5.2 Power: how many labels are actually needed

For a normal-approximation half-width *h* on a rate near p = 0.9, *n* ≈ z²p(1−p)/h² **in the class
being measured**:

| target half-width on TPR | positives needed | at prevalence 1/3, total items |
|---|---|---|
| ±0.10 | 35 | ~105 |
| ±0.05 | 139 | ~417 |
| ±0.03 | 385 | ~1,155 |

**ASSUMPTION:** prevalence stays near the current 0.333 (8/24) and the label set is drawn from the
post-filter population the judge actually sees. If prevalence drops, the total climbs
proportionally.

The practical reading: **24 items buys roughly a ±0.15–0.25 answer.** It is enough to distinguish
"broken" (TPR 0.250) from "working" (TPR 1.000) — which is exactly the v1→v2 comparison, and is why
that study is honest. It is nowhere near enough to distinguish v2 from a hypothetical v3, or model A
from model B. Getting to ±0.10 needs ~105 items; ±0.05 needs ~417. That is a labelling programme,
not an afternoon, and it should be planned as one.

### 5.3 Comparing v1 to v2: the right test, and its floor

The v1/v2 comparison is **paired** — same items, same labels, two prompts — so the correct test is
**McNemar's exact test on the discordant pairs**, not a two-proportion z-test (which assumes
independent samples and would be anticonservative here).

Computed from the committed verdicts:

```
HC v1 vs v2 McNemar: v2-correct-only = 6, v1-correct-only = 0,
                     discordant n = 6, exact two-sided p = 0.03125
```

So v1 → v2 **is** significant at α = 0.05 — but look at how narrowly. With *n* discordant pairs all
falling one way, the exact two-sided p is 2/2ⁿ. Therefore:

| discordant pairs, all one direction | exact two-sided p | significant at 0.05? |
|---|---|---|
| 4 | 0.125 | no |
| 5 | 0.0625 | no |
| **6** | **0.03125** | **yes — this is the observed case** |
| 7 | 0.015625 | yes |

**On a 24-item set, the smallest detectable prompt improvement is six items all moving the same
way.** A v3 that fixed three items and broke none would be unpublishable (p = 0.25) no matter how
real the improvement was. This is a concrete, repo-specific number that makes the "label more items"
recommendation actionable rather than a platitude, and it should be printed in `compare_reports`
output next to the delta table.

Same arithmetic for `scorer_study` (27 items, 6 discordant, all one way): p = 0.03125 likewise.

### 5.4 The variance the CIs do *not* include

A Wilson interval is binomial sampling error over items. It assumes the judge's answer per item is
fixed. §1.5 and §1.6 show it is not. The honest total uncertainty is (item sampling) ⊕ (run-to-run
instrument noise), and the second component is currently reported separately and never combined.

A defensible cheap combination: report the rate as **min–max across the k recorded replicates**, and
the Wilson interval on the pooled counts. For `scorer_study` v2 TNR that yields "0.917–1.000 across
3 runs; 95% CI on run 1 [0.757, 1.000]" — which reads as the genuinely humble claim it is.

---

## 6. Panels and ensembles

### 6.1 What the literature says

*Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models*
(arXiv:2404.18796, Cohere) — abstract read — finds that *"using a PoLL composed of a larger number of
smaller models outperforms a single large judge, exhibits less intra-model bias due to its
composition of disjoint model families, and does so while being over seven times less expensive."*
The mechanism that matters is **disjoint model families**: a panel of three variants of one model
shares that model's blind spots and buys much less than three families do.

Later work surfaced by search but not read: *RoPoLL: Robust Panel of LLM Judges* (arXiv:2606.30931),
*Who can we trust? LLM-as-a-jury for Comparative Assessment* (arXiv:2602.16610), *A Finite-Calibration
Regime Map for LLM Judge Panels* (arXiv:2606.01034).

### 6.2 When disagreement is signal, not noise

This is the part that fits this repository's philosophy best, and it is the argument that resolves
the repo's principled objection to ensembling.

The repo rejects voting because *"averaging those runs into a verdict… would spend three calls per
item to make the instability invisible."* That objection is correct **about voting** and does not
apply to a panel used as an instrument rather than as an oracle:

- **Panel agreement is a per-item difficulty signal.** Items where three disjoint-family judges
  agree are easy; items where they split are hard. That split is the abstention trigger in §4.2, and
  it is more trustworthy than either verbalised confidence or same-model replicate dispersion,
  because it does not share a single model's blind spot.
- **Panel disagreement is a label-quality signal.** An item where every judge disagrees with the
  human is far more likely to be a mislabel than an item where one judge does. On a 24-item set
  where the repo itself says *"mislabels are the single largest source of apparent judge error"*,
  this is the cheapest label-audit instrument available.
- **A panel makes self-enhancement bias measurable.** If judge A (same family as the agent) and
  judge B (different family) disagree systematically in A's favour, that is the bias, quantified.

So: **panel as measurement, yes; panel as verdict, no.** That reading preserves the repo's cardinal
rule (an instability made visible, not averaged away) while getting the panel benefit. It also
inherits the Krippendorff constraint from §4.1 — three raters means alpha, not kappa.

### 6.3 The cost, stated plainly

A three-family panel over the two existing studies at record time:

- `hallucinated_confirmation`: 24 items × 2 prompts × 3 replicates × 3 families = **432 calls**
  (currently 144).
- `roleplay/scorer_study`: 27 items × 2 rubrics × 3 replicates × 3 families = **486 calls**
  (currently 162).

At replay time the cost is zero and the run stays offline, exactly as today — the recordings are
committed. **ASSUMPTION:** a panel would require three provider credentials at record time, which
raises an operational (not architectural) barrier the current single-route design does not have.

---

## 7. Rubric-as-code and decomposed grading

### 7.1 The technique

The idea traces to FActScore (Nakano/Min et al., arXiv:2305.14251 / EMNLP 2023): rather than asking
"is this output good", **break the output into atomic, independently verifiable claims and grade
each one**, then aggregate. Ragas' `faithfulness` metric implements the same shape for RAG — *"the
ratio of claims supported by context to total claims in the answer"*
([Ragas docs](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)).

Why it reduces variance rather than just improving accuracy:

- Each sub-question has a narrower answer space, so there is less for the sampler to disagree about.
- Sub-questions are individually auditable — a human reviewing a disagreement reads one claim, not a
  whole transcript.
- Verbosity bias attacks a holistic score; it has much less purchase on "is this specific sentence
  present in the source".

### 7.2 What this repo already has, under a different name

**This is the technique the repository is strongest at and does not brand.** Three existing surfaces
are decomposed grading:

- **The six declarative contracts** (`ToolContract`, `PromiseContract`, `NoReAskContract`,
  `FieldPropagationContract`, `NoProgressContract`, `PhraseContract`) decide on event-stream position,
  deterministically, with no model in the loop at all. That is rubric-as-code with a variance of
  exactly zero.
- **`FeedbackGroundednessContract`** — from `make roleplay-demo` section 5: *"every quoted span and
  every presupposed topic in the feedback must be present in the session"*. It found 12 rows of
  hallucinated feedback, each reported at the claim level, e.g. `pitch-feature-dump-no-discovery:
  0/1 feedback claims grounded in the session -- fee objection: claimed but never came up`. **This is
  FActScore's shape, applied to a coach's feedback, computed deterministically.**
- **`ScoreClaimContract`** — *"a factual claim on the score card must agree with the session's own
  disclosure register and compliance flags"*. It found 12 rows where the model's score card asserted
  "no unlicensed advice" while the session's own ledger recorded a `flag_compliance_risk` event.

And the v2 prompt rewrite (§1.4) is manual rubric decomposition: it converted one holistic question
into an enumerated positive class, an enumerated negative class, and a mandatory quote.

### 7.3 The gap

What is missing is **decomposition of the judge's own reasoning into separately-scored atomic
sub-verdicts with per-sub-verdict calibration**. The repo's own docstring already anticipates the
right architecture: *"If severity genuinely matters, the correct move is more than one binary judge
(each with its own calibration), not one judge with more values."*

Applied to the five-criterion rubric scorer, that would mean five binary judges with five confusion
matrices instead of one five-valued card with none — which would have caught the `[0, 4, 0]`
`mandatory_disclosure` swing (§1.6) as a per-criterion instability figure rather than as a footnote.
It is more calls and more labels, and it is the architecturally correct answer.

---

## 8. What the serious tooling does, and what this repo does that they do not

**ASSUMPTION for this whole section:** feature descriptions are taken from vendor documentation
current as of this writing, not from running each tool. Anything marked "surfaced by search" was not
independently verified.

### 8.1 What they have that this repo does not

| tool | relevant capability | this repo |
|---|---|---|
| **DeepEval** ([G-Eval docs](https://deepeval.com/docs/metrics-llm-evals)) | G-Eval: LLM-as-judge with chain-of-thought over arbitrary custom criteria; a large library of prebuilt metrics; `ConversationalGEval` and `ArenaGEval` (pairwise) variants | no CoT scaffolding (deliberate), no metric library, no pairwise surface |
| **Ragas** ([faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)) | claim-level faithfulness decomposition as a first-class packaged metric | `ragcheck/` does per-claim faithfulness; not packaged/reusable as a library metric |
| **promptfoo** ([llm-rubric](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/llm-rubric/)) | declarative YAML assertions, `llm-rubric` with a score threshold, red-teaming, matrix sweeps over providers | `lab/report/interop.py` **exports to** promptfoo (`to_promptfoo_tests`, `to_promptfoo_config`) but does not run assertions in that style |
| **Braintrust** ([human review](https://www.braintrust.dev/docs/annotate/human-review), [autoevals](https://github.com/braintrustdata/autoevals)) | hosted experiment tracking; **human review inside the eval loop** to curate production logs into datasets and to assess automated scorers' efficacy | no UI, no hosted store; label curation is by hand into JSONL |
| **Langfuse** ([LLM-as-a-judge](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge), [annotation queues](https://langfuse.com/docs/evaluation/evaluation-methods/annotation-queues)) | production tracing; **annotation queues for domain experts** with corrected outputs, explicitly to *align LLM-as-a-judge evaluation with human annotation* | `to_langfuse_batch` / `from_langfuse_batch` round-trip exists; no queue, no live tracing |
| **OpenAI Evals** | hosted graders, model-graded templates, integration with the platform's own logs | none |
| **Inspect AI** ([inspect.aisi.org.uk](https://inspect.aisi.org.uk/), [repo](https://github.com/UKGovernmentBEIS/inspect_ai)) | composable Task = dataset + solvers + scorers; strong agent/tool-use support; a large public eval collection; a viewer | different architecture (trace-first, not task-first); no eval collection; no viewer |

The two capabilities in that table that this repository most conspicuously lacks are both about
**humans**: Braintrust's human review loop and Langfuse's annotation queues. Both exist precisely to
attack the ceiling problem in §4.3. That is not a coincidence, and it should inform the ranking.

### 8.2 What this repo does that they do not

Stated carefully — these are claims about emphasis and default behaviour, not claims that no
competitor could be configured to do the same:

1. **A gate that structurally refuses an uncalibrated judge.** Every tool above lets you write an
   LLM judge and use its verdicts immediately. `require_calibrated()` raises. The override is ugly
   by design and unavailable from config or environment. I am not aware of another framework where
   *"has this judge been measured against human labels?"* is a hard precondition rather than a
   recommended practice.
2. **The prompt digest invalidating a calibration.** `Judge.with_prompt()` returns a judge with no
   calibration; `ReplayJudge` refuses a stale recording. Agreement is treated as a property of a
   *specific prompt against a specific label digest*, and carrying it across an edit is made
   impossible rather than discouraged.
3. **Per-item self-consistency reported instead of voted away**, with the cancelling-instability case
   worked through on real data (§1.5). The tools above mostly offer k-sample voting; this repo
   deliberately declines to average and shows why.
4. **A warm/cold control arm** to localise instability to cross-session state (§1.7). I have not seen
   this in any of the frameworks surveyed.
5. **Labels derived by stated rule from the product's own ledgers, with an explicit AMBIGUOUS class
   that is excluded rather than guessed** (§1.8).
6. **Denominator-safe reporting as a type invariant** — `Rate` cannot print without its fraction;
   `abstained` shrinks a denominator visibly and a validator enforces `flagged + abstained <=
   judged`.
7. **Everything runs from a clean clone with zero API keys**, with committed recordings that store
   the model's *raw output* so replay exercises the parser too. Most of the tools above require at
   minimum an API key to demonstrate their judge features at all.
8. **A trace schema honest enough to grade a voice agent from** — which `lab/report/interop.py`
   correctly identifies as the actual contribution, positioning `lab` as a layer on the ecosystem
   rather than a rival to it.

Points 1, 2, 3 and 5 together are the differentiator. They are all the same idea applied four times:
**the measuring instrument is itself measured, and the measurement is pinned to what was measured.**

---

## 9. Ranked recommendations

Ranking criteria, in order: (a) does it protect the cardinal rule — clean clone, zero keys, green;
(b) does it strengthen the differentiator in §8.2 rather than chase parity with §8.1; (c) cost;
(d) how much trustworthiness it buys per unit of work.

**Costs are engineering estimates and are labelled ASSUMPTION.** No item below has been built.

---

### Rank 1 — Report the run-to-run band on every rate. *Do this first.*

**What.** The k = 3 replicates are already recorded. Compute each rate on each replicate and print
`TNR 1.000 (12/12) [0.917–1.000 across 3 runs]`. Add a derived quantity — call it the *decision
band* — and refuse any v-to-v comparison whose delta falls inside it.

**Why it is rank 1.** §1.5 and §1.6 are the repo's two best findings and neither currently changes a
single published number. Today `scorer_study` prints "the table is not reproducible" in prose and
then quotes run 1's table as though it were the answer. This closes that loop.

**Buys.** Converts a known-and-narrated weakness into an enforced property. Makes every prompt
comparison honest by construction.
**Costs.** ASSUMPTION: ~150 lines in `calibration.py` plus report-rendering changes; no new
dependency; zero API calls; the committed fixtures already contain the data.
**Zero-key fit.** Perfect — pure arithmetic over committed recordings.

---

### Rank 2 — Establish the human ceiling: blind test–retest by the same labeller.

**What.** Re-label the same 24 (and 27) items, blind, ids shuffled, later. Publish the intra-rater
agreement as a printed ceiling next to every judge rate: *"labeller test–retest agreement: 22/24
(0.917); a judge scoring above this is agreeing with label noise."*

**Why.** §4.3. This is the bound on every number in the repository, it is currently a docstring
caveat, and no amount of prompt or decoding work can move it. Doing it also directly answers the
sharpest question a reviewer can ask about the 1.000 rates.

**Buys.** The single largest increment in trustworthiness available. Also converts "1.000 means the
set is finished" from an assertion into a demonstration.
**Costs.** ASSUMPTION: one afternoon of the owner's labelling time; ~80 lines to load a second label
column and compute agreement; zero dollars; zero keys.
**Zero-key fit.** Perfect.
**Note.** With two label columns, Cohen's kappa still applies (two raters, complete data). With
three or with abstentions, switch to Krippendorff's alpha (§4.1).

---

### Rank 3 — Print confidence intervals, and reverse the stated position on them.

**What.** Wilson score interval on every rate, always, next to the fraction. Rule-of-three note on
zero-error cells. Gate on the **lower bound**, or at minimum print whether the lower bound clears
the threshold, so `TPR 1.000 (8/8) 95% CI [0.676, 1.000] — lower bound does NOT clear 0.85` is
visible.

**Why.** §5.1. The current position is that a CI on 8/8 implies unsupportable precision; the
opposite is true, and the repo already quotes the Wilson lower bounds in prose in two files, so the
arithmetic is already trusted — it is just not in the report.

**Buys.** Kills the most obvious criticism of the headline 1.000s. Makes "label more items" a
number rather than an opinion.
**Costs.** ASSUMPTION: ~60 lines, no dependency (Wilson is a closed form; do not pull in scipy).
Zero keys. The main cost is a doc rewrite and the owner reversing a stated position, which should be
done explicitly and in the docstring rather than silently.
**Zero-key fit.** Perfect.

---

### Rank 4 — McNemar in `compare_reports`, with the detectability floor printed.

**What.** Add the exact paired test and print `discordant 6/0, exact two-sided p = 0.031` alongside
the delta table, plus the standing note: *on a set this size, the smallest detectable improvement is
six items moving the same way (p = 2/2ⁿ).*

**Why.** §5.3. Turns "v2 beat v1" into "v2 beat v1, p = 0.031, and here is what would have been
undetectable."

**Buys.** Rigour on the single most-quoted comparison in the repository, at trivial cost.
**Costs.** ASSUMPTION: ~50 lines, closed-form binomial, no dependency, zero keys.
**Zero-key fit.** Perfect.

---

### Rank 5 — Judge-side abstention as a coverage/risk curve.

**What.** Keep `Label` binary. Attach a per-item confidence — start with replicate unanimity (free,
already recorded), upgrade to verdict-token logprob where the route supports it. Report a coverage
curve: TPR/TNR at 100% coverage, at 92% coverage, at 80% coverage, with the abstained items named.

**Why.** §4.2. The label side already abstains (R4); the judge side cannot. `lab/report` already
carries `abstained` with a denominator validator. This wires existing pieces together and matches
the repo's own philosophy ("a visible smaller number beats an invisible wrong one").

**Buys.** A judge that says "I do not know" on hard items, and a curve a reviewer can pick an
operating point from. Directly addresses the overconfidence literature (arXiv:2508.06225).
**Costs.** ASSUMPTION: ~200 lines across `calibration.py` and `report.py`; zero new API calls if
confidence comes from the existing replicates; logprob support would need a re-record.
**Zero-key fit.** Good — replicate-unanimity confidence is computed entirely offline.

---

### Rank 6 — Record the decoding provenance: seed, `system_fingerprint`, dated model version.

**What.** Pass a fixed `seed` where the provider supports it; capture and commit
`system_fingerprint` (or the equivalent) with every recorded verdict alongside the existing prompt
and label digests; encourage a dated model route in docs.

**Why.** §3.3. This does not make anything deterministic — Azure/OpenAI say determinism is *not
guaranteed* even with a matching seed and fingerprint. It makes non-determinism **attributable**,
which is the thing the repo's digest philosophy is already about. Right now, if the recordings were
re-drawn and the numbers moved, there would be no way to tell a backend change from a prompt effect.

**Buys.** Closes the last hole in the provenance chain (prompt digest ✓, label digest ✓, model route
✓, backend build ✗).
**Costs.** ASSUMPTION: ~80 lines plus a fixture-schema field; a re-record to populate it (144 + 162
calls, real money); litellm does not expose the field uniformly across providers, so it must be
optional-and-recorded-as-absent rather than required.
**Zero-key fit.** Good — the field is metadata; offline replay is unaffected.

---

### Rank 7 — Constrained/structured decoding for the judge's output contract.

**What.** Where the route supports JSON-schema-constrained decoding, use it; keep the existing
tolerant parser as the fallback path and keep testing it.

**Why.** Removes the entire class of output-shape variance and unparseable answers. Note the current
parse-error rate is **0/24 and 0/27** — so this buys robustness for futures, not a fix for a
present problem.

**Buys.** `max_parse_error_rate = 0` becomes structurally satisfied rather than luckily satisfied.
**Costs.** ASSUMPTION: ~100 lines; provider-dependent; a re-record; a small risk that a rigid schema
degrades verdict quality, which would itself need measuring. **Do not delete the tolerant parser** —
recordings store raw output specifically so replay exercises it.
**Zero-key fit.** Good.

---

### Rank 8 — A verbosity-bias probe (offline, fixture-based).

**What.** For each labelled item, generate a padded variant (repeat a neutral clause, add filler
turns that change no fact) and record the judge on both. Report verdict-flip rate and, for the
rubric scorer, mean score delta as a function of added length.

**Why.** §2.3. Verbosity bias is live here and unmeasured, and the ordinal rubric scorer is exactly
the shape it exploits. The repo already has a perturbation harness in `lab/voice`; this is the text
analogue.

**Buys.** A named bias moved from "unknown" to "measured", with a committed fixture that makes it a
regression test.
**Costs.** ASSUMPTION: ~250 lines plus a re-record (24 items × 2 prompts × 2 variants = 96 extra
calls for `hallucinated_confirmation` alone). Padding must be generated deterministically from
committed text, not by a model, or the probe itself becomes non-reproducible.
**Zero-key fit.** Good, once recorded.

---

### Rank 9 — A panel of two-to-three disjoint-family judges, used as an instrument.

**What.** Same prompt, same items, judges from different model families. **Do not vote.** Report
per-item panel agreement, use splits as the abstention trigger for rank 5, and flag items where the
whole panel disagrees with the human as label-audit candidates. Agreement metric: Krippendorff's
alpha, not kappa (§4.1).

**Why.** §6.2. It is the only technique that attacks self-enhancement bias, the only confidence
signal that does not share one model's blind spot, and the cheapest label-audit instrument available
on a small set.

**Buys.** The most *measurement* per unit of cost of anything below rank 5 — but it needs rank 1, 2
and 5 in place first or its output has nowhere to go.
**Costs.** ASSUMPTION: ~400 lines; 432 + 486 recorded calls (§6.3); **three provider credentials at
record time**, which is a new operational burden the single-route design does not currently carry.
Replay stays free and offline.
**Zero-key fit.** Acceptable at replay; the record path becomes materially harder to run.

---

### Rank 10 — Decompose the five-criterion rubric scorer into five calibrated binary judges.

**What.** Follow the repo's own stated principle — *"more than one binary judge, each with its own
calibration"* — and replace the single five-valued card with five binary sub-judges, each with a
confusion matrix and its own gate.

**Why.** §7.3. It would have surfaced the `[0, 4, 0]` `mandatory_disclosure` swing as a per-criterion
TPR/TNR rather than a footnote in a stability table.

**Buys.** Architecturally the most correct item on this list.
**Costs.** ASSUMPTION: the largest item here — five label columns instead of one (so five times the
labelling in rank 2), five prompts, 5× the calls, and a redesign of the score card contract.
**Zero-key fit.** Fine, but the labelling cost is the real blocker.
**Recommendation: do not start this until rank 2 has established what one label column costs.**

---

### Explicitly *not* recommended

| technique | why not, here |
|---|---|
| **Majority voting over k samples** | The repo's objection is correct: it spends k× to make instability invisible. Rank 1 spends the same recordings to make it *visible*. If voting is ever added it must print the per-item spread alongside the vote, or it is a regression in honesty. |
| **Batch-invariant kernels** | Genuinely solves determinism, and requires owning the inference server. Incompatible with a litellm-routed, hosted-provider harness. Worth a paragraph in the docs as "the thing that would actually work, and why we cannot use it." |
| **Chain-of-thought scaffolding in the judge prompt** | Raises token cost and, per the repo's own position, cannot be believed before the single-call case is measured. The v2 prompt already gets the auditability benefit from the mandatory quote + critique at a fraction of the cost. |
| **A 1–5 scale for the binary judge** | The argument in `judge.py`'s docstring is sound and should be left alone. The right response to "severity matters" is rank 10, not more values. |
| **Adopting DeepEval / Ragas / promptfoo as a dependency** | `lab/report/interop.py` has the right posture already — export to them, depend on none of them, pin the shapes with this repo's own tests. Taking a dependency would import a version constraint and, for several of them, an API-key requirement into a repo whose cardinal rule forbids both. |

---

## 10. Findings written down, not fixed

Per the brief: these are recorded for the owner to decide on. **None has been changed.**

**A. `scorer_study`'s gate verdict is computed from one sample of a demonstrably unstable
instrument.** `roleplay/scorer_study/study.md` prints "**Gate … : PASS**" for rubric v2 while the
same file shows that run 3 produced a different confusion matrix (TNR 0.917 (11/12) vs 1.000
(12/12)). Run 3 still clears the 0.85 bar, so the verdict happens to be robust here — but nothing in
the code checks that, and a judge sitting near the threshold would get a gate verdict that is itself
a coin flip. Rank 1 fixes this by construction. *Severity: latent, not currently biting.*

**B. Nothing prevents the live judge and the live agent being routed to the same model.**
`make live-replay` runs `--live-agent --live-caller --live-judge`; all three read model routes from
the environment via litellm, and there is no assertion anywhere that the judge's route differs from
the agent's. That is textbook self-enhancement-bias exposure (arXiv:2410.21819) with no guard rail
and no warning. A one-line check at record time — warn (or refuse) when judge route == agent route —
would close it. *Severity: real, currently unmeasured.*

**C. `hallucinated_confirmation` v2 saturates its label set, and the repo says so without acting on
it.** 24/24, 8/8, 16/16. The docstring is admirably clear: *"a set on which a judge makes no mistakes
cannot measure that judge any further, and cannot detect a regression in it."* But the set is still
the one wired into `make calibrate`, so the gate that guards the build is guarded by a set that
cannot detect regression. Ranks 2 and 3 make the limitation visible; only harder items fix it.
*Severity: the gate is currently decorative for this judge.*

**D. `Rate` guarantees a denominator; the rendered rate does not carry its uncertainty.** Discussed
in §5.1 and ranked at 3. Recording it here because it is the one place I believe the repository's
*stated* reasoning is wrong rather than merely incomplete.

**E. The five ordinal criteria have no agreement metric at all.** Nobody currently computes kappa on
them, so nothing is wrong today — but if one is ever added it must be ordinal (weighted kappa or
ordinal Krippendorff's alpha), because scoring 4 against a label of 3 is not the same error as
scoring 0. Worth a comment in `roleplay/scorer.py` before someone reaches for `_cohens_kappa`.
*Severity: a trap, not a defect.*

**F. Replicate count is fixed at `REPLICATES = 3`.** Three replicates can distinguish "unanimous"
from "not unanimous" and essentially nothing else — a 2/24 flip rate is estimated with enormous
error from three draws. Whatever rank 1 prints as a band will itself be a very noisy band, and that
should be said in the output rather than left for a reader to work out. *Severity: honesty caveat on
a fix, not a defect in the current code.*

---

## 11. Assumptions register

Everything in this document is either reproducible by a command in §0, quoted from a file in this
repository, or attributed to a cited source — except the following, which are labelled assumptions:

1. **Engineering cost estimates in Part 9** (line counts, "one afternoon"). Estimates, not
   measurements.
2. **Prevalence stays near 0.333 (8/24)** in the §5.2 power table. If the post-filter population's
   defect rate shifts, the totals scale.
3. **Tool capability descriptions in Part 8** come from vendor documentation, not from running each
   tool. Items marked "surfaced by search" were not read at all.
4. **A three-family panel needs three credentials at record time** (§6.3). Inferred from the
   architecture, not verified against litellm's routing behaviour.
5. **Provider support for `seed`, `system_fingerprint`, JSON-schema decoding and `logprobs` is
   uneven across the routes litellm serves.** Believed true and not enumerated per provider here.
6. **§8.2's "I am not aware of another framework that…" claims** are claims about my awareness after
   the survey in §8.1, not exhaustive proof of novelty.
7. **Several arXiv entries in Part 2, 4 and 6 were surfaced by search and not read.** Each is marked
   at its first mention. The papers whose abstracts I did read are: arXiv:2306.05685 (via search
   summary), arXiv:2404.18796, arXiv:2508.06225, arXiv:2510.27106.

---

## 12. Sources

**Papers**

- Zheng et al., *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena* — https://arxiv.org/abs/2306.05685
- Cohere, *Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models* (PoLL) — https://arxiv.org/abs/2404.18796
- Min et al., *FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation* — https://arxiv.org/abs/2305.14251
- *Self-Preference Bias in LLM-as-a-Judge* — https://arxiv.org/pdf/2410.21819
- *Overconfidence in LLM-as-a-Judge: Diagnosis and Confidence-Driven Solution* — https://arxiv.org/abs/2508.06225
- *Rating Roulette: Self-Inconsistency in LLM-As-A-Judge Frameworks* (EMNLP 2025) — https://arxiv.org/pdf/2510.27106
- *Analyzing Uncertainty of LLM-as-a-Judge: Interval Evaluations with Conformal Prediction* — https://arxiv.org/pdf/2509.18658 *(surfaced by search, not read)*
- *LLMs-as-Judges: A Comprehensive Survey on LLM-based Evaluation Methods* — https://arxiv.org/pdf/2412.05579 *(surfaced by search, not read)*
- *Counting on Consensus: Selecting the Right Inter-annotator Agreement Metric for NLP Annotation and Evaluation* — https://arxiv.org/pdf/2603.06865 *(surfaced by search, not read)*
- *RoPoLL: Robust Panel of LLM Judges* — https://arxiv.org/html/2606.30931 *(surfaced by search, not read)*

**Determinism**

- Thinking Machines Lab, *Defeating Nondeterminism in LLM Inference* — https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- `batch_invariant_ops` — https://github.com/thinking-machines-lab/batch_invariant_ops
- Azure OpenAI, *How to generate reproducible output* — https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/reproducible-output?view=foundry-classic
- OpenAI cookbook, *Reproducible outputs with the seed parameter* — https://developers.openai.com/cookbook/examples/reproducible_outputs_with_the_seed_parameter

**Agreement metrics**

- Label Studio, *Krippendorff's Alpha for Annotation Agreement* — https://labelstud.io/blog/how-to-use-krippendorff-s-alpha-to-measure-annotation-agreement/
- Encord, *Introduction to Krippendorff's Alpha* — https://encord.com/blog/interrater-reliability-krippendorffs-alpha/

**Tooling**

- DeepEval G-Eval — https://deepeval.com/docs/metrics-llm-evals
- Ragas faithfulness — https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/
- promptfoo `llm-rubric` — https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/llm-rubric/
- promptfoo model-graded metrics — https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/
- Braintrust human review — https://www.braintrust.dev/docs/annotate/human-review
- Braintrust autoevals — https://github.com/braintrustdata/autoevals
- Langfuse LLM-as-a-judge — https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge
- Langfuse annotation queues — https://langfuse.com/docs/evaluation/evaluation-methods/annotation-queues
- Inspect AI — https://inspect.aisi.org.uk/ and https://github.com/UKGovernmentBEIS/inspect_ai
