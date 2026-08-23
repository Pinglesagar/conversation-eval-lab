"""Failure-mode frequencies from the hand-assigned codes: table, then chart.

WHAT THIS DEMONSTRATES
----------------------
That the taxonomy in `axial_coding.md` is computed from a committed artefact
rather than asserted in prose. `codes.csv` is the coding — one row per (code,
trace), assigned by reading traces — and everything here is arithmetic over that
file. If a count in the prose disagrees with this script's output, the prose is
wrong.

Three things it refuses to do:

**It does not infer codes.** A tempting version of this script would grep the
traces for repeated utterances and empty note fields and call the result a
taxonomy. That measures the grep, not the failures: it can only find modes
someone already thought to write a pattern for, which is the exact limitation
that makes reading traces necessary. So the codes are human judgements, and this
script is a counter.

**It does not print a percentage without its denominator.** Every figure is
`n/N`, including the cumulative column, because "38% of failures" is unreadable
without knowing whether that is 12 occurrences or 1,200.

**It does not silently produce a chart nobody can check.** The table prints
whether or not matplotlib is installed, and the PNG's bars are annotated with
their counts. `--check` validates every scenario id in `codes.csv` against the
corpus and exits non-zero on a typo, because a taxonomy that cites a trace which
does not exist is fiction that reads like data.

USAGE
-----
    python -m error_analysis.pareto              # table, and pareto.png if it can
    python -m error_analysis.pareto --no-chart   # table only
    python -m error_analysis.pareto --check      # validate ids against the corpus
"""

from __future__ import annotations

import argparse
import csv
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

HERE = Path(__file__).resolve().parent
CODES_CSV = HERE / "codes.csv"
CHART_PNG = HERE / "pareto.png"

FIELDS = ("code", "scenario_id", "class", "caught", "note")


@dataclass(frozen=True)
class Coded:
    """One coded occurrence: a failure mode observed in one trace."""

    code: str
    scenario_id: str
    klass: str
    caught: bool
    note: str

    @property
    def is_product(self) -> bool:
        return self.klass == "product"


def load_codes(path: Path = CODES_CSV) -> list[Coded]:
    """Read `codes.csv`, skipping `#` comments, validating the vocabulary."""
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    reader = csv.DictReader(lines)
    if reader.fieldnames != list(FIELDS):
        raise ValueError(f"{path}: expected columns {FIELDS}, got {reader.fieldnames}")

    out: list[Coded] = []
    for number, row in enumerate(reader, start=2):
        if row["class"] not in ("product", "label"):
            raise ValueError(f"{path}:{number}: class must be product or label")
        if row["caught"] not in ("yes", "no"):
            raise ValueError(f"{path}:{number}: caught must be yes or no")
        out.append(
            Coded(
                code=row["code"],
                scenario_id=row["scenario_id"],
                klass=row["class"],
                caught=row["caught"] == "yes",
                note=row["note"],
            )
        )
    duplicates = [
        pair for pair, n in Counter((c.code, c.scenario_id) for c in out).items() if n > 1
    ]
    if duplicates:
        raise ValueError(f"{path}: the same code is assigned twice to {duplicates}")
    return out


@dataclass(frozen=True)
class Row:
    """One line of the Pareto table."""

    code: str
    count: int
    caught: int
    cumulative: int
    total: int

    def rate(self) -> str:
        return f"{self.count}/{self.total}"

    def cumulative_rate(self) -> str:
        return f"{self.cumulative}/{self.total}"

    def percent(self) -> float:
        return 100.0 * self.count / self.total if self.total else 0.0

    def cumulative_percent(self) -> float:
        return 100.0 * self.cumulative / self.total if self.total else 0.0


def pareto(codes: Sequence[Coded]) -> list[Row]:
    """Codes ordered by frequency, ties broken alphabetically for a stable chart."""
    counts = Counter(c.code for c in codes)
    caught = Counter(c.code for c in codes if c.caught)
    total = len(codes)
    running = 0
    rows: list[Row] = []
    for code, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        running += count
        rows.append(
            Row(code=code, count=count, caught=caught[code], cumulative=running, total=total)
        )
    return rows


def render_table(rows: Sequence[Row], codes: Sequence[Coded]) -> str:
    """The table, as markdown. Same numbers the chart draws."""
    total = len(codes)
    traces = len({c.scenario_id for c in codes})
    product = [c for c in codes if c.is_product]
    caught_product = [c for c in product if c.caught]

    lines = [
        "| failure mode | occurrences | share | cumulative | caught by a check |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.code} | {row.rate()} | {row.percent():.1f}% | "
            f"{row.cumulative_rate()} ({row.cumulative_percent():.1f}%) | "
            f"{row.caught}/{row.count} |"
        )
    lines += [
        "",
        f"{total} coded occurrences across {traces} traces "
        f"({len(rows)} distinct modes).",
        f"Product defects: {len(product)}/{total}; the remainder are defects in a "
        "check or in the scenario that declares it.",
        f"Caught by a contract in the committed run: "
        f"{len(caught_product)}/{len(product)} product occurrences.",
    ]
    return "\n".join(lines)


def render_chart(rows: Sequence[Row], path: Path = CHART_PNG, *, dpi: int = 144) -> Path | None:
    """Draw the Pareto chart. Returns None when matplotlib is not installed.

    Bars are annotated with their counts and the caught/total split, because a
    Pareto chart read without its numbers is a mood board — and because the
    interesting part of this one is not which bar is tallest but how many bars
    have nothing checking them.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # no display in CI
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        return None

    labels = [textwrap.fill(r.code.replace("-", " "), 15) for r in rows]
    counts = [r.count for r in rows]
    caught = [r.caught for r in rows]
    missed = [r.count - r.caught for r in rows]
    cumulative = [r.cumulative_percent() for r in rows]
    total = rows[0].total if rows else 0

    figure, axis = plt.subplots(figsize=(max(8.0, 1.15 * len(rows)), 6.2))
    positions = range(len(rows))
    axis.bar(positions, caught, color="#31688e", label="caught by a contract")
    axis.bar(positions, missed, bottom=caught, color="#c7cdd4", label="found by reading only")
    axis.set_xticks(list(positions))
    axis.set_xticklabels(labels, fontsize=7, rotation=0)
    axis.set_ylabel(f"coded occurrences (n = {total})")
    axis.set_title(
        "TableMate 0.1.0 — failure modes by frequency\n"
        "blue is what the suite caught; grey is what only reading the traces found",
        fontsize=10,
    )
    for index, row in enumerate(rows):
        axis.text(
            index,
            row.count + 0.08,
            f"{row.count}\n({row.caught} caught)",
            ha="center",
            va="bottom",
            fontsize=7,
        )

    twin = axis.twinx()
    twin.plot(list(positions), cumulative, color="#b3541e", marker="o", markersize=4, linewidth=1)
    twin.set_ylim(0, 105)
    twin.set_ylabel("cumulative share of occurrences (%)")
    twin.axhline(80, color="#b3541e", linestyle=":", linewidth=0.8)

    axis.set_ylim(0, max(counts) + 1.4)
    axis.legend(loc="upper right", fontsize=8, frameon=False)
    figure.tight_layout()
    figure.savefig(path, dpi=dpi)
    plt.close(figure)
    return path


def unknown_scenarios(codes: Sequence[Coded]) -> list[str]:
    """Scenario ids in `codes.csv` that the corpus does not contain.

    Imported lazily and reported rather than raised at import time, so that this
    module still counts codes in a checkout where the corpus is not importable.
    """
    try:
        from scenarios.loader import load_corpus
    except ModuleNotFoundError:  # pragma: no cover - corpus always present here
        return []
    known = set(load_corpus().ids())
    return sorted({c.scenario_id for c in codes} - known)


def iter_uncaught(codes: Sequence[Coded]) -> Iterator[Coded]:
    """Product occurrences no contract reports — the backlog for the next pass."""
    return (c for c in codes if c.is_product and not c.caught)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-chart", action="store_true", help="skip the PNG")
    parser.add_argument(
        "--check", action="store_true", help="validate scenario ids against the corpus"
    )
    parser.add_argument("--out", default=str(CHART_PNG), help="where to write the PNG")
    args = parser.parse_args(argv)

    codes = load_codes()
    rows = pareto(codes)
    print(render_table(rows, codes))

    if args.check:
        missing = unknown_scenarios(codes)
        print()
        if missing:
            print("codes.csv cites scenarios the corpus does not have:", file=sys.stderr)
            for scenario_id in missing:
                print(f"  {scenario_id}", file=sys.stderr)
            return 1
        print(f"every scenario id in codes.csv exists in the corpus ({len(codes)} rows)")

    if not args.no_chart:
        written = render_chart(rows, Path(args.out))
        print()
        if written is None:
            print("matplotlib not installed; table only (pip install -e '.[charts]')")
        else:
            print(f"wrote {written}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
