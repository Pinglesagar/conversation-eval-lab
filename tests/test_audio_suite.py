"""The audio tier, run offline: eighteen declared rows against committed fixtures.

Every test here runs on a fresh clone with **every credential unset**. That is
the repo's cardinal rule, and for this tier it is also the only way the tests are
worth having: the clips are committed, the transcripts are a cassette keyed by
audio digest, and the perturbations are seeded, so there is no legitimate source
of variation left and nothing needs a network.

THE TESTS ITERATE THE CORPUS RATHER THAN RESTATING IT
-----------------------------------------------------
No expected postcode, threshold or budget is written in this file. Every one of
them is declared in `scenarios/audio/*.yaml` and read from there. That is
deliberate and it is the difference between a corpus and a folder of files: a
nineteenth row needs a YAML file and no code, and a row's assertion cannot drift
away from the row's stated purpose, because there is only one copy of it.

What this file does contain is the *properties the tier as a whole* must have —
that every category is populated, that a blocked row is never counted as a pass,
that an untestable row is never counted as either, and that the vocabulary and
the vendor capability sets have not drifted from the modules they were copied
from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.voice.engines.clipcache import ClipCache, clip_cache_key
from lab.voice.engines.stt import RecordedSTT, TranscriptCassette
from lab.voice.suite import (
    AUDIO_SUITE_CASSETTE,
    CLIPS,
    LADDERS,
    assemble_audio,
    clip_for,
    corpus_cost,
    parse_magnitude,
    run_row,
    run_tier,
    tier_summary,
)
from scenarios.audio import tier, validate_tier
from scenarios.loader import (
    AUDIO_TAG_VOCABULARY,
    AUDIO_TIER,
    AUDIO_TIER_MINIMUM,
    CODE_SWITCHABLE_LANGUAGE_IDS,
    SYNTHESISABLE_LANGUAGE_IDS,
    Scenario,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "audio" / "cloud"
CASSETTE_PATH = FIXTURES / AUDIO_SUITE_CASSETTE

#: The tier's six categories. Every row carries exactly one, which is what makes
#: the category table countable rather than editorial.
CATEGORIES = {
    "digits-and-names",
    "line-quality",
    "barge-in",
    "silence",
    "multilingual",
    "untestable",
}


@pytest.fixture(scope="module")
def rows() -> list[Scenario]:
    return tier()


@pytest.fixture(scope="module")
def cassette() -> TranscriptCassette:
    if not CASSETTE_PATH.is_file():
        pytest.skip(
            f"no committed cassette at {CASSETTE_PATH}; run "
            "`python -m scripts.make_audio_suite_fixtures` with the live flags"
        )
    return TranscriptCassette.load(CASSETTE_PATH)


@pytest.fixture(scope="module")
def results(rows: list[Scenario], cassette: TranscriptCassette):
    return run_tier(rows, cache=ClipCache(), stt=RecordedSTT(cassette))


# --------------------------------------------------------------------------- #
# The corpus itself
# --------------------------------------------------------------------------- #


def test_every_row_loads_and_validates() -> None:
    """The whole tier parses, and nothing in it is merely a warning either."""
    validation = validate_tier()
    assert not validation.issues, "\n".join(str(i) for i in validation.issues)


def test_the_tier_meets_its_own_minimum(rows: list[Scenario]) -> None:
    assert len(rows) >= AUDIO_TIER_MINIMUM


def test_the_tier_is_not_in_the_default_corpus() -> None:
    """The measurement decision that keeps audio results out of text denominators."""
    from scenarios.loader import SUITES, load_corpus

    assert AUDIO_TIER not in SUITES
    default = load_corpus()
    assert not [s for s in default.scenarios if s.suite == AUDIO_TIER]


def test_the_tier_is_exactly_the_top_level_files(rows: list[Scenario]) -> None:
    """Sub-directories of `scenarios/audio/` are separate tiers, not more of this one.

    `iter_scenario_paths` globs `*.yaml` non-recursively, so a sub-directory —
    `scenarios/audio/transport/`, for instance — is invisible here and is loaded
    by whatever owns it. That is the right behaviour: a transport row measures a
    receiver-side quantity that does not exist in an in-process run, its results
    share no denominator with these, and its schema is not this `Scenario` model.
    Asserted rather than assumed, because if that glob ever becomes recursive this
    tier would silently absorb another one's rows and every count in `§10`, every
    category assertion and the pass-rate denominator would move without a single
    file in this directory changing.
    """
    directory = (
        Path(__file__).resolve().parents[1] / "scenarios" / AUDIO_TIER
    )
    top_level = {path.stem for path in directory.glob("*.yaml")}
    assert {scenario.id for scenario in rows} == top_level
    for scenario in rows:
        assert Path(scenario.source).parent == directory, scenario.source


def test_every_row_carries_exactly_one_category(rows: list[Scenario]) -> None:
    for scenario in rows:
        found = set(scenario.tags) & CATEGORIES
        assert len(found) == 1, f"{scenario.id}: category tags {sorted(found)}"


def test_every_category_is_populated(rows: list[Scenario]) -> None:
    """A category with no rows is an aspiration presented as coverage."""
    used = {tag for scenario in rows for tag in scenario.tags if tag in CATEGORIES}
    assert used == CATEGORIES, f"unpopulated: {sorted(CATEGORIES - used)}"


def test_the_audio_vocabulary_has_no_unused_and_no_undefined_tags(
    rows: list[Scenario],
) -> None:
    """Both directions, which is the point.

    Undefined tags the loader already rejects. Unused ones it cannot see, and an
    unused tag in a closed vocabulary reads as coverage to anyone counting tags —
    which is how this dict came to offer three categories and four caller-voice
    locales that no row could ever have.
    """
    used = {tag for scenario in rows for tag in scenario.tags}
    defined = set(AUDIO_TAG_VOCABULARY)
    assert used <= defined, f"undefined: {sorted(used - defined)}"
    assert defined <= used, f"defined but unused: {sorted(defined - used)}"


def test_the_five_reference_bugs_each_have_a_row(rows: list[Scenario]) -> None:
    """The suite exists to catch these, so each one must map to a row."""
    by_id = {scenario.id: scenario for scenario in rows}
    # 1 silence misattribution, 5 interruption metrics; 3 and 4 are the capture
    # group. Reference bug 2 — agent-side latency excluding delivery — is
    # deliberately absent from this tier and refused in the adapter instead: an
    # in-process run has no delivery leg to measure, so a row here could only
    # ever report the number the dashboard already gets wrong.
    assert "audio-silence-boundary-misattributed" in by_id
    assert by_id["audio-silence-boundary-misattributed"].audio.silence.expect_verdict == (
        "vad_false_silence"
    )
    assert "audio-barge-in-agent-yields" in by_id
    assert "audio-barge-in-not-discovered" in by_id
    capture_rows = [s for s in rows if "digits-and-names" in s.tags]
    assert len(capture_rows) == 5


# --------------------------------------------------------------------------- #
# Vendor capability facts: duplicated constants must not drift
# --------------------------------------------------------------------------- #


def test_loader_language_sets_match_the_engine_modules() -> None:
    """The corpus cannot import numpy, so these are copies. Copies drift."""
    from lab.voice.engines.coverage import SYNTHESISABLE_LANGUAGES
    from lab.voice.engines.deepgram_stt import MULTI_LANGUAGES

    assert SYNTHESISABLE_LANGUAGE_IDS == set(SYNTHESISABLE_LANGUAGES)
    assert CODE_SWITCHABLE_LANGUAGE_IDS == set(MULTI_LANGUAGES)


def test_cantonese_is_still_untestable_and_the_row_still_says_so(
    rows: list[Scenario],
) -> None:
    """The refusal expires by itself when the vendor ships Cantonese.

    If `yue` ever enters the synthesisable set, `UntestableDeclaration` refuses to
    validate and `test_every_row_loads_and_validates` goes red. This test states
    the same thing from the other side so the failure is legible rather than a
    validation error somebody has to decode.
    """
    assert "yue" not in SYNTHESISABLE_LANGUAGE_IDS
    assert "zh-HK" not in SYNTHESISABLE_LANGUAGE_IDS
    refusals = [s for s in rows if s.audio and s.audio.untestable]
    assert len(refusals) == 1
    declaration = refusals[0].audio.untestable
    assert declaration.language == "yue"
    assert declaration.remediation, "a gap with no remediation is a complaint"
    remediation = " ".join(declaration.remediation).lower()
    assert "azure" in remediation and "google" in remediation


def test_a_code_switching_row_cannot_claim_a_pair_the_recogniser_will_not_follow(
    rows: list[Scenario],
) -> None:
    """The loader's guard, asserted on the real corpus.

    A row switching into a language outside the recogniser's ten must declare
    `expect_capture: false`. Otherwise its red cell reads as a product defect and
    somebody is assigned a vendor limitation to fix.
    """
    for scenario in rows:
        spec = scenario.audio
        if spec is None or spec.capture is None or len(spec.languages) < 2:
            continue
        outside = set(spec.languages) - CODE_SWITCHABLE_LANGUAGE_IDS
        if outside:
            assert not spec.capture.expect_capture, (
                f"{scenario.id} switches into {sorted(outside)} and expects capture"
            )


def test_the_constructed_row_is_labelled_as_constructed(rows: list[Scenario]) -> None:
    """A spliced utterance presented as a native one would be the worst artefact here."""
    constructed = [s for s in rows if s.audio and s.audio.is_constructed()]
    assert len(constructed) == 1
    scenario = constructed[0]
    assert "constructed" in scenario.tags
    assert len(scenario.audio.clauses) == 2
    assert "CONSTRUCTED BY CONCATENATION" in scenario.notes


# --------------------------------------------------------------------------- #
# Clips, cost and the reuse claim
# --------------------------------------------------------------------------- #


def test_every_clip_a_row_names_is_in_the_registry(rows: list[Scenario]) -> None:
    for scenario in rows:
        for clip_id in (scenario.audio.clip_ids() if scenario.audio else []):
            assert clip_for(clip_id).id == clip_id


def test_every_registry_clip_is_committed() -> None:
    """A fresh clone must be able to run the tier, so the clips ship with it."""
    cache = ClipCache()
    missing = [
        clip.id
        for clip in CLIPS.values()
        if cache.get(
            clip_cache_key(
                text=clip.text,
                voice=clip.voice,
                model=clip.model,
                output_format="pcm_16000",
            )
        )
        is None
    ]
    assert not missing, f"clips absent from the committed cache: {missing}"


def test_the_reuse_claim_is_arithmetic_not_rhetoric() -> None:
    """Half the clips are reused, and that is what keeps the tier inside budget."""
    cost = corpus_cost()
    assert cost["clips_reused"] >= 7
    assert cost["credits_new"] < cost["credits_if_nothing_reused"]
    # The binding constraint: the free ElevenLabs allowance does not renew until
    # the monthly reset, and this tier had to fit in what was left of it.
    assert cost["characters_new"] < 1_000


def test_the_phonetic_row_has_no_word_error_rate_available() -> None:
    """`eleven_flash_v2` is the only SSML-phoneme model and is not a spoken-form model.

    Asserted because it is a real constraint that looks like an oversight. The one
    row that can plant a mispronunciation is the one row with no WER reference —
    and it costs nothing, because that row asserts a field and WER would have been
    the wrong instrument for it anyway.
    """
    forced = clip_for("confusable-forced")
    assert forced.model == "eleven_flash_v2"
    assert not forced.has_spoken_form
    assert clip_for("confusable-plain").has_spoken_form


# --------------------------------------------------------------------------- #
# The magnitude parser, where lakh is the whole point
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The premium is four thousand two hundred and fifty pounds.", 4250.0),
        ("the premium is 4250 pounds", 4250.0),
        ("the premium is 4,250 pounds", 4250.0),
        ("fifteen lakh", 1_500_000.0),
        ("15 lakh", 1_500_000.0),
        ("two crore", 20_000_000.0),
        ("no number here at all", None),
    ],
)
def test_parse_magnitude(text: str, expected: float | None) -> None:
    """Lakh and crore are not powers of a thousand, which is why this exists.

    A parser that knows only thousand and million reads "fifteen lakh" correctly
    and returns 15 — a silent five-order-of-magnitude error in a portfolio value,
    and one that looks entirely plausible on a screen.
    """
    found = parse_magnitude(text)
    if expected is None:
        assert found is None
    else:
        assert found == pytest.approx(expected)


def test_a_transcript_with_no_number_is_none_and_not_zero() -> None:
    """Zero is a value. Absence is not, and conflating them hides a dropped field."""
    assert parse_magnitude("") is None


# --------------------------------------------------------------------------- #
# Running the tier
# --------------------------------------------------------------------------- #


def test_the_whole_tier_runs_offline(results) -> None:
    assert len(results) == 18


def test_a_blocked_row_is_never_a_pass_and_never_a_failure(results) -> None:
    """The honest half of reference bug 5."""
    blocked = [r for r in results if r.status == "blocked"]
    assert len(blocked) == 1
    assert blocked[0].scenario_id == "audio-barge-in-not-discovered"
    assert blocked[0].passed is None, "a row that never ran has no verdict"
    assert "blocked on trace event kinds nothing discovers" in blocked[0].note


def test_an_untestable_row_is_counted_in_its_own_column(results) -> None:
    """Hong Kong is neither a pass nor a failure, and both would mislead."""
    untestable = [r for r in results if r.status == "untestable"]
    assert len(untestable) == 1
    row = untestable[0]
    assert row.scenario_id == "audio-hk-cantonese-untestable"
    assert row.passed is None
    assert row.untestable_language == "yue"
    assert row.remediation


def test_the_summary_never_prints_a_naked_percentage(results) -> None:
    """A bare percentage is a defect in this repo, and this tier is why.

    Sixteen runnable rows, one blocked and one untestable: "94%" is available and
    wrong three different ways depending on which denominator it silently used.
    """
    summary = tier_summary(results)
    assert summary["rows"] == 18
    assert summary["runnable"] == 16
    assert len(summary["blocked"]) == 1
    assert len(summary["untestable"]) == 1
    assert "runnable" in summary["pass_rate"]
    assert summary["passed"] + summary["failed"] == summary["runnable"]


def test_the_three_silence_rows_resolve_the_threshold(rows, cassette) -> None:
    """5.9 does not fire, 6.1 does, and a talking caller is not called silent.

    The pair matters more than either half. A detector that never fires passes
    the under-threshold row alone; one that always fires passes the over-threshold
    row alone. Only both together assert the threshold is where it is claimed.
    """
    cache = ClipCache()
    stt = RecordedSTT(cassette)
    by_id = {s.id: s for s in rows if "silence" in s.tags}
    assert len(by_id) == 3

    under = run_row(by_id["audio-silence-under-threshold"], cache=cache, stt=stt)
    over = run_row(by_id["audio-silence-over-threshold"], cache=cache, stt=stt)
    wrong = run_row(by_id["audio-silence-boundary-misattributed"], cache=cache, stt=stt)

    assert under.silence.fires is False
    assert over.silence.fires is True
    assert over.silence.reason_is_accurate is True

    # The reference bug: the timeout fires and the label is a lie.
    assert wrong.silence.fires is True
    assert wrong.silence.verdict == "vad_false_silence"
    assert wrong.silence.reason_is_accurate is False
    assert wrong.silence.measured_silence_s < wrong.silence.threshold_s

    for result in (under, over, wrong):
        # The instrument checking itself. If the declared pause is not the pause
        # the envelope measured, the verdict is about padding arithmetic.
        assert result.silence.declared_matches_measured, result.silence
        assert result.passed, result.silence


def test_the_two_silence_verdicts_recommend_opposite_remedies(rows, cassette) -> None:
    """One production label, two situations, and the fix for one makes the other worse."""
    cache = ClipCache()
    stt = RecordedSTT(cassette)
    by_id = {s.id: s for s in rows}
    genuine = run_row(by_id["audio-silence-over-threshold"], cache=cache, stt=stt)
    false = run_row(by_id["audio-silence-boundary-misattributed"], cache=cache, stt=stt)
    assert "raise the timeout" in genuine.silence.description
    assert "fix turn detection" in false.silence.description
    assert genuine.silence.verdict != false.silence.verdict


def test_the_barge_in_row_measures_a_yield_from_real_clip_durations(rows, cassette) -> None:
    cache = ClipCache()
    result = run_row(
        next(s for s in rows if s.id == "audio-barge-in-agent-yields"),
        cache=cache,
        stt=RecordedSTT(cassette),
    )
    outcome = result.barge_in
    assert outcome.yielded is True
    assert outcome.yield_ms is not None
    assert outcome.overlap_s > 0.0
    # The agent's clip length is measured, not declared, so the overlap cannot be
    # a number chosen to make the row pass.
    assert outcome.agent_duration_s > 1.2
    assert outcome.within_budget is True
    assert result.passed


def test_the_line_quality_rows_report_a_breaking_point_not_a_boolean(
    rows, cassette
) -> None:
    """"Fails at 6 dB" is not actionable. "Holds to 10, breaks at 5" is a margin."""
    cache = ClipCache()
    stt = RecordedSTT(cassette)
    ladder_rows = [s for s in rows if "line-quality" in s.tags]
    assert len(ladder_rows) == 3
    for scenario in ladder_rows:
        result = run_row(scenario, cache=cache, stt=stt)
        assert result.ladder is not None, scenario.id
        assert result.ladder.axis in LADDERS
        assert len(result.ladder.captured) == len(result.ladder.rungs)
        # Every rung must have a committed transcript. A missing rung would
        # otherwise be scored as a capture failure and manufacture a breaking
        # point out of an unrecorded fixture.
        assert not result.ladder.missing_rungs, result.ladder.describe()


def test_the_control_clip_isolates_the_planted_mispronunciation(rows, cassette) -> None:
    """Two clips, one variable, and the variable is the thing the row claims to test.

    Without the control, "we forced a mispronunciation and capture failed" is
    consistent with the recogniser having failed on that name anyway.
    """
    cache = ClipCache()
    result = run_row(
        next(s for s in rows if s.id == "audio-capture-confusable-names"),
        cache=cache,
        stt=RecordedSTT(cassette),
    )
    assert result.control is not None, "the row declares a control clip"
    assert result.capture is not None


def test_every_runnable_row_observed_what_it_declared(results) -> None:
    """The tier's headline verdict, with the failures named rather than counted."""
    failures = [
        f"{r.scenario_id}: {r.note}"
        for r in results
        if r.status == "runnable" and not r.passed
    ]
    assert not failures, "\n".join(failures)


# --------------------------------------------------------------------------- #
# The committed evidence
# --------------------------------------------------------------------------- #


def test_the_committed_evidence_matches_a_fresh_offline_run(results) -> None:
    """A fixture that has drifted from the code is a failing test, not a stale file."""
    path = FIXTURES / "audio_suite_evidence.json"
    if not path.is_file():
        pytest.skip("no committed evidence yet")
    committed = json.loads(path.read_text(encoding="utf-8"))
    fresh = [row.model_dump(mode="json") for row in results]
    assert committed["rows"] == fresh


def test_the_cassette_records_only_real_recognition(cassette) -> None:
    """No reference stand-ins in this tier: every transcript came from the engine.

    The earlier fixture set is explicit that its transcripts have
    `provenance="reference"` and that no word error rate may be quoted from them.
    This tier's cassette is different in kind, and the difference is worth an
    assertion rather than a sentence.
    """
    assert cassette.provenances() == {"recorded"}


def test_assembling_a_row_is_deterministic(rows, cassette) -> None:
    """Same corpus, same clips, same digest — twice. Otherwise the cassette misses."""
    cache = ClipCache()
    for scenario in rows:
        if scenario.audio_status() != "runnable":
            continue
        first = assemble_audio(scenario, cache=cache)
        second = assemble_audio(scenario, cache=cache)
        assert first.digest == second.digest, scenario.id


def test_an_untestable_row_cannot_be_assembled(rows) -> None:
    """Its whole finding is that there is no audio, so producing some would be a lie."""
    scenario = next(s for s in rows if s.audio and s.audio.untestable)
    with pytest.raises(ValueError, match="no audio to assemble"):
        assemble_audio(scenario, cache=ClipCache())
