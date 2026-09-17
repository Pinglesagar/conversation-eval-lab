"""The command line: `evallab`, the one entry point a reviewer actually runs.

WHAT THIS DEMONSTRATES
----------------------
That the pieces compose. Everything else in `lab/` is a library with a test
suite; this module is where a corpus, an agent, a trace, a contract set, a judge
and a calibration verdict are wired into a single command that produces a report
and an exit code. Three decisions in here are the ones worth reading:

**1. The gate is about *change*, not about *correctness*.**
The system under test has real defects, so "did every check pass" is a question
whose answer is already known and therefore useless as a build gate. Two
verdicts are computed and printed, and neither is derived from the other:

    report verdict    FAIL while any contract fails at all — the product's state
    regression gate   PASS while nothing has changed since the committed baseline

The gate fails on a finding that is new, on a finding that has *disappeared*, on
a corpus `expected_failure` that stopped reproducing, and on a scenario whose k
repeats were not identical. The middle two are the ones people leave out. A
suite that only shouts about new failures lets a fixed defect sit in the
baseline for ever as a standing excuse, and — worse — cannot tell a fix from a
check that quietly stopped applying, because both look like one fewer failure.
So a fix fails the gate until the baseline is updated in the same change, which
forces somebody to say in a diff which of the two it was.

**2. `--replay` runs k repeats of a deterministic fixture, and says what that
measures.** It measures the harness, not the model: repeats of a scripted caller
against a scripted backend either come back byte-identical or the harness has a
reproducibility bug. That is worth measuring — it is checked and reported — but
it is not a variance measurement, and calling it one would be the kind of claim
this repo exists to avoid. Model variance needs `--live`.

**3. Offline, the judge abstains rather than guesses.** The recorded judge
verdicts are keyed to the prompts of its 24-item calibration set; there is no
recording for a trace it has never seen, and inventing one would put fabricated
verdicts in a report. So `run` selects the sessions the judge cascade *would*
grade, records that it abstained on all of them, and prints the judge's measured
TPR/TNR next to the abstention. An abstention is visible; a guess is not.

LAYERING
--------
`lab` is meant to be extractable into its own package, and the case study
(`roleplay`, `scenarios`, `fixtures`) is not part of it. So nothing here is
imported at module scope: the corpus loader, the agent factory and the caller
fixtures are resolved lazily, by dotted path, through `--corpus-module` and
`--agent-factory`. `import lab` therefore never pulls in the case study, and the
seam that will become a plugin point after the split is already the seam the
default values sit behind.

OUT OF SCOPE
------------
`run` drives the **text** adapter only. The voice suite's rows are loaded,
validated and counted, and then reported as not driven with their denominator
visible, because a perturbation row run as text produces a verdict that says
nothing about audio. The audio path lives in `lab.voice` and is exercised on its
own terms, not through this command.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from pydantic import ValidationError

from lab import __version__
from lab.checks import CheckReport, CheckResult
from lab.clock import FakeClock
from lab.report import (
    ContractStat,
    FailureRecord,
    JudgeCalibration,
    JudgeSummary,
    RunReport,
    VoiceMetrics,
)
from lab.simulator import RunOutcome, ScriptedCaller, StabilityVerdict, run_pass_k
from lab.stats import wilson_lower_bound
from lab.trace.io import read_jsonl, write_jsonl
from lab.trace.schema import EventKind, Trace

__all__ = [
    "DEFAULT_CORPUS_MODULE",
    "AGENT_MODEL_ENV_VAR",
    "DEFAULT_OUT_DIR",
    "LIVE_RUN_DIR",
    "LIVE_BASELINE",
    "MUTATING_TOOLS",
    "LIVE_AGENT_ADAPTERS",
    "build_of",
    "RunEvaluation",
    "build_parser",
    "main",
]

# --------------------------------------------------------------------------- #
# Where the case study lives. Overridable, because `lab` must not require it.
# --------------------------------------------------------------------------- #

#: Module exposing `load_corpus(root=None, strict=True)` and `CORPUS_ROOT`.
DEFAULT_CORPUS_MODULE: str = "roleplay.corpus"

#: Dotted path to a callable with `build_agent`'s keyword signature.

#: Where a live agent reads its litellm route from. A *name*, not an import from
#: the case study, for the layering reason above — the domain package holds the
#: same string on the other side of the seam, and a test asserts the two agree so
#: that renaming one without the other is a failure rather than a silent gap in
#: the self-grading check.
AGENT_MODEL_ENV_VAR: str = "LAB_AGENT_MODEL"

#: Caller lines, per scenario. A fixture in the same sense as a cassette: the
#: caller is part of the instrument, so its utterances are recorded and reviewed
#: rather than generated afresh on every run.

#: Generated output. `reports/` is gitignored; the committed reference run lives
#: in `fixtures/replay_run/` so that a reviewer can read a report without
#: running anything, and so CI can prove the run reproduces it byte for byte.
DEFAULT_OUT_DIR: str = "reports"
REFERENCE_RUN_DIR: str = "fixtures/replay_run"

#: Tools whose success means a reservation really changed. The first stage of the
#: judge cascade keeps only sessions where none of them succeeded.
MUTATING_TOOLS: tuple[str, ...] = ("create_booking", "modify_booking", "cancel_booking")

#: Default repeats. Three, so that determinism is measured rather than assumed;
#: see the module docstring for what k means under `--replay`.
DEFAULT_K: int = 3

_JUDGE_DIR = "lab/judges/hallucinated_confirmation"

#: Where a full-live run keeps its recordings. One directory, three kinds of
#: fixture, because a live run has three stochastic parts and each one has to be
#: replayable on its own terms:
#:
#:     agent_sessions.json   every (agent, message history, tool list) -> reply
#:     caller/<scenario>/    one cassette per repeat of the simulated caller
#:     judge_verdicts.jsonl  the judge's raw output per session it graded
#:
#: Committed, so `evallab run --live-*` with no key replays the exact run that was
#: paid for. That is the difference between a number in a README and evidence.
LIVE_RUN_DIR: str = "fixtures/live_full"
DEFAULT_CALLER_ROOT: str = f"{LIVE_RUN_DIR}/caller"
DEFAULT_JUDGE_RECORDING: str = f"{LIVE_RUN_DIR}/judge_verdicts.jsonl"
LIVE_BASELINE: str = f"{LIVE_RUN_DIR}/run_report.json"

#: Dotted path to the backend `--live-agent` hands the agent factory. The CLI knows
#: two things about it and no more: it is constructed with `cassette=`, `model=` and
#: `temperature=`, and the agent factory accepts it as `backend=`. What it does with
#: a model is the system under test's business, not the harness's.

#: Sampling temperature for a live *agent*. Not zero: an agent pinned to greedy
#: decoding is a different system from the one that answers the phone, and the
#: variance is part of what k repeats exist to measure.
#: so the two measurements are comparable.
DEFAULT_CALLER_TEMPERATURE: float = 0.7

#: The caller's turn budget under `--live-caller`, and the driver's hard stop above
#: it. Twelve is not a guess: `lab.simulator.flake_band` measured the same corpus at
#: eight and found the budget alone decided a verdict on one row.
DEFAULT_CALLER_BUDGET: int = 12


def repo_root() -> Path:
    """The checkout root, for resolving the case study's default paths."""
    return Path(__file__).resolve().parents[1]


def _resolve(path: str | Path) -> Path:
    """Absolute path, interpreting a relative one against the checkout root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root() / candidate




def _import_module(name: str) -> Any:
    """Import `name`, looking in the checkout as well as on `sys.path`.

    `lab` is installed as a package; the case study beside it — `scenarios`,
    `error_analysis`, the fixtures — deliberately is not, because it is not part
    of the library and has no business in a wheel. That layering is right and it
    has one sharp edge: a console script does not put the working directory on
    `sys.path`, so `evallab validate` would fail to find the corpus while
    `python -m lab.cli validate` found it, purely because of how it was invoked.

    So the checkout root is added on the retry, and only on the retry: an
    installed module of the same name still wins, and nothing is put on the path
    when nothing needed it.

    And when the retry fails too, that is the end of the road, so it ends in a
    sentence rather than a stack. The way to get here is a *non-editable* install
    — `pip install .` instead of `pip install -e .` — after which `repo_root()`
    points inside site-packages, where the case study was never copied because
    the packaging deliberately excludes it. The traceback that used to come out
    named `scenarios` and nothing else, which is the least useful half of the
    explanation.
    """
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        root = str(repo_root())
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            raise SystemExit(
                f"cannot import '{name}', and it is not beside the library either "
                f"(looked in {root}).\n"
                "The case study — scenarios/, roleplay/, fixtures/ — "
                "ships in the checkout, not in the wheel, so an installed copy has "
                "only the library.\n"
                "Install for development from a clone instead:\n"
                '    pip install -e ".[dev]"\n'
                "or point the command at your own corpus with --corpus-module."
            ) from exc


def _import_object(dotted: str) -> Any:
    """Import `pkg.mod:name` (or `pkg.mod.name`) and return the object."""
    module_name, _, attribute = dotted.partition(":")
    if not attribute:
        module_name, _, attribute = dotted.rpartition(".")
    return getattr(_import_module(module_name), attribute)


# --------------------------------------------------------------------------- #
# The caller fixture
# --------------------------------------------------------------------------- #




_SEED_ACTIONS = frozenset({"ensure_booking", "book_out"})




# --------------------------------------------------------------------------- #
# The live rig: which parts of the loop are a model on this run
# --------------------------------------------------------------------------- #








# --------------------------------------------------------------------------- #
# One run, and the classification of its verdicts
# --------------------------------------------------------------------------- #


@dataclass
class RunEvaluation:
    """A check report, split by whether anybody predicted each failure.

    The split is the whole point of the class: `report.ok` answers "is the agent
    correct", which for a system with three documented defects is a question
    whose answer is already known and therefore useless as a gate. `gate_passed`
    answers "did anything change", which is the question CI can act on.
    """

    scenario_id: str
    report: CheckReport
    unexpected: list[CheckResult] = field(default_factory=list)
    known_gaps: list[CheckResult] = field(default_factory=list)
    stale: list[CheckResult] = field(default_factory=list)
    unreproduced: list[CheckResult] = field(default_factory=list)

    @property
    def gate_passed(self) -> bool:
        return not self.unexpected and not self.stale

    def gate_evidence(self) -> str | None:
        """The quote a failing gate is reported with, or None when it passed."""
        for result in self.unexpected:
            quote = result.evidence[0].render() if result.evidence else result.detail
            return f"UNEXPECTED {result.name}: {quote.strip()}"
        for result in self.stale:
            return (
                f"STALE EXPECTATION {result.name}: declared as a known gap and did "
                f"not reproduce ({result.status.lower()}: {result.detail})"
            )
        return None

    def failed_check_names(self) -> list[str]:
        return [r.name for r in (*self.unexpected, *self.stale)]


#: Adapters in which a *model* held the agent's decision seat. Read off the trace
#: rather than passed in, so a committed fixture carries its own provenance: a trace
#: is evidence about the build that produced it, and six months later the command
#: line that produced it is gone.
LIVE_AGENT_ADAPTERS: frozenset[str] = frozenset({"text:live", "text:live-agent"})


def build_of(trace: Trace) -> str:
    """Which build of the system under test produced this trace.

    Only the *agent* side counts. `text:live-caller` is a live caller against the
    deterministic agent — `lab.simulator.flake_band`'s configuration — and the
    expectations in the corpus are predictions about the agent, so that run is
    scored against the scripted build's expectations. Getting this backwards would
    make every flake-band row report the seeded defects as undeclared regressions.
    """
    return "live" if trace.adapter in LIVE_AGENT_ADAPTERS else "scripted"


def evaluate_trace(scenario: Any, trace: Trace) -> RunEvaluation:
    """Run a scenario's contracts over a trace and classify each verdict.

    STALENESS IS A PROPERTY OF k REPEATS, NOT OF ONE
    -----------------------------------------------
    A declared gap that comes back PASS is either a fixed defect or a check that
    went quiet, and both need a human — that is what `stale` is for. On the
    deterministic build, one repeat settles it: all k are identical, so a gap that
    did not reproduce here did not reproduce at all.

    On a live build it settles nothing. A defect planted in a prompt is a tendency,
    and the first full live run of this corpus has one that fires in **2 of 3**
    repeats of `edge-modification-after-booking`. Classifying the third repeat as a
    stale expectation would mean the corpus's own answer key fails the gate for
    being probabilistic — and, worse, the same run would report the expectation as
    both reproduced and stale, which is not a verdict anybody can act on.

    So a live repeat that does not reproduce its declared gap records
    `unreproduced` instead, and `_scenario_level_stale` decides staleness once per
    scenario, from all k. `unreproduced` is not silence: it is what the rate in the
    report's notes is computed from, and a gap that reproduced 0/k still fails the
    gate.
    """
    report = scenario.contract_set().run(trace, scenario.check_context())
    evaluation = RunEvaluation(scenario_id=scenario.id, report=report)
    build = build_of(trace)
    stochastic = build == "live"
    for result in report.results:
        expected = scenario.expects_failure_of(result.name, build)
        if result.status in ("FAIL", "ERROR"):
            (evaluation.known_gaps if expected else evaluation.unexpected).append(result)
        elif expected:
            evaluation.unreproduced.append(result)
            if not stochastic:
                evaluation.stale.append(result)
    return evaluation




# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


#: One line per contract type, keyed by the class that produced the verdict, for
#: the report's "what it checks" column. A table of contract names with no
#: statement of what each one asserts is unreadable by anyone who did not write
#: the corpus — which is everyone the report is for.
CONTRACT_DESCRIPTIONS: dict[str, str] = {
    "ToolContract": "expected, forbidden and ordered tool calls, plus argument predicates",
    "PromiseContract": "every spoken commitment is backed by the call that would make it true",
    "NoReAskContract": "a fact the caller has already supplied is never asked for again",
    "FieldPropagationContract": "a supplied value survives the handoffs into the tool call",
    "NoProgressContract": "the same question is not put twice with nothing accomplished between",
    "PhraseContract": "phrases the agent must not say, and any it must",
}














# --------------------------------------------------------------------------- #
# Voice metrics and the calibration verdict behind them
# --------------------------------------------------------------------------- #












# --------------------------------------------------------------------------- #
# The judge stage
# --------------------------------------------------------------------------- #












# --------------------------------------------------------------------------- #
# The baseline: what this build was already known to get wrong
# --------------------------------------------------------------------------- #

Finding = tuple[str, str]  # (scenario_id, contract)

#: Keys `RunReport.to_dict()` materialises from properties so the JSON stands on
#: its own, listed here per level because they have to come back off before the
#: model (which forbids extras) will accept the file. Reloading also re-derives
#: them and compares — see `load_run_report`.
_DERIVED_KEYS: dict[str, frozenset[str]] = {
    "": frozenset({"verdict", "headline", "stability_summary", "integrity_gaps"}),
    "stability": frozenset({"pass_rate", "flake_rate", "passed"}),
    "contracts": frozenset({"applicable", "failure_rate"}),
    "judges": frozenset({"flag_rate", "tpr", "tpr_ci95", "tnr", "tnr_ci95"}),
    "voice": frozenset({"trustworthy"}),
}










# --------------------------------------------------------------------------- #
# `evallab run`
# --------------------------------------------------------------------------- #


















#: The two-sided 95% z. Written once, because every interval quoted anywhere in
#: this repository is a 95% one and a second literal would eventually disagree.
_Z_95 = 1.959963984540054












def _audit_judges_for_ci() -> bool:
    """Refuse to pass CI with an uncalibrated or below-threshold judge.

    The judge whose verdicts the report quotes is registered here with the
    calibration recomputed from the committed labels, and then put through the
    registry's gate in strict mode. Doing it in the CLI rather than trusting the
    committed `calibration_v2.json` is deliberate: the number in the report and
    the number the gate checks come from the same computation, so a stale
    artefact cannot let a judge through.
    """
    from lab.judges import hallucinated_confirmation as judge_pkg
    from lab.judges.registry import (
        CalibrationGateError,
        JudgeRegistry,
    )

    registry = JudgeRegistry()
    ok = True
    try:
        judge = judge_pkg.judge_v2(replay=True)
        registry.register(judge, calibration=judge_pkg.calibrate_version("v2"))
        registry.require_calibrated(judge, ci=True)
    except CalibrationGateError as exc:
        print(f"CI refuses the judge: {exc}", file=sys.stderr)
        ok = False
    for status in registry.audit():
        if not status.ok:
            print(
                f"CI refuses judge {status.name!r} ({status.version}): "
                + "; ".join(status.failures),
                file=sys.stderr,
            )
            ok = False
    return ok




# --------------------------------------------------------------------------- #
# `evallab validate`
# --------------------------------------------------------------------------- #




# --------------------------------------------------------------------------- #
# `evallab report`
# --------------------------------------------------------------------------- #




# --------------------------------------------------------------------------- #
# `evallab calibrate`
# --------------------------------------------------------------------------- #



def _portable(path: Path) -> str:
    """A path fit to be written into a committed artefact.

    Repo-relative where possible, absolute otherwise. An absolute path in a
    committed report makes the artefact machine-specific, and the point of
    committing it is that two machines produce the same bytes.
    """
    try:
        return str(path.relative_to(repo_root()))
    except ValueError:
        return str(path)


def _is_number(value: object) -> bool:
    """A real numeric measurement. `bool` is an `int` in Python and is not one."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _verdict_from_calibration_samples(payload: object) -> str:
    """Score a timing-calibration artefact from its own recorded samples.

    Returns PASS, FAIL, or NOT_RUN. NOT_RUN covers every way the artefact fails
    to be evidence: wrong shape, no delays, a delay with fewer than two samples
    (no standard deviation exists), a non-positive nominal delay. Silence is the
    honest answer there — the alternative is a verdict derived from nothing.

    The artefact's own tolerance is honoured only where it is at least as strict
    as `CalibrationTolerance()`'s defaults. Otherwise the numbers are real but
    the bar they cleared is not the harness's, and a report that printed PASS
    would be quoting a pass against a bar the artefact chose for itself. Since
    this report prints the verdict without the tolerance beside it, a looser bar
    is unreadable from the report and is refused rather than reported.
    """
    from lab.voice.calibration import CalibrationTolerance

    if not isinstance(payload, dict):
        return "NOT_RUN"
    rows = payload.get("delays")
    if not isinstance(rows, list) or not rows:
        return "NOT_RUN"
    try:
        tolerance = CalibrationTolerance.model_validate(payload.get("tolerance") or {})
    except ValidationError:
        return "NOT_RUN"
    stated = CalibrationTolerance()
    if (
        tolerance.max_rel_error > stated.max_rel_error
        or tolerance.max_stdev_s > stated.max_stdev_s
    ):
        return "NOT_RUN"

    verdict = "PASS"
    for row in rows:
        if not isinstance(row, dict):
            return "NOT_RUN"
        samples = row.get("samples_s")
        nominal = row.get("nominal_delay_s")
        if not isinstance(samples, list) or len(samples) < 2:
            return "NOT_RUN"
        if not all(_is_number(value) for value in samples):
            return "NOT_RUN"
        if not _is_number(nominal) or nominal <= 0:
            return "NOT_RUN"
        mean = statistics.fmean(samples)
        spread = statistics.stdev(samples)
        rel_error = (mean - nominal) / nominal
        if abs(rel_error) > tolerance.max_rel_error or spread > tolerance.max_stdev_s:
            verdict = "FAIL"
    return verdict


def _calibration_verdict(path: Path) -> tuple[str, str | None]:
    """Re-derive the timing gate's verdict from the artefact, or say it was never run.

    The verdict is recomputed from the recorded samples rather than read out of
    the artefact's `verdict` field, for the same reason `_audit_judges_for_ci`
    recomputes the judge's calibration instead of trusting
    `calibration_v2.json`: a stale or hand-edited artefact must not be able to
    put a PASS badge on a report. Reading a one-word claim would make every
    latency figure below it rest on a string somebody could have typed.

    The artefact carries every raw sample, so this needs no measurement — the
    tolerance and the per-delay samples in the file decide the verdict, and a
    file that cannot support one is `NOT_RUN`. (Re-running the gate outright
    costs a couple of milliseconds and would be defensible too, but then `run`
    would report a verdict about the machine it happened to run on rather than
    about the committed evidence a reader can inspect.)
    """
    if not path.exists():
        return "NOT_RUN", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "NOT_RUN", None
    return _verdict_from_calibration_samples(payload), _portable(path)


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Run the calibration gates. Both by default; either alone on request."""
    both = not (args.timing or args.judges)
    status = 0

    if args.timing or both:
        from lab.voice import calibration as timing

        print("== timing calibration ==")
        status |= timing.main(["--out", str(_resolve(args.out))])

    if args.judges or both:
        from lab.judges import hallucinated_confirmation as judge_pkg

        print()
        print("== judge calibration ==")
        status |= judge_pkg.main([])
        print()
        print("== judge gate ==")
        if not _audit_judges_for_ci():
            status |= 1
        else:
            print("  the reported judge clears the gate")

    # The exit code is the gate. `--ci` changes nothing about it and is accepted
    # only so the same command line works in both places.
    return 1 if status else 0


# --------------------------------------------------------------------------- #
# `evallab replay`
# --------------------------------------------------------------------------- #


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-check a committed trace, with no agent and no scenario runner involved.

    This is the auditability claim, executable: a verdict in a report either
    recomputes from the trace on disk or it was never evidence. It is also how a
    disagreement gets settled — the trace is the artefact, not the summary.
    """
    loader = _import_module(args.corpus_module)
    corpus = loader.load_corpus()

    paths = [Path(p) for p in args.traces]
    expanded: list[Path] = []
    for path in paths:
        resolved = _resolve(path)
        expanded.extend(sorted(resolved.glob("*.jsonl")) if resolved.is_dir() else [resolved])
    if not expanded:
        print("no trace files given", file=sys.stderr)
        return 2

    failures = 0
    for path in expanded:
        trace = read_jsonl(path)
        scenario_id = args.scenario or trace.scenario_id
        try:
            scenario = corpus.by_id(scenario_id)
        except KeyError:
            print(f"{path.name}: no scenario {scenario_id!r} in the corpus", file=sys.stderr)
            failures += 1
            continue
        evaluation = evaluate_trace(scenario, trace)
        report = evaluation.report
        print(f"{path.name}  ({scenario_id}, {len(trace.events)} events)")
        print(report.render(failures_only=args.failures_only))
        if evaluation.known_gaps:
            print(
                "  declared known gap(s): "
                + ", ".join(r.name for r in evaluation.known_gaps)
            )
        if not evaluation.gate_passed:
            failures += 1
        print()

    print(f"replayed {len(expanded)} trace(s); {failures} with unexpected findings")
    return 1 if failures and args.ci else 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _add_layering_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus-module",
        default=DEFAULT_CORPUS_MODULE,
        help=f"module exposing load_corpus/validate_corpus (default: {DEFAULT_CORPUS_MODULE})",
    )
    parser.add_argument("--corpus", default=None, help="corpus root directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evallab",
        description=(
            "Evaluation harness for conversational agents. Every subcommand runs "
            "offline with no API keys; live paths are opt-in behind an env var."
        ),
    )
    parser.add_argument("--version", action="version", version=f"evallab {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---------------------------------------------------------------- run
    calibrate = subparsers.add_parser(
        "calibrate", help="run the timing and judge calibration gates"
    )
    calibrate.add_argument("--timing", action="store_true", help="timing gate only")
    calibrate.add_argument("--judges", action="store_true", help="judge calibration only")
    calibrate.add_argument("--out", default="fixtures", help="where timing artefacts go")
    calibrate.add_argument("--ci", action="store_true", help="exit non-zero on failure")
    calibrate.set_defaults(func=cmd_calibrate)

    # ---------------------------------------------------------------- replay
    replay = subparsers.add_parser(
        "replay", help="re-check committed traces without running an agent"
    )
    _add_layering_args(replay)
    replay.add_argument(
        "traces",
        nargs="*",
        default=[f"{REFERENCE_RUN_DIR}/traces"],
        help="trace files or a directory of them",
    )
    replay.add_argument("--scenario", default=None, help="force a scenario id")
    replay.add_argument(
        "--failures-only", action="store_true", help="print only the checks that failed"
    )
    replay.add_argument("--ci", action="store_true", help="exit non-zero on any finding")
    replay.set_defaults(func=cmd_replay)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `evallab` console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
