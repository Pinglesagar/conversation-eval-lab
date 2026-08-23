"""Tests for the scenario corpus — the dataset, checked like a dataset.

WHAT THIS DEMONSTRATES
----------------------
That the corpus is under test, not merely under version control. An eval corpus
occupies an awkward position: it is the input to every result the harness
produces, so a defect in it is invisible in exactly the way a defect in a test
fixture is invisible — nothing crashes, a row simply asserts less than its author
believed and reports a pass. These tests exist to make that class of defect loud.

Five families of assertion, each aimed at a specific way a corpus rots:

* **It loads at all.** Every file parses, every id is unique, every id matches
  its filename and its suite. This is the cheap half.

* **Closed vocabularies really are closed.** Tool names are checked against the
  system under test's actual surface, perturbation names against the audio
  registry's actual keys. Both sets are restated in `scenarios/loader.py` for
  reasons its docstring explains, and a restatement nobody checks is a copy that
  drifts — so the checking happens here.

* **Coverage is asserted, not claimed.** Every tag in the vocabulary is carried
  by at least one scenario and every shared persona is cited by at least one
  scenario. A README can say a corpus covers prompt injection; only a test can
  keep that true after six months of edits.

* **Determinism, where the corpus is the thing that decides it.** Two of the five
  perturbations draw random numbers. A voice row that uses one without pinning a
  seed turns its own verdict into a sample, so that is a test failure rather than
  a convention.

* **The validator fires.** Roughly half of this file feeds deliberately broken
  scenarios to the loader and asserts it rejects them, naming the reason. A
  validator is a claim about what cannot get through; untested, it is a claim
  about what someone once intended.

WHAT IS NOT TESTED HERE
-----------------------
Whether the system under test passes any of these scenarios. Nothing in this file
runs an agent, an adapter or a model — the corpus is data, and these are tests of
the data. The `expected_failure` blocks are checked for internal consistency
only: that they name contracts which really are declared and really do still run.
Whether the prediction inside them comes true is a question for a suite run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from lab.checks import ContractSet
from lab.clock import FakeClock
from lab.trace.build import TraceBuilder
from lab.trace.schema import Trace
from scenarios.loader import (
    ARG_OPS,
    CORPUS_ROOT,
    PERTURBATION_NAMES,
    SUITE_MINIMUMS,
    SUITES,
    TAG_VOCABULARY,
    TOOL_NAMES,
    Corpus,
    CorpusError,
    Scenario,
    ValidationIssue,
    iter_scenario_paths,
    load_corpus,
    load_scenario,
    main,
    validate_corpus,
)

#: Perturbations with a random component. A voice row using one of these without
#: a seed is not reproducible, which is the one property the whole harness is
#: built on. Derived by inspection of the perturbation signatures, and pinned by
#: `test_stochastic_perturbation_set_matches_the_registry` below so that a new
#: seeded perturbation cannot be added to the registry without landing here too.
STOCHASTIC_PERTURBATIONS: frozenset[str] = frozenset({"add_noise", "packet_loss"})


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    """The real corpus, loaded strictly. Module-scoped: it is read-only data."""
    return load_corpus(strict=True)


# --------------------------------------------------------------------------- #
# It loads, and it is addressable
# --------------------------------------------------------------------------- #


def test_every_scenario_file_loads_and_validates() -> None:
    """The headline assertion: zero errors across the whole corpus.

    Asserted through `validate_corpus` rather than `load_corpus` so that a broken
    corpus produces a list of every problem in the failure message, not just the
    first one. A contributor with fifty errors wants fifty lines.
    """
    validation = validate_corpus()
    assert validation.files_seen > 0, "no scenario files found; is the corpus in place?"
    assert validation.errors == [], "\n".join(i.render() for i in validation.errors)
    assert len(validation.scenarios) == validation.files_seen
    assert validation.ok


def test_no_warnings_either() -> None:
    """Advisories are non-blocking by design, and the shipped corpus still has none.

    A warning here means a row is legal but probably not what its author meant —
    a scenario that can only assert on wording, or a known gap with no start
    date. Tolerating them permanently is how a warning channel becomes noise, so
    the corpus is held to zero and the loader keeps the severity distinction for
    corpora under construction.
    """
    validation = validate_corpus()
    assert validation.warnings == [], "\n".join(i.render() for i in validation.warnings)


def test_scenario_ids_are_unique(corpus: Corpus) -> None:
    ids = corpus.ids()
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicates, f"duplicate scenario id(s): {duplicates}"
    assert len(set(ids)) == len(corpus.scenarios)


def test_id_matches_filename_and_suite(corpus: Corpus) -> None:
    """Given an id from a result row, the file is found by string manipulation.

    Three facts have to agree — directory, filename and id prefix — and the point
    of the redundancy is that a result row is self-locating: `edge-foo` is
    `scenarios/edge/edge-foo.yaml`, with no index to consult and none to fall out
    of date.
    """
    for scenario in corpus:
        assert scenario.source is not None
        path = Path(scenario.source)
        assert path.stem == scenario.id
        assert path.parent.name == scenario.suite
        assert scenario.id.startswith(f"{scenario.suite}-")
        assert path.is_file()


def test_every_file_on_disk_is_accounted_for(corpus: Corpus) -> None:
    """No scenario file is skipped, and nothing outside the suites is parsed."""
    on_disk = {str(p) for p in iter_scenario_paths()}
    loaded = {s.source for s in corpus}
    assert loaded == on_disk
    # Personas are YAML in the corpus root's subtree and must never be read as
    # scenarios; the suite-directory restriction is what prevents it.
    assert not any("personas" in path for path in on_disk)


def test_suite_minimums_are_met(corpus: Corpus) -> None:
    """The corpus is big enough per suite to support a claim about each suite."""
    counts = corpus.suite_counts()
    shortfalls = {
        suite: (counts[suite], floor)
        for suite, floor in SUITE_MINIMUMS.items()
        if counts[suite] < floor
    }
    assert not shortfalls, f"suites below their minimum (have, want): {shortfalls}"
    assert set(counts) == set(SUITES)
    assert sum(counts.values()) == len(corpus.scenarios)


# --------------------------------------------------------------------------- #
# Closed vocabularies, checked against the things they close over
# --------------------------------------------------------------------------- #


def test_every_tool_named_exists_in_the_spec_list(corpus: Corpus) -> None:
    """No scenario constrains a tool the system under test does not expose.

    The loader enforces this per file; this asserts it over the corpus and, more
    usefully, asserts the converse — that every tool in the spec is constrained
    by somebody. An unconstrained tool is a hole in the corpus, not a clean bill
    of health, and it is the kind of hole that appears when a tool is added.
    """
    referenced = corpus.tools_referenced()
    assert referenced <= TOOL_NAMES, f"unknown tool(s): {sorted(referenced - TOOL_NAMES)}"
    assert referenced == TOOL_NAMES, (
        f"tool(s) no scenario constrains: {sorted(TOOL_NAMES - referenced)}; "
        "an unexercised tool is a coverage gap"
    )


def test_tool_names_match_the_system_under_test() -> None:
    """`TOOL_NAMES` is the real tool surface, not a list that once was.

    The corpus names tools as strings, so nothing about it would break if
    `tablemate` renamed one — every predicate would simply become a constraint on
    a tool nobody calls, and the suite would go quieter rather than red. This is
    the test that turns that into a failure.
    """
    tablemate = pytest.importorskip(
        "tablemate.tools", reason="the system under test is built by a separate module"
    )
    exported = {
        name
        for name in dir(tablemate)
        if not name.startswith("_") and callable(getattr(tablemate, name))
    }
    missing = sorted(TOOL_NAMES - exported)
    assert not missing, (
        f"the corpus constrains {missing}, which `tablemate.tools` does not define; "
        "either the tool was renamed or the corpus is asserting on nothing"
    )


def test_perturbation_names_match_the_audio_registry() -> None:
    """The duplicated perturbation set really equals the registry's keys.

    `scenarios/loader.py` restates `lab.voice.perturb.PERTURBATIONS`'s keys so
    that loading a corpus does not require numpy. Its docstring promises this
    test exists; here it is. Drift in either direction matters: a name removed
    from the registry makes a voice row silently unrunnable, and one added
    without landing here cannot be used by any scenario.
    """
    perturb = pytest.importorskip("lab.voice.perturb", reason="needs the audio extra")
    assert PERTURBATION_NAMES == set(perturb.PERTURBATIONS), (
        "loader.PERTURBATION_NAMES has drifted from lab.voice.perturb.PERTURBATIONS: "
        f"loader-only={sorted(PERTURBATION_NAMES - set(perturb.PERTURBATIONS))}, "
        f"registry-only={sorted(set(perturb.PERTURBATIONS) - PERTURBATION_NAMES)}"
    )


def test_arg_ops_are_all_accepted_by_the_predicate() -> None:
    """Every operator the loader admits is one `ArgPredicate` implements."""
    from lab.checks import ArgPredicate

    for op in sorted(ARG_OPS):
        needs_value = op not in ("present", "absent", "truthy")
        ArgPredicate(
            tool="create_booking",
            arg="party_size",
            op=op,
            value=(["2"] if op == "in" else "2") if needs_value else None,
        )


# --------------------------------------------------------------------------- #
# Coverage, asserted rather than claimed
# --------------------------------------------------------------------------- #


def test_corpus_covers_every_tag_at_least_once(corpus: Corpus) -> None:
    """Every entry in the tag vocabulary is exercised by at least one scenario."""
    unused = corpus.unused_tags()
    assert not unused, (
        f"tag(s) defined but unused: {unused}; either write a scenario that "
        "exercises each, or delete the vocabulary entry — an aspirational tag "
        "makes a coverage report read better than the corpus is"
    )
    assert set(corpus.tag_counts()) == set(TAG_VOCABULARY)


@pytest.mark.parametrize(
    "requirement",
    [
        # The coverage the corpus was commissioned to provide, each expressed as
        # the tag combination that demonstrates it. Parametrised so a gap names
        # itself in the failure output instead of hiding inside one big assert.
        ("multi-intent",),
        ("correction",),
        ("third-party",),
        ("large-party",),
        ("dietary", "notes"),
        ("modification",),
        ("vague-opener",),
        ("withholding",),
        ("policy", "booking"),
        ("cancellation",),
        ("missing-reference",),
        ("injection",),
        ("off-topic",),
        ("noise",),
        ("telephone-band",),
        ("fast-speech",),
    ],
)
def test_required_coverage_is_present(corpus: Corpus, requirement: tuple[str, ...]) -> None:
    """Each commissioned capability has at least one row demonstrating it."""
    matches = corpus.tagged(*requirement)
    assert matches, f"no scenario carries all of {list(requirement)}"


def test_every_shared_persona_is_used(corpus: Corpus) -> None:
    """A persona nobody cites is dead weight that still looks like coverage."""
    cited = {s.persona for s in corpus if isinstance(s.persona, str)}
    unused = sorted(set(corpus.personas) - cited)
    assert not unused, f"persona(s) defined but never used: {unused}"


def test_personas_are_reused_across_suites(corpus: Corpus) -> None:
    """At least one persona appears in more than one suite.

    The reason personas are a separate object from goals is so that "the terse
    caller fails" is a statement about the agent rather than about one
    conversation. That only holds if personas are actually shared across suites;
    a corpus where every row invented its own caller would have the type
    structure and none of the benefit.
    """
    suites_by_persona: dict[str, set[str | None]] = {}
    for scenario in corpus:
        if isinstance(scenario.persona, str):
            suites_by_persona.setdefault(scenario.persona, set()).add(scenario.suite)
    shared = {name: s for name, s in suites_by_persona.items() if len(s) > 1}
    assert shared, "no persona is used in more than one suite"


def test_inline_personas_are_the_exception(corpus: Corpus) -> None:
    """Inline personas exist, and stay rare enough to mean something.

    Both halves matter. If none were inline the loader's inline branch would be
    dead code in practice; if many were, the shared-persona discipline that makes
    cross-suite comparison possible would be gone. One is a code path with
    coverage; a dozen would be a corpus with a habit.
    """
    inline = [s.id for s in corpus if not isinstance(s.persona, str)]
    assert inline, "no scenario uses an inline persona, so that branch is untested by data"
    assert len(inline) <= 3, f"too many inline personas ({inline}); prefer scenarios/personas/"


# --------------------------------------------------------------------------- #
# Contracts compile, and assert something
# --------------------------------------------------------------------------- #


def test_every_scenario_compiles_to_contracts(corpus: Corpus) -> None:
    """Each scenario builds a runnable `ContractSet` with unique contract names."""
    for scenario in corpus:
        contract_set = scenario.contract_set()
        assert isinstance(contract_set, ContractSet)
        assert contract_set.name == scenario.id
        names = [c.name for c in contract_set.contracts]
        assert names, f"{scenario.id}: compiled to zero contracts"
        assert len(set(names)) == len(names), f"{scenario.id}: duplicate contract names {names}"
        assert names == scenario.contract_names(), (
            f"{scenario.id}: declared contract names disagree with the compiled ones; "
            "expected_failure is validated against the declared list, so the two must match"
        )


def test_contracts_run_without_raising_on_an_unrelated_trace(corpus: Corpus) -> None:
    """No contract explodes on a trace that has nothing to do with it.

    Every scenario's contracts are run against one short, unrelated session. The
    assertion is not that they pass — most should not, and several should report
    themselves inapplicable — but that a contract's failure mode is a *result*
    rather than an exception. A contract that raises on unfamiliar input turns one
    bad row into a suite that cannot finish.
    """
    trace = _unrelated_trace()
    for scenario in corpus:
        report = scenario.contract_set().run(trace, scenario.check_context())
        assert report.total == len(scenario.contract_names())
        assert report.errors == 0, (
            f"{scenario.id}: contract(s) raised on an unrelated trace: "
            + "; ".join(r.render() for r in report.failures() if r.error)
        )


def test_expected_failures_still_run_their_contracts(corpus: Corpus) -> None:
    """`expected_failure` is a prediction, not a skip.

    The contract it names must be one the scenario actually builds and runs, so
    that the day the gap closes the corpus reports an unexpected pass instead of
    continuing to say nothing. This is the test that keeps the block honest.
    """
    gaps = corpus.expected_failures()
    assert gaps, "no scenario declares an expected failure; the case study has seeded defects"
    for scenario in gaps:
        assert scenario.expected_failure is not None
        compiled = {c.name for c in scenario.contract_set().contracts}
        for name in scenario.expected_failure.contracts:
            assert name in compiled, (
                f"{scenario.id}: expects {name!r} to fail but does not compile it"
            )
            assert scenario.expects_failure_of(name)
        assert scenario.expected_failure.since, f"{scenario.id}: expected_failure has no `since`"


def test_expected_failures_are_a_minority(corpus: Corpus) -> None:
    """Known gaps stay a minority of the corpus, and are reported as a rate.

    A suite where most rows are expected to fail has stopped being a regression
    detector: nobody reads it, because its resting state is red. The bound is
    generous on purpose — the point is that the property is measured, in a repo
    whose house style forbids printing a rate without both its terms.
    """
    gaps, total = len(corpus.expected_failures()), len(corpus.scenarios)
    assert total, "empty corpus"
    assert gaps * 2 < total, f"{gaps}/{total} scenarios expect a failure, which is too many"


def test_expected_failures_span_more_than_one_contract_kind(corpus: Corpus) -> None:
    """The known gaps are not all the same check firing repeatedly.

    Three seeded defects with three distinct signatures should show up as at
    least three distinct contract names in the expected-failure index. If they
    collapsed onto one, either two defects share a detector — in which case one
    of them is not being localised — or a row has been mislabelled.
    """
    counts = corpus.expected_failure_counts()
    assert len(counts) >= 3, f"expected failures concentrate on too few contracts: {counts}"


def test_expected_failure_prose_is_about_the_system(corpus: Corpus) -> None:
    """The prediction describes observable behaviour, in future tense.

    A weak `expectation` field is the most likely way this block rots: it turns
    into a one-line label, and then a reader has to run the suite to find out what
    the row was for. The schema already enforces a length floor; this adds the
    grammatical shape, which is what makes it a prediction rather than a note.
    """
    for scenario in corpus.expected_failures():
        assert scenario.expected_failure is not None
        prose = scenario.expected_failure.expectation.lower()
        assert "we expect" in prose or "is expected" in prose, (
            f"{scenario.id}: expectation reads as a description rather than a prediction"
        )
        assert len(prose.split()) >= 25, f"{scenario.id}: expectation is too thin to act on"


# --------------------------------------------------------------------------- #
# Voice rows: audio conditions, and determinism
# --------------------------------------------------------------------------- #


def test_voice_rows_declare_audio_conditions(corpus: Corpus) -> None:
    """Every voice row perturbs audio; no other row does."""
    for scenario in corpus:
        if scenario.suite == "voice":
            assert scenario.voice is not None, f"{scenario.id}: voice row with no voice block"
            assert scenario.voice.perturbations, f"{scenario.id}: voice row with no perturbation"
            assert scenario.voice.sample_rate > 0
        elif scenario.voice is not None:
            assert not scenario.voice.perturbations, (
                f"{scenario.id}: perturbs audio outside the voice suite, so suite-level "
                "results stop being comparable"
            )


def test_stochastic_perturbations_are_seeded(corpus: Corpus) -> None:
    """A random perturbation without a seed makes a verdict a sample.

    This is the corpus's share of the repo's cardinal rule. `add_noise` and
    `packet_loss` draw from a generator, so an unseeded row would place different
    noise on the audio every run and its pass or fail would carry a variance
    nobody declared — indistinguishable, in a report, from a flaky agent.
    """
    for scenario in corpus.suite("voice"):
        assert scenario.voice is not None
        for perturbation in scenario.voice.perturbations:
            if perturbation.name in STOCHASTIC_PERTURBATIONS:
                assert "seed" in perturbation.params, (
                    f"{scenario.id}: {perturbation.name} draws random numbers but declares no "
                    "seed, so this row is not reproducible"
                )
                assert isinstance(perturbation.params["seed"], int)


def test_stochastic_perturbation_set_matches_the_registry() -> None:
    """`STOCHASTIC_PERTURBATIONS` is derived from the signatures, not remembered.

    Without this, a newly added random perturbation would slip past the seed test
    above: the corpus could use it unseeded and nothing would object, because the
    list of things needing a seed is maintained by hand in this file.
    """
    import inspect

    perturb = pytest.importorskip("lab.voice.perturb", reason="needs the audio extra")
    seeded = {
        name
        for name, function in perturb.PERTURBATIONS.items()
        if "seed" in inspect.signature(function).parameters
    }
    assert seeded == STOCHASTIC_PERTURBATIONS, (
        "the set of perturbations taking a seed has changed; update "
        f"STOCHASTIC_PERTURBATIONS (registry says {sorted(seeded)})"
    )


def test_perturbation_chains_keep_declaration_order(corpus: Corpus) -> None:
    """A chain is applied in the order written, because these do not commute.

    Band-limiting after adding noise filters out the noise the caller actually
    hears; adding it after produces the channel they get. `chain()` therefore has
    to preserve order rather than normalise it, and at least one row in the corpus
    has to have more than one stage for the property to be observable at all.
    """
    chains = [
        (s.id, s.voice.chain()) for s in corpus.suite("voice") if s.voice and s.voice.perturbations
    ]
    assert chains
    for scenario_id, chain in chains:
        scenario = corpus.by_id(scenario_id)
        assert scenario.voice is not None
        assert [name for name, _ in chain] == [p.name for p in scenario.voice.perturbations]
    assert any(len(chain) > 1 for _, chain in chains), (
        "no voice row chains two perturbations, so ordering is untested by data"
    )


def test_reference_transcripts_are_caller_words(corpus: Corpus) -> None:
    """Where a reference transcript exists it is non-empty, per turn.

    Optional by design: word error rate is only meaningful against ground truth,
    and a reference that is really a paraphrase produces a confidently wrong
    number. So the assertion is on the rows that opt in — a blank turn in a
    reference would silently distort the score for the whole row.
    """
    with_reference = [
        s for s in corpus.suite("voice") if s.voice and s.voice.reference_transcript
    ]
    assert with_reference, "no voice row supplies a reference transcript, so WER is unscorable"
    for scenario in with_reference:
        assert scenario.voice is not None
        for turn in scenario.voice.reference_transcript:
            assert turn.strip(), f"{scenario.id}: empty turn in the reference transcript"


def test_voice_latency_budgets_are_plausible(corpus: Corpus) -> None:
    """A declared budget is a positive number of milliseconds, not a placeholder."""
    for scenario in corpus.suite("voice"):
        assert scenario.voice is not None
        budget = scenario.voice.latency_budget_ms
        if budget is not None:
            assert 100.0 <= budget <= 10_000.0, f"{scenario.id}: implausible budget {budget}"


# --------------------------------------------------------------------------- #
# Goals and personas are internally consistent
# --------------------------------------------------------------------------- #


def test_gated_facts_are_askable(corpus: Corpus) -> None:
    """A fact the caller withholds must have a pattern that releases it.

    `on_request_only` without `ask_patterns` is a fact the caller will never say,
    because no agent utterance can match a pattern that does not exist. The
    scenario then runs with a value the agent could not have obtained, and any
    check on it reports a failure the agent had no way to avoid — a false finding,
    which is more expensive than a missing check.
    """
    for scenario in corpus:
        goal = scenario.goal
        for key in goal.gated_keys():
            assert goal.ask_patterns.get(key), (
                f"{scenario.id}: fact {key!r} is withheld until asked but has no ask_patterns, "
                "so the caller can never release it"
            )


def test_caller_profiles_resolve(corpus: Corpus) -> None:
    """Every scenario yields a caller: persona plus goal, no dangling references."""
    for scenario in corpus:
        profile = scenario.caller_profile(corpus.personas)
        assert profile.scenario_id == scenario.id
        assert profile.persona.style
        assert profile.goal.intent


def test_success_criteria_are_stated(corpus: Corpus) -> None:
    """Every row says in prose what success looks like.

    Not executable — `lab.checks` owns assertions — and that is exactly why it is
    load-bearing. These lines are how a reviewer judges whether the contracts
    cover the goal, which is the one question no amount of green can answer.
    """
    for scenario in corpus:
        criteria = scenario.goal.success_criteria
        assert len(criteria) >= 2, f"{scenario.id}: fewer than two success criteria"
        assert all(c.strip() for c in criteria)


def test_notes_explain_the_row(corpus: Corpus) -> None:
    """Each row carries enough prose to justify its own existence.

    The schema enforces 20 characters; this enforces a sentence's worth. A corpus
    is read far more often than it is written, and a row nobody can explain is a
    row nobody dares delete — which is how suites grow to a thousand cases with
    two hundred of them meaningful.
    """
    for scenario in corpus:
        assert len(scenario.notes.split()) >= 15, f"{scenario.id}: notes too thin"
        assert scenario.title[0].isupper(), f"{scenario.id}: title should read as a sentence"


def test_clean_domain(corpus: Corpus) -> None:
    """The corpus is about restaurant booking and nothing else.

    A guard against topic drift as the corpus grows: scenario text is the easiest
    place for an unrelated domain to arrive, via a copied example or a habit of
    phrasing. Cheap to run, and it fails on the file that introduced the term.

    The term list is deliberately restricted to words with no innocent reading in
    a restaurant. An earlier version included the bare word "patient", and it
    fired on two rows describing an agent's *patient questioning* — a perfectly
    ordinary adjective. A second version fired on the word "diagnostic", which is
    ordinary engineering English and appears in a note explaining what a row is
    for. That is the failure mode of every lint of this kind: a guard that cries
    wolf on correct prose gets suppressed, and then it is guarding nothing. So
    the noun forms are matched and the adjectives are not.
    """
    forbidden = (
        r"\bpatients\b",
        r"\bthe patient\b",
        r"\bclinic\b",
        r"\bprescription\b",
        r"\bdiagnos(is|ed)\b",
        r"\bmedical record\b",
        r"\bwaiting room\b",
    )
    for path in iter_scenario_paths():
        text = path.read_text(encoding="utf-8").lower()
        found = [pattern for pattern in forbidden if re.search(pattern, text)]
        assert not found, f"{path.name}: out-of-domain term(s) matching {found}"

    # Both directions, in one test, because the assertion above passes trivially
    # if the patterns match nothing at all — which is exactly what happens after
    # someone "fixes" a false positive by gutting the pattern.
    assert any(re.search(p, "we telephoned the clinic") for p in forbidden)
    assert any(re.search(p, "three patients were waiting") for p in forbidden)
    innocent = "the agent's patient questioning was diagnostic"
    assert not any(re.search(p, innocent) for p in forbidden)


# --------------------------------------------------------------------------- #
# The validator fires — the other direction
# --------------------------------------------------------------------------- #


def _write(tmp_path: Path, suite: str, name: str, body: dict[str, Any]) -> Path:
    """Write a scenario file into a throwaway corpus and return its path."""
    directory = tmp_path / suite
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


def _valid_body(scenario_id: str = "happy-fixture-row") -> dict[str, Any]:
    """A minimal scenario that really does validate, as the base for mutations.

    Every negative test below starts from this and breaks exactly one thing, so a
    rejection can only be attributed to that thing. The base is asserted valid by
    `test_the_fixture_body_is_actually_valid`, without which every negative test
    in this file could be passing for the wrong reason.
    """
    return {
        "id": scenario_id,
        "title": "A fixture row used by the validator tests",
        "persona": "brisk_regular",
        "tags": ["booking"],
        "goal": {
            "intent": "book a table for two",
            "facts": {"party_size": "2", "date": "Friday"},
            "success_criteria": ["a booking exists", "it is for two"],
        },
        "tools": {
            "expected": ["create_booking"],
            "min_calls": {"create_booking": 1},
            "args": [
                {"tool": "create_booking", "arg": "party_size", "op": "eq", "ref": "party_size"}
            ],
        },
        "notes": "A deliberately dull row that exists only to be mutated by the tests.",
    }


def test_the_fixture_body_is_actually_valid(tmp_path: Path) -> None:
    """The control for every negative test below."""
    _write(tmp_path, "happy", "happy-fixture-row", _valid_body())
    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")
    assert validation.errors == [], "\n".join(i.render() for i in validation.errors)
    assert len(validation.scenarios) == 1


@pytest.mark.parametrize(
    "mutation, expected_fragment",
    [
        pytest.param(
            {"tools": {"expected": ["search_table"]}},
            "unknown tool",
            id="typo-in-tool-name",
        ),
        pytest.param(
            {"tags": ["dietry"]},
            "unknown tag",
            id="typo-in-tag",
        ),
        pytest.param(
            {"tags": ["happy"]},
            "unknown tag",
            id="suite-used-as-tag",
        ),
        pytest.param(
            {"tags": ["booking", "booking"]},
            "duplicate tag",
            id="duplicate-tag",
        ),
        pytest.param(
            {
                "no_re_ask": {
                    "fields": [{"name": "allergy"}],
                }
            },
            "can never fire",
            id="tracked-field-the-caller-never-says",
        ),
        pytest.param(
            {
                "tools": {
                    "expected": ["create_booking"],
                    "args": [
                        {
                            "tool": "create_booking",
                            "arg": "party_size",
                            "op": "eq",
                            "ref": "covers",
                        }
                    ],
                }
            },
            "which is not in the scenario's context",
            id="unresolvable-ref",
        ),
        pytest.param(
            {
                "expected_failure": {
                    "contracts": ["promise-kept"],
                    "expectation": (
                        "We expect the agent to claim a booking it never made, and the "
                        "contract to report the claim with nothing behind it."
                    ),
                    "since": "a test",
                }
            },
            "does not declare",
            id="known-gap-pointing-at-a-contract-that-does-not-run",
        ),
        pytest.param(
            {"tools": {"expected": ["create_booking"], "forbidden": ["create_booking"]}},
            "both expected and forbidden",
            id="tool-required-and-forbidden",
        ),
        pytest.param(
            {
                "tools": {
                    "expected": ["create_booking"],
                    "min_calls": {"create_booking": 3},
                    "max_calls": {"create_booking": 1},
                }
            },
            "no run can satisfy",
            id="impossible-call-counts",
        ),
        pytest.param(
            {"goal": {"intent": "book", "on_request_only": ["name"], "facts": {"date": "Friday"}}},
            "on_request_only names facts that do not exist",
            id="gated-fact-that-does-not-exist",
        ),
        pytest.param(
            {"tools": None, "promises": None, "phrases": None},
            "asserts nothing",
            id="scenario-with-no-contracts",
        ),
        pytest.param(
            {"promises": {"use_defaults": False}},
            "would assert nothing",
            id="promise-contract-with-nothing-in-it",
        ),
        pytest.param(
            {"phrases": {"name": "phrases"}},
            "asserts nothing",
            id="phrase-contract-with-no-phrases",
        ),
        pytest.param(
            {"tools": {"expected": ["create_booking"], "unexpected_key": 1}},
            "Extra inputs are not permitted",
            id="unknown-key-in-a-block",
        ),
        pytest.param(
            {"title": "short"},
            "at least 8 characters",
            id="title-too-short",
        ),
        pytest.param(
            {
                "tools": {
                    "expected": ["create_booking"],
                    "args": [
                        {
                            "tool": "create_booking",
                            "arg": "notes",
                            "op": "matches",
                            "value": "unclosed (group",
                        }
                    ],
                }
            },
            "not a valid regex",
            id="bad-regex",
        ),
        pytest.param(
            {
                "tools": {
                    "expected": ["create_booking"],
                    "args": [
                        {
                            "tool": "create_booking",
                            "arg": "party_size",
                            "op": "eq",
                            "value": "2",
                            "ref": "party_size",
                        }
                    ],
                }
            },
            "needs exactly one of",
            id="both-value-and-ref",
        ),
        pytest.param(
            {"voice": {"perturbations": [{"name": "add_noise", "params": {"snr_db": 5}}]}},
            "perturbations belong to the voice suite",
            id="perturbation-outside-the-voice-suite",
        ),
    ],
)
def test_validator_rejects_broken_scenarios(
    tmp_path: Path, mutation: dict[str, Any], expected_fragment: str
) -> None:
    """One mutation, one rejection, and the message says which.

    The mutations are the silent-green failure modes named in `loader.py`'s
    docstring: a tool that does not exist, a tag that is nearly right, a tracked
    field the caller never says, a known gap pointing at a check that does not
    run. None of these would crash a suite run. Each would quietly assert less
    than its author intended and report a pass, which is why each has to be an
    error at load time and why each needs a test proving it still is.
    """
    body = _valid_body()
    for key, value in mutation.items():
        if value is None:
            body.pop(key, None)
        elif isinstance(value, dict) and isinstance(body.get(key), dict):
            body[key] = {**body[key], **value} if key == "goal" else value
        else:
            body[key] = value
    _write(tmp_path, "happy", "happy-fixture-row", body)

    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")

    assert validation.errors, f"the corpus accepted a scenario it should have rejected: {body}"
    joined = " ".join(i.message for i in validation.errors)
    assert expected_fragment in joined, (
        f"rejected, but not for the stated reason.\nwanted fragment: {expected_fragment!r}\n"
        f"got: {joined}"
    )
    assert validation.scenarios == []
    assert not validation.ok


def test_validator_reports_every_problem_not_just_the_first(tmp_path: Path) -> None:
    """Collect, then report. A dataset validator that stops at file one is useless.

    Three separately broken files must produce three issues in one pass, each
    naming its own path — the difference between one review and three round
    trips.
    """
    _write(tmp_path, "happy", "happy-bad-tool", {**_valid_body("happy-bad-tool"), "tags": ["nope"]})
    _write(tmp_path, "edge", "edge-bad-tag", {**_valid_body("edge-bad-tag"), "tags": ["nope"]})
    _write(
        tmp_path,
        "adversarial",
        "adversarial-bad-title",
        {**_valid_body("adversarial-bad-title"), "title": "x"},
    )

    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")

    assert validation.files_seen == 3
    assert len(validation.errors) == 3, [i.render() for i in validation.errors]
    assert {Path(i.path).stem for i in validation.errors} == {
        "happy-bad-tool",
        "edge-bad-tag",
        "adversarial-bad-title",
    }
    assert "0/3" in validation.summary_line() or "0 " in validation.summary_line()


def test_suite_as_tag_is_rejected_on_its_own_terms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suite-as-tag rule, exercised where it is actually reachable.

    Writing `tags: [happy]` today is rejected as an unknown tag, because no suite
    name is in the vocabulary — so the dedicated suite-as-tag check never runs and
    the parametrised case above asserts the message it really gets. That check is
    not pointless, though: it is what catches the day somebody adds a vocabulary
    entry that collides with a suite name, which is an easy and tempting mistake
    ("policy" and "booking" are already tags; "voice" would look like a natural
    third). Reached here by putting such an entry in the vocabulary for the
    duration of one test, so the branch is covered by an assertion rather than by
    an intention.
    """
    monkeypatch.setitem(TAG_VOCABULARY, "happy", "a tag that collides with a suite name")
    _write(tmp_path, "happy", "happy-fixture-row", {**_valid_body(), "tags": ["happy"]})

    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")

    assert any("is a suite, not a tag" in i.message for i in validation.errors), [
        i.render() for i in validation.errors
    ]


def test_duplicate_ids_are_structurally_impossible(tmp_path: Path) -> None:
    """A colliding id cannot be written, because the id *is* the filename.

    Worth stating as a test rather than as a comment, because the property is
    what the whole addressing scheme rests on and it is enforced by two rules
    acting together: an id must equal its file's stem, and it must carry its
    directory's prefix. Between them there is no pair of files that can share an
    id — the second copy is rejected before the collision can form, whichever way
    round it is attempted.

    `validate_corpus` does also carry an explicit duplicate-id check. It is
    unreachable while those two rules hold, and it is kept deliberately: it is the
    guard that would catch a future loader relaxing the filename rule, at which
    point a collision would stop being impossible and start being silent.
    """
    body = _valid_body("happy-fixture-row")
    _write(tmp_path, "happy", "happy-fixture-row", body)
    # Same id, different file: rejected because the id no longer matches the stem.
    _write(tmp_path, "happy", "happy-second-file", body)
    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")
    assert len(validation.scenarios) == 1
    assert any("does not match the file name" in i.message for i in validation.errors)

    # Same id, different suite: rejected because the id lacks that suite's prefix.
    other = tmp_path / "other"
    _write(other, "happy", "happy-fixture-row", body)
    _write(other, "edge", "edge-fixture-row", {**body, "id": "happy-fixture-row"})
    validation = validate_corpus(other, persona_dir=CORPUS_ROOT / "personas")
    assert len(validation.scenarios) == 1
    assert any("must start with its suite prefix" in i.message for i in validation.errors)


def test_id_must_match_the_filename(tmp_path: Path) -> None:
    """A row whose id and filename disagree cannot be located from a result."""
    _write(tmp_path, "happy", "happy-fixture-row", _valid_body("happy-different-id"))
    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")
    assert any("does not match the file name" in i.message for i in validation.errors)


def test_unknown_persona_is_an_error(tmp_path: Path) -> None:
    """A scenario citing a persona that does not exist is rejected by name."""
    _write(tmp_path, "happy", "happy-fixture-row", {**_valid_body(), "persona": "nobody"})
    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")
    assert any("unknown persona" in i.message for i in validation.errors)


def test_voice_row_without_a_perturbation_is_an_error(tmp_path: Path) -> None:
    """A text row in the voice suite says nothing about audio, so it is rejected."""
    body = {**_valid_body("voice-no-audio"), "id": "voice-no-audio"}
    _write(tmp_path, "voice", "voice-no-audio", body)
    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")
    assert any("must declare at least one perturbation" in i.message for i in validation.errors)


def test_unknown_perturbation_is_an_error(tmp_path: Path) -> None:
    """A typo in a perturbation name would otherwise report clean audio as noisy."""
    body = {
        **_valid_body("voice-typo"),
        "id": "voice-typo",
        "voice": {"perturbations": [{"name": "add_noize", "params": {"snr_db": 5}}]},
    }
    _write(tmp_path, "voice", "voice-typo", body)
    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")
    assert any("unknown perturbation" in i.message for i in validation.errors)


def test_suite_declared_in_yaml_must_match_the_directory(tmp_path: Path) -> None:
    """A row cannot claim a suite it does not sit in."""
    _write(tmp_path, "happy", "happy-fixture-row", {**_valid_body(), "suite": "edge"})
    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")
    assert any("declares suite" in i.message for i in validation.errors)


def test_load_scenario_rejects_a_non_mapping(tmp_path: Path) -> None:
    """A YAML list where a mapping belongs is a clear error, not a confusing one."""
    directory = tmp_path / "happy"
    directory.mkdir(parents=True)
    path = directory / "happy-list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a mapping"):
        load_scenario(path)


def test_load_scenario_rejects_an_unknown_suite(tmp_path: Path) -> None:
    """Scenarios live in one of the four suite directories and nowhere else."""
    directory = tmp_path / "sundry"
    directory.mkdir(parents=True)
    path = directory / "sundry-row.yaml"
    path.write_text(yaml.safe_dump(_valid_body()), encoding="utf-8")
    with pytest.raises(ValueError, match="is not one of"):
        load_scenario(path)


def test_advisory_warnings_do_not_block(tmp_path: Path) -> None:
    """A wording-only row loads, and says so as a warning rather than an error.

    The severity split is the point: a row that can only assert on phrasing is
    legal and occasionally correct, but it cannot detect a booking that never
    happened, and a reviewer should be told. `ok` stays true, so a corpus under
    construction remains loadable.
    """
    body = _valid_body()
    body.pop("tools")
    body["phrases"] = {"forbidden": ["is confirmed"]}
    _write(tmp_path, "happy", "happy-fixture-row", body)

    validation = validate_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas")

    assert validation.ok
    assert len(validation.scenarios) == 1
    assert any("no tool or promise contract" in i.message for i in validation.warnings)
    assert validation.errors == []


def test_strict_load_raises_listing_every_issue(tmp_path: Path) -> None:
    """`load_corpus(strict=True)` fails loudly and completely."""
    _write(tmp_path, "happy", "happy-a", {**_valid_body("happy-a"), "tags": ["nope"]})
    _write(tmp_path, "happy", "happy-b", {**_valid_body("happy-b"), "tags": ["nope"]})
    with pytest.raises(CorpusError) as excinfo:
        load_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas", strict=True)
    assert len(excinfo.value.issues) == 2
    assert "happy-a" in str(excinfo.value) and "happy-b" in str(excinfo.value)


def test_non_strict_load_returns_what_parsed(tmp_path: Path) -> None:
    """Non-strict loading keeps the good rows and carries the issues alongside."""
    _write(tmp_path, "happy", "happy-good", _valid_body("happy-good"))
    _write(tmp_path, "happy", "happy-bad", {**_valid_body("happy-bad"), "tags": ["nope"]})
    loaded = load_corpus(tmp_path, persona_dir=CORPUS_ROOT / "personas", strict=False)
    assert loaded.ids() == ["happy-good"]
    assert len(loaded.issues) == 1


# --------------------------------------------------------------------------- #
# The CLI is the gate
# --------------------------------------------------------------------------- #


def test_cli_exits_zero_on_the_real_corpus(capsys: pytest.CaptureFixture[str]) -> None:
    """`python -m scenarios.loader` is green, and says so in words."""
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "VALID" in out
    assert "scenario files loaded" in out


def test_cli_exits_nonzero_on_a_broken_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gate fails the build rather than printing a suggestion."""
    _write(tmp_path, "happy", "happy-bad", {**_valid_body("happy-bad"), "tags": ["nope"]})
    code = main(["--root", str(tmp_path), "--persona-dir", str(CORPUS_ROOT / "personas")])
    assert code == 1
    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "unknown tag" in out


def test_cli_strict_warnings_flag(tmp_path: Path) -> None:
    """`--strict-warnings` turns advisories into a non-zero exit, on request."""
    body = _valid_body()
    body.pop("tools")
    body["phrases"] = {"forbidden": ["is confirmed"]}
    _write(tmp_path, "happy", "happy-fixture-row", body)
    argv = ["--root", str(tmp_path), "--persona-dir", str(CORPUS_ROOT / "personas")]
    assert main(argv) == 0
    assert main([*argv, "--strict-warnings"]) == 1


def test_cli_summary_reports_rates_with_both_terms(capsys: pytest.CaptureFixture[str]) -> None:
    """The coverage report prints counts over totals, never a bare percentage.

    House style, enforced rather than requested: a percentage without its
    denominator is the single easiest way for an eval report to mislead, and a
    coverage summary is exactly where the temptation lives.
    """
    assert main(["--summary"]) == 0
    out = capsys.readouterr().out
    assert "%" not in out
    assert f"/{len(load_corpus().scenarios)}" in out
    for suite in SUITES:
        assert suite in out


def test_cli_json_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` emits a parseable report with the coverage keys a CI job needs."""
    import json

    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["loaded"] == payload["files_seen"]
    assert payload["unused_tags"] == []
    assert set(payload["suite_counts"]) == set(SUITES)
    assert sorted(payload["perturbations_referenced"]) == sorted(PERTURBATION_NAMES)


def test_cli_list_names_every_scenario(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    for scenario_id in load_corpus().ids():
        assert scenario_id in out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _unrelated_trace() -> Trace:
    """A short session that satisfies nothing in particular.

    Used to prove contracts return results rather than raise on input they were
    not written for. Deliberately not a passing trace for any row: it books a
    party of two with no notes, so most contracts should fail or report
    themselves inapplicable — and either is a fine outcome for this test, which
    only forbids exceptions.
    """
    clock = FakeClock()
    builder = TraceBuilder(
        scenario_id="unrelated", adapter="test", session_id="sess-unrelated", clock=clock
    )
    builder.session_start()
    clock.advance(1.0)
    builder.caller_utterance("A table for two on Friday at 7, please.")
    clock.advance(0.5)
    builder.agent_utterance("Of course, let me check.", agent="BookingAgent")
    clock.advance(0.2)
    builder.tool_call("search_tables", args={"date": "Friday", "time": "7pm", "party_size": "2"})
    clock.advance(0.3)
    builder.tool_result("search_tables", result={"tables": ["T1"]})
    clock.advance(0.2)
    builder.tool_call(
        "create_booking",
        args={"name": "Fixture", "date": "Friday", "time": "7pm", "party_size": "2"},
    )
    clock.advance(0.3)
    builder.tool_result("create_booking", result={"booking_ref": "TM-2001"})
    clock.advance(0.2)
    builder.agent_utterance("That is booked for two on Friday at 7.", agent="BookingAgent")
    clock.advance(0.1)
    builder.session_end()
    return builder.build()


def test_the_unrelated_trace_is_well_formed() -> None:
    """The helper above is a valid trace, so tests using it test what they claim."""
    trace = _unrelated_trace()
    assert trace.is_ordered()
    assert trace.unknown_kinds() == set()
    assert "create_booking" in trace.tool_names()


def test_validation_issue_renders_readably() -> None:
    """An issue prints as one line naming severity, file and scenario."""
    issue = ValidationIssue(path="scenarios/happy/x.yaml", scenario_id="happy-x", message="bad")
    rendered = issue.render()
    assert "ERROR" in rendered and "happy-x" in rendered and "bad" in rendered


def test_scenario_summary_line_flags_expected_failures(corpus: Corpus) -> None:
    """A listing line marks the rows that predict a failure, for triage at a glance."""
    gap = corpus.expected_failures()[0]
    assert "expected-failure" in gap.summary_line()
    clean = next(s for s in corpus if s.expected_failure is None)
    assert "expected-failure" not in clean.summary_line()
    assert isinstance(gap, Scenario)
