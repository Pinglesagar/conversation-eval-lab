"""Deepgram nova-3 recognition — batch, verbatim, and word-level.

WHAT THIS DEMONSTRATES
----------------------
The recognition half of the real stack, wired so that the three things a voice
eval actually needs from a recogniser are all available and none of them can be
confused with each other:

    the scored string    verbatim tokens, `smart_format=false`
    the display string   vendor-prettified, and structurally unable to be scored
    the word detail      per-word start, end and confidence

WHY `smart_format=false` IS NOT A PREFERENCE
--------------------------------------------
It is the difference between a measurement and a fabrication, and the number was
measured. One sentence, synthesised and transcribed by this repo's own engines:

    spoken       "The postcode is S W one A one A A."
    raw          "the postcode is s w one a one a a"      <- scored, WER 0.000
    smart_format "The postcode is SW1A1AA"                <- display, WER 0.700

The recognition is perfect — every digit and every letter of the postcode is
right. Scored word-by-word against the spoken form, the smart-formatted string
reports 70% word error. On the date-of-birth row it reports 55.6%, having rewritten
"the fourteenth of March, nineteen eighty-two" as "03/14/1982" — in US order, for
a UK date. On the sort-code row, 80%.

Nothing is wrong with either string; they are formatting the same content for
different audiences. Comparing them measures **formatting policy**, not
recognition.

The consequence, if this is got wrong, is specific and bad: the digits-and-names
rows — the ten whose entire purpose is proving a postcode survives the channel —
become the worst-scoring category in the suite while both vendors are performing
flawlessly, and the suite is then used to argue for a vendor change that fixes
nothing. `engines/WER_NORMALISATION.md` is the long version.

So this engine requests `smart_format=false` and `punctuate=false` for anything
scored. The prettified string is available, opt-in, as `display_text`, and it
travels under a trace key called `display_text_unscored`. Three separate
mechanisms keep the two apart, because one would eventually be forgotten:

1.  Different fields on the result. `text` is scored; `display_text` is not.
2.  A `formatting` flag on the result, written into every trace event, and a
    *refusal* in `lab.voice.adapter.audio_wer_report` when the scored text is
    smart-formatted. This is the load-bearing one: it makes the mistake fail
    loudly instead of publishing a plausible wrong number.
3.  The display string is a **second request**, off by default. It therefore
    cannot be the only string available, so it can never end up scored because
    nothing else was there.

WHY BATCH AND NOT STREAMING
---------------------------
The pre-recorded endpoint, on a whole buffer. That matches the half-duplex,
file-based design argued for in `lab.voice.adapter`, and it buys determinism: the
same clip returns the same transcript, so a committed cassette is a real fixture
rather than an approximation of one. Streaming would buy interim hypotheses and
cost reproducibility.

It also costs something real, and the cost should be stated rather than
discovered. Deepgram's own guidance for code-switched audio is to drop
`endpointing` to 100 ms — and **`endpointing` is a streaming parameter with no
meaning on the pre-recorded endpoint.** So the vendor's recommended mitigation
for the hardest multilingual case is not available to this harness at all. That is
a limit of the batch design, it is not fixable by tuning, and a suite that quietly
sent the parameter anyway would be claiming a mitigation it never applied.

NO NETWORK DEPENDENCY
---------------------
The request is a POST of raw bytes with two headers, so it is issued with
`urllib` from the standard library rather than through the vendor SDK or `httpx`.
Neither is a dependency of this package, and adding one so that a live-only path
can exist would make a clean clone heavier for no benefit to anyone who never
exports a key. `transport` is injectable, which is how every line of the parsing,
the word extraction and the language guard is tested offline with no key and no
network.

THE LANGUAGE FACTS, AND THE ONE THAT ENDS A MARKET
--------------------------------------------------
`nova-3` code-switches — the `multi` model — across **exactly ten** languages:
English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese, Italian,
Dutch. Mandarin, **Cantonese (`zh-HK`)**, Korean and Vietnamese are supported
monolingually and are *not* in that set.

Deepgram therefore distinguishes Cantonese from Mandarin. ElevenLabs does not: a
live read of `GET /v1/models` returned zero occurrences of `yue`, `zh-HK` or
"Cantonese" across all nine models, and the only Chinese language id on offer is
`zh`, named "Mandarin Chinese" on `eleven_v3`. So Hong Kong can be *recognised*
and cannot be *synthesised*, which means it cannot be audio-tested end to end at
all. `lab.voice.engines.coverage` turns that into a checkable matrix; this module
only supplies the recognition half of it.

Asking for `multi` with a language outside the ten is refused rather than sent.
The API would accept it and simply not code-switch, and a run that believes it
tested code-switching when it did not is worse than a run that failed.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    SCORABLE_FORMATTING,
    Audio,
    EngineUnavailable,
    Transcription,
    WordTiming,
    quantise_pcm16,
)
from lab.voice.engines.stt import LIVE_STT_ENV_VAR

__all__ = [
    "DEEPGRAM_KEY_ENV_VAR",
    "DEEPGRAM_MODEL_ENV_VAR",
    "DEEPGRAM_ENDPOINT",
    "DEFAULT_DEEPGRAM_MODEL",
    "MULTI_LANGUAGE",
    "MULTI_LANGUAGES",
    "MONOLINGUAL_ONLY_LANGUAGES",
    "Transport",
    "CodeSwitchingUnsupported",
    "LanguageOptionConflict",
    "DeepgramSTT",
    "wav_container",
]

#: Read from the environment at call time; never stored on the instance, never
#: logged, never written to a trace or a fixture.
DEEPGRAM_KEY_ENV_VAR: str = "DEEPGRAM_API_KEY"

#: Override the model without touching code, for an A/B.
DEEPGRAM_MODEL_ENV_VAR: str = "LAB_DEEPGRAM_MODEL"

#: The pre-recorded (batch) endpoint. Not the streaming one — see the docstring.
DEEPGRAM_ENDPOINT: str = "https://api.deepgram.com/v1/listen"

DEFAULT_DEEPGRAM_MODEL: str = "nova-3"

#: The value of `language` that turns on code-switching.
MULTI_LANGUAGE: str = "multi"

#: The exact set `multi` covers. Ten, and the boundary matters more than the
#: contents: everything outside it is monolingual-only, and asking for `multi`
#: anyway yields a run that silently did not code-switch.
MULTI_LANGUAGES: frozenset[str] = frozenset(
    {"en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it", "nl"}
)

#: Supported by nova-3 for one-language-at-a-time transcription only. `zh-HK` is
#: in here, and its presence is the whole Hong Kong finding: the recogniser can
#: tell Cantonese from Mandarin, and no TTS model in the stack can produce it.
MONOLINGUAL_ONLY_LANGUAGES: frozenset[str] = frozenset(
    {"zh", "zh-CN", "zh-TW", "zh-HK", "ko", "vi"}
)

#: A transport takes (url, body, headers) and returns the decoded JSON response.
#: Injectable so every parsing path is testable with no key and no network.
Transport = Callable[[str, bytes, Mapping[str, str]], dict[str, Any]]


class LanguageOptionConflict(EngineUnavailable):
    """`detect_language` was combined with an explicit `language`. Measured to be fatal.

    Not a style rule. Measured, on this repo's own committed clips:

        clip                     language     detect_language   transcript
        ------------------------------------------------------------------
        Japanese, 2.14 s         "multi"      true              ""      conf 0.0
        Japanese, 2.14 s         "multi"      false             correct conf 0.999
        Japanese, 2.14 s         "ja"         false             correct conf 1.0
        Arabic, 2.93 s           "ar"         true              ""      conf 0.0
        Arabic, 2.93 s           "ar"         false             correct conf 0.998

    Sending both makes the API return an **empty transcript with confidence 0.0**
    and a `detected_language` of `en`. Nothing errors. The row simply scores 100%
    word error, and the obvious conclusion — "the recogniser cannot handle
    Japanese" or "our TTS produced unintelligible audio" — is wrong in a way that
    would have been recorded as a vendor limitation and a market written off.

    That is exactly the kind of self-inflicted wound this suite exists to catch,
    and it was caught by re-transcribing a cached clip under a second setting.
    So the two options are now mutually exclusive at construction: a run cannot
    reach the API in the combination that silently returns nothing.
    """


class CodeSwitchingUnsupported(EngineUnavailable):
    """`multi` was requested for a language it does not cover.

    Its own class because the failure is silent at the vendor and must not be
    silent here: the API accepts the request and transcribes monolingually, so a
    suite that sent it would report a code-switching result it never obtained.
    """


def wav_container(audio: Audio, sample_rate: int) -> bytes:
    """Wrap mono float samples in a 44-byte RIFF/WAVE header as 16-bit PCM.

    Deepgram accepts raw PCM if told the encoding and rate in query parameters,
    and accepts a WAV container with no parameters at all. The container is
    chosen because it carries the sample rate *inside the bytes*: a rate passed
    beside the audio is a rate that can disagree with it, and a clip transcribed
    at the wrong declared rate comes back plausible and wrong — pitch-shifted
    speech that the recogniser does its best with. Self-describing bytes remove
    the whole class of mistake.

    Built by hand rather than through `soundfile` so that the live path has no
    optional dependency, and through the same `quantise_pcm16` the audio digest
    uses so the bytes sent are exactly the bytes keyed.
    """
    pcm = quantise_pcm16(audio).tobytes()
    byte_rate = sample_rate * 2  # mono, 2 bytes per sample
    header = b"".join(
        (
            b"RIFF",
            (36 + len(pcm)).to_bytes(4, "little"),
            b"WAVEfmt ",
            (16).to_bytes(4, "little"),  # fmt chunk size
            (1).to_bytes(2, "little"),  # PCM
            (1).to_bytes(2, "little"),  # mono
            int(sample_rate).to_bytes(4, "little"),
            byte_rate.to_bytes(4, "little"),
            (2).to_bytes(2, "little"),  # block align
            (16).to_bytes(2, "little"),  # bits per sample
            b"data",
            len(pcm).to_bytes(4, "little"),
        )
    )
    return header + pcm


def _urllib_transport(
    url: str, body: bytes, headers: Mapping[str, str], *, timeout_s: float = 120.0
) -> dict[str, Any]:
    """POST `body` and decode the JSON response, using only the standard library.

    An HTTP error is re-raised as `EngineUnavailable` with the vendor's own
    message attached, because "HTTP 401" without the body is a guess and the body
    usually says exactly which parameter was rejected.
    """
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:  # pragma: no cover - live only
        detail = error.read().decode("utf-8", "replace")[:400]
        raise EngineUnavailable(
            "stt:deepgram",
            f"the API returned HTTP {error.code}: {detail}",
            "check the model and language are valid together, and that the key is live",
        ) from error
    except urllib.error.URLError as error:  # pragma: no cover - live only
        raise EngineUnavailable(
            "stt:deepgram",
            f"could not reach {url}: {error.reason}",
            "check network access, or run offline with RecordedSTT and the committed cassette",
        ) from error


class DeepgramSTT:
    """Deepgram pre-recorded transcription. Opt-in, verbatim by default.

    Gated on `$LAB_LIVE_STT` *and* a key, and the refusal names whichever is
    missing rather than stopping at the first one it noticed — being told about
    one blocker at a time turns setup into three failed runs.

    The identity string is `stt:deepgram/nova-3/en/raw`: model, language *and*
    formatting. Formatting is in the identity because it changes the scored
    string, so "which formatting produced this figure?" has to be answerable from
    the trace's `engine` field alone, without cross-referencing anything.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        language: str | None = "en",
        smart_format: bool = False,
        want_display: bool = False,
        detect_language: bool = False,
        env_var: str = LIVE_STT_ENV_VAR,
        key_env_var: str = DEEPGRAM_KEY_ENV_VAR,
        endpoint: str = DEEPGRAM_ENDPOINT,
        timeout_s: float = 120.0,
        transport: Transport | None = None,
    ) -> None:
        """
        Args:
            model: Defaults to `$LAB_DEEPGRAM_MODEL` then `nova-3`.
            language: A language code, or `"multi"` for code-switching, or None
                to say nothing and let `detect_language` decide. `"multi"` is
                validated against `MULTI_LANGUAGES` by `require_code_switching`.
            smart_format: Formatting of the **scored** string. Defaults to False
                and should stay there. Setting it True is honoured, is recorded as
                `formatting="smart"` on the result and in the trace, and causes
                `audio_wer_report` to refuse the trace. That combination is
                deliberate: the option exists so the failure can be demonstrated,
                not so it can be used.
            want_display: Also fetch a prettified transcript for human display, as
                a second request. Off by default so the display string can never
                be the only one available.
            detect_language: Ask the API to detect the language rather than being
                told it. **Mutually exclusive with `language`**, and refused if
                both are given — see `LanguageOptionConflict`, which exists
                because combining them silently returned empty transcripts.
            env_var: Live opt-in flag.
            key_env_var: Name of the variable holding the key.
            endpoint: Overridable for a regional or proxied deployment.
            timeout_s: Per-request timeout.
            transport: Injected in tests. When supplied, `available()` is True and
                no key or flag is consulted — the tests exercise every parsing
                path with no credentials in the environment.
        """
        if detect_language and language is not None:
            raise LanguageOptionConflict(
                f"stt:deepgram/{model or DEFAULT_DEEPGRAM_MODEL}/{language}",
                f"detect_language=True cannot be combined with language={language!r}. "
                "Measured on this repo's committed clips: sending both returns an EMPTY "
                "transcript with confidence 0.0 and detected_language 'en', with no error "
                "— so the row scores 100% word error and the blame lands on the "
                "synthesiser or on the language, neither of which is at fault",
                "pass language=None to detect, or drop detect_language to pin a language "
                "(DeepgramSTT.for_language picks the right pin, including 'multi')",
            )
        self.model = model or os.environ.get(DEEPGRAM_MODEL_ENV_VAR) or DEFAULT_DEEPGRAM_MODEL
        self.language = language
        self.smart_format = smart_format
        self.want_display = want_display
        self.detect_language = detect_language
        self.env_var = env_var
        self.key_env_var = key_env_var
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self._transport = transport
        self.is_replay = False
        self.requests = 0
        self.audio_seconds = 0.0
        self.formatting = "smart" if smart_format else SCORABLE_FORMATTING
        self.name = (
            f"stt:deepgram/{self.model}/{self.language or 'detect'}/{self.formatting}"
        )

    # ----------------------------------------------------------- availability

    @property
    def live_enabled(self) -> bool:
        return bool(os.environ.get(self.env_var))

    def key_present(self) -> bool:
        """True when the key variable is set. Reads presence; never the value."""
        return bool(os.environ.get(self.key_env_var))

    def available(self) -> bool:
        if self._transport is not None:
            return True
        return self.live_enabled and self.key_present()

    def missing_requirements(self) -> list[str]:
        """Names of everything standing between this engine and a live call."""
        if self._transport is not None:
            return []
        missing = []
        if not self.live_enabled:
            missing.append(self.env_var)
        if not self.key_present():
            missing.append(self.key_env_var)
        return missing

    def describe(self) -> str:
        state = "enabled" if self.live_enabled else f"disabled (set {self.env_var}=1)"
        scored = (
            "scored text is VERBATIM (smart_format=false)"
            if not self.smart_format
            else "scored text is SMART-FORMATTED — audio_wer_report will refuse this trace"
        )
        display = "display string fetched separately" if self.want_display else "no display string"
        switching = (
            f"code-switching over {len(MULTI_LANGUAGES)} languages"
            if self.language == MULTI_LANGUAGE
            else f"monolingual {self.language}"
        )
        return (
            f"{self.name} (network batch endpoint, {state}; key {self.key_env_var} "
            f"{'set' if self.key_present() else 'unset'}; {scored}; {display}; "
            f"{switching}; endpointing is a streaming parameter and does not apply here; "
            f"{self.requests} request(s) over {self.audio_seconds:.1f}s of audio)"
        )

    # ------------------------------------------------------------- the guards

    def _require_supported_language(self) -> None:
        if self.language != MULTI_LANGUAGE:
            return
        # `multi` itself is always fine; the guard is for callers who pin a
        # specific language *and* expect switching, checked via for_language().
        return

    @classmethod
    def for_language(cls, language: str, **kwargs: Any) -> "DeepgramSTT":
        """Build an engine for `language`, choosing `multi` when it can switch.

        The factory exists so the choice is made in one place and is explainable:
        a language inside the ten gets the code-switching model, and anything
        outside gets a monolingual engine and is *told* it got one. Scattering
        this decision across scenario rows is how half a corpus ends up
        accidentally monolingual.
        """
        if language in MULTI_LANGUAGES:
            return cls(language=MULTI_LANGUAGE, **kwargs)
        return cls(language=language, **kwargs)

    @classmethod
    def require_code_switching(cls, languages: Mapping[str, str] | list[str]) -> None:
        """Raise unless every language in `languages` is inside the `multi` set.

        Called by the code-switching rows before they run. The refusal is the
        point: the API would accept `multi` for Cantonese or Arabic and quietly
        transcribe monolingually, so a row that skipped this check could report a
        code-switching pass it never earned.
        """
        wanted = list(languages)
        outside = [code for code in wanted if code not in MULTI_LANGUAGES]
        if not outside:
            return
        raise CodeSwitchingUnsupported(
            f"stt:deepgram/{DEFAULT_DEEPGRAM_MODEL}/{MULTI_LANGUAGE}",
            f"{', '.join(sorted(outside))} {'is' if len(outside) == 1 else 'are'} not in "
            f"the {len(MULTI_LANGUAGES)}-language code-switching set "
            f"({', '.join(sorted(MULTI_LANGUAGES))}). The API would accept the request and "
            "transcribe monolingually, so the row would report a code-switching result it "
            "never obtained",
            "run these languages as separate monolingual rows and label them as such, or "
            "drop the row and record the gap — see lab.voice.engines.coverage",
        )

    # -------------------------------------------------------------- transcribe

    def _params(self, *, smart: bool) -> dict[str, str]:
        params: dict[str, str] = {
            "model": self.model,
            "smart_format": "true" if smart else "false",
            "punctuate": "true" if smart else "false",
            # Always on: word timings are what let a failure be attributed to a
            # token instead of to a sentence.
            "words": "true",
        }
        # Exactly one of these two ever appears. The constructor refuses the
        # combination, so this is a statement of that invariant rather than a
        # second place it could drift.
        if self.language is not None:
            params["language"] = self.language
        elif self.detect_language:
            params["detect_language"] = "true"
        return params

    def _post(self, body: bytes, *, smart: bool) -> dict[str, Any]:
        url = f"{self.endpoint}?{urllib.parse.urlencode(self._params(smart=smart))}"
        if self._transport is not None:
            return self._transport(url, body, {"Content-Type": "audio/wav"})
        missing = self.missing_requirements()
        if missing:
            listing = " and ".join(
                [", ".join(missing[:-1]), missing[-1]] if len(missing) > 1 else missing
            )
            raise EngineUnavailable(
                self.name,
                f"live transcription needs {listing}, which "
                f"{'is' if len(missing) == 1 else 'are'} not set",
                f"export {self.env_var}=1 to permit billed API calls and export "
                f"{self.key_env_var} (this repo stores variable names, never values). "
                "Or stay offline: RecordedSTT replays the committed cassette and "
                "WhisperCppSTT runs locally",
            )
        headers = {
            "Authorization": f"Token {os.environ[self.key_env_var]}",
            "Content-Type": "audio/wav",
        }
        return _urllib_transport(url, body, headers, timeout_s=self.timeout_s)

    def transcribe(
        self, audio: Audio, *, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> Transcription:
        """Transcribe `audio`. One request, or two when a display string is wanted.

        Raises:
            EngineUnavailable: if the flag or the key is missing, or the response
                contains no alternative at all.
        """
        self._require_supported_language()
        body = wav_container(audio, sample_rate)
        started = time.perf_counter()
        document = self._post(body, smart=self.smart_format)
        elapsed = time.perf_counter() - started
        self.requests += 1
        self.audio_seconds += len(audio) / float(sample_rate)

        alternative, channel = self._alternative(document)
        words = self._words(alternative)
        display: str | None = None
        if self.want_display:
            display_document = self._post(body, smart=True)
            self.requests += 1
            display_alt, _ = self._alternative(display_document)
            display = str(display_alt.get("transcript", "")) or None

        confidence = alternative.get("confidence")
        language = channel.get("detected_language") or (
            None if self.language == MULTI_LANGUAGE else self.language
        )
        return Transcription(
            text=str(alternative.get("transcript", "")),
            engine=self.name,
            provenance="engine",
            confidence=self._clamped(confidence),
            language=str(language) if language else None,
            transcribe_s=elapsed,
            formatting="smart" if self.smart_format else SCORABLE_FORMATTING,
            display_text=display,
            words=words,
        )

    # ------------------------------------------------------------ extraction

    def _alternative(self, document: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """The first alternative of the first channel, and that channel.

        An empty transcript is a legitimate result — a clip of pure noise really
        does transcribe to nothing, and the digits rows need that case to be
        scoreable. A *missing* alternative is not: it means the response shape is
        not what this parser was written against, and returning "" for it would
        record a total recognition failure that never happened.
        """
        channels = (document.get("results") or {}).get("channels") or []
        if not channels:
            raise EngineUnavailable(
                self.name,
                "the response carried no results.channels; the response shape is not the "
                "one this parser expects",
                "check the model and language parameters, and the endpoint URL",
            )
        channel = channels[0] or {}
        alternatives = channel.get("alternatives") or []
        if not alternatives:
            raise EngineUnavailable(
                self.name,
                "the response carried a channel with no alternatives, so there is no "
                "hypothesis to record. An empty transcript is a result; a missing one is a "
                "parsing failure, and reporting it as an empty transcript would invent a "
                "total recognition failure",
                "check the request parameters against the API reference",
            )
        return dict(alternatives[0] or {}), dict(channel)

    def _words(self, alternative: Mapping[str, Any]) -> list[WordTiming]:
        """Word timings and confidences, skipping anything malformed rather than failing.

        `punctuated_word` is carried when the vendor supplies it and is display
        material only — the per-word mirror of the `text` / `display_text` split.
        """
        out: list[WordTiming] = []
        for raw in alternative.get("words") or []:
            if not isinstance(raw, Mapping):
                continue
            word = raw.get("word")
            start, end = raw.get("start"), raw.get("end")
            if word is None or start is None or end is None:
                continue
            out.append(
                WordTiming(
                    word=str(word),
                    start_s=max(0.0, float(start)),
                    end_s=max(0.0, float(end)),
                    confidence=self._clamped(raw.get("confidence")),
                    punctuated_word=(
                        str(raw["punctuated_word"]) if raw.get("punctuated_word") else None
                    ),
                )
            )
        return out

    @staticmethod
    def _clamped(value: Any) -> float | None:
        """A confidence in [0, 1], or None.

        Clamped rather than validated-and-raised because a vendor returning
        1.0000001 is a rounding artefact, not a reason to lose a session — while
        `Transcription` would reject it outright. Anything unparseable becomes
        None, which is the honest answer: `base.py` argues at length that
        inventing a confidence is how a confidence gate ends up gated on nothing.
        """
        if value is None:
            return None
        try:
            return min(1.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return None

    def __repr__(self) -> str:
        return (
            f"DeepgramSTT(model={self.model!r}, language={self.language!r}, "
            f"formatting={self.formatting!r}, live={self.live_enabled})"
        )
