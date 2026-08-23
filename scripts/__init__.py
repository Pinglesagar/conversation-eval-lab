"""Developer scripts. Not part of the installed package.

`pyproject.toml` restricts the distribution to `lab*` and `tablemate*`, so nothing
here ships. This file exists only so the test suite can import a generator and
check that regenerating a fixture reproduces the committed one — a generator that
cannot be re-run against its own output is a generator whose output nobody can
verify.
"""
