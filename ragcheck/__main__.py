"""`python -m ragcheck` — the whole argument, in one offline run.

Four things get printed, in this order, because each one makes the next
believable:

    1. the retrieval metrics, which need no oracle and are exactly reproducible
    2. the three worked examples, each a failure one metric catches and the
       others cannot see
    3. the full report, retrieval and generation kept apart
    4. the calibration of the grader, and the gate refusing it

No API keys, no network, no arguments.
"""

from __future__ import annotations

import sys

from lab.judges.registry import CalibrationGateError

from ragcheck.calibration import load_claim_labels
from ragcheck.report import evaluate

_RULE = "=" * 78


def _heading(text: str) -> None:
    print(f"\n{_RULE}\n{text}\n{_RULE}")


def _worked_examples(report) -> None:
    rows = {row.case_id: row for row in report.generation.rows}

    _heading("WORKED EXAMPLE 1 — retrieval is perfect and the answer is wrong")
    row = rows["c02"]
    print(f"\n  question   {row.question}")
    print(f"  context    {row.context}   gold {row.gold}")
    print(f"  answer     {row.answer}")
    print(f"\n  recall of gold in the context   {row.context_recall_gold}")
    print(f"  context precision (gold ids)    {row.context_precision_gold:.3f}")
    print(f"  groundedness                    {row.groundedness.rate}")
    for claim in row.groundedness.unsupported:
        print(f"\n    unsupported: {claim.claim}")
        print(f"    because:     {claim.critique}")
        print(f"    passage:     {claim.evidence}")
    print(
        "\n  Every retrieval metric on this row is 1.0. The passage holding the answer\n"
        "  was retrieved at rank 1. A retrieval-only suite reports this question as a\n"
        "  pass, and the system told the customer a figure that is 67% too high."
    )

    _heading("WORKED EXAMPLE 2 — every claim supported, and none of them the answer")
    row = rows["c12"]
    print(f"\n  question   {row.question}")
    print(f"  context    {row.context}   gold {row.gold}")
    print(f"  answer     {row.answer}")
    print(f"\n  groundedness       {row.groundedness.rate}   <- perfect")
    print(f"  answer relevance   {'relevant' if row.relevance.relevant else 'OFF-QUESTION'}")
    print(f"    {row.relevance.critique}")
    print(
        "\n  The answer is grounded in p07, which was retrieved, and p07 is not what was\n"
        "  asked about. Groundedness cannot see this failure by construction: it only\n"
        "  ever asks whether the context supports the answer, never whether the answer\n"
        "  addresses the question. Two metrics, two questions, and a suite that gates on\n"
        "  faithfulness alone ships this."
    )

    _heading("WORKED EXAMPLE 3 — faithful, relevant, and still not the answer")
    row = rows["c18"]
    print(f"\n  question   {row.question}")
    print(f"  context    {row.context}   gold {row.gold}")
    print(f"  answer     {row.answer}")
    print("  reference  A party of nine pays a deposit of GBP 15 per person. …")
    print(f"\n  groundedness                    {row.groundedness.rate}   <- perfect")
    print("  answer relevance                relevant")
    print(f"  recall of gold in the context   {row.context_recall_gold}   <- p01 missing")
    print(f"  context recall (reference)      {row.context_recall.rate}")
    for claim in row.context_recall.unsupported:
        print(f"\n    reference claim the context cannot support: {claim.claim}")
    print(
        "\n  The generator did nothing wrong. It was handed a context missing half the\n"
        f"  answer — recall@{report.k} on this row is {row.context_recall_gold} — and stayed inside it, which is\n"
        "  what we asked of it. Only context recall, measured against a written\n"
        "  reference rather than against the generated answer, names the retrieval team\n"
        "  as the owner of this bug."
    )


def main(argv: list[str] | None = None) -> int:
    labels = load_claim_labels()

    _heading("ragcheck — RAG evaluation over a 16-chunk corpus and 18 questions")
    print(
        "\n  Retrieval metrics need no model. The judged metrics run on the offline\n"
        "  lexical stand-in in ragcheck/offline.py, which is not a model and is wrong\n"
        f"  in ways this run measures against {len(labels)} hand labels."
    )

    report = evaluate()
    _worked_examples(report)

    _heading("THE FULL REPORT")
    print()
    print(report.to_text())

    _heading("WHAT THE GRADER'S ERROR RATE DOES TO THE HEADLINE NUMBER")
    # The 16 labelled items drawn from the evaluation set are exactly the claims
    # the two support metrics are computed over, so the two counts are directly
    # comparable. The two `probe-` rows are not part of any metric and are
    # excluded here; they exist to measure the grader, not the system.
    from_cases = [label for label in labels if "#" in label.item_id]
    hand_supported = sum(1 for label in from_cases if label.label == "pass")
    measured = report.generation.pooled_groundedness
    recall = report.generation.pooled_context_recall
    stand_in_supported = measured.numerator + recall.numerator
    stand_in_total = measured.denominator + recall.denominator
    print(
        f"\n  claims the stand-in called supported:  {stand_in_supported}/{stand_in_total}"
        f"   (groundedness {measured}, context recall {recall})"
        f"\n  claims a human called supported:       {hand_supported}/{len(from_cases)}"
        "\n"
        "\n  The gap is one row: c13#claim2. The passage says vouchers may NOT be used to\n"
        "  pay a deposit; the answer says they may; every content word matches. The\n"
        "  stand-in calls it supported, so the metric over-reports faithfulness — and\n"
        "  the calibration report above predicted exactly that, at TPR 4/5, before\n"
        "  anybody read a single claim."
    )

    _heading("THE GATE")
    try:
        evaluate(gate=True)
    except CalibrationGateError as exc:
        print(f"\n  evaluate(gate=True) raised {type(exc).__name__}:\n")
        for line in str(exc).split("; "):
            print(f"    {line}")
        print(
            "\n  Which is the correct outcome. The oracle that ships in this repository is\n"
            "  not good enough to gate a release, the gate says so in CI, and the only way\n"
            "  past it has to be written at the call site and logs a warning.\n"
            "  Put a real judge behind the same interface, re-run the calibration, and the\n"
            "  gate decides again on the new numbers. Nothing else in the package moves."
        )
        return 0
    print("\n  the gate passed, so the support judge cleared its thresholds")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
