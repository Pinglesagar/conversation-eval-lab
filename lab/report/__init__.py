"""Rendering results for humans, and exporting them to other people's tools.

WHAT THIS DEMONSTRATES
----------------------
One rule governs everything in this package: print every rate with its numerator
and denominator. "83% pass" hides whether that was 5 of 6 or 830 of 1000, and
those two numbers warrant completely different conclusions. Naked percentages are
the most common way an evaluation report misleads its own author.

Two further rules are enforced by the types rather than by review, because a
convention that depends on someone remembering is a convention that fails on a
deadline:

*   a judge verdict cannot be rendered without the judge's measured TPR and TNR
    (`JudgeSummary.calibration` is a required field);
*   a latency figure cannot be rendered without the timing calibration gate's
    verdict (`VoiceMetrics.calibration_verdict`, including an explicit `NOT_RUN`).

    lab.report.report    the run report: markdown + JSON, headline verdicts,
                         pass^k stability, contract failures, judge verdicts with
                         calibration, voice metrics, failures with evidence, and
                         a section auditing the report's own gaps
    lab.report.heatmap   the transition-failure matrix — from-agent x to-agent —
                         as a table with no dependencies, or a PNG with the
                         optional `[charts]` extra
    lab.report.interop   langfuse and promptfoo exports, depending on neither
                         package: this harness layers onto the existing
                         ecosystem instead of competing with it

`matplotlib` is imported inside `render_heatmap`, so the core install stays small
and the test suite never depends on a plotting backend.
"""

from lab.report.heatmap import (
    SESSION_END_OK_REASONS,
    TransitionMatrix,
    default_failure_predicate,
    matrix_from_failures,
    render_heatmap,
    transition_key,
    transition_matrix,
)
from lab.report.interop import (
    EPOCH,
    LANGFUSE_API_TARGET,
    PROMPTFOO_API_TARGET,
    from_langfuse_batch,
    promptfoo_assertions_for,
    to_langfuse_batch,
    to_promptfoo_config,
    to_promptfoo_tests,
)
from lab.report.report import (
    ContractStat,
    FailureRecord,
    JudgeCalibration,
    JudgeSummary,
    Rate,
    RunReport,
    VoiceMetrics,
    format_rate,
    write_report,
)

__all__ = [
    "ContractStat",
    "EPOCH",
    "FailureRecord",
    "JudgeCalibration",
    "JudgeSummary",
    "LANGFUSE_API_TARGET",
    "PROMPTFOO_API_TARGET",
    "Rate",
    "RunReport",
    "SESSION_END_OK_REASONS",
    "TransitionMatrix",
    "VoiceMetrics",
    "default_failure_predicate",
    "format_rate",
    "from_langfuse_batch",
    "matrix_from_failures",
    "promptfoo_assertions_for",
    "render_heatmap",
    "to_langfuse_batch",
    "to_promptfoo_config",
    "to_promptfoo_tests",
    "transition_key",
    "transition_matrix",
    "write_report",
]
