"""Rendering results for humans.

WHAT THIS DEMONSTRATES
----------------------
One rule governs everything in this package: print every rate with its numerator
and denominator. "83% pass" hides whether that was 5 of 6 or 830 of 1000, and
those two numbers warrant completely different conclusions. Naked percentages
are the most common way an evaluation report misleads its own author.

Empty in the foundation commit, apart from the markdown rendering that
`lab.voice.calibration` does for its own report. Charts use `matplotlib` from
the optional `[charts]` extra, so the core install stays small and the test
suite never depends on a plotting backend.
"""
