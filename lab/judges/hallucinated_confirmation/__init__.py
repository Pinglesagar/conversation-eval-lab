"""A worked judge iteration: v1 -> v2, in real numbers.

WHAT THIS DEMONSTRATES
----------------------
The whole point of `lab.judges` in one directory: a judge, the labelled set it
was measured against, two prompt versions, and the two calibration reports that
say which one is fit to gate a build. Everything here runs offline with no API
key.

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

The recorded verdicts are **synthetic**, generated in `dataset.py` and stamped
with the model id `synthetic/deterministic-stand-in`. See that module's docstring
for exactly what is and is not being claimed, and for the one-command path to
re-record against a real provider through the identical code.

WHAT CHANGED BETWEEN v1 AND v2
------------------------------
v1 (`prompt_v1.md`, 10 lines) asks "did the assistant hallucinate a
confirmation?" and takes PASS or FAIL. It is the prompt anybody writes first, and
it is not obviously wrong.

Reading its critiques on the items it got wrong showed one failure, six times
over: **v1 cannot tell an intention from a completion.** "I'll get that booked
for you now" — FAIL. "Shall I go ahead and confirm that?" — FAIL. A read-back
ending in "is that right?" — FAIL. It never missed a real claim; it just also
flagged every sentence in the neighbourhood.

v2 (`prompt_v2.md`) changes four things, all aimed at that one failure:

1.  **Defines the target** — a claim about an *accomplished* fact, with the tense
    called out explicitly.
2.  **Enumerates what does not count** — intention, question, condition,
    read-back, and describing a pre-existing booking, each with examples.
3.  **Requires a quotation.** "If you cannot quote the sentence, the answer is
    PASS." This single rule is what kills the false alarms: there is no sentence
    to quote in "shall I confirm?".
4.  **Narrows the scope** — judge the words, not the world, and not whether the
    call went well. A dropped action is explicitly declared out of scope, because
    a deterministic check owns it.

Note what v2 does *not* do: it does not chase the last error. One false positive
survives (`existing-booking-read-back`, where the assistant describes a booking
the caller already had), and it stays. It is a genuinely ambiguous utterance, the
label was a judgement call, and tuning a prompt until a 24-item set comes back
clean is how you produce a judge that scores 1.00 on its own calibration set and
has never been measured on anything else.

THE RESULT — SAME 24 ITEMS, SAME MODEL, ONLY THE PROMPT CHANGED
---------------------------------------------------------------
    metric                  v1                v2               delta
    true positive rate      1.000 (8/8)       1.000 (8/8)        0.000
    true negative rate      0.625 (10/16)     0.938 (15/16)     +0.312
    precision               0.571 (8/14)      0.889 (8/9)       +0.317
    F1                      0.727 (16/22)     0.941 (16/17)     +0.214
    raw agreement           0.750 (18/24)     0.958 (23/24)     +0.208
    Cohen's kappa           0.526             0.909            +0.383
    false positives         6                 1                    -5
    false negatives         0                 0                     0

    gate (TPR >= 0.85, TNR >= 0.85):   v1 FAILS on TNR      v2 PASSES

Three things in that table are worth more than the improvement itself:

*   **v1 has perfect recall and is still unusable.** It finds every defect and
    raises six false alarms out of fourteen alerts. Somebody has to read all
    fourteen, and after the third false alarm they stop reading. A gate on TPR
    alone would have passed it.
*   **Raw agreement understates the change; kappa doesn't.** Raw agreement moves
    0.750 -> 0.958 (+0.208) while kappa moves 0.526 -> 0.909 (+0.383) — because
    raw agreement was already collecting credit for the ten easy negatives that
    both versions got right. This is the same arithmetic that makes raw agreement
    flattering on imbalanced sets, seen from the other side.
*   **Both figures rest on 24 items.** One relabelled item moves TNR by 6 points.
    The honest reading of v2 is "no measured miss on eight positives, one false
    alarm in sixteen negatives", not "0.938".

FILES
-----
    prompt_v1.md          the naive prompt
    prompt_v2.md          the rewrite
    labels.jsonl          24 items: trace, human label, labeller's reason
    verdicts_v1.jsonl     recorded raw answers for v1, with prompt digests
    verdicts_v2.jsonl     recorded raw answers for v2
    calibration_v1.json   full report: matrix, rates, every disagreement
    calibration_v1.md     the same report, readable
    calibration_v2.json
    calibration_v2.md
    iteration.md          the v1 -> v2 delta table above, generated

Regenerate everything (offline, deterministic):

    python -m lab.judges.hallucinated_confirmation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from lab.judges.calibration import (
    CalibrationReport,
    CalibrationThresholds,
    LabelledTrace,
    calibrate,
    compare_reports,
    load_labels,
    write_labels,
)
from lab.judges.hallucinated_confirmation import dataset
from lab.judges.judge import (
    Judge,
    LiteLLMCompletion,
    PromptTemplate,
    ReplayJudge,
    ScriptedCompletion,
    model_from_env,
    record_verdicts,
)

__all__ = [
    "JUDGE_NAME",
    "DIR",
    "VERSIONS",
    "prompt_path",
    "prompt",
    "verdicts_path",
    "labels_path",
    "labels",
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

_RAW: dict[str, dict[str, str]] = {"v1": dataset.RAW_V1, "v2": dataset.RAW_V2}


def prompt_path(version: str) -> Path:
    """Where a prompt version lives. Prompts are text files, editable by anyone."""
    _check_version(version)
    return DIR / f"prompt_{version}.md"


def verdicts_path(version: str) -> Path:
    _check_version(version)
    return DIR / f"verdicts_{version}.jsonl"


def labels_path() -> Path:
    return DIR / "labels.jsonl"


def _check_version(version: str) -> None:
    if version not in VERSIONS:
        raise ValueError(f"unknown prompt version {version!r}; have {list(VERSIONS)}")


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
            model=model or dataset.SYNTHETIC_MODEL,
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


def calibrate_version(
    version: str, *, items: Sequence[LabelledTrace] | None = None
) -> CalibrationReport:
    """Calibrate one prompt version against the checked-in labels."""
    resolved = list(items) if items is not None else labels()
    return calibrate(judge(version), resolved, extra_notes=_notes_for(version))


def iteration_summary() -> str:
    """The v1 -> v2 delta table, computed from the checked-in fixtures."""
    items = labels()
    return compare_reports(
        calibrate_version("v1", items=items), calibrate_version("v2", items=items)
    )


# --------------------------------------------------------------------------- #
# Regenerating the artefacts
# --------------------------------------------------------------------------- #


def regenerate(
    *,
    out_dir: str | Path = DIR,
    live: bool = False,
    model: str | None = None,
) -> dict[str, Path]:
    """Rebuild labels, recordings and both calibration reports.

    Offline by default, using the synthetic answers in `dataset.py`. With
    `live=True` the recordings are produced by a real provider through
    `record_verdicts` — the identical code path, so nothing downstream changes and
    the reports are directly comparable with the committed ones.

    Deterministic: every file is byte-identical between runs, so `git status`
    after a regeneration tells you whether anything actually changed.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    items = dataset.labelled_items()
    dataset.check_preconditions(items)
    written: dict[str, Path] = {"labels": write_labels(items, directory / "labels.jsonl")}

    pairs = [(item.item_id, item.trace) for item in items]
    reports: dict[str, CalibrationReport] = {}

    for version in VERSIONS:
        source = (
            Judge(
                name=JUDGE_NAME,
                prompt=prompt(version),
                version=version,
                model=model or model_from_env(),
                completion=LiteLLMCompletion(),
                include_tools=False,
            )
            if live
            else Judge(
                name=JUDGE_NAME,
                prompt=prompt(version),
                version=version,
                model=model or dataset.SYNTHETIC_MODEL,
                completion=ScriptedCompletion(_RAW[version]),
                include_tools=False,
            )
        )
        recording_path = directory / f"verdicts_{version}.jsonl"
        record_verdicts(source, pairs, recording_path)
        written[f"verdicts_{version}"] = recording_path

        replayed = ReplayJudge(
            recording=recording_path,
            name=JUDGE_NAME,
            prompt=prompt(version),
            version=version,
            model=source.model,
            include_tools=False,
        )
        report = calibrate(replayed, items, extra_notes=_notes_for(version))
        reports[version] = report
        paths = report.write(directory, stem=f"calibration_{version}")
        written[f"report_{version}_json"] = paths["json"]
        written[f"report_{version}_md"] = paths["markdown"]

    comparison = directory / "iteration.md"
    comparison.write_text(
        compare_reports(reports["v1"], reports["v2"]), encoding="utf-8"
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
    f"Verdicts are synthetic recordings ({dataset.SYNTHETIC_MODEL}), not captured "
    "provider output. They demonstrate the calibration machinery offline; see "
    "lab/judges/hallucinated_confirmation/dataset.py.",
    "Single labeller, no second-rater agreement measured, so label noise is "
    "attributed to the judge.",
    "24 items is a small set: one relabelled negative moves TNR by roughly six "
    "points. Read the fractions, not the decimals.",
)

_VERSION_NOTES: dict[str, str] = {
    "v1": (
        "v1 is the naive prompt: one question, no definitions, no output contract. "
        "Its errors are all the same error — future-tense or interrogative wording "
        "read as a completed action."
    ),
    "v2": (
        "v2 defines the target, enumerates what does not count, and requires a "
        "quotable sentence. The single surviving false positive is a genuinely "
        "ambiguous utterance and was left alone rather than tuned away, because a "
        "prompt tuned until its own calibration set comes back clean has been fitted "
        "to that set."
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
            "Re-record verdicts against a real provider instead of the synthetic "
            "stand-in. Requires LAB_LIVE_JUDGE=1 and a model."
        ),
    )
    parser.add_argument("--model", default=None, help="litellm model route for --live.")
    args = parser.parse_args(argv)

    written = regenerate(out_dir=args.out, live=args.live, model=args.model)

    items = labels()
    v1 = calibrate_version("v1", items=items)
    v2 = calibrate_version("v2", items=items)

    print(v1.to_text())
    print()
    print(v2.to_text())
    print()
    print(compare_reports(v1, v2))

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
