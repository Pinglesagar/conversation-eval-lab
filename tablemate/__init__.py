"""TableMate — the system under test.

WHAT THIS DEMONSTRATES
----------------------
A harness is only credible if it catches real bugs, and a harness demonstrated
against a working agent proves nothing at all: green results are equally
consistent with a good agent and a blind test suite. So the system under test
here is deliberately imperfect. It is a multi-agent restaurant-booking assistant
(a greeter routing to booking, modification and policy specialists) carrying a
small number of documented defects, each planted to be caught by a specific
class of check.

The defects are real code paths rather than feature flags, so they reproduce
deterministically in recorded fixtures, and they are documented in exactly one
place: `tablemate/SEEDED_BUGS.md`. Nothing in `lab` knows they exist.

Empty in the foundation commit: the agents, their tools and the seeded defects
are built in the next step. Nothing in `lab` imports from this package, and
nothing here imports a check.
"""
