"""The evaluation set: questions, gold chunk ids, and optional answers.

WHAT THIS DEMONSTRATES
----------------------
A RAG eval set is a *labelled dataset*, and the label is the expensive part. So
it lives in YAML, validated on load, one row per question, reviewable in a pull
request by somebody who does not read Python — the same rule the scenario corpus
in this repository follows.

Three validations happen at load time rather than at measurement time, because
each one silently corrupts a metric otherwise:

*   **Every gold id exists in the corpus.** A typo'd gold id is an answer no
    retriever can ever return, so recall@k drops and the retriever takes the
    blame. This is the label error that costs the most and shows up the least;
    in one production categorisation review I ran, 79 of 163 apparent failures
    were label errors rather than defects, and they looked exactly like this.
*   **`gold` is non-empty and duplicate-free.** Recall's denominator is |gold|.
*   **Ids are unique across the set.** A duplicated row is a silently
    double-weighted question.

`answer` and `reference` are optional and mean different things. `answer` is
what the system under test said, and it is what groundedness and answer
relevance are computed over. `reference` is what a correct answer would have
contained, and it is what *context recall* is computed over — that is the whole
distinction between "did the generator stay inside its context" and "did the
context contain what an answer needed".
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ragcheck.corpus import Corpus

__all__ = ["CASES_PATH", "RagCase", "RagDataset", "load_cases"]

CASES_PATH = Path(__file__).parent / "fixtures" / "cases.yaml"


class RagCase(BaseModel):
    """One question, the chunks that answer it, and optionally what was said."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    gold: list[str] = Field(min_length=1, description="Chunk ids that genuinely answer the question.")
    retrieved: list[str] | None = Field(
        default=None,
        description=(
            "Pin the context instead of retrieving it. Set on rows whose purpose "
            "is a generation metric, so the number moves only when generation does."
        ),
    )
    answer: str | None = Field(default=None, description="What the system under test said.")
    reference: str | None = Field(
        default=None,
        description="What a correct answer would contain; the input to context recall.",
    )
    note: str = ""
    tags: list[str] = Field(default_factory=list)

    def model_post_init(self, _context: object) -> None:
        if len(set(self.gold)) != len(self.gold):
            raise ValueError(
                f"case {self.id}: duplicate gold ids {self.gold}; recall's denominator "
                "would count the same chunk twice"
            )
        if self.retrieved is not None and len(set(self.retrieved)) != len(self.retrieved):
            raise ValueError(f"case {self.id}: duplicate ids in pinned retrieved {self.retrieved}")

    @property
    def gold_set(self) -> set[str]:
        return set(self.gold)

    @property
    def has_answer(self) -> bool:
        return bool(self.answer and self.answer.strip())

    @property
    def has_reference(self) -> bool:
        return bool(self.reference and self.reference.strip())


class RagDataset(BaseModel):
    """A validated set of cases, checked against the corpus it refers to."""

    model_config = ConfigDict(extra="forbid")

    cases: list[RagCase]

    def model_post_init(self, _context: object) -> None:
        seen: set[str] = set()
        for case in self.cases:
            if case.id in seen:
                raise ValueError(f"duplicate case id {case.id!r}: it would be weighted twice")
            seen.add(case.id)

    def __iter__(self):  # type: ignore[override]
        return iter(self.cases)

    def __len__(self) -> int:
        return len(self.cases)

    def get(self, case_id: str) -> RagCase:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(f"no case {case_id!r} in this set ({len(self)} cases)")

    def answered(self) -> list[RagCase]:
        """Rows carrying an answer — the ones generation metrics apply to."""
        return [case for case in self.cases if case.has_answer]

    def validate_against(self, corpus: Corpus) -> None:
        """Raise unless every gold and pinned id names a real chunk.

        Collects every bad id before raising. A validator that stops at the first
        error turns a five-minute fix into five runs.
        """
        known = set(corpus.ids)
        problems: list[str] = []
        for case in self.cases:
            for chunk_id in case.gold:
                if chunk_id not in known:
                    problems.append(f"{case.id}: gold id {chunk_id!r} is not in the corpus")
            for chunk_id in case.retrieved or ():
                if chunk_id not in known:
                    problems.append(f"{case.id}: pinned id {chunk_id!r} is not in the corpus")
        if problems:
            raise ValueError(
                "the evaluation set refers to chunks that do not exist, so those "
                "questions are unanswerable by construction:\n  - "
                + "\n  - ".join(problems)
            )

    def pinned_contexts(self) -> dict[str, Sequence[str]]:
        """`{question: pinned ids}` for every row that pins one."""
        return {case.question: case.retrieved for case in self.cases if case.retrieved}


def load_cases(
    path: str | Path = CASES_PATH, *, corpus: Corpus | None = None
) -> RagDataset:
    """Read the evaluation set, and validate it against `corpus` when given."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "cases" not in raw:
        raise ValueError(f"{path}: expected a mapping with a 'cases' key")
    dataset = RagDataset.model_validate(raw)
    if corpus is not None:
        dataset.validate_against(corpus)
    return dataset
