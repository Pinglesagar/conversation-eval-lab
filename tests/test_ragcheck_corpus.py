"""Corpus, retriever, dataset validation, and the claim splitter.

WHAT THIS DEMONSTRATES
----------------------
The unglamorous half of an eval suite, which is where the bugs that corrupt a
metric silently actually live:

*   a label that names a chunk which does not exist, so the question is
    unanswerable and the retriever takes the blame;
*   a retriever whose ties resolve differently on Tuesday;
*   a claim splitter whose denominator moves under a metric.

`test_a_gold_id_that_names_no_chunk_is_refused_at_load_time` is the one I would
point at. In a production categorisation review I ran, 79 of 163 apparent
failures turned out to be label errors rather than defects; they all looked like
this, and every one of them was findable by a validator that runs before the
measurement rather than after the argument.
"""

from __future__ import annotations

import pytest

from ragcheck.claims import split_claims, split_sentences
from ragcheck.corpus import (
    Chunk,
    Corpus,
    LexicalRetriever,
    PinnedRetriever,
    Retrieval,
    load_corpus,
)
from ragcheck.dataset import RagCase, RagDataset, load_cases
from ragcheck.text import content_words, numbers, overlap, stem


# --------------------------------------------------------------------------- #
# the committed fixtures
# --------------------------------------------------------------------------- #


def test_the_fixture_corpus_and_case_set_load_and_agree() -> None:
    """16 chunks, 18 questions, every gold id naming a real chunk."""
    corpus = load_corpus()
    dataset = load_cases(corpus=corpus)
    assert len(corpus) == 16
    assert len(dataset) == 18
    dataset.validate_against(corpus)  # raises if any id is unknown
    assert len(dataset.answered()) == 8


def test_a_gold_id_that_names_no_chunk_is_refused_at_load_time() -> None:
    """And the error names every bad id, not just the first one.

    A validator that stops at the first problem turns a five-minute fix into
    five runs, which is how validation ends up switched off.
    """
    corpus = Corpus(chunks=[Chunk(id="p01", text="only chunk")])
    dataset = RagDataset(
        cases=[
            RagCase(id="a", question="q", gold=["p01"]),
            RagCase(id="b", question="q", gold=["p99"]),
            RagCase(id="c", question="q", gold=["p01"], retrieved=["p98"]),
        ]
    )
    with pytest.raises(ValueError) as excinfo:
        dataset.validate_against(corpus)
    message = str(excinfo.value)
    assert "p99" in message and "p98" in message


def test_duplicate_ids_are_refused_in_both_the_corpus_and_the_case_set() -> None:
    """A duplicate is a silently double-weighted item, which no total reveals."""
    with pytest.raises(ValueError, match="duplicate chunk id"):
        Corpus(chunks=[Chunk(id="p01", text="a"), Chunk(id="p01", text="b")])
    with pytest.raises(ValueError, match="duplicate case id"):
        RagDataset(
            cases=[
                RagCase(id="a", question="q", gold=["p01"]),
                RagCase(id="a", question="q", gold=["p01"]),
            ]
        )
    with pytest.raises(ValueError, match="duplicate gold ids"):
        RagCase(id="a", question="q", gold=["p01", "p01"])


# --------------------------------------------------------------------------- #
# the retriever
# --------------------------------------------------------------------------- #


def test_ties_resolve_by_chunk_id_so_a_ranking_is_reproducible() -> None:
    """Two chunks with identical text score identically; order is by id.

    A flaky ranking makes a flaky recall figure, and a suite that fails one run
    in five teaches everyone to re-run the build instead of reading it.
    """
    corpus = Corpus(
        chunks=[
            Chunk(id="p02", text="deposits are taken at booking"),
            Chunk(id="p01", text="deposits are taken at booking"),
        ]
    )
    retriever = LexicalRetriever(corpus)
    first = retriever.retrieve("deposits", k=2).ids
    assert first == ["p01", "p02"]
    assert all(retriever.retrieve("deposits", k=2).ids == first for _ in range(5))


def test_a_zero_scoring_chunk_is_not_returned_to_pad_the_window() -> None:
    """Padding to k with passages the retriever itself rates irrelevant would
    raise recall@k for free."""
    corpus = Corpus(
        chunks=[
            Chunk(id="p01", text="deposits are taken at booking"),
            Chunk(id="p02", text="the terrace is open to dogs"),
        ]
    )
    result = LexicalRetriever(corpus).retrieve("deposit", k=2)
    assert result.ids == ["p01"]
    assert len(result.scores) == 1


def test_the_retriever_finds_the_gold_chunk_for_a_question_using_other_words() -> None:
    """c07 asks about "the private dining room"; the chunk says "Cellar Room".

    And it works for a thin reason worth naming: the only word the two have in
    common is "room". Neither "private" nor "dining" appears in any chunk, so
    lexical retrieval gets this one right by a single shared noun, and would miss
    it entirely if the passage said "the Cellar". That is what an embedding buys,
    and it is why the recorded baseline in test_ragcheck_retrieval.py is a
    baseline rather than a target.
    """
    corpus = load_corpus()
    assert "p07" in LexicalRetriever(corpus).retrieve(
        "How many people fit in the private dining room?", k=3
    ).ids


def test_a_pinned_retriever_returns_exactly_what_it_was_given() -> None:
    corpus = load_corpus()
    pinned = PinnedRetriever(corpus, {"q": ["p05", "p01"]})
    assert pinned.retrieve("q", k=3).ids == ["p05", "p01"]
    assert pinned.retrieve("q", k=1).ids == ["p05"]
    with pytest.raises(KeyError):
        pinned.retrieve("unknown question", k=1)


def test_a_rendered_context_is_numbered_and_cited() -> None:
    """"Every answer cites its source" is only testable if the source is named."""
    corpus = load_corpus()
    rendered = Retrieval(query="q", chunks=corpus.select(["p02", "p03"])).render()
    assert rendered.startswith("[1] p02 (Cancellation window")
    assert "[2] p03" in rendered
    assert Retrieval(query="q").render() == "(nothing was retrieved)"


def test_rare_terms_ignore_words_the_corpus_has_never_seen() -> None:
    """Absent words have the highest idf of all, and match nothing.

    A "what is this question about" built out of them selects for the part of the
    question the corpus cannot answer — which is how a relevance heuristic ends
    up keying on "much" and "need".
    """
    corpus = load_corpus()
    assert corpus.rare_terms("Is there a dress code for the Cellar Room?") == ["code", "dres"]
    assert corpus.rare_terms("How much notice do I need to cancel?") == ["notic", "cancel"]


# --------------------------------------------------------------------------- #
# the lexical layer
# --------------------------------------------------------------------------- #


def test_the_stemmer_collides_the_inflections_that_matter_here() -> None:
    """Each pair below is one a support check gets wrong if they do not meet."""
    assert stem("cancelled") == stem("cancelling") == stem("cancel")
    assert stem("charged") == stem("charge") == stem("charges")
    assert stem("parties") == stem("party")
    assert stem("minutes") == stem("minute")
    # And one it does not: the suffix rule would leave a two-character stem, so
    # it is skipped, and these two never meet. Written down rather than hidden.
    assert stem("used") != stem("use")


def test_numbers_are_extracted_separately_from_words() -> None:
    """The figure check is the only part of the offline oracle that is not a
    similarity heuristic, and it is the part that catches GBP 25 against 15."""
    assert numbers("It is GBP 25 per person") == {"25"}
    assert numbers("held for 20 minutes past 18:00") == {"20", "18", "00"}
    assert numbers("no figures here") == set()


def test_overlap_is_asymmetric_because_the_question_is_asymmetric() -> None:
    """"Is all of this claim in that passage" — not "are these two similar"."""
    claim = "the deposit is retained"
    passage = "A booking may be cancelled free of charge up to 48 hours before the reservation time. Inside 48 hours the deposit is retained in full."
    assert overlap(claim, passage) == 1.0
    assert overlap(passage, claim) < 1.0
    assert overlap("", passage) == 0.0


def test_content_words_keep_the_words_that_carry_the_comparison() -> None:
    words = content_words("Vouchers may not be used against the deposit")
    assert "not" in words  # a stoplist that drops this decides support wrongly
    assert "the" not in words


# --------------------------------------------------------------------------- #
# claim splitting: the denominator of every support metric
# --------------------------------------------------------------------------- #


def test_a_sentence_with_two_assertions_is_split_into_two_claims() -> None:
    """One true half and one invented half must be able to score 1/2.

    Left as a single claim, this answer scores 1/1 or 0/1 and is wrong either
    way, and no reviewer can tell which sentence to fix.
    """
    claims = split_claims(
        "We hold your table for 20 minutes past the booked time, and after that "
        "we will phone you to check you are still coming."
    )
    assert claims == [
        "We hold your table for 20 minutes past the booked time.",
        "After that we will phone you to check you are still coming.",
    ]


def test_splitting_is_deterministic_so_the_denominator_cannot_drift() -> None:
    """The property that makes a groundedness fraction comparable across runs.

    An LLM decomposer — what Ragas and DeepEval both use — chooses this
    denominator at run time, so the same answer can score 3/4 and then 4/5 with
    nothing about the system under test having changed.
    """
    text = "Vouchers last 12 months. They may not pay a deposit; food only."
    assert len({tuple(split_claims(text)) for _ in range(20)}) == 1


def test_a_number_does_not_end_a_sentence_and_an_abbreviation_does_not_either() -> None:
    assert split_sentences("The deposit is GBP 15. It is per person.") == [
        "The deposit is GBP 15.",
        "It is per person.",
    ]
    assert len(split_sentences("Allergens, e.g. nuts, are noted on the record.")) == 1


def test_fragments_and_questions_are_dropped() -> None:
    """"Yes." asserts nothing about the world; the next sentence does."""
    assert split_claims("Yes. Shall I book it? A deposit of GBP 15 applies.") == [
        "A deposit of GBP 15 applies."
    ]
