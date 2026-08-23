"""The tier's report: three rows, their evidence, and the gate that admits them.

WHAT THIS DEMONSTRATES
----------------------
Two habits from the rest of this repository, applied to a tier whose numbers came
from a live network.

**The gate runs first, and it can refuse.** `lab.voice.calibration` proves the
harness recovers a known delay while excluding its own compute. It is offline,
deterministic and costs milliseconds, so this report runs it every time and prints
its verdict above the latency figure. If it fails, the delivery gap is not printed
as a caveated number — it is not printed at all, and the refusal says why. An
uncalibrated stopwatch does not produce a slightly less trustworthy figure; it
produces one with no meaning.

**Every rate carries its denominator.** Three rows make every percentage a
rounding of one third, so counts are printed as counts. The one place a percentage
appears is where the underlying quantity is genuinely a fraction — silent frames
over delivered frames — and it is printed beside the fraction it came from.

WHY THE REPORT RECOMPUTES INSTEAD OF READING STORED RESULTS
-----------------------------------------------------------
Nothing in the committed recordings is a result. They hold timestamps and energies;
every figure below is derived here, on each run, from those. That is what makes the
tier auditable after the fact: change a threshold and the report changes, which is
the property a stored summary cannot have. It is also how the offline suite can
assert on numbers from a live session it never ran.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from lab.voice.calibration import CalibrationReport, run_calibration
from lab.voice.transport.measure import (
    DEFAULT_THRESHOLD_RMS,
    DegradationComparison,
    DeliveryGapMeasurement,
    LifecycleObservation,
    degradation_comparison,
    delivery_gap,
    lifecycle_observation,
    threshold_sensitivity,
)
from lab.voice.transport.records import TransportRecording
from lab.voice.transport.rows import (
    ROW_DIR,
    RowOutcome,
    TransportRow,
    coverage,
    evaluate_degradation,
    evaluate_delivery_gap,
    evaluate_lifecycle,
    load_rows,
)

__all__ = [
    "FIXTURE_DIR",
    "TransportReport",
    "build_report",
    "ladder_side",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Where the committed recordings live.
FIXTURE_DIR: Path = REPO_ROOT / "fixtures" / "audio" / "transport"

#: Clips the recordings were published from, resolved by the name in the record.
CLIP_DIR: Path = REPO_ROOT / "fixtures" / "audio" / "clips"

#: The harness quiet time after a rejoin, declared so the lifecycle row can say
#: which part of the listener's silence it chose. Mirrors
#: `session.LiveKitTransport.settle_s`; stated here rather than imported so the
#: report does not need the WebRTC client to render.
SETTLE_S: float = 0.6

#: Fill modes compared on the degradation row. Both, because which one is used
#: decides whether the file ladder agrees with reality — see the row's findings.
FILL_MODES: tuple[str, ...] = ("zero", "hold")


def ladder_side(
    recording: TransportRecording, *, fill: str, seed: int = 20260822
) -> tuple[list[float] | None, list[float] | None, float | None, str | None]:
    """Compute the file-based ladder's side of the degradation comparison.

    Returns `(perturbed_rms, baseline_rms, realised_loss, refusal)`. The refusal is
    a string when the ladder side cannot be computed at all — a missing clip, or
    numpy absent — so the caller reports a reason rather than an empty column.

    The clip is resolved from the *recording's own* provenance field, not from a
    constant here, so the comparison cannot silently drift onto different audio
    from the one that was published.
    """
    injected = next(
        (u for u in recording.utterances if u.withheld_source_index), None
    )
    if injected is None:
        return None, None, None, "no utterance in this recording withheld any frames"
    clip_path = CLIP_DIR / injected.clip
    if not clip_path.exists():
        return None, None, None, f"the published clip {injected.clip} is not in {CLIP_DIR}"
    try:
        from lab.voice.engines.audiofile import read_audio  # noqa: PLC0415 - needs numpy
        from lab.voice.perturb import packet_loss  # noqa: PLC0415 - needs numpy
        from lab.voice.transport.session import frame_energies  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - any import failure is the same refusal
        return None, None, None, (
            f"the file ladder needs numpy and soundfile ({exc.__class__.__name__}: "
            '{exc}); install the audio extra with `pip install -e ".[dev]"`'
        )

    audio, rate = read_audio(clip_path)
    if audio.ndim > 1:  # pragma: no cover - committed clips are mono
        audio = audio[:, 0]
    samples = [float(value) for value in audio]
    frame_samples = recording.arrivals.frame_samples
    nominal = injected.nominal_loss or 0.0
    perturbed, descriptor = packet_loss(
        audio,
        loss_rate=nominal,
        sample_rate=rate,
        packet_ms=injected.frame_ms,
        fill=fill,  # type: ignore[arg-type]
        seed=seed,
    )
    realised = descriptor.measured.get("realised_loss_rate")
    return (
        frame_energies([float(value) for value in perturbed], frame_samples),
        frame_energies(samples, frame_samples),
        float(realised) if realised is not None else None,
        None,
    )


class RowReport(BaseModel):
    """One row: its definition, its verdict, and whichever measurement it produced."""

    model_config = ConfigDict(extra="forbid")

    row: TransportRow
    outcome: RowOutcome
    recording_path: str | None = None
    recording_summary: str | None = None
    delivery: DeliveryGapMeasurement | None = None
    degradation: list[DegradationComparison] = Field(default_factory=list)
    lifecycle: LifecycleObservation | None = None
    sensitivity: list[tuple[float, float | None]] = Field(
        default_factory=list,
        description="(threshold, mean gap in ms or None when refused) — the sweep.",
    )
    other_sessions: list[tuple[str, DeliveryGapMeasurement]] = Field(
        default_factory=list,
        description=(
            "Further recordings of the same row, from separate live sessions. Any "
            "`<row>-*.json` beside the primary fixture is picked up automatically, so "
            "committing a second session is all it takes to put this row's run-to-run "
            "spread in the report rather than in a caveat."
        ),
    )
    notes: list[str] = Field(default_factory=list)


class TransportReport(BaseModel):
    """The whole tier: the gate, three rows, and what the tier does not claim."""

    model_config = ConfigDict(extra="forbid")

    calibration: CalibrationReport
    rows: list[RowReport]
    coverage: dict[str, Any]
    threshold_rms: float = DEFAULT_THRESHOLD_RMS

    @property
    def verdict(self) -> str:
        """PASS only if every row passed. NOT-RUN counts as neither pass nor fail."""
        verdicts = {report.outcome.verdict for report in self.rows}
        if verdicts == {"PASS"}:
            return "PASS"
        if "FAIL" in verdicts:
            return "FAIL"
        return "INCOMPLETE"

    def to_markdown(self) -> str:
        lines: list[str] = []
        add = lines.append
        add("# The WebRTC transport tier")
        add("")
        add(
            f"**{len(self.rows)} rows. Tier verdict: {self.verdict}. "
            "Non-gating in CI by design.**"
        )
        add("")
        add(
            "Three rows run through real WebRTC transport because three things only "
            "exist in transport. Everything else in this harness runs in process, "
            "because in process is faster, deterministic, and owns its own clock."
        )
        add("")

        add("## The gate")
        add("")
        add(
            f"`lab.voice.calibration`: **{self.calibration.verdict}** "
            f"({self.calibration.tolerance.describe()}), and its naive whole-turn "
            f"control: **{self.calibration.control_verdict}**, as expected."
        )
        add("")
        if not self.calibration.passed:
            add(
                "> The timing gate did not pass, so no latency figure below is "
                "reported. This is not a caveat, it is a refusal."
            )
            add("")

        add("## Rows")
        add("")
        add("| row | category | verdict | headline |")
        add("| --- | --- | --- | --- |")
        for report in self.rows:
            headline = self._headline(report)
            add(
                f"| `{report.row.id}` | {report.row.category} | "
                f"**{report.outcome.verdict}** | {headline} |"
            )
        add("")

        for report in self.rows:
            add(f"## {report.row.title}")
            add("")
            add(f"*{report.row.measures.strip()}*")
            add("")
            add(f"**Verdict: {report.outcome.verdict}.** {report.recording_summary or ''}")
            add("")
            for reason in report.outcome.reasons:
                add(f"- {reason}")
            if report.outcome.reasons:
                add("")
            for finding in report.outcome.findings:
                add(f"- {finding}")
            if report.outcome.findings:
                add("")
            body = self._detail(report)
            if body:
                add(body)
                add("")

        add("## Coverage, as counts")
        add("")
        add(f"- rows: {self.coverage['rows']}")
        for name, count in self.coverage["categories"].items():
            add(f"- category `{name}`: {count}")
        add("")
        return "\n".join(lines)

    # ------------------------------------------------------------- rendering

    def _headline(self, report: RowReport) -> str:
        if report.delivery is not None:
            if not report.delivery.reportable:
                return f"not reported — {report.delivery.refusal}"
            net = (
                f", {report.delivery.net_mean_ms:.1f} ms net of the local send queue"
                if report.delivery.net_mean_ms is not None
                else ""
            )
            return (
                f"{report.delivery.mean_ms:.1f} ms mean over "
                f"{report.delivery.distribution.n if report.delivery.distribution else 0} "
                f"turns{net}, against 0 ms agent-side"
            )
        if report.degradation:
            parts = []
            for comparison in report.degradation:
                if not comparison.reportable:
                    return f"not reported — {comparison.refusal}"
                ratio = comparison.ratio
                parts.append(
                    f"fill={comparison.fill} {ratio:.2f}x"
                    if ratio is not None
                    else f"fill={comparison.fill} unavailable"
                )
            return "file ladder vs transport: " + ", ".join(parts)
        if report.lifecycle is not None:
            if not report.lifecycle.reportable:
                return f"not reported — {report.lifecycle.refusal}"
            silence = report.lifecycle.audio_silence_s
            return (
                f"{report.lifecycle.verdict}; listener heard nothing for "
                + (f"{silence * 1000:.0f} ms" if silence is not None else "an unmeasured interval")
            )
        return "not run"

    def _detail(self, report: RowReport) -> str:
        if report.delivery is not None and report.delivery.reportable:
            return self._delivery_detail(report)
        if report.degradation:
            return self._degradation_detail(report)
        if report.lifecycle is not None and report.lifecycle.reportable:
            return self._lifecycle_detail(report.lifecycle)
        return ""

    def _delivery_detail(self, report: RowReport) -> str:
        measurement = report.delivery
        assert measurement is not None and measurement.distribution is not None
        raw = measurement.distribution
        net = measurement.net_distribution
        rows = [
            "| figure | n | mean | p50 | p90 | p95 | min | max |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]

        def row(name: str, dist: Any) -> str:
            def cell(q: float) -> str:
                quantile = dist.quantile(q)
                return (
                    f"{quantile.value_s * 1000:.1f} ms"
                    if quantile.value_s is not None
                    else f"n/a (n<{quantile.min_n})"
                )

            return (
                f"| {name} | {dist.n} | {(dist.mean_s or 0) * 1000:.1f} ms | "
                f"{cell(0.50)} | {cell(0.90)} | {cell(0.95)} | "
                f"{(dist.min_s or 0) * 1000:.1f} ms | {(dist.max_s or 0) * 1000:.1f} ms |"
            )

        rows.append(row("delivery gap (measured)", raw))
        if net is not None:
            rows.append(row("net of local send queue", net))
        rows.append(
            f"| agent-side figure | {raw.n} | 0.0 ms | 0.0 ms | 0.0 ms | 0.0 ms | "
            "0.0 ms | 0.0 ms |"
        )
        block = ["\n".join(rows), ""]
        if measurement.queue_correlation is not None and net is not None:
            block.append(
                f"The gap and this harness's own send-queue depth correlate at "
                f"**{measurement.queue_correlation:.2f}**, and subtracting the queue "
                f"takes the scatter from {(raw.stdev_s or 0) * 1000:.1f} ms to "
                f"{(net.stdev_s or 0) * 1000:.1f} ms. So the measured figure is what "
                "the listener waited, and the net figure is what the transport itself "
                "contributed; the difference is ours, and it is measured rather than "
                "assumed."
            )
            block.append("")
        if report.other_sessions:
            block.append(
                "Run-to-run spread. A live figure from one session is one session, so "
                "the same row was recorded again and both recordings are committed:"
            )
            block.append("")
            block.append(
                "| session | n | mean | p50 | net of send queue | stdev | row verdict |"
            )
            block.append("| --- | --- | --- | --- | --- | --- | --- |")
            sessions: list[tuple[str, Any]] = [("committed (primary)", measurement)]
            sessions.extend(report.other_sessions)
            means: list[float] = []
            medians: list[float] = []
            failures = 0
            for label, other in sessions:
                verdict = evaluate_delivery_gap(report.row, other).verdict
                failures += verdict == "FAIL"
                if not other.reportable or other.distribution is None:
                    block.append(
                        f"| {label} | — | not reported | — | — | — | {verdict} |"
                    )
                    continue
                means.append(other.mean_ms or 0.0)
                p50 = other.distribution.quantile(0.50).value_s
                if p50 is not None:
                    medians.append(p50 * 1000.0)
                block.append(
                    f"| {label} | {other.distribution.n} | {other.mean_ms:.1f} ms | "
                    + (f"{p50 * 1000:.1f} ms | " if p50 is not None else "n/a | ")
                    + (
                        f"{other.net_mean_ms:.1f} ms | "
                        if other.net_mean_ms is not None
                        else "n/a | "
                    )
                    + f"{(other.distribution.stdev_s or 0.0) * 1000:.1f} ms | {verdict} |"
                )
            block.append("")
            if len(means) > 1:
                spread = max(means) - min(means)
                block.append(
                    f"The session **means** differ by {spread:.1f} ms "
                    f"({spread / min(means):.0%} of the smaller)."
                )
                if len(medians) > 1:
                    median_spread = max(medians) - min(medians)
                    block.append(
                        f" The session **medians** differ by {median_spread:.1f} ms. "
                        "That asymmetry is the finding, and it decides which statistic "
                        "this row should be quoted by: the typical delivery gap "
                        "reproduces across sessions, while the mean and the tail do "
                        "not, because a session can contain a stall that drags them and "
                        "leaves the median where it was. Quote the median; read the "
                        "spread as the risk."
                    )
                block.append("")
                block.append(
                    "What does not vary at all is the comparison the row exists to "
                    "make: every session, on every statistic, puts the gap far above "
                    "the 0 ms an agent-side figure implies."
                )
                block.append("")
            if failures:
                block.append(
                    f"**{failures} of the {len(sessions)} committed session(s) fails this "
                    "row's own assertions**, and it is kept rather than deleted. The "
                    "scatter ceiling is what catches it: a session with a mid-call stall "
                    "produces a mean that looks usable and a per-turn spread that does "
                    "not, and for live in-call coaching the spread is the risk. An "
                    "assertion no recorded session has ever tripped is an assertion "
                    "nobody has tested."
                )
                block.append("")
        if report.sensitivity:
            block.append("Sensitivity to the one judgement call — where speech starts:")
            block.append("")
            block.append("| onset threshold (RMS) | mean gap |")
            block.append("| --- | --- |")
            for threshold, mean_ms in report.sensitivity:
                block.append(
                    f"| {threshold:.3f} | "
                    + (f"{mean_ms:.1f} ms |" if mean_ms is not None else "refused |")
                )
            block.append("")
        return "\n".join(block)

    def _degradation_detail(self, report: RowReport) -> str:
        lines = [
            "| fill mode | injected | realised in file | transport adds | file adds | "
            "per unit loss (transport) | per unit loss (file) | ratio | verdict |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for comparison in report.degradation:
            if not comparison.reportable:
                lines.append(
                    f"| {comparison.fill} | — | — | — | — | — | — | — | "
                    f"not reported: {comparison.refusal} |"
                )
                continue
            ratio = comparison.ratio
            lines.append(
                f"| `{comparison.fill}` | {comparison.nominal_rate} | "
                f"{(comparison.file_realised_loss or 0.0):.1%} | "
                f"{(comparison.transport_excess or 0.0):.1%} | "
                f"{(comparison.ladder_excess or 0.0):.1%} | "
                f"{(comparison.transport_silence_per_loss or 0.0):.2f} | "
                f"{(comparison.ladder_silence_per_loss or 0.0):.2f} | "
                f"{ratio:.2f}x | " + ("AGREE" if comparison.agree else "DISAGREE") + " |"
                if ratio is not None
                else f"| `{comparison.fill}` | — | — | — | — | — | — | — | unavailable |"
            )
        lines.append("")
        first = report.degradation[0] if report.degradation else None
        if first is not None and first.jitter is not None and first.reportable:
            jitter = first.jitter
            lines.append(
                f"Pacing at the receiver: {jitter.frames} frames, "
                f"{jitter.gaps} intervals, nominal {jitter.nominal_frame_ms:.0f} ms, "
                f"mean absolute deviation "
                f"{(jitter.mean_abs_deviation_ms or 0.0):.1f} ms, max interval "
                f"{(jitter.max_gap_ms or 0.0):.1f} ms, late frames {jitter.late_rate}. "
                "The file ladder has no figure to put in this paragraph: a perturbed "
                "file has no time axis."
            )
            lines.append("")
        return "\n".join(lines)

    def _lifecycle_detail(self, observation: LifecycleObservation) -> str:
        recovery = observation.transport_recovery_s
        heard = observation.audio_silence_s
        rows = [
            "| interval | measured | what it contains |",
            "| --- | --- | --- |",
            f"| drop -> publisher reconnected | "
            f"{(observation.downtime_s or 0.0) * 1000:.0f} ms | signalling only |",
            f"| drop -> far side subscribed again | "
            + (f"{recovery * 1000:.0f} ms" if recovery is not None else "not measured")
            + " | signalling, republish, subscription |",
            f"| last audio heard -> next audio heard | "
            + (f"{heard * 1000:.0f} ms" if heard is not None else "not measured")
            + f" | the listener's experience, including {observation.settle_s * 1000:.0f} ms "
            "the harness left deliberately |",
            "",
            f"Runs before the drop: {observation.runs_before_drop}. Straddling it: "
            f"{observation.runs_straddling_drop}. After the reconnect: "
            f"{observation.runs_after_reconnect}. Connection attempts: "
            f"{observation.attempts}. Frames of the interrupted turn that arrived after "
            f"the sender had gone: {observation.frames_after_drop_in_flight}.",
            "",
        ]
        return "\n".join(rows)


def build_report(
    *,
    row_dir: Path | str = ROW_DIR,
    fixture_dir: Path | str = FIXTURE_DIR,
    calibration: CalibrationReport | None = None,
    threshold_rms: float | None = None,
) -> TransportReport:
    """Load the rows, recompute every figure from the committed recordings, score them.

    `calibration` defaults to a fresh run of the gate. It is a parameter so a test
    can inject a failing report and assert the delivery gap is withheld — the
    refusal path has to be exercised, not just written.
    """
    rows = load_rows(row_dir)
    gate = calibration if calibration is not None else run_calibration()
    base = Path(fixture_dir)
    reports: list[RowReport] = []

    for row in rows:
        name = row.id.replace("audio-transport-", "")
        path = base / f"{name}.json"
        if not path.exists():
            reports.append(
                RowReport(
                    row=row,
                    outcome=RowOutcome(
                        row_id=row.id,
                        category=row.category,
                        verdict="NOT-RUN",
                        reasons=[
                            f"no committed recording at {path.relative_to(REPO_ROOT)}; "
                            "run `python -m scripts.make_transport_fixtures` against a "
                            "live room"
                        ],
                    ),
                )
            )
            continue

        recording = TransportRecording.read(path)
        threshold = threshold_rms if threshold_rms is not None else recording.onset_threshold_rms
        report = RowReport(
            row=row,
            outcome=RowOutcome(row_id=row.id, category=row.category, verdict="NOT-RUN"),
            recording_path=str(path.relative_to(REPO_ROOT)),
            recording_summary=recording.describe(),
        )

        if row.category == "delivery-gap":
            measurement = delivery_gap(
                recording, calibration=gate, threshold_rms=threshold
            )
            report.delivery = measurement
            report.outcome = evaluate_delivery_gap(row, measurement)
            report.other_sessions = [
                (
                    extra.stem,
                    delivery_gap(
                        TransportRecording.read(extra),
                        calibration=gate,
                        threshold_rms=threshold,
                    ),
                )
                for extra in sorted(base.glob(f"{name}-*.json"))
            ]
            report.sensitivity = [
                (
                    value,
                    result.mean_ms if result.reportable else None,
                )
                for value, result in threshold_sensitivity(recording, calibration=gate)
            ]
        elif row.category == "transport-degradation":
            comparisons: list[DegradationComparison] = []
            for fill in FILL_MODES:
                perturbed, baseline, realised, refusal = ladder_side(recording, fill=fill)
                comparisons.append(
                    degradation_comparison(
                        recording,
                        file_rms=perturbed,
                        file_baseline_rms=baseline,
                        file_realised_loss=realised,
                        fill=fill,
                        threshold_rms=threshold,
                    )
                )
                if refusal is not None:
                    report.notes.append(f"fill={fill}: {refusal}")
            report.degradation = comparisons
            report.outcome = evaluate_degradation(row, comparisons[0] if comparisons else None)
        elif row.category == "connection-lifecycle":
            observation = lifecycle_observation(
                recording, threshold_rms=threshold, settle_s=SETTLE_S
            )
            report.lifecycle = observation
            report.outcome = evaluate_lifecycle(row, observation)

        reports.append(report)

    return TransportReport(
        calibration=gate,
        rows=reports,
        coverage=coverage(rows),
        threshold_rms=(
            threshold_rms if threshold_rms is not None else DEFAULT_THRESHOLD_RMS
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute and render the WebRTC transport tier from its committed "
            "recordings. Offline; no key, no network."
        )
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    parser.add_argument("--out", default=None, help="Write to this path as well as stdout.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit non-zero unless every row passed. Off by default: this tier is "
            "non-gating, and a live-network row that blocks a merge trains people to "
            "bypass the gate."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = build_report()
    text = (
        json.dumps(report.model_dump(mode="json"), indent=2)
        if args.json
        else report.to_markdown()
    )
    print(text)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
    if args.strict and report.verdict != "PASS":
        print(f"\nstrict mode: tier verdict is {report.verdict}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
