"""Three RAG judges, built out of `lab.judges` rather than beside it.

WHAT THIS DEMONSTRATES
----------------------
Not one line of judging machinery is reimplemented here. `RagJudge` subclasses
`lab.judges.judge.Judge` and overrides exactly one method — `fields()`, which
says what a prompt may talk about — so a RAG judge inherits, for free and
without a fork:

*   the binary-verdict-plus-critique contract, and the parser that refuses
    anything else;
*   fail-closed on unparseable output, counted separately rather than defaulted
    to pass;
*   prompt versioning, with a digest that invalidates a stale recording;
*   `calibrate()` and `require_calibrated()`, so a RAG judge cannot gate a build
    on agreement nobody has measured.

That last one is the whole reason for doing it this way. The temptation with a
new metric family is to write a fresh scorer with a fresh scoring loop, and the
calibration discipline quietly does not come along.

THE THREE JUDGES
----------------
    claim_support        is this one statement supported by these passages?
    answer_relevance     does this answer address the question asked?
    passage_relevance    was this one retrieved passage worth retrieving?

`claim_support` serves **both** groundedness and context recall. The question is
identical — is this statement supported by this context — and only the source of
the statements differs: the generated answer for groundedness, the reference
answer for context recall. Ragas ships two prompts for the two metrics; I could
not justify the second, and one prompt means one calibration and one set of
disagreements for a human to read instead of two.

WHY `{{claim}}` AND `{{answer}}` ARE SEPARATE FIELDS
---------------------------------------------------
They render the same string. `{{claim}}` additionally *asserts* that the trace
carries exactly one agent utterance, and raises if it does not. A prompt asking
about "the statement" while being handed six sentences is the kind of error that
does not fail — it produces verdicts, and the verdicts look like data. So the
field a prompt chooses is also the guard it gets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from lab.judges.judge import (
    Completion,
    Judge,
    JudgeError,
    PromptTemplate,
    model_from_env,
)
from lab.trace.schema import Trace

from ragcheck.corpus import Retrieval
from ragcheck.traces import answer_of, question_of, retrieval_of

__all__ = [
    "PROMPT_DIR",
    "RagPromptTemplate",
    "RagJudge",
    "claim_support_judge",
    "answer_relevance_judge",
    "passage_relevance_judge",
    "SYSTEM_PROMPT",
]

#: Prompts are text and text belongs in files, reviewable by whoever owns the
#: rubric rather than whoever owns the code.
PROMPT_DIR = Path(__file__).parent / "prompts"

#: Deliberately different from `lab.judges.judge.DEFAULT_SYSTEM`: the instruction
#: that matters most for a grounding judge is that outside knowledge is not
#: evidence, and that is not something a transcript judge needs to be told.
SYSTEM_PROMPT = (
    "You are a careful evaluator of retrieval-augmented answers. You answer "
    "exactly one binary question about the material you are given, you treat the "
    "supplied passages as the only evidence that exists, and you follow the "
    "required output format exactly."
)


class RagPromptTemplate(PromptTemplate):
    """A prompt that may talk about a question, a context, an answer or a claim.

    Extends the base field list rather than replacing it, so a RAG prompt can
    still render `{{transcript}}` when the whole turn is what needs grading.
    `require_transcript` defaults to False here for the same reason: the natural
    RAG prompt shows the passages and the statement, not a dialogue.
    """

    FIELDS: tuple[str, ...] = PromptTemplate.FIELDS + (
        "question",
        "context",
        "answer",
        "claim",
    )

    def __init__(self, text: str, *, require_transcript: bool = False) -> None:
        super().__init__(text, require_transcript=require_transcript)
        if not any(field in self.placeholders for field in ("question", "context", "answer", "claim", "transcript")):
            raise JudgeError(
                "a RAG prompt with none of {{question}}, {{context}}, {{answer}}, "
                "{{claim}} or {{transcript}} would grade nothing at all"
            )

    @classmethod
    def from_path(cls, path: str | Path, **kwargs: Any) -> RagPromptTemplate:
        return cls(Path(path).read_text(encoding="utf-8"), **kwargs)


class RagJudge(Judge):
    """A judge whose prompt is rendered from the RAG parts of a trace."""

    def __init__(self, *, prompt: str | PromptTemplate, **kwargs: Any) -> None:
        template = prompt if isinstance(prompt, PromptTemplate) else RagPromptTemplate(prompt)
        kwargs.setdefault("system", SYSTEM_PROMPT)
        super().__init__(prompt=template, **kwargs)

    def fields(self, trace: Trace) -> dict[str, str]:
        base = super().fields(trace)
        chunks = retrieval_of(trace)
        utterances = answer_of(trace)
        placeholders = self.template.placeholders

        if "answer" in placeholders or "claim" in placeholders:
            if not utterances:
                raise JudgeError(
                    f"{self.name} {self.version} was asked about the answer in trace "
                    f"{trace.session_id!r}, which has no agent utterance: there is "
                    "nothing to grade, and rendering an empty field would produce a "
                    "verdict about nothing"
                )
        if "claim" in placeholders and len(utterances) != 1:
            raise JudgeError(
                f"{self.name} {self.version} uses {{{{claim}}}} and so asks about one "
                f"statement, but trace {trace.session_id!r} carries {len(utterances)} "
                "agent utterances. Split the answer with ragcheck.claims.split_claims "
                "and judge one claim_trace at a time."
            )
        if "context" in placeholders and not chunks:
            raise JudgeError(
                f"{self.name} {self.version} was asked about the retrieved context in "
                f"trace {trace.session_id!r}, which records no retrieve result. A "
                "support verdict against an empty context is not a measurement."
            )

        answer = " ".join(utterances)
        base.update(
            question=question_of(trace),
            context=Retrieval(query=question_of(trace), chunks=chunks).render(),
            answer=answer,
            claim=answer,
        )
        return base

    def with_prompt(  # type: ignore[override]
        self, prompt: str | PromptTemplate, *, version: str, **overrides: Any
    ) -> RagJudge:
        """Same iteration primitive as `Judge.with_prompt`, returning a RagJudge.

        Overridden because the base implementation constructs a `Judge`, which
        would silently drop the RAG fields on a v1 -> v2 prompt edit — and the
        prompt edit is exactly when you are least likely to notice.
        """
        kwargs: dict[str, Any] = {
            "name": self.name,
            "prompt": prompt,
            "version": version,
            "model": self.model,
            "completion": self.completion,
            "system": self.system,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "include_tools": self.include_tools,
            "strict": self.strict,
        }
        kwargs.update(overrides)
        return RagJudge(**kwargs)


def _build(
    name: str,
    stem: str,
    *,
    completion: Completion | None,
    model: str | None,
    version: str,
    strict: bool,
    extra: Mapping[str, Any] | None = None,
) -> RagJudge:
    template = RagPromptTemplate.from_path(PROMPT_DIR / f"{stem}_{version}.md")
    kwargs: dict[str, Any] = {
        "name": name,
        "prompt": template,
        "version": version,
        # No model id is hardcoded in this package, for the reason `lab` gives:
        # providers, prices and ids move, and a harness that pins one has an
        # expiry date. Offline callers pass the stand-in's name explicitly.
        "model": model or model_from_env(),
        "strict": strict,
    }
    if completion is not None:
        kwargs["completion"] = completion
    kwargs.update(extra or {})
    return RagJudge(**kwargs)


def claim_support_judge(
    *,
    completion: Completion | None = None,
    model: str | None = None,
    version: str = "v1",
    strict: bool = True,
) -> RagJudge:
    """Is this one statement supported by these passages?

    The engine behind both groundedness (statements from the answer) and context
    recall (statements from the reference answer).
    """
    return _build(
        "claim_support",
        "claim_support",
        completion=completion,
        model=model,
        version=version,
        strict=strict,
    )


def answer_relevance_judge(
    *,
    completion: Completion | None = None,
    model: str | None = None,
    version: str = "v1",
    strict: bool = True,
) -> RagJudge:
    """Does this answer address the question that was asked?"""
    return _build(
        "answer_relevance",
        "answer_relevance",
        completion=completion,
        model=model,
        version=version,
        strict=strict,
    )


def passage_relevance_judge(
    *,
    completion: Completion | None = None,
    model: str | None = None,
    version: str = "v1",
    strict: bool = True,
) -> RagJudge:
    """Was this one retrieved passage worth retrieving?"""
    return _build(
        "passage_relevance",
        "passage_relevance",
        completion=completion,
        model=model,
        version=version,
        strict=strict,
    )
