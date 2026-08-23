"""Which markets can actually be audio-tested, and which cannot — as a matrix, not a claim.

WHAT THIS DEMONSTRATES
----------------------
An advisory coaching platform selling into 24 markets will be asked, at some
point, "is the voice quality good in Hong Kong?" The honest answer for this
harness is that the question cannot be answered by it at all, and the reason is
not effort or budget: **no text-to-speech model in the stack can synthesise
Cantonese**, so there is no audio to test with.

That is a finding, and findings decay. A vendor adds a language, someone widens a
constant, and the prose in a document goes stale without anything failing. So the
capability boundary lives here as data, the per-market verdict is *computed* from
it, and a test asserts the constants still match the committed vendor snapshot in
`fixtures/audio/cloud/elevenlabs_capabilities.json`. When a vendor ships
Cantonese, that test fails, and the failure is the notification.

THE TWO SETS THAT DECIDE EVERYTHING
-----------------------------------
Synthesis (ElevenLabs, live `GET /v1/models`, 23 Aug 2026): `eleven_flash_v2_5`
offers 32 language ids, `eleven_multilingual_v2` offers 29. Across all nine
models there are **zero** occurrences of `yue`, `zh-HK` or "Cantonese"; the only
Chinese id is `zh`, named "Mandarin Chinese" on `eleven_v3`. The Voice Library's
`yue` filter returns no voices, and the thirteen voices labelled with a
"Cantonese (Hong Kong)" accent are *voice metadata under `language=Chinese`* —
accent labelling, not model support. Selecting one does not make the model speak
Cantonese.

Recognition (Deepgram `nova-3`): the `multi` code-switching model covers exactly
ten languages. Mandarin, **Cantonese (`zh-HK`)**, Korean and Vietnamese are
supported monolingually and are not in that set.

Recognition is therefore *ahead* of synthesis here, which is the counter-intuitive
part and the part that makes the gap structural. Deepgram distinguishes Cantonese
from Mandarin; ElevenLabs does not model it at all.

THREE VERDICTS, AND WHY NOT TWO
-------------------------------
    "code-switched"   both languages synthesisable AND both inside the ten. The
                      full end-to-end test: a caller who switches mid-sentence,
                      and a recogniser expected to follow.
    "monolingual"     synthesisable, but the pair cannot be code-switched, so the
                      row runs as separate single-language turns. A real test of
                      pronunciation, capture and latency; not a test of switching.
    "untestable"      no synthesis exists. There is no audio, so there is no row.

The middle verdict is the one that would be lost by collapsing to a pass/fail.
Singapore (English + Mandarin) and the UAE (Arabic) are perfectly testable as
monolingual rows — both languages synthesise fine — and are *not* testable for
code-switching, because Mandarin and Arabic are outside Deepgram's ten. Calling
those markets "covered" would overclaim; calling them "untestable" would
underclaim and would hide the fact that Hong Kong is a different and worse
problem. A coverage report that cannot tell a partial capability from an absent
one is a coverage report that gets someone a surprise in a customer meeting.

WHAT TO DO ABOUT HONG KONG
--------------------------
Stated here because a gap without a remediation is a complaint. Azure AI Speech
and Google Cloud Text-to-Speech both offer `yue-HK` voices. Adding either behind
the existing `TTSEngine` protocol would move Hong Kong from "untestable" to
"monolingual" — and no further, since `zh-HK` is still outside Deepgram's
code-switching ten, so an English/Cantonese switching row would remain out of
reach on this recogniser. Two vendor changes, two separate gains, and neither is
speculative about what it buys.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lab.voice.engines.deepgram_stt import MULTI_LANGUAGES

__all__ = [
    "CANTONESE",
    "ELEVENLABS_FLASH_V2_5_LANGUAGES",
    "ELEVENLABS_MULTILINGUAL_V2_LANGUAGES",
    "SYNTHESISABLE_LANGUAGES",
    "YUE_REMEDIATION",
    "MARKETS",
    "Market",
    "MarketCoverage",
    "Verdict",
    "coverage_for",
    "coverage_table",
    "untestable_markets",
]

#: The language id Deepgram uses for Cantonese, and that ElevenLabs does not have.
#: A named constant because it is referenced by the remediation note, the market
#: table and a test, and a string literal repeated in three places is a string
#: literal that gets fixed in two.
CANTONESE: str = "zh-HK"

#: `eleven_flash_v2_5`, 32 ids, from the committed capability snapshot.
ELEVENLABS_FLASH_V2_5_LANGUAGES: frozenset[str] = frozenset(
    {
        "ar", "bg", "cs", "da", "de", "el", "en", "es", "fi", "fil", "fr", "hi",
        "hr", "hu", "id", "it", "ja", "ko", "ms", "nl", "no", "pl", "pt", "ro",
        "ru", "sk", "sv", "ta", "tr", "uk", "vi", "zh",
    }
)

#: `eleven_multilingual_v2`, 29 ids. A subset of flash's, less `hu`, `no`, `vi`.
ELEVENLABS_MULTILINGUAL_V2_LANGUAGES: frozenset[str] = frozenset(
    {
        "ar", "bg", "cs", "da", "de", "el", "en", "es", "fi", "fil", "fr", "hi",
        "hr", "id", "it", "ja", "ko", "ms", "nl", "pl", "pt", "ro", "ru", "sk",
        "sv", "ta", "tr", "uk", "zh",
    }
)

#: Anything this stack can turn into audio at all — the union of the two models
#: that also honour text normalisation, which is the constraint that matters
#: because a synthesisable language with no spoken-form reference cannot carry a
#: word error rate. `zh-HK` is absent, and that absence is the finding.
SYNTHESISABLE_LANGUAGES: frozenset[str] = (
    ELEVENLABS_FLASH_V2_5_LANGUAGES | ELEVENLABS_MULTILINGUAL_V2_LANGUAGES
)

#: Vendors with a documented `yue-HK` voice. Named so the gap ships with its fix.
YUE_REMEDIATION: tuple[str, ...] = ("Azure AI Speech (yue-HK)", "Google Cloud TTS (yue-HK)")

Verdict = Literal["code-switched", "monolingual", "untestable"]


class Market(BaseModel):
    """One market the platform sells into, and the languages a call there uses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    hub: str
    regulator: str
    languages: tuple[str, ...] = Field(min_length=1)


class MarketCoverage(BaseModel):
    """What this harness can and cannot prove about one market, and why.

    `reason` is mandatory and is prose. A coverage table whose cells are only
    verdicts invites the reader to guess at the cause, and the three causes here
    — no synthesis, no code-switching, or fully covered — call for three
    completely different responses from a team.
    """

    model_config = ConfigDict(extra="forbid")

    market: str
    hub: str
    regulator: str
    languages: tuple[str, ...]
    verdict: Verdict
    synthesisable: tuple[str, ...]
    not_synthesisable: tuple[str, ...]
    outside_code_switching: tuple[str, ...]
    reason: str
    remediation: tuple[str, ...] = ()

    @property
    def audio_testable(self) -> bool:
        """True when there is *any* audio row to run. False only for `untestable`."""
        return self.verdict != "untestable"


#: The markets named in the product brief, with the language a call in each
#: actually uses. Deliberately not all 24: a matrix padded with markets whose
#: language profile nobody checked would look more thorough and be less true.
#: These are the ones whose language profile is established, and they include
#: every case the boundary runs through.
MARKETS: tuple[Market, ...] = (
    Market(name="United Kingdom", hub="London", regulator="FCA COBS", languages=("en",)),
    Market(name="United States", hub="London", regulator="Reg BI", languages=("en",)),
    Market(name="Singapore", hub="Singapore", regulator="MAS", languages=("en", "zh")),
    Market(name="Hong Kong", hub="Hong Kong", regulator="SFC/IA", languages=("en", CANTONESE)),
    Market(name="Spain", hub="London", regulator="FCA COBS", languages=("en", "es")),
    Market(name="India", hub="Singapore", regulator="MAS", languages=("en", "hi")),
    Market(name="Japan", hub="Hong Kong", regulator="SFC/IA", languages=("en", "ja")),
    Market(name="France", hub="London", regulator="FCA COBS", languages=("en", "fr")),
    Market(name="Germany", hub="London", regulator="FCA COBS", languages=("en", "de")),
    Market(name="United Arab Emirates", hub="London", regulator="FCA COBS", languages=("en", "ar")),
)


def coverage_for(market: Market) -> MarketCoverage:
    """Compute one market's verdict from the two capability sets.

    Computed, never stored. The whole reason this module exists is that a verdict
    written down by hand is a verdict that survives the vendor change which
    invalidated it.
    """
    missing = tuple(
        code for code in market.languages if code not in SYNTHESISABLE_LANGUAGES
    )
    present = tuple(code for code in market.languages if code in SYNTHESISABLE_LANGUAGES)
    outside = tuple(code for code in market.languages if code not in MULTI_LANGUAGES)

    if missing:
        return MarketCoverage(
            market=market.name,
            hub=market.hub,
            regulator=market.regulator,
            languages=market.languages,
            verdict="untestable",
            synthesisable=present,
            not_synthesisable=missing,
            outside_code_switching=outside,
            reason=(
                f"no TTS model in this stack can synthesise {', '.join(missing)}, so there "
                "is no audio to test with. This is not a budget or effort limit — the "
                "capability does not exist at the vendor. Recognition is not the blocker: "
                f"Deepgram nova-3 transcribes {', '.join(missing)} monolingually and "
                "distinguishes it from Mandarin"
            ),
            remediation=YUE_REMEDIATION,
        )
    if outside:
        return MarketCoverage(
            market=market.name,
            hub=market.hub,
            regulator=market.regulator,
            languages=market.languages,
            verdict="monolingual",
            synthesisable=present,
            not_synthesisable=(),
            outside_code_switching=outside,
            reason=(
                f"every language synthesises, so pronunciation, capture and latency are "
                f"fully testable as separate single-language rows. {', '.join(outside)} "
                f"{'is' if len(outside) == 1 else 'are'} outside Deepgram nova-3's "
                f"{len(MULTI_LANGUAGES)}-language code-switching set, so a caller who "
                "switches mid-sentence cannot be tested here. Requesting `multi` anyway "
                "would transcribe monolingually and report a pass it never earned"
            ),
        )
    return MarketCoverage(
        market=market.name,
        hub=market.hub,
        regulator=market.regulator,
        languages=market.languages,
        verdict="code-switched",
        synthesisable=present,
        not_synthesisable=(),
        outside_code_switching=(),
        reason=(
            "every language synthesises and all of them are inside Deepgram nova-3's "
            "code-switching set, so this market is testable end to end including a "
            "caller who switches language mid-sentence"
        ),
    )


def coverage_table(markets: tuple[Market, ...] = MARKETS) -> list[MarketCoverage]:
    """Every market's verdict, worst first, then alphabetically.

    Worst first because the reason anyone opens this table is to find out what is
    *not* covered, and a table that opens on the good news makes them scroll for
    the bad.
    """
    order = {"untestable": 0, "monolingual": 1, "code-switched": 2}
    return sorted(
        (coverage_for(market) for market in markets),
        key=lambda row: (order[row.verdict], row.market),
    )


def untestable_markets(markets: tuple[Market, ...] = MARKETS) -> list[MarketCoverage]:
    """Only the markets with no audio path at all. The headline of the suite."""
    return [row for row in coverage_table(markets) if row.verdict == "untestable"]
