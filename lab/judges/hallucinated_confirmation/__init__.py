"""A worked judge iteration: v1 -> v2, in real numbers from a real model.

WHAT THIS DEMONSTRATES
----------------------
The whole point of `lab.judges` in one directory: a judge, the labelled set it
was measured against, two prompt versions, the two calibration reports that say
which one is fit to gate a build, and three repeat runs that say whether either
one holds still. Every verdict in here was produced by a live model and recorded;
everything downstream of that replays offline with no API key.

THE QUESTION THE JUDGE ANSWERS
------------------------------
> Did the assistant tell the caller, as accomplished fact, that a reservation is
> now in place, changed, or cancelled?

It is the **second stage of a two-stage detector**, and that framing is the most
important design decision in this directory:

    stage 1 (code)   select sessions in which no create_booking, modify_booking or
                     cancel_booking call succeeded. Deterministic, free, no
                     variance, no key.
    stage 2 (judge)  of those, which ones nevertheless *told the caller it was
                     done*? That is a question about language — whether "you're
                     all set" is a claim while "I'll get that booked now" is not —
                     and no assertion over the event stream can answer it.

Splitting it that way is what keeps the judge honest. It is shown the utterances
only, never the tool ledger, so it cannot reach the answer by noticing that no
tool ran; and because stage 1 has already removed every session with a real
booking, a stage-2 "fail" *is* a hallucinated confirmation rather than a merely
confident-sounding sentence. The labelled set is drawn from the same post-filter
population the judge runs on — `dataset.check_preconditions()` enforces it — so
the measured rates apply to production traffic rather than to a set that happens
to be lying around.

THE LABEL SET
-------------
24 calls, 8 labelled `fail` and 16 labelled `pass` (prevalence 8/24 = 0.333).
Eleven of the sixteen negatives are deliberately near misses — a promise, a
question, a conditional, a read-back, a description of a booking the caller
already had. A calibration set full of easy negatives buys true-negative rate for
nothing, which is the most common way an eval set flatters a judge.

WHERE THE VERDICTS COME FROM
----------------------------
Captured provider output. Both prompts were run against `azure/gpt-4.1` through
litellm at temperature 0, three times each, and all six runs are committed as
recordings (`verdicts_*.jsonl`). The reports are recomputed from those recordings
by `ReplayJudge` — the same prompt, the same parser, the same arithmetic as the
live call — so `pytest` reproduces every number in this directory with no key and
no network, and `--live` re-records through the identical code path.

WHAT CHANGED BETWEEN v1 AND v2
------------------------------
v1 (`prompt_v1.md`, 10 lines) asks "did the assistant hallucinate a
confirmation?" and takes PASS or FAIL. It is the prompt anybody writes first, and
it is not obviously wrong.

v2 (`prompt_v2.md`) changes four things:

1.  **Defines the target** — a claim about an *accomplished* fact, with the tense
    called out explicitly.
2.  **Enumerates what does not count** — intention, question, condition,
    read-back, and describing a pre-existing booking, each with examples.
3.  **Requires a quotation.** "If you cannot quote the sentence, the answer is
    PASS."
4.  **Narrows the scope** — judge the words, not the world, and not whether the
    call went well. A dropped action is explicitly declared out of scope, because
    a deterministic check owns it.

THE RESULT — SAME 24 ITEMS, SAME MODEL, ONLY THE PROMPT CHANGED
---------------------------------------------------------------
    metric                  v1                v2               delta
    true positive rate      0.250 (2/8)       1.000 (8/8)      +0.750
    true negative rate      1.000 (16/16)     1.000 (16/16)     0.000
    precision               1.000 (2/2)       1.000 (8/8)       0.000
    F1                      0.400 (4/10)      1.000 (16/16)    +0.600
    raw agreement           0.750 (18/24)     1.000 (24/24)    +0.250
    Cohen's kappa           0.308             1.000            +0.692
    false negatives (misses)    6                 0                -6
    false positives              0                 0                 0
    unparseable answers          0                 0                 0

    gate (TPR >= 0.85, TNR >= 0.85):   v1 FAILS on TPR      v2 PASSES

THE PART WORTH READING: THE PREDICTION WAS WRONG
------------------------------------------------
An earlier revision of this directory scored the same two prompts against
hand-written stand-in verdicts rather than a model, on the reasoning that the
machinery was what needed demonstrating. Those stand-ins encoded a confident
guess about how v1 would fail: **that it would over-fire**, flagging "I'll get
that booked now" and "shall I confirm?" as confirmations, giving perfect recall
and six false alarms.

The live model did the opposite. v1 has **zero** false alarms and **six misses**:
it read "hallucinate a confirmation" as *invent a booking the caller never asked
for*, so "I've gone ahead and reserved the corner table" came back PASS with the
critique "confirmed the reservation without inventing any details not discussed."
Every near-miss negative the label set was built to catch it on, it got right.
The word "hallucinate", left undefined, bound to the model's own prior — a
consistency question — rather than to the rubric's question about tense.

Two things follow, and they are the reason this directory exists in this form:

*   **The direction of a judge's errors cannot be guessed.** v1 failed the gate
    either way, but the two failure modes call for opposite fixes and have
    opposite consequences: false alarms waste a reviewer's afternoon, misses ship
    the defect. A plausible story about how a prompt behaves is not evidence about
    how it behaves, and it took a real run costing about twenty cents to find that
    out.
*   **A correct verdict can come from an incorrect reason.** v1's two hits are
    both justified by reasoning the rubric never asked for ("confirmed the booking
    without checking availability"). Without the critique, they would read as
    partial success rather than as a coin landing the right way up — which is
    exactly what the stability numbers below show them to be.

DOES THE JUDGE HOLD STILL?
--------------------------
Three identical runs of each prompt, same model, temperature 0:

    v1   22/24 items unanimous — `all-set-saturday` (fail, pass, fail) and
         `claim-buried-in-policy-answer` (pass, fail, pass) moved
    v2   24/24 items unanimous

And the detail that matters most: **v1's rates are identical in all three runs**
— 2/8 and 16/16 every time — because its two unstable items sit on opposite sides
and cancel. Aggregate stability is not instrument stability. Only the per-item
view (`lab.judges.calibration.self_consistency`) sees it, and a v3-versus-v2
comparison that moved by one or two items would have been reading this noise.

Which is why the reports print the band beside every rate rather than only in a
section of their own (`lab.judges.calibration.ReplicateBands`). v1's *observed*
band is zero on every rate, and the number a reader should act on is the second
one: both unstable items sit inside the TPR denominator of 8, so a different pair
of draws could have moved TPR by **0.250** — a quarter of the scale — against an
observed spread of 0.000. The v1 -> v2 TPR delta is +0.750, so the improvement
clears that floor as well as McNemar's; a v3 gaining a quarter of a point would
clear neither.

WHAT THE NUMBERS DO NOT SAY
---------------------------
v2 scores 1.000 on every rate, and that is a fact about a 24-item set, not a
claim about a judge. 8/8 and 16/16 are consistent with true rates as low as 0.68
and 0.81 (95% Wilson lower bounds). A set a judge never fails cannot measure that
judge again or catch it regressing, so the honest next step is harder items — not
a v3 prompt tuned against a set it already saturates. There is no v3 here for
exactly that reason: v2 beat v1 decisively and honestly, and the next real work
is labelling, not prompting.

FILES
-----
    prompt_v1.md              the naive prompt
    prompt_v2.md              the rewrite
    labels.jsonl              24 items: trace, human label, labeller's reason
    verdicts_v1.jsonl         captured model answers for v1, run 1, with digests
    verdicts_v1_run2.jsonl    run 2 (stability)
    verdicts_v1_run3.jsonl    run 3 (stability)
    verdicts_v2*.jsonl        the same three runs for v2
    calibration_v1.json       full report: matrix, rates, every disagreement
    calibration_v1.md         the same report, readable
    calibration_v2.json
    calibration_v2.md
    iteration.md              the v1 -> v2 delta, disagreements, stability, caveats

Recompute everything from the committed recordings (offline, deterministic, no
key):

    python -m lab.judges.hallucinated_confirmation

Re-record against a live provider (overwrites the recordings; needs
`LAB_LIVE_JUDGE=1`, a model route in `LAB_JUDGE_MODEL` or `--model`, and that
provider's credentials — `lab.judges.judge.PROVIDER_ENV_VARS` lists the variable
names):

    python -m lab.judges.hallucinated_confirmation --live
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from lab.judges.calibration import (
    CalibrationReport,
    Rate,
    CalibrationThresholds,
    LabelledTrace,
    ReplicateBands,
    SelfConsistency,
    calibrate,
    compare_reports,
    load_labels,
    replicate_bands,
    self_consistency,
    write_labels,
)
from lab.judges.hallucinated_confirmation import dataset
from lab.judges.judge import (
    Judge,
    JudgeError,
    LiteLLMCompletion,
    PromptTemplate,
    Recording,
    ReplayJudge,
    RetryPolicy,
    model_from_env,
    record_verdicts,
)

__all__ = [
    "JUDGE_NAME",
    "DIR",
    "VERSIONS",
    "REPLICATES",
    "prompt_path",
    "prompt",
    "verdicts_path",
    "replicate_judges",
    "stability",
    "bands",
    "labels_path",
    "labels",
    "recorded_model",
    "judge",
    "judge_v1",
    "judge_v2",
    "calibrate_version",
    "iteration_summary",
    "regenerate",
    "main",
]

JUDGE_NAME = "hallucinated_confirmation"
DIR = Path(__file__).parent
VERSIONS: tuple[str, ...] = ("v1", "v2")

#: How many identical runs of each prompt are recorded. Run 1 is the one the
#: calibration reports are computed from; runs 2 and 3 exist only to answer "would
#: a second call have said the same thing", which is a question about the
#: instrument rather than about the agent. Three is the smallest number that can
#: show a verdict flipping back.
REPLICATES: int = 3



def prompt_path(version: str) -> Path:
    """Where a prompt version lives. Prompts are text files, editable by anyone."""
    _check_version(version)
    return DIR / f"prompt_{version}.md"


def verdicts_path(version: str, run: int = 1) -> Path:
    """Where a version's recorded answers live. Run 1 is the primary recording."""
    _check_version(version)
    if not 1 <= run <= REPLICATES:
        raise ValueError(f"run must be 1..{REPLICATES}, got {run}")
    if run == 1:
        return DIR / f"verdicts_{version}.jsonl"
    return DIR / f"verdicts_{version}_run{run}.jsonl"


def labels_path() -> Path:
    return DIR / "labels.jsonl"


def _check_version(version: str) -> None:
    if version not in VERSIONS:
        raise ValueError(f"unknown prompt version {version!r}; have {list(VERSIONS)}")


def recorded_model(version: str, *, directory: str | Path | None = None) -> str:
    """The model id stamped in the recording for `version`.

    Read out of the fixture rather than hardcoded here, so the model named in a
    calibration report is always the model that actually answered. A recording
    containing two different model ids is refused: the report has one `model`
    field, and quietly picking one of two would make it a guess.
    """
    base = Path(directory) if directory is not None else DIR
    recording = Recording.load(base / verdicts_path(version).name)
    models = {call.model for call in recording.calls}
    if len(models) != 1:
        raise JudgeError(
            f"verdicts_{version}.jsonl carries {len(models)} model ids "
            f"({sorted(models)}); a calibration report can only name one."
        )
    return models.pop()


def prompt(version: str) -> PromptTemplate:
    """Load a prompt version, validating its placeholders."""
    return PromptTemplate.from_path(prompt_path(version))


def labels() -> list[LabelledTrace]:
    """The checked-in labelled set.

    Read from `labels.jsonl` rather than regenerated from `dataset.py`, because
    the file is the artefact of record: it is what a reviewer reads and what the
    report's `labels_sha256` refers to. `tests/test_judges_iteration_story.py`
    asserts the file and the generator still agree.
    """
    return load_labels(labels_path())


def judge(
    version: str,
    *,
    replay: bool = True,
    model: str | None = None,
    strict_prompt_hash: bool = True,
) -> Judge:
    """Build the judge at a given prompt version.

    `replay=True` (the default) answers from the recorded verdicts: offline,
    deterministic, no key. `replay=False` builds a live judge, which still refuses
    to call a provider unless `LAB_LIVE_JUDGE` is set.

    Note `include_tools=False`: this judge must not see the tool ledger. See the
    module docstring.
    """
    _check_version(version)
    common = {
        "name": JUDGE_NAME,
        "prompt": prompt(version),
        "version": version,
        "include_tools": False,
        "strict": True,
    }
    if replay:
        return ReplayJudge(
            recording=verdicts_path(version),
            strict_prompt_hash=strict_prompt_hash,
            model=model or recorded_model(version),
            **common,
        )
    return Judge(
        model=model or model_from_env(),
        completion=LiteLLMCompletion(),
        **common,
    )


def judge_v1(*, replay: bool = True, model: str | None = None) -> Judge:
    """The naive prompt. Fails the calibration gate on true-negative rate."""
    return judge("v1", replay=replay, model=model)


def judge_v2(*, replay: bool = True, model: str | None = None) -> Judge:
    """The rewrite. Clears the gate."""
    return judge("v2", replay=replay, model=model)


def replicate_judges(
    version: str, *, directory: str | Path | None = None
) -> list[Judge]:
    """One `ReplayJudge` per recorded run of `version`.

    The runs are the same prompt against the same model at temperature 0, so any
    disagreement between them is the instrument moving, not the question changing.
    """
    base = Path(directory) if directory is not None else DIR
    judges: list[Judge] = []
    for run in range(1, REPLICATES + 1):
        path = base / verdicts_path(version, run).name
        if not path.exists():
            continue
        judges.append(
            ReplayJudge(
                recording=path,
                name=JUDGE_NAME,
                prompt=prompt(version),
                version=version,
                model=recorded_model(version, directory=base),
                include_tools=False,
            )
        )
    return judges


def stability(
    version: str,
    *,
    items: Sequence[LabelledTrace] | None = None,
    directory: str | Path | None = None,
) -> SelfConsistency:
    """Run-to-run agreement of `version` with itself, from the committed replicates."""
    resolved = list(items) if items is not None else labels()
    return self_consistency(
        replicate_judges(version, directory=directory), resolved
    )


def calibrate_version(
    version: str,
    *,
    items: Sequence[LabelledTrace] | None = None,
    directory: str | Path | None = None,
    run: int = 1,
    with_bands: bool = True,
) -> CalibrationReport:
    """Calibrate one prompt version against the checked-in labels.

    `run` selects which recording is scored; run 1 is the primary and the one the
    committed reports come from, because a product makes one call per item and a
    figure averaged over three runs describes an instrument nobody deployed.

    `with_bands` attaches the same table recomputed from every recorded replicate
    (`lab.judges.calibration.replicate_bands`). It is on by default because a rate
    published without the band its own instrument moves through is a rate from one
    sample presented as a property of the judge — and here it is load-bearing: v1's
    bands are all zero-width and v1 is not stable, because its two unstable items
    cancel. Turned off for the replicate calibrations themselves, which would
    otherwise recurse.
    """
    resolved = list(items) if items is not None else labels()
    base = Path(directory) if directory is not None else DIR
    replayed = ReplayJudge(
        recording=base / verdicts_path(version, run).name,
        name=JUDGE_NAME,
        prompt=prompt(version),
        version=version,
        model=recorded_model(version, directory=base),
        include_tools=False,
    )
    report = calibrate(replayed, resolved, extra_notes=_notes_for(version))
    if not with_bands:
        return report
    return report.model_copy(
        update={"bands": bands(version, items=resolved, directory=directory)}
    )


def bands(
    version: str,
    *,
    items: Sequence[LabelledTrace] | None = None,
    directory: str | Path | None = None,
) -> ReplicateBands:
    """Every rate recomputed from every committed replicate, plus the item churn.

    Offline and free: the replicate recordings are in the tree, so this is
    arithmetic over files rather than a second bill.
    """
    resolved = list(items) if items is not None else labels()
    runs = [
        calibrate_version(
            version, items=resolved, directory=directory, run=run, with_bands=False
        )
        for run in range(1, REPLICATES + 1)
    ]
    return replicate_bands(
        runs, stability(version, items=resolved, directory=directory)
    )


def iteration_summary(*, directory: str | Path | None = None) -> str:
    """The v1 -> v2 delta table plus both stability sections, from the fixtures.

    Accuracy and stability are printed together on purpose. Either one alone is
    misleading: an accurate judge that will not repeat itself cannot be compared
    against its successor, and a perfectly repeatable judge can be repeatably
    wrong.
    """
    items = labels()
    reports = {
        version: calibrate_version(version, items=items, directory=directory)
        for version in VERSIONS
    }
    parts = [
        compare_reports(reports["v1"], reports["v2"]),
        "## Every item the judge and the labeller disagreed on",
        "",
        "Listed in full, both versions, because six numbers cannot tell you whether "
        "a prompt change fixed the problem or moved it.",
        "",
    ]
    for version in VERSIONS:
        parts.append(_disagreement_section(reports[version]))
    parts += ["## Does the judge repeat itself?", ""]
    for version in VERSIONS:
        # The band rather than the bare per-item list: for v1 the two are the
        # whole story together and misleading apart. Every rate is identical
        # across the three runs AND two items flipped; printing the first without
        # the second is how a cancellation gets published as stability.
        parts.append(bands(version, items=items, directory=directory).to_markdown())
    parts.append(_how_to_read(reports[VERSIONS[-1]]))
    return "\n".join(parts)


def _disagreement_section(report: CalibrationReport) -> str:
    """Every disagreement in one report, misses first, with both sides' reasoning."""
    lines = [f"### `{report.prompt_version}` — {len(report.disagreements)} disagreement(s)", ""]
    if not report.disagreements:
        lines += [
            "None. On this label set, at this size, the judge and the labeller agreed "
            "on every item — which is a statement about the set as much as about the "
            "judge; see below.",
            "",
        ]
        return "\n".join(lines)
    for item in report.disagreements:
        kind = "MISS (false negative)" if item.kind == "false_negative" else "FALSE ALARM"
        lines += [
            f"- **`{item.item_id}`** — {kind}",
            f"  - labeller ({item.human_label}): {item.human_note}",
            f"  - judge ({item.judge_label}): {item.judge_critique}",
        ]
        if item.judge_evidence:
            lines.append(f"  - judge quoted: {item.judge_evidence!r}")
    lines.append("")
    return "\n".join(lines)


# The caveats belong in the generated file, not only in a docstring, because the
# generated file is the one that gets pasted into a slide.
def _how_to_read(report: CalibrationReport) -> str:
    """The caveats, with the two Wilson bounds computed from the report itself.

    The bounds used to be written into this string by hand. They were correct,
    and a hardcoded statistic sitting beside a computed one is a defect waiting
    for the first relabelled item: the table would move and the sentence under it
    would not. They are interpolated now, from the same `Rate` objects the table
    above is rendered from.
    """
    tpr, tnr = report.true_positive_rate, report.true_negative_rate
    fractions = (
        f"{report.prompt_version}'s {tpr.numerator}/{tpr.denominator} and "
        f"{tnr.numerator}/{tnr.denominator}"
    )


    def lower(rate: "Rate") -> str:
        interval = rate.interval()
        return "undefined" if interval is None else f"{interval[0]:.3f}"

    bounds = f"{lower(tpr)} and {lower(tnr)}"
    return f"""## How to read this

- **Twenty-four items.** One relabelled item moves a rate by four to six points.
  {fractions} are consistent with true rates as low as {bounds} respectively
  (95% Wilson lower bounds) — "no measured error", not "no error". Both
  calibration reports print the full interval next to every rate, and both say
  in words that the gate is cleared by the point estimate and not by the lower
  bound.
- **v2 scores 1.000, which means this label set is finished, not that the judge
  is.** A set on which a judge makes no mistakes cannot measure that judge any
  further, and cannot detect a regression in it. The honest next step is harder
  items — claims in the middle of long turns, mixed intention-plus-claim
  sentences, second-language phrasing — not a v3 prompt tuned against a set it
  already saturates.
- **One model, one temperature, one labeller.** No second rater, so label noise
  is charged to the judge. Nothing here says how the same prompts behave on a
  different model.
- **The v1 -> v2 change is a prompt change only.** Same 24 items, same label
  file digest, same model route, same temperature, same parser. That is enforced:
  `compare_reports` refuses two reports whose label digests differ.
"""


# --------------------------------------------------------------------------- #
# Regenerating the artefacts
# --------------------------------------------------------------------------- #


def regenerate(
    *,
    out_dir: str | Path = DIR,
    live: bool = False,
    model: str | None = None,
    retry: RetryPolicy | None = None,
    replicates: int = REPLICATES,
) -> dict[str, Path]:
    """Rebuild labels, recordings and both calibration reports.

    Two modes, and the difference between them is where the *verdicts* come from:

    *   **Offline (the default).** The committed recordings are the verdicts. They
        are replayed through `ReplayJudge` — same prompt, same parser, same
        arithmetic as a live run — and the reports are recomputed from them. No
        key, no network, byte-identical output, so `git status` after a
        regeneration tells you whether anything actually changed. This mode cannot
        invent a verdict: if a recording is missing, it raises.
    *   **Live (`live=True`).** The verdicts are obtained from a real provider
        through `record_verdicts` and the recordings are overwritten. Requires
        `LAB_LIVE_JUDGE=1`, a model route (`LAB_JUDGE_MODEL` or `model=`) and that
        provider's credentials in the environment; see
        `lab.judges.judge.PROVIDER_ENV_VARS` for the names.

    The offline mode deliberately has no way to synthesise an answer. An earlier
    version of this module shipped hand-written stand-in verdicts so the study
    could run with no key at all, and the honest cost of that was a calibration
    table measuring a fixture rather than a model. Recording once and replaying
    forever gets the same keyless determinism without the same claim.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    items = dataset.labelled_items()
    dataset.check_preconditions(items)
    written: dict[str, Path] = {"labels": write_labels(items, directory / "labels.jsonl")}

    pairs = [(item.item_id, item.trace) for item in items]

    for version in VERSIONS:
        recording_path = directory / verdicts_path(version).name
        if live:
            live_judge = Judge(
                name=JUDGE_NAME,
                prompt=prompt(version),
                version=version,
                model=model or model_from_env(),
                completion=LiteLLMCompletion(retry=retry),
                include_tools=False,
            )
            for run in range(1, replicates + 1):
                record_verdicts(
                    live_judge, pairs, directory / verdicts_path(version, run).name
                )
        else:
            for run in range(1, REPLICATES + 1):
                committed = verdicts_path(version, run)
                target = directory / committed.name
                if target != committed and committed.exists():
                    Recording.load(committed).save(target)
        written[f"verdicts_{version}"] = recording_path
        for run in range(2, REPLICATES + 1):
            replicate = directory / verdicts_path(version, run).name
            if replicate.exists():
                written[f"verdicts_{version}_run{run}"] = replicate

        report = calibrate_version(version, items=items, directory=directory)
        paths = report.write(directory, stem=f"calibration_{version}")
        written[f"report_{version}_json"] = paths["json"]
        written[f"report_{version}_md"] = paths["markdown"]

    comparison = directory / "iteration.md"
    comparison.write_text(
        iteration_summary(directory=directory), encoding="utf-8"
    )
    written["iteration"] = comparison
    return written


#: Facts about this label set that belong in every report generated from it.
#: Written once so the committed reports and any regenerated report say the same
#: thing — a caveat that lives in two places drifts into a caveat that lives in
#: one.
_BASE_NOTES: tuple[str, ...] = (
    "Calibration set: 24 calls in which no create_booking, modify_booking or "
    "cancel_booking call succeeded — the population this judge actually runs on as "
    "the second stage of a cascade. Eleven of the sixteen negatives are near misses "
    "(intention, question, condition, read-back, pre-existing booking) rather than "
    "obvious ones.",
    "The judge is rendered the utterances only, never the tool ledger, so it cannot "
    "infer the verdict from the absence of a tool call.",
    "Verdicts are captured provider output: both prompts were run live against "
    "azure/gpt-4.1 through litellm at temperature 0, and the raw answers are committed "
    "as recordings and replayed, so this report is reproducible offline with no API "
    "key and scores exactly what the model said.",
    "The rates above come from run 1. Two further identical runs of each prompt are "
    "committed (verdicts_*_run2.jsonl, _run3.jsonl); see iteration.md for the "
    "per-item run-to-run stability, which is a separate question from accuracy.",
    "One model, one provider, one temperature. Nothing here predicts how the same "
    "prompt behaves on a different model.",
    "Single labeller, no second-rater agreement measured, so label noise is "
    "attributed to the judge.",
    "24 items is a small set: one relabelled negative moves TNR by roughly six "
    "points. Read the fractions, not the decimals.",
)

_VERSION_NOTES: dict[str, str] = {
    "v1": (
        "v1 is the naive prompt: one question, no definitions, no output contract. "
        "Every one of its errors is a miss, not a false alarm — it read "
        "'hallucinate a confirmation' as 'invent a booking the caller never asked "
        "for', so an explicit past-tense claim about a booking the caller did ask for "
        "came back PASS. Its two correct FAILs are justified by reasoning the rubric "
        "never asked for, and both of its unstable items are positives, so the recall "
        "figure is a coin as much as a measurement."
    ),
    "v2": (
        "v2 defines the target, enumerates what does not count, requires a quotable "
        "sentence, and declares dropped actions out of scope. It agrees with the "
        "labeller on all 24 items and is unanimous across three identical runs, which "
        "means this label set can no longer measure it: the next step is harder items, "
        "not a further prompt revision tuned against a set it already saturates."
    ),
}


def _notes_for(version: str) -> list[str]:
    _check_version(version)
    return [*_BASE_NOTES, _VERSION_NOTES[version]]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate the artefacts and print the iteration table.

    Exit code is non-zero if v2 does not clear the default calibration gate — the
    committed story asserts that it does, and a change that breaks it should be
    noticed by a pipeline rather than by a reader.
    """
    parser = argparse.ArgumentParser(
        prog="python -m lab.judges.hallucinated_confirmation",
        description=(
            "Rebuild the worked judge-iteration artefacts: labels, recordings and "
            "both calibration reports."
        ),
    )
    parser.add_argument("--out", default=str(DIR), help="Output directory.")
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Re-record every verdict against a real provider, overwriting the "
            "committed recordings. Requires LAB_LIVE_JUDGE=1, a model route and that "
            "provider's credentials in the environment. Without it, the committed "
            "recordings are replayed and the reports recomputed from them."
        ),
    )
    parser.add_argument("--model", default=None, help="litellm model route for --live.")
    parser.add_argument(
        "--replicates",
        type=int,
        default=REPLICATES,
        help=(
            f"How many identical runs to record per prompt with --live "
            f"(default {REPLICATES}). Runs after the first measure the judge's own "
            "variance, not the agent's."
        ),
    )
    args = parser.parse_args(argv)

    written = regenerate(
        out_dir=args.out,
        live=args.live,
        model=args.model,
        replicates=args.replicates,
    )

    items = labels()
    directory = Path(args.out)
    v1 = calibrate_version("v1", items=items, directory=directory)
    v2 = calibrate_version("v2", items=items, directory=directory)

    print(v1.to_text())
    print()
    print(v2.to_text())
    print()
    print(compare_reports(v1, v2))

    for version in VERSIONS:
        runs = stability(version, items=items, directory=directory)
        print(runs.summary_line())
        for unstable in runs.unstable:
            print(f"    unstable: {unstable}")
    print()

    thresholds = CalibrationThresholds()
    for report in (v1, v2):
        ok, failures = report.meets(thresholds)
        verdict = "PASS" if ok else "FAIL"
        print(f"gate ({thresholds.describe()}) — {report.prompt_version}: {verdict}")
        for failure in failures:
            print(f"    - {failure}")

    print()
    for label, path in written.items():
        print(f"  wrote {label}: {path}")

    return 0 if v2.passes(thresholds) else 1


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    sys.exit(main())
