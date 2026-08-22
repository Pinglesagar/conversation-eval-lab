"""Test suite.

Every test here runs offline with zero API keys. Anything that would talk to a
live provider is marked `live` and skipped unless its opt-in environment
variable is set, and has a recorded fixture that replays deterministically in
its place.
"""
