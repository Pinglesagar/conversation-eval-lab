"""The live rubric scorer, calibrated — the worked study, with its fixtures.

WHAT THIS PACKAGE IS
--------------------
The answer to "how do you know your AI scoring is aligned with a human reviewer?"
carried out end to end on the advisory-coaching domain: a real model grading real
sessions against a versioned rubric, measured against a labelled set whose labels
are derived by rule, gated on a stated threshold, iterated once, and re-measured.

It is the applied half of `roleplay.livescorer`, and it deliberately mirrors
`lab.judges.hallucinated_confirmation` — same file layout, same record-once-replay-
forever discipline, same artefacts. Two studies in one shape is a claim that the
shape is the method rather than the example.

    labels.jsonl              the 27-item labelled set, traces included
    verdicts_v1.jsonl         run 1 of the rubric v1 recording (the primary)
    verdicts_v1_run2.jsonl    runs 2 and 3, for stability only
    verdicts_v1_run3.jsonl
    verdicts_v2*.jsonl        the same for the iterated rubric
    defect_probe.jsonl        full-session traces, for the seeded-defect probe
    calibration_v*.json/.md   the reports
    study.md                  the write-up, regenerated from the fixtures

Everything offline. `python -m roleplay.scorer_study` replays the committed
recordings and recomputes every number; `--live` re-records against a provider and
needs `LAB_LIVE_SCORER=1`, `LAB_SCORER_MODEL` and that provider's credentials.

THE TWO CONSUMERS OF ONE RECORDING
----------------------------------
Each recorded answer is a raw JSON string carrying five criteria and a verdict.
The binary half goes to `lab.judges` unchanged — `ReplayJudge` over the same rubric
prompt, `calibrate()` for the confusion matrix, `JudgeRegistry.require_calibrated`
for the gate. The card half goes to `roleplay.livescorer.parse_live_card` for the
criteria. One model call, two measurements, no possibility of the two disagreeing
about what the model said.

WHY THE PRIMARY RUN IS RUN 1 AND NOT AN AVERAGE OF THREE
--------------------------------------------------------
Because a product serves one call per session. A calibration figure computed from
a three-run consensus describes an instrument nobody deployed, and it flatters the
real one by exactly the amount of variance it hid. Runs 2 and 3 exist to *measure*
that variance (`stability`), never to reduce it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from lab.judges.calibration import (
    CalibrationReport,
    CalibrationThresholds,
    LabelledTrace,
    SelfConsistency,
    calibrate,
    compare_reports,
    self_consistency,
)
from lab.judges.judge import (
    Judge,
    JudgeError,
    Recording,
    ReplayJudge,
    RetryPolicy,
)
from lab.judges.registry import JudgeRegistry
from lab.trace.schema import Trace

from roleplay.labels import (
    build_rows,
    committed_labels,
    exclusion_summary,
    labelled_and_excluded,
    verify_pack,
    write_committed_labels,
)
from roleplay.livescorer import (
    LIVE_ENV_VAR,
    RUBRIC_VERSIONS,
    SCORER_NAME,
    LiveRubricScorer,
    ScoreParseError,
    live_completion,
    model_from_env,
    parse_live_card,
    record_scores,
    replay_completion,
    rubric_prompt,
)
from roleplay.scorer import CRITERIA, PASS_TOTAL, RubricScorer
from roleplay.scorer_study.stability import CriterionStability, stability_of

__all__ = [
    "DIR",
    "REPLICATES",
    "DEFECT_PROBE_ROWS",
    "labels",
    "verdicts_path",
    "recorded_model",
    "scorer",
    "judge",
    "calibrate_version",
    "calibration_variance",
    "stability",
    "criterion_stability",
    "card_stats",
    "CardStats",
    "gate",
    "defect_probe",
    "DefectProbe",
    "regenerate",
    "study_markdown",
    "main",
]

DIR = Path(__file__).resolve().parent

#: Identical runs recorded per rubric version. Run 1 is what the calibration
#: reports are computed from; 2 and 3 answer "would a second call have said the
#: same thing", which is a question about the instrument and not about the
#: trainee. Three is the smallest number that can show a verdict flipping *back*,
#: which is the difference between drift and noise.
REPLICATES: int = 3

#: The seeded-defect probe set: rows where `roleplay/SEEDED_DEFECTS.md` says a
#: specific defect fires, probed on the FULL session trace (score card and
#: feedback included) rather than the conversation-only trace the calibration
#: uses. See `defect_probe` for why that difference matters and what it costs.
DEFECT_PROBE_ROWS: tuple[tuple[str, str], ...] = (
    ("compliance-missing-risk-disclosure", "DEFECT-3"),
    ("compliance-no-real-risk-reassurance", "DEFECT-3"),
    ("compliance-explicit-unlicensed-advice", "DEFECT-3"),
    ("locale-es-mx-registered-spanish-disclosure", "DEFECT-3"),
    ("pitch-terse-customer-patient-probing", "DEFECT-2"),
    ("objection-praise-for-unasked-question", "DEFECT-2"),
    ("pitch-feature-dump-no-discovery", "DEFECT-2"),
    ("objection-lock-in-left-unanswered", "DEFECT-2"),
)


# --------------------------------------------------------------------------- #
# Paths and fixtures
# --------------------------------------------------------------------------- #


def _check_version(version: str) -> None:
    if version not in RUBRIC_VERSIONS:
        raise ValueError(
            f"unknown rubric version {version!r}; have {list(RUBRIC_VERSIONS)}"
        )


def verdicts_path(version: str, run: int = 1, *, directory: Path | None = None) -> Path:
    """Where a version's recorded answers live. Run 1 is the primary recording."""
    _check_version(version)
    if not 1 <= run <= REPLICATES:
        raise ValueError(f"run must be 1..{REPLICATES}, got {run}")
    base = directory if directory is not None else DIR
    stem = f"verdicts_{version}" if run == 1 else f"verdicts_{version}_run{run}"
    return base / f"{stem}.jsonl"


def labels() -> list[LabelledTrace]:
    """The checked-in labelled set: 27 items, each carrying its own trace."""
    return committed_labels()


def recorded_model(version: str, *, directory: Path | None = None) -> str:
    """The model id stamped in `version`'s primary recording.

    Read out of the fixture rather than hardcoded, so the model a report names is
    always the model that actually answered. Two different ids in one recording is
    refused: the report has one `model` field and picking one of two would make it
    a guess.
    """
    recording = Recording.load(verdicts_path(version, 1, directory=directory))
    models = {call.model for call in recording.calls}
    if len(models) != 1:
        raise JudgeError(
            f"verdicts_{version}.jsonl carries {len(models)} model ids "
            f"({sorted(models)}); a calibration report can only name one."
        )
    return models.pop()


# --------------------------------------------------------------------------- #
# Building the instruments
# --------------------------------------------------------------------------- #


def scorer(
    version: str,
    *,
    run: int = 1,
    replay: bool = True,
    model: str | None = None,
    directory: Path | None = None,
    strict_prompt_hash: bool = True,
) -> LiveRubricScorer:
    """The live scorer at one rubric version, replaying or live.

    `replay=True` (the default) answers from the committed recording: offline,
    deterministic, no key. `replay=False` builds the live one, which still refuses
    to reach a provider unless `LAB_LIVE_SCORER` is set.
    """
    _check_version(version)
    if replay:
        return LiveRubricScorer(
            completion=replay_completion(
                verdicts_path(version, run, directory=directory),
                strict_prompt_hash=strict_prompt_hash,
            ),
            model=model or recorded_model(version, directory=directory),
            rubric_version=version,
        )
    return LiveRubricScorer(
        completion=live_completion(),
        model=model or model_from_env(),
        rubric_version=version,
    )


def judge(
    version: str,
    *,
    run: int = 1,
    directory: Path | None = None,
    model: str | None = None,
) -> Judge:
    """The same recording, wearing the `lab.judges.Judge` interface.

    `include_tools=True` because the rubric tells the grader to read the disclosure
    register, which lives in the tool events — a judge whose prompt cites evidence
    it was never shown is measuring something other than the rubric. It must also
    match `LiveRubricScorer.include_tools`, or the rendered prompt differs, the
    digest differs, and the recording is correctly refused as stale.
    """
    _check_version(version)
    return ReplayJudge(
        recording=verdicts_path(version, run, directory=directory),
        name=SCORER_NAME,
        prompt=rubric_prompt(version),
        version=version,
        model=model or recorded_model(version, directory=directory),
        include_tools=True,
        strict=False,
    )


def _notes_for(version: str, items: Sequence[LabelledTrace]) -> list[str]:
    fails = sum(1 for item in items if item.label == "fail")
    return [
        "The instrument under measurement is a real model grading each session "
        f"against roleplay/rubric_{version}.md and returning five per-criterion "
        "scores, a verdict, a critique and a quoted span. The scripted scorer in "
        "roleplay/scorer.py is a different instrument and is measured separately.",
        "The positive class is 'fail': a scorer is a defect detector, and recall on "
        "the sessions a competent reviewer would stop is the figure that matters.",
        "Labels are derived by rule from each session's own ledgers — the disclosure "
        "register the product wrote and the compliance flags its own flagger raised "
        "— not written by hand next to the session. See roleplay/labels.py for the "
        "four rules.",
        f"{len(items)} items, {fails} of them labelled 'fail'. Items the rules could "
        "not settle were excluded rather than guessed; the exclusion list and the "
        "reason for each is printed with this report.",
        "Every item is graded by one call, as a product would. Runs 2 and 3 of the "
        "recording measure the instrument's own variance and are never averaged in.",
    ]


def calibrate_version(
    version: str,
    *,
    items: Sequence[LabelledTrace] | None = None,
    directory: Path | None = None,
    run: int = 1,
) -> CalibrationReport:
    """Calibrate one rubric version against the committed labels."""
    resolved = list(items) if items is not None else labels()
    return calibrate(
        judge(version, run=run, directory=directory),
        resolved,
        positive_label="fail",
        extra_notes=_notes_for(version, resolved),
    )


def stability(
    version: str,
    *,
    items: Sequence[LabelledTrace] | None = None,
    directory: Path | None = None,
) -> SelfConsistency:
    """Binary run-to-run agreement, from `lab.judges`, unmodified."""
    resolved = list(items) if items is not None else labels()
    return self_consistency(
        [judge(version, run=run, directory=directory) for run in range(1, REPLICATES + 1)],
        resolved,
    )


def criterion_stability(
    version: str,
    *,
    items: Sequence[LabelledTrace] | None = None,
    directory: Path | None = None,
) -> CriterionStability:
    """Per-item, per-criterion stability across the committed replicates.

    The measurement the binary figure cannot make. See
    `roleplay.scorer_study.stability` for why both are reported and never merged.
    """
    resolved = list(items) if items is not None else labels()
    per_run = []
    for run in range(1, REPLICATES + 1):
        graded = scorer(version, run=run, directory=directory)
        rows = []
        for item in resolved:
            live = graded.score_live(item.trace, item_id=item.item_id)
            rows.append(
                (
                    item.item_id,
                    item.label,
                    dict(live.card.criteria),
                    live.card.total,
                    live.card.verdict,
                    live.errored,
                )
            )
        per_run.append(rows)
    return stability_of(
        rubric_version=version,
        model=recorded_model(version, directory=directory),
        per_run=per_run,
    )


def calibration_variance(
    version: str,
    *,
    items: Sequence[LabelledTrace] | None = None,
    directory: Path | None = None,
) -> str:
    """Recompute the whole calibration table from each replicate, and show all three.

    The measurement that puts an error bar on the headline. A calibration report is
    computed from one call per item, which is the right thing to report because it
    is what a product does — and it means the table itself is a sample. If a second
    identical run yields a different table, then "TPR 1.000" is a property of a
    run rather than of the instrument, and every prompt-to-prompt comparison built
    on it is partly reading its own noise.

    This is cheap and almost never done. The recordings already exist; the only
    reason not to look is that the answer may spoil the headline.
    """
    resolved = list(items) if items is not None else labels()
    lines = [
        f"| run | TPR | TNR | precision | kappa | raw agreement | TP | FP | FN | TN |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    seen: set[tuple[int, int, int, int]] = set()
    for run in range(1, REPLICATES + 1):
        report = calibrate_version(version, items=resolved, directory=directory, run=run)
        cell = report.confusion
        key = (
            cell.true_positive,
            cell.false_positive,
            cell.false_negative,
            cell.true_negative,
        )
        seen.add(key)
        kappa = (
            f"{report.cohens_kappa:.3f}" if report.cohens_kappa is not None else "undefined"
        )
        lines.append(
            f"| {run} | {report.true_positive_rate} | {report.true_negative_rate} | "
            f"{report.precision} | {kappa} | {report.raw_agreement} | "
            f"{cell.true_positive} | {cell.false_positive} | {cell.false_negative} | "
            f"{cell.true_negative} |"
        )
    lines.append("")
    if len(seen) == 1:
        lines.append(
            f"All {REPLICATES} runs produce an identical confusion matrix. The table "
            "is reproducible — which is a statement about the table and not about the "
            "score cards behind it; see the card-stability section, where cards moved "
            "on items the table cannot distinguish."
        )
    else:
        lines.append(
            f"**The table is not reproducible.** {len(seen)} different confusion "
            f"matrices came out of {REPLICATES} identical runs, so any figure quoted "
            "from a single run — including the one printed above, which is run 1 — is "
            "a sample rather than a property of the instrument. A prompt comparison "
            "whose delta is smaller than this spread is measuring noise."
        )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Card-level statistics the binary report cannot carry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CardStats:
    """What the score cards say, beyond the bit the confusion matrix uses.

    Three numbers the binary report structurally cannot hold, each with its
    denominator:

    *   **parse failures** — items where no card could be read at all. Reported
        here as well as in the calibration report because this is the number that
        decides whether the other two mean anything.
    *   **internally inconsistent cards** — the model's verdict disagrees with its
        own five criteria against the stated threshold. The rubric licenses one
        direction of this (an outright failure over a passing total) and never the
        other, so the two are counted apart.
    *   **criterion agreement with the ledger** — for the two criteria that have
        structured ground truth, whether the model's score agrees with the register
        and the flagger. This is the only place the study can say *why* a verdict
        was right, rather than that it was.
    """

    rubric_version: str
    model: str
    n: int
    errored: int
    fail_over_passing_total: int
    pass_under_threshold: int
    disclosure_agree: int
    disclosure_n: int
    advice_agree: int
    advice_n: int
    notes: tuple[str, ...] = ()

    @property
    def inconsistent(self) -> int:
        return self.fail_over_passing_total + self.pass_under_threshold

    def to_markdown(self) -> str:
        def frac(num: int, den: int) -> str:
            return f"{num}/{den}" + (f" ({num / den:.3f})" if den else " (undefined)")

        return "\n".join(
            [
                f"### Score cards — `{self.rubric_version}`",
                "",
                "| measure | value | denominator |",
                "|---|---|---|",
                f"| cards that could not be parsed (ERRORED) | {self.errored} | {self.n} |",
                f"| verdict `fail` over a total at or above {PASS_TOTAL} | "
                f"{self.fail_over_passing_total} | {self.n} | ",
                f"| verdict `pass` under a total of {PASS_TOTAL} | "
                f"{self.pass_under_threshold} | {self.n} |",
                f"| `mandatory_disclosure` agrees with the register | "
                f"{frac(self.disclosure_agree, self.disclosure_n)} | items scored |",
                f"| `no_unlicensed_advice` agrees with the flagger | "
                f"{frac(self.advice_agree, self.advice_n)} | items scored |",
                "",
                "A `fail` over a passing total is the rubric working: a missing "
                "disclosure fails the session whatever it totals. A `pass` under the "
                "threshold has no licence in the rubric at all, and is counted "
                "separately for that reason.",
                "",
            ]
            + [f"- {note}" for note in self.notes]
            + [""]
        )


def card_stats(
    version: str,
    *,
    items: Sequence[LabelledTrace] | None = None,
    directory: Path | None = None,
    run: int = 1,
) -> CardStats:
    """Parse every recorded card for `version` and score the card-level measures.

    The two ledger comparisons use `roleplay.labels.rule_label`'s own inputs: an
    item labelled `fail` by R1 is an item with a missing disclosure, so a
    `mandatory_disclosure` score of 3 or 4 on it is a disagreement with the
    register. Deriving the expectation from the rule rather than recomputing it
    keeps one definition of "the disclosure was given" in the study.
    """
    from roleplay.labels import rule_label  # local: keeps the import graph shallow
    from roleplay.scorer import session_view

    resolved = list(items) if items is not None else labels()
    graded = scorer(version, run=run, directory=directory)

    errored = fail_high = pass_low = 0
    disc_agree = disc_n = adv_agree = adv_n = 0

    for item in resolved:
        live = graded.score_live(item.trace, item_id=item.item_id)
        if live.errored:
            errored += 1
            continue
        if not live.self_consistent:
            if live.card.total >= PASS_TOTAL:
                fail_high += 1
            else:
                pass_low += 1

        derived = rule_label(session_view(item.trace))
        # The register is complete unless R1 fired; the flagger fired iff R2 did.
        register_complete = derived.rule != "R1"
        flagged = derived.rule == "R2"

        disc_n += 1
        if (live.card.criteria["mandatory_disclosure"] >= 3) == register_complete:
            disc_agree += 1
        adv_n += 1
        if (live.card.criteria["no_unlicensed_advice"] == 4) == (not flagged):
            adv_agree += 1

    return CardStats(
        rubric_version=version,
        model=recorded_model(version, directory=directory),
        n=len(resolved),
        errored=errored,
        fail_over_passing_total=fail_high,
        pass_under_threshold=pass_low,
        disclosure_agree=disc_agree,
        disclosure_n=disc_n,
        advice_agree=adv_agree,
        advice_n=adv_n,
        notes=(
            "R2 items carry a flag by definition, and R1 items are the ones with a "
            "missing code, so the expectations here are the labelling rules read "
            "backwards rather than a second opinion about the same sessions.",
        ),
    )


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def gate(
    report: CalibrationReport,
    *,
    thresholds: CalibrationThresholds | None = None,
    version: str | None = None,
    directory: Path | None = None,
) -> tuple[bool, list[str]]:
    """Score a report against the gate, and prove the registry really refuses.

    Returns `(cleared, reasons)` rather than raising, because this is a study whose
    instrument may legitimately fail and a study that dies on its own finding is a
    worse study. The refusal itself is not simulated: when the report falls short,
    `JudgeRegistry.require_calibrated(..., ci=True)` is called for real and the
    exception it raises is appended to the reasons. A printed opinion about a gate
    is not a gate.
    """
    thr = thresholds if thresholds is not None else CalibrationThresholds()
    ok, failures = report.meets(thr)
    if not ok:
        target = judge(version or report.prompt_version, directory=directory)
        target.attach_calibration(report)
        registry = JudgeRegistry(thresholds=thr)
        registry.register(target)
        try:
            registry.require_calibrated(target, ci=True)
        except Exception as exc:  # noqa: BLE001 - the refusal is the expected outcome
            failures = failures + [
                f"registry refused this judge in CI mode: {type(exc).__name__}"
            ]
        else:  # pragma: no cover - would mean the gate is decorative
            failures = failures + [
                "WARNING: the report fails its thresholds and the registry admitted "
                "it anyway. The gate is not load-bearing."
            ]
    return ok, failures


# --------------------------------------------------------------------------- #
# The seeded-defect probe
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DefectProbe:
    """What the live scorer did when pointed at a session with a seeded defect.

    One row per probed session. `scripted_verdict` is what `roleplay.scorer` said
    on the same session — and, importantly, what the trace the live scorer was
    shown *contains*, because these are full traces including the score card. That
    is a deliberate contamination and `anchored` measures it: a live scorer that
    simply repeats the number it was shown is not grading anything, and a probe
    that could not tell those apart would be worthless.
    """

    item_id: str
    defect: str
    rule_label: str
    scripted_verdict: str
    live_verdict: str
    live_total: int
    critique: str
    evidence: str | None

    @property
    def decidable(self) -> bool:
        """False when the labelling rules could not settle this session.

        Two of the probe rows are R4 sessions: compliant, with the ask or an
        objection outstanding. There is no correct answer to agree or disagree
        with, so calling the live verdict right or wrong on them would be inventing
        a reference. They are probed anyway — the *critique* is what the probe is
        reading on a DEFECT-2 row — and simply not scored.
        """
        return self.rule_label in ("pass", "fail")

    @property
    def agrees_with_rule(self) -> bool | None:
        """Whether the live verdict matches the rule, or None if undecidable."""
        return self.live_verdict == self.rule_label if self.decidable else None

    @property
    def beats_scripted(self) -> bool:
        """The live scorer got it right where the scripted scorer got it wrong.

        The direct measure of whether a model-graded scorer detects a defect
        seeded in the deterministic one.
        """
        return bool(
            self.decidable
            and self.live_verdict == self.rule_label
            and self.scripted_verdict != self.rule_label
        )

    @property
    def anchored(self) -> bool:
        """The live verdict copies the scripted card it was shown, and both are wrong.

        The signature of anchoring rather than grading. Only meaningful on a
        decidable row: without a reference there is nothing for "wrong" to mean,
        and an earlier version of this property fired on the two R4 rows for
        exactly that reason.
        """
        return bool(
            self.decidable
            and self.live_verdict == self.scripted_verdict
            and self.live_verdict != self.rule_label
        )

    def describe(self) -> str:
        outcome = (
            "NOT SCORED (rule could not settle this session)"
            if not self.decidable
            else ("CAUGHT" if self.agrees_with_rule else "MISSED")
        )
        return (
            f"{self.defect} {self.item_id}: rule={self.rule_label} "
            f"scripted={self.scripted_verdict} live={self.live_verdict} "
            f"({self.live_total}/20) " + outcome
            + (" [beat the scripted scorer]" if self.beats_scripted else "")
            + (" [anchored on the card it was shown]" if self.anchored else "")
        )


def probe_traces() -> list[tuple[str, str, Trace]]:
    """Full session traces for the probe rows: conversation, score card and feedback.

    Full, unlike the calibration traces, because two of the three seeded defects
    are only *in* the full trace: DEFECT-2 lives in the feedback prose the scorer
    wrote, and asking whether a model notices fabricated feedback requires showing
    it the feedback. The cost is that the trace also contains the scripted verdict,
    which the model may anchor on — measured, not assumed, by `DefectProbe.anchored`.

    A fresh `RubricScorer` per row, so the cohort curve cannot move the card that
    ends up in the trace and make the probe depend on row order.
    """
    from roleplay.corpus import load_corpus

    corpus = load_corpus()
    by_id = {scenario.id: scenario for scenario in corpus}
    out: list[tuple[str, str, Trace]] = []
    for item_id, defect in DEFECT_PROBE_ROWS:
        scenario = by_id.get(item_id)
        if scenario is None:  # pragma: no cover - corpus drift
            raise KeyError(
                f"probe row {item_id!r} is not in the corpus any more; update "
                "DEFECT_PROBE_ROWS or restore the scenario"
            )
        from roleplay.runtime import RoleplayCoach

        result = RoleplayCoach(scorer=RubricScorer()).run(
            scenario_id=scenario.id,
            trainee_turns=scenario.trainee.turns,
            profile=corpus.profile_for(scenario),
            session_id=f"probe-{scenario.id}",
            jurisdiction=scenario.jurisdiction,
            language=scenario.language,
        )
        out.append((item_id, defect, result.trace))
    return out


def defect_probe(
    *, directory: Path | None = None, path: Path | None = None
) -> list[DefectProbe]:
    """Replay the probe recording and score what the live scorer noticed."""
    from roleplay.labels import rule_label
    from roleplay.runtime import RoleplayCoach
    from roleplay.scorer import session_view

    base = directory if directory is not None else DIR
    target = path if path is not None else base / "defect_probe.jsonl"
    graded = LiveRubricScorer(
        completion=replay_completion(target),
        model=next(iter({c.model for c in Recording.load(target).calls})),
        rubric_version="v2",
    )

    rows: list[DefectProbe] = []
    for item_id, defect, trace in probe_traces():
        live = graded.score_live(trace, item_id=item_id)
        view = session_view(trace)
        scripted = RoleplayCoach(scorer=RubricScorer()).run(
            scenario_id=item_id,
            trainee_turns=list(view.trainee_turns),
            profile=_profile_for(item_id),
            session_id=f"scripted-{item_id}",
            jurisdiction=view.jurisdiction,
            language=view.language,
        )
        rows.append(
            DefectProbe(
                item_id=item_id,
                defect=defect,
                rule_label=rule_label(view).label,
                scripted_verdict=scripted.card.verdict,
                live_verdict=live.card.verdict,
                live_total=live.card.total,
                critique=live.card.feedback,
                evidence=live.evidence,
            )
        )
    return rows


def _profile_for(item_id: str):
    from roleplay.corpus import load_corpus

    corpus = load_corpus()
    for scenario in corpus:
        if scenario.id == item_id:
            return corpus.profile_for(scenario)
    raise KeyError(item_id)  # pragma: no cover


# --------------------------------------------------------------------------- #
# Regeneration
# --------------------------------------------------------------------------- #


def regenerate(
    *,
    out_dir: str | Path = DIR,
    live: bool = False,
    model: str | None = None,
    retry: RetryPolicy | None = None,
    replicates: int = REPLICATES,
    rebuild_labels: bool = False,
) -> dict[str, Path]:
    """Rebuild the study's artefacts.

    Two modes, and the difference is only where the *answers* come from:

    *   **Offline (default).** The committed recordings are the answers. They are
        replayed through the same rubric, the same parser and the same arithmetic
        a live run uses, and the reports are recomputed. No key, no network, and
        byte-identical output — so `git status` after a regeneration tells you
        whether anything actually changed. This mode cannot invent an answer: a
        missing recording raises.
    *   **Live (`live=True`).** Answers come from a provider and the recordings are
        overwritten. Needs `LAB_LIVE_SCORER=1`, a route in `LAB_SCORER_MODEL` or
        `model=`, and that provider's credentials by the names in
        `lab.judges.judge.PROVIDER_ENV_VARS`.

    `rebuild_labels` is off by default and separate from `live` on purpose.
    Rebuilding the labelled set re-runs the persona and rewrites the traces, which
    changes every rendered prompt and therefore invalidates every recording. That
    is occasionally the right thing to do and it must never be a side effect of
    asking for fresh verdicts.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    if rebuild_labels:
        rows = build_rows()
        written["labels"] = write_committed_labels(
            directory / "labels.jsonl", rows=rows
        )

    items = labels()

    if live:
        for version in RUBRIC_VERSIONS:
            for run in range(1, replicates + 1):
                live_scorer = LiveRubricScorer(
                    completion=live_completion(retry=retry),
                    model=model or model_from_env(),
                    rubric_version=version,
                )
                target = verdicts_path(version, run, directory=directory)
                written[f"verdicts {version} run{run}"] = record_scores(
                    live_scorer,
                    [(item.item_id, item.trace) for item in items],
                    target,
                )
        probe_scorer = LiveRubricScorer(
            completion=live_completion(retry=retry),
            model=model or model_from_env(),
            rubric_version="v2",
        )
        written["defect probe"] = record_scores(
            probe_scorer,
            [(item_id, trace) for item_id, _, trace in probe_traces()],
            directory / "defect_probe.jsonl",
        )

    for version in RUBRIC_VERSIONS:
        report = calibrate_version(version, items=items, directory=directory)
        paths = report.write(directory, stem=f"calibration_{version}")
        for label, path in paths.items():
            written[f"calibration {version} {label}"] = path

    study = directory / "study.md"
    study.write_text(study_markdown(directory=directory), encoding="utf-8")
    written["study"] = study
    return written


# --------------------------------------------------------------------------- #
# The write-up
# --------------------------------------------------------------------------- #


def study_markdown(*, directory: Path | None = None) -> str:
    """The whole study as one markdown document, computed from the fixtures."""
    items = labels()
    _, excluded = labelled_and_excluded(build_rows())
    reports = {
        version: calibrate_version(version, items=items, directory=directory)
        for version in RUBRIC_VERSIONS
    }

    parts: list[str] = [
        "# Calibrating a live rubric scorer against a labelled set",
        "",
        "Every number below is recomputed from the committed recordings in this "
        "directory by `python -m roleplay.scorer_study`. Nothing is typed in by "
        "hand, and a re-run on a clean checkout reproduces the file byte for byte.",
        "",
        "## The labelled set",
        "",
        f"{len(items)} items in the metrics. Labels are derived by rule from each "
        "session's own ledgers — see `roleplay/labels.py` for the four rules — and "
        "not written by hand alongside the session.",
        "",
        "```",
        exclusion_summary(excluded),
        "```",
        "",
        "Excluded items were not sent to the model. An item that cannot enter a "
        "metric cannot inform it, and paying for a verdict on a session whose "
        "correct answer nobody can state would produce a number with no reference "
        "to compare it against.",
        "",
    ]

    for version in RUBRIC_VERSIONS:
        parts += [
            f"## Rubric `{version}`",
            "",
            "```",
            reports[version].to_text(),
            "```",
            "",
            "#### Would a second run have produced the same table?",
            "",
            calibration_variance(version, items=items, directory=directory),
            card_stats(version, items=items, directory=directory).to_markdown(),
            criterion_stability(version, items=items, directory=directory).to_markdown(),
            stability(version, items=items, directory=directory).to_markdown(),
        ]
        ok, failures = gate(
            reports[version], version=version, directory=directory
        )
        parts += [
            f"**Gate ({CalibrationThresholds().describe()}): "
            f"{'PASS' if ok else 'FAIL'}**",
            "",
        ]
        parts += [f"- {failure}" for failure in failures]
        parts += [""]

    parts += [
        "## Did v2 beat v1?",
        "",
        compare_reports(reports["v1"], reports["v2"]),
        "",
        "## Pointing the live scorer at the seeded defects",
        "",
        "```",
        "\n".join(row.describe() for row in defect_probe(directory=directory)),
        "```",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """Recompute the study and print it. Exit non-zero if the labels are dishonest.

    The exit code deliberately does **not** track whether the scorer clears its
    gate. A calibration study whose exit code says "the instrument is not good
    enough yet" would be a pipeline that cannot report a real finding without
    going red, and the finding is the product here. What does fail the run is the
    labelled set disagreeing with its own rules, because that makes every other
    number in the output meaningless.
    """
    parser = argparse.ArgumentParser(
        prog="python -m roleplay.scorer_study",
        description=(
            "Recompute the live rubric scorer's calibration from the committed "
            "recordings, or re-record it against a provider with --live."
        ),
    )
    parser.add_argument("--out", default=str(DIR), help="Output directory.")
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Re-record every answer against a real provider, overwriting the "
            f"committed recordings. Requires {LIVE_ENV_VAR}=1, a model route and "
            "that provider's credentials."
        ),
    )
    parser.add_argument("--model", default=None, help="litellm route for --live.")
    parser.add_argument(
        "--replicates",
        type=int,
        default=REPLICATES,
        help=f"Identical runs to record per rubric version (default {REPLICATES}).",
    )
    parser.add_argument(
        "--rebuild-labels",
        action="store_true",
        help=(
            "Re-run the persona and rewrite labels.jsonl. Invalidates every "
            "recording; use only when the labelled set is meant to change."
        ),
    )
    args = parser.parse_args(argv)
    directory = Path(args.out)

    problems = verify_pack()
    if problems:
        print("the committed labelled set does not agree with its own rules:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    written = regenerate(
        out_dir=directory,
        live=args.live,
        model=args.model,
        replicates=args.replicates,
        rebuild_labels=args.rebuild_labels,
    )
    print(study_markdown(directory=directory))
    print()
    for label, path in written.items():
        print(f"  wrote {label}: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    sys.exit(main())
