"""The selector's own calibration: does the measurement measure anything.

WHAT THESE TESTS ARE FOR
------------------------
`lab.selection.calibrate` exists to stop a selector being trusted on faith. That
makes it a measuring instrument, and an instrument that reports a flattering
number when it has learned nothing is worse than no instrument at all — so most
of what follows is about the *refusals*:

*   a recall over an empty denominator is `None`, never 1.0;
*   a failure the selector could not possibly have missed does not count toward
    the recall it publishes;
*   a case that could not be measured is excluded and named, never treated as a
    silent pass;
*   the gate raises on all three, and the bypass is a keyword argument.

The cheap tests build `CaseOutcome`s by hand, which is why `score_case` was
written to take observations rather than to go and collect them: the failures
this repository's history does not contain can be constructed in three lines.
The handful of expensive tests that really do run a suite are marked so, and
kept to the smallest sample that still exercises the path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lab.selection.calibrate import (
    MIN_RECALL,
    CaseOutcome,
    CaseScore,
    HistoryStudy,
    Mutant,
    SelectorBelowThresholdError,
    SelectorNotCalibratedError,
    SuiteRun,
    aggregate,
    enumerate_mutants,
    in_ci_mode,
    main,
    require_calibrated_selector,
    run_history_study,
    run_mutation_study,
    run_text_suite,
    sample_mutants,
    score_case,
)
from lab.selection.select import Selection, Verdict

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------- #
# Builders. Observations, not machinery.
# --------------------------------------------------------------------------- #


def make_selection(selected: list[str], corpus: int) -> Selection:
    """A `Selection` that reports `selected` out of a corpus of `corpus`."""
    from lab.selection.select import Reason, ReasonCode, ScenarioDecision

    decisions = []
    for index in range(corpus):
        scenario_id = f"s{index}"
        chosen = scenario_id in selected
        code = ReasonCode.TRACE_DEPENDENCY if chosen else ReasonCode.NO_OVERLAP
        decisions.append(
            ScenarioDecision(
                scenario_id=scenario_id,
                suite="happy",
                selected=chosen,
                reasons=(Reason(code=code, detail="test"),),
            )
        )
    return Selection(
        base_ref="base",
        head_ref="head",
        repo_root="/tmp",
        map_path="map",
        overrides_path=None,
        corpus_size=corpus,
        verdict=Verdict.SUBSET,
        decisions=tuple(decisions),
    )


def outcome(
    *,
    base: dict[str, list[str]],
    head: dict[str, list[str]],
    selected: list[str],
    corpus: int = 4,
    label: str = "case",
    kind: str = "mutation",
    selection_error: str | None = None,
    base_error: str | None = None,
    head_error: str | None = None,
) -> CaseOutcome:
    return CaseOutcome(
        label=label,
        kind=kind,
        base=SuiteRun(
            root="/base",
            verdicts={k: tuple(v) for k, v in base.items()},
            error=base_error,
        ),
        head=SuiteRun(
            root="/head",
            verdicts={k: tuple(v) for k, v in head.items()},
            error=head_error,
        ),
        selection=None if selection_error else make_selection(selected, corpus),
        selection_error=selection_error,
    )


# --------------------------------------------------------------------------- #
# score_case: what counts as a failure, and what counts as a miss
# --------------------------------------------------------------------------- #


def test_a_check_that_newly_fails_is_a_regression():
    score = score_case(
        outcome(base={"s0": [], "s1": []}, head={"s0": ["tools"], "s1": []}, selected=["s0"])
    )
    assert score.usable
    assert score.regressions == ("s0",)
    assert score.missed_regressions == ()


def test_a_regression_the_selector_skipped_is_a_miss():
    score = score_case(
        outcome(base={"s0": [], "s1": []}, head={"s0": ["tools"], "s1": []}, selected=["s1"])
    )
    assert score.regressions == ("s0",)
    assert score.missed_regressions == ("s0",), "the whole point of the tool"


def test_a_failure_that_was_already_there_is_not_a_regression():
    """A declared gap is not a new failure, and counting it would invent evidence."""
    score = score_case(
        outcome(base={"s0": ["tools"]}, head={"s0": ["tools"]}, selected=[])
    )
    assert score.regressions == ()
    assert score.changes == ()


def test_a_failure_that_disappeared_is_a_change_but_not_a_regression():
    """A vanished failure is a silent false fix: tracked, but not as a regression."""
    score = score_case(
        outcome(base={"s0": ["tools"]}, head={"s0": []}, selected=[])
    )
    assert score.regressions == ()
    assert score.changes == ("s0",)
    assert score.missed_changes == ("s0",)


def test_a_second_failing_check_on_an_already_failing_row_is_a_regression():
    score = score_case(
        outcome(base={"s0": ["tools"]}, head={"s0": ["tools", "promise-kept"]}, selected=[])
    )
    assert score.regressions == ("s0",)


def test_a_row_that_only_appears_at_head_is_handled():
    score = score_case(outcome(base={}, head={"s0": ["tools"]}, selected=[]))
    assert score.regressions == ("s0",)


# --------------------------------------------------------------------------- #
# Unusable cases are named, never counted as passes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"base_error": "import blew up"}, "base tree would not run"),
        ({"head_error": "syntax error"}, "head tree would not run"),
        ({"selection_error": "GitUnavailable: no git"}, "selector failed"),
    ],
)
def test_an_unmeasurable_case_is_excluded_with_its_reason(kwargs, fragment):
    score = score_case(
        outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=[], **kwargs)
    )
    assert not score.usable
    assert fragment in score.reason
    assert score.regressions == ()


def test_an_unusable_case_does_not_raise_the_published_recall():
    """The cases most likely to hide a miss must not be able to flatter the number."""
    good = score_case(
        outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"], label="good")
    )
    broken = score_case(
        outcome(
            base={"s0": []},
            head={"s0": ["tools"]},
            selected=[],
            label="broken",
            head_error="boom",
        )
    )
    calibration = aggregate(mutation=_study([good, broken]), evidence="mutation")
    assert calibration.regressions_total == 1, "only the measured case counts"
    assert len(calibration.unusable) == 1
    assert "broken" in [s.label for s in calibration.unusable]


# --------------------------------------------------------------------------- #
# Vacuous versus discriminating: the distinction the headline cannot survive
# --------------------------------------------------------------------------- #


def test_a_case_that_selected_everything_did_not_narrow():
    score = score_case(
        outcome(
            base={"s0": []},
            head={"s0": ["tools"]},
            selected=["s0", "s1", "s2", "s3"],
            corpus=4,
        )
    )
    assert score.narrowed is False


def test_a_failure_where_nothing_was_skipped_is_a_vacuous_confirmation():
    """It confirms only that the selector declined to narrow."""
    score = score_case(
        outcome(
            base={"s0": []},
            head={"s0": ["tools"]},
            selected=["s0", "s1", "s2", "s3"],
            corpus=4,
        )
    )
    calibration = aggregate(mutation=_study([score]), evidence="mutation")
    assert calibration.recall == 1.0, "the raw rate looks perfect"
    assert calibration.vacuous_confirmations == 1
    assert calibration.discriminating is False
    assert calibration.discriminating_recall is None
    assert not calibration.passed(), "and the gate must not be fooled by it"


def test_a_failure_where_something_was_skipped_is_discriminating():
    score = score_case(
        outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"], corpus=4)
    )
    calibration = aggregate(mutation=_study([score]), evidence="mutation")
    assert calibration.discriminating is True
    assert calibration.discriminating_total == 1
    assert calibration.discriminating_recall == 1.0
    assert calibration.passed()


def test_a_miss_in_a_narrowing_case_fails_both_recalls():
    score = score_case(
        outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s1"], corpus=4)
    )
    calibration = aggregate(mutation=_study([score]), evidence="mutation")
    assert calibration.recall == 0.0
    assert calibration.discriminating_recall == 0.0
    assert calibration.missed == (("case", "s0"),)
    assert not calibration.passed()


# --------------------------------------------------------------------------- #
# An empty denominator is unknown, never 1.0
# --------------------------------------------------------------------------- #


def test_no_failures_at_all_means_recall_is_undefined():
    """The exact shape of this repository's real history, and it must not read as a pass."""
    score = score_case(outcome(base={"s0": []}, head={"s0": []}, selected=["s0"]))
    calibration = aggregate(mutation=_study([score]), evidence="mutation")
    assert calibration.regressions_total == 0
    assert calibration.recall is None, "not 1.0 — there was nothing to catch"
    assert calibration.calibrated is False
    assert not calibration.passed()


def test_the_summary_says_undefined_rather_than_printing_a_number():
    calibration = aggregate(
        mutation=_study([score_case(outcome(base={"s0": []}, head={"s0": []}, selected=[]))]),
        evidence="mutation",
    )
    text = "\n".join(calibration.summary_lines())
    assert "undefined" in text
    assert "1.000" not in text.split("gate")[0]


def test_every_rate_in_the_summary_carries_its_denominator():
    """House rule: a naked percentage is a defect."""
    calibration = aggregate(
        mutation=_study(
            [score_case(outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"]))]
        ),
        evidence="mutation",
    )
    for line in calibration.summary_lines():
        if "caught" in line or "non-vacuous" in line or "confirmations" in line:
            assert "/" in line, f"rate without a denominator: {line!r}"


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def _study(scores):
    from lab.selection.calibrate import MutationStudy

    return MutationStudy(
        roots=("tablemate",),
        seed=0,
        enumerated=len(scores),
        strata={"number": len(scores)},
        sampled=(),
        scores=tuple(scores),
        base_rows=1,
    )


def _passing():
    return aggregate(
        mutation=_study(
            [score_case(outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"]))]
        ),
        evidence="mutation",
    )


def _missing():
    return aggregate(
        mutation=_study(
            [score_case(outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s1"]))]
        ),
        evidence="mutation",
    )


def _unmeasured():
    return aggregate(
        mutation=_study([score_case(outcome(base={"s0": []}, head={"s0": []}, selected=["s0"]))]),
        evidence="mutation",
    )


def _vacuous():
    return aggregate(
        mutation=_study(
            [
                score_case(
                    outcome(
                        base={"s0": []},
                        head={"s0": ["tools"]},
                        selected=["s0", "s1", "s2", "s3"],
                        corpus=4,
                    )
                )
            ]
        ),
        evidence="mutation",
    )


def test_the_gate_passes_a_measured_selector_that_missed_nothing():
    assert require_calibrated_selector(_passing(), ci=True) is not None


def test_the_gate_refuses_when_a_regression_was_missed():
    with pytest.raises(SelectorBelowThresholdError) as excinfo:
        require_calibrated_selector(_missing(), ci=True)
    assert "missed 1 of 1" in str(excinfo.value)


def test_the_gate_refuses_when_nothing_was_ever_measured():
    with pytest.raises(SelectorNotCalibratedError) as excinfo:
        require_calibrated_selector(_unmeasured(), ci=True)
    assert "never been measured" in str(excinfo.value)


def test_the_gate_refuses_a_recall_built_only_from_vacuous_confirmations():
    with pytest.raises(SelectorNotCalibratedError) as excinfo:
        require_calibrated_selector(_vacuous(), ci=True)
    assert "vacuous" in str(excinfo.value)


def test_outside_ci_the_gate_advises_instead_of_raising(caplog):
    with caplog.at_level("WARNING"):
        require_calibrated_selector(_missing(), ci=False)
    assert "advisory only" in caplog.text


def test_the_bypass_is_a_keyword_argument_and_it_shouts(caplog):
    with caplog.at_level("WARNING"):
        require_calibrated_selector(_missing(), ci=True, allow_uncalibrated=True)
    assert "SKIPPING TESTS ON AN UNPROVEN SELECTOR" in caplog.text


def test_no_environment_variable_can_open_the_gate(monkeypatch):
    """The override lives at the call site or nowhere. A shell can only make it stricter."""
    for name in (
        "LAB_SELECTOR_ALLOW_UNCALIBRATED",
        "SELECTOR_ALLOW_UNCALIBRATED",
        "LAB_SELECTION_MIN_RECALL",
        "ALLOW_UNCALIBRATED",
    ):
        monkeypatch.setenv(name, "1")
    with pytest.raises(SelectorBelowThresholdError):
        require_calibrated_selector(_missing(), ci=True)


def test_min_recall_defaults_to_one():
    assert MIN_RECALL == 1.0


def test_a_lowered_threshold_must_be_passed_explicitly():
    calibration = _missing()
    with pytest.raises(SelectorBelowThresholdError):
        require_calibrated_selector(calibration, ci=True)
    require_calibrated_selector(calibration, ci=True, min_recall=0.0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("", False), ("no", False)],
)
def test_ci_detection_reads_the_conventional_variables(monkeypatch, value, expected):
    monkeypatch.delenv("LAB_SELECTOR_CI", raising=False)
    monkeypatch.setenv("CI", value)
    assert in_ci_mode() is expected


def test_the_selector_ci_variable_wins_over_the_generic_one(monkeypatch):
    monkeypatch.setenv("LAB_SELECTOR_CI", "0")
    monkeypatch.setenv("CI", "1")
    assert in_ci_mode() is False


def test_ci_detection_agrees_with_the_judge_layers_definition(monkeypatch):
    """Restated rather than imported; a real divergence in meaning must fail loudly."""
    registry = pytest.importorskip("lab.judges.registry")
    for value in ("1", "true", "0", "", "no", "on"):
        monkeypatch.setenv("CI", value)
        monkeypatch.delenv("LAB_SELECTOR_CI", raising=False)
        monkeypatch.delenv("LAB_JUDGE_CI", raising=False)
        assert in_ci_mode() is registry.in_ci_mode(), value


# --------------------------------------------------------------------------- #
# Evidence selection
# --------------------------------------------------------------------------- #


def _history(scores):
    return HistoryStudy(
        repo_root="/repo",
        commits_examined=("a" * 40,),
        commits_runnable=("a" * 40,),
        unrunnable={},
        scores=tuple(scores),
    )


def test_history_only_evidence_ignores_the_mutation_study():
    """A site that will only gate on real commits gets a refusal, which is correct."""
    hit = score_case(outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"]))
    clean = score_case(outcome(base={"s0": []}, head={"s0": []}, selected=["s0"], kind="history"))
    calibration = aggregate(
        history=_history([clean]), mutation=_study([hit]), evidence="history"
    )
    assert calibration.regressions_total == 0
    assert calibration.recall is None
    with pytest.raises(SelectorNotCalibratedError):
        require_calibrated_selector(calibration, ci=True)


def test_all_evidence_combines_both_studies():
    hit = score_case(outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"]))
    clean = score_case(outcome(base={"s0": []}, head={"s0": []}, selected=["s0"], kind="history"))
    calibration = aggregate(history=_history([clean]), mutation=_study([hit]), evidence="all")
    assert len(calibration.scores) == 2
    assert calibration.regressions_total == 1


def test_an_unknown_evidence_name_is_refused():
    with pytest.raises(ValueError, match="evidence must be"):
        aggregate(evidence="whatever")


# --------------------------------------------------------------------------- #
# Mutants are derived, not declared
# --------------------------------------------------------------------------- #


def test_mutants_are_enumerated_from_the_real_tree():
    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    assert len(mutants) > 100, "a derived catalogue, not a hand-written list"
    assert {m.operator for m in mutants} >= {"number", "string", "bool", "compare"}


def test_every_mutant_applies_cleanly_and_changes_the_source(tmp_path):
    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    for mutant in mutants[:200]:
        source = (REPO_ROOT / mutant.path).read_text(encoding="utf-8")
        assert mutant.apply_to(source) != source


def test_a_mutant_applied_to_moved_source_refuses_rather_than_corrupting(tmp_path):
    mutant = Mutant(
        path="x.py",
        operator="number",
        lineno=1,
        start=0,
        end=1,
        qualname="<module>",
        before="1",
        after="2",
    )
    with pytest.raises(ValueError, match="source moved"):
        mutant.apply_to("something else entirely")


def test_the_mutated_source_still_parses():
    """A mutant that will not compile measures the parser, not the selector."""
    import ast

    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    sampled = sample_mutants(mutants, size=40, seed=0)
    for mutant in sampled:
        source = (REPO_ROOT / mutant.path).read_text(encoding="utf-8")
        ast.parse(mutant.apply_to(source))


def test_the_sample_is_stratified_so_one_operator_cannot_dominate():
    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    drawn = sample_mutants(mutants, size=40, seed=0)
    counts: dict[str, int] = {}
    for mutant in drawn:
        counts[mutant.operator] = counts.get(mutant.operator, 0) + 1
    assert len(counts) >= 4
    assert max(counts.values()) <= len(drawn) // 2, counts


def test_the_sample_is_reproducible_from_its_seed():
    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    first = [m.label for m in sample_mutants(mutants, size=20, seed=0)]
    again = [m.label for m in sample_mutants(mutants, size=20, seed=0)]
    other = [m.label for m in sample_mutants(mutants, size=20, seed=1)]
    assert first == again
    assert first != other, "a seed that changes nothing is not a seed"


def test_a_docstring_mutant_is_labelled_as_one_rather_than_dropped():
    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    assert any(m.operator == "docstring" for m in mutants)
    assert any(m.operator == "string" for m in mutants)


def test_enumeration_skips_a_file_that_will_not_parse(tmp_path):
    """Historical trees are walked too, and one bad file must not stop the study."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "broken.py").write_text("def (", encoding="utf-8")
    (tmp_path / "pkg" / "fine.py").write_text("X = 1\n", encoding="utf-8")
    mutants = enumerate_mutants(tmp_path, ("pkg",))
    assert [m.path for m in mutants] == ["pkg/fine.py"]


def test_enumeration_of_a_missing_root_is_empty_not_an_error():
    assert enumerate_mutants(REPO_ROOT, ("no_such_package",)) == []


def test_a_mutant_names_its_enclosing_definition():
    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    qualnames = {m.qualname for m in mutants}
    assert "<module>" in qualnames
    assert any("." in q for q in qualnames), "methods carry their class"


# --------------------------------------------------------------------------- #
# The artefact
# --------------------------------------------------------------------------- #


def test_the_report_records_the_command_and_the_date():
    calibration = aggregate(
        mutation=_study(
            [score_case(outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"]))]
        ),
        evidence="mutation",
        command="python -m lab.selection.calibrate",
        generated="2026-01-01",
    )
    payload = calibration.to_dict()
    assert payload["_provenance"]["command"] == "python -m lab.selection.calibrate"
    assert payload["_provenance"]["generated"] == "2026-01-01"
    assert payload["min_recall"] == MIN_RECALL


def test_the_report_is_json_serialisable_and_names_every_miss():
    calibration = aggregate(
        mutation=_study(
            [score_case(outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s1"]))]
        ),
        evidence="mutation",
    )
    payload = json.loads(json.dumps(calibration.to_dict(), sort_keys=True))
    assert payload["missed"] == [{"case": "case", "scenario": "s0"}]
    assert payload["recall"] == 0.0
    assert payload["passed"] is False


def test_the_history_study_states_what_would_make_it_sound_when_it_is_empty():
    study = _history(
        [score_case(outcome(base={"s0": []}, head={"s0": []}, selected=[], kind="history"))]
    )
    text = "\n".join(study.limitation())
    assert "denominator is zero" in text
    assert "WHAT WOULD MAKE THIS MEASUREMENT SOUND" in text
    assert "red" in text and "green" in text


def test_the_history_study_reports_its_evidence_when_it_has_some():
    study = _history(
        [
            score_case(
                outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"], kind="history")
            )
        ]
    )
    assert "1 historical failure" in "\n".join(study.limitation())


# --------------------------------------------------------------------------- #
# Nothing here needs a credential
# --------------------------------------------------------------------------- #


def test_importing_and_scoring_needs_no_key(monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_API_KEY",
        "LAB_KEY",
        "AZURE_OPENAI_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    score = score_case(outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s0"]))
    assert score.usable


def test_the_module_reads_no_environment_variable_but_the_ci_flags():
    """A clean clone with every key unset must behave identically."""
    source = (REPO_ROOT / "lab" / "selection" / "calibrate.py").read_text(encoding="utf-8")
    for marker in ("os.environ.get", "os.getenv"):
        for line in source.splitlines():
            if marker in line:
                assert "name" in line, f"unexpected environment read: {line.strip()}"


# --------------------------------------------------------------------------- #
# The expensive half: it really does run the suite
# --------------------------------------------------------------------------- #

requires_git = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("tar") is None,
    reason="needs git and tar to build a scratch checkout",
)


@requires_git
def test_the_baseline_suite_runs_offline_and_reports_rows(monkeypatch):
    for name in ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "LAB_KEY"):
        monkeypatch.delenv(name, raising=False)
    run = run_text_suite(REPO_ROOT)
    assert run.ok, run.error
    assert run.rows > 40, "the deterministic text tier"
    assert run.failing_rows, "the corpus declares known gaps; they should be red"


def test_a_tree_that_cannot_be_driven_is_recorded_not_raised(tmp_path):
    run = run_text_suite(tmp_path)
    assert not run.ok
    assert run.error


@requires_git
def test_a_real_mutation_is_measured_end_to_end():
    """One mutant, applied to a throwaway checkout, run, diffed and selected."""
    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    chosen = [m for m in mutants if m.path == "tablemate/agents.py"][:2]
    study = run_mutation_study(REPO_ROOT, mutants=chosen)
    assert len(study.scores) == 2
    assert study.enumerated > 0
    assert any(s.usable for s in study.scores)
    for score in study.scores:
        if score.usable:
            assert score.corpus_size > 0
            assert score.selected > 0, "fail safe: never an empty selection"


@requires_git
def test_the_history_study_runs_over_real_commits():
    study = run_history_study(REPO_ROOT, limit=2)
    assert len(study.commits_examined) == 3
    assert study.pairs_backtestable >= 1
    for score in study.scores:
        assert score.corpus_size > 0


@requires_git
def test_the_scratch_repo_never_writes_to_the_repository_it_measures():
    """The one thing this module must never do."""
    before = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    from lab.selection.calibrate import ScratchRepo

    with ScratchRepo(REPO_ROOT) as repo:
        assert (repo.root / "tablemate").is_dir()
        assert (repo.root / ".git").is_dir()
    after = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert before == after


# --------------------------------------------------------------------------- #
# The entry point
# --------------------------------------------------------------------------- #


def test_the_cli_exits_nonzero_when_it_could_not_measure(capsys):
    """A tool that exits 0 having learned nothing gets wired into CI and believed."""
    code = main(["--skip-history", "--skip-mutation", "--quiet"])
    captured = capsys.readouterr()
    assert code == 1
    assert "undefined" in captured.out


def test_the_cli_writes_the_artefact_where_it_is_told(tmp_path, capsys):
    target = tmp_path / "calibration.json"
    main(["--skip-history", "--skip-mutation", "--quiet", "--write", str(target)])
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["_provenance"]["command"].startswith("python -m lab.selection.calibrate")
    assert payload["recall"] is None


def test_the_cli_can_emit_json(capsys):
    main(["--skip-history", "--skip-mutation", "--quiet", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["calibrated"] is False
    assert payload["min_recall"] == MIN_RECALL


def test_the_module_is_runnable_as_a_command():
    completed = subprocess.run(
        [sys.executable, "-m", "lab.selection.calibrate", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0
    assert "miss rate" in completed.stdout


# --------------------------------------------------------------------------- #
# Stratification: the enriched rows must not be allowed to flatter the saving
# --------------------------------------------------------------------------- #


def test_the_observed_sites_are_the_ones_the_trace_map_resolved():
    from lab.selection.calibrate import observed_locations

    sites = observed_locations()
    assert sites, "the committed map resolves some names to source"
    assert all("::" in site for site in sites), "path::qualname, the shared vocabulary"


def test_only_mutants_at_observed_sites_are_enriched():
    from lab.selection.calibrate import mutants_at_observed_sites, observed_locations

    mutants = enumerate_mutants(REPO_ROOT, ("tablemate",))
    picked = mutants_at_observed_sites(mutants, observed_locations())
    assert 0 < len(picked) < len(mutants), "a real subset, not everything and not nothing"
    paths = {m.path for m in picked}
    assert paths <= {"tablemate/agents.py", "tablemate/tools.py"}


def test_a_method_inside_an_observed_class_counts_as_observed():
    """Stage 3 joins a nested qualname to its ancestor; this must agree with it."""
    from lab.selection.calibrate import mutants_at_observed_sites

    mutant = Mutant(
        path="tablemate/agents.py",
        operator="number",
        lineno=1,
        start=0,
        end=1,
        qualname="PolicyAgent.handle",
        before="1",
        after="2",
    )
    assert mutants_at_observed_sites([mutant], {"tablemate/agents.py::PolicyAgent"})


def test_an_unrelated_sibling_name_is_not_observed():
    """`check_policy_helper` is not part of `check_policy`; the dot carries the test."""
    from lab.selection.calibrate import mutants_at_observed_sites

    mutant = Mutant(
        path="tablemate/tools.py",
        operator="number",
        lineno=1,
        start=0,
        end=1,
        qualname="check_policy_helper",
        before="1",
        after="2",
    )
    assert not mutants_at_observed_sites([mutant], {"tablemate/tools.py::check_policy"})


def test_the_ancestor_rule_agrees_with_the_selectors_own():
    """Restated, not imported — so a real divergence has to fail somewhere."""
    from lab.selection import select as select_module
    from lab.selection.calibrate import _related

    for observed, changed in [
        ("A", "A"),
        ("A", "A.b"),
        ("A.b", "A"),
        ("A", "AB"),
        ("check_policy", "check_policy_helper"),
        ("A.b", "A.c"),
    ]:
        assert _related(observed, changed) == select_module._related(observed, changed), (
            observed,
            changed,
        )


def test_the_saving_is_reported_over_the_proportional_stratum_only():
    """An enriched row narrows by construction; pooling it would quote a saving
    the tool does not deliver on a representative change."""
    proportional = CaseScore(
        label="p",
        kind="mutation",
        usable=True,
        reason="measured",
        corpus_size=10,
        selected=10,
        stratum="proportional",
    )
    enriched = CaseScore(
        label="e",
        kind="mutation",
        usable=True,
        reason="measured",
        corpus_size=10,
        selected=2,
        stratum="observed-enriched",
    )
    calibration = aggregate(mutation=_study([proportional, enriched]), evidence="mutation")
    mean, corpus = calibration.selection_ratio
    assert (mean, corpus) == (10.0, 10), "the enriched row must not lower the mean"
    assert calibration.strata == {"proportional": 1, "observed-enriched": 1}


def test_recall_does_pool_every_stratum():
    """The saving is stratified; the safety number is not — a miss is a miss."""
    enriched = score_case(
        outcome(base={"s0": []}, head={"s0": ["tools"]}, selected=["s1"], corpus=4)
    )
    enriched = CaseScore(**{**enriched.__dict__, "stratum": "observed-enriched"})
    calibration = aggregate(mutation=_study([enriched]), evidence="mutation")
    assert calibration.regressions_total == 1
    assert calibration.recall == 0.0


def test_the_stratum_survives_into_the_report():
    score = CaseScore(
        label="e",
        kind="mutation",
        usable=True,
        reason="measured",
        corpus_size=10,
        selected=2,
        stratum="observed-enriched",
    )
    payload = aggregate(mutation=_study([score]), evidence="mutation").to_dict()
    assert payload["selection_mean_stratum"] == "proportional"
    assert payload["strata"] == {"observed-enriched": 1}


# --------------------------------------------------------------------------- #
# The recall's observable base
#
# The suite this study drives is smaller than the corpus the selector reasons
# over. Rows outside it cannot fail here, so a miss on one is never counted.
# For unmapped rows that is harmless — they are always selected. For mapped rows
# it is a blind spot, and a rate whose blind spot is unstated is a naked rate.
# --------------------------------------------------------------------------- #


def test_the_limitation_states_the_observable_base_with_its_denominator():
    from lab.selection.calibrate import MutationStudy

    study = MutationStudy(
        roots=("tablemate",),
        seed=0,
        enumerated=1,
        strata={"number": 1},
        sampled=(),
        scores=(),
        base_rows=47,
        corpus_rows=73,
        blind_excludable_rows=8,
    )
    text = " ".join(study.limitation())
    assert "47/73" in text
    assert "8" in text
    assert "18" in text  # 73 - 47 - 8, the rows that cannot be missed
    assert "blind spot" in text


def test_the_blind_spot_is_carried_into_the_recorded_artefact():
    from lab.selection.calibrate import MutationStudy

    study = MutationStudy(
        roots=("tablemate",),
        seed=0,
        enumerated=1,
        strata={},
        sampled=(),
        scores=(),
        base_rows=47,
        corpus_rows=73,
        blind_excludable_rows=8,
    )
    recorded = study.to_dict()
    assert recorded["base_rows"] == 47
    assert recorded["corpus_rows"] == 73
    assert recorded["blind_excludable_rows"] == 8


def test_the_observable_base_counts_mapped_rows_the_suite_cannot_drive():
    """A row the study never runs, but the selector may exclude, is the blind spot."""
    from lab.selection.calibrate import SuiteRun, _observability

    sizes = _observability(
        SuiteRun(root=".", verdicts={"happy-one": ()}),
        map_path=None,
    )
    assert sizes["corpus_rows"] > sizes["blind_excludable_rows"] >= 0
    # every mapped row the run did not drive must be counted, not assumed safe
    from lab.selection.trace_map import DEFAULT_MAP_PATH, load_trace_map

    expected = sum(
        1
        for r in load_trace_map(DEFAULT_MAP_PATH).scenarios
        if r.mapped and r.scenario_id != "happy-one"
    )
    assert sizes["blind_excludable_rows"] == expected


def test_an_unreadable_map_does_not_crash_the_study_but_warns(caplog):
    from lab.selection.calibrate import SuiteRun, _observability

    with caplog.at_level("WARNING"):
        sizes = _observability(SuiteRun(root="."), map_path="/nonexistent/map.json")
    assert sizes == {"corpus_rows": 0, "blind_excludable_rows": 0}
    assert "observable base" in caplog.text
