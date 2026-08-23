"""ElevenLabs synthesis — and the reference string that makes WER mean anything.

WHAT THIS DEMONSTRATES
----------------------
One real vendor behind the same four-method protocol as the local engines, plus
the one thing about that vendor which decides whether every word error rate in
the suite is a measurement or a fabrication.

THE REFERENCE PROBLEM, MEASURED
-------------------------------
ElevenLabs **normalises text before it synthesises it**. Hand it

    "Ring at 7:30 from SW1A 1AA."

and the audio says

    "Ring at seven thirty from S W one A one A A."

Score a recogniser's transcript of that audio against the string you sent and you
are comparing a spoken form against a written one — you are measuring *formatting
policy*, not recognition.

Measured, on the committed round trip in `fixtures/audio/cloud/`. The postcode row
was recognised **perfectly** — every digit and every letter — and scores:

    reference = the spoken form            normalised WER 0.000   (0/10 words)
    reference = the string we sent         normalised WER 1.400   (7/5 words)

Same audio, same transcript, same metric. Only the reference changed. The second
figure exceeds 1.0 because the spoken form has twice the tokens of the written one,
so the errors are mostly insertions — which is exactly how a formatting mismatch
looks when it is mistaken for a recognition failure.

A harness that picks the second reference publishes 140% error on flawless
recognition, and the digits-and-names rows — the ones whose entire purpose is
proving a postcode survives the channel — become the worst category in the suite
while both vendors are doing their job. Somebody then swaps a vendor to fix
nothing. The account-number row scores 0.000 against 1.250 the same way, and the
sort-code row 0.200 against 1.429.

So this engine calls `/with-timestamps` rather than the plain convert endpoint,
and takes the reference from `normalized_alignment.characters` — the characters
ElevenLabs says it spoke. That is what `SynthesisResult.spoken_text` carries and
what `SynthesisResult.reference_source` labels.

THE PART THE DOCUMENTATION DOES NOT TELL YOU
--------------------------------------------
`normalized_alignment` is nullable and undocumented as to which models populate
it. It was probed directly. One 27-character sentence, one premade voice,
`pcm_16000`, 23 Aug 2026:

    model                    normalization  normalized_alignment
    ----------------------------------------------------------------------------
    eleven_flash_v2_5        "on"           " Ring at seven thirty from S W one
                                              A one A A. "          <- SPOKEN FORM
    eleven_flash_v2_5        "auto"         " Ring at 7:30 from SW1A 1AA. "
    eleven_v3                "on"           "Ring at 7:30 from SW1A 1AA."
    eleven_multilingual_v2   "on"           " Ring at seven thirty from S W one
                                              A one A A. "          <- SPOKEN FORM

Three findings, and the second and third are traps:

1.  It is never null on any model that answers. The field's nullability is not
    the risk.

2.  **Under `"auto"` — the API default — the field is the input text with a space
    bolted on each end, while the audio still speaks the numerals out loud.** The
    two clips were 3.808 s and 3.854 s: the model said "seven thirty" either way
    and only *reported* it in one of them. A field named `normalized_alignment`
    that is not a normalised form, cannot be told from one by inspection, and is
    the default. This is the original bug wearing a helpful-looking label.

3.  **On `eleven_v3`, `apply_text_normalization="on"` is silently ignored** —
    `normalized_alignment` came back byte-identical to `alignment`, with none of
    the padding the honouring models add. So the newest, most capable model is
    the one that *cannot* supply a WER reference, and it fails by returning a
    plausible string rather than an error or a null.

Finding 3 is why `SPOKEN_FORM_MODELS` exists and why the "can I trust the
reference?" predicate is **model-aware**. Asking only "did I request
normalisation?" — as the first draft of this module did — returns True for
`eleven_v3` + `"on"`, where the reference is provably not the spoken form. That
predicate would have reintroduced the exact bug this module was written to
prevent, on the model a reader is most likely to reach for.

When the configuration cannot yield a spoken form, this engine **declines to
publish one**: `spoken_text` stays None, `reference_source` reads
`caller-input`, and the fallback is visible in the trace and named in the report.
The fallback is not the bug. An invisible fallback is the bug.

AND THE RULE HAS A SCRIPT BOUNDARY
----------------------------------
"Prefer the spoken form" is right for Latin scripts and **wrong for CJK**, which
was found by running the real markets rather than by reasoning about them. On a
Japanese row the normalised form came back as `"Zi Chan Pei Fen woJian Zhi
shitaidesu."` — Mandarin pinyin, for Japanese input — while the audio was correct
Japanese that Deepgram returned verbatim at confidence 1.0. On a Mandarin row it
came back as `"Wo Xiang Jian Shi Wo De Tou Zi Zu He ."` while the audio
transcribed at 0.992.

So for CJK the vendor transliterates the *written* form instead of reporting the
spoken one, and scoring against it reports 100% error on audio that is very nearly
perfect — the precise mirror image of the Latin-script trap. A harness that
applied the rule unconditionally would have written off Singapore and Japan while
both were working. `_is_romanised` detects it structurally (CJK in, no CJK out)
and declines the reference. Arabic and Devanagari are unaffected: their normalised
forms come back in their own scripts.

THE OTHER PROBED FACT: pcm_16000 ON A FREE KEY
----------------------------------------------
Undocumented for the free tier, so it was measured: `output_format=pcm_16000`
returns real 16 kHz mono PCM on a free key — 121,858 bytes for the sentence
above, peak amplitude 0.68, not silence and not an error. That matters because
16 kHz is exactly this pipeline's rate, so the usual path has no decode step, no
codec dependency and no resample between the synthesiser and a word error rate.

WHICH VOICES, AND WHY THOSE
---------------------------
Free-tier keys cannot use Voice Library voices over the API, so the choice is
among the account's stock voices. Both defaults are `category == "premade"`:

    caller  George (JBFqnCBsd6RMkjVDRZzb) — British, premade. Chosen for the
            widest verified-language set in the premade pool (en, fr, es, hi, ja,
            ar, cs, fil), which is what lets the same caller voice carry the
            English rows *and* the four testable code-switching pairs without
            introducing a voice change as a confound.
    agent   Alice (Xb7hH8MSUJpSbSDYk0k2) — British, premade, female. Chosen to be
            plainly distinguishable from George by ear and by name, because a
            report that cannot tell the two synthesisers apart cannot attribute a
            regression to a side.

THE CREDIT MULTIPLIER, AND WHERE IT ACTUALLY LIVES
--------------------------------------------------
The multiplier is real and it is **on the model, not the voice**. `GET /v1/models`
returns `model_rates.character_cost_multiplier`, measured today as 0.5 for
`eleven_flash_v2_5` and `eleven_v3_conversational`, and 1.0 for `eleven_v3` and
`eleven_multilingual_v2`. So the same sentence costs twice as many credits on v3
as on flash.

`GET /v2/voices` exposes **no per-voice cost field at all** — the full key union
is in `docs/AUDIO_SUITE.md` and contains nothing cost-shaped; `available_for_tiers`
is empty on every voice. So a per-voice multiplier cannot be read from the API,
and the only voice-side signals are `category` (premade / professional / cloned)
and the model's `serves_pro_voices` flag, which is False on every model for a free
key. The enforceable guard is therefore: **refuse any voice not on the measured
premade allowlist**, and charge the ledger in credits using the model multiplier.
`ELEVENLABS_PREMADE_VOICES` is that allowlist.

Budgeting in credits rather than characters is the conservative reading of two
vendor pricing pages that disagree, and it is the one that cannot under-count.

THE COUNTER YOU CANNOT POLL
---------------------------
`GET /v1/user/subscription` reported `character_count` 284 before the probe calls,
284 immediately after them, and 284 on a later re-read — while 108 characters were
demonstrably synthesised. Re-read at the end of the session it stood at 705.

So the counter is real but **lagged by tens of minutes**, and the delta settles a
second question: 705 - 284 = 421, against 763 characters actually sent and 427
credits computed locally at the model multipliers. The vendor's counter is
denominated in **credits, not characters**.

Both facts point the same way. The guard is a local ledger charged in credits: a
cost control that polls a counter which is both lagged and denominated in a
different unit than the caller assumes is a cost control that reports the
overspend afterwards. The local figure tracks the vendor's to within 1.5% and errs
high, because `credits_for` rounds up per line.

WHAT THIS DOES NOT DO
---------------------
No streaming (`stream_with_timestamps` exists and buys nothing for a file-based
post-hoc pipeline — see `lab.voice.adapter` on that trade), no voice cloning, no
SSML, no request stitching via `previous_text`/`next_text`. Character-level
timings are fetched and used for one thing only: the duration cross-check below.
"""

from __future__ import annotations

import base64
import math
import os
import time
from typing import Any

import numpy as np

from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    Audio,
    EngineUnavailable,
    SynthesisResult,
)
from lab.voice.engines.clipcache import ClipCache, clip_cache_key
from lab.voice.engines.tts import LIVE_TTS_ENV_VAR, resample_rate

__all__ = [
    "ELEVENLABS_KEY_ENV_VAR",
    "ELEVENLABS_MODEL_ENV_VAR",
    "ELEVENLABS_VOICE_ENV_VAR",
    "ELEVENLABS_PREMADE_VOICES",
    "CREDIT_BUDGET_ENV_VAR",
    "CHARACTER_COST_MULTIPLIERS",
    "DEFAULT_ELEVENLABS_MODEL",
    "DEFAULT_CALLER_VOICE",
    "DEFAULT_AGENT_VOICE",
    "DEFAULT_CREDIT_BUDGET",
    "NO_LANGUAGE_CODE_MODELS",
    "PCM_RATES",
    "SPOKEN_FORM_MODELS",
    "SPOKEN_FORM_NORMALISATION",
    "_is_romanised",
    "CreditBudgetExceeded",
    "VoiceNotPermitted",
    "ElevenLabsTTS",
    "credits_for",
]

#: The key is read from the environment at call time and never stored on the
#: instance, never logged, and never written into a trace or a fixture. This repo
#: documents variable *names*; values live outside it.
ELEVENLABS_KEY_ENV_VAR: str = "ELEVENLABS_API_KEY"

#: Overrides without touching code, for an A/B between models or voices.
ELEVENLABS_MODEL_ENV_VAR: str = "LAB_ELEVENLABS_MODEL"
ELEVENLABS_VOICE_ENV_VAR: str = "LAB_ELEVENLABS_VOICE"

#: Credits this process may spend before the engine refuses.
CREDIT_BUDGET_ENV_VAR: str = "LAB_ELEVENLABS_CREDIT_BUDGET"

#: Fast, cheap, 16 kHz-capable, and one of the two models measured to honour
#: `apply_text_normalization`. See the module docstring.
DEFAULT_ELEVENLABS_MODEL: str = "eleven_flash_v2_5"

#: George — British, premade, widest verified-language set in the premade pool.
DEFAULT_CALLER_VOICE: str = "JBFqnCBsd6RMkjVDRZzb"

#: Alice — British, premade, plainly distinct from the caller voice.
DEFAULT_AGENT_VOICE: str = "Xb7hH8MSUJpSbSDYk0k2"

#: The `apply_text_normalization` value under which a spoken form is published.
#: `"auto"` — the API default — is measured *not* to publish one while still
#: speaking the normalised words, which is the trap in the module docstring.
SPOKEN_FORM_NORMALISATION: str = "on"

#: Models measured to honour `apply_text_normalization="on"` and return a real
#: spoken form in `normalized_alignment`.
#:
#: `eleven_v3` is deliberately absent. It accepts the parameter, returns HTTP 200,
#: and echoes the input back in `normalized_alignment` — a wrong answer that looks
#: exactly like a right one. Adding it here on the strength of the documentation
#: rather than a measurement would silently restore the bug this module prevents.
#: `eleven_v3_conversational` is absent for the same reason: unmeasured is not
#: assumed-working, and the cost of assuming wrong is a fabricated WER.
SPOKEN_FORM_MODELS: frozenset[str] = frozenset(
    {"eleven_flash_v2_5", "eleven_multilingual_v2"}
)

#: Models that reject or ignore `language_code`. Sending it to
#: `eleven_multilingual_v2` is documented as unsupported, so the parameter is
#: dropped for it rather than sent and hoped about — a pinned language that the
#: model never received is worse than no pin, because the run looks pinned.
NO_LANGUAGE_CODE_MODELS: frozenset[str] = frozenset({"eleven_multilingual_v2"})

#: `model_rates.character_cost_multiplier`, read from `GET /v1/models` on
#: 23 Aug 2026. Static because a cost guard must work before the first request:
#: fetching the rate card requires a network call, and an engine that has to go
#: online to find out whether it may go online is not a guard. `refresh_rates()`
#: re-reads the live values for anyone who wants to check this table has not
#: drifted, and `describe()` says which source was used.
CHARACTER_COST_MULTIPLIERS: dict[str, float] = {
    "eleven_v3": 1.0,
    "eleven_v3_conversational": 0.5,
    "eleven_multilingual_v2": 1.0,
    "eleven_flash_v2_5": 0.5,
    "eleven_flash_v2": 0.5,
}

#: Unknown models are charged at the highest multiplier seen, not the lowest.
#: A cost guard that guesses cheap is a cost guard that fails open.
FALLBACK_COST_MULTIPLIER: float = 1.0

#: Stock premade voice ids on the probed account, `category == "premade"` from
#: `GET /v2/voices`. The allowlist is the enforceable half of the credit-multiplier
#: guard: since no per-voice cost field is exposed, "is this a stock voice?" is the
#: only question the API will answer, and a professional or cloned voice is the
#: only kind that could plausibly carry a surcharge.
ELEVENLABS_PREMADE_VOICES: frozenset[str] = frozenset(
    {
        "hpp4J3VqNfWAUOO0d1Us",  # Bella   — american female
        "CwhRBWXzGAHq8TQ4Fs17",  # Roger   — american male
        "EXAVITQu4vr4xnSDxMaL",  # Sarah   — american female
        "FGY2WhTYpPnrIDTdsKH5",  # Laura   — american female
        "IKne3meq5aSn9XLyUdCD",  # Charlie — australian male
        "JBFqnCBsd6RMkjVDRZzb",  # George  — british male   (caller default)
        "N2lVS1w4EtoT3dr4eOWO",  # Callum  — american male
        "SAz9YHcvj6GT2YYXdXww",  # River   — american neutral
        "SOYHLrjzK2X1ezoPC6cr",  # Harry   — american male
        "TX3LPaxmHKxFdv7VOQHJ",  # Liam    — american male
        "Xb7hH8MSUJpSbSDYk0k2",  # Alice   — british female (agent default)
        "XrExE9yKIg1WjnnlVkGX",  # Matilda — american female
        "bIHbv24MWmeRgasZH58o",  # Will    — american male
        "cgSgspJ2msm6clMCkdW9",  # Jessica — american female
        "cjVigY5qzO86Huf0OWal",  # Eric    — american male
        "iP95p4xoKVk53GoZ742B",  # Chris   — american male
        "nPczCjzI2devNBz1zQrb",  # Brian   — american male
        "onwK4e9ZLuTAKqWW03F9",  # Daniel  — british male
        "pFZP5JQG7iQjIQuC4Bku",  # Lily    — british female
        "pNInz6obpgDQGcFmaJgB",  # Adam    — american male
        "pqHfZKP75CvOlQylNhV4",  # Bill    — american male
    }
)

#: Free-tier allowance is 10,000 characters. The default budget is a small
#: fraction of it, on the principle that the guard should stop a runaway loop in
#: its first seconds and a deliberate corpus run should have to say so out loud.
DEFAULT_CREDIT_BUDGET: int = 2_000

#: Raw-PCM output formats the API offers. Anything else gets the nearest of these
#: plus a resample, which is stated in `describe()` rather than done quietly.
PCM_RATES: tuple[int, ...] = (8_000, 16_000, 22_050, 24_000, 32_000, 44_100, 48_000)


class CreditBudgetExceeded(EngineUnavailable):
    """This line would take the process past its credit budget.

    A subclass of `EngineUnavailable` so a caller that already handles "no engine
    here" handles "no budget left" too, and distinct so a test can assert on it.
    Raised *before* the request: a budget is a ceiling, not a receipt.
    """


class VoiceNotPermitted(EngineUnavailable):
    """The requested voice is not on the premade allowlist.

    Its own class because the remedy is completely different from a missing key —
    nothing needs installing, a voice id needs changing — and because a test
    proving the guard bites should not have to match on prose.
    """


def credits_for(text: str, model_id: str) -> int:
    """Credits `text` costs on `model_id`, rounded up.

    Rounded up rather than to nearest, and unknown models charged at the dearest
    known rate, because the only acceptable direction for a cost estimate to be
    wrong in is the one that stops you early.
    """
    multiplier = CHARACTER_COST_MULTIPLIERS.get(model_id, FALLBACK_COST_MULTIPLIER)
    return int(math.ceil(len(text) * multiplier))


def _is_cjk(character: str) -> bool:
    """True for a Han ideograph, kana or Hangul syllable — the scripts that get romanised."""
    code = ord(character)
    return any(
        low <= code <= high
        for low, high in (
            (0x3040, 0x30FF),  # hiragana + katakana
            (0x3400, 0x4DBF),  # CJK extension A
            (0x4E00, 0x9FFF),  # CJK unified ideographs
            (0xAC00, 0xD7AF),  # hangul syllables
            (0xF900, 0xFAFF),  # CJK compatibility ideographs
        )
    )


def _is_romanised(sent: str, spoken: str) -> bool:
    """True when a CJK input came back as a Latin "spoken form". The CJK inversion.

    **This inverts the module's central rule for one family of scripts, and the
    inversion was measured, not predicted.** Two live rows, `eleven_flash_v2_5`
    with `apply_text_normalization="on"` and `language_code` pinned:

        sent    "資産配分を見直したいです。"        (Japanese)
        spoken  "Zi Chan Pei Fen woJian Zhi shitaidesu."
        audio   correct Japanese — Deepgram nova-3 returned the input back,
                confidence 1.0

        sent    "我想检视我的投资组合。"            (Mandarin)
        spoken  "Wo Xiang Jian Shi Wo De Tou Zi Zu He ."
        audio   correct Mandarin — transcribed at confidence 0.992, two
                characters out (检视 heard as 见识, a genuine homophone)

    The audio is right. The *reference* is romanised — into Mandarin pinyin in
    both cases, including for the Japanese row, where the correct reading would
    have been "shisan haibun". So for CJK, `normalized_alignment` is not the
    spoken form: it is a transliteration of the written form, and scoring against
    it reports **100% error on audio that is very nearly perfect.**

    Which is the exact opposite of the Latin-script case that motivates this whole
    module, where the spoken form is right and the input is wrong. "Always prefer
    the spoken form" is therefore a rule with a script boundary, and a harness
    that applied it unconditionally would report total failure for Singapore and
    Japan while both were working. Arabic is unaffected — its normalised form came
    back in Arabic script — so the guard is about romanisation, not about
    non-Latin text in general.

    Detected structurally rather than by a language list: if the input contains
    CJK characters and the returned form contains none, the vendor transliterated
    and the field is not usable. That test needs no language pin, so it also
    catches the case where the caller forgot to set one.
    """
    if not any(_is_cjk(character) for character in sent):
        return False
    return not any(_is_cjk(character) for character in spoken)


def _pick(payload: Any, *names: str) -> Any:
    """First present attribute or key among `names`. `None` if none of them are.

    Exists because the SDK's Python attribute and its wire alias disagree, and
    both spellings are load-bearing depending on how the object reached us. The
    timestamped response exposes `audio_base_64` as an attribute and serialises it
    as `audio_base64` — the wire spelling confirmed against the raw HTTP response
    during the probe. A fixture recorded with `model_dump()` therefore has one
    spelling and a live SDK call the other. Reading only one of them works
    perfectly right up until the fixture is replayed.
    """
    for name in names:
        if isinstance(payload, dict):
            if name in payload:
                return payload[name]
            continue
        value = getattr(payload, name, None)
        if value is not None:
            return value
    return None


def _characters_of(alignment: Any) -> str:
    """Join a character-alignment's `characters` list into the string it spells."""
    if alignment is None:
        # An absent alignment is not an error here; the caller decides what a
        # missing spoken form means, because the answer differs by mode.
        return ""
    characters = _pick(alignment, "characters") or []
    return "".join(str(character) for character in characters)


def _last_end_time(alignment: Any) -> float | None:
    """The alignment's final character end time, in seconds, or None."""
    if alignment is None:
        return None
    times = _pick(alignment, "character_end_times_seconds") or []
    return float(times[-1]) if times else None


def _int_from_env(name: str, default: int) -> int:
    """Read an int from the environment, restoring the default on anything unparseable.

    A malformed budget is not a reason to fail a run, and is very much not a
    reason to silently mean "unlimited".
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


class ElevenLabsTTS:
    """ElevenLabs text-to-speech via the current SDK. Opt-in, cached, budgeted.

    Gated on `$LAB_LIVE_TTS` *and* a key, in that order, and the refusal names
    whichever is missing. The flag rather than the key alone is the rule the rest
    of the repo follows: a developer with an ElevenLabs key exported for other
    work has not consented to this test suite spending their allowance.

    The identity string names the model and the voice —
    `tts:elevenlabs/eleven_flash_v2_5/JBFqnCBsd6RMkjVDRZzb` — because "ElevenLabs
    got worse" is not a finding and "this voice on this model got worse" is. It
    lands in the `engine` field of every trace event the engine produced, which is
    what lets `lab.voice.metrics.latencies_by_engine` slice a distribution by it.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        voice_id: str | None = None,
        language_code: str | None = None,
        apply_text_normalization: str = SPOKEN_FORM_NORMALISATION,
        env_var: str = LIVE_TTS_ENV_VAR,
        key_env_var: str = ELEVENLABS_KEY_ENV_VAR,
        cache: ClipCache | None = None,
        credit_budget: int | None = None,
        allow_non_premade: bool = False,
        timeout_s: float = 120.0,
        client: Any = None,
    ) -> None:
        """
        Args:
            model_id: Defaults to `$LAB_ELEVENLABS_MODEL` then `eleven_flash_v2_5`.
            voice_id: Defaults to `$LAB_ELEVENLABS_VOICE` then George, a premade
                voice. Must be on `ELEVENLABS_PREMADE_VOICES` unless
                `allow_non_premade`.
            language_code: Pins one language on models that accept it. Dropped for
                the models in `NO_LANGUAGE_CODE_MODELS`, which ignore it. There is
                no code-switching setting: the vendor supports none, so a
                mixed-language line is a single-language request and the suite
                says so rather than pretending.
            apply_text_normalization: `"on"`, `"auto"` or `"off"`. Defaults to
                `"on"`. A spoken form is published only when this is `"on"` *and*
                the model is in `SPOKEN_FORM_MODELS` — both, because `eleven_v3`
                satisfies the first and fails the second. Any other configuration
                is honoured and costs the spoken-form reference.
            env_var: Live opt-in flag.
            key_env_var: Name of the variable holding the key. The name is
                configuration; the value is never held on this object.
            cache: Clip cache. Defaults to the layered committed+scratch cache.
            credit_budget: Ceiling on credits this instance may spend. Defaults to
                `$LAB_ELEVENLABS_CREDIT_BUDGET` then 2,000. Zero or negative
                disables the guard, which is a thing you should have to type.
            allow_non_premade: Permit a voice off the allowlist. Off by default;
                see the module docstring on why the allowlist is the only
                enforceable half of the cost guard.
            timeout_s: Per-request timeout.
            client: An already-built SDK client, or any object exposing
                `text_to_speech.convert_with_timestamps`. Injected by the tests so
                extraction, decoding, caching, the ledger, the voice guard and the
                duration cross-check are all exercised offline with no SDK, no key
                and no spend.
        """
        self.model_id = (
            model_id or os.environ.get(ELEVENLABS_MODEL_ENV_VAR) or DEFAULT_ELEVENLABS_MODEL
        )
        self.voice_id = (
            voice_id or os.environ.get(ELEVENLABS_VOICE_ENV_VAR) or DEFAULT_CALLER_VOICE
        )
        self.language_code = language_code
        self.apply_text_normalization = apply_text_normalization
        self.env_var = env_var
        self.key_env_var = key_env_var
        self.cache = cache if cache is not None else ClipCache()
        self.allow_non_premade = allow_non_premade
        self.timeout_s = timeout_s
        self.name = f"tts:elevenlabs/{self.model_id}/{self.voice_id}"
        self.is_replay = False
        self.voice = self.voice_id
        self.characters_spent = 0
        self.credits_spent = 0
        self.requests = 0
        self.cached_lines = 0
        self.rates_source = "measured 23 Aug 2026"
        self._client = client
        self.credit_budget = (
            credit_budget
            if credit_budget is not None
            else _int_from_env(CREDIT_BUDGET_ENV_VAR, DEFAULT_CREDIT_BUDGET)
        )
        self._require_permitted_voice(self.voice_id)

    # ----------------------------------------------------------- availability

    @property
    def live_enabled(self) -> bool:
        return bool(os.environ.get(self.env_var))

    def key_present(self) -> bool:
        """True when the key variable is set. Reads presence; never the value."""
        return bool(os.environ.get(self.key_env_var))

    def sdk_available(self) -> bool:
        """True when the `elevenlabs` package can be imported."""
        from importlib.util import find_spec

        try:
            return find_spec("elevenlabs") is not None
        except (ImportError, ValueError):  # pragma: no cover - broken install
            return False

    def available(self) -> bool:
        if self._client is not None:
            return True
        return self.live_enabled and self.key_present() and self.sdk_available()

    def missing_requirements(self) -> list[str]:
        """Names of everything standing between this engine and a live call.

        Returned as a list of *names* so a refusal can say "LAB_LIVE_TTS and the
        elevenlabs package" rather than stopping at the first thing it noticed.
        Being told one blocker at a time turns setup into three failed runs.
        """
        if self._client is not None:
            return []
        missing = []
        if not self.live_enabled:
            missing.append(self.env_var)
        if not self.key_present():
            missing.append(self.key_env_var)
        if not self.sdk_available():
            missing.append("the 'elevenlabs' package")
        return missing

    @property
    def publishes_spoken_form(self) -> bool:
        """True when this configuration yields a trustworthy spoken-form reference.

        **Both** conditions, and the conjunction is the whole point. Normalisation
        must have been requested, *and* the model must be one measured to honour
        it. `eleven_v3` passes the first and fails the second: it returns a
        populated `normalized_alignment` that is just the input echoed back. A
        predicate that checked only the request — as the first draft of this
        module did — would call that a spoken form and hand a written-form
        reference to the WER, which is the original bug with extra steps.
        """
        return (
            self.apply_text_normalization == SPOKEN_FORM_NORMALISATION
            and self.model_id in SPOKEN_FORM_MODELS
        )

    def _require_permitted_voice(self, voice_id: str) -> None:
        if self.allow_non_premade or voice_id in ELEVENLABS_PREMADE_VOICES:
            return
        raise VoiceNotPermitted(
            f"tts:elevenlabs/{self.model_id}/{voice_id}",
            f"voice {voice_id!r} is not on the measured premade allowlist. The API "
            "exposes no per-voice cost field, so a voice off the stock list may carry "
            "a credit surcharge that would not show up until the allowance ran out; "
            "Voice Library voices are also unavailable over the API on a free key",
            "pick an id from ELEVENLABS_PREMADE_VOICES, or pass "
            "allow_non_premade=True if you have checked this voice's rate yourself",
        )

    def refresh_rates(self) -> dict[str, float]:
        """Re-read `model_rates.character_cost_multiplier` from the live API.

        Not called on the synthesis path — see `CHARACTER_COST_MULTIPLIERS` on why
        the guard must not need the network. This exists so the static table can be
        *checked* rather than trusted indefinitely, and it updates
        `rates_source` so `describe()` stops claiming a date it no longer means.
        """
        client = self._ensure_client()
        models = client.models.list()
        found: dict[str, float] = {}
        for model in models:
            model_id = _pick(model, "model_id")
            rates = _pick(model, "model_rates")
            multiplier = _pick(rates, "character_cost_multiplier") if rates else None
            if model_id and multiplier is not None:
                found[str(model_id)] = float(multiplier)
        CHARACTER_COST_MULTIPLIERS.update(found)
        self.rates_source = "refreshed from GET /v1/models"
        return found

    def describe(self) -> str:
        state = "enabled" if self.live_enabled else f"disabled (set {self.env_var}=1)"
        if self.publishes_spoken_form:
            reference = "reference=normalized_alignment (spoken form)"
        elif self.apply_text_normalization != SPOKEN_FORM_NORMALISATION:
            reference = (
                f"reference=caller input — normalisation is "
                f"{self.apply_text_normalization!r}, so the spoken form is unknown"
            )
        else:
            reference = (
                f"reference=caller input — {self.model_id} does not honour "
                "apply_text_normalization (measured: it echoes the input back), so its "
                "normalized_alignment is not a spoken form"
            )
        multiplier = CHARACTER_COST_MULTIPLIERS.get(self.model_id, FALLBACK_COST_MULTIPLIER)
        return (
            f"{self.name} (network, {state}; key {self.key_env_var} "
            f"{'set' if self.key_present() else 'unset'}; {reference}; "
            f"{multiplier}x credits/char, {self.rates_source}; "
            f"{self.cache.describe()}; spent {self.credits_spent}/{self.credit_budget} "
            f"credits over {self.requests} request(s), {self.cached_lines} line(s) free "
            f"from cache)"
        )

    # -------------------------------------------------------------- synthesis

    def output_format_for(self, sample_rate: int) -> tuple[str, int]:
        """`(output_format, native_rate)` for a requested pipeline rate.

        Asks for raw PCM at the pipeline rate when the API offers it — which it
        does at 16 kHz, the rate this repo works at and the one the probe
        confirmed works on a free key — so the usual path involves no decode and
        no resample. Otherwise it picks the nearest offered rate and the caller
        resamples through `lab.voice.engines.tts.resample_rate`, which is
        frequency-domain and therefore anti-aliased on the way down.
        """
        if sample_rate in PCM_RATES:
            return f"pcm_{sample_rate}", sample_rate
        native = min(PCM_RATES, key=lambda rate: (abs(rate - sample_rate), -rate))
        return f"pcm_{native}", native

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        missing = self.missing_requirements()
        if missing:
            listing = " and ".join(
                [", ".join(missing[:-1]), missing[-1]] if len(missing) > 1 else missing
            )
            raise EngineUnavailable(
                self.name,
                f"live synthesis needs {listing}, which {'is' if len(missing) == 1 else 'are'} "
                "not set up",
                f"export {self.env_var}=1 to permit billed API calls and export "
                f"{self.key_env_var} (this repo stores variable names, never values); "
                "run scripts/setup_audio.sh --only cloud to install the vendor SDKs, "
                "which are not dependencies of this package. Or stay offline: "
                "KokoroTTS is local and FixtureTTS replays the committed clips",
            )
        from elevenlabs.client import ElevenLabs

        self._client = ElevenLabs(api_key=os.environ[self.key_env_var], timeout=self.timeout_s)
        return self._client

    def _check_budget(self, text: str) -> None:
        if self.credit_budget <= 0:
            return
        cost = credits_for(text, self.model_id)
        if self.credits_spent + cost > self.credit_budget:
            raise CreditBudgetExceeded(
                self.name,
                f"this line costs {cost} credit(s) ({len(text)} characters at "
                f"{CHARACTER_COST_MULTIPLIERS.get(self.model_id, FALLBACK_COST_MULTIPLIER)}x) "
                f"and only {self.credit_budget - self.credits_spent} of "
                f"{self.credit_budget} remain in this process. Cached lines are free and "
                "are not charged here",
                f"raise {CREDIT_BUDGET_ENV_VAR}, or pass credit_budget=, if the spend is "
                "intended. The vendor's own character_count was measured not to update "
                "in time, so this ledger is the only live view of the spend",
            )

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        """Synthesise `text`, from cache when possible, and report what was spoken.

        A cache hit costs **zero** credits and makes no request: the budget check
        and the ledger are both downstream of the lookup, which is the property
        that makes a re-run of the suite free rather than merely cheaper.

        Raises:
            VoiceNotPermitted: if `voice` is off the premade allowlist.
            CreditBudgetExceeded: before the request, if it would pass the budget.
            EngineUnavailable: if the engine is not enabled, keyed and installed;
                if the response carries no audio; or if the decoded duration
                disagrees with the character alignment (see below).
        """
        voice_id = voice or self.voice_id
        self._require_permitted_voice(voice_id)
        output_format, native_rate = self.output_format_for(sample_rate)
        key = clip_cache_key(
            text=text,
            voice=voice_id,
            model=self.model_id,
            output_format=output_format,
            normalisation=self.apply_text_normalization,
        )
        entry = self.cache.get(key)
        if entry is not None:
            self.cached_lines += 1
            return SynthesisResult(
                audio=(
                    entry.audio
                    if entry.sample_rate == sample_rate
                    else resample_rate(entry.audio, entry.sample_rate, sample_rate)
                ),
                sample_rate=sample_rate,
                engine=self.name,
                voice=voice_id,
                # A file read is not a synthesis and must not be timed as one —
                # the same rule FixtureTTS follows.
                synthesis_s=None,
                replayed=True,
                text=text,
                # The romanisation guard is re-applied on the cache path, not only
                # on the live one. It is a property of the *data*, so a sidecar
                # written before the guard existed — or by an older checkout —
                # must not be able to serve a reference the live path would have
                # refused. A guard that only runs on a cache miss is a guard that
                # stops working as soon as the cache warms up.
                spoken_text=(
                    None
                    if entry.spoken_text and _is_romanised(text, entry.spoken_text)
                    else entry.spoken_text
                ),
            )

        self._check_budget(text)
        client = self._ensure_client()
        request: dict[str, Any] = {
            "text": text,
            "model_id": self.model_id,
            "output_format": output_format,
            "apply_text_normalization": self.apply_text_normalization,
        }
        if self.language_code and self.model_id not in NO_LANGUAGE_CODE_MODELS:
            request["language_code"] = self.language_code
        started = time.perf_counter()
        response = client.text_to_speech.convert_with_timestamps(voice_id, **request)
        elapsed = time.perf_counter() - started
        self.characters_spent += len(text)
        self.credits_spent += credits_for(text, self.model_id)
        self.requests += 1

        audio = self._decode(response)
        spoken = self._spoken_text(response, text)
        self._cross_check_duration(response, audio, native_rate, text)

        self.cache.put(
            key,
            audio,
            native_rate,
            {
                "engine": self.name,
                "model_id": self.model_id,
                "voice_id": voice_id,
                "output_format": output_format,
                "apply_text_normalization": self.apply_text_normalization,
                "language_code": request.get("language_code"),
                "text": text,
                "spoken_text": spoken,
                "sample_rate": native_rate,
                "credits": credits_for(text, self.model_id),
            },
        )
        return SynthesisResult(
            audio=(
                audio
                if native_rate == sample_rate
                else resample_rate(audio, native_rate, sample_rate)
            ),
            sample_rate=sample_rate,
            engine=self.name,
            voice=voice_id,
            synthesis_s=elapsed,
            text=text,
            spoken_text=spoken,
        )

    # ------------------------------------------------------------ extraction

    def _decode(self, response: Any) -> Audio:
        """Decode the base64 raw PCM16 payload into mono float samples."""
        payload = _pick(response, "audio_base_64", "audio_base64")
        if not payload:
            raise EngineUnavailable(
                self.name,
                "the response carried no audio (no audio_base_64 / audio_base64 field)",
                "check the model id and voice id are both valid for this account",
            )
        raw = base64.b64decode(payload)
        if len(raw) % 2:
            # An odd byte count cannot be 16-bit PCM. Truncating it silently would
            # shift every sample after the hole by half a frame and turn the clip
            # into noise that looks like a bad voice.
            raise EngineUnavailable(
                self.name,
                f"decoded {len(raw)} bytes, which is not a whole number of 16-bit "
                "samples; the response is not the raw PCM this engine requested",
                "check output_format is a pcm_* value and not a container format",
            )
        return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0

    def _spoken_text(self, response: Any, text: str) -> str | None:
        """The words the engine says it spoke, or None when it cannot be trusted to know.

        None in four distinct cases, all of which mean "do not publish a spoken
        form": normalisation was not requested; the model does not honour it
        (`eleven_v3` — see `publishes_spoken_form`); the response carried no
        alignment at all; or the normalisation **romanised** the input, for which
        see `_is_romanised`.

        Whitespace is collapsed because the honouring models return the field
        padded with a leading and a trailing space — measured, and the padding is
        itself the tell that separates a real normalisation from `"auto"`'s echo.
        A reference that differs from the transcript by invisible characters is a
        reference that fails a comparison for no reason a reader can see.
        """
        if not self.publishes_spoken_form:
            return None
        spoken = _characters_of(_pick(response, "normalized_alignment"))
        collapsed = " ".join(spoken.split())
        if not collapsed:
            return None
        if _is_romanised(text, collapsed):
            return None
        return collapsed

    def _cross_check_duration(
        self, response: Any, audio: Audio, native_rate: int, text: str
    ) -> None:
        """Refuse a clip whose decoded length disagrees with its own alignment.

        The alignment's last character end time is the vendor's statement of how
        long the clip is; the decoded sample count over the rate we *assumed* is
        ours. When those two disagree, the sample rate about to be stamped on the
        audio is wrong — the commonest cause being an `output_format` that did not
        survive a parameter change — and every downstream number is then wrong in
        a way nothing else in the pipeline can detect: durations, speaking time,
        the SNR of a perturbation, and the digest that keys a transcript cassette.

        On the live probe the two agreed exactly (60,929 samples at 16 kHz =
        3.808 s, alignment end 3.808 s), so the tolerance below is loose enough to
        be about real mismatches rather than about rounding.
        """
        stated = _last_end_time(_pick(response, "alignment")) or _last_end_time(
            _pick(response, "normalized_alignment")
        )
        if stated is None or stated <= 0:
            return
        measured = float(np.asarray(audio).size) / float(native_rate)
        if abs(measured - stated) > max(0.25, 0.10 * stated):
            raise EngineUnavailable(
                self.name,
                f"decoded {measured:.3f}s of audio for {text[:40]!r} but the character "
                f"alignment ends at {stated:.3f}s. The declared sample rate "
                f"({native_rate} Hz) does not match the payload, so every duration, "
                "latency and audio digest derived from this clip would be wrong",
                "check the requested output_format against the sample rate the "
                "pipeline is running at",
            )

    def __repr__(self) -> str:
        return (
            f"ElevenLabsTTS(model={self.model_id!r}, voice={self.voice_id!r}, "
            f"live={self.live_enabled}, credits={self.credits_spent}, "
            f"spoken_form={self.publishes_spoken_form})"
        )
