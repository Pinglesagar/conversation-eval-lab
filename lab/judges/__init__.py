"""Model-graded checks.

WHAT THIS DEMONSTRATES
----------------------
Judges are for the residue: the properties no assertion can express, like tone,
or whether a refusal was appropriate. They are also the least trustworthy part
of any harness, so the plan is that every judge here ships with an agreement
measurement against hand-labelled examples, and any judge whose agreement is not
reported is not used.

Empty in the foundation commit. Judges reach a provider through `litellm`,
imported lazily so that offline test collection never touches the network, and
every judged path has a recorded fixture so it replays with no API key.
"""
