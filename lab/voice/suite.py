"""The audio tier's runner: eighteen declared rows, executed against committed clips.

WHAT THIS DEMONSTRATES
----------------------
That the audio tier is a *corpus with a runner*, not a folder of test functions.
Every row in `scenarios/audio/` declares what it asserts — a captured value, a
timeout verdict, a yield latency, or the fact that it cannot be run at all — and
this module turns that declaration into a result. The assertions live in the
corpus where they can be counted; the execution lives here where it can be
audited; and `tests/test_audio_suite.py` iterates the corpus rather than
restating it, so a nineteenth row needs a YAML file and nothing else.

THE PIPELINE, AND WHY IT IS IN-PROCESS
--------------------------------------
    committed clip  ->  assemble  ->  perturb  ->  recognise  ->  assert
      (digest-keyed     (splice or    (declared     (cassette,      (field,
       cache, free)      pause)        chain)        keyed by        numeric,
                                                     digest)         verdict)

No room, no transport, no network. The harness owns every timestamp, every
sample, and the ground truth — because it synthesised the audio, which is the
one advantage this tier has over production and the reason reference bug 3 is
tractable here at all. A production reconciliation found roughly 17 to 18 silent
speech-recogniser corrections per 100 calls with **68.7% of them
unattributable**, because production never knows what was really said. Here we
do, so attribution is total.

WHY THE CLIP REGISTRY IS DATA AND WHY IT MATTERS SO MUCH
--------------------------------------------------------
`CLIPS` is the binding cost control of this entire tier. The ElevenLabs free
allowance is 10,000 characters that do not renew until the monthly reset, and
9,295 of them remained when this tier was written. A cache hit is *free*, and the
cache key is `sha256(text, voice, model, output_format, normalisation)` — so a
row that reuses an already-recorded line costs nothing at all.

Eleven of the eighteen rows do exactly that. Every silence row, every barge-in
row, every line-quality rung and three of the five capture rows are built from
clips the previous phase already paid for, which is why three silence thresholds,
three channel axes and a barge-in measurement were added for **zero characters**.
The seven new clips exist only where the content genuinely could not be reused: a
planted mispronunciation, a money amount, a Spanish disclosure, a Spanish
sentence carrying an English acronym, a Hinglish magnitude, and an English clause
to splice onto the Mandarin one.

Registry entries therefore carry the *exact* synthesis parameters, not
approximations of them. A single changed character in `text` is a cache miss and a
new charge.

THE LADDERS ARE HERE AND NOT IN THE YAML, DELIBERATELY
------------------------------------------------------
A `VoiceSpec` declares one perturbation chain, which is the right shape for a
condition: it is what was applied, reproducibly, and it is the rung the row's own
verdict is read at. But the useful output of a channel test is not a verdict at
one rung, it is the rung where capture *breaks*. "Fails at 6 dB" is not
actionable; "holds to 10 dB, breaks at 5" is a margin.

So `LADDERS` holds the sweep per axis and `ladder_result` walks it, reporting the
first rung at which the declared value stops being captured. The row stays a
single reproducible condition and the report still gets a breaking point.

WHAT THIS MODULE REFUSES TO DO
------------------------------
No latency figures. `lab/voice/calibration.py` is the gate for those and it is
not consulted here, because nothing in this tier measures a response time: an
in-process run has no delivery leg, and a barge-in *yield* latency is arithmetic
over two clip durations rather than a measured response. The one number that
looks like a latency — `BargeInOutcome.yield_ms` — is exactly that arithmetic,
and it is named for the yield rather than for latency so it cannot be quoted as
one.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from lab.voice.engines.base import DEFAULT_SAMPLE_RATE, Audio, audio_digest
from lab.voice.engines.clipcache import ClipCache
from lab.voice.engines.elevenlabs_tts import (
    DEFAULT_AGENT_VOICE,
    DEFAULT_CALLER_VOICE,
    SPOKEN_FORM_MODELS,
    credits_for,
)
from lab.voice.interaction import attribute_silence, barge_in, pause_for_silence
from lab.voice.perturb import apply_chain

__all__ = [
    "AUDIO_SUITE_CASSETTE",
    "CLIPS",
    "LADDERS",
    "CaptureOutcome",
    "Clip",
    "BargeInOutcome",
    "LadderOutcome",
    "RowResult",
    "SilenceOutcome",
    "assemble_audio",
    "capture_outcome",
    "clip_for",
    "corpus_cost",
    "ladder_result",
    "new_clips",
    "parse_magnitude",
    "run_row",
]

#: The tier's own transcript cassette, kept separate from the previous phase's
#: `deepgram_transcripts.json`. Two files rather than one so that re-recording
#: this tier cannot touch the evidence the earlier findings rest on — a generator
#: that rewrites a file it did not produce is one bad flag away from destroying
#: the measurements somebody else's document cites.
AUDIO_SUITE_CASSETTE: str = "audio_suite_transcripts.json"


class Clip(BaseModel):
    """One synthesised line, and the exact parameters that produced it.

    `reused_from` names the corpus row of the earlier phase that already paid for
    this clip. It is not a comment: `corpus_cost` uses it to report what this
    tier actually spent against what it would have spent had every row been
    written fresh, which is the difference between a cost claim and a cost.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    text: str
    voice: str = DEFAULT_CALLER_VOICE
    model: str = "eleven_flash_v2_5"
    language: str | None = "en"
    purpose: str = Field(min_length=10)
    reused_from: str | None = None

    @property
    def is_new(self) -> bool:
        """True when this tier had to pay for the clip."""
        return self.reused_from is None

    @property
    def credits(self) -> int:
        """What synthesising this line costs, at the model's own multiplier."""
        return credits_for(self.text, self.model)

    @property
    def has_spoken_form(self) -> bool:
        """Whether a word-error-rate reference is available for this clip at all.

        False for `eleven_flash_v2`, the only model that accepts SSML `<phoneme>`
        and therefore the only one that can plant a mispronunciation. The row that
        needs phonetic control is the row that cannot have a WER — which costs
        nothing, because that row asserts a captured field and WER would have been
        the wrong instrument for it regardless.
        """
        return self.model in SPOKEN_FORM_MODELS


#: Every clip the tier reads, keyed by the id a scenario's `audio.clip` names.
#:
#: The `reused_from` column is the cost story. Seven clips are new; the rest were
#: recorded and paid for by the engine phase, and reusing them is free because the
#: cache key is content-addressed.
CLIPS: dict[str, Clip] = {
    # ---- reused: identical text, voice, model and format, so these are cache hits
    "drawdown": Clip(
        id="drawdown",
        text="I'd like to review my drawdown before the tax year ends.",
        purpose="caller speech for the three silence rows; content is irrelevant to a pause",
        reused_from="advisory-drawdown",
    ),
    "suitability": Clip(
        id="suitability",
        text="Is this fund still suitable given my attitude to risk?",
        purpose="the caller's interrupting turn in both barge-in rows",
        reused_from="advisory-suitability",
    ),
    "agent-confirmation": Clip(
        id="agent-confirmation",
        text="Certainly. Your appointment is confirmed for seven thirty.",
        voice=DEFAULT_AGENT_VOICE,
        purpose="the agent speech that gets talked over; a real duration, not a parameter",
        reused_from="agent-confirmation",
    ),
    "postcode": Clip(
        id="postcode",
        text="The postcode is SW1A 1AA.",
        purpose="the capture control and all three line-quality ladder axes",
        reused_from="readback-postcode",
    ),
    "dob": Clip(
        id="dob",
        text="Date of birth: the fourteenth of March, nineteen eighty-two.",
        purpose="a spoken date, which the recogniser's formatter rewrites into US order",
        reused_from="readback-dob",
    ),
    "spelled-surname": Clip(
        id="spelled-surname",
        text="My name is Priya Gupta, that's G-U-P-T-A.",
        purpose="letter-by-letter spelling, the caller's recovery path after a mishearing",
        reused_from="readback-name-spelled",
    ),
    "mandarin-portfolio": Clip(
        id="mandarin-portfolio",
        text="我想检视我的投资组合。",
        language="zh",
        purpose="the Mandarin half of the constructed Singapore utterance",
        reused_from="mono-zh-singapore",
    ),
    # ---- new: content the tier could not get from the existing corpus
    "confusable-plain": Clip(
        id="confusable-plain",
        text="Is that Beattie or Beatty?",
        purpose="the control for the planted mispronunciation: same sentence, no phoneme tag",
    ),
    "confusable-forced": Clip(
        id="confusable-forced",
        text=(
            'Is that <phoneme alphabet="cmu-arpabet" ph="B IY1 T IY0">Beattie</phoneme> '
            "or Beatty?"
        ),
        model="eleven_flash_v2",
        purpose="SSML phoneme forcing, English-only, one tag per word: a known planted slip",
    ),
    "money-amount": Clip(
        id="money-amount",
        text="The premium is four thousand two hundred and fifty pounds.",
        purpose="a spoken magnitude asserted as a number rather than as a string",
    ),
    "es-disclosure": Clip(
        id="es-disclosure",
        text=(
            "This call is recorded. Esta llamada se graba por motivos de cumplimiento."
        ),
        language="es",
        purpose="the control arm: en/es is the only pair both vendors fully support",
    ),
    "es-regulator": Clip(
        id="es-regulator",
        text="FINRA exige que le informemos del coste total antes de continuar.",
        language="es",
        purpose="a regulator's name inside Spanish, which must survive untranslated",
    ),
    "hinglish-lakh": Clip(
        id="hinglish-lakh",
        text="मेरा portfolio अब पंद्रह लाख का है।",
        language="hi",
        purpose="attempted native mixed-script synthesis; lakh scale, asserted numerically",
    ),
    "sg-english-clause": Clip(
        id="sg-english-clause",
        text="I want to check my portfolio,",
        purpose="the English half spliced onto the Mandarin clause; no model speaks both",
    ),
}

#: The sweep per channel axis: the parameter that varies, and the rungs, ordered
#: from kindest to harshest. Read `ladder_result` for what is done with them.
#:
#: The axes are not interchangeable and the ordering reflects that. SNR descends
#: because a lower signal-to-noise ratio is worse; loss rate ascends because more
#: loss is worse. A single "severity" scale over both would be a fiction.
#: **These ranges were extended after the first run, and the reason is the
#: finding.** The first ladder stopped at 0 dB SNR and 30% packet loss, and the
#: postcode was captured at *every* rung of both. A ladder that never breaks
#: reports no breaking point, which is the one output a ladder exists to produce,
#: so the range was the problem rather than the result.
#:
#: The obvious suspicion was checked first, because a perturbation that is not
#: being applied looks exactly like a recogniser that is unbreakable: the noise
#: was measured back out of the assembled clip and came to 20.00, 10.00, 6.00,
#: 3.00 and 0.00 dB against the declared values, with a distinct digest at every
#: rung. The perturbation was real. nova-3 simply holds a spelled postcode at
#: unity signal-to-noise on a short clip.
#:
#: Extending down to -15 dB and up to 70% loss is not adversarial theatre — it is
#: the range that contains the answer. Reporting "held at every rung" from a
#: ladder whose harshest rung the system passes comfortably would have been a
#: coverage claim disguised as a measurement.
LADDERS: dict[str, tuple[str, tuple[float, ...]]] = {
    "add_noise": ("snr_db", (20.0, 15.0, 10.0, 6.0, 3.0, 0.0, -5.0, -10.0, -15.0)),
    "packet_loss": (
        "loss_rate",
        (0.01, 0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 0.90),
    ),
    # Band limiting has one rung: it is the telephone network, not a dial. A
    # ladder over cutoff frequencies would be sweeping a parameter no real
    # channel varies, and reporting a breaking point in it would invite tuning
    # against a condition that does not occur.
    "telephone_band": ("low_hz", (300.0,)),
}


def clip_for(clip_id: str) -> Clip:
    """The registry entry, or an error that names the legal ids."""
    try:
        return CLIPS[clip_id]
    except KeyError:
        raise KeyError(
            f"unknown clip id {clip_id!r}; the audio tier reads only committed clips, "
            f"available: {sorted(CLIPS)}"
        ) from None


def new_clips() -> list[Clip]:
    """Clips this tier had to synthesise, in registry order."""
    return [clip for clip in CLIPS.values() if clip.is_new]


def corpus_cost() -> dict[str, int]:
    """What the tier spent, and what it would have spent with no reuse.

    Returned as data rather than printed so a test can assert the reuse claim
    instead of a reader having to believe it.
    """
    new = new_clips()
    reused = [clip for clip in CLIPS.values() if not clip.is_new]
    return {
        "clips_total": len(CLIPS),
        "clips_new": len(new),
        "clips_reused": len(reused),
        "characters_new": sum(len(clip.text) for clip in new),
        "credits_new": sum(clip.credits for clip in new),
        "credits_avoided_by_reuse": sum(clip.credits for clip in reused),
        "credits_if_nothing_reused": sum(clip.credits for clip in CLIPS.values()),
    }


# --------------------------------------------------------------------------- #
# Assembling a row's audio
# --------------------------------------------------------------------------- #


class AssembledAudio(BaseModel):
    """The caller-side signal a row is actually recognised from, and its provenance.

    `constructed` and `clause_boundaries_s` exist so a spliced utterance can never
    be mistaken for a natural one downstream. The boundary times are carried
    because a recogniser failing *at the join* and one failing across the whole
    second clause are different findings, and without the boundary nobody can tell
    which happened.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    audio: Any
    sample_rate: int = DEFAULT_SAMPLE_RATE
    digest: str
    clip_ids: tuple[str, ...]
    constructed: bool = False
    clause_boundaries_s: tuple[float, ...] = ()
    declared_pause_s: float | None = None
    measured_pause_s: float | None = None
    perturbations: tuple[str, ...] = ()

    @property
    def duration_s(self) -> float:
        return float(np.asarray(self.audio).size) / float(self.sample_rate)


def _read(cache: ClipCache, clip: Clip) -> tuple[Audio, int]:
    """One clip's samples out of the committed cache, or an actionable failure."""
    from lab.voice.engines.clipcache import clip_cache_key  # noqa: PLC0415

    key = clip_cache_key(
        text=clip.text,
        voice=clip.voice,
        model=clip.model,
        output_format="pcm_16000",
    )
    entry = cache.get(key)
    if entry is None:
        raise FileNotFoundError(
            f"clip {clip.id!r} is not in the committed cache (key {key[:12]}...). "
            "The audio tier runs offline from committed fixtures, so a missing clip "
            "is not a cache miss to be filled at test time — it is a fixture that was "
            "never committed. Run `python -m scripts.make_audio_suite_fixtures` with "
            "LAB_LIVE_TTS=1 and LAB_LIVE_STT=1 to record it, and commit the result."
        )
    return entry.audio, entry.sample_rate


def assemble_audio(
    scenario: Any,
    *,
    cache: ClipCache,
    override: Mapping[str, Any] | None = None,
) -> AssembledAudio:
    """Build the caller-side signal this row is recognised from.

    Three things can happen to it, in this order, and the order is not negotiable:
    clauses are concatenated, a silence row's pause is appended, and the declared
    perturbation chain is applied. Perturbing before appending the pause would
    add noise to the silence and the pause would stop being silent; appending the
    pause before splicing would put it in the middle of the utterance.

    `override` replaces the parameters of the single declared perturbation, which
    is how `ladder_result` walks a sweep without editing the corpus.
    """
    spec = scenario.audio
    if spec is None or spec.untestable is not None:
        raise ValueError(
            f"{scenario.id}: an untestable row has no audio to assemble — that is its "
            "entire finding. Check `audio_status()` before calling this."
        )

    clip_ids = list(spec.clauses) if spec.clauses else [spec.clip]
    parts: list[Audio] = []
    boundaries: list[float] = []
    rate = DEFAULT_SAMPLE_RATE
    for clip_id in clip_ids:
        samples, rate = _read(cache, clip_for(clip_id))
        if parts:
            boundaries.append(sum(len(p) for p in parts) / float(rate))
        parts.append(np.asarray(samples, dtype=np.float64))
    audio = np.concatenate(parts) if len(parts) > 1 else parts[0]

    declared: float | None = None
    measured: float | None = None
    if spec.silence is not None:
        declared = spec.silence.target_silence_s
        # `pause_for_silence`, not `insert_pause`: every committed clip carries
        # about 200 ms of its own trailing silence, so appending a declared 5.9 s
        # pause produces a 6.1 s silent run and fires the 6 s timeout. The
        # boundary is the subject of these rows, so the padding is computed
        # against the clip in hand.
        audio, measured = pause_for_silence(
            audio, target_silence_s=declared, sample_rate=rate
        )

    applied: list[str] = []
    voice = getattr(scenario, "voice", None)
    if voice is not None and voice.perturbations:
        steps = [
            (name, dict(override) if override is not None else params)
            for name, params in voice.chain()
        ]
        audio, descriptors = apply_chain(audio, steps, sample_rate=rate)
        applied = [d.name for d in descriptors]

    return AssembledAudio(
        audio=audio,
        sample_rate=rate,
        digest=audio_digest(audio, sample_rate=rate),
        clip_ids=tuple(clip_ids),
        constructed=bool(spec.clauses),
        clause_boundaries_s=tuple(boundaries),
        declared_pause_s=declared,
        measured_pause_s=measured,
        perturbations=tuple(applied),
    )


# --------------------------------------------------------------------------- #
# The four kinds of outcome
# --------------------------------------------------------------------------- #

_DIGIT_WORDS: dict[str, int] = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}

#: Multiplier words, including the two that are not powers of a thousand.
#:
#: `lakh` and `crore` are the reason this parser exists rather than a library
#: call. 1 lakh is 100,000 and 1 crore is 10,000,000, so "fifteen lakh" is
#: 1,500,000 and no grouping of Western thousands produces it. A parser that
#: knows only thousand/million reads the words correctly and returns 15 — a
#: silent five-orders-of-magnitude error in a portfolio value, and one that looks
#: entirely plausible on a screen.
_SCALE_WORDS: dict[str, int] = {
    "hundred": 100,
    "thousand": 1_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    # Devanagari, because that is what the recogniser actually returns.
    #
    # This was not a guess. The Hinglish row transcribed at confidence 0.996 as
    # `मेरा portfolio अब पंद्रह लाख का है` — correct, in Devanagari, with the
    # English noun preserved. An ASCII-only parser reads that as *no number at
    # all*, and the row would have been filed as a capture failure against a
    # near-perfect transcript. The same class of bug as `wer.normalise` reducing
    # Hindi to the empty string: the instrument fails on the script, and the
    # result is read as a failure of the market.
    #
    # `smart_format` does not rescue it either — it returned the same Devanagari
    # with a full stop added — so there was no written-form shortcut available.
    "लाख": 100_000,
    "करोड़": 10_000_000,
    "करोड": 10_000_000,
    "हज़ार": 1_000,
    "हजार": 1_000,
    "सौ": 100,
}

#: Hindi cardinals the corpus's magnitude rows can contain. Deliberately partial
#: and documented as such: a full Hindi number parser is a project, and inventing
#: one here would add a large surface of untested behaviour to support one row.
#: What is here is what the recogniser was measured to return.
_DEVANAGARI_DIGIT_WORDS: dict[str, int] = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6,
    "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12,
    "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15, "सोलह": 16,
    "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "पच्चीस": 25,
    "तीस": 30, "चालीस": 40, "पचास": 50,
}


def parse_magnitude(text: str) -> float | None:
    """The numeric value a transcript states, whether spoken as words or digits.

    Handles the four renderings a recogniser can legitimately produce for the same
    amount — `4250`, `4,250`, `£4250`, "four thousand two hundred and fifty" — and
    the Indian scale words, which is the case this function exists for. Returns
    `None` when the transcript states no number at all, which is a different
    answer from zero and is kept distinct.

    A deliberately small parser rather than a general one. It handles the forms
    this corpus's rows actually contain; the alternative is a dependency whose
    treatment of "lakh" would have to be verified anyway before any figure it
    produced could be trusted.
    """
    # A thousands separator is *inside* a number and a comma between words is
    # not, so they cannot be treated alike. Replacing every comma with a space
    # turned the smart-formatted `£4,250` into "4 250" and the parser returned
    # **4** — a four-thousand-pound premium read as four pounds, from a transcript
    # that was completely correct. Separators go first, then remaining commas
    # become word boundaries.
    lowered = text.lower()
    lowered = re.sub(r"(?<=\d)[, \s](?=\d{3}\b)", "", lowered)
    lowered = lowered.replace(",", " ").replace("-", " ")
    digits = re.findall(r"\d+(?:\.\d+)?", lowered)
    # Latin letters *and* Devanagari, including the combining marks Devanagari
    # writes its vowels with. `\w` does not match combining marks and Python's
    # `re` has no `\p{M}`, so the range is spelled out — the same trap that
    # turned Hindi into disconnected consonants in `wer.normalise`.
    words = re.findall(r"[a-zÀ-ɏ]+|[ऀ-ॿ]+", lowered)
    number_words = {**_DIGIT_WORDS, **_DEVANAGARI_DIGIT_WORDS}

    total = 0.0
    current = 0.0
    seen_word_number = False
    for word in words:
        if word in number_words:
            current += number_words[word]
            seen_word_number = True
        elif word in _SCALE_WORDS:
            scale = _SCALE_WORDS[word]
            if scale == 100:
                current = (current or 1) * scale
            else:
                total += (current or 1) * scale
                current = 0.0
            seen_word_number = True
        elif word == "and":
            continue
    if seen_word_number:
        spoken = total + current
        # A bare scale word attached to digits — "15 lakh", which is what a
        # smart-formatted Hinglish transcript looks like — is not a word number
        # standing alone. The digits carry the mantissa and the word carries the
        # scale, so combine them rather than returning the scale on its own.
        if digits and not any(w in number_words for w in words):
            mantissa = float(digits[0])
            scales = [_SCALE_WORDS[w] for w in words if w in _SCALE_WORDS]
            if scales:
                return mantissa * float(max(scales))
        if spoken:
            return spoken
    if digits:
        return float(digits[0])
    return None


class CaptureOutcome(BaseModel):
    """Which declared values survived the channel, field by field.

    **Never a word error rate**, for the reason `WER_NORMALISATION.md` records at
    length: the same perfect postcode transcript scores 0.000 or 1.400 depending
    only on which reference it is compared against, so a rate would report this
    category as the suite's worst while both vendors worked. `passed` is compared
    against the row's own `expect_capture`, so a row that predicted failure and
    got it is a pass.
    """

    model_config = ConfigDict(extra="forbid")

    transcript: str
    display_text: str | None = None
    confidence: float | None = None
    fields: dict[str, bool] = Field(default_factory=dict)
    numeric: dict[str, bool] = Field(default_factory=dict)
    numeric_seen: dict[str, float | None] = Field(default_factory=dict)
    verbatim: dict[str, bool] = Field(default_factory=dict)
    expected_capture: bool = True

    @property
    def all_captured(self) -> bool:
        checks = list(self.fields.values()) + list(self.numeric.values())
        checks += list(self.verbatim.values())
        return bool(checks) and all(checks)

    @property
    def passed(self) -> bool:
        """True when the row observed what it declared it would observe."""
        return self.all_captured == self.expected_capture

    def describe(self) -> str:
        missed = sorted(
            [name for name, ok in self.fields.items() if not ok]
            + [name for name, ok in self.numeric.items() if not ok]
            + [name for name, ok in self.verbatim.items() if not ok]
        )
        if not missed:
            return "every declared value captured"
        predicted = "as predicted" if not self.expected_capture else "NOT predicted"
        return f"missed {missed} ({predicted})"


def capture_outcome(
    expectation: Any,
    *,
    transcript: str,
    display_text: str | None = None,
    confidence: float | None = None,
) -> CaptureOutcome:
    """Score one transcript against one row's declared values.

    Field matching reuses the suite's shipped matcher rather than a local
    one-liner — see `lab.voice.adapter.readback_report`. That is not tidiness: an
    earlier fixture generator had its own matcher, applied only `normalise`, and
    reported two *perfect* transcripts as capture failures, because `normalise`'s
    cardinal parser corrupts digit-by-digit readouts ("four zero seven one nine
    nine two eight" becomes "4 8 9 11 8"). Two instruments, two answers, and the
    wrong one was being committed as evidence.
    """
    from lab.voice.adapter import _collapse_digit_runs  # noqa: PLC0415
    from lab.voice.wer import normalise  # noqa: PLC0415

    def contains(wanted: str) -> bool:
        plain = normalise(wanted).replace(" ", "")
        digits = _collapse_digit_runs(wanted).replace(" ", "")
        heard_plain = normalise(transcript).replace(" ", "")
        heard_digits = _collapse_digit_runs(transcript).replace(" ", "")
        return bool(
            (plain and plain in heard_plain) or (digits and digits in heard_digits)
        )

    fields = {name: contains(value) for name, value in expectation.fields.items()}
    numeric: dict[str, bool] = {}
    seen: dict[str, float | None] = {}
    for name, value in expectation.numeric.items():
        found = parse_magnitude(transcript)
        seen[name] = found
        numeric[name] = found is not None and abs(found - float(value)) < 0.005
    # Case-folded but otherwise exact: a recogniser writing `finra` for `FINRA` is
    # making a formatting decision, and translating it is the failure being
    # guarded against.
    verbatim = {
        token: token.casefold() in transcript.casefold()
        for token in expectation.verbatim
    }
    return CaptureOutcome(
        transcript=transcript,
        display_text=display_text,
        confidence=confidence,
        fields=fields,
        numeric=numeric,
        numeric_seen=seen,
        verbatim=verbatim,
        expected_capture=expectation.expect_capture,
    )


class SilenceOutcome(BaseModel):
    """A timeout verdict, and — separately — whether its label would be true."""

    model_config = ConfigDict(extra="forbid")

    verdict: str
    expected_verdict: str
    fires: bool
    reason_is_accurate: bool
    expected_reason_accurate: bool
    measured_silence_s: float
    declared_pause_s: float
    threshold_s: float
    declared_matches_measured: bool
    description: str

    @property
    def passed(self) -> bool:
        """Both halves, plus the instrument check.

        `declared_matches_measured` is included because without it the row could
        pass on the wrong pause. If a declared 5.9 s pause measures 4.2 s, the
        verdict "would_not_fire" is correct and means nothing — the assertion has
        become a test of the harness's padding arithmetic.
        """
        return (
            self.verdict == self.expected_verdict
            and self.reason_is_accurate == self.expected_reason_accurate
            and self.declared_matches_measured
        )


class BargeInOutcome(BaseModel):
    """Did the agent stop, and how long did it take.

    `yield_ms` is `None` when the agent never stopped. Not a large number: an
    agent that talks through an interruption has failed rather than lagged, and
    averaging the two together is how a failure disappears into a median.
    """

    model_config = ConfigDict(extra="forbid")

    yielded: bool
    expected_yield: bool
    yield_ms: float | None
    budget_ms: float | None
    overlap_s: float
    agent_duration_s: float
    within_budget: bool | None
    description: str

    @property
    def passed(self) -> bool:
        if self.yielded != self.expected_yield:
            return False
        return self.within_budget is not False


class LadderOutcome(BaseModel):
    """Where on a channel axis capture stops holding.

    `broke_at` is the first rung that lost a declared value, and `None` means the
    content survived every rung — which is a result, not a missing one. The
    `held_to` / `broke_at` pair is the actionable output: a margin.
    """

    model_config = ConfigDict(extra="forbid")

    axis: str
    parameter: str
    rungs: tuple[float, ...]
    captured: tuple[bool, ...]
    held_to: float | None
    broke_at: float | None
    missing_rungs: tuple[float, ...] = ()

    def describe(self) -> str:
        if self.missing_rungs:
            return (
                f"{self.axis}: incomplete — no committed transcript for "
                f"{self.parameter} {list(self.missing_rungs)}"
            )
        if self.broke_at is None:
            return f"{self.axis}: captured at every rung down to {self.rungs[-1]}"
        held = "nothing" if self.held_to is None else f"{self.held_to}"
        return (
            f"{self.axis}: held to {self.parameter} {held}, broke at "
            f"{self.broke_at}"
        )


class RowResult(BaseModel):
    """One audio row's result, with its status kept separate from its verdict.

    `status` is `runnable`, `blocked` or `untestable` and comes from the corpus.
    `passed` is `None` for anything that is not runnable — deliberately, because
    `False` would put a blocked row in the failure column where somebody would
    try to fix it, and `True` would count a row that never ran as coverage.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    category: str
    status: str
    kind: str
    passed: bool | None = None
    digest: str | None = None
    constructed: bool = False
    clip_ids: tuple[str, ...] = ()
    capture: CaptureOutcome | None = None
    control: CaptureOutcome | None = None
    silence: SilenceOutcome | None = None
    barge_in: BargeInOutcome | None = None
    ladder: LadderOutcome | None = None
    untestable_language: str | None = None
    remediation: tuple[str, ...] = ()
    note: str = ""


def _category(scenario: Any) -> str:
    """The row's single category tag, which is what makes the tier countable."""
    from scenarios.loader import AUDIO_TAG_VOCABULARY  # noqa: PLC0415

    categories = {
        "digits-and-names",
        "line-quality",
        "barge-in",
        "silence",
        "multilingual",
        "untestable",
    }
    assert categories <= set(AUDIO_TAG_VOCABULARY), "category tag missing from vocabulary"
    found = sorted(set(scenario.tags) & categories)
    if len(found) != 1:
        raise ValueError(
            f"{scenario.id}: expected exactly one category tag, found {found}. The "
            "category table is only countable if every row carries one."
        )
    return found[0]


def _transcribe(assembled: AssembledAudio, stt: Any) -> Any:
    """Recognise the assembled clip. Offline this is a cassette lookup by digest."""
    return stt.transcribe(assembled.audio, sample_rate=assembled.sample_rate)


def ladder_result(
    scenario: Any,
    *,
    cache: ClipCache,
    stt: Any,
) -> LadderOutcome | None:
    """Walk the declared axis and report where capture breaks.

    Rungs with no committed transcript are reported as `missing_rungs` rather than
    treated as failures. A missing fixture is not a capture failure, and scoring
    it as one would manufacture a breaking point out of an unrecorded rung —
    exactly the sort of number that gets quoted.
    """
    voice = getattr(scenario, "voice", None)
    if voice is None or len(voice.perturbations) != 1:
        return None
    step = voice.perturbations[0]
    if step.name not in LADDERS:
        return None
    parameter, rungs = LADDERS[step.name]

    captured: list[bool] = []
    missing: list[float] = []
    held: float | None = None
    broke: float | None = None
    for rung in rungs:
        params = {**step.params, parameter: rung}
        assembled = assemble_audio(scenario, cache=cache, override=params)
        try:
            transcription = _transcribe(assembled, stt)
        except Exception:  # noqa: BLE001 - a missing rung is data, not an error
            missing.append(rung)
            captured.append(False)
            continue
        outcome = capture_outcome(
            scenario.audio.capture,
            transcript=transcription.text,
            display_text=getattr(transcription, "display_text", None),
            confidence=getattr(transcription, "confidence", None),
        )
        captured.append(outcome.all_captured)
        if outcome.all_captured:
            held = rung
        elif broke is None:
            broke = rung
    return LadderOutcome(
        axis=step.name,
        parameter=parameter,
        rungs=tuple(rungs),
        captured=tuple(captured),
        held_to=held,
        broke_at=broke,
        missing_rungs=tuple(missing),
    )


def run_row(
    scenario: Any,
    *,
    cache: ClipCache,
    stt: Any,
    with_ladder: bool = True,
) -> RowResult:
    """Execute one declared row and return its result.

    The three statuses are decided before anything is read, because two of them
    mean "do not run this and do not score it": a blocked row waits on this
    harness, an untestable row waits on a vendor, and either one scored as a pass
    or a failure would be a lie in a different direction.
    """
    spec = scenario.audio
    if spec is None:
        raise ValueError(f"{scenario.id}: not an audio-tier row")
    status = scenario.audio_status()
    category = _category(scenario)

    if status == "untestable":
        declaration = spec.untestable
        return RowResult(
            scenario_id=scenario.id,
            category=category,
            status=status,
            kind="untestable",
            passed=None,
            untestable_language=declaration.language,
            remediation=tuple(declaration.remediation),
            note=(
                f"no TTS model in this stack synthesises {declaration.language!r}; "
                "counted as untestable, never as a pass or a failure"
            ),
        )

    if status == "blocked":
        return RowResult(
            scenario_id=scenario.id,
            category=category,
            status=status,
            kind=spec.kind(),
            passed=None,
            clip_ids=tuple(spec.clip_ids()),
            note=(
                "blocked on trace event kinds nothing discovers: "
                f"{', '.join(scenario.blocked_on())}. Reported as blocked, never as a pass"
            ),
        )

    assembled = assemble_audio(scenario, cache=cache)
    transcription = _transcribe(assembled, stt)

    if spec.capture is not None:
        outcome = capture_outcome(
            spec.capture,
            transcript=transcription.text,
            display_text=getattr(transcription, "display_text", None),
            confidence=getattr(transcription, "confidence", None),
        )
        control: CaptureOutcome | None = None
        if spec.control_clip:
            control = _control_outcome(scenario, cache=cache, stt=stt)
        ladder = ladder_result(scenario, cache=cache, stt=stt) if with_ladder else None
        return RowResult(
            scenario_id=scenario.id,
            category=category,
            status=status,
            kind="capture",
            passed=outcome.passed,
            digest=assembled.digest,
            constructed=assembled.constructed,
            clip_ids=assembled.clip_ids,
            capture=outcome,
            control=control,
            ladder=ladder,
            note=outcome.describe(),
        )

    if spec.silence is not None:
        expectation = spec.silence
        attribution = attribute_silence(
            assembled.audio,
            threshold_s=expectation.threshold_s,
            declared_pause_s=expectation.target_silence_s,
            sample_rate=assembled.sample_rate,
            speech_during_timeout=expectation.speech_during_timeout,
        )
        outcome = SilenceOutcome(
            verdict=attribution.verdict,
            expected_verdict=expectation.expect_verdict,
            fires=attribution.fires,
            reason_is_accurate=attribution.reason_is_accurate,
            expected_reason_accurate=expectation.expect_reason_accurate,
            measured_silence_s=attribution.measured_silence_s,
            declared_pause_s=expectation.target_silence_s,
            threshold_s=expectation.threshold_s,
            declared_matches_measured=attribution.declared_matches_measured,
            description=attribution.describe(),
        )
        return RowResult(
            scenario_id=scenario.id,
            category=category,
            status=status,
            kind="silence",
            passed=outcome.passed,
            digest=assembled.digest,
            clip_ids=assembled.clip_ids,
            silence=outcome,
            note=outcome.description,
        )

    expectation = spec.barge_in
    agent_samples, agent_rate = _read(cache, clip_for(spec.agent_clip))
    agent_duration = float(np.asarray(agent_samples).size) / float(agent_rate)
    event = barge_in(
        agent_started_s=0.0,
        agent_duration_s=agent_duration,
        caller_started_s=expectation.caller_starts_s,
        agent_stopped_s=expectation.yield_after_s,
    )
    yield_ms = None if event.latency_s is None else event.latency_s * 1000.0
    within = None
    if expectation.max_yield_ms is not None and yield_ms is not None:
        within = yield_ms <= expectation.max_yield_ms
    outcome = BargeInOutcome(
        yielded=event.yielded,
        expected_yield=expectation.expect_yield,
        yield_ms=yield_ms,
        budget_ms=expectation.max_yield_ms,
        overlap_s=event.overlap_s,
        agent_duration_s=agent_duration,
        within_budget=within,
        description=event.describe(),
    )
    return RowResult(
        scenario_id=scenario.id,
        category=category,
        status=status,
        kind="barge_in",
        passed=outcome.passed,
        digest=assembled.digest,
        clip_ids=assembled.clip_ids,
        barge_in=outcome,
        note=outcome.description,
    )


def _control_outcome(scenario: Any, *, cache: ClipCache, stt: Any) -> CaptureOutcome:
    """Score the declared control clip on its own, with no perturbation.

    The control exists to isolate one variable, so it is read from the clip
    directly rather than through `assemble_audio`: applying the row's chain to it
    would reintroduce the very condition it is controlling for.
    """
    spec = scenario.audio
    samples, rate = _read(cache, clip_for(spec.control_clip))
    transcription = stt.transcribe(np.asarray(samples), sample_rate=rate)
    return capture_outcome(
        spec.capture,
        transcript=transcription.text,
        display_text=getattr(transcription, "display_text", None),
        confidence=getattr(transcription, "confidence", None),
    )


def run_tier(
    scenarios: Iterable[Any],
    *,
    cache: ClipCache,
    stt: Any,
    with_ladder: bool = True,
) -> list[RowResult]:
    """Every row, in corpus order."""
    return [
        run_row(scenario, cache=cache, stt=stt, with_ladder=with_ladder)
        for scenario in scenarios
    ]


def tier_summary(results: Sequence[RowResult]) -> dict[str, Any]:
    """Counts with their denominators, and never a naked percentage.

    `lab/report` treats a bare percentage as a defect, and this tier is the place
    that rule earns its keep: 16 runnable rows, 1 blocked and 1 untestable means
    "94% pass" is available and dishonest three different ways. So the summary
    reports the three populations separately and the pass rate carries the
    denominator it was computed over.
    """
    runnable = [r for r in results if r.status == "runnable"]
    passed = [r for r in runnable if r.passed]
    return {
        "rows": len(results),
        "runnable": len(runnable),
        "blocked": [r.scenario_id for r in results if r.status == "blocked"],
        "untestable": [r.scenario_id for r in results if r.status == "untestable"],
        "passed": len(passed),
        "failed": len(runnable) - len(passed),
        "pass_rate": (
            f"{len(passed)}/{len(runnable)} runnable" if runnable else "0/0 runnable"
        ),
        "by_category": {
            category: sum(1 for r in results if r.category == category)
            for category in sorted({r.category for r in results})
        },
        "constructed_rows": [r.scenario_id for r in results if r.constructed],
    }
