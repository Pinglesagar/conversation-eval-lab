"""The offline oracle: a deliberately weak stand-in for a judge, and honest about it.

WHAT THIS IS
------------
A table of pre-computed verdicts, produced by word overlap and number matching,
that plugs into `lab.judges.judge.ScriptedCompletion` and lets every metric in
this package run with **no model, no network and no API key**. It is what makes
`pytest` a check on the arithmetic rather than a check on somebody's billing.

**It is not a judge, and nothing here pretends otherwise.** It cannot see
negation, paraphrase or implication. Three specific things it gets wrong on this
fixture, all of them deliberate and all of them measured:

    "a voucher may be used against your deposit"     said SUPPORTED.
        The passage says vouchers may not be used to pay a deposit. Every content
        word matches; the word that reverses the meaning is one the overlap
        counts as a match too. This is a FALSE NEGATIVE on the defect, the
        dangerous cell.
    "bookings can be pushed back a single time at no cost"   said UNSUPPORTED.
        The passage says a booking may be moved once free of charge. Same fact,
        no shared vocabulary. A FALSE POSITIVE: a reviewer's time, wasted.
    a passage on the group menu, for a question about deposits    said USEFUL.
        Both mention parties of N or more.

Those are not bugs to fix. They are the reason `ragcheck.calibration` exists and
the reason its report refuses this oracle as a CI gate: measured against 18 hand
labels it recovers 4 of the 5 unsupported claims, and a gate wants 0.85.

WHY BUILD IT AT ALL
-------------------
Because the alternative is worse in both directions. Mocking the judge to return
"pass" makes every test green and every metric meaningless. Requiring a live
model makes the suite unrunnable on a laptop, unrunnable in CI, and expensive to
change. A weak-but-real oracle exercises the whole path — prompt rendering,
parsing, verdict construction, aggregation, calibration — and then tells you, in
numbers, how far you can trust the result. That is the same argument the recorded
fixtures in `lab` make, one step further: this one is measured, and it fails.

Swapping in a real judge is one argument at the call site. Nothing downstream
changes except how much the numbers are worth.
"""

from __future__ import annotations

import json
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.judges.judge import ScriptedCompletion

from ragcheck.claims import split_claims, split_sentences
from ragcheck.corpus import Corpus, Retrieval
from ragcheck.dataset import RagCase, RagDataset
from ragcheck.generation import answer_item_id, claim_item_id, passage_item_id
from ragcheck.text import content_words, numbers, overlap

__all__ = [
    "STAND_IN_MODEL",
    "SUPPORT_OVERLAP",
    "FOCUS_TERMS",
    "Probe",
    "LexicalOracle",
    "probes_for_case",
    "probes_for_dataset",
    "offline_completion",
]

#: Stands where a model route would. Named so that any report, recording or
#: calibration produced with it says on its face that no model was involved —
#: `model="stand-in/lexical-v1"` is not something anyone mistakes for a provider.
STAND_IN_MODEL = "stand-in/lexical-v1"

#: A claim is called supported when this fraction of its distinct content words
#: appear in one retrieved passage. Chosen by looking at the fixture, which is
#: exactly the kind of tuning that makes a threshold worthless on new data — said
#: here rather than discovered later.
SUPPORT_OVERLAP = 0.6

#: How many high-idf question terms count as "what the question is about".
FOCUS_TERMS = 2

ProbeKind = Literal["support", "relevance", "passage"]


class Probe(BaseModel):
    """One question the oracle will be asked, in structured form.

    The oracle works from these rather than from the rendered prompt text. It
    could parse the prompt — the sections are headed — but a stand-in that
    depends on prompt layout breaks the moment somebody improves the prompt, and
    then the failure looks like a metric regression.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: ProbeKind
    question: str
    text: str = Field(default="", description="The claim or answer under review.")
    chunk_ids: list[str] = Field(default_factory=list)


class LexicalOracle:
    """Word-overlap verdicts in the judge output format."""

    def __init__(
        self,
        corpus: Corpus,
        *,
        support_overlap: float = SUPPORT_OVERLAP,
        focus_terms: int = FOCUS_TERMS,
    ) -> None:
        self.corpus = corpus
        self.support_overlap = support_overlap
        self.focus_terms = focus_terms

    # ------------------------------------------------------------- verdicts

    def support(self, claim: str, chunk_ids: Sequence[str]) -> tuple[bool, str, str | None]:
        """Is `claim` supported by any one of these passages?

        A passage supports the claim when enough of the claim's words are in it
        **and** every number in the claim is in it too. The numeric condition is
        the only part of this oracle that is not a similarity heuristic, and it is
        the part that catches the fixture's headline defect: an answer that quotes
        GBP 25 from a passage that says 15 has near-perfect word overlap.
        """
        best_id: str | None = None
        best_score = -1.0
        for chunk_id in chunk_ids:
            chunk = self.corpus.get(chunk_id)
            score = overlap(claim, chunk.text)
            if score > best_score:
                best_score, best_id = score, chunk_id
            if score < self.support_overlap:
                continue
            claim_numbers = numbers(claim)
            passage_numbers = numbers(chunk.text)
            if claim_numbers - passage_numbers:
                continue
            return (
                True,
                f"{int(round(score * 100))}% of the claim's content words appear in "
                f"{chunk_id}, and its figures match.",
                _first_sentence(chunk.text),
            )
        if best_id is None:
            return False, "no passages were supplied, so nothing can support the claim.", None
        best_chunk = self.corpus.get(best_id)
        claim_numbers = numbers(claim)
        conflicting = sorted(claim_numbers - numbers(best_chunk.text))
        if conflicting and best_score >= self.support_overlap:
            return (
                False,
                f"the wording matches {best_id} but the figure(s) {conflicting} do not "
                f"appear in it.",
                _first_sentence(best_chunk.text),
            )
        return (
            False,
            f"the best passage was {best_id} at {int(round(best_score * 100))}% word "
            f"overlap, below the {int(self.support_overlap * 100)}% the stand-in requires.",
            None,
        )

    def relevance(self, question: str, answer: str) -> tuple[bool, str, str | None]:
        """Does `answer` mention what the question is about?

        "About" is the question's highest-idf corpus-known terms. It is a crude
        proxy for a real relevance judgement and it is right on this fixture for a
        reason worth naming: a fully grounded answer that discusses the room's
        capacity instead of its dress code shares the *place* words with the
        question and not the *subject* words, and idf is what separates those two.
        """
        focus = self.corpus.rare_terms(question, limit=self.focus_terms)
        answer_terms = set(content_words(answer))
        hit = [term for term in focus if term in answer_terms]
        if hit:
            return (
                True,
                f"the answer mentions {hit}, the question's distinguishing term(s).",
                _first_sentence(answer),
            )
        return (
            False,
            f"the answer never mentions {focus}, which is what the question is about.",
            None,
        )

    def passage(self, question: str, chunk_id: str) -> tuple[bool, str, str | None]:
        """Was this passage worth retrieving? Same focus-term test."""
        chunk = self.corpus.get(chunk_id)
        focus = self.corpus.rare_terms(question, limit=self.focus_terms)
        chunk_terms = set(content_words(chunk.text))
        hit = [term for term in focus if term in chunk_terms]
        if hit:
            return True, f"{chunk_id} contains {hit}.", _first_sentence(chunk.text)
        return False, f"{chunk_id} contains none of {focus}.", None

    # ----------------------------------------------------------- the seam

    def raw(self, probe: Probe) -> str:
        """The probe's verdict, in the judge's own output format."""
        if probe.kind == "support":
            passed, critique, quote = self.support(probe.text, probe.chunk_ids)
        elif probe.kind == "relevance":
            passed, critique, quote = self.relevance(probe.question, probe.text)
        elif probe.kind == "passage":
            if len(probe.chunk_ids) != 1:
                raise ValueError(
                    f"a passage probe judges one passage at a time, got {probe.chunk_ids}"
                )
            passed, critique, quote = self.passage(probe.question, probe.chunk_ids[0])
        else:  # pragma: no cover - Literal makes this unreachable
            raise ValueError(f"unknown probe kind {probe.kind!r}")
        return json.dumps(
            {
                "verdict": "pass" if passed else "fail",
                "quote": quote,
                "critique": f"[lexical stand-in, not a model] {critique}",
            }
        )

    def completion(self, probes: Iterable[Probe]) -> ScriptedCompletion:
        """A `ScriptedCompletion` answering exactly these item ids.

        Anything else raises `MissingRecordingError` from `lab.judges`, which is
        the behaviour worth having: an item the oracle was never given a context
        for should stop the run, not receive a default verdict.
        """
        return ScriptedCompletion({probe.item_id: self.raw(probe) for probe in probes})


def _first_sentence(text: str) -> str | None:
    sentences = split_sentences(text)
    return sentences[0] if sentences else None


def probes_for_case(case: RagCase, retrieval: Retrieval) -> list[Probe]:
    """Every question the metrics will ask about `case`, with its inputs.

    Item ids come from `ragcheck.generation`, not from a format string here, so
    the oracle's table and the metrics' lookups cannot drift apart.
    """
    chunk_ids = retrieval.ids
    probes: list[Probe] = []
    if case.has_answer:
        for index, claim in enumerate(split_claims(case.answer or ""), start=1):
            probes.append(
                Probe(
                    item_id=claim_item_id(case.id, index, kind="claim"),
                    kind="support",
                    question=case.question,
                    text=claim,
                    chunk_ids=chunk_ids,
                )
            )
        probes.append(
            Probe(
                item_id=answer_item_id(case.id),
                kind="relevance",
                question=case.question,
                text=case.answer or "",
                chunk_ids=chunk_ids,
            )
        )
    if case.has_reference:
        for index, claim in enumerate(split_claims(case.reference or ""), start=1):
            probes.append(
                Probe(
                    item_id=claim_item_id(case.id, index, kind="ref"),
                    kind="support",
                    question=case.question,
                    text=claim,
                    chunk_ids=chunk_ids,
                )
            )
    for rank, chunk_id in enumerate(chunk_ids, start=1):
        probes.append(
            Probe(
                item_id=passage_item_id(case.id, rank),
                kind="passage",
                question=case.question,
                chunk_ids=[chunk_id],
            )
        )
    return probes


def probes_for_dataset(
    dataset: RagDataset, contexts: dict[str, Retrieval]
) -> list[Probe]:
    """Probes for every case that has a context, in dataset order."""
    probes: list[Probe] = []
    for case in dataset:
        retrieval = contexts.get(case.id)
        if retrieval is None:
            continue
        probes.extend(probes_for_case(case, retrieval))
    return probes


def offline_completion(
    corpus: Corpus, dataset: RagDataset, contexts: dict[str, Retrieval]
) -> ScriptedCompletion:
    """One table of stand-in verdicts covering the whole dataset."""
    return LexicalOracle(corpus).completion(probes_for_dataset(dataset, contexts))
