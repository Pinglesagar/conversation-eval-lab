# RAG evaluation: what I implemented, what it catches, and what I have not done

One page on the `ragcheck/` package: the metrics, their exact denominators, what
each one is blind to, what the published frameworks call the same things, and —
at the bottom, unhedged — the parts of RAG evaluation I have never done.

Run it: `python -m ragcheck` (offline, no keys), `pytest tests/test_ragcheck_*.py`
(79 tests).

---

## 1. The two halves, and why they are never averaged

    RETRIEVAL   did the passages that answer the question come back, and in what order?
                deterministic, no model, exactly reproducible
    GENERATION  did the answer stay inside those passages, and did it answer the question?
                needs an oracle — a judge, or a human

There is no single "RAG score" in this package and adding one would be a
regression. recall@3 = 0.75 and groundedness = 12/14 are two findings owned by
two different teams; averaging them to 0.80 produces a number that moves for two
unrelated reasons and tells nobody what to fix.

**recall@k is a ceiling on every generation metric.** A fact absent from the
context cannot appear in a grounded answer. So when groundedness drops, the first
question is whether retrieval moved — and that question is answerable offline,
exactly, before anyone spends a token.

---

## 2. The metrics, exactly as implemented

| Metric | Definition, with its denominator | Catches | Blind to | Ragas | DeepEval |
|---|---|---|---|---|---|
| `recall_at_k` | gold chunks in top-k / **all gold chunks** | the answer never reached the model | rank; a gold chunk at k scores like one at 1 | (its `context_recall` is judge-based, not this) | `ContextualRecallMetric` (judge-based) |
| `precision_at_k` | gold chunks in top-k / **k** | context padding, token cost, attention dilution | which position they sat in | — | `ContextualPrecisionMetric` (judge-based) |
| `reciprocal_rank` / MRR | 1 / rank of first gold chunk | how deep the first useful passage was | everything after the first hit — wrong metric for split answers | — | — |
| `ndcg_at_k` | DCG / ideal DCG over **min(k, \|gold\|)** terms | rank quality, comparably across questions with different \|gold\| | graded relevance (binary gains here) | — | — |
| `average_precision_at_k` | Σ precision@i over gold positions / **gold chunks inside the window** | putting the useful passage first | anything outside k | `context_precision` (same formula, judge-scored) | `ContextualPrecisionMetric` |
| `groundedness` | supported claims / **all claims in the answer** | invented facts, wrong figures, contradictions | whether the answer was on-topic at all | `faithfulness` | `FaithfulnessMetric` |
| `answer_relevance` | relevant answers / **answered questions** (binary per answer) | answering a different question | whether the answer was true | `answer_relevancy` (embedding cosine — different method, see §4) | `AnswerRelevancyMetric` (statement fraction) |
| `context_recall` | reference claims supportable from the context / **all reference claims** | the context was missing half the answer | generation quality | `context_recall` | `ContextualRecallMetric` |
| `judged_context_precision` | AP@k with a per-passage judge instead of gold ids | useless passages when no gold labels exist | only as good as the passage judge | `context_precision` | `ContextualPrecisionMetric` |

Two conventions run through all of it, both inherited from `lab`:

* **Every rate prints its fraction.** `0.923` and `12/13` are the same number
  and not the same claim; one relabelled item moves the second by eight points.
* **Micro and macro are both reported, labelled.** Micro pools numerators
  (every claim weighs the same — a six-claim answer has six chances to
  hallucinate). Macro averages per-question rates (every question weighs the
  same). On the fixture set they differ, and quoting one as "recall" without
  saying which is a small dishonesty that compounds.

---

## 3. The three failures, each one invisible to the other two

Worked end to end by `python -m ragcheck`, and pinned in
`tests/test_ragcheck_report.py`.

**c02 — retrieval perfect, answer wrong.** The passage saying the deposit is GBP
15 per person is retrieved at rank 1. The answer says GBP 25. recall@3 = 1/1,
context precision = 1.0, groundedness = **1/2**. *A retrieval-only suite passes
this row and the customer is quoted a figure 67% too high.*

**c12 — grounded and useless.** Asked about the dress code; answered with the
room's capacity and minimum spend, both supported by a retrieved passage.
groundedness = **2/2**, answer relevance = **fail**. *Faithfulness cannot see
the wrong question — it only ever asks whether the context supports the answer.
A suite gating on faithfulness alone ships this.*

**c18 — faithful, relevant, incomplete.** Two chunks are needed; retrieval
returns one. The answer stays inside what it was given. groundedness = 1/1,
relevance = pass, and **context recall = 1/2**, because the reference answer
names a fact the context never contained. *Only a metric measured against a
written reference — not against the generated answer — names the retrieval team
as the owner of this bug.*

---

## 4. Where I deliberately diverge from Ragas and DeepEval

**Deterministic claim decomposition.** Both frameworks ask a model to split an
answer into atomic statements. That puts the *denominator* of every faithfulness
score under a model's control at run time, so the same answer scores 3/4 on
Monday and 4/5 on Tuesday with nothing about the system under test having
changed — and a regression becomes indistinguishable from a re-roll of the
decomposer. `ragcheck.claims` is a sentence-and-clause splitter: worse at
English, usable as a gate. Its own failure modes are listed in its docstring, and
a model-based decomposer plugs into the same seam if the trade is worth making.

**Binary answer relevance, not embedding cosine.** Ragas computes answer
relevancy by generating questions from the answer and cosine-comparing them to
the original. That is clever and it is a similarity score, with a threshold
somebody picks afterwards. `lab.judges` argues for binary verdicts plus a written
critique, and I kept that: one question, one bit, nuance in the critique where a
human reads it. It also means the metric can be calibrated against human labels
as a classifier, which a cosine cannot be without first inventing a cut-off.

**One prompt for faithfulness and context recall.** The question is identical —
is this statement supported by these passages — and only the source of the
statements differs (generated answer vs reference answer). Ragas ships two
prompts. One prompt means one calibration and one disagreement list for a human
to read, so I could not justify the second.

**Gold-id context precision is preferred over the judged form.** Where gold ids
exist, average precision needs no oracle and is exactly reproducible. The judged
variant exists for corpora that have no labels yet, and on this fixture it is
visibly the weaker of the two — it calls a group-menu passage useful for a
deposit question.

---

## 5. The grader is the weakest component, so it is measured

The judged metrics in this repo run offline against a deliberately weak oracle:
idf-weighted word overlap plus a numeric check (`ragcheck/offline.py`). It is not
a model and the package never says it is. Measured against 18 hand-labelled
claim/context pairs (`ragcheck/fixtures/claim_labels.yaml`):

    TPR 0.800 (4/5)   TNR 0.923 (12/13)   raw agreement 0.889 (16/18)   kappa 0.723
    calibration gate: REFUSED — TPR 0.800 is below the required 0.85

Its one miss is the row I would put in front of anyone: the passage says
vouchers *may not* be used to pay a deposit, the answer says they may, every
content word matches, and word overlap cannot see the negation. That single item
is why the run's reported groundedness (13/16 claims supported) is one item
higher than the hand-labelled truth (12/16) — and the calibration report predicted
exactly that before anybody read a claim.

Two things follow, and they are the point of the package:

1. **An uncalibrated grader cannot gate a release.** `evaluate(gate=True)`
   raises. The override exists, has to be written at the call site, and logs a
   warning.
2. **Adversarial label items are worth more than sampled ones.** A random sample
   from a working system mostly contains items everything gets right. The two
   `probe-` rows were written to attack a known blind spot, and both landed.

---

## 6. RAG failure modes I would write tests for on a real product

The metrics above are the instruments; this is the list I would actually hunt
with. Ordered by how often I have seen the equivalent shape of bug in
conversational systems.

1. **Near-duplicate passages with different scopes.** Two policy paragraphs, one
   applying to a party of nine and one to a party of six. Retrieval returns both,
   the generator picks either, and the answer is fluent, grounded, and wrong for
   this customer. Test: gold sets that name the *correct* variant, plus a
   distractor sitting in the corpus on purpose.
2. **Negation and exceptions.** "may not be used", "except where", "unless the
   client is a professional investor". The most expensive answers to get wrong
   and the hardest for a similarity-based grader to score. Test: labelled
   contradiction pairs, and a judge calibrated specifically on them.
3. **Split answers / multi-hop.** No single chunk answers the question, so
   recall@1 is capped below 1.0 by construction and k becomes a design decision
   rather than a default. Test: multi-gold rows, and both recall@k and context
   recall reported.
4. **Citation correctness as a contract, not a vibe.** "Every answer cites its
   source" is testable the same way "the agent said it submitted, therefore the
   submit tool call must exist" is: the cited id must appear in the retrieve
   result, and the cited passage must be the one that supports the claim. In a
   regulated setting where the required disclosure is a *structured record*
   rather than model output, this is the compliance evidence — the model saying
   it disclosed something is not the same event as the disclosure existing, and
   only a trace holds both.
5. **Refusal and abstention.** A correct "I don't have that in the material" must
   score as a pass, and an invented answer to an unanswerable question must not.
   Test: rows whose gold set is empty by design, scored on abstention rather than
   on groundedness.
6. **Stale or partially-indexed corpora.** The answer was right last month.
   Test: index freshness assertions and a corpus digest in the report, so a
   metric change can be attributed to the index rather than the model.
7. **Instructions inside retrieved text.** Retrieved content is data. A passage
   containing "ignore previous instructions" must not change behaviour. Test: a
   poisoned chunk in the corpus and an assertion on the answer.
8. **Tenant and jurisdiction isolation.** In a multi-tenant deployment the worst
   defect is not a wrong answer, it is a *right answer from another customer's
   corpus*. Test: per-tenant corpora, a query engineered to match the other
   tenant's chunk, and an assertion that no foreign chunk id ever appears in the
   trace. The same test shape covers per-jurisdiction rule sets.
9. **Multilingual retrieval.** Question and corpus in different languages, or a
   question in a script the chunker mishandles. I have done language-fidelity QA
   across 19 languages including native-script transcript review; the retrieval
   half of it is the part `ragcheck` would need an embedding to do at all.
10. **Chunking regressions.** A change to chunk size or overlap moves every
    metric at once. Test: chunking as a versioned input to the report, so the
    diff names it.

---

## 7. What I have not done — stated plainly

* **No production RAG evaluation.** Everything above is a clean-room
  implementation written to understand the metrics, plus one 16-chunk fixture
  corpus. My shipped conversational-AI evaluation work is voice and chat agents,
  tool-call contracts, categorisation accuracy and LLM-judge calibration at
  production scale — not retrieval.
* **No embeddings, no vector store, no reranker.** So: no embedding-based
  metrics (including Ragas's own answer-relevancy method), no ANN recall
  measurement, no chunking-strategy experiments, no reranker A/B. The lexical
  retriever here exists to make the metrics testable, not to be good.
* **I have not shipped with Ragas, DeepEval, Langfuse, LangSmith, Braintrust or
  Promptfoo.** I have read their metric definitions closely enough to implement
  the equivalents and to say where I would diverge and why — which is what this
  page is — but I have not run any of them against a production corpus. What I
  bring instead is the discipline that surrounds them: golden datasets that are
  validated before they are trusted, judges that are calibrated before they gate,
  denominators that are stated, and failure ownership triaged as product / harness
  / label / variance before a red is believed. On a real system I would start
  from an existing framework rather than this code — my argument is not that
  hand-rolling is better, it is that I now know exactly what each number in one
  of those frameworks is counting.
* **No human-in-the-loop annotation programme run end to end for RAG
  specifically.** I have done the equivalent for categorisation: 1,000
  production conversations graded against a new spec, all 163 mismatches
  re-verified into 72 real prompt gaps, 79 label errors and 12 model variance,
  moving reported accuracy from 83.7% to 92.8%. The label-error rate is the part
  that transfers directly — 79 of 163 apparent failures were the labels.

---

## 8. What I would do in the first month on a real RAG product

1. **Find out what the ground truth already is.** Most products have one and do
   not call it that: the material the answers must come from, plus whatever the
   domain experts have already marked as right or wrong. That is the golden
   dataset; the job is to validate it, not to invent one.
2. **Split the pipeline before measuring it.** Retrieval metrics first, because
   they need no oracle and they bound everything else. A week of recall@k by
   question type usually tells you where the product actually is.
3. **Calibrate the judge before quoting it.** A few dozen hand-labelled items per
   judged property, TPR and TNR reported with their fractions, and a gate that
   refuses an uncalibrated judge in CI. Where domain experts are already
   reviewing judge output, that review *is* the calibration set — it just needs
   to be recorded as labels rather than as opinions.
4. **Make the citation contract a hard assertion.** Cheap, deterministic, and in
   a regulated setting it is the check with the highest consequence per line of
   code.
5. **Then gate.** Coverage thresholds and a release verdict that reports
   retrieval and generation separately, with the stability band measured rather
   than assumed — non-determinism gets a `pass^k` verdict and a flake band, not a
   re-run.
