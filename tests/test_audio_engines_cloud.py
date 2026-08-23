"""The two cloud engines, tested offline: no keys, no network, no spend.

Every test in this file runs on a clean clone with every credential unset. That is
the repo's cardinal rule and it is also the only way these tests are worth having:
a test that needs a key is a test that is skipped in CI, and a skipped test is not
a verdict anyone can read.

Three things make it possible. `ElevenLabsTTS(client=...)` and
`DeepgramSTT(transport=...)` accept an injected seam, so every line of the
extraction, caching, ledger and guard logic is exercised against recorded response
shapes. The response shapes themselves are not invented — they are the ones
captured from the live APIs and committed under `fixtures/audio/cloud/`. And the
few facts that cannot be derived (which models honour text normalisation, which
languages code-switch) are asserted *against that committed evidence*, so the
constants in the source cannot drift away from what was measured without a test
going red.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from lab.trace.build import TraceBuilder
from lab.voice.adapter import (
    TTS_OWNER_HARNESS,
    TTS_OWNER_SUT,
    DeliveredLatencyUnavailable,
    WERUnproven,
    audio_delivered_latency_report,
    audio_wer_report,
    readback_report,
    silent_correction_report,
    transcript_formattings,
    tts_intelligibility_probe,
    tts_owner,
    wer_reference_sources,
)
from lab.voice.engines.base import (
    DEFAULT_SAMPLE_RATE,
    EngineUnavailable,
    SynthesisResult,
)
from lab.voice.engines.clipcache import ClipCache, clip_cache_key
from lab.voice.engines.coverage import (
    CANTONESE,
    ELEVENLABS_FLASH_V2_5_LANGUAGES,
    ELEVENLABS_MULTILINGUAL_V2_LANGUAGES,
    SYNTHESISABLE_LANGUAGES,
    YUE_REMEDIATION,
    coverage_table,
    untestable_markets,
)
from lab.voice.engines.deepgram_stt import (
    MULTI_LANGUAGE,
    MULTI_LANGUAGES,
    CodeSwitchingUnsupported,
    DeepgramSTT,
    wav_container,
)
from lab.voice.engines.elevenlabs_tts import (
    CHARACTER_COST_MULTIPLIERS,
    DEFAULT_AGENT_VOICE,
    DEFAULT_CALLER_VOICE,
    ELEVENLABS_PREMADE_VOICES,
    SPOKEN_FORM_MODELS,
    CreditBudgetExceeded,
    ElevenLabsTTS,
    VoiceNotPermitted,
    credits_for,
)

CLOUD = Path(__file__).resolve().parents[1] / "fixtures" / "audio" / "cloud"
PROBE_TEXT = "Ring at 7:30 from SW1A 1AA."
SPOKEN_FORM = "Ring at seven thirty from S W one A one A A."


# --------------------------------------------------------------------------- #
# Committed vendor evidence
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def probe() -> dict[str, Any]:
    """The captured `/with-timestamps` probe: four models, one sentence."""
    return json.loads((CLOUD / "normalized_alignment_probe.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def capabilities() -> dict[str, Any]:
    """The captured `/v1/models` and `/v2/voices` snapshot."""
    return json.loads((CLOUD / "elevenlabs_capabilities.json").read_text(encoding="utf-8"))


def _case(probe: dict[str, Any], model: str, normalisation: str) -> dict[str, Any]:
    for case in probe["cases"]:
        if case["model_id"] == model and case["apply_text_normalization"] == normalisation:
            return case
    raise AssertionError(f"no probe case for {model} / {normalisation}")


# --------------------------------------------------------------------------- #
# The probed facts, asserted against the evidence that produced them
# --------------------------------------------------------------------------- #


def test_pcm_16000_returns_real_audio_on_a_free_tier_key(probe: dict[str, Any]) -> None:
    """The first undocumented fact. Every case came back as real 16 kHz PCM."""
    for case in probe["cases"]:
        assert case["output_format"] == "pcm_16000"
        assert case["http_status"] == 200
        assert case["audio_bytes"] > 0
        # A whole number of 16-bit samples, and a plausible length for the sentence.
        assert case["audio_bytes"] % 2 == 0
        assert 2.0 < case["audio_duration_s"] < 8.0


def test_normalized_alignment_is_never_null_on_any_probed_model(probe: dict[str, Any]) -> None:
    """The field's nullability turned out not to be the risk. Its *content* is."""
    for case in probe["cases"]:
        assert case["normalized_alignment"] is not None
        assert case["normalized_alignment"]["n_characters"] > 0


def test_flash_and_multilingual_publish_a_real_spoken_form(probe: dict[str, Any]) -> None:
    """The two models the engine trusts, and the reason it trusts them."""
    for model in ("eleven_flash_v2_5", "eleven_multilingual_v2"):
        case = _case(probe, model, "on")
        assert case["normalized_is_spoken_form"] is True
        spoken = case["normalized_alignment"]["characters_joined"].strip()
        assert spoken == SPOKEN_FORM
        # The digits and the postcode are spelled out. That is the whole point:
        # this is what the recogniser actually heard.
        assert "seven thirty" in spoken
        assert "7:30" not in spoken


def test_auto_normalisation_returns_the_input_wearing_a_helpful_label(
    probe: dict[str, Any],
) -> None:
    """The API default is the trap, and this is the shape of it.

    Padded, so it looks processed. Not the spoken form, so it is useless as a
    reference. And the audio is a comparable length either way, which is the
    evidence that the model spoke the normalised words and merely declined to
    report them.
    """
    auto = _case(probe, "eleven_flash_v2_5", "auto")
    on = _case(probe, "eleven_flash_v2_5", "on")
    assert auto["normalized_is_spoken_form"] is False
    assert auto["normalized_is_padded"] is True
    assert auto["normalized_alignment"]["characters_joined"].strip() == PROBE_TEXT
    assert abs(auto["audio_duration_s"] - on["audio_duration_s"]) < 0.5


def test_eleven_v3_silently_ignores_the_normalisation_request(probe: dict[str, Any]) -> None:
    """The decisive finding, and the reason the spoken-form predicate is model-aware.

    `eleven_v3` accepts `apply_text_normalization="on"`, returns HTTP 200, and
    hands back `normalized_alignment` byte-identical to `alignment`. A predicate
    that asked only "did I request normalisation?" would call this a spoken form
    and score a written-form reference — the original bug, restored on the newest
    model.
    """
    case = _case(probe, "eleven_v3", "on")
    assert case["http_status"] == 200
    assert case["normalized_is_spoken_form"] is False
    assert case["normalized_equals_input_exactly"] is True
    assert (
        case["normalized_alignment"]["characters_joined"]
        == case["alignment"]["characters_joined"]
    )
    assert "eleven_v3" not in SPOKEN_FORM_MODELS


def test_the_spoken_form_model_set_matches_the_probe_exactly(probe: dict[str, Any]) -> None:
    """A constant checked against its evidence. When a vendor fixes v3, this goes red."""
    measured = {
        case["model_id"]
        for case in probe["cases"]
        if case["apply_text_normalization"] == "on" and case["normalized_is_spoken_form"]
    }
    assert measured == set(SPOKEN_FORM_MODELS)


def test_no_elevenlabs_model_offers_cantonese(capabilities: dict[str, Any]) -> None:
    """The finding that ends a market, asserted against the live capability read."""
    cantonese = capabilities["cantonese_probe"]
    assert cantonese["occurrences_of_yue"] == 0
    assert cantonese["occurrences_of_cantonese"] == 0
    assert cantonese["occurrences_of_zh_hk"] == 0
    assert cantonese["only_chinese_language_id"] == ["zh"]
    assert CANTONESE not in SYNTHESISABLE_LANGUAGES


def test_the_api_exposes_no_per_voice_credit_multiplier(capabilities: dict[str, Any]) -> None:
    """Why the voice guard is an allowlist and not a rate lookup."""
    voice_probe = capabilities["voice_cost_field_probe"]
    assert voice_probe["cost_shaped_keys"] == []
    for voice in capabilities["voices"]:
        assert voice["available_for_tiers"] == []


def test_the_credit_multipliers_match_the_captured_rate_card(
    capabilities: dict[str, Any],
) -> None:
    """The multiplier lives on the model. This is the table, checked."""
    for model in capabilities["models"]:
        if not model["can_do_text_to_speech"]:
            continue
        expected = CHARACTER_COST_MULTIPLIERS.get(model["model_id"])
        if expected is None:
            continue
        assert expected == model["character_cost_multiplier"], model["model_id"]
    # And the one that costs twice as much as the default.
    assert CHARACTER_COST_MULTIPLIERS["eleven_v3"] == 2 * (
        CHARACTER_COST_MULTIPLIERS["eleven_flash_v2_5"]
    )


def test_both_default_voices_are_premade(capabilities: dict[str, Any]) -> None:
    """Free-tier keys cannot use Library voices over the API, so the defaults are stock."""
    by_id = {voice["voice_id"]: voice for voice in capabilities["voices"]}
    for voice_id in (DEFAULT_CALLER_VOICE, DEFAULT_AGENT_VOICE):
        assert by_id[voice_id]["category"] == "premade"
        assert voice_id in ELEVENLABS_PREMADE_VOICES
    assert DEFAULT_CALLER_VOICE != DEFAULT_AGENT_VOICE, (
        "caller and agent must not share a voice, or a report cannot attribute a "
        "synthesis regression to a side"
    )


def test_the_premade_allowlist_matches_the_captured_roster(
    capabilities: dict[str, Any],
) -> None:
    captured = {v["voice_id"] for v in capabilities["voices"] if v["category"] == "premade"}
    assert captured == set(ELEVENLABS_PREMADE_VOICES)
    non_premade = {v["voice_id"] for v in capabilities["voices"] if v["category"] != "premade"}
    assert non_premade and not (non_premade & set(ELEVENLABS_PREMADE_VOICES)), (
        "the account holds at least one non-premade voice and none of them may be "
        "on the allowlist, or the guard proves nothing"
    )


def test_the_caller_voice_covers_the_four_testable_switching_languages(
    capabilities: dict[str, Any],
) -> None:
    """Why George: one voice for English and all four code-switchable pairs.

    A voice change between rows would be a confound. This asserts the default
    caller voice is verified for every language the suite can actually
    code-switch, so no row needs a different voice.
    """
    by_id = {voice["voice_id"]: voice for voice in capabilities["voices"]}
    verified = set(by_id[DEFAULT_CALLER_VOICE]["verified_language_ids"])
    for language in ("en", "es", "hi", "ja", "fr"):
        assert language in verified, f"{language} not verified for the default caller voice"


# --------------------------------------------------------------------------- #
# ElevenLabsTTS — the refusals
# --------------------------------------------------------------------------- #


def test_synthesis_refuses_and_names_both_missing_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag and the key, both named at once. Not one blocker per failed run."""
    monkeypatch.delenv("LAB_LIVE_TTS", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    engine = ElevenLabsTTS()
    assert engine.available() is False
    assert engine.missing_requirements()[:2] == ["LAB_LIVE_TTS", "ELEVENLABS_API_KEY"]
    with pytest.raises(EngineUnavailable) as excinfo:
        engine.synthesise("hello")
    message = str(excinfo.value)
    assert "LAB_LIVE_TTS" in message and "ELEVENLABS_API_KEY" in message
    # And the remedy names the offline alternatives, not just the missing pieces.
    assert "KokoroTTS" in message and "FixtureTTS" in message


def test_a_key_alone_does_not_authorise_spending(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key exported for other work is not consent for this suite to spend it."""
    monkeypatch.delenv("LAB_LIVE_TTS", raising=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "not-a-real-key")
    engine = ElevenLabsTTS()
    assert engine.available() is False
    assert engine.missing_requirements() == ["LAB_LIVE_TTS"]


def test_a_voice_off_the_premade_allowlist_is_refused_at_construction() -> None:
    with pytest.raises(VoiceNotPermitted) as excinfo:
        ElevenLabsTTS(voice_id="hZTuv9Zqrq4yHYrEmF1r")  # the account's 'professional' voice
    assert "premade allowlist" in str(excinfo.value)
    assert "allow_non_premade" in str(excinfo.value)


def test_the_voice_guard_can_be_overridden_deliberately() -> None:
    engine = ElevenLabsTTS(voice_id="hZTuv9Zqrq4yHYrEmF1r", allow_non_premade=True)
    assert engine.voice_id == "hZTuv9Zqrq4yHYrEmF1r"


def test_a_per_call_voice_override_is_guarded_too() -> None:
    """The guard is on the synthesis path, not only the constructor."""
    engine = ElevenLabsTTS(client=_FakeElevenLabs())
    with pytest.raises(VoiceNotPermitted):
        engine.synthesise("hello", voice="hZTuv9Zqrq4yHYrEmF1r")


# --------------------------------------------------------------------------- #
# ElevenLabsTTS — the spoken-form predicate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("model", "normalisation", "expected"),
    [
        ("eleven_flash_v2_5", "on", True),
        ("eleven_multilingual_v2", "on", True),
        ("eleven_flash_v2_5", "auto", False),
        ("eleven_flash_v2_5", "off", False),
        # The regression this predicate exists for.
        ("eleven_v3", "on", False),
        ("eleven_v3_conversational", "on", False),
    ],
)
def test_the_spoken_form_predicate_is_model_aware(
    model: str, normalisation: str, expected: bool
) -> None:
    engine = ElevenLabsTTS(model_id=model, apply_text_normalization=normalisation)
    assert engine.publishes_spoken_form is expected


def test_describe_explains_which_way_the_reference_fell_back() -> None:
    """Two different failures, two different sentences. The reader should not guess."""
    normalisation_off = ElevenLabsTTS(apply_text_normalization="auto").describe()
    assert "normalisation is 'auto'" in normalisation_off

    wrong_model = ElevenLabsTTS(model_id="eleven_v3").describe()
    assert "does not honour apply_text_normalization" in wrong_model
    assert "echoes the input back" in wrong_model


# --------------------------------------------------------------------------- #
# ElevenLabsTTS — a fake client over the real response shape
# --------------------------------------------------------------------------- #


def _pcm_base64(samples: int, *, rate: int = DEFAULT_SAMPLE_RATE) -> str:
    """A quiet but non-silent tone, as the wire would carry it."""
    t = np.arange(samples, dtype=np.float64) / rate
    wave = (0.2 * np.sin(2 * np.pi * 220.0 * t) * 32767.0).astype("<i2")
    return base64.b64encode(wave.tobytes()).decode("ascii")


class _FakeTextToSpeech:
    def __init__(self, outer: "_FakeElevenLabs") -> None:
        self._outer = outer

    def convert_with_timestamps(self, voice_id: str, **kwargs: Any) -> dict[str, Any]:
        self._outer.calls.append({"voice_id": voice_id, **kwargs})
        samples = self._outer.samples
        duration = samples / DEFAULT_SAMPLE_RATE
        spoken = self._outer.spoken or kwargs.get("text", "")
        return {
            # The *wire* spelling, which is what a recorded fixture carries.
            "audio_base64": _pcm_base64(samples),
            "alignment": {
                "characters": list(kwargs.get("text", "")),
                "character_end_times_seconds": [duration],
            },
            "normalized_alignment": {
                "characters": list(f" {spoken} "),
                "character_end_times_seconds": [duration],
            },
        }


class _FakeElevenLabs:
    """Stands in for the SDK client, over the shape the live API really returns."""

    def __init__(self, *, samples: int = 16_000, spoken: str | None = SPOKEN_FORM) -> None:
        self.samples = samples
        self.spoken = spoken
        self.calls: list[dict[str, Any]] = []
        self.text_to_speech = _FakeTextToSpeech(self)


def test_the_wer_reference_is_the_spoken_form_not_the_input(tmp_path: Path) -> None:
    """The headline behaviour of the engine, end to end through the result type."""
    engine = ElevenLabsTTS(client=_FakeElevenLabs(), cache=_tmp_cache(tmp_path))
    result = engine.synthesise(PROBE_TEXT)
    assert result.text == PROBE_TEXT
    assert result.spoken_text == SPOKEN_FORM
    assert result.reference_source == "spoken-form"
    assert result.wer_reference == SPOKEN_FORM
    assert "7:30" not in result.wer_reference


def test_an_untrusted_model_declines_to_publish_a_spoken_form(tmp_path: Path) -> None:
    """`eleven_v3` gets the fallback, and the fallback is *labelled*, not silent."""
    engine = ElevenLabsTTS(
        model_id="eleven_v3", client=_FakeElevenLabs(), cache=_tmp_cache(tmp_path)
    )
    result = engine.synthesise(PROBE_TEXT)
    assert result.spoken_text is None
    assert result.reference_source == "caller-input"
    assert result.wer_reference == PROBE_TEXT


def test_the_engine_identity_names_the_model_and_the_voice() -> None:
    engine = ElevenLabsTTS()
    assert engine.name == f"tts:elevenlabs/eleven_flash_v2_5/{DEFAULT_CALLER_VOICE}"


def test_the_identity_lands_on_the_result(tmp_path: Path) -> None:
    engine = ElevenLabsTTS(client=_FakeElevenLabs(), cache=_tmp_cache(tmp_path))
    result = engine.synthesise("hello")
    assert result.engine == engine.name


def test_language_code_is_dropped_for_the_model_that_ignores_it(tmp_path: Path) -> None:
    """A pin the model never received would be worse than no pin: the run looks pinned."""
    client = _FakeElevenLabs()
    multilingual = ElevenLabsTTS(
        model_id="eleven_multilingual_v2",
        language_code="es",
        client=client,
        cache=_tmp_cache(tmp_path / "a"),
    )
    multilingual.synthesise("hola")
    assert "language_code" not in client.calls[-1]

    flash = ElevenLabsTTS(
        model_id="eleven_flash_v2_5",
        language_code="es",
        client=client,
        cache=_tmp_cache(tmp_path / "b"),
    )
    flash.synthesise("hola")
    assert client.calls[-1]["language_code"] == "es"


def test_a_response_with_no_audio_is_refused(tmp_path: Path) -> None:
    class _Empty:
        class text_to_speech:
            @staticmethod
            def convert_with_timestamps(voice_id: str, **kwargs: Any) -> dict[str, Any]:
                return {"alignment": None, "normalized_alignment": None}

    engine = ElevenLabsTTS(client=_Empty(), cache=_tmp_cache(tmp_path))
    with pytest.raises(EngineUnavailable, match="carried no audio"):
        engine.synthesise("hello")


def test_an_odd_byte_count_is_refused_rather_than_truncated(tmp_path: Path) -> None:
    """Truncating would turn the clip into noise that looks like a bad voice."""

    class _Odd:
        class text_to_speech:
            @staticmethod
            def convert_with_timestamps(voice_id: str, **kwargs: Any) -> dict[str, Any]:
                return {"audio_base64": base64.b64encode(b"\x01\x02\x03").decode("ascii")}

    engine = ElevenLabsTTS(client=_Odd(), cache=_tmp_cache(tmp_path))
    with pytest.raises(EngineUnavailable, match="not a whole number of 16-bit"):
        engine.synthesise("hello")


def test_a_duration_that_disagrees_with_the_alignment_is_refused(tmp_path: Path) -> None:
    """The cross-check that catches a sample rate silently not matching the payload."""

    class _Mismatched:
        class text_to_speech:
            @staticmethod
            def convert_with_timestamps(voice_id: str, **kwargs: Any) -> dict[str, Any]:
                return {
                    "audio_base64": _pcm_base64(16_000),  # 1.0 s
                    "alignment": {
                        "characters": ["a"],
                        "character_end_times_seconds": [9.0],  # the vendor says 9 s
                    },
                }

    engine = ElevenLabsTTS(client=_Mismatched(), cache=_tmp_cache(tmp_path))
    with pytest.raises(EngineUnavailable, match="does not match the payload"):
        engine.synthesise("hello")


# --------------------------------------------------------------------------- #
# The ledger and the budget
# --------------------------------------------------------------------------- #


def test_credits_are_charged_at_the_model_multiplier() -> None:
    text = "x" * 100
    assert credits_for(text, "eleven_flash_v2_5") == 50
    assert credits_for(text, "eleven_v3") == 100
    # An unknown model is charged at the dearest known rate, never the cheapest.
    assert credits_for(text, "eleven_something_new") == 100


def test_credits_round_up() -> None:
    assert credits_for("abc", "eleven_flash_v2_5") == 2  # 1.5 -> 2


def test_the_budget_refuses_before_the_request_not_after(tmp_path: Path) -> None:
    """A ceiling, not a receipt. The client must never be called."""
    client = _FakeElevenLabs()
    engine = ElevenLabsTTS(client=client, cache=_tmp_cache(tmp_path), credit_budget=10)
    with pytest.raises(CreditBudgetExceeded) as excinfo:
        engine.synthesise("x" * 100)  # 50 credits
    assert client.calls == [], "the budget guard let a billable request through"
    assert engine.credits_spent == 0
    assert "LAB_ELEVENLABS_CREDIT_BUDGET" in str(excinfo.value)


def test_the_ledger_tracks_characters_and_credits_separately(tmp_path: Path) -> None:
    engine = ElevenLabsTTS(client=_FakeElevenLabs(), cache=_tmp_cache(tmp_path))
    engine.synthesise("x" * 40)
    assert engine.characters_spent == 40
    assert engine.credits_spent == 20  # flash is 0.5x
    assert engine.requests == 1


def test_a_zero_budget_disables_the_guard(tmp_path: Path) -> None:
    engine = ElevenLabsTTS(client=_FakeElevenLabs(), cache=_tmp_cache(tmp_path), credit_budget=0)
    engine.synthesise("x" * 5_000)
    assert engine.credits_spent == 2_500


# --------------------------------------------------------------------------- #
# The digest cache — the property that makes a re-run free
# --------------------------------------------------------------------------- #


def _tmp_cache(root: Path) -> ClipCache:
    """A cache with both layers under `root`, so no test touches the real fixtures."""
    return ClipCache(committed=root / "committed", scratch=root / "scratch")


def test_a_cache_hit_spends_zero_credits_and_makes_no_request(tmp_path: Path) -> None:
    """The headline property. Stated as an assertion, not as a docstring claim."""
    cache = _tmp_cache(tmp_path)
    client = _FakeElevenLabs()
    first = ElevenLabsTTS(client=client, cache=cache)
    first.synthesise(PROBE_TEXT)
    assert len(client.calls) == 1
    assert first.credits_spent > 0

    # A fresh engine, a fresh ledger, the same cache: this is the re-run case.
    second_client = _FakeElevenLabs()
    second = ElevenLabsTTS(client=second_client, cache=ClipCache(
        committed=tmp_path / "committed", scratch=tmp_path / "scratch"
    ))
    result = second.synthesise(PROBE_TEXT)
    assert second_client.calls == [], "a cache hit issued a billable request"
    assert second.credits_spent == 0
    assert second.characters_spent == 0
    assert second.cached_lines == 1
    assert result.replayed is True
    assert result.synthesis_s is None, "a file read is not a synthesis and must not be timed"


def test_a_cache_hit_preserves_the_spoken_form(tmp_path: Path) -> None:
    """The loss that would be invisible.

    A cache that returned the audio and dropped the spoken form would hand back a
    clip whose WER reference quietly reverted to the input string — the original
    bug, reintroduced by a caching layer and therefore looked for in the wrong
    file.
    """
    cache = _tmp_cache(tmp_path)
    ElevenLabsTTS(client=_FakeElevenLabs(), cache=cache).synthesise(PROBE_TEXT)
    replayed = ElevenLabsTTS(
        client=_FakeElevenLabs(),
        cache=ClipCache(committed=tmp_path / "committed", scratch=tmp_path / "scratch"),
    ).synthesise(PROBE_TEXT)
    assert replayed.spoken_text == SPOKEN_FORM
    assert replayed.reference_source == "spoken-form"


@pytest.mark.parametrize(
    "change",
    [
        {"text": "different words"},
        {"voice": "Xb7hH8MSUJpSbSDYk0k2"},
        {"model": "eleven_multilingual_v2"},
        {"output_format": "pcm_24000"},
        {"normalisation": "auto"},
    ],
)
def test_every_input_that_changes_the_audio_changes_the_key(change: dict[str, str]) -> None:
    """A cache that answers for inputs it never saw is worse than no cache."""
    base = {
        "text": PROBE_TEXT,
        "voice": DEFAULT_CALLER_VOICE,
        "model": "eleven_flash_v2_5",
        "output_format": "pcm_16000",
        "normalisation": "on",
    }
    assert clip_cache_key(**base) != clip_cache_key(**{**base, **change})  # type: ignore[arg-type]


def test_the_key_is_stable_for_identical_inputs() -> None:
    args = {
        "text": PROBE_TEXT,
        "voice": DEFAULT_CALLER_VOICE,
        "model": "eleven_flash_v2_5",
        "output_format": "pcm_16000",
        "normalisation": "on",
    }
    assert clip_cache_key(**args) == clip_cache_key(**args)  # type: ignore[arg-type]


def test_normalisation_mode_is_in_the_key_because_it_changes_the_audio() -> None:
    """Measured: 3.808 s at 'on' against 3.854 s at 'auto'. Different sound, different key."""
    common = {
        "text": PROBE_TEXT,
        "voice": DEFAULT_CALLER_VOICE,
        "model": "eleven_flash_v2_5",
        "output_format": "pcm_16000",
    }
    assert clip_cache_key(**common, normalisation="on") != clip_cache_key(  # type: ignore[arg-type]
        **common, normalisation="auto"
    )


def test_the_committed_layer_is_read_before_the_scratch_layer(tmp_path: Path) -> None:
    cache = _tmp_cache(tmp_path)
    key = "deadbeef"
    audio = np.zeros(400, dtype=np.float64)
    cache.put(key, audio, DEFAULT_SAMPLE_RATE, {"spoken_text": "from scratch"})
    assert cache.get(key).layer == "scratch"  # type: ignore[union-attr]
    assert cache.promote(key) is True
    fresh = _tmp_cache(tmp_path)
    entry = fresh.get(key)
    assert entry is not None and entry.layer == "committed"
    assert fresh.committed_hits == 1


def test_writes_never_land_in_the_committed_layer(tmp_path: Path) -> None:
    """A fixture enters the repo by a decision with a diff, not as a test side effect."""
    cache = _tmp_cache(tmp_path)
    cache.put("abc123", np.zeros(200, dtype=np.float64), DEFAULT_SAMPLE_RATE, {})
    assert not (tmp_path / "committed").exists()
    assert (tmp_path / "scratch" / "abc123.wav").is_file()


def test_a_half_written_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    cache = _tmp_cache(tmp_path)
    (tmp_path / "scratch").mkdir(parents=True)
    (tmp_path / "scratch" / "orphan.wav").write_bytes(b"not a wav")
    assert cache.get("orphan") is None
    assert cache.misses == 1


def test_a_corrupt_sidecar_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    cache = _tmp_cache(tmp_path)
    cache.put("k", np.zeros(200, dtype=np.float64), DEFAULT_SAMPLE_RATE, {})
    (tmp_path / "scratch" / "k.json").write_text("{not json", encoding="utf-8")
    assert cache.get("k") is None


def test_membership_does_not_disturb_the_counters(tmp_path: Path) -> None:
    """A report counting what is already paid for must not perturb what it prints."""
    cache = _tmp_cache(tmp_path)
    cache.put("k", np.zeros(200, dtype=np.float64), DEFAULT_SAMPLE_RATE, {})
    hits, misses = cache.hits, cache.misses
    assert "k" in cache
    assert "nope" not in cache
    assert (cache.hits, cache.misses) == (hits, misses)


# --------------------------------------------------------------------------- #
# DeepgramSTT
# --------------------------------------------------------------------------- #


def _dg_response(
    transcript: str, *, confidence: float = 0.997, words: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The shape the pre-recorded endpoint really returns."""
    return {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": transcript,
                            "confidence": confidence,
                            "words": words if words is not None else [],
                        }
                    ]
                }
            ]
        }
    }


class _FakeTransport:
    """Records each request URL and replies from a queue keyed on smart_format."""

    def __init__(self, raw: str, smart: str | None = None) -> None:
        self.raw = raw
        self.smart = smart
        self.urls: list[str] = []
        self.bodies: list[bytes] = []

    def __call__(self, url: str, body: bytes, headers: Any) -> dict[str, Any]:
        self.urls.append(url)
        self.bodies.append(body)
        wants_smart = "smart_format=true" in url
        return _dg_response(self.smart if (wants_smart and self.smart) else self.raw)


def test_transcription_refuses_and_names_both_missing_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAB_LIVE_STT", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    engine = DeepgramSTT()
    assert engine.available() is False
    assert engine.missing_requirements() == ["LAB_LIVE_STT", "DEEPGRAM_API_KEY"]
    with pytest.raises(EngineUnavailable) as excinfo:
        engine.transcribe(np.zeros(160, dtype=np.float64))
    message = str(excinfo.value)
    assert "LAB_LIVE_STT" in message and "DEEPGRAM_API_KEY" in message
    assert "RecordedSTT" in message  # the offline alternative is named


def test_the_scored_request_asks_for_verbatim_text() -> None:
    """`smart_format=false` and `punctuate=false`, on the request, checked."""
    transport = _FakeTransport("ring at seven thirty from s w one a one a a")
    engine = DeepgramSTT(transport=transport)
    engine.transcribe(np.zeros(1600, dtype=np.float64))
    assert len(transport.urls) == 1
    url = transport.urls[0]
    assert "smart_format=false" in url
    assert "punctuate=false" in url
    assert "model=nova-3" in url
    assert "words=true" in url


def test_the_scored_transcript_is_flagged_raw_and_is_measurable() -> None:
    transport = _FakeTransport("ring at seven thirty")
    result = DeepgramSTT(transport=transport).transcribe(np.zeros(1600, dtype=np.float64))
    assert result.formatting == "raw"
    assert result.is_measurable is True
    assert result.display_text is None
    assert result.provenance == "engine"
    assert result.confidence == pytest.approx(0.997)


def test_smart_formatting_is_honoured_and_makes_the_result_unmeasurable() -> None:
    """The option exists so the failure can be demonstrated, not so it can be used."""
    transport = _FakeTransport("Ring at 07:30 from SW1A1AA.")
    result = DeepgramSTT(transport=transport, smart_format=True).transcribe(
        np.zeros(1600, dtype=np.float64)
    )
    assert result.formatting == "smart"
    assert result.is_measurable is False
    assert "smart" in DeepgramSTT(smart_format=True).name


def test_the_display_string_is_a_second_request_and_is_off_by_default() -> None:
    """So it can never be the only string available, and so never end up scored."""
    transport = _FakeTransport("ring at seven thirty", smart="Ring at 07:30.")
    default = DeepgramSTT(transport=transport).transcribe(np.zeros(1600, dtype=np.float64))
    assert default.display_text is None
    assert len(transport.urls) == 1

    transport = _FakeTransport("ring at seven thirty", smart="Ring at 07:30.")
    with_display = DeepgramSTT(transport=transport, want_display=True).transcribe(
        np.zeros(1600, dtype=np.float64)
    )
    assert with_display.text == "ring at seven thirty"
    assert with_display.display_text == "Ring at 07:30."
    assert with_display.formatting == "raw", "the scored string must stay verbatim"
    assert len(transport.urls) == 2
    assert "smart_format=false" in transport.urls[0]
    assert "smart_format=true" in transport.urls[1]


def test_word_timings_and_confidences_reach_the_result() -> None:
    words = [
        {"word": "ring", "start": 0.0, "end": 0.3, "confidence": 0.99},
        {"word": "postcode", "start": 0.4, "end": 0.9, "confidence": 0.42,
         "punctuated_word": "Postcode"},
        {"word": "malformed"},  # skipped, not fatal
    ]
    response = _dg_response("ring postcode", words=words)
    engine = DeepgramSTT(transport=lambda url, body, headers: response)
    result = engine.transcribe(np.zeros(1600, dtype=np.float64))
    assert [w.word for w in result.words] == ["ring", "postcode"]
    assert result.words[1].punctuated_word == "Postcode"
    assert result.mean_word_confidence == pytest.approx((0.99 + 0.42) / 2)
    assert result.lowest_confidence_words(1)[0].word == "postcode"


def test_the_lowest_confidence_word_reaches_the_trace_payload() -> None:
    """Where to look when a row fails, carried on the event rather than in a log."""
    words = [
        {"word": "table", "start": 0.0, "end": 0.2, "confidence": 0.99},
        {"word": "sw1a", "start": 0.3, "end": 0.8, "confidence": 0.41},
    ]
    response = _dg_response("table sw1a", words=words)
    result = DeepgramSTT(transport=lambda u, b, h: response).transcribe(
        np.zeros(1600, dtype=np.float64)
    )
    payload = result.trace_payload()
    assert payload["formatting"] == "raw"
    assert payload["word_count"] == 2
    assert payload["lowest_confidence_word"] == "sw1a"
    assert payload["lowest_word_confidence"] == pytest.approx(0.41)
    assert "display_text" not in payload, "the display key must name its own prohibition"


def test_the_display_string_travels_under_a_key_that_names_its_prohibition() -> None:
    transport = _FakeTransport("raw words", smart="Raw words.")
    result = DeepgramSTT(transport=transport, want_display=True).transcribe(
        np.zeros(1600, dtype=np.float64)
    )
    payload = result.trace_payload()
    assert payload["display_text_unscored"] == "Raw words."


def test_an_empty_transcript_is_a_result_but_a_missing_one_is_a_failure() -> None:
    """A clip of pure noise really does transcribe to nothing. That must be scoreable."""
    empty = DeepgramSTT(transport=lambda u, b, h: _dg_response("")).transcribe(
        np.zeros(1600, dtype=np.float64)
    )
    assert empty.text == ""
    assert empty.is_measurable is True

    with pytest.raises(EngineUnavailable, match="no results.channels"):
        DeepgramSTT(transport=lambda u, b, h: {"results": {"channels": []}}).transcribe(
            np.zeros(1600, dtype=np.float64)
        )
    with pytest.raises(EngineUnavailable, match="no alternatives"):
        DeepgramSTT(
            transport=lambda u, b, h: {"results": {"channels": [{"alternatives": []}]}}
        ).transcribe(np.zeros(1600, dtype=np.float64))


def test_an_out_of_range_confidence_is_clamped_not_fatal() -> None:
    response = _dg_response("hello", confidence=1.0000001)
    result = DeepgramSTT(transport=lambda u, b, h: response).transcribe(
        np.zeros(160, dtype=np.float64)
    )
    assert result.confidence == 1.0


def test_the_engine_identity_names_model_language_and_formatting() -> None:
    assert DeepgramSTT().name == "stt:deepgram/nova-3/en/raw"
    assert DeepgramSTT(language="multi").name == "stt:deepgram/nova-3/multi/raw"


# --------------------------------------------------------------------------- #
# The WAV container
# --------------------------------------------------------------------------- #


def test_the_wav_container_is_self_describing_and_round_trips(tmp_path: Path) -> None:
    """The rate lives inside the bytes, so it cannot disagree with them."""
    from lab.voice.engines.audiofile import read_audio

    t = np.arange(8_000, dtype=np.float64) / DEFAULT_SAMPLE_RATE
    audio = 0.5 * np.sin(2 * np.pi * 440.0 * t)
    payload = wav_container(audio, DEFAULT_SAMPLE_RATE)
    assert payload[:4] == b"RIFF"
    assert payload[8:12] == b"WAVE"
    assert len(payload) == 44 + audio.size * 2

    path = tmp_path / "probe.wav"
    path.write_bytes(payload)
    decoded, rate = read_audio(path)
    assert rate == DEFAULT_SAMPLE_RATE
    assert decoded.size == audio.size
    assert np.max(np.abs(decoded - audio)) < 1e-3


def test_the_container_declares_the_rate_it_was_given() -> None:
    payload = wav_container(np.zeros(100, dtype=np.float64), 8_000)
    assert int.from_bytes(payload[24:28], "little") == 8_000


# --------------------------------------------------------------------------- #
# Code switching
# --------------------------------------------------------------------------- #


def test_the_code_switching_set_has_exactly_ten_languages() -> None:
    assert len(MULTI_LANGUAGES) == 10
    assert MULTI_LANGUAGES == {"en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it", "nl"}


def test_for_language_picks_multi_only_where_switching_exists() -> None:
    assert DeepgramSTT.for_language("es").language == MULTI_LANGUAGE
    assert DeepgramSTT.for_language("ja").language == MULTI_LANGUAGE
    # Outside the ten: monolingual, and named as such rather than silently 'multi'.
    assert DeepgramSTT.for_language("zh").language == "zh"
    assert DeepgramSTT.for_language(CANTONESE).language == CANTONESE


@pytest.mark.parametrize("pair", [["en", CANTONESE], ["en", "ar"], ["en", "zh"], ["en", "ko"]])
def test_code_switching_is_refused_for_pairs_outside_the_ten(pair: list[str]) -> None:
    """The API would accept `multi` and transcribe monolingually. That must not pass silently."""
    with pytest.raises(CodeSwitchingUnsupported) as excinfo:
        DeepgramSTT.require_code_switching(pair)
    assert "code-switching set" in str(excinfo.value)
    assert "never obtained" in str(excinfo.value)


@pytest.mark.parametrize("pair", [["en", "es"], ["en", "hi"], ["en", "ja"], ["en", "fr"]])
def test_the_four_testable_pairs_pass_the_code_switching_guard(pair: list[str]) -> None:
    DeepgramSTT.require_code_switching(pair)  # must not raise


# --------------------------------------------------------------------------- #
# The market coverage matrix
# --------------------------------------------------------------------------- #


def test_hong_kong_cannot_be_audio_tested_at_all() -> None:
    """The headline finding, computed rather than asserted in prose."""
    hong_kong = [row for row in coverage_table() if row.market == "Hong Kong"]
    assert len(hong_kong) == 1
    row = hong_kong[0]
    assert row.verdict == "untestable"
    assert row.audio_testable is False
    assert row.not_synthesisable == (CANTONESE,)
    assert row.remediation == YUE_REMEDIATION
    assert "no TTS model" in row.reason


def test_singapore_and_the_uae_are_monolingual_not_untestable() -> None:
    """The middle verdict, which a pass/fail table would lose."""
    rows = {row.market: row for row in coverage_table()}
    for market, outside in (("Singapore", "zh"), ("United Arab Emirates", "ar")):
        row = rows[market]
        assert row.verdict == "monolingual"
        assert row.audio_testable is True
        assert row.not_synthesisable == ()
        assert row.outside_code_switching == (outside,)


@pytest.mark.parametrize("market", ["Spain", "India", "Japan", "France", "Germany"])
def test_the_code_switchable_markets_are_testable_end_to_end(market: str) -> None:
    row = {r.market: r for r in coverage_table()}[market]
    assert row.verdict == "code-switched"
    assert row.outside_code_switching == ()


def test_only_hong_kong_is_untestable() -> None:
    assert [row.market for row in untestable_markets()] == ["Hong Kong"]


def test_the_table_puts_the_worst_verdict_first() -> None:
    """People open a coverage table to find what is missing."""
    verdicts = [row.verdict for row in coverage_table()]
    assert verdicts[0] == "untestable"
    assert verdicts == sorted(
        verdicts, key={"untestable": 0, "monolingual": 1, "code-switched": 2}.__getitem__
    )


def test_the_tts_language_sets_match_the_committed_capability_snapshot(
    capabilities: dict[str, Any],
) -> None:
    """When a vendor ships a language, this test is the notification."""
    by_id = {model["model_id"]: model for model in capabilities["models"]}
    assert set(by_id["eleven_flash_v2_5"]["language_ids"]) == ELEVENLABS_FLASH_V2_5_LANGUAGES
    assert (
        set(by_id["eleven_multilingual_v2"]["language_ids"])
        == ELEVENLABS_MULTILINGUAL_V2_LANGUAGES
    )


def test_recognition_is_ahead_of_synthesis_for_cantonese() -> None:
    """The structural shape of the gap, and why a TTS vendor is the remediation."""
    from lab.voice.engines.deepgram_stt import MONOLINGUAL_ONLY_LANGUAGES

    assert CANTONESE in MONOLINGUAL_ONLY_LANGUAGES  # Deepgram can hear it
    assert CANTONESE not in SYNTHESISABLE_LANGUAGES  # nothing here can say it
    assert CANTONESE not in MULTI_LANGUAGES  # and it cannot be code-switched either


def test_every_market_carries_a_reason(capabilities: dict[str, Any]) -> None:
    """A verdict with no reason invites the reader to guess at the cause."""
    for row in coverage_table():
        assert len(row.reason) > 40
        assert row.regulator
        assert row.hub


# --------------------------------------------------------------------------- #
# The adapter's honesty rules
# --------------------------------------------------------------------------- #


def _trace_with(**session: Any):  # type: ignore[no-untyped-def]
    """A minimal one-turn audio trace, built through the real TraceBuilder."""
    builder = TraceBuilder(scenario_id="probe", adapter="voice:audio", session_id="s1")
    builder.session_start(latency_gate="PASS", latency_gate_detail="ok", **session)
    return builder


def test_delivered_latency_is_refused_when_the_harness_owned_the_voice() -> None:
    """Reference bug 2, enforced in code rather than described in a document."""
    builder = _trace_with(tts_owner=TTS_OWNER_HARNESS)
    builder.caller_utterance("hello", reference_source="spoken-form")
    builder.agent_audio_first_byte(turn=1)
    trace = builder.build()
    assert tts_owner(trace) == TTS_OWNER_HARNESS
    with pytest.raises(DeliveredLatencyUnavailable) as excinfo:
        audio_delivered_latency_report(trace)
    message = str(excinfo.value)
    assert "synthesised here" in message
    assert "tts_owner='sut'" in message
    assert "excludes network delivery" in message


def test_an_absent_tts_owner_is_treated_as_the_harness() -> None:
    """A safety check that defaults to 'permitted' is not a safety check."""
    builder = _trace_with()
    builder.caller_utterance("hello")
    trace = builder.build()
    assert tts_owner(trace) == TTS_OWNER_HARNESS
    with pytest.raises(DeliveredLatencyUnavailable):
        audio_delivered_latency_report(trace)


def test_delivered_latency_still_needs_the_calibration_gate() -> None:
    """A more important question does not make an unproven stopwatch trustworthy."""
    from lab.voice.adapter import LatencyUnproven

    builder = TraceBuilder(scenario_id="probe", adapter="voice:audio", session_id="s2")
    builder.session_start(tts_owner=TTS_OWNER_SUT, latency_gate="NOT_RUN")
    builder.caller_utterance("hello")
    builder.agent_audio_first_byte(turn=1)
    with pytest.raises(LatencyUnproven):
        audio_delivered_latency_report(builder.build())


def test_wer_is_refused_for_smart_formatted_transcripts() -> None:
    """The third refusal, beside provenance and the gate."""
    builder = _trace_with(tts_owner=TTS_OWNER_HARNESS)
    builder.caller_utterance("ring at seven thirty", reference_source="spoken-form")
    builder.transcript_in("Ring at 07:30.", provenance="engine", formatting="smart")
    trace = builder.build()
    assert transcript_formattings(trace) == {"smart": 1}
    with pytest.raises(WERUnproven) as excinfo:
        audio_wer_report(trace)
    assert "formatting policy" in str(excinfo.value)
    assert "smart_format=false" in str(excinfo.value)


def test_wer_is_allowed_for_raw_transcripts() -> None:
    builder = _trace_with()
    builder.caller_utterance("ring at seven thirty", reference_source="spoken-form")
    builder.transcript_in("ring at seven thirty", provenance="engine", formatting="raw")
    report = audio_wer_report(builder.build())
    assert report.micro_wer(normalised=True) == 0.0


def test_the_wer_reference_is_the_spoken_form_when_the_trace_carries_one() -> None:
    """The trace-level half of the normalisation fix, with the number that motivates it."""
    builder = _trace_with()
    builder.caller_utterance(
        "Ring at 7:30 from SW1A 1AA.",
        reference_source="spoken-form",
        spoken_text=SPOKEN_FORM,
    )
    builder.transcript_in(
        "ring at seven thirty from s w one a one a a", provenance="engine", formatting="raw"
    )
    trace = builder.build()
    assert wer_reference_sources(trace) == {"spoken-form": 1}
    assert audio_wer_report(trace).micro_wer(normalised=True) == 0.0


def test_scoring_the_same_turn_against_the_input_string_manufactures_error() -> None:
    """The counterfactual, so the fix is shown to matter rather than asserted to."""
    builder = _trace_with()
    # No spoken_text: the reference falls back to the written form.
    builder.caller_utterance("Ring at 7:30 from SW1A 1AA.", reference_source="caller-input")
    builder.transcript_in(
        "ring at seven thirty from s w one a one a a", provenance="engine", formatting="raw"
    )
    inflated = audio_wer_report(builder.build()).micro_wer(normalised=True)
    assert inflated is not None and inflated > 0.5, (
        "a perfect transcript must score badly against the written form, or this "
        "test is not exercising the trap it was written for"
    )


# --------------------------------------------------------------------------- #
# The metric that must never be called WER
# --------------------------------------------------------------------------- #


def test_the_intelligibility_probe_is_not_named_or_shaped_like_wer() -> None:
    builder = _trace_with()
    builder.caller_utterance("hello", reference_source="spoken-form")
    builder.transcript_in("hello", provenance="engine", formatting="raw")
    builder.transcript_out("Certainly, what time?")
    builder.agent_audio_complete(
        turn=1,
        loopback_text="certainly what time",
        loopback_engine="stt:deepgram/nova-3/en/raw",
        loopback_provenance="engine",
    )
    probe = tts_intelligibility_probe(builder.build())
    assert probe.metric == "tts_intelligibility_probe"
    assert "must not be reported as WER" in probe.caveat
    assert probe.turns == 1
    assert probe.error_rate == 0.0
    assert not hasattr(probe, "wer")
    # And the name travels with the number into any serialisation.
    assert json.loads(probe.model_dump_json())["metric"] == "tts_intelligibility_probe"


def test_the_agent_wer_report_ignores_the_loopback_entirely() -> None:
    """The two must not be able to contaminate each other."""
    builder = _trace_with()
    builder.caller_utterance("hello", reference_source="spoken-form")
    builder.transcript_in("hello", provenance="engine", formatting="raw")
    builder.transcript_out("Certainly.")
    builder.agent_audio_complete(
        turn=1,
        loopback_text="totally different words entirely",
        loopback_engine="x",
        loopback_provenance="engine",
    )
    report = audio_wer_report(builder.build())
    assert report.micro_wer(normalised=True) == 0.0, (
        "the loopback transcript leaked into the caller-side WER"
    )


def test_a_reference_provenance_loopback_is_excluded_from_the_probe() -> None:
    builder = _trace_with()
    builder.transcript_out("Certainly.")
    builder.agent_audio_complete(
        turn=1, loopback_text="certainly", loopback_provenance="reference"
    )
    probe = tts_intelligibility_probe(builder.build())
    assert probe.turns == 0
    assert probe.error_rate is None
    assert "no loopback turns" in probe.describe()


# --------------------------------------------------------------------------- #
# Silent corrections, with 100% attribution
# --------------------------------------------------------------------------- #


def test_every_silent_correction_is_attributed_because_we_synthesised_the_audio() -> None:
    """Reference bug 3. Production could attribute 31.3%; here it is all of them."""
    builder = _trace_with()
    builder.caller_utterance(
        "Ring at 7:30 from SW1A 1AA.", reference_source="spoken-form", spoken_text=SPOKEN_FORM
    )
    builder.transcript_in(
        "ring at seven thirty from s w one a one a b",  # last letter misheard
        provenance="engine",
        formatting="raw",
        confidence=0.91,
        lowest_confidence_word="b",
        lowest_word_confidence=0.44,
    )
    report = silent_correction_report(builder.build())
    assert report.turns_compared == 1
    assert report.unattributable == 0
    assert report.attributed_fraction == 1.0
    assert report.reference_source == "spoken-form"
    assert len(report.corrections) == 1
    correction = report.corrections[0]
    assert correction.kind == "substitution"
    assert (correction.spoken, correction.heard) == ("a", "b")
    assert correction.lowest_word == "b"
    assert correction.lowest_word_confidence == pytest.approx(0.44)
    assert "31.3%" in report.describe()


def test_the_correction_rate_carries_its_denominator() -> None:
    """A naked percentage is a defect in this repo."""
    builder = _trace_with()
    for index in range(4):
        builder.caller_utterance(f"word{index}", reference_source="spoken-form")
        builder.transcript_in(
            f"word{index}" if index else "wrong", provenance="engine", formatting="raw"
        )
    report = silent_correction_report(builder.build())
    assert report.turns_compared == 4
    assert report.per_hundred_turns == 25.0
    assert "over 4 turn(s)" in report.describe()


def test_deletions_and_insertions_are_distinguished_from_substitutions() -> None:
    builder = _trace_with()
    builder.caller_utterance("table for four people", reference_source="spoken-form")
    builder.transcript_in("table for people", provenance="engine", formatting="raw")
    builder.caller_utterance("table for four", reference_source="spoken-form")
    builder.transcript_in("table for four now", provenance="engine", formatting="raw")
    report = silent_correction_report(builder.build())
    kinds = {c.kind for c in report.corrections}
    assert kinds == {"deletion", "insertion"}
    deletion = next(c for c in report.corrections if c.kind == "deletion")
    assert deletion.spoken == "4"  # normalise() folds number words to digits
    insertion = next(c for c in report.corrections if c.kind == "insertion")
    assert insertion.heard == "now"


def test_reconciliation_is_refused_for_reference_transcripts() -> None:
    """Every comparison would be identical by construction — zero corrections, always."""
    builder = _trace_with()
    builder.caller_utterance("hello", reference_source="spoken-form")
    builder.transcript_in("hello", provenance="reference", formatting="raw")
    with pytest.raises(WERUnproven, match="identical by construction"):
        silent_correction_report(builder.build())


def test_reconciliation_is_refused_for_smart_formatted_transcripts() -> None:
    """It would attribute formatting policy as recognition defects — inventing bugs."""
    builder = _trace_with()
    builder.caller_utterance("ring at seven thirty", reference_source="spoken-form")
    builder.transcript_in("Ring at 07:30.", provenance="engine", formatting="smart")
    with pytest.raises(WERUnproven, match="inventing defects"):
        silent_correction_report(builder.build())


# --------------------------------------------------------------------------- #
# Read-back: field assertions, not WER
# --------------------------------------------------------------------------- #


def test_a_spelled_postcode_counts_as_captured() -> None:
    """The case WER gets exactly backwards: 100% word error, flawless capture."""
    builder = _trace_with()
    builder.caller_utterance("...", reference_source="spoken-form")
    builder.transcript_in(
        "my postcode is s w one a one a a", provenance="engine", formatting="raw"
    )
    report = readback_report(builder.build(), {"postcode": "SW1A 1AA"})
    assert report.captured_fraction == 1.0
    assert report.fields[0].captured is True
    assert report.fields[0].found_in_turn == 1


def test_a_spoken_time_counts_as_captured() -> None:
    builder = _trace_with()
    builder.transcript_in("half seven, so seven thirty", provenance="engine", formatting="raw")
    report = readback_report(builder.build(), {"time": "7:30"})
    assert report.fields[0].captured is True


def test_a_genuinely_misheard_field_is_reported_as_missed() -> None:
    builder = _trace_with()
    builder.transcript_in("my postcode is s w one a one a b", provenance="engine", formatting="raw")
    report = readback_report(builder.build(), {"postcode": "SW1A 1AA"})
    assert report.fields[0].captured is False
    assert report.captured_fraction == 0.0
    assert report.missed[0].field == "postcode"
    assert "MISSED" in report.fields[0].describe()
    # The transcript it was looked for in is attached, so the miss is debuggable.
    assert report.fields[0].heard is not None


def test_read_back_never_averages_a_right_field_with_a_wrong_one() -> None:
    """Per-field booleans, and a fraction that carries its denominator."""
    builder = _trace_with()
    builder.transcript_in(
        "name gupta postcode s w one a one a b", provenance="engine", formatting="raw"
    )
    report = readback_report(builder.build(), {"name": "Gupta", "postcode": "SW1A 1AA"})
    assert [f.captured for f in report.fields] == [True, False]
    assert report.captured_fraction == 0.5
    assert len(report.fields) == 2
    assert "1/2 field(s) captured" in report.describe()


def test_dates_of_birth_survive_the_number_word_fold() -> None:
    builder = _trace_with()
    builder.transcript_in(
        "born on the fourteenth of march nineteen eighty two",
        provenance="engine",
        formatting="raw",
    )
    report = readback_report(builder.build(), {"dob_day": "14th"})
    assert report.fields[0].captured is True


def test_an_empty_field_set_reports_no_fraction_rather_than_zero() -> None:
    builder = _trace_with()
    report = readback_report(builder.build(), {})
    assert report.captured_fraction is None
    assert "no fields declared" in report.describe()


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #


def test_both_engines_satisfy_their_protocols() -> None:
    from lab.voice.engines.base import STTEngine, TTSEngine

    assert isinstance(ElevenLabsTTS(), TTSEngine)
    assert isinstance(DeepgramSTT(), STTEngine)


def test_both_engines_describe_themselves_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`describe()` must be safe to print on a machine with no credentials."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("LAB_LIVE_TTS", raising=False)
    monkeypatch.delenv("LAB_LIVE_STT", raising=False)
    tts = ElevenLabsTTS().describe()
    stt = DeepgramSTT().describe()
    assert "unset" in tts and "unset" in stt
    assert "LAB_LIVE_TTS" in tts and "LAB_LIVE_STT" in stt


def test_no_engine_ever_reports_a_key_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """The repo documents variable names. Values must not reach a string anywhere."""
    secret = "sk-do-not-leak-me-0123456789"
    monkeypatch.setenv("ELEVENLABS_API_KEY", secret)
    monkeypatch.setenv("DEEPGRAM_API_KEY", secret)
    monkeypatch.setenv("LAB_LIVE_TTS", "1")
    monkeypatch.setenv("LAB_LIVE_STT", "1")
    tts = ElevenLabsTTS()
    stt = DeepgramSTT()
    for text in (tts.describe(), repr(tts), tts.name, stt.describe(), repr(stt), stt.name):
        assert secret not in text
    assert tts.key_present() is True
    assert stt.key_present() is True


def test_the_synthesis_result_never_confuses_unknown_with_identical() -> None:
    """`spoken_text=None` means 'nobody told us', not 'the same as text'."""
    result = SynthesisResult(
        audio=np.zeros(160, dtype=np.float64),
        sample_rate=DEFAULT_SAMPLE_RATE,
        engine="tts:probe",
        text="7:30",
    )
    assert result.spoken_text is None
    assert result.reference_source == "caller-input"
    assert result.wer_reference == "7:30"


# --------------------------------------------------------------------------- #
# The four defects the live run found, and the guards that now catch them
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def evidence() -> dict[str, Any]:
    """The committed round trip: 17 lines through both real engines."""
    return json.loads((CLOUD / "round_trip_evidence.json").read_text(encoding="utf-8"))


def _row(evidence: dict[str, Any], row_id: str) -> dict[str, Any]:
    for row in evidence["rows"]:
        if row["row_id"] == row_id:
            return row
    raise AssertionError(f"no evidence row {row_id!r}")


# ---- 1. detect_language combined with language returns nothing ---------------


def test_detect_language_cannot_be_combined_with_a_language() -> None:
    """Measured to return an empty transcript at confidence 0.0, with no error."""
    from lab.voice.engines.deepgram_stt import LanguageOptionConflict

    with pytest.raises(LanguageOptionConflict) as excinfo:
        DeepgramSTT(language="multi", detect_language=True)
    message = str(excinfo.value)
    assert "EMPTY" in message
    assert "confidence 0.0" in message
    assert "for_language" in message  # the remedy names the factory


def test_detection_is_expressible_by_leaving_the_language_unset() -> None:
    """The guard forbids the broken combination, not the capability."""
    engine = DeepgramSTT(language=None, detect_language=True)
    assert engine.language is None
    assert "detect" in engine.name
    params = engine._params(smart=False)
    assert params["detect_language"] == "true"
    assert "language" not in params


def test_a_pinned_language_is_sent_and_detection_is_not() -> None:
    """Exactly one of the two ever reaches the API."""
    params = DeepgramSTT(language="ja")._params(smart=False)
    assert params["language"] == "ja"
    assert "detect_language" not in params


def test_every_committed_row_transcribed_to_something() -> None:
    """The regression test for the conflict: empty transcripts were the symptom."""
    for row in json.loads(
        (CLOUD / "round_trip_evidence.json").read_text(encoding="utf-8")
    )["rows"]:
        assert row["heard_raw_scored"].strip(), (
            f"{row['row_id']} transcribed to nothing — the detect_language conflict "
            "produced exactly this, on rows where the audio was fine"
        )
        assert (row["confidence"] or 0) > 0.5, row["row_id"]


# ---- 2. the CJK romanisation inversion --------------------------------------


@pytest.mark.parametrize(
    ("sent", "spoken", "romanised"),
    [
        ("資産配分を見直したいです。", "Zi Chan Pei Fen woJian Zhi shitaidesu.", True),
        ("我想检视我的投资组合。", "Wo Xiang Jian Shi Wo De Tou Zi Zu He .", True),
        ("資産配分を見直したいです。", "資産配分を見直したいです。", False),
        # Arabic and Devanagari come back in their own scripts: unaffected.
        ("أريد مراجعة محفظتي.", "أريد مراجعة محفظتي.", False),
        ("मुझे अपना पोर्टफोलियो", "मुझे अपना पोर्टफोलियो", False),
        # The ordinary Latin case, which is the whole point of the spoken form.
        ("Ring at 7:30", "Ring at seven thirty", False),
    ],
)
def test_the_romanisation_guard_fires_only_on_cjk(sent: str, spoken: str, romanised: bool) -> None:
    from lab.voice.engines.elevenlabs_tts import _is_romanised

    assert _is_romanised(sent, spoken) is romanised


def test_a_romanised_spoken_form_is_declined_live(evidence: dict[str, Any]) -> None:
    """The two CJK rows fall back to caller-input, and that is the *correct* answer."""
    for row_id in ("switch-ja", "mono-zh-singapore"):
        row = _row(evidence, row_id)
        assert row["spoken_text"] is None, (
            f"{row_id} published a romanised spoken form; scoring against it reported "
            "100% error on audio that was nearly perfect"
        )
        assert row["reference_source"] == "caller-input"
        # And the figure that results is a real one rather than a fabricated total loss.
        cell = row["wer_matrix"]["caller-input/raw"]
        assert cell["normalised_wer"] < 0.5, row_id


def test_the_japanese_row_is_perfect_once_the_guard_applies(evidence: dict[str, Any]) -> None:
    """0.000 where the unguarded reference would have said 1.000."""
    row = _row(evidence, "switch-ja")
    assert row["wer_matrix"]["caller-input/raw"]["normalised_wer"] == 0.0
    assert row["wer_matrix"]["spoken-form/raw"] is None  # no spoken form was published
    assert (row["confidence"] or 0) > 0.99


def test_the_romanisation_guard_also_applies_to_a_cached_sidecar(tmp_path: Path) -> None:
    """A guard that only runs on a cache miss stops working when the cache warms."""
    cache = _tmp_cache(tmp_path)
    japanese = "資産配分を見直したいです。"
    engine = ElevenLabsTTS(
        client=_FakeElevenLabs(spoken="Zi Chan Pei Fen woJian Zhi shitaidesu."), cache=cache
    )
    live = engine.synthesise(japanese)
    assert live.spoken_text is None

    # Now force a sidecar that *does* carry the romanised form, as an older
    # checkout would have written, and prove the read path still refuses it.
    key = clip_cache_key(
        text=japanese,
        voice=DEFAULT_CALLER_VOICE,
        model="eleven_flash_v2_5",
        output_format="pcm_16000",
        normalisation="on",
    )
    cache.put(
        key,
        np.zeros(1600, dtype=np.float64),
        DEFAULT_SAMPLE_RATE,
        {"spoken_text": "Zi Chan Pei Fen woJian Zhi shitaidesu."},
    )
    replayed = ElevenLabsTTS(
        client=_FakeElevenLabs(),
        cache=ClipCache(committed=tmp_path / "committed", scratch=tmp_path / "scratch"),
    ).synthesise(japanese)
    assert replayed.replayed is True
    assert replayed.spoken_text is None
    assert replayed.reference_source == "caller-input"


# ---- 3. the ASCII-only normaliser -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "मुझे अपना पोर्टफोलियो देखना है।",
        "資産配分を見直したいです。",
        "我想检视我的投资组合。",
        "أريد مراجعة محفظتي قبل نهاية العام.",
        "Necesito revisar mi pensión antes de fin de año.",
        "Je voudrais revoir mon portefeuille avant la fin de l'année.",
    ],
)
def test_no_script_normalises_to_nothing(text: str) -> None:
    """It used to. Non-Latin scripts became "", which makes WER undefined."""
    from lab.voice.wer import normalise

    assert normalise(text).strip(), f"{text!r} normalised to the empty string"


@pytest.mark.parametrize(
    "text",
    [
        "Necesito revisar mi pensión antes de fin de año.",
        "Je voudrais revoir mon portefeuille avant la fin de l'année.",
        "मुझे अपना पोर्टफोलियो देखना है।",
        "أريد مراجعة محفظتي قبل نهاية العام.",
        "資産配分を見直したいです。",
    ],
)
def test_a_perfect_transcript_scores_zero_in_every_script(text: str) -> None:
    """The silent half of the bug: accented and combining characters were deleted.

    "pensión" became "pensi n" — one word split into two tokens — so every accented
    word in a Spanish or French row scored a substitution plus an insertion.
    Nothing raised. The es/fr word error rates were simply wrong, in proportion to
    how many accents the sentence happened to contain.
    """
    from lab.voice.wer import wer

    assert wer(text, text).normalised.wer == 0.0


def test_accents_and_matras_survive_normalisation() -> None:
    from lab.voice.wer import normalise

    assert "pensión" in normalise("Necesito revisar mi pensión")
    assert "año" in normalise("antes de fin de año")
    assert "मुझे" in normalise("मुझे अपना पोर्टफोलियो")


def test_spaceless_scripts_are_segmented_and_labelled() -> None:
    """A Japanese figure is a character error rate, and must not be called a word one."""
    from lab.voice.wer import normalise, scoring_unit

    assert scoring_unit("資産配分を見直したいです。") == "character"
    assert scoring_unit("我想检视我的投资组合。") == "character"
    assert scoring_unit("Ring at 7:30") == "word"
    assert scoring_unit("मुझे अपना") == "word"  # Devanagari does use spaces
    # Segmented, so the metric has more than two possible values.
    assert len(normalise("資産配分を見直したいです。").split()) == 12


def test_the_mandarin_row_scores_its_real_error_not_a_total_loss(
    evidence: dict[str, Any],
) -> None:
    """Two characters out of ten, from a genuine homophone. Not 100%."""
    row = _row(evidence, "mono-zh-singapore")
    cell = row["wer_matrix"]["caller-input/raw"]
    assert cell["reference_words"] == 10  # segmented per character
    assert 0.0 < cell["normalised_wer"] < 0.5


# ---- 4. digit-by-digit readouts --------------------------------------------


def test_spoken_digit_runs_collapse_to_the_number_they_spell() -> None:
    from lab.voice.adapter import _collapse_digit_runs

    heard = "account number four zero seven one nine nine two eight"
    assert _collapse_digit_runs(heard) == "account number 40719928"
    assert _collapse_digit_runs("Account number 4071 9928.") == "account number 40719928"


def test_the_collapse_bypasses_the_cardinal_parser_that_corrupts_readouts() -> None:
    """`normalise` turns a digit readout into "4 8 9 11 8". This must not use it."""
    from lab.voice.adapter import _collapse_digit_runs
    from lab.voice.wer import normalise

    heard = "account number four zero seven one nine nine two eight"
    assert normalise(heard) == "account number 4 8 9 11 8"  # the corruption, pinned
    assert "40719928" in _collapse_digit_runs(heard)


def test_a_hyphenated_digit_run_still_collapses() -> None:
    """Deepgram returned one hyphenated token even with punctuate=false."""
    from lab.voice.adapter import _collapse_digit_runs

    heard = "account number four-zero-seven-one-nine-nine-two-eight"
    assert _collapse_digit_runs(heard) == "account number 40719928"


def test_the_collapse_does_not_fuse_a_spoken_time() -> None:
    """The safety property, and it holds for a reason worth pinning.

    The collapse maps **digit names only** — "seven" is one, "thirty" is not — so a
    spoken time survives as two tokens rather than being fused into "730". That is
    why the transform is safe to apply on the field path.

    It is also why it must not be folded into `lab.voice.wer.normalise`: there,
    number-word conversion runs first and turns "thirty" into "30", so a
    subsequent digit collapse *would* see two adjacent numerics and join them.
    The order of operations is what makes one location safe and the other not.
    """
    from lab.voice.adapter import _collapse_digit_runs
    from lab.voice.wer import normalise

    assert normalise("meet at seven thirty") == "meet at 7 30"
    assert _collapse_digit_runs("meet at seven thirty") == "meet at 7 thirty"


def test_account_and_sort_code_capture_on_the_committed_transcripts(
    evidence: dict[str, Any],
) -> None:
    """These reported False before the collapse existed, on perfect transcripts."""
    for row_id, field in (
        ("readback-account-number", "account"),
        ("readback-sort-code", "sort_code"),
        ("readback-postcode", "postcode"),
        ("readback-dob", "dob_day"),
    ):
        row = _row(evidence, row_id)
        assert row["field_capture"][field] is True, f"{row_id}/{field}"


def test_the_generator_and_the_library_agree_on_field_capture(
    evidence: dict[str, Any],
) -> None:
    """A generator that scores differently produces evidence about itself.

    The committed `field_capture` values are re-derived here through the library's
    own `readback_report`, so the two can never drift apart again.
    """
    from lab.trace.build import TraceBuilder

    for row in evidence["rows"]:
        if not row["field_capture"]:
            continue
        builder = TraceBuilder(scenario_id=row["row_id"], adapter="voice:audio")
        builder.session_start(latency_gate="PASS")
        builder.transcript_in(row["heard_raw_scored"], provenance="engine", formatting="raw")
        expected = {
            name: _EXPECTED_VALUES[row["row_id"]][name] for name in row["field_capture"]
        }
        report = readback_report(builder.build(), expected)
        for field in report.fields:
            assert field.captured is row["field_capture"][field.field], (
                f"{row['row_id']}/{field.field}: library says {field.captured}, "
                f"committed evidence says {row['field_capture'][field.field]}"
            )


#: The declared values behind the committed `field_capture` block, mirrored from
#: `scripts/make_cloud_fixtures.py`. Kept here rather than imported so that a test
#: comparing the generator to the library does not read one of them twice.
_EXPECTED_VALUES: dict[str, dict[str, str]] = {
    "readback-name-spelled": {"surname": "Gupta"},
    "readback-dob": {"dob_day": "14th", "dob_year": "1982"},
    "readback-postcode": {"postcode": "SW1A 1AA"},
    "readback-account-number": {"account": "4071"},
    "readback-irish-name": {"surname": "Rourke"},
    "readback-sort-code": {"sort_code": "204577"},
}


# ---- what the whole corpus proves -------------------------------------------


def test_the_reference_choice_is_worth_up_to_140_points_of_apparent_error(
    evidence: dict[str, Any],
) -> None:
    """The headline, from the committed live round trip rather than from prose."""
    postcode = _row(evidence, "readback-postcode")
    assert postcode["wer_matrix"]["spoken-form/raw"]["normalised_wer"] == 0.0
    inflated = postcode["wer_matrix"]["caller-input/raw"]["normalised_wer"]
    assert inflated > 1.0, (
        "a flawless postcode capture must score catastrophically against the written "
        "form, or the suite's central claim is not demonstrated by its own evidence"
    )
    assert postcode["field_capture"]["postcode"] is True, (
        "and the field-level instrument must say it was captured — that divergence "
        "between WER and field capture is the entire argument for field assertions"
    )


def test_smart_formatting_inflates_error_on_the_date_row(evidence: dict[str, Any]) -> None:
    row = _row(evidence, "readback-dob")
    assert row["wer_matrix"]["spoken-form/raw"]["normalised_wer"] == 0.0
    assert row["wer_matrix"]["spoken-form/smart"]["normalised_wer"] > 0.4
    # And the prettified string is the one that rewrote it.
    assert row["heard_smart_display_unscored"] != row["heard_raw_scored"]


def test_a_genuine_read_back_failure_is_present_and_localised(
    evidence: dict[str, Any],
) -> None:
    """Not every row passes, and the one that fails is a real defect.

    "Siobhan O'Rourke" was heard as "siobono rock". The field assertion says
    missed, and the per-word confidences localise it: the two name tokens came back
    around 0.72-0.75 while every other row sits above 0.99. That is what word-level
    confidence is *for* — an utterance-level number would have shown 0.93 and
    pointed at nothing.
    """
    row = _row(evidence, "readback-irish-name")
    assert row["field_capture"]["surname"] is False
    worst = row["lowest_confidence_words"][0]
    assert worst["confidence"] < 0.8
    assert (row["confidence"] or 0) > 0.9, (
        "the utterance-level confidence stayed high while a name was lost, which is "
        "why the per-word figures are recorded"
    )


def test_the_committed_corpus_covers_every_group(evidence: dict[str, Any]) -> None:
    groups = {row["group"] for row in evidence["rows"]}
    assert groups == {
        "read-back",
        "advisory",
        "code-switched",
        "monolingual",
        "reference-trap",
        "agent-voice",
    }


def test_the_ledger_records_what_the_corpus_cost(evidence: dict[str, Any]) -> None:
    """And the committed run cost nothing, because every clip was already cached."""
    ledger = evidence["ledger"]
    assert ledger["corpus_characters"] > 0
    assert ledger["corpus_credits_if_uncached"] > 0
    assert ledger["characters_spent_this_run"] == 0, (
        "the committed evidence should be the product of a fully cached re-run, "
        "which is the proof that re-running the paid suite is free"
    )


def test_the_v3_row_declines_a_spoken_form_on_a_live_response(
    evidence: dict[str, Any],
) -> None:
    row = _row(evidence, "trap-v3-no-spoken-form")
    assert row["model"] == "eleven_v3"
    assert row["spoken_text"] is None
    assert row["reference_source"] == "caller-input"
    assert row["wer_matrix"]["spoken-form/raw"] is None
