"""ElevenLabs synthesis — and the reference string that makes WER mean anything.

WHAT THIS DEMONSTRATES
----------------------
One real vendor behind the same four-method protocol as the local engines, plus
the one thing about that vendor which decides whether every word error rate in
the suite is a measurement or a fabrication.

THE REFERENCE PROBLEM, MEASURED
-------------------------------
ElevenLabs **normalises text before it synthesises it**. Hand it

    "Table for four at 7:30, postcode SW1A 1AA."

and the audio says

    "table for four at seven thirty, postcode S W one A one A A"

Score a recogniser's transcript of that audio against the string you sent and
you are comparing a spoken form against a written one — you are measuring
*formatting policy*, not recognition. Measured on this repo's own live round trip
(`eleven_flash_v2_5` -> `nova-3`, 23 Aug 2026, both responses committed under
`fixtures/audio/cloud/`), one sentence recognised **perfectly** scores:

    reference = the string we sent          normalised WER 0.778  (7/9 words)
    reference = the normalised spoken form  normalised WER 0.000  (0/14 words)

Same audio, same transcript, same metric. Only the reference changed. A harness
that picks the first one publishes 78% error on flawless recognition, and the
digits-and-names rows — the ones whose entire purpose is proving a postcode
survives the channel — become the worst category in the suite while the vendors
are doing their job. Somebody then swaps a vendor to fix nothing.

So this engine calls `convert_with_timestamps` rather than `convert`, and takes
the reference from `normalized_alignment.characters` — the characters ElevenLabs
says it spoke. That is what `SynthesisResult.spoken_text` carries, and what
`lab.voice.adapter.audio_wer_report` scores against.

THE PART THE DOCUMENTATION DOES NOT TELL YOU
--------------------------------------------
`normalized_alignment` is only the spoken form if normalisation actually ran, and
by default **it does not** on the fast models. Measured, same sentence, same
voice, `convert_with_timestamps`:

    model                   apply_text_normalization   normalized_alignment
    eleven_flash_v2_5       "auto"  (the default)      "Table for four at 7:30, postcode SW1A 1AA."   3.07 s
    eleven_flash_v2_5       "on"                       "Table for four at seven thirty, postcode S W one A one A A."   4.83 s
    eleven_turbo_v2_5       "auto"                     "Table for four at 7:30, postcode SW1A 1AA."   3.90 s
    eleven_multilingual_v2  "on"                       "Table for four at seven thirty, postcode S W one A one A A."   3.95 s

Under `"auto"` the field comes back as the input text with a space bolted on each
end — while the audio still speaks the numerals out loud (the recogniser heard
"seven thirty" either way, and the duration difference shows the model saying
more). That is the trap in its most dangerous form: a field named
`normalized_alignment` that looks like a spoken form, is not one, and cannot be
told apart from one by inspection when the sentence happens to contain no digits.

This engine therefore requests `apply_text_normalization="on"` by default, and
when it is configured any other way it **declines to publish a spoken form at
all** (`spoken_text` stays None, the reference falls back to the input string and
is labelled `caller-input`). A reference that might be the spoken form is not a
reference.

WHY FLASH, AND WHY THE COST GUARD IS IN THE CODE
------------------------------------------------
`eleven_flash_v2_5` is the default because a synthesised caller is a fixture, not
a performance: it needs to be intelligible and cheap, and flash is half the
credit cost of the multilingual model at the same 16 kHz output. The free tier is
a *character* budget, so this engine:

*   **caches every clip on disk by content digest** — model, voice, output
    format, normalisation mode and text — so an unchanged line is never
    synthesised twice, across runs and across checkouts. Re-running the voice
    suite after editing one scenario costs one line, not the corpus.
*   **keeps a character ledger and refuses past a budget** rather than
    discovering the ceiling as a 401 in the middle of a corpus run. The refusal
    names the budget, the spend and the env var, because a cost guard whose
    message does not say how to raise it will simply be deleted.

WHY 16 kHz PCM AND NOT MP3
--------------------------
`pcm_16000` is requested directly, so there is no decode step, no codec
dependency, and no lossy round trip between the synthesiser and a word error
rate. It is also exactly the pipeline rate (`DEFAULT_SAMPLE_RATE`), so nothing is
resampled and the "we did not measure our own resampler" claim in
`lab.voice.engines.tts` costs nothing here.

WHAT THIS DOES NOT DO
---------------------
No streaming (`stream_with_timestamps` exists and buys nothing for a file-based
post-hoc pipeline — see `lab.voice.adapter` on that trade), no voice cloning, no
SSML, no request stitching via `previous_text`/`next_text`. Character-level
timings are fetched and used for one thing only: the duration cross-check below.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from lab.voice.engines.audiofile import read_audio, write_audio
from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    Audio,
    EngineUnavailable,
    SynthesisResult,
)
from lab.voice.engines.tts import LIVE_TTS_ENV_VAR, resample_rate

__all__ = [
    "ELEVENLABS_KEY_ENV_VAR",
    "ELEVENLABS_MODEL_ENV_VAR",
    "ELEVENLABS_VOICE_ENV_VAR",
    "CHAR_BUDGET_ENV_VAR",
    "CACHE_DIR_ENV_VAR",
    "DEFAULT_ELEVENLABS_MODEL",
    "DEFAULT_ELEVENLABS_VOICE",
    "DEFAULT_CHARACTER_BUDGET",
    "DEFAULT_CACHE_DIR",
    "PCM_RATES",
    "SPOKEN_FORM_NORMALISATION",
    "CharacterBudgetExceeded",
    "ClipCache",
    "ElevenLabsTTS",
]

#: The key is read from the environment at call time and never stored on the
#: instance, never logged, and never written into a trace or a fixture. The repo
#: documents variable *names*; values live outside it.
ELEVENLABS_KEY_ENV_VAR: str = "ELEVENLABS_API_KEY"

#: Overrides without touching code, for an A/B between models or voices.
ELEVENLABS_MODEL_ENV_VAR: str = "LAB_ELEVENLABS_MODEL"
ELEVENLABS_VOICE_ENV_VAR: str = "LAB_ELEVENLABS_VOICE"

#: Characters this process may synthesise before the engine refuses.
CHAR_BUDGET_ENV_VAR: str = "LAB_ELEVENLABS_CHAR_BUDGET"

#: Where synthesised clips are cached between runs.
CACHE_DIR_ENV_VAR: str = "LAB_TTS_CACHE_DIR"

#: Fast, cheap, 16 kHz-capable. See the module docstring.
DEFAULT_ELEVENLABS_MODEL: str = "eleven_flash_v2_5"

#: A stock premade voice ("River"), pinned by id rather than by name. A voice
#: named in a fixture manifest must be resolvable by a reviewer with a different
#: account, and only the id is stable.
DEFAULT_ELEVENLABS_VOICE: str = "SAz9YHcvj6GT2YYXdXww"

#: The only setting under which `normalized_alignment` is the spoken form.
SPOKEN_FORM_NORMALISATION: str = "on"

#: 20k characters is roughly the whole 55-row voice corpus once, and about twice
#: the monthly free allowance at flash's half-credit-per-character rate. Low
#: enough that a runaway loop is caught in the first minute; high enough that a
#: deliberate corpus run does not have to raise it.
DEFAULT_CHARACTER_BUDGET: int = 20_000

#: Outside the repo, so a cache survives `git clean` and is shared between
#: checkouts, and so no synthesised clip can be committed by an over-broad
#: `git add`.
DEFAULT_CACHE_DIR: Path = Path.home() / ".cache" / "lab-audio" / "tts-cache"

#: Raw-PCM output formats the API offers. Anything else gets the nearest of these
#: and a resample, which is stated in `describe()` rather than done quietly.
PCM_RATES: tuple[int, ...] = (8_000, 16_000, 22_050, 24_000, 32_000, 44_100, 48_000)


class CharacterBudgetExceeded(EngineUnavailable):
    """The per-process character budget would be exceeded by this line.

    A subclass of `EngineUnavailable` so a caller that already handles "no engine
    here" handles "no budget left" too, and distinct so a test can assert on it.
    Raised *before* the request, so the budget is a ceiling and not a report of
    what was already spent.
    """


def _pick(payload: Any, *names: str) -> Any:
    """First present attribute or key among `names`. `None` if none of them are.

    Exists because the SDK's Python attribute and its wire alias disagree, and
    both spellings are load-bearing depending on how the object reached us. The
    timestamped response exposes `audio_base_64` as an attribute and serialises
    it as `audio_base64`; a fixture recorded with `model_dump()` therefore has the
    second spelling and a live call has the first. Reading only one of them works
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
        return ""
        # An absent alignment is not an error here; the caller decides what a
        # missing spoken form means, because the answer differs by mode.
    characters = _pick(alignment, "characters") or []
    return "".join(str(character) for character in characters)


def _last_end_time(alignment: Any) -> float | None:
    """The alignment's final character end time, in seconds, or None."""
    if alignment is None:
        return None
    times = _pick(alignment, "character_end_times_seconds") or []
    return float(times[-1]) if times else None


def _cache_key(
    *, text: str, model_id: str, voice_id: str, output_format: str, normalisation: str
) -> str:
    """Content digest of everything that can change the audio.

    Every input that affects the samples is in the key, so a changed voice or a
    changed normalisation mode misses the cache instead of serving a clip
    synthesised under different settings. That is the same argument
    `lab.voice.engines.base.audio_digest` makes for keying a transcript cassette
    on the audio: a cache that answers for inputs it never saw is worse than no
    cache, because it is silent.
    """
    hasher = hashlib.sha256()
    hasher.update(
        "|".join(["elevenlabs-v1", model_id, voice_id, output_format, normalisation, text]).encode(
            "utf-8"
        )
    )
    return hasher.hexdigest()[:32]


class ClipCache:
    """Synthesised clips on disk, keyed by content digest. Free tier insurance.

    A WAV plus a JSON sidecar per clip. WAV because it needs no codec and this is
    scratch rather than a committed fixture (the committed fixtures are Opus —
    see `lab.voice.engines.audiofile` for that arithmetic); the sidecar because
    the *spoken form* has to survive the round trip too. A cache that returned
    the audio and lost `spoken_text` would hand back a clip whose WER reference
    silently reverted to the input string, which is precisely the bug this
    module exists to prevent.
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        configured = directory if directory is not None else os.environ.get(CACHE_DIR_ENV_VAR)
        self.directory = Path(configured) if configured else DEFAULT_CACHE_DIR
        self.hits = 0
        self.misses = 0

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.directory / f"{key}.wav", self.directory / f"{key}.json"

    def get(self, key: str) -> tuple[Audio, int, dict[str, Any]] | None:
        """Cached samples, rate and sidecar for `key`, or None on a miss."""
        audio_path, meta_path = self._paths(key)
        if not (audio_path.is_file() and meta_path.is_file()):
            self.misses += 1
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            audio, rate = read_audio(audio_path)
        except (OSError, ValueError, json.JSONDecodeError):
            # A half-written cache entry is a miss, not a crash. It will be
            # overwritten by the next synthesis of the same line.
            self.misses += 1
            return None
        self.hits += 1
        return audio, rate, meta

    def put(self, key: str, audio: Audio, sample_rate: int, meta: dict[str, Any]) -> None:
        """Store samples and sidecar under `key`. Best effort: a cache is not a result."""
        audio_path, meta_path = self._paths(key)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            write_audio(audio_path, audio, sample_rate)
            meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        except OSError:
            return

    def describe(self) -> str:
        return f"cache {self.directory} (hits={self.hits}, misses={self.misses})"


class ElevenLabsTTS:
    """ElevenLabs text-to-speech via the current SDK. Opt-in, cached, budgeted.

    Gated on `$LAB_LIVE_TTS` *and* a key, in that order. The flag rather than the
    key alone is the same rule the rest of the repo follows: a developer with an
    ElevenLabs key exported for other work has not consented to this test suite
    spending their characters.

    The identity string names the model and the voice — `tts:elevenlabs/
    eleven_flash_v2_5/SAz9YHcvj6GT2YYXdXww` — because "elevenlabs got worse" is
    not a finding and "this voice on this model got worse" is. It lands in the
    `engine` field of every trace event the engine produced, which is what lets
    `lab.voice.metrics.latencies_by_engine` slice a distribution by it.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        voice_id: str | None = None,
        apply_text_normalization: str = SPOKEN_FORM_NORMALISATION,
        env_var: str = LIVE_TTS_ENV_VAR,
        key_env_var: str = ELEVENLABS_KEY_ENV_VAR,
        cache: ClipCache | None = None,
        character_budget: int | None = None,
        timeout_s: float = 120.0,
        client: Any = None,
    ) -> None:
        """
        Args:
            model_id: Defaults to `$LAB_ELEVENLABS_MODEL` then `eleven_flash_v2_5`.
            voice_id: Defaults to `$LAB_ELEVENLABS_VOICE` then a stock premade id.
            apply_text_normalization: `"on"`, `"auto"` or `"off"`. Defaults to
                `"on"`, the only setting under which the returned
                `normalized_alignment` is the form that was actually spoken. Any
                other value is honoured and costs the spoken-form reference —
                see the module docstring for the measurements.
            env_var: Live opt-in flag.
            key_env_var: Name of the variable holding the key. The name is
                configuration; the value is never held on this object.
            cache: Clip cache. Defaults to a shared on-disk one.
            character_budget: Ceiling on characters synthesised by this instance.
                Defaults to `$LAB_ELEVENLABS_CHAR_BUDGET` then 20,000. Zero or
                negative disables the guard, which is a thing you should have to
                type.
            timeout_s: Per-request timeout.
            client: An already-built SDK client, or any object exposing
                `text_to_speech.convert_with_timestamps`. Injected by the tests so
                the extraction, decoding, caching, budget and cross-check logic
                are all exercised offline with no SDK and no key.
        """
        self.model_id = model_id or os.environ.get(ELEVENLABS_MODEL_ENV_VAR) or (
            DEFAULT_ELEVENLABS_MODEL
        )
        self.voice_id = voice_id or os.environ.get(ELEVENLABS_VOICE_ENV_VAR) or (
            DEFAULT_ELEVENLABS_VOICE
        )
        self.apply_text_normalization = apply_text_normalization
        self.env_var = env_var
        self.key_env_var = key_env_var
        self.cache = cache if cache is not None else ClipCache()
        self.timeout_s = timeout_s
        self.name = f"tts:elevenlabs/{self.model_id}/{self.voice_id}"
        self.is_replay = False
        self.voice = self.voice_id
        self.characters_spent = 0
        self.requests = 0
        self._client = client
        budget = (
            character_budget
            if character_budget is not None
            else _int_from_env(CHAR_BUDGET_ENV_VAR, DEFAULT_CHARACTER_BUDGET)
        )
        self.character_budget = budget

    # ----------------------------------------------------------- availability

    @property
    def live_enabled(self) -> bool:
        return bool(os.environ.get(self.env_var))

    def key_present(self) -> bool:
        """True when the key variable is set. Reads presence; never the value."""
        return bool(os.environ.get(self.key_env_var))

    def sdk_available(self) -> bool:
        """True when the `elevenlabs` package can be imported."""
        from importlib.util import find_spec  # noqa: PLC0415

        try:
            return find_spec("elevenlabs") is not None
        except (ImportError, ValueError):  # pragma: no cover - broken install
            return False

    def available(self) -> bool:
        if self._client is not None:
            return True
        return self.live_enabled and self.key_present() and self.sdk_available()

    @property
    def reference_is_spoken_form(self) -> bool:
        """True when this configuration yields a usable spoken-form WER reference."""
        return self.apply_text_normalization == SPOKEN_FORM_NORMALISATION

    def describe(self) -> str:
        state = "enabled" if self.live_enabled else f"disabled (set {self.env_var}=1)"
        reference = (
            "reference=normalized_alignment (spoken form)"
            if self.reference_is_spoken_form
            else f"reference=caller input — normalisation is {self.apply_text_normalization!r}, "
            "so the spoken form is unknown and is not published"
        )
        return (
            f"{self.name} (network, {state}; key {self.key_env_var} "
            f"{'set' if self.key_present() else 'unset'}; {reference}; "
            f"{self.cache.describe()}; spent {self.characters_spent}/{self.character_budget} chars)"
        )

    # -------------------------------------------------------------- synthesis

    def output_format_for(self, sample_rate: int) -> tuple[str, int]:
        """`(output_format, native_rate)` for a requested pipeline rate.

        Asks for raw PCM at the pipeline rate when the API offers it — which it
        does at 16 kHz, the rate this repo works at, so the usual path involves
        no decode and no resample. Otherwise it picks the nearest offered rate and
        the caller resamples through
        `lab.voice.engines.tts.resample_rate`, which is frequency-domain and
        therefore anti-aliased on the way down.
        """
        if sample_rate in PCM_RATES:
            return f"pcm_{sample_rate}", sample_rate
        native = min(PCM_RATES, key=lambda rate: (abs(rate - sample_rate), -rate))
        return f"pcm_{native}", native

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.live_enabled:
            raise EngineUnavailable(
                self.name,
                f"live synthesis is not enabled; {self.env_var} is unset",
                f"export {self.env_var}=1 to permit a billed API call, or use a local "
                "engine (KokoroTTS) or the committed fixtures (FixtureTTS)",
            )
        if not self.key_present():
            raise EngineUnavailable(
                self.name,
                f"{self.key_env_var} is not set",
                f"export {self.key_env_var} (the repo stores variable names, never values)",
            )
        if not self.sdk_available():
            raise EngineUnavailable(
                self.name,
                "the 'elevenlabs' package is not installed",
                "run scripts/setup_audio.sh --only cloud (it pip-installs the two "
                "vendor SDKs; neither is a dependency of this package)",
            )
        from elevenlabs.client import ElevenLabs  # noqa: PLC0415 - optional, live-only

        self._client = ElevenLabs(
            api_key=os.environ[self.key_env_var], timeout=self.timeout_s
        )
        return self._client

    def _check_budget(self, text: str) -> None:
        if self.character_budget <= 0:
            return
        if self.characters_spent + len(text) > self.character_budget:
            raise CharacterBudgetExceeded(
                self.name,
                f"synthesising {len(text)} more character(s) would pass the budget of "
                f"{self.character_budget} ({self.characters_spent} already spent in this "
                "process). Cached lines are free and do not count",
                f"raise {CHAR_BUDGET_ENV_VAR}, or pass character_budget=, if the spend "
                "is intended",
            )

    def synthesise(
        self, text: str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, voice: str | None = None
    ) -> SynthesisResult:
        """Synthesise `text`, from cache when possible, and report what was spoken.

        Raises:
            CharacterBudgetExceeded: before the request, if it would pass the budget.
            EngineUnavailable: if the engine is not enabled, keyed and installed;
                if the response carries no audio; or if the decoded duration
                disagrees with the character alignment (see below).
        """
        voice_id = voice or self.voice_id
        output_format, native_rate = self.output_format_for(sample_rate)
        key = _cache_key(
            text=text,
            model_id=self.model_id,
            voice_id=voice_id,
            output_format=output_format,
            normalisation=self.apply_text_normalization,
        )
        cached = self.cache.get(key)
        if cached is not None:
            audio, rate, meta = cached
            return SynthesisResult(
                audio=audio if rate == sample_rate else resample_rate(audio, rate, sample_rate),
                sample_rate=sample_rate,
                engine=self.name,
                voice=voice_id,
                # A file read is not a synthesis and must not be timed as one —
                # the same rule FixtureTTS follows.
                synthesis_s=None,
                replayed=True,
                text=text,
                spoken_text=meta.get("spoken_text"),
            )

        self._check_budget(text)
        client = self._ensure_client()
        started = time.perf_counter()
        response = client.text_to_speech.convert_with_timestamps(
            voice_id,
            text=text,
            model_id=self.model_id,
            output_format=output_format,
            apply_text_normalization=self.apply_text_normalization,
        )
        elapsed = time.perf_counter() - started
        self.characters_spent += len(text)
        self.requests += 1

        audio = self._decode(response, native_rate)
        spoken = self._spoken_text(response)
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
                "text": text,
                "spoken_text": spoken,
                "sample_rate": native_rate,
            },
        )
        return SynthesisResult(
            audio=(
                audio if native_rate == sample_rate else resample_rate(audio, native_rate, sample_rate)
            ),
            sample_rate=sample_rate,
            engine=self.name,
            voice=voice_id,
            synthesis_s=elapsed,
            text=text,
            spoken_text=spoken,
        )

    # ------------------------------------------------------------ extraction

    def _decode(self, response: Any, native_rate: int) -> Audio:
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
                f"samples; the response is not the raw PCM this engine requested",
                "check output_format is a pcm_* value and not a container format",
            )
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
        del native_rate  # the rate is what we asked for; the cross-check verifies it
        return samples

    def _spoken_text(self, response: Any) -> str | None:
        """The words the engine says it spoke, or None when it cannot be trusted to know.

        None in two distinct cases, both of which mean "do not publish a spoken
        form": normalisation was not requested (so the field is an echo of the
        input, see the module docstring), or the response carried no alignment at
        all. Whitespace is collapsed because the field arrives padded with a
        leading and trailing space, and a reference that differs from the
        transcript by invisible characters is a reference that fails a string
        comparison for no reason a reader can see.
        """
        if not self.reference_is_spoken_form:
            return None
        spoken = _characters_of(_pick(response, "normalized_alignment"))
        collapsed = " ".join(spoken.split())
        return collapsed or None

    def _cross_check_duration(
        self, response: Any, audio: Audio, native_rate: int, text: str
    ) -> None:
        """Refuse a clip whose decoded length disagrees with its own alignment.

        The alignment's last character end time is the vendor's statement of how
        long the clip is; the decoded sample count over the rate we *assumed* is
        ours. When those two disagree, the sample rate we are about to stamp on
        the audio is wrong — the commonest cause being an `output_format` that
        did not survive a parameter change — and every downstream number is then
        wrong in a way nothing else in the pipeline can detect: durations,
        speaking time, SNR of a perturbation, and the digest that keys a
        transcript cassette. On the live probe the two agreed to the millisecond
        (2.647 s vs 2.647 s), so the tolerance below is loose enough to be about
        real mismatches and not about rounding.
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
            f"live={self.live_enabled}, spent={self.characters_spent})"
        )


def _int_from_env(name: str, default: int) -> int:
    """Read an int from the environment, falling back on anything unparseable.

    A malformed budget is not a reason to fail a run and is a reason not to
    silently mean "unlimited": the default is restored instead.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default
