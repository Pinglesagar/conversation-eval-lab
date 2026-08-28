"""Tests for `lab.stats` — the closed forms, against values worked by hand.

WHAT THIS DEMONSTRATES
----------------------
A statistics module tested against a second implementation of the same formula
agrees with itself, including where both are wrong. So every figure asserted here
is either arithmetic written out in the test docstring, a textbook identity that
holds exactly, or a figure already quoted in prose elsewhere in this repository —
which makes these tests the check that the prose and the code agree.

The last of those is the one that matters. `docs/ENHANCEMENT_PLAN.md`,
`lab/judges/hallucinated_confirmation/__init__.py` and
`tests/test_checks_paraphrase.py` all quote Wilson bounds in sentences. If the
implementation ever drifts from them, a reader has two numbers and no way to tell
which is wrong; these tests make that a test failure instead.
"""

from __future__ import annotations

import math

import pytest

from lab.stats import (
    Z_95,
    format_interval,
    min_trials_for_lower_bound,
    rule_of_three_upper_bound,
    wilson_interval,
    wilson_lower_bound,
    z_for_confidence,
)


# --------------------------------------------------------------------------- #
# The quantile
# --------------------------------------------------------------------------- #


def test_the_95_percent_quantile_is_the_familiar_constant() -> None:
    """1.959963985, the number every statistics table prints as 1.96."""
    assert Z_95 == pytest.approx(1.959963985, abs=1e-9)
    assert z_for_confidence(0.95) == Z_95


def test_a_higher_confidence_needs_a_wider_quantile() -> None:
    assert z_for_confidence(0.99) > z_for_confidence(0.95) > z_for_confidence(0.90)
    assert z_for_confidence(0.99) == pytest.approx(2.5758293, abs=1e-6)


def test_confidence_outside_the_open_unit_interval_is_refused() -> None:
    for bad in (0.0, 1.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="strictly between 0 and 1"):
            z_for_confidence(bad)


# --------------------------------------------------------------------------- #
# Wilson, against the figures this repository already quotes in prose
# --------------------------------------------------------------------------- #


def test_wilson_reproduces_every_bound_quoted_in_the_tree() -> None:
    """The table in `docs/ENHANCEMENT_PLAN.md` and the prose in two modules.

    8/8 is worked by hand in the docstring of `lab.stats.wilson_interval`:

        z^2 = 3.8414588,  n = 8,  p = 1
        denominator = 1 + 3.8414588/8            = 1.48018235
        centre      = (1 + 3.8414588/16) / that  = 0.8378
        half        = (3.8414588/16) / that      = 0.16220
        lower       = 0.8378 - 0.16220           = 0.6756
    """
    expected = {
        (3, 3): (0.439, 1.000),
        (5, 5): (0.566, 1.000),
        (8, 8): (0.676, 1.000),
        (16, 16): (0.806, 1.000),
        (15, 15): (0.796, 1.000),
        (12, 12): (0.758, 1.000),
        (11, 12): (0.646, 0.985),
        (2, 8): (0.071, 0.591),
        (9, 32): (0.156, 0.454),
        (36, 38): (0.827, 0.985),
    }
    for (successes, trials), (low, high) in expected.items():
        got = wilson_interval(successes, trials)
        assert got[0] == pytest.approx(low, abs=0.0005), f"{successes}/{trials} lower"
        assert got[1] == pytest.approx(high, abs=0.0005), f"{successes}/{trials} upper"


def test_a_perfect_score_has_an_upper_limit_of_exactly_one() -> None:
    """At p = 1 the algebra collapses: centre + half = denominator/denominator.

    Worth pinning, because it is the property that makes Wilson the right choice
    here and Wald the wrong one — the interval says "up to 1.0" and puts the whole
    claim in the lower bound.
    """
    for n in (1, 8, 16, 24, 500):
        low, high = wilson_interval(n, n)
        assert high == 1.0
        assert 0.0 < low < 1.0


def test_zero_successes_mirror_a_perfect_score() -> None:
    """0/8 is 8/8 reflected: [0.000, 0.324] against [0.676, 1.000]."""
    low, high = wilson_interval(0, 8)
    assert low == 0.0
    assert high == pytest.approx(1.0 - wilson_lower_bound(8, 8), abs=1e-12)


def test_the_interval_narrows_as_the_set_grows() -> None:
    """The whole argument for 'label more items', as a monotonicity property."""
    widths = [
        wilson_interval(n, n)[1] - wilson_interval(n, n)[0] for n in (4, 8, 16, 32, 64)
    ]
    assert widths == sorted(widths, reverse=True)


def test_a_wider_confidence_gives_a_wider_interval() -> None:
    at_95 = wilson_interval(8, 8, confidence=0.95)
    at_99 = wilson_interval(8, 8, confidence=0.99)
    assert at_99[0] < at_95[0]


def test_an_explicit_z_overrides_the_confidence_level() -> None:
    """The seam that lets `lab.cli` delegate instead of keeping a second copy."""
    assert wilson_lower_bound(8, 8, z=Z_95) == pytest.approx(wilson_lower_bound(8, 8))


def test_a_non_proportion_is_refused_rather_than_clamped() -> None:
    with pytest.raises(ValueError, match="at least one trial"):
        wilson_interval(1, 0)
    with pytest.raises(ValueError, match="not a proportion"):
        wilson_interval(4, 3)
    with pytest.raises(ValueError, match="not a proportion"):
        wilson_interval(-1, 3)


# --------------------------------------------------------------------------- #
# The rule of three
# --------------------------------------------------------------------------- #


def test_the_rule_of_three_matches_the_sentences_the_repo_quotes() -> None:
    """8/8 is consistent with a 37.5% miss rate; 16/16 with 18.8%."""
    assert rule_of_three_upper_bound(8) == pytest.approx(0.375)
    assert rule_of_three_upper_bound(16) == pytest.approx(0.1875)
    assert rule_of_three_upper_bound(15) == pytest.approx(0.200)


def test_the_rule_of_three_agrees_with_wilson_to_within_a_few_points() -> None:
    """It is a rule of thumb, and the test says how good a one, rather than
    asserting it is exact. At n = 8 the two differ by under 0.06; by n = 100 the
    gap is under 0.01, which is the regime the approximation was written for."""
    for n, tolerance in ((8, 0.06), (30, 0.02), (100, 0.01)):
        wilson_error_bound = 1.0 - wilson_lower_bound(n, n)
        assert abs(wilson_error_bound - rule_of_three_upper_bound(n)) < tolerance


def test_the_rule_of_three_never_exceeds_one() -> None:
    assert rule_of_three_upper_bound(1) == 1.0
    assert rule_of_three_upper_bound(2) == 1.0


# --------------------------------------------------------------------------- #
# How many items it would actually take — the number that makes the caveat
# actionable rather than rhetorical
# --------------------------------------------------------------------------- #


def test_twenty_two_perfect_trials_are_needed_to_clear_the_default_gate() -> None:
    """The repository's headline number, and it is checked from both sides.

        21 perfect trials -> lower bound 0.8454  (below 0.85)
        22 perfect trials -> lower bound 0.8513  (clears it)

    The shipped judge has 8 positives and 16 negatives, so neither of its gated
    rates could clear 0.85 on the lower bound however perfectly it scored.
    """
    assert min_trials_for_lower_bound(0.85) == 22
    assert wilson_lower_bound(21, 21) == pytest.approx(0.8454, abs=0.0005)
    assert wilson_lower_bound(22, 22) == pytest.approx(0.8513, abs=0.0005)


def test_a_lower_threshold_needs_fewer_items() -> None:
    assert min_trials_for_lower_bound(0.80) == 16
    assert min_trials_for_lower_bound(0.90) > min_trials_for_lower_bound(0.85)


def test_a_threshold_of_zero_is_met_by_a_single_trial() -> None:
    assert min_trials_for_lower_bound(0.0) == 1


def test_a_threshold_of_one_is_unreachable_and_says_so() -> None:
    """No finite perfect sample proves a rate of exactly 1.0, and the function
    raises rather than looping to its limit in silence."""
    with pytest.raises(ValueError, match="no perfect sample below"):
        min_trials_for_lower_bound(1.0, limit=200)


def test_a_threshold_outside_zero_to_one_is_refused() -> None:
    with pytest.raises(ValueError, match=r"threshold must be in \[0, 1\]"):
        min_trials_for_lower_bound(1.5)


# --------------------------------------------------------------------------- #
# Formatting — one renderer, so two artefacts print the same number the same way
# --------------------------------------------------------------------------- #


def test_an_interval_renders_with_three_places_and_square_brackets() -> None:
    assert format_interval(wilson_interval(8, 8)) == "[0.676, 1.000]"
    assert format_interval(None) == "undefined"
    assert format_interval((0.5, 0.75), places=2) == "[0.50, 0.75]"


def test_nothing_here_needs_a_third_party_package() -> None:
    """The zero-dependency claim, asserted rather than asserted in a docstring."""
    import lab.stats as stats

    source = (stats.__file__ or "").replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()
    for banned in ("import numpy", "import scipy", "from scipy", "from numpy"):
        assert banned not in text
    assert math is not None  # the only maths import this module needs
