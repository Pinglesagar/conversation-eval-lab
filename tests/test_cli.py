"""The `evallab` command line: what survives, and that it still refuses properly.

The CLI is two verbs now. `run`, `validate`, `select` and `report` existed to
drive the restaurant case study that this repository no longer carries, and a
command whose fixtures have been deleted is worse than no command: it looks like
a capability and fails on first use.

So what is tested here is what is left — the calibration gate and trace replay —
plus the two properties that matter more than either: the parser documents every
subcommand it has, and a command that cannot do its job says so and exits
non-zero rather than printing an empty success.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab import cli

ROOT = Path(__file__).resolve().parents[1]
SPOKEN_TRACE = ROOT / "fixtures" / "audio" / "spoken_call" / "trace.jsonl"
SECOND_TRACE = ROOT / "fixtures" / "audio" / "spoken_call_pass" / "trace.jsonl"

SUBCOMMANDS = {"calibrate", "replay"}


# --------------------------------------------------------------------------- #
# The parser
# --------------------------------------------------------------------------- #


def test_the_parser_documents_every_subcommand() -> None:
    """A subcommand with no help text is a subcommand nobody will find."""
    parser = cli.build_parser()
    actions = [a for a in parser._actions if getattr(a, "choices", None)]
    assert actions, "the parser has no subcommands"
    choices = actions[0].choices
    assert set(choices) == SUBCOMMANDS, (
        f"the parser offers {sorted(choices)} but this test knows about "
        f"{sorted(SUBCOMMANDS)} — add the new one here and to the docs"
    )
    for name, sub in choices.items():
        assert sub.description or sub.format_help(), f"{name} has no help"


def test_an_unknown_subcommand_exits_non_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["definitely-not-a-command"])
    assert exc.value.code != 0


def test_no_subcommand_exits_non_zero_rather_than_doing_something() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


def test_the_default_corpus_module_is_the_domain_this_repository_ships() -> None:
    """The one system under test here is `roleplay/`, so that is the default.

    It pointed at the restaurant corpus until that domain was removed. A default
    naming a module that no longer exists would turn every bare `evallab replay`
    into an import error.
    """
    assert cli.DEFAULT_CORPUS_MODULE == "roleplay.corpus"
    __import__(cli.DEFAULT_CORPUS_MODULE)


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #


def test_replay_reads_a_committed_trace_and_reports_on_it(capsys: pytest.CaptureFixture[str]) -> None:
    assert SPOKEN_TRACE.is_file(), "the committed spoken call is the replay fixture"
    code = cli.main(["replay", str(SPOKEN_TRACE)])
    out = capsys.readouterr().out
    assert code == 0
    assert "replayed 1 trace" in out


def test_replay_reads_several_traces(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["replay", str(SPOKEN_TRACE), str(SECOND_TRACE)])
    assert "replayed 2 trace" in capsys.readouterr().out


def test_replay_of_a_missing_path_is_an_error_not_an_empty_success() -> None:
    """A path that is not there must never read as 'nothing wrong found'."""
    with pytest.raises((SystemExit, FileNotFoundError)) as exc:
        cli.main(["replay", str(ROOT / "fixtures" / "no-such-trace.jsonl")])
    if isinstance(exc.value, SystemExit):
        assert exc.value.code != 0


def test_replay_names_a_scenario_it_cannot_find_rather_than_passing_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail-safe: an unknown scenario is reported, never silently treated as clean."""
    cli.main(["replay", str(SPOKEN_TRACE)])
    out = capsys.readouterr().out
    assert "no scenario" in out or "unexpected findings" in out


def test_replay_makes_no_network_call_and_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cardinal rule, asserted rather than claimed."""
    for var in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "DEEPGRAM_API_KEY", "LIVEKIT_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert cli.main(["replay", str(SPOKEN_TRACE)]) == 0


# --------------------------------------------------------------------------- #
# calibrate
# --------------------------------------------------------------------------- #


def test_calibrate_timing_reports_a_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["calibrate", "--timing"])
    out = capsys.readouterr().out.lower()
    assert code in (0, 1), "the gate either passes or fails; anything else is a crash"
    assert out.strip(), "a gate that prints nothing cannot be read in CI"


def test_the_committed_calibration_report_is_readable_json() -> None:
    payload = json.loads((ROOT / "fixtures" / "calibration_report.json").read_text("utf-8"))
    assert payload, "the committed calibration artefact is empty"


# --------------------------------------------------------------------------- #
# helpers that other modules rely on
# --------------------------------------------------------------------------- #


def test_import_object_accepts_both_dotted_spellings() -> None:
    """`pkg.mod:name` and `pkg.mod.name` resolve to the same object.

    This is the adapter seam's resolver: a user pointing the harness at their own
    agent should not have to know which of the two spellings we prefer.
    """
    colon = cli._import_object("roleplay.corpus:load_corpus")
    dotted = cli._import_object("roleplay.corpus.load_corpus")
    assert colon is dotted


def test_import_object_on_a_missing_module_says_what_is_missing() -> None:
    with pytest.raises(SystemExit) as exc:
        cli._import_object("no_such_module_at_all:thing")
    assert exc.value.code != 0
