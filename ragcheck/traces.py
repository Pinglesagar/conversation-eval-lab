"""A RAG turn, expressed as a trace — so the existing harness can grade it.

WHAT THIS DEMONSTRATES
----------------------
There is no new evidence format in this package. A retrieval-augmented answer is
a conversational turn with a tool call in the middle of it:

    caller_utterance   the question
    tool_call          retrieve(query, k)
    tool_result        the ranked chunks that came back
    agent_utterance    the answer

Writing it that way is not decoration. It buys three things immediately, none of
which had to be built:

*   **`lab.judges` works unchanged.** `Judge` grades a `Trace`, so a RAG judge is
    a prompt, not a new class hierarchy — and `lab.judges.calibration.calibrate`
    takes `LabelledTrace`, so a hand-labelled RAG item is the same object as a
    hand-labelled conversation and gets the same TPR/TNR treatment.
*   **`lab.checks` contract checks apply as they stand.** "The agent cited a
    source, therefore a retrieve call must exist and must have returned that
    chunk" is the same decision-versus-action contract shape as "the agent said
    it booked the table, therefore create_booking must have been called". In a
    regulated setting — a disclosure the model claims it made — that check is the
    compliance evidence.
*   **One trace, one audit.** Whatever a metric reports, the retrieved chunk text
    that produced it is in the same file, so a disputed number is settled by
    reading rather than by re-running.

CLAIM TRACES
------------
Groundedness and context recall are per-claim, not per-answer, so
`claim_trace()` builds a trace whose single agent utterance is one claim, over
the same retrieval events. Its session id is `<case>#claim<n>`, which becomes
the calibration item id, which is the granularity a human labels at anyway: "is
*this sentence* supported by *that passage*" is answerable in seconds, and "is
this whole answer faithful" is an argument.
"""

from __future__ import annotations

from typing import Sequence

from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import EventKind, Trace

from ragcheck.corpus import Chunk, Retrieval

__all__ = [
    "ADAPTER",
    "RETRIEVE_TOOL",
    "rag_trace",
    "claim_trace",
    "retrieval_of",
    "question_of",
    "answer_of",
    "as_retrieval",
]

#: Named in `Trace.adapter` so a mixed corpus of traces can be filtered by where
#: it came from without parsing ids.
ADAPTER = "rag:text"

#: The tool name a retrieval step is recorded under. One constant, because a
#: check that greps for "retrieve" in three spellings is a check that misses one.
RETRIEVE_TOOL = "retrieve"

# Fixed, fake, monotonic timing. A RAG trace built here carries no real latency
# measurement, and inventing plausible-looking timestamps would let somebody
# quote a p95 that never happened. `lab.voice` measures latency from traces that
# actually recorded it; these are structural traces, and the timestamps only
# order the events.
_STEP = 0.1


def rag_trace(
    *,
    case_id: str,
    question: str,
    retrieval: Retrieval,
    answer: str | None = None,
    session_id: str | None = None,
    k: int | None = None,
) -> Trace:
    """One question, one retrieval, and optionally one answer, as a trace."""
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id=case_id,
        adapter=ADAPTER,
        session_id=session_id or case_id,
        clock=clock,
    )
    builder.session_start()
    clock.advance(_STEP)
    builder.caller_utterance(question)
    clock.advance(_STEP)
    call = builder.tool_call(
        RETRIEVE_TOOL,
        {"query": retrieval.query, "k": k if k is not None else len(retrieval.chunks)},
    )
    clock.advance(_STEP)
    builder.tool_result(
        RETRIEVE_TOOL,
        {
            "chunks": [
                {
                    "id": chunk.id,
                    "title": chunk.title,
                    "section": chunk.section,
                    "text": chunk.text.strip(),
                    "score": score,
                }
                for chunk, score in zip(
                    retrieval.chunks, retrieval.scores or [None] * len(retrieval.chunks)
                )
            ]
        },
        call_id=call.get("call_id"),
    )
    if answer is not None:
        clock.advance(_STEP)
        builder.agent_utterance(answer, agent="rag")
    clock.advance(_STEP)
    builder.session_end(reason="answered" if answer else "retrieved_only", turns=1)
    return builder.build()


def claim_trace(
    *,
    case_id: str,
    question: str,
    retrieval: Retrieval,
    claim: str,
    index: int,
    kind: str = "claim",
) -> Trace:
    """A trace whose single agent utterance is one claim from an answer.

    `kind` distinguishes a claim taken from the generated answer ("claim") from
    one taken from a reference answer ("ref"), because they feed different
    metrics — groundedness and context recall respectively — and their item ids
    must not collide in a label file.
    """
    return rag_trace(
        case_id=case_id,
        question=question,
        retrieval=retrieval,
        answer=claim,
        session_id=f"{case_id}#{kind}{index}",
    )


def retrieval_of(trace: Trace) -> list[Chunk]:
    """The chunks the last `retrieve` call returned, in rank order.

    Reads the trace rather than being handed the retrieval, so a trace loaded
    from disk months later yields the same context a judge was shown.
    """
    chunks: list[Chunk] = []
    for event in trace.events:
        if event.kind != EventKind.TOOL_RESULT or event.get("name") != RETRIEVE_TOOL:
            continue
        result = event.get("result") or {}
        chunks = [
            Chunk(
                id=str(item.get("id", "")),
                text=str(item.get("text", "")),
                title=str(item.get("title", "")),
                section=str(item.get("section", "")),
            )
            for item in result.get("chunks", [])
        ]
    return chunks


def question_of(trace: Trace) -> str:
    """The first caller utterance. Empty string when there is none."""
    for event in trace.events:
        if event.kind == EventKind.CALLER_UTTERANCE:
            return str(event.get("text", ""))
    return ""


def answer_of(trace: Trace) -> list[str]:
    """Every agent utterance, in order. A claim trace has exactly one."""
    return [
        str(event.get("text", ""))
        for event in trace.events
        if event.kind == EventKind.AGENT_UTTERANCE
    ]


def as_retrieval(query: str, chunks: Sequence[Chunk]) -> Retrieval:
    """Wrap chunks as a `Retrieval` with no scores. For traces read off disk."""
    return Retrieval(query=query, chunks=list(chunks))
