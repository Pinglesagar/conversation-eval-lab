"""Per-item, per-criterion stability of the live scorer across identical repeats.

WHY `lab.judges.calibration.SelfConsistency` IS NOT ENOUGH HERE
--------------------------------------------------------------
That class answers the right question about a judge: across k identical runs, did
the *verdict* hold still, per item. It is reused unmodified for the binary half of
this study.

A rubric scorer has four more outputs than a judge, and they are the ones the
product ships. A dashboard shows five criteria; a coaching page quotes a total; a
manager compares one adviser's discovery score against another's. So a scorer can
be perfectly stable on the verdict — every run says "fail" — while its discovery
score walks between 1 and 4, and every number a human being actually looks at is
noise. The binary stability figure cannot see that, and reporting it alone would
be the same mistake this repo has already documented once, one level down.

THE CANCELLATION THAT MAKES AGGREGATES LIE
------------------------------------------
The instructive case is not a scorer that drifts. It is a scorer whose errors
cancel. If item A's total moves +2 between run 1 and run 2 while item B's moves
-2, the cohort mean is identical in both runs, the confusion matrix may be
byte-identical, and two individual certification decisions changed. Any aggregate
computed across items — mean total, pass rate, TPR — is blind to it by
construction, because summing is exactly the operation that destroys the
information.

So `CriterionStability` reports both, adjacent and never merged:

    per item      which items moved, on which criterion, by how much
    aggregate     the cohort mean per run, and whether it moved at all

and `cancellation` names the case where the second is flat and the first is not.
That is not a curiosity: it is the shape in which an unstable instrument passes
review, because the number in the summary slide is the aggregate.

WHAT IS DELIBERATELY NOT DONE
-----------------------------
The repeats are not averaged into a consensus score. Averaging three runs costs
three times the money to make the instability invisible, and an evaluation harness
that hides variance in the instrument it is measuring has inverted its own
purpose. Variance is reported. If it is unacceptable, the fix is the prompt or the
model, not a bigger sample per item.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

from roleplay.scorer import CRITERIA, PASS_TOTAL

__all__ = [
    "ItemRuns",
    "CriterionStability",
    "stability_of",
]


@dataclass(frozen=True)
class ItemRuns:
    """One session's full output from each of several identical runs.

    `criteria` is keyed by criterion name and holds one score per run, in run
    order. `verdicts` and `totals` are parallel lists over the same runs, and
    `errored` marks the runs where no card could be parsed at all.
    """

    item_id: str
    human_label: str
    criteria: dict[str, tuple[int, ...]]
    totals: tuple[int, ...]
    verdicts: tuple[str, ...]
    errored: tuple[bool, ...]

    @property
    def runs(self) -> int:
        return len(self.totals)

    @property
    def verdict_stable(self) -> bool:
        return len(set(self.verdicts)) <= 1

    @property
    def total_spread(self) -> int:
        """Max minus min of the rubric total, in points, across the runs."""
        return max(self.totals) - min(self.totals) if self.totals else 0

    @property
    def moved_criteria(self) -> dict[str, int]:
        """Every criterion that did not hold still, and its spread in points."""
        return {
            name: max(scores) - min(scores)
            for name, scores in self.criteria.items()
            if len(set(scores)) > 1
        }

    @property
    def stable(self) -> bool:
        """True when the verdict, the total and all five criteria held still.

        The conjunction, not the verdict alone. An item whose verdict repeated and
        whose discovery score did not is an item this study must count as unstable,
        because the discovery score is shipped.
        """
        return self.verdict_stable and self.total_spread == 0 and not self.moved_criteria

    def describe(self) -> str:
        parts = [
            f"{self.item_id} (human: {self.human_label}) "
            f"verdicts {'/'.join(self.verdicts)} totals {list(self.totals)}"
        ]
        if self.moved_criteria:
            moved = ", ".join(
                f"{name} {list(self.criteria[name])} (spread {spread})"
                for name, spread in sorted(self.moved_criteria.items())
            )
            parts.append(f"moved: {moved}")
        if any(self.errored):
            parts.append(f"errored on run(s) {[i + 1 for i, e in enumerate(self.errored) if e]}")
        return " -- ".join(parts)


@dataclass(frozen=True)
class CriterionStability:
    """Per-item and aggregate stability across k identical runs of one scorer."""

    rubric_version: str
    model: str
    runs: int
    items: tuple[ItemRuns, ...]

    @property
    def n(self) -> int:
        return len(self.items)

    @property
    def unstable(self) -> tuple[ItemRuns, ...]:
        """Items where anything moved — verdict, total or a single criterion."""
        return tuple(item for item in self.items if not item.stable)

    @property
    def verdict_unstable(self) -> tuple[ItemRuns, ...]:
        return tuple(item for item in self.items if not item.verdict_stable)

    @property
    def score_unstable(self) -> tuple[ItemRuns, ...]:
        """Items whose numbers moved while the verdict held.

        The population the binary stability figure cannot see, and the reason this
        class exists.
        """
        return tuple(
            item
            for item in self.items
            if item.verdict_stable and (item.total_spread or item.moved_criteria)
        )

    def mean_total(self, run: int) -> float:
        """The cohort's mean rubric total on one run (0-indexed)."""
        if not self.items:
            return 0.0
        return round(sum(item.totals[run] for item in self.items) / self.n, 3)

    @property
    def aggregate_means(self) -> tuple[float, ...]:
        return tuple(self.mean_total(run) for run in range(self.runs))

    @property
    def aggregate_spread(self) -> float:
        means = self.aggregate_means
        return round(max(means) - min(means), 3) if means else 0.0

    @property
    def pass_counts(self) -> tuple[int, ...]:
        """How many items passed on each run. The other aggregate that can cancel."""
        return tuple(
            sum(1 for item in self.items if item.verdicts[run] == "pass")
            for run in range(self.runs)
        )

    @property
    def cancellation(self) -> bool:
        """True when the aggregate is flat and individual items are not.

        The finding this class was written to be able to state. A flat aggregate
        over a moving population is not stability; it is stability's signature
        without its substance, and a reviewer shown only the aggregate would
        certify the instrument.
        """
        return self.aggregate_spread == 0.0 and bool(self.unstable)

    @property
    def spread_stdev(self) -> float:
        """Population standard deviation of per-item total spreads, in points.

        Population rather than sample, for the reason `roleplay.consistency` gives
        about its own: these runs are the entire population of interest — every
        repeat of one fixed set against one build — rather than a draw from a
        larger one.
        """
        if not self.items:
            return 0.0
        spreads = [item.total_spread for item in self.items]
        mean = sum(spreads) / len(spreads)
        return round(
            math.sqrt(sum((s - mean) ** 2 for s in spreads) / len(spreads)), 3
        )

    def criterion_movement(self) -> dict[str, int]:
        """How many items each criterion moved on. The per-criterion league table."""
        counts = {name: 0 for name in CRITERIA}
        for item in self.items:
            for name in item.moved_criteria:
                counts[name] += 1
        return counts

    # -------------------------------------------------------------- printing

    def summary_line(self) -> str:
        return (
            f"{self.rubric_version}: {self.n - len(self.unstable)}/{self.n} items "
            f"fully stable across {self.runs} identical runs of {self.model} "
            f"(verdict moved on {len(self.verdict_unstable)}, numbers only on "
            f"{len(self.score_unstable)})"
        )

    def to_markdown(self) -> str:
        lines = [
            f"### Run-to-run stability of the score card — `{self.rubric_version}`",
            "",
            f"{self.runs} identical runs, same rubric, same model (`{self.model}`), "
            f"temperature 0, {self.n} items.",
            "",
            "| measure | value | denominator |",
            "|---|---|---|",
            f"| items fully stable (verdict, total and all five criteria) | "
            f"{self.n - len(self.unstable)} | {self.n} |",
            f"| items whose verdict moved | {len(self.verdict_unstable)} | {self.n} |",
            f"| items whose numbers moved but verdict held | "
            f"{len(self.score_unstable)} | {self.n} |",
            f"| cohort mean total, per run | "
            f"{', '.join(f'{m}' for m in self.aggregate_means)} | out of 20 |",
            f"| cohort pass count, per run | "
            f"{', '.join(str(c) for c in self.pass_counts)} | {self.n} |",
            f"| spread of per-item total spreads (population sd) | "
            f"{self.spread_stdev} | points |",
            "",
        ]
        movement = {k: v for k, v in self.criterion_movement().items() if v}
        if movement:
            lines += ["Which criteria moved, and on how many items:", ""]
            for name, count in sorted(movement.items(), key=lambda kv: -kv[1]):
                lines.append(f"- `{name}` — {count}/{self.n} items")
            lines.append("")
        if self.cancellation:
            lines += [
                "**The aggregate is flat and the items are not.** The cohort mean is "
                f"identical on all {self.runs} runs while {len(self.unstable)} of "
                f"{self.n} individual score cards changed. Movements in opposite "
                "directions cancel in the sum, so every statistic computed across "
                "items — mean total, pass rate, TPR, TNR — reads as a stable "
                "instrument. Only the per-item view shows otherwise, and the "
                "per-item view is the one a certification decision is taken from.",
                "",
            ]
        if not self.unstable:
            lines += [
                "Nothing moved. Every card was byte-stable across the repeats. That is "
                "a property of this set at temperature 0 and not a guarantee for "
                "unseen items — but an unstable scorer would have shown it here.",
                "",
            ]
        else:
            lines += ["Items that did not hold still:", ""]
            for item in self.unstable:
                lines.append(f"- {item.describe()}")
            lines.append("")
        return "\n".join(lines)


def stability_of(
    *,
    rubric_version: str,
    model: str,
    per_run: Sequence[Sequence[tuple[str, str, dict[str, int], int, str, bool]]],
) -> CriterionStability:
    """Assemble a stability report from per-run rows.

    Each run is a sequence of
    `(item_id, human_label, criteria, total, verdict, errored)` tuples. Deliberately
    a plain-data seam rather than taking scorers or recordings: the same arithmetic
    has to score runs replayed from committed fixtures, runs produced live, and
    runs stitched together from CI shards, and the way to guarantee that is to let
    exactly one function do the scoring.

    Refuses a ragged set of runs. Runs covering different items, or the same items
    in a different order, would silently compare item A's run-1 score against item
    B's run-2 score, and the resulting "instability" would be an indexing bug.
    """
    if len(per_run) < 2:
        raise ValueError(
            "stability needs at least two runs; one run cannot disagree with itself"
        )
    keys = [tuple(row[0] for row in run) for run in per_run]
    if len({keys_ for keys_ in keys}) != 1:
        raise ValueError(
            "the runs do not cover the same items in the same order, so a "
            "per-item comparison would be comparing different sessions: "
            f"{[len(k) for k in keys]} items per run"
        )

    items: list[ItemRuns] = []
    for index, item_id in enumerate(keys[0]):
        rows = [run[index] for run in per_run]
        items.append(
            ItemRuns(
                item_id=item_id,
                human_label=rows[0][1],
                criteria={
                    name: tuple(int(row[2].get(name, 0)) for row in rows)
                    for name in CRITERIA
                },
                totals=tuple(int(row[3]) for row in rows),
                verdicts=tuple(str(row[4]) for row in rows),
                errored=tuple(bool(row[5]) for row in rows),
            )
        )
    return CriterionStability(
        rubric_version=rubric_version,
        model=model,
        runs=len(per_run),
        items=tuple(items),
    )


#: Re-exported so a caller reporting a spread next to a threshold does not have to
#: import the scorer module for the one constant.
PASS_THRESHOLD: int = PASS_TOTAL
Verdict = Literal["pass", "fail", "errored"]
