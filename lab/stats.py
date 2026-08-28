"""The closed-form statistics this repository is allowed to print.

WHAT THIS DEMONSTRATES
----------------------
One implementation of each statistic, in one file, with the hand-worked values it
must reproduce written into its own docstring. Three modules needed a Wilson bound
and two of them had grown their own copy; a repository whose argument is that an
instrument must be measured before it is trusted cannot ship the measuring
arithmetic twice and hope the copies agree.

Everything here is closed form and standard library only. No scipy, no numpy, no
optional extra. That is not minimalism for its own sake: a number a reader cannot
recompute from the counts in the report is a claim rather than a result, and the
cheapest way to keep that promise is arithmetic short enough to check by hand.

WHY WILSON AND NOT WALD
-----------------------
The interval everyone writes first is Wald: `p +- z*sqrt(p(1-p)/n)`. It is
degenerate at exactly the point that matters here. At `p = 1` its half-width is
zero, so it reports `[1.000, 1.000]` for 8 correct answers out of 8 — it asserts
the true rate is exactly 1.0, which is the claim an interval exists to refuse.
Every headline rate in this repository is a 1.000 over a few dozen items, so Wald
would print a confident nothing on every line that needs an interval most.

The Wilson score interval is the inversion of the score test. It never leaves
[0, 1], it has sensible width at the boundaries, and at `p = 1` its upper limit is
exactly 1.0 while its lower limit falls away with `n` — which is the shape of the
honest statement: "no observed errors in eight tries" bounds the true rate below,
and says nothing above.

    successes/trials    Wilson 95%          reading
    3/3                 [0.439, 1.000]      three passes buy almost nothing
    5/5                 [0.566, 1.000]
    8/8                 [0.676, 1.000]      the headline TPR of the shipped judge
    16/16               [0.806, 1.000]
    2/8                 [0.071, 0.591]
    9/32                [0.156, 0.454]
    36/38               [0.827, 0.985]

THE RULE OF THREE, WHICH IS THE SAME FACT IN THE FORM PEOPLE REMEMBER
---------------------------------------------------------------------
With zero observed errors in `n` trials, the 95% upper bound on the true error
rate is approximately `3/n`. So 8/8 is consistent with a true miss rate up to
37.5% and 16/16 with up to 18.8%. It is the one-line version of a Wilson lower
bound and the sentence to have ready when somebody reads a 1.000 as "perfect".

WHAT AN INTERVAL HERE DOES NOT COVER
------------------------------------
A Wilson interval is binomial sampling error over *items*, and it assumes the
judge's answer for a given item is fixed. `lab.judges.calibration.SelfConsistency`
shows that it is not: identical runs of one prompt at temperature 0 move
individual verdicts. The two uncertainties are separate and this module only
computes the first. The reports print both, side by side, and never add them —
see `lab.judges.calibration.ReplicateBands`.

SCOPE
-----
Pure functions over integers and floats. Nothing here imports from the rest of
`lab`, reads the clock, touches the environment or raises on anything but a
malformed input, so it is importable from any layer — including `lab.report`,
which deliberately imports no measurement code.
"""

from __future__ import annotations

import math
from statistics import NormalDist

__all__ = [
    "Z_95",
    "z_for_confidence",
    "wilson_interval",
    "wilson_lower_bound",
    "rule_of_three_upper_bound",
    "min_trials_for_lower_bound",
    "format_interval",
]

#: The two-sided 95% normal quantile. Named because it appears in prose in several
#: artefacts and a reader should be able to find the constant behind it.
Z_95: float = NormalDist().inv_cdf(0.975)


def z_for_confidence(confidence: float) -> float:
    """The two-sided normal quantile for a confidence level in (0, 1).

    `statistics.NormalDist.inv_cdf` is standard library, so an arbitrary
    confidence level costs no dependency. 0.95 returns `Z_95` exactly.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError(
            f"confidence must be strictly between 0 and 1, got {confidence!r}"
        )
    return NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)


def _check_proportion(successes: int, trials: int) -> None:
    if trials <= 0:
        raise ValueError(f"an interval needs at least one trial, got {trials}")
    if not 0 <= successes <= trials:
        raise ValueError(
            f"{successes} of {trials} is not a proportion: successes must be "
            "between 0 and trials"
        )


def wilson_interval(
    successes: int, trials: int, *, confidence: float = 0.95, z: float | None = None
) -> tuple[float, float]:
    """The Wilson score interval on a proportion, clamped to [0, 1].

    Closed form:

        centre = (p + z^2/2n) / (1 + z^2/n)
        half   = z/(1 + z^2/n) * sqrt(p(1-p)/n + z^2/4n^2)

    At `p = 1` the half-width equals `z^2/2n` divided by the same denominator, so
    `centre + half` is exactly 1.0 and `centre - half` is the number that carries
    the whole claim. That is why the lower bound is the figure the gate reads and
    the upper bound is never quoted alone.

    `z` overrides the quantile directly, for callers that already hold one. It
    exists so there is exactly one implementation of this arithmetic in the tree:
    `lab.cli` needs the bound at a fixed z and now delegates here rather than
    keeping a second copy that could drift.
    """
    _check_proportion(successes, trials)
    if z is None:
        z = z_for_confidence(confidence)
    p_hat = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    centre = (p_hat + z2 / (2 * trials)) / denominator
    half = (z / denominator) * math.sqrt(
        p_hat * (1.0 - p_hat) / trials + z2 / (4 * trials * trials)
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


def wilson_lower_bound(
    successes: int, trials: int, *, confidence: float = 0.95, z: float | None = None
) -> float:
    """Just the lower limit — the half of the interval that bounds a claim."""
    return wilson_interval(successes, trials, confidence=confidence, z=z)[0]


def rule_of_three_upper_bound(trials: int) -> float:
    """The 95% upper bound on the true error rate after zero observed errors.

    `3/n`, the standard approximation, valid for `n` above roughly 30 and quoted
    here at smaller `n` as what it is — a rule of thumb that agrees with the
    Wilson bound to within a couple of points and is the version a reader can
    check in their head. Only meaningful when the observed error count is zero;
    the caller is responsible for that, because a function cannot tell.
    """
    if trials <= 0:
        raise ValueError(f"the rule of three needs at least one trial, got {trials}")
    return min(1.0, 3.0 / trials)


def min_trials_for_lower_bound(
    threshold: float, *, confidence: float = 0.95, limit: int = 100_000
) -> int:
    """Perfect trials needed before the Wilson lower bound clears `threshold`.

    The answer to "how many more items would it take". With a perfect score the
    lower bound rises monotonically in `n`, so this is a search rather than an
    inversion, and it is exact rather than approximate.

    At the repository's default gate of 0.85 the answer is **22** consecutive
    correct answers *in the class being gated* — not 22 items, 22 positives for
    TPR and 22 negatives for TNR. The shipped judge has 8 and 16, which is why
    its gate currently passes on a point estimate and not on evidence.
    (21 perfect trials give a lower bound of 0.845; 22 give 0.851.)
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"a threshold must be in [0, 1], got {threshold!r}")
    for n in range(1, limit + 1):
        if wilson_lower_bound(n, n, confidence=confidence) >= threshold:
            return n
    raise ValueError(
        f"no perfect sample below {limit} trials reaches a lower bound of {threshold}"
    )


def format_interval(bounds: tuple[float, float] | None, *, places: int = 3) -> str:
    """`[0.676, 1.000]`, or `undefined` when there was nothing to measure.

    One formatter, so that an interval reads the same in a markdown table, a CI
    log and a JSON field, and a reader comparing two artefacts is comparing
    numbers rather than renderings.
    """
    if bounds is None:
        return "undefined"
    low, high = bounds
    return f"[{low:.{places}f}, {high:.{places}f}]"
