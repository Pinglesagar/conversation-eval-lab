"""Word error rate, reported twice: raw and normalised.

WHAT THIS DEMONSTRATES
----------------------
**WER here is HARNESS-RELATIVE. It is a regression signal, not a production
estimate.** Read that before quoting any number this module produces.

The reference is what the simulated caller was *scripted* to say
(`caller_utterance`). The hypothesis is what the agent side transcribed
(`transcript_in`). The gap between them is transcription error — but whose? In
this harness the audio path is: scripted text -> harness TTS -> (perturbation) ->
the agent's STT. So the measured error is a property of that whole chain, and the
chain contains a synthetic voice that no real caller has. Concretely:

* If the harness TTS differs from any voice the system will meet in production,
  the absolute WER is not a prediction of production WER, in either direction.
* If the agent's STT engine is the one under test, a *change* in this number
  between two runs of the same fixtures is a real signal about that engine.
* If the harness resolves the transcript with its own STT rather than reading the
  agent's, the number measures the harness and says nothing about the agent.

So: compare runs, do not quote levels. The one thing this module is genuinely
good for is catching the day an STT config change starts mangling party sizes.

WHY BOTH RAW AND NORMALISED, ALWAYS
-----------------------------------
Raw WER counts "twenty six" against "26" as an error, and "Table for two." against
"table for two" as an error. Both meanings survived intact. A harness that reports
only raw WER will spend its owner's attention on formatting; one that reports only
normalised WER will hide a genuine regression inside a normaliser that quietly
patches it up. So both are always reported, side by side, together with how many
raw errors the normaliser absorbed — that difference is itself a diagnostic. A
large gap means the two sides disagree mostly on surface form; a small gap means
the words really were wrong.

A NOTE ON WHAT THE NORMALISER IS FOR
------------------------------------
It is applied to *both* sides, so it does not need to be linguistically correct —
it needs to be *consistent*. "one moment" becoming "1 moment" looks silly and is
harmless, because the hypothesis gets the same treatment. What would be harmful
is a rule that fires on one side only, and that is why every rule here is a pure
function of a single string with no reference to the other.

Known limits, stated rather than discovered later: the number parser handles
English cardinals up to the millions and ordinals up to 31st (the useful range
for booking dates), and refuses to merge word runs that are not valid cardinal
compositions — so "seven thirty" stays two tokens instead of collapsing into 37.
It does not handle digit-by-digit readouts ("oh seven nine" for a phone number),
fractions, or currency.

BACKENDS
--------
`jiwer` is the reference implementation and is used whenever it is importable.
A pure-standard-library Levenshtein alignment is included as a fallback so that
`lab.voice.wer` works on the zero-optional-dependency install, and
`tests/test_voice_wer.py` asserts the two agree on a shared corpus — a fallback
nobody checks against the real thing is just a second bug surface. Every result
records which backend produced it.
"""

from __future__ import annotations

import re
import statistics
from typing import Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.trace.schema import EventKind, Trace

__all__ = [
    "Backend",
    "CorpusWER",
    "UtteranceWER",
    "WERScore",
    "available_backends",
    "normalise",
    "corpus_wer",
    "trace_wer",
    "wer",
]

Backend = Literal["auto", "jiwer", "builtin"]


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

#: Contractions expanded before punctuation is stripped, so that "don't" becomes
#: "do not" rather than "dont". Both sides get the same treatment, so the only
#: requirement is that the mapping is total and deterministic.
CONTRACTIONS: dict[str, str] = {
    "i'm": "i am",
    "i've": "i have",
    "i'll": "i will",
    "i'd": "i would",
    "you're": "you are",
    "you've": "you have",
    "you'll": "you will",
    "you'd": "you would",
    "we're": "we are",
    "we've": "we have",
    "we'll": "we will",
    "we'd": "we would",
    "they're": "they are",
    "they've": "they have",
    "they'll": "they will",
    "it's": "it is",
    "it'll": "it will",
    "that's": "that is",
    "there's": "there is",
    "here's": "here is",
    "what's": "what is",
    "who's": "who is",
    "let's": "let us",
    "can't": "cannot",
    "won't": "will not",
    "don't": "do not",
    "doesn't": "does not",
    "didn't": "did not",
    "isn't": "is not",
    "aren't": "are not",
    "wasn't": "was not",
    "weren't": "were not",
    "haven't": "have not",
    "hasn't": "has not",
    "hadn't": "had not",
    "couldn't": "could not",
    "wouldn't": "would not",
    "shouldn't": "should not",
}

_ONES: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_MULTIPLIERS: dict[str, int] = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
}

_ORDINALS: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
    "thirtieth": 30,
}

_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "‘": "'"})
_NON_WORD = re.compile(r"[^a-z0-9' ]+")
_WHITESPACE = re.compile(r"\s+")


_ORDINAL_SUFFIXES: dict[int, str] = {1: "st", 2: "nd", 3: "rd"}


def _ordinal_suffix(value: int) -> str:
    """`1 -> '1st'`, `2 -> '2nd'`, `13 -> '13th'`, `21 -> '21st'`."""
    if value % 100 in (11, 12, 13):
        return f"{value}th"
    return f"{value}{_ORDINAL_SUFFIXES.get(value % 10, 'th')}"


def _parse_cardinal(tokens: Sequence[str], start: int) -> tuple[int | None, int]:
    """Longest valid cardinal phrase at `tokens[start]`; `(value, tokens_consumed)`.

    The merge rule is the classic decreasing-magnitude one: each new addend must
    be strictly smaller than the previous addend in the same group. That is what
    keeps "twenty six" -> 26 while leaving "seven thirty" as two tokens, because
    30 is not smaller than 7 and so cannot be continuing the same number. Without
    that rule a spoken time would silently normalise into a nonsense integer and
    quietly inflate WER on exactly the utterances this domain cares about.
    """
    total = 0
    current = 0
    last_addend: int | None = None
    index = start
    saw_multiplier = False
    saw_any = False

    while index < len(tokens):
        token = tokens[index]

        if token == "and":
            # "a hundred and six" — only a bridge after a multiplier, and only
            # if a number word actually follows it.
            if not saw_multiplier or index + 1 >= len(tokens):
                break
            following = tokens[index + 1]
            if following not in _ONES and following not in _TENS:
                break
            index += 1
            continue

        if token in _ONES or token in _TENS:
            value = _ONES.get(token, _TENS.get(token, 0))
            if last_addend is not None and value >= last_addend:
                break
            current += value
            last_addend = value
            saw_any = True
            index += 1
            continue

        if token in _MULTIPLIERS:
            if not saw_any:
                break  # a bare "hundred" is a word, not a number
            multiplier = _MULTIPLIERS[token]
            if multiplier == 100:
                current *= 100
                last_addend = 100
            else:
                total += current * multiplier
                current = 0
                last_addend = multiplier
            saw_multiplier = True
            index += 1
            continue

        break

    if not saw_any:
        return None, 0
    return total + current, index - start


def _convert_numbers(tokens: list[str]) -> list[str]:
    """Replace cardinal and ordinal word runs with digit tokens."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]

        # Compound ordinals first: "twenty first" -> "21st".
        if (
            token in _TENS
            and index + 1 < len(tokens)
            and tokens[index + 1] in _ORDINALS
            and _ORDINALS[tokens[index + 1]] < 10
        ):
            out.append(_ordinal_suffix(_TENS[token] + _ORDINALS[tokens[index + 1]]))
            index += 2
            continue

        if token in _ORDINALS:
            out.append(_ordinal_suffix(_ORDINALS[token]))
            index += 1
            continue

        value, consumed = _parse_cardinal(tokens, index)
        if value is not None and consumed > 0:
            out.append(str(value))
            index += consumed
            continue

        out.append(token)
        index += 1
    return out


def normalise(text: str) -> str:
    """Canonical form for WER comparison: casing, punctuation, contractions, numbers.

    Applied identically to reference and hypothesis. The steps, in order — the
    order matters, and getting it wrong is a classic quiet bug:

    1. lowercase, and fold typographic apostrophes to ASCII;
    2. expand contractions **before** punctuation is stripped, so "don't"
       becomes "do not" and not "dont";
    3. drop everything that is not a letter, digit or space;
    4. convert number words to digits ("twenty six" -> "26", "the fourteenth"
       -> "the 14th");
    5. collapse whitespace.

    Reversing steps 2 and 3 would turn every contraction into a made-up word that
    matches nothing, which raises WER while looking like it lowered it.
    """
    lowered = text.translate(_APOSTROPHES).lower()
    for contraction, expansion in CONTRACTIONS.items():
        lowered = re.sub(rf"\b{re.escape(contraction)}\b", expansion, lowered)
    stripped = _NON_WORD.sub(" ", lowered).replace("'", "")
    tokens = [t for t in _WHITESPACE.split(stripped) if t]
    return " ".join(_convert_numbers(tokens))


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


class WERScore(BaseModel):
    """One WER figure with the edit counts it was built from.

    The counts are the point. `wer=0.25` could be one substitution in four words
    or four insertions against one word, and those are different bugs: a
    substitution says the engine misheard, a run of insertions says it
    hallucinated on noise, a run of deletions says it dropped audio.
    """

    model_config = ConfigDict(extra="forbid")

    wer: float = Field(..., ge=0.0)
    hits: int = Field(..., ge=0)
    substitutions: int = Field(..., ge=0)
    deletions: int = Field(..., ge=0)
    insertions: int = Field(..., ge=0)
    reference_words: int = Field(..., ge=0)

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    def describe(self) -> str:
        """Numerator and denominator, never a naked rate."""
        return (
            f"WER {self.wer:.4f} = {self.errors} errors / "
            f"{self.reference_words} reference words "
            f"(S={self.substitutions} D={self.deletions} I={self.insertions})"
        )

    def __repr__(self) -> str:
        return f"WERScore({self.describe()})"


def available_backends() -> tuple[str, ...]:
    """Which WER backends this environment can use, best first."""
    if _import_jiwer() is not None:
        return ("jiwer", "builtin")
    return ("builtin",)


def _import_jiwer():  # type: ignore[no-untyped-def]
    """Import jiwer if installed, else None. Imported lazily and never at module scope.

    `lab` must remain importable on an install with no optional extras — that is
    the repo's cardinal rule — so the dependency is resolved at call time and its
    absence is a degraded mode, not an ImportError.
    """
    try:
        import jiwer  # noqa: PLC0415
    except ImportError:  # pragma: no cover - exercised on the no-extras install
        return None
    return jiwer


def _builtin_counts(
    reference_words: Sequence[str], hypothesis_words: Sequence[str]
) -> tuple[int, int, int, int]:
    """Levenshtein alignment over word lists; `(hits, substitutions, deletions, insertions)`.

    Standard dynamic program with unit costs, plus a backtrace to attribute each
    edit to a class. Unit costs are what makes this comparable with `jiwer`: WER
    is defined on the *minimum* edit distance, and any other cost matrix would
    give a different, incomparable number.

    Ties in the backtrace are broken substitution-first. Different tie-breaking
    can shift an error between the S/D/I columns without changing their total, so
    `wer` is stable across backends even where the columns are not — the test
    suite asserts the total and the rate, and asserts the columns only on
    unambiguous cases.
    """
    n_ref, n_hyp = len(reference_words), len(hypothesis_words)
    # distance[i][j] = edit distance between the first i ref and first j hyp words
    distance = [[0] * (n_hyp + 1) for _ in range(n_ref + 1)]
    for i in range(1, n_ref + 1):
        distance[i][0] = i
    for j in range(1, n_hyp + 1):
        distance[0][j] = j
    for i in range(1, n_ref + 1):
        for j in range(1, n_hyp + 1):
            if reference_words[i - 1] == hypothesis_words[j - 1]:
                distance[i][j] = distance[i - 1][j - 1]
            else:
                distance[i][j] = 1 + min(
                    distance[i - 1][j - 1],  # substitution
                    distance[i - 1][j],  # deletion
                    distance[i][j - 1],  # insertion
                )

    hits = substitutions = deletions = insertions = 0
    i, j = n_ref, n_hyp
    while i > 0 or j > 0:
        if i > 0 and j > 0 and reference_words[i - 1] == hypothesis_words[j - 1]:
            hits += 1
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and distance[i][j] == distance[i - 1][j - 1] + 1:
            substitutions += 1
            i, j = i - 1, j - 1
        elif i > 0 and distance[i][j] == distance[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return hits, substitutions, deletions, insertions


def _score(
    reference: str, hypothesis: str, *, backend: Backend
) -> tuple[WERScore, str]:
    """Score one pair; returns the score and the backend name that produced it."""
    reference_words = reference.split()
    hypothesis_words = hypothesis.split()
    if not reference_words:
        raise ValueError(
            "WER is undefined for an empty reference: the denominator is the "
            "reference word count. Drop the pair or supply a reference."
        )

    jiwer_module = _import_jiwer() if backend in ("auto", "jiwer") else None
    if backend == "jiwer" and jiwer_module is None:
        raise RuntimeError(
            "backend='jiwer' requested but jiwer is not installed; "
            "install the [audio] extra or use backend='builtin'."
        )

    # jiwer raises on an empty hypothesis in some versions; that case is
    # unambiguous (every reference word was deleted) so it is handled directly
    # rather than depending on a library's edge-case behaviour.
    if jiwer_module is not None and hypothesis_words:
        output = jiwer_module.process_words(reference, hypothesis)
        return (
            WERScore(
                wer=float(output.wer),
                hits=int(output.hits),
                substitutions=int(output.substitutions),
                deletions=int(output.deletions),
                insertions=int(output.insertions),
                reference_words=len(reference_words),
            ),
            "jiwer",
        )

    hits, substitutions, deletions, insertions = _builtin_counts(
        reference_words, hypothesis_words
    )
    errors = substitutions + deletions + insertions
    return (
        WERScore(
            wer=errors / len(reference_words),
            hits=hits,
            substitutions=substitutions,
            deletions=deletions,
            insertions=insertions,
            reference_words=len(reference_words),
        ),
        "builtin",
    )


class UtteranceWER(BaseModel):
    """Raw and normalised WER for one reference/hypothesis pair.

    Both texts are kept, in both forms. A WER row you cannot read the words of is
    a number you cannot act on: the whole point of per-utterance scoring is to be
    able to look at the one that went wrong.
    """

    model_config = ConfigDict(extra="forbid")

    reference: str
    hypothesis: str
    reference_normalised: str
    hypothesis_normalised: str
    raw: WERScore
    normalised: WERScore
    backend: str = Field(..., description="Which implementation produced the scores.")

    @property
    def errors_absorbed_by_normalisation(self) -> int:
        """Raw errors that turned out to be surface form only.

        Can be negative in principle — a normaliser that splits a token can
        create errors — and that is worth seeing rather than clamping, because a
        negative value means a normalisation rule is actively harmful.
        """
        return self.raw.errors - self.normalised.errors

    def describe(self) -> str:
        return "\n".join(
            [
                f"  reference : {self.reference}",
                f"  hypothesis: {self.hypothesis}",
                f"  raw        {self.raw.describe()}",
                f"  normalised {self.normalised.describe()}",
                f"  normalisation absorbed {self.errors_absorbed_by_normalisation} "
                f"of {self.raw.errors} raw errors",
            ]
        )

    def __repr__(self) -> str:
        return (
            f"UtteranceWER(raw={self.raw.wer:.4f}, normalised={self.normalised.wer:.4f}, "
            f"backend={self.backend!r})"
        )


def wer(reference: str, hypothesis: str, *, backend: Backend = "auto") -> UtteranceWER:
    """Score one pair, reporting raw and normalised WER together.

    Args:
        reference: What the caller was scripted to say.
        hypothesis: What the agent-side STT transcribed.
        backend: `"auto"` uses jiwer when installed and the built-in alignment
            otherwise; `"jiwer"` insists on jiwer; `"builtin"` forces the
            fallback, which is how the test suite compares the two.

    Raises:
        ValueError: if the reference has no words — WER's denominator would be
            zero, and returning 0.0 or infinity would both be a lie.
    """
    reference_normalised = normalise(reference)
    hypothesis_normalised = normalise(hypothesis)
    raw_score, used = _score(reference, hypothesis, backend=backend)
    normalised_score, _ = _score(
        reference_normalised, hypothesis_normalised, backend=backend
    )
    return UtteranceWER(
        reference=reference,
        hypothesis=hypothesis,
        reference_normalised=reference_normalised,
        hypothesis_normalised=hypothesis_normalised,
        raw=raw_score,
        normalised=normalised_score,
        backend=used,
    )


class CorpusWER(BaseModel):
    """Aggregate WER over many utterances, micro-averaged and macro-averaged.

    **Micro is the figure to quote.** It is total errors over total reference
    words, which is what "the WER of this corpus" means. Macro — the mean of the
    per-utterance rates — weights a three-word utterance the same as a
    thirty-word one, so a single mangled "yes" can dominate it. Both are reported
    because their divergence is informative: macro far above micro means the
    damage is concentrated in short utterances, which is a different engine
    problem from uniform degradation.
    """

    model_config = ConfigDict(extra="forbid")

    utterances: list[UtteranceWER] = Field(default_factory=list)
    backend: str = "builtin"

    # ------------------------------------------------------------------ micro

    def _totals(self, *, normalised: bool) -> tuple[int, int]:
        errors = 0
        words = 0
        for item in self.utterances:
            score = item.normalised if normalised else item.raw
            errors += score.errors
            words += score.reference_words
        return errors, words

    def micro_wer(self, *, normalised: bool) -> float | None:
        """Total errors / total reference words. None for an empty corpus."""
        errors, words = self._totals(normalised=normalised)
        return errors / words if words else None

    def macro_wer(self, *, normalised: bool) -> float | None:
        """Unweighted mean of the per-utterance rates. None for an empty corpus."""
        if not self.utterances:
            return None
        return statistics.fmean(
            (item.normalised if normalised else item.raw).wer
            for item in self.utterances
        )

    @property
    def n(self) -> int:
        return len(self.utterances)

    def worst(self, limit: int = 5, *, normalised: bool = True) -> list[UtteranceWER]:
        """The `limit` worst utterances by WER — the error-analysis entry point."""
        key = (lambda u: u.normalised.wer) if normalised else (lambda u: u.raw.wer)
        return sorted(self.utterances, key=key, reverse=True)[:limit]

    def describe(self) -> str:
        if not self.utterances:
            return "WER: no utterance pairs (0/0) — nothing to report."
        raw_errors, raw_words = self._totals(normalised=False)
        norm_errors, norm_words = self._totals(normalised=True)
        raw_micro = self.micro_wer(normalised=False) or 0.0
        norm_micro = self.micro_wer(normalised=True) or 0.0
        raw_macro = self.macro_wer(normalised=False) or 0.0
        norm_macro = self.macro_wer(normalised=True) or 0.0
        return "\n".join(
            [
                f"WER over {self.n} utterance pair(s), backend={self.backend}",
                f"  raw        micro {raw_micro:.4f} = {raw_errors}/{raw_words} words"
                f"   macro {raw_macro:.4f}",
                f"  normalised micro {norm_micro:.4f} = {norm_errors}/{norm_words} words"
                f"   macro {norm_macro:.4f}",
                f"  normalisation absorbed {raw_errors - norm_errors} of {raw_errors} "
                "raw errors (surface form only)",
                "  HARNESS-RELATIVE: compare between runs; do not read as a "
                "production WER estimate.",
            ]
        )

    def to_markdown(self) -> str:
        """Markdown table of the four aggregate figures."""
        raw_errors, raw_words = self._totals(normalised=False)
        norm_errors, norm_words = self._totals(normalised=True)

        def cell(value: float | None) -> str:
            return f"{value:.4f}" if value is not None else "n/a"

        return "\n".join(
            [
                f"### Word error rate — {self.n} utterance pair(s), backend `{self.backend}`",
                "",
                "| form | micro WER | errors / ref words | macro WER |",
                "|---|---|---|---|",
                f"| raw | {cell(self.micro_wer(normalised=False))} | "
                f"{raw_errors} / {raw_words} | {cell(self.macro_wer(normalised=False))} |",
                f"| normalised | {cell(self.micro_wer(normalised=True))} | "
                f"{norm_errors} / {norm_words} | {cell(self.macro_wer(normalised=True))} |",
                "",
                "Micro is the figure to quote. **Harness-relative**: this measures the "
                "harness TTS plus the agent STT as a chain, so treat it as a "
                "regression signal between runs, not an estimate of production WER.",
            ]
        )

    def __repr__(self) -> str:
        return f"CorpusWER(n={self.n}, backend={self.backend!r})"


def corpus_wer(
    pairs: Iterable[tuple[str, str]], *, backend: Backend = "auto"
) -> CorpusWER:
    """Score many `(reference, hypothesis)` pairs into one `CorpusWER`.

    Pairs whose reference is empty are skipped rather than raising: a suite of a
    thousand utterances should not be lost to one blank line. Skipping is safe
    here because an empty reference contributes nothing to either the numerator
    or the denominator of the micro figure.
    """
    scored: list[UtteranceWER] = []
    used = "builtin"
    for reference, hypothesis in pairs:
        if not reference.split():
            continue
        item = wer(reference, hypothesis, backend=backend)
        used = item.backend
        scored.append(item)
    return CorpusWER(utterances=scored, backend=used)


def trace_wer(trace: Trace, *, backend: Backend = "auto") -> CorpusWER:
    """WER for one trace: `caller_utterance` as reference, `transcript_in` as hypothesis.

    The pairing is `Trace.event_pairs`, the same primitive the latency metrics
    use, so an utterance that produced no transcript at all is *dropped from the
    denominator* rather than counted as 100% error. That is a deliberate choice
    and it needs saying out loud: a dropped turn is a worse failure than a
    misheard one, but it is a different failure, and burying it inside a WER
    average would hide it. Total-loss turns are visible as the gap between
    `len(trace.events_of_kind("caller_utterance"))` and `CorpusWER.n`.
    """
    pairs = [
        (str(caller.get("text", "")), str(transcript.get("text", "")))
        for caller, transcript in trace.event_pairs(
            EventKind.CALLER_UTTERANCE, EventKind.TRANSCRIPT_IN
        )
    ]
    return corpus_wer(pairs, backend=backend)
