"""Tests for `lab.voice.wer` — known text pairs, scored raw and normalised.

WHAT THIS DEMONSTRATES
----------------------
Three things worth more than the rest:

* `test_builtin_backend_agrees_with_jiwer` — the pure-standard-library fallback
  is checked against the reference implementation on every pair in the corpus. A
  fallback nobody compares to the real thing is a second bug surface pretending
  to be resilience.
* `test_normalisation_does_not_hide_a_genuine_mishearing` — the pair that must
  *not* be absorbed. It is easy to write a normaliser that lowers WER by
  destroying information; the guard against that is a test that insists a real
  substitution survives it.
* `test_seven_thirty_is_not_a_number` — the number parser refuses invalid
  cardinal compositions, so a spoken time stays two tokens instead of collapsing
  into 37. Without that rule the normaliser would *raise* WER on precisely the
  utterances a restaurant-booking agent hears most.
"""

from __future__ import annotations

import pytest

import lab.voice.wer as wer_module
from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.voice.wer import (
    CONTRACTIONS,
    available_backends,
    corpus_wer,
    normalise,
    trace_wer,
    wer,
)

#: Pairs used both for the backend-agreement test and the corpus tests. A mix of
#: clean matches, surface-form-only differences, and genuine mishearings.
CORPUS: list[tuple[str, str]] = [
    ("a table for four at eight", "a table for four at eight"),
    ("a table for twenty six, please", "a table for 26 please"),
    ("we would like the fourteenth", "we would like the 14th"),
    ("book it for seven thirty", "book it for 7 30"),
    ("a table for four", "a table for five"),
    ("do you have a high chair", "do you have a high chair or two"),
    ("cancel booking B seven seven", "cancel booking b 77"),
    ("nothing like the reference at all", "completely different words here"),
]


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Table for twenty six, please!", "table for 26 please"),
        ("Don't cancel it.", "do not cancel it"),
        ("We'd like a table at seven thirty", "we would like a table at 7 30"),
        ("the fourteenth of March", "the 14th of march"),
        ("twenty first", "21st"),
        ("thirty first of December", "31st of december"),
        ("one hundred and twenty six covers", "126 covers"),
        ("It's 7:30", "it is 7 30"),
        ("a table for two", "a table for 2"),
        ("Nine o'clock", "9 oclock"),
        ("zero", "0"),
        ("hundred", "hundred"),
        ("bread and butter", "bread and butter"),
        ("  ragged   spacing  ", "ragged spacing"),
    ],
)
def test_normalise_examples(text: str, expected: str) -> None:
    assert normalise(text) == expected


def test_seven_thirty_is_not_a_number() -> None:
    """The decreasing-magnitude rule: 30 cannot continue 7, so the run breaks.

    A naive left-to-right accumulator would produce "37" here, which is worse
    than doing nothing: it turns a matching pair into two errors.
    """
    assert normalise("seven thirty") == "7 30"
    assert normalise("seven thirty") == normalise("7:30")


def test_contractions_expand_before_punctuation_is_stripped() -> None:
    """Order matters: strip first and every contraction becomes a nonsense word."""
    assert normalise("don't") == "do not"
    assert "dont" not in normalise("I don't think so")
    # Every mapping is reachable through the public normaliser.
    for contraction, expansion in CONTRACTIONS.items():
        assert normalise(contraction) == normalise(expansion)


def test_normalisation_is_idempotent() -> None:
    """Normalising twice must equal normalising once, or comparisons drift."""
    for text, _ in CORPUS:
        once = normalise(text)
        assert normalise(once) == once


def test_normaliser_is_a_function_of_one_string() -> None:
    """It cannot peek at the other side, so it cannot be biased toward matching."""
    reference = "a table for twenty six"
    assert normalise(reference) == normalise(reference)
    # Same input, different partner, same output.
    assert (
        wer(reference, "a table for 26").reference_normalised
        == wer(reference, "totally unrelated text").reference_normalised
    )


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def test_identical_text_scores_zero_both_ways() -> None:
    result = wer("a table for four at eight", "a table for four at eight")
    assert result.raw.wer == 0.0
    assert result.normalised.wer == 0.0
    assert result.raw.hits == 6
    assert result.raw.reference_words == 6


def test_surface_form_difference_is_absorbed_by_normalisation() -> None:
    result = wer("Table for twenty six, please", "table for 26 please")
    assert result.raw.wer > 0.0
    assert result.normalised.wer == 0.0
    assert result.errors_absorbed_by_normalisation == result.raw.errors
    assert "normalisation absorbed" in result.describe()


def test_normalisation_does_not_hide_a_genuine_mishearing() -> None:
    """One substitution in four reference words must survive normalisation."""
    result = wer("a table for four", "a table for five")
    assert result.normalised.wer == pytest.approx(0.25)
    assert result.normalised.substitutions == 1
    assert result.normalised.errors == 1
    assert result.normalised.reference_words == 4
    assert result.errors_absorbed_by_normalisation == 0
    assert "1 errors / 4 reference words" in result.normalised.describe()


@pytest.mark.parametrize(
    ("reference", "hypothesis", "substitutions", "deletions", "insertions"),
    [
        ("book a table for four", "book a table for five", 1, 0, 0),
        ("book a table for four", "book table for four", 0, 1, 0),
        ("book a table for four", "book a nice table for four", 0, 0, 1),
        ("book a table for four", "", 0, 5, 0),
    ],
)
def test_edit_classes_are_distinguished(
    reference: str,
    hypothesis: str,
    substitutions: int,
    deletions: int,
    insertions: int,
) -> None:
    """S, D and I are different bugs, so they are counted separately.

    A substitution means the engine misheard; a run of insertions means it
    hallucinated on noise; a run of deletions means it dropped audio. A single
    "WER" figure cannot tell those apart.
    """
    score = wer(reference, hypothesis).raw
    assert (score.substitutions, score.deletions, score.insertions) == (
        substitutions,
        deletions,
        insertions,
    )
    assert score.errors == substitutions + deletions + insertions


def test_empty_hypothesis_is_total_loss_not_a_zero_score() -> None:
    score = wer("book a table for four", "").raw
    assert score.wer == pytest.approx(1.0)
    assert score.deletions == 5
    assert score.hits == 0


def test_empty_reference_is_refused() -> None:
    with pytest.raises(ValueError, match="undefined for an empty reference"):
        wer("", "something was heard")


def test_scores_print_numerator_and_denominator() -> None:
    described = wer("a table for four", "a table for five").raw.describe()
    assert "errors /" in described and "reference words" in described
    assert "S=1 D=0 I=0" in described


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #


def test_builtin_backend_agrees_with_jiwer() -> None:
    """The fallback must match the reference implementation, pair by pair."""
    if "jiwer" not in available_backends():
        pytest.skip("jiwer is not installed in this environment")
    for reference, hypothesis in CORPUS:
        with_jiwer = wer(reference, hypothesis, backend="jiwer")
        builtin = wer(reference, hypothesis, backend="builtin")
        assert with_jiwer.backend == "jiwer"
        assert builtin.backend == "builtin"
        for attribute in ("raw", "normalised"):
            left = getattr(with_jiwer, attribute)
            right = getattr(builtin, attribute)
            assert left.wer == pytest.approx(right.wer, abs=1e-12), (
                reference,
                hypothesis,
                attribute,
            )
            assert left.errors == right.errors
            assert left.reference_words == right.reference_words


def test_auto_backend_falls_back_when_jiwer_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zero-optional-dependency install must still score, just via the fallback."""
    monkeypatch.setattr(wer_module, "_import_jiwer", lambda: None)
    assert available_backends() == ("builtin",)
    result = wer("a table for four", "a table for five")
    assert result.backend == "builtin"
    assert result.normalised.wer == pytest.approx(0.25)


def test_explicit_jiwer_backend_fails_loudly_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking for jiwer by name and silently getting something else would be worse."""
    monkeypatch.setattr(wer_module, "_import_jiwer", lambda: None)
    with pytest.raises(RuntimeError, match="jiwer is not installed"):
        wer("a table for four", "a table for five", backend="jiwer")


def test_module_states_that_wer_is_harness_relative() -> None:
    """The honesty claim is load-bearing, so it is asserted rather than trusted."""
    assert "HARNESS-RELATIVE" in (wer_module.__doc__ or "")
    corpus = corpus_wer(CORPUS)
    assert "HARNESS-RELATIVE" in corpus.describe()
    assert "regression signal" in corpus.to_markdown()


# --------------------------------------------------------------------------- #
# Corpus aggregation
# --------------------------------------------------------------------------- #


def test_micro_and_macro_are_both_reported_and_differ() -> None:
    """One mangled short utterance dominates macro and barely moves micro.

    pair 1: 1 error / 1 word   -> WER 1.0
    pair 2: 0 errors / 9 words -> WER 0.0
    micro = 1/10 = 0.1        macro = (1.0 + 0.0) / 2 = 0.5
    """
    corpus = corpus_wer(
        [
            ("yes", "no"),
            (
                "i would like to book a table for four",
                "i would like to book a table for four",
            ),
        ]
    )
    assert corpus.n == 2
    assert corpus.micro_wer(normalised=True) == pytest.approx(0.1)
    assert corpus.macro_wer(normalised=True) == pytest.approx(0.5)
    described = corpus.describe()
    assert "micro" in described and "macro" in described
    assert "1/10 words" in described


def test_micro_is_total_errors_over_total_reference_words() -> None:
    corpus = corpus_wer(CORPUS)
    total_errors = sum(item.normalised.errors for item in corpus.utterances)
    total_words = sum(item.normalised.reference_words for item in corpus.utterances)
    assert corpus.micro_wer(normalised=True) == pytest.approx(
        total_errors / total_words
    )
    assert f"{total_errors}/{total_words} words" in corpus.describe()


def test_empty_corpus_reports_none_rather_than_zero() -> None:
    corpus = corpus_wer([])
    assert corpus.n == 0
    assert corpus.micro_wer(normalised=True) is None
    assert corpus.macro_wer(normalised=False) is None
    assert "nothing to report" in corpus.describe()
    assert "n/a" in corpus.to_markdown()


def test_pairs_with_an_empty_reference_are_skipped_not_fatal() -> None:
    corpus = corpus_wer([("", "heard something"), ("a table for four", "a table for four")])
    assert corpus.n == 1


def test_worst_surfaces_the_utterance_to_read() -> None:
    corpus = corpus_wer(CORPUS)
    worst = corpus.worst(limit=2)
    assert len(worst) == 2
    assert worst[0].normalised.wer >= worst[1].normalised.wer
    # The words are on the object, because a WER row you cannot read is useless.
    assert worst[0].reference and worst[0].hypothesis


# --------------------------------------------------------------------------- #
# Trace integration
# --------------------------------------------------------------------------- #


def test_trace_wer_pairs_caller_text_with_the_transcript() -> None:
    builder = TraceBuilder(
        scenario_id="booking-noisy", adapter="test:synthetic", clock=FakeClock()
    )
    builder.session_start(ts=0.0)
    builder.caller_utterance("a table for twenty six", ts=1.0)
    builder.transcript_in("a table for 26", confidence=0.91, ts=1.4, engine="stt-x")
    builder.caller_utterance("at seven thirty", ts=3.0)
    builder.transcript_in("at seven thirteen", confidence=0.62, ts=3.3, engine="stt-x")
    builder.session_end(turns=2, ts=4.0)

    corpus = trace_wer(builder.build())
    assert corpus.n == 2
    # First pair is surface form only; second is a real mishearing.
    assert corpus.utterances[0].normalised.wer == 0.0
    assert corpus.utterances[1].normalised.errors == 1


def test_trace_wer_drops_unpaired_turns_and_the_gap_is_visible() -> None:
    """A turn with no transcript at all is excluded, not scored as 100% error.

    A dropped turn is a worse failure than a misheard one, but it is a different
    failure. Burying it in a WER average would hide it; leaving it out makes the
    count mismatch the caller-turn count, which is where it shows up.
    """
    builder = TraceBuilder(
        scenario_id="booking-noisy", adapter="test:synthetic", clock=FakeClock()
    )
    builder.session_start(ts=0.0)
    builder.caller_utterance("a table for four", ts=1.0)
    builder.transcript_in("a table for four", ts=1.2)
    builder.caller_utterance("actually make it six", ts=2.0)  # never transcribed
    builder.caller_utterance("thank you", ts=3.0)
    builder.transcript_in("thank you", ts=3.2)
    builder.session_end(turns=3, ts=4.0)
    trace = builder.build()

    corpus = trace_wer(trace)
    assert corpus.n == 2
    assert len(trace.events_of_kind("caller_utterance")) == 3
