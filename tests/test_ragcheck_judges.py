"""The RAG judges, and the gate that decides whether their verdicts are admissible.

WHAT THIS DEMONSTRATES
----------------------
That `ragcheck` inherits `lab.judges` rather than reimplementing it, and that the
inheritance is load-bearing rather than decorative. The tests fall in two halves:

*   **The prompt/trace contract.** A RAG prompt may only reference fields the
    judge can actually render, `{{claim}}` asserts that it is being asked about
    one statement, and an empty context is refused instead of quietly rendering
    as nothing. Each of these failures produces verdicts that *look like data*,
    which is why each one raises.
*   **The calibration gate.** The offline oracle is measured against 18 hand
    labels, scores TPR 4/5, and is refused. That refusal is the artefact: a
    package that ships a grader and a measurement showing its grader is not good
    enough to gate a release.
"""

from __future__ import annotations

import json

import pytest

from lab.judges.calibration import CalibrationThresholds
from lab.judges.judge import JudgeError, ScriptedCompletion
from lab.judges.registry import JudgeBelowThresholdError, UncalibratedJudgeError, require_calibrated

from ragcheck.calibration import (
    calibrate_claim_support,
    gate_claim_support,
    labelled_traces,
    load_claim_labels,
    offline_claim_support_judge,
)
from ragcheck.corpus import Retrieval, load_corpus
from ragcheck.judges import RagJudge, RagPromptTemplate, claim_support_judge
from ragcheck.traces import claim_trace, rag_trace, retrieval_of

CORPUS = load_corpus()
LABELS = load_claim_labels()


def _retrieval(*ids: str) -> Retrieval:
    return Retrieval(query="q", chunks=CORPUS.select(list(ids)), scores=[1.0] * len(ids))


def _judge(prompt: str, answer: str = '{"verdict": "pass", "critique": "ok"}') -> RagJudge:
    return RagJudge(
        name="probe",
        prompt=prompt,
        version="v1",
        model="test/stub",
        completion=ScriptedCompletion({"item": answer}),
    )


# --------------------------------------------------------------------------- #
# the prompt contract
# --------------------------------------------------------------------------- #


def test_a_rag_prompt_may_reference_the_rag_fields_and_nothing_else() -> None:
    """A typo'd placeholder fails when the judge is built, not at run time.

    The alternative is a prompt that renders `{{contxt}}` as an empty string and
    asks a model to grade nothing — which returns a verdict, and the verdict
    looks like data.
    """
    RagPromptTemplate("Q: {{question}}\nC: {{context}}\nS: {{claim}}")
    with pytest.raises(Exception, match="unknown field"):
        RagPromptTemplate("C: {{contxt}}")
    with pytest.raises(JudgeError, match="grade nothing at all"):
        RagPromptTemplate("Grade something, somehow.")


def test_the_question_context_and_claim_are_rendered_from_the_trace() -> None:
    """The judge reads the trace, so a trace off disk renders the same prompt."""
    judge = _judge("Q: {{question}}\nC: {{context}}\nS: {{claim}}")
    trace = claim_trace(
        case_id="c",
        question="How much notice do I need to cancel?",
        retrieval=_retrieval("p02"),
        claim="Cancellation is free up to 48 hours ahead.",
        index=1,
    )
    rendered = judge.render(trace)
    assert "How much notice" in rendered
    assert "[1] p02 (Cancellation window" in rendered
    assert "free of charge up to 48 hours" in rendered  # the passage text itself
    assert "Cancellation is free up to 48 hours ahead." in rendered


def test_a_claim_prompt_refuses_a_trace_holding_a_whole_answer() -> None:
    """{{claim}} means "one statement", and the guard makes that true.

    A prompt asking about "the statement" while being handed six sentences does
    not fail — it produces verdicts. So the field a prompt chooses is also the
    assertion it gets.
    """
    judge = _judge("S: {{claim}}")
    multi = rag_trace(
        case_id="c",
        question="q",
        retrieval=_retrieval("p02"),
        answer="First claim here. Second claim here.",
    )
    # One agent utterance holding two sentences is fine — the guard counts
    # utterances, and a claim trace has exactly one.
    assert judge.fields(multi)["claim"].startswith("First claim")

    from lab.clock import FakeClock
    from lab.trace.build import TraceBuilder

    builder = TraceBuilder(scenario_id="c", adapter="rag:text", clock=FakeClock())
    builder.session_start()
    builder.caller_utterance("q")
    builder.agent_utterance("First.", agent="rag")
    builder.agent_utterance("Second.", agent="rag")
    with pytest.raises(JudgeError, match="carries 2"):
        judge.fields(builder.build())


def test_an_empty_context_is_refused_rather_than_rendered_as_nothing() -> None:
    """A support verdict against no context is not a measurement."""
    judge = _judge("C: {{context}}")
    trace = rag_trace(case_id="c", question="q", retrieval=Retrieval(query="q"), answer="a")
    with pytest.raises(JudgeError, match="records no retrieve result"):
        judge.fields(trace)


def test_an_answer_prompt_is_refused_when_there_is_no_answer() -> None:
    judge = _judge("A: {{answer}}")
    trace = rag_trace(case_id="c", question="q", retrieval=_retrieval("p02"))
    with pytest.raises(JudgeError, match="no agent utterance"):
        judge.fields(trace)


def test_a_prompt_edit_produces_a_sibling_judge_with_no_calibration() -> None:
    """The iteration primitive, and the reason it drops the calibration.

    v2 has not been measured, so it must not inherit v1's numbers. Overridden
    here only because `Judge.with_prompt` would hand back a plain `Judge` and
    silently lose the RAG fields — on the one operation where nobody looks.
    """
    judge, report = calibrate_claim_support()
    assert judge.calibration is not None
    v2 = judge.with_prompt("Q: {{question}}\nC: {{context}}\nS: {{claim}}", version="v2")
    assert isinstance(v2, RagJudge)
    assert v2.calibration is None
    assert v2.prompt_sha256 != judge.prompt_sha256
    with pytest.raises(UncalibratedJudgeError):
        require_calibrated(v2, ci=True)
    assert report.prompt_version == "v1"


def test_the_three_shipped_prompts_are_readable_and_versioned() -> None:
    judge = claim_support_judge(completion=ScriptedCompletion({}), model="test/stub")
    assert judge.name == "claim_support"
    assert judge.version == "v1"
    assert len(judge.prompt_sha256) == 64
    assert judge.temperature == 0.0  # a measuring instrument, not a writer


# --------------------------------------------------------------------------- #
# the trace bridge
# --------------------------------------------------------------------------- #


def test_a_rag_turn_is_a_trace_with_the_retrieval_as_a_tool_call() -> None:
    """Which is why lab.checks contract checks apply to it unchanged.

    "The answer cited p02, therefore the retrieve result must contain p02" is the
    same shape of assertion as "the agent said it booked, therefore
    create_booking must have been called".
    """
    trace = rag_trace(
        case_id="c02",
        question="q",
        retrieval=_retrieval("p01", "p03"),
        answer="an answer",
    )
    assert trace.tool_names() == ["retrieve"]
    assert [chunk.id for chunk in retrieval_of(trace)] == ["p01", "p03"]
    assert [event.kind for event in trace.events][:3] == [
        "session_start",
        "caller_utterance",
        "tool_call",
    ]
    # The chunk text travels with the trace, so a verdict recorded today can be
    # audited against the passage it was based on months later.
    assert "deposit" in retrieval_of(trace)[0].text


# --------------------------------------------------------------------------- #
# calibration and the gate
# --------------------------------------------------------------------------- #


def test_the_offline_oracle_is_measured_and_refused() -> None:
    """TPR 4/5, TNR 12/13, and the gate says no.

    The numbers are pinned deliberately. If the oracle, the corpus or the labels
    change, this test fails and somebody has to look at the new confusion matrix
    — which is the only way a calibration figure stays true.
    """
    _, report = calibrate_claim_support()
    assert report.judge == "claim_support"
    assert report.model == "stand-in/lexical-v1"
    matrix = report.confusion
    assert (
        matrix.true_positive,
        matrix.false_positive,
        matrix.false_negative,
        matrix.true_negative,
    ) == (4, 1, 1, 12)
    assert str(report.true_positive_rate) == "0.800 (4/5)"
    assert str(report.true_negative_rate) == "0.923 (12/13)"
    assert report.n == 18
    assert report.passes() is False

    with pytest.raises(JudgeBelowThresholdError, match="TPR"):
        gate_claim_support(ci=True)


def test_the_two_disagreements_are_the_ones_the_labels_were_written_to_catch() -> None:
    """A false negative on a negation, and a false positive on a paraphrase.

    Both were written on purpose. A calibration set sampled at random from a
    working system mostly contains items everything gets right, and tells you
    nothing about where the instrument fails.
    """
    _, report = calibrate_claim_support()
    kinds = {item.item_id: item.kind for item in report.disagreements}
    assert kinds == {
        "c13#claim2": "false_negative",
        "probe-paraphrase": "false_positive",
    }
    negation = next(item for item in report.disagreements if item.item_id == "c13#claim2")
    assert "may NOT be used" in negation.human_note


def test_raw_agreement_flatters_the_oracle_more_than_kappa_does() -> None:
    """0.889 raw agreement, 0.723 kappa, on a set that is 28% defects.

    The gap is what chance correction buys, and it is the reason both are printed
    side by side — and the reason the gate is on TPR and TNR, which are
    comparable across differently balanced sets, rather than on either of these.
    """
    _, report = calibrate_claim_support()
    assert str(report.raw_agreement) == "0.889 (16/18)"
    assert report.cohens_kappa is not None
    assert report.cohens_kappa == pytest.approx(0.723, abs=0.001)
    assert str(report.prevalence) == "0.278 (5/18)"


def test_a_calibration_cannot_be_attached_to_a_different_prompt_version() -> None:
    """"This judge is calibrated" must not decay into "some judge once was"."""
    judge, report = calibrate_claim_support()
    other = claim_support_judge(
        completion=ScriptedCompletion({}), model="test/stub", version="v1"
    )
    other.version = "v2"
    with pytest.raises(JudgeError, match="refusing to attach"):
        other.attach_calibration(report)


def test_a_stricter_threshold_refuses_the_oracle_on_both_rates() -> None:
    """The thresholds are a parameter and they are printed next to the verdict.

    A standard nobody can see is not a standard, so `CalibrationThresholds`
    prints itself into the failure message.
    """
    with pytest.raises(JudgeBelowThresholdError) as excinfo:
        gate_claim_support(ci=True, thresholds=CalibrationThresholds(min_tpr=0.99, min_tnr=0.99))
    message = str(excinfo.value)
    assert "TPR >= 0.99" in message and "TNR >= 0.99" in message


def test_the_override_exists_is_ugly_and_returns_the_report_anyway() -> None:
    """Bypassing the gate is sometimes legitimate and must always be visible.

    `allow_uncalibrated=True` has to be written at the call site, so it shows up
    in the diff, and `lab.judges.registry` logs a warning, so it shows up in the
    log. An override settable from config becomes permanent within a month.
    """
    report = gate_claim_support(ci=True, allow_uncalibrated=True)
    assert report is not None and report.passes() is False


def test_every_label_is_self_contained_and_turns_into_a_trace() -> None:
    """One file holds the question, the passages, the claim, the verdict and why.

    A label set is data a human is accountable for. Referencing the evidence by
    path means a reviewer chases sidecar files that may have moved on since the
    label was written.
    """
    items = labelled_traces(LABELS, CORPUS)
    assert len(items) == len(LABELS) == 18
    first = items[0]
    assert first.item_id == "c01#claim1"
    assert first.note
    assert first.labeller == "repo author, by hand, reading each passage"
    assert [chunk.id for chunk in retrieval_of(first.trace)] == ["p02", "p03", "p15"]


def test_the_oracle_answers_only_the_items_it_was_given() -> None:
    """An unknown item id stops the run instead of receiving a default verdict."""
    judge = offline_claim_support_judge(CORPUS, LABELS[:2])
    trace = claim_trace(
        case_id="unknown",
        question="q",
        retrieval=_retrieval("p02"),
        claim="Something nobody has scored.",
        index=1,
    )
    with pytest.raises(Exception, match="no scripted answer"):
        judge.judge(trace, item_id="unknown#claim1")


def test_the_oracle_emits_the_same_output_format_a_model_is_asked_for() -> None:
    """So the offline path exercises the real parser, not a simplified one."""
    from ragcheck.offline import LexicalOracle, Probe

    raw = LexicalOracle(CORPUS).raw(
        Probe(
            item_id="x",
            kind="support",
            question="q",
            text="It is GBP 25 per person, taken on the night.",
            chunk_ids=["p01"],
        )
    )
    payload = json.loads(raw)
    assert payload["verdict"] == "fail"
    assert "25" in payload["critique"]
    assert payload["critique"].startswith("[lexical stand-in, not a model]")
