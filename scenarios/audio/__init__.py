"""The audio tier: rows whose subject is the audio layer itself.

Loaded explicitly rather than by default. `scenarios/audio/` is deliberately not
in `SUITES`, so `load_corpus()` does not see it, no text baseline moves when a
row is added here, and no audio result is averaged into a text denominator.

    from scenarios.audio import tier
    for scenario in tier():
        ...

THE ADMISSION RULE, WHICH IS THE ONLY REASON THIS TIER IS SMALL
---------------------------------------------------------------
A row belongs here **only if the audio layer is the thing under test.**
Compliance logic, disclosure ordering, objection handling and judge calibration
are all cheaper, faster and more repeatable in text, and putting them behind a
synthesiser buys nothing but cost and variance. Eighteen rows is what that rule
admits: three silence, two barge-in, three line-quality, five English capture,
four multilingual, one recorded refusal.

`scenarios.loader` enforces the rule rather than trusting it — an audio-tier row
must declare an `audio:` block, and it must declare either a channel condition,
the trace events it is blocked on, or a vendor limitation that makes it
untestable.
"""

from __future__ import annotations

from scenarios.loader import AUDIO_TIER, Corpus, Scenario, load_corpus, validate_corpus

__all__ = ["AUDIO_TIER", "tier", "tier_corpus", "validate_tier"]


def tier_corpus() -> Corpus:
    """The tier as a `Corpus`, with personas resolved."""
    return load_corpus(suites=(AUDIO_TIER,))


def tier() -> list[Scenario]:
    """Every audio row, in corpus order."""
    return list(tier_corpus().scenarios)


def validate_tier():  # noqa: ANN201 - mirrors `validate_corpus`'s return type
    """Collect every issue in the tier without raising. See `validate_corpus`."""
    return validate_corpus(suites=(AUDIO_TIER,))
