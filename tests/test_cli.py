"""The CLI, and the invariants that keep the committed artefacts honest.

WHAT THIS DEMONSTRATES
----------------------
Two kinds of test live here, and the second kind is the interesting one.

The ordinary kind covers `lab.cli` itself: the caller fixture is validated rather
than trusted, the baseline diff is scoped to the scenarios that actually ran, a
written report can be read back and re-derives its own verdict, and the
subcommands return the exit codes their documentation claims.

The second kind tests the *case study's own paperwork* against the artefacts it
describes. `error_analysis/codes.csv` says which failure modes a contract caught;
`fixtures/replay_run/run_report.json` says which contracts failed. Those two files
are written by hand and by machine respectively, and nothing but a test keeps them
in agreement. Without it, the first fix to the system under test turns the error
analysis into confident fiction — the prose still says "caught by
`propagation:dairy`" long after that row went green. So:

    every codes.csv row marked caught=yes must be a failure in the report
    every row marked caught=no must not be

That pair of assertions is the reason a reader can believe the number this
project leads with (9/31 product occurrences caught): it is checked, in CI, on
every commit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from lab import cli
from lab.report import ContractStat, FailureRecord, RunReport
from lab.trace.build import TraceBuilder
from lab.clock import FakeClock
from scenarios.loader import load_corpus

REPO = cli.repo_root()
REFERENCE = REPO / cli.REFERENCE_RUN_DIR
REPORT_JSON = REFERENCE / "run_report.json"


# --------------------------------------------------------------------------- #
# The caller fixture is data, and data gets validated
# --------------------------------------------------------------------------- #


def test_the_committed_caller_fixture_loads() -> None:
    scripts = cli.load_caller_scripts(cli.DEFAULT_SCRIPTS)
    assert scripts, "the fixture is what makes the run reproducible; it cannot be empty"
    for script in scripts.values():
        assert script.script, script.scenario_id


def test_every_non_voice_scenario_has_a_committed_script() -> None:
    """A row with no script is silently not run, which is the worst kind of gap.

    The run report prints the coverage fraction, but a fraction nobody reads is
    not a guard. This is the guard: adding a text scenario without a caller
    script fails the suite instead of quietly shrinking the denominator.
    """
    from scenarios.loader import load_corpus

    corpus = load_corpus()
    text_rows = [
        s.id for s in corpus.scenarios if not (s.voice and s.voice.perturbations)
    ]
    scripts = cli.load_caller_scripts(cli.DEFAULT_SCRIPTS)
    missing = sorted(set(text_rows) - set(scripts))
    assert not missing, f"no caller script for {missing}"


def test_scripts_do_not_name_scenarios_that_do_not_exist() -> None:
    from scenarios.loader import load_corpus

    known = set(load_corpus().ids())
    scripts = cli.load_caller_scripts(cli.DEFAULT_SCRIPTS)
    assert not sorted(set(scripts) - known)


@pytest.mark.parametrize(
    "block, expected",
    [
        ("{}", "non-empty `scripts:` mapping"),
        ("scripts:\n  a: {script: []}", "non-empty list of lines"),
        ("scripts:\n  a: {script: ['hi'], nonsense: 1}", "unknown key"),
        ("scripts:\n  a: {script: ['  ']}", "non-empty string"),
        ("scripts:\n  a: {script: ['hi'], seed: [{teleport: {}}]}", "unknown seed action"),
        ("scripts:\n  a: {script: ['hi'], seed: {}}", "list of single-action mappings"),
    ],
)
def test_a_malformed_caller_fixture_is_an_error_not_a_short_conversation(
    tmp_path: Path, block: str, expected: str
) -> None:
    path = tmp_path / "scripts.yaml"
    path.write_text(block, encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)) as caught:
        cli.load_caller_scripts(path)
    assert expected in str(caught.value)


def test_a_missing_fixture_says_how_to_fix_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as caught:
        cli.load_caller_scripts(tmp_path / "nope.yaml")
    assert "--scripts" in str(caught.value)


def test_seed_steps_become_a_callable_that_touches_the_real_store() -> None:
    script = cli.CallerScript(
        scenario_id="x",
        script=("hello",),
        seed=({"book_out": {"date": "Saturday", "time": "8pm"}},),
    )
    from tablemate.store import default_restaurant

    store = default_restaurant()
    assert store.free_tables("saturday", "8pm", 2)
    seed = script.seed_fn()
    assert seed is not None
    seed(store)
    assert not store.free_tables("saturday", "8pm", 2), "the sitting must really be full"


def test_no_seed_means_no_callable() -> None:
    assert cli.CallerScript(scenario_id="x", script=("hi",)).seed_fn() is None


# --------------------------------------------------------------------------- #
# Classification: expected, unexpected, stale
# --------------------------------------------------------------------------- #


def _trace(scenario_id: str):
    builder = TraceBuilder(scenario_id=scenario_id, adapter="text:test", clock=FakeClock())
    builder.session_start()
    builder.caller_utterance("Table for six on Friday at 8pm please.")
    builder.agent_utterance("That is all booked in.", agent="BookingAgent")
    builder.session_end(reason="caller_hung_up", turns=1)
    return builder.build()


def test_a_declared_gap_that_reproduces_is_a_known_gap_not_a_gate_failure() -> None:
    from scenarios.loader import load_corpus

    scenario = load_corpus().by_id("edge-large-party-of-six")
    evaluation = cli.evaluate_trace(scenario, _trace(scenario.id))
    assert [r.name for r in evaluation.known_gaps] == ["tools", "promise-kept"]
    assert not evaluation.unexpected
    assert evaluation.gate_passed


def test_a_declared_gap_that_stops_reproducing_fails_the_gate() -> None:
    """The half of a regression gate that people leave out.

    A fixed defect and a check that quietly stopped applying look identical from
    the outside — one fewer failure — so both have to stop the build until
    somebody says which it was.
    """
    from scenarios.loader import load_corpus

    scenario = load_corpus().by_id("edge-large-party-of-six")
    builder = TraceBuilder(scenario_id=scenario.id, adapter="text:test", clock=FakeClock())
    builder.session_start()
    builder.caller_utterance("Table for six on Friday at 8pm please.")
    builder.tool_call(
        "create_booking",
        {"name": "Okonkwo", "date": "friday", "time": "8pm", "party_size": "6", "notes": ""},
        call_id="c1",
    )
    builder.tool_result("create_booking", {"booking_ref": "TM-2001"}, call_id="c1", ok=True)
    builder.agent_utterance("That is all booked in.", agent="BookingAgent")
    builder.session_end(reason="caller_hung_up", turns=1)

    evaluation = cli.evaluate_trace(scenario, builder.build())
    assert [r.name for r in evaluation.stale] == ["tools", "promise-kept"]
    assert not evaluation.gate_passed
    assert "STALE EXPECTATION" in (evaluation.gate_evidence() or "")


def test_an_undeclared_failure_is_unexpected() -> None:
    from scenarios.loader import load_corpus

    scenario = load_corpus().by_id("happy-party-of-five-boundary")
    evaluation = cli.evaluate_trace(scenario, _trace(scenario.id))
    assert evaluation.unexpected
    assert not evaluation.gate_passed
    assert (evaluation.gate_evidence() or "").startswith("UNEXPECTED")


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_identical_repeats_ignores_the_session_id_and_nothing_else() -> None:
    first, second = _trace("a"), _trace("a")
    assert cli._identical_repeats([first, second])

    third = _trace("a")
    third.events[1].payload["text"] = "something else"
    assert not cli._identical_repeats([first, third])


def test_one_repeat_is_trivially_identical() -> None:
    assert cli._identical_repeats([_trace("a")])


# --------------------------------------------------------------------------- #
# The baseline
# --------------------------------------------------------------------------- #


def _report(*findings: tuple[str, str]) -> RunReport:
    return RunReport(
        contracts=[ContractStat(name="tools", failures=len(findings), runs=len(findings) + 1)],
        failures=[
            FailureRecord(scenario_id=scenario, contract=contract, evidence="quoted")
            for scenario, contract in findings
        ],
    )


def test_baseline_diff_reports_both_directions(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(_report(("a", "tools"), ("b", "tools")).to_json(), encoding="utf-8")

    diff = cli.compare_to_baseline(_report(("a", "tools"), ("c", "tools")), path)
    assert diff.added == [("c", "tools")]
    assert diff.removed == [("b", "tools")]
    assert not diff.clean


def test_baseline_diff_is_scoped_to_the_scenarios_that_ran(tmp_path: Path) -> None:
    """`--suite happy` must not report the other three suites as fixed."""
    path = tmp_path / "baseline.json"
    path.write_text(_report(("a", "tools"), ("b", "tools")).to_json(), encoding="utf-8")

    diff = cli.compare_to_baseline(_report(("a", "tools")), path, scope=["a"])
    assert diff.clean
    assert diff.baseline_size == 1


def test_no_baseline_is_reported_rather_than_assumed_clean() -> None:
    diff = cli.compare_to_baseline(_report(), None)
    assert not diff.available
    assert "no baseline" in diff.describe()


# --------------------------------------------------------------------------- #
# The committed report round-trips, and re-derives its own verdict
# --------------------------------------------------------------------------- #


def test_the_committed_report_reloads_and_rerenders() -> None:
    report = cli.load_run_report(REPORT_JSON)
    assert report.verdict == json.loads(REPORT_JSON.read_text(encoding="utf-8"))["verdict"]
    assert report.to_markdown() == (REFERENCE / "run_report.md").read_text(encoding="utf-8")


def test_a_hand_edited_verdict_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    payload["verdict"] = "PASS"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="recomputed"):
        cli.load_run_report(path)


def test_the_committed_report_holds_no_absolute_paths() -> None:
    """A machine-specific path in a committed artefact breaks reproducibility."""
    text = REPORT_JSON.read_text(encoding="utf-8")
    assert str(REPO) not in text
    assert '"/Users' not in text and '"/home' not in text


# --------------------------------------------------------------------------- #
# The error analysis and the report have to agree
# --------------------------------------------------------------------------- #


def _committed_findings() -> set[tuple[str, str]]:
    report = cli.load_run_report(REPORT_JSON)
    return {(f.scenario_id, f.contract) for f in report.failures}


def test_codes_marked_caught_are_failures_in_the_committed_report() -> None:
    from error_analysis.pareto import load_codes

    findings = {scenario for scenario, _ in _committed_findings()}
    for coded in load_codes():
        if coded.caught:
            assert coded.scenario_id in findings, (
                f"{coded.code} on {coded.scenario_id} is coded as caught, but that "
                "scenario has no finding in the committed report"
            )


def test_codes_marked_uncaught_have_no_finding_of_their_own() -> None:
    """The other direction, which is the one that rots.

    A mode coded `caught=no` on a scenario that now reports a failure means
    either the suite improved or the coding was wrong. Both need a human, so the
    test fails rather than letting the prose drift.

    Scenarios carrying more than one code are exempt from this direction: a trace
    can hold one mode a contract caught and another it did not, and the report
    records the failure per contract rather than per mode.
    """
    from collections import Counter

    from error_analysis.pareto import load_codes

    codes = load_codes()
    per_scenario = Counter(c.scenario_id for c in codes)
    findings = {scenario for scenario, _ in _committed_findings()}
    for coded in codes:
        if not coded.caught and per_scenario[coded.scenario_id] == 1:
            assert coded.scenario_id not in findings, (
                f"{coded.code} on {coded.scenario_id} is coded as uncaught, but the "
                "committed report has a finding there"
            )


def test_the_pareto_arithmetic_matches_the_prose() -> None:
    """The numbers FINDINGS.md and axial_coding.md lead with."""
    from error_analysis.pareto import load_codes, pareto

    codes = load_codes()
    product = [c for c in codes if c.is_product]
    caught = [c for c in product if c.caught]
    assert (len(codes), len(product), len(caught)) == (32, 31, 9)

    rows = pareto(codes)
    assert rows[0].cumulative == rows[0].count
    assert rows[-1].cumulative == len(codes), "the cumulative column must reach n"
    assert [r.count for r in rows] == sorted((r.count for r in rows), reverse=True)


def test_every_coded_scenario_has_a_committed_trace() -> None:
    from error_analysis.pareto import load_codes

    for coded in load_codes():
        path = REFERENCE / "traces" / f"{coded.scenario_id}.jsonl"
        assert path.exists(), f"{coded.code} cites {coded.scenario_id} with no trace"


# --------------------------------------------------------------------------- #
# Subcommands and exit codes
# --------------------------------------------------------------------------- #


def test_validate_passes_on_the_committed_corpus(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["validate"]) == 0
    assert "55/55" in capsys.readouterr().out


def test_replay_recomputes_a_verdict_from_a_committed_trace(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The auditability claim, executed: no agent, no runner, same verdict."""
    trace = str(REFERENCE / "traces" / "edge-large-party-of-six.jsonl")
    assert cli.main(["replay", trace]) == 0
    out = capsys.readouterr().out
    assert "declared known gap(s): tools, promise-kept" in out


def test_replay_of_a_clean_trace_reports_no_findings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    trace = str(REFERENCE / "traces" / "happy-two-covers-thursday.jsonl")
    assert cli.main(["replay", trace, "--ci"]) == 0
    assert "0 with unexpected findings" in capsys.readouterr().out


def test_run_one_scenario_end_to_end(tmp_path: Path) -> None:
    code = cli.main(
        [
            "run",
            "--scenario",
            "happy-two-covers-thursday",
            "-k",
            "2",
            "--out",
            str(tmp_path),
            "--no-baseline",
            "--ci",
        ]
    )
    assert code == 0
    report = cli.load_run_report(tmp_path / "run_report.json")
    assert report.summary.stable_pass == 1
    assert (tmp_path / "traces" / "happy-two-covers-thursday.jsonl").exists()


def test_run_reports_a_gate_failure_on_an_undeclared_failure(tmp_path: Path) -> None:
    """The row where my own check is wrong is also the row that proves the gate."""
    code = cli.main(
        [
            "run",
            "--scenario",
            "happy-saturday-lunch-four",
            "-k",
            "1",
            "--out",
            str(tmp_path),
            "--no-baseline",
            "--ci",
        ]
    )
    assert code == 1


def test_run_refuses_an_empty_selection(tmp_path: Path) -> None:
    assert (
        cli.main(["run", "--suite", "voice", "--out", str(tmp_path), "--no-baseline"]) == 2
    )


def test_live_needs_an_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAB_LIVE_AGENT", raising=False)
    assert cli.main(["run", "--live", "--out", str(tmp_path), "--no-baseline"]) == 2


def test_recording_a_live_judge_needs_an_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Spending money needs the environment's agreement, not just a flag.

    `--record` is the only mode that calls a provider, so it is the mode the guard
    belongs on. The report must not exist afterwards: a refusal that still writes an
    artefact has produced a report whose provenance line is a guess.
    """
    monkeypatch.delenv("LAB_LIVE_JUDGE", raising=False)
    code = cli.main(
        [
            "run",
            "--live-judge",
            "--record",
            "--out",
            str(tmp_path),
            "--no-baseline",
            "--no-traces",
        ]
    )
    assert code == 2
    assert "LAB_LIVE_JUDGE" in capsys.readouterr().err
    assert not (tmp_path / "run_report.json").exists()


def test_recording_nothing_live_is_refused(tmp_path: Path, capsys) -> None:
    """`--record` with no live part records nothing, so it is a mistake, not a mode."""
    code = cli.main(
        ["run", "--record", "--out", str(tmp_path), "--no-baseline", "--no-traces"]
    )
    assert code == 2
    assert "records nothing" in capsys.readouterr().err


def test_live_judge_without_a_recording_abstains_rather_than_guessing(
    tmp_path: Path, capsys
) -> None:
    """No key, no recording, no verdicts — and the report says so in numbers.

    This is the property the old version of this test could not express, because
    `--live-judge` used to be a *labelling* flag: it set `abstained` to zero and
    `replayed_from_fixture` to false without a single call being made, which is a
    fabricated provenance claim. The flag now grades, so the honest outcome when
    there is nothing to grade from is an abstention on every selected session.
    """
    code = cli.main(
        [
            "run",
            "--suite",
            "happy",
            "--live-judge",
            "--judge-recording",
            str(tmp_path / "nothing-here.jsonl"),
            "--out",
            str(tmp_path),
            "--no-baseline",
            "--no-traces",
        ]
    )
    assert code == 0
    payload = json.loads((tmp_path / "run_report.json").read_text(encoding="utf-8"))
    judge = payload["judges"][0]
    assert judge["abstained"] == judge["judged"]
    assert judge["flagged"] == 0
    assert judge["replayed_from_fixture"] is True
    assert "abstained" in capsys.readouterr().err


def test_live_judge_replays_a_committed_recording_and_reports_real_flags(
    tmp_path: Path,
) -> None:
    """With a recording, the flag count in the report is the judge's own answer.

    Built by recording the judge against a scripted completion — the same
    `RecordingCompletion` path a live run uses — so the test exercises the
    replay code rather than a mock of it.
    """
    from lab.judges import hallucinated_confirmation as judge_pkg
    from lab.judges.judge import RecordingCompletion, ScriptedCompletion

    out = tmp_path / "run"
    recording_path = tmp_path / "verdicts.jsonl"

    # Drive once with no judge to learn which sessions the first stage selects.
    assert (
        cli.main(
            [
                "run",
                "--suite",
                "happy",
                "-k",
                "1",
                "--out",
                str(out),
                "--no-baseline",
                "--no-traces",
            ]
        )
        == 0
    )
    corpus = load_corpus()
    scripts = cli.load_caller_scripts(cli.DEFAULT_SCRIPTS)
    traces = []
    for scenario in corpus.scenarios:
        if scenario.suite != "happy" or scenario.id not in scripts:
            continue
        traces.append(
            cli._drive(
                scenario=scenario,
                script=scripts[scenario.id],
                build_agent=cli._import_object(cli.DEFAULT_AGENT_FACTORY),
                personas=corpus.personas,
                index=0,
                max_turns=14,
            )
        )
    candidates = cli._judge_candidates(traces)
    assert candidates, "the happy suite must contain at least one unmutated session"

    answer = 'VERDICT: FAIL\nCRITIQUE: said it was booked.\nEVIDENCE: "all booked in"'
    judge = judge_pkg.judge("v2", replay=False, model="test/scripted")
    judge._completion = ScriptedCompletion({t.session_id: answer for t in candidates})
    recorder = RecordingCompletion(judge.completion, judge=judge.name, prompt_version="v2")
    recording_judge = judge.with_completion(recorder)
    for trace in candidates:
        recording_judge.judge(trace, item_id=trace.session_id)
    recorder.recording.save(recording_path)

    assert (
        cli.main(
            [
                "run",
                "--suite",
                "happy",
                "-k",
                "1",
                "--live-judge",
                "--judge-recording",
                str(recording_path),
                "--out",
                str(out),
                "--no-baseline",
                "--no-traces",
            ]
        )
        == 0
    )
    payload = json.loads((out / "run_report.json").read_text(encoding="utf-8"))
    judge_block = payload["judges"][0]
    assert judge_block["judged"] == len(candidates)
    assert judge_block["flagged"] == len(candidates)
    assert judge_block["abstained"] == 0


def test_run_can_draw_the_heatmap_beside_the_report(tmp_path: Path, capsys) -> None:
    """`make demo` promises a heatmap PNG, so `run` has to be able to write one.

    The matrix table is printed whether or not a plotting backend is installed —
    the numbers are the finding, the picture is a convenience — so that half is
    asserted unconditionally and the PNG only where matplotlib is present.
    """
    out = tmp_path / "run"
    chart = tmp_path / "charts" / "handoff_heatmap.png"
    assert (
        cli.main(
            ["run", "--suite", "happy", "--out", str(out), "--no-baseline", "--heatmap", str(chart)]
        )
        == 0
    )
    assert "GreeterAgent" in capsys.readouterr().out
    pytest.importorskip("matplotlib", reason="matplotlib lives in the [dev] and [charts] extras")
    assert chart.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_run_summary_states_how_much_of_the_corpus_was_driven(
    tmp_path: Path, capsys
) -> None:
    """A pass rate over a self-selected subset is the harness flattering itself.

    The headline's denominator counts driven scenarios, which is smaller than the
    corpus, so the terminal has to say so next to it.
    """
    assert cli.main(["run", "--out", str(tmp_path), "--no-baseline", "--no-traces"]) == 0
    out = capsys.readouterr().out
    corpus_size = len(cli._import_module("scenarios.loader").load_corpus().scenarios)
    assert f"/{corpus_size} scenarios driven" in out
    assert "voice row(s) need the audio adapter" in out


def test_the_judge_gate_accepts_the_reported_judge() -> None:
    """`--ci` refuses an uncalibrated judge; the one this run reports clears it."""
    assert cli._audit_judges_for_ci() is True


def test_the_parser_documents_every_subcommand() -> None:
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert set(actions[0].choices) == {"run", "validate", "report", "calibrate", "replay"}


def test_a_missing_corpus_module_ends_in_a_sentence_not_a_stack() -> None:
    """The non-editable-install failure mode, reachable without one.

    `pip install .` (no `-e`) puts the library in site-packages, where the case
    study was never copied, and the old failure was a twenty-line traceback whose
    last word was `scenarios`. A person reading that cannot tell that the fix is
    a flag on the install command. The message has to say so.
    """
    with pytest.raises(SystemExit) as caught:
        cli._import_module("no_such_corpus_module")
    message = str(caught.value)
    assert "no_such_corpus_module" in message
    assert 'pip install -e ".[dev]"' in message
    assert "--corpus-module" in message
    assert "Traceback" not in message


def test_import_object_accepts_both_spellings() -> None:
    assert cli._import_object("tablemate.runtime:build_agent") is cli._import_object(
        "tablemate.runtime.build_agent"
    )


# --------------------------------------------------------------------------- #
# The unanimity caveat, and the arithmetic under it
#
# The caveat used to interpolate the run's real `k` and then hardcode the
# arithmetic for k=3, so at any other k the artefact said two different things
# about one run. Both committed live reports run at k=3, so the bug was invisible
# on every path anybody exercised. These tests pin the derivation away from 3.
# --------------------------------------------------------------------------- #


def test_wilson_lower_bound_reproduces_the_figures_quoted_in_the_tree() -> None:
    # Every one of these is quoted in prose somewhere in the repository; the
    # helper has to agree with the documents, not merely with itself.
    assert cli._wilson_lower_bound(3, 3) == pytest.approx(0.439, abs=0.0005)
    assert cli._wilson_lower_bound(5, 5) == pytest.approx(0.566, abs=0.0005)
    assert cli._wilson_lower_bound(8, 8) == pytest.approx(0.676, abs=0.0005)
    assert cli._wilson_lower_bound(16, 16) == pytest.approx(0.806, abs=0.0005)
    assert cli._wilson_lower_bound(2, 8) == pytest.approx(0.071, abs=0.0005)


def test_wilson_lower_bound_refuses_a_non_proportion() -> None:
    with pytest.raises(ValueError):
        cli._wilson_lower_bound(1, 0)
    with pytest.raises(ValueError):
        cli._wilson_lower_bound(4, 3)


def test_unanimity_caveat_is_computed_from_k_not_written_down() -> None:
    at_three = cli._unanimity_caveat(3)
    assert "3 passes out of 3" in at_three
    assert "0.44" in at_three and "0.56" in at_three

    at_five = cli._unanimity_caveat(5)
    assert "5 passes out of 5" in at_five
    assert "0.57" in at_five and "0.43" in at_five
    # The old bug in one assertion: the k=5 sentence must not talk about three.
    assert "three" not in at_five and " 3 " not in at_five

    assert cli._unanimity_caveat(1).startswith("1 pass out of 1")


def test_the_live_k_note_agrees_with_itself_at_k_other_than_three() -> None:
    selection = cli._Selection(
        scenarios=[], corpus_size=0, voice_skipped=[], unscripted=[], filtered_out=0
    )
    args = argparse.Namespace(repeats=5)
    notes = cli._notes(
        args=args,
        selection=selection,
        evaluations=[],
        contract_notes=[],
        candidates=[],
        non_deterministic=[],
        rig=cli.LiveRig(agent=True),
        sessions=0,
    )
    k_note = next(note for note in notes if note.startswith("k=5 with a live rig"))
    assert "5 passes out of 5" in k_note
    assert "0.57" in k_note
    assert "three passes out of three" not in k_note
    assert "0.44" not in k_note
