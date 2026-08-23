"""The corpus and a retriever over it, both deliberately small.

WHAT THIS DEMONSTRATES
----------------------
A retriever is the *system under test* for half of this package, so it needs to
be real enough to fail in the ways real ones do and simple enough that every
number it produces can be hand-checked. `LexicalRetriever` is idf-weighted term
overlap: no embeddings, no index, no network, about thirty lines.

Two properties matter more than its quality:

*   **It is deterministic, including its ties.** Two chunks with the same score
    come back in chunk-id order, always. A retriever whose ties resolve by dict
    ordering produces an eval suite that fails one run in five and teaches
    everyone to re-run the build.
*   **It is swappable.** Everything downstream consumes a `Retrieval` — a
    ranked list of `(chunk, score)` — so pointing the same metrics at a real
    vector store is a constructor change, not a rewrite. `ragcheck.dataset`
    rows can also pin their own retrieved ids, which is how the generation
    metrics are measured against a fixed context.

WHAT A REAL ONE WOULD ADD, AND WHY IT DOES NOT CHANGE THE METRICS
-----------------------------------------------------------------
Chunking strategy, an embedding model, hybrid dense+sparse scoring, a reranker,
metadata filters. Every one of those changes the *ranking* and none of them
changes what recall@k means or how it is computed, which is the whole argument
for measuring the ranking rather than the machinery that produced it.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ragcheck.text import content_words

__all__ = [
    "CORPUS_PATH",
    "Chunk",
    "Corpus",
    "Retrieval",
    "Retriever",
    "LexicalRetriever",
    "PinnedRetriever",
    "load_corpus",
    "as_corpus",
]

#: The committed fixture corpus. A path, not an import, so a different corpus is
#: a command-line argument rather than a code change.
CORPUS_PATH = Path(__file__).parent / "fixtures" / "corpus.yaml"


class Chunk(BaseModel):
    """One retrievable passage, with the provenance a citation needs.

    `title` and `section` are not decoration. "Every answer cites its source" is
    a testable contract only if the source has a stable name, and a chunk id is
    not a name a human can check.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    title: str = ""
    section: str = ""

    @property
    def citation(self) -> str:
        """How this chunk is referred to in a rendered context block."""
        parts = [part for part in (self.title, self.section) if part]
        return f"{self.id} ({' — '.join(parts)})" if parts else self.id


class Corpus(BaseModel):
    """An ordered, id-unique collection of chunks, plus document statistics."""

    model_config = ConfigDict(extra="forbid")

    doc: str = ""
    chunks: list[Chunk]

    def model_post_init(self, _context: object) -> None:
        seen: set[str] = set()
        for chunk in self.chunks:
            if chunk.id in seen:
                raise ValueError(
                    f"duplicate chunk id {chunk.id!r}: gold ids would be ambiguous, "
                    "and a retrieval metric computed over ambiguous ids is not a "
                    "measurement of anything"
                )
            seen.add(chunk.id)

    def __iter__(self) -> Iterator[Chunk]:  # type: ignore[override]
        return iter(self.chunks)

    def __len__(self) -> int:
        return len(self.chunks)

    @property
    def ids(self) -> list[str]:
        return [chunk.id for chunk in self.chunks]

    def get(self, chunk_id: str) -> Chunk:
        """The chunk with this id, or a `KeyError` naming what was asked for."""
        for chunk in self.chunks:
            if chunk.id == chunk_id:
                return chunk
        raise KeyError(f"no chunk {chunk_id!r} in the corpus ({len(self)} chunks)")

    def select(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        """The named chunks, in the order named. Raises on an unknown id."""
        return [self.get(chunk_id) for chunk_id in chunk_ids]

    def document_frequency(self, term: str) -> int:
        """How many chunks contain `term` (already stemmed)."""
        return sum(1 for chunk in self.chunks if term in set(content_words(chunk.text)))

    def idf(self, term: str) -> float:
        """Smoothed inverse document frequency, floored at 1.0.

        `log((N + 1) / (df + 1)) + 1`. The +1s keep an unseen term finite and the
        floor keeps a term that appears in every chunk from scoring zero — a term
        everyone shares is weak evidence, not no evidence.
        """
        n = len(self.chunks)
        return math.log((n + 1) / (self.document_frequency(term) + 1)) + 1.0

    def rare_terms(self, text: str, *, limit: int = 2) -> list[str]:
        """The `limit` highest-idf content words in `text` that the corpus knows.

        Used to ask "what is this question actually about": in "is there a dress
        code for the Cellar Room", `dress` and `code` occur in one chunk each and
        `cellar`/`room` in two, so the rare terms are the two that name the
        subject rather than the two that name the place.

        Terms with a document frequency of zero are excluded first. They have the
        highest idf of all — nothing is rarer than absent — and they are exactly
        the words no passage can ever match, so a "focus" built out of them
        selects for the part of the question the corpus cannot answer. Ties are
        broken alphabetically so the result is stable.
        """
        known = {term for term in content_words(text) if self.document_frequency(term)}
        if not known:
            known = set(content_words(text))
        return sorted(known, key=lambda term: (-self.idf(term), term))[:limit]


class Retrieval(BaseModel):
    """What a retriever returned for one query: a ranked list, best first."""

    model_config = ConfigDict(extra="forbid")

    query: str
    chunks: list[Chunk] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)

    def model_post_init(self, _context: object) -> None:
        if self.scores and len(self.scores) != len(self.chunks):
            raise ValueError(
                f"{len(self.chunks)} chunks but {len(self.scores)} scores: a rank "
                "without a score cannot be audited"
            )

    @property
    def ids(self) -> list[str]:
        """Retrieved chunk ids, in rank order. The input to every ranking metric."""
        return [chunk.id for chunk in self.chunks]

    def top(self, k: int) -> list[Chunk]:
        if k < 1:
            raise ValueError(f"k must be at least 1, got {k}")
        return self.chunks[:k]

    def render(self, *, k: int | None = None) -> str:
        """The context block a judge is shown, numbered and cited.

        Rendered here rather than inside a prompt template so that every judge in
        this package sees a context in the same shape, and so the shape is
        reviewable in one place.
        """
        chosen = self.chunks if k is None else self.chunks[:k]
        if not chosen:
            return "(nothing was retrieved)"
        return "\n\n".join(
            f"[{index}] {chunk.citation}\n{chunk.text.strip()}"
            for index, chunk in enumerate(chosen, start=1)
        )


class Retriever:
    """The seam. Anything with this method can be measured by this package."""

    def retrieve(self, query: str, *, k: int) -> Retrieval:  # pragma: no cover - protocol
        raise NotImplementedError


class LexicalRetriever(Retriever):
    """idf-weighted term overlap. Deterministic, offline, and easily fooled.

    The score for a chunk is the idf-weighted fraction of the query's distinct
    content words that the chunk contains, so it lands in [0, 1] and is
    comparable across queries of different lengths. Chunks scoring zero are not
    returned at all: padding a result list to k with passages the retriever
    itself rates as irrelevant would inflate recall@k for free.
    """

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self._chunk_terms = {chunk.id: set(content_words(chunk.text)) for chunk in corpus}
        self._idf = {
            term: corpus.idf(term)
            for chunk in corpus
            for term in self._chunk_terms[chunk.id]
        }

    def score(self, query: str, chunk: Chunk) -> float:
        query_terms = set(content_words(query))
        if not query_terms:
            return 0.0
        total = sum(self._idf.get(term, 1.0) for term in query_terms)
        matched = sum(
            self._idf.get(term, 1.0)
            for term in query_terms
            if term in self._chunk_terms.get(chunk.id, set())
        )
        return matched / total if total else 0.0

    def retrieve(self, query: str, *, k: int) -> Retrieval:
        if k < 1:
            raise ValueError(f"k must be at least 1, got {k}")
        scored = [(self.score(query, chunk), chunk) for chunk in self.corpus]
        # Sort by score descending, then by id ascending. The second key is what
        # makes the ranking reproducible when scores tie, which on a corpus this
        # small happens constantly.
        scored.sort(key=lambda pair: (-pair[0], pair[1].id))
        kept = [(score, chunk) for score, chunk in scored[:k] if score > 0.0]
        return Retrieval(
            query=query,
            chunks=[chunk for _, chunk in kept],
            scores=[round(score, 6) for score, _ in kept],
        )


class PinnedRetriever(Retriever):
    """Returns a fixed context per query. For rows that pin their own ids.

    Not a mock of a retriever: it is how a generation metric is isolated from
    retrieval, so that a groundedness number moves only when generation changes.
    """

    def __init__(self, corpus: Corpus, pinned: dict[str, Sequence[str]]) -> None:
        self.corpus = corpus
        self.pinned = {query: list(ids) for query, ids in pinned.items()}

    def retrieve(self, query: str, *, k: int) -> Retrieval:
        try:
            ids = self.pinned[query]
        except KeyError as exc:
            raise KeyError(f"no pinned context for query {query!r}") from exc
        chunks = self.corpus.select(ids[:k])
        return Retrieval(query=query, chunks=chunks, scores=[1.0] * len(chunks))


def load_corpus(path: str | Path = CORPUS_PATH) -> Corpus:
    """Read a corpus from YAML. `safe_load` only: a corpus is data."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "chunks" not in raw:
        raise ValueError(f"{path}: expected a mapping with a 'chunks' key")
    return Corpus.model_validate(raw)


def as_corpus(chunks: Iterable[Chunk | dict[str, object]]) -> Corpus:
    """Build a corpus in memory. Convenience for tests and notebooks."""
    return Corpus(
        chunks=[c if isinstance(c, Chunk) else Chunk.model_validate(c) for c in chunks]
    )
