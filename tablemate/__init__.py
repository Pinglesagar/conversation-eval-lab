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

WHAT IS WHERE
-------------
    tablemate.store           tables, the booking diary, the policy sheet
    tablemate.tools           the five tools, and the toolbox that records them
    tablemate.understanding   routing and slot extraction — every decision
    tablemate.agents          the four agents, their briefs and the router
    tablemate.runtime         the adapter: one callable turn, three backends
    tablemate.__main__        a runner, not part of the system: drives the live
                              backend, records its cassette, scores the result

Nothing in `lab` imports from this package. In the other direction, exactly one
module *of the system* — `tablemate.runtime`, the adapter — imports `lab`, and it
imports three type names to build a reply with. Everything that decides, acts or
remembers is `lab`-free, which is what makes the harness's central claim (an
instrument pointed at a system, not a framework the system must adopt) checkable
rather than aspirational. `tablemate.__main__` imports `lab` too, and is exempt for
a different reason: it is a runner that drives the harness over this system, in the
same way `lab.cli` is, and nothing in the system imports it. Both halves of that are
asserted in `tests/test_tablemate_agents.py`.

THREE BACKENDS, TWO VARIABLES
-----------------------------
`ScriptedBackend` decides and speaks in code, and is the default. `PhrasingBackend`
lets a model choose the words for a line the code decided. `LLMBackend` hands the
decisions over too: each desk gets its remit as a prompt, its allow-list as tool
schemas and its brief as its only memory, and the model picks the tools, the
handoffs and the hang-up. All three produce the same shape of trace, which is what
lets one set of contracts read all three — and the seeded defects survive the move
to a model because they were never switches, only prompts and briefs.

Nothing here imports a check, and no module here mentions a defect.
"""

from tablemate.runtime import TableMate, build_agent

__all__ = ["TableMate", "build_agent"]
