# The documentation, indexed by question

**Presenting this to someone in ten minutes?** Start at
[WALKTHROUGH.md](WALKTHROUGH.md) — the guided path. [WIKI.md](WIKI.md) is the full
reference underneath it, not a thing to read aloud.

Behind this page: seventeen documents, 25,423 lines
(`ls docs/*.md | grep -v README | xargs wc -l`). You should never need to open
more than one of them. Find your question below, open the file next to it, stop.

If you have not run anything yet, close this page and run `make start`.

## I want to run something

| your question | the answer |
| --- | --- |
| What does this thing *do*? Show me, in a second. | `make start` — one finding, offline, no key |
| What can I run, and which of it costs money? | `make help` — grouped; four targets are marked MONEY+KEY |
| What do I run before I push? | `make gate`, and [GATES.md](GATES.md) for what each of its eight stages proves — and what it cannot catch |
| What are the CLI's six subcommands? | [cli.md](cli.md) |
| Which tests does my change actually need? | [TEST_SELECTION.md](TEST_SELECTION.md) — derived from the trace, and it refuses to guess |

## Something failed and I need it to stop

| your question | the answer |
| --- | --- |
| I ran something, it went red, now what? | **[DEBUGGING.md](DEBUGGING.md)** — every failure on that page was induced on purpose and the output is what actually happened |
| `git diff --exit-code` failed in CI and I did not touch that file. | [DEBUGGING.md](DEBUGGING.md), and [GATES.md](GATES.md) stages 4 and 5 for why those two stages write before they diff |
| The gate is green. Am I safe? | [GATES.md](GATES.md) — **replay is blind to a prompt change**, and that page names which live tier answers instead |

## I need to understand how it works

| your question | the answer |
| --- | --- |
| What do these words mean here? | [VOCABULARY.md](VOCABULARY.md) — this repo's terms against the names the field uses. [WIKI.md §12](WIKI.md#12-glossary) is the plain glossary |
| How does scoring work? | [WIKI.md §7](WIKI.md#7-the-scoring-model) |
| What is in a trace, field by field? | [trace_schema.md](trace_schema.md) — one JSONL file per session; every figure in the repo derives from it |
| Why is it built this way at all? | [WIKI.md §3](WIKI.md#3-the-one-idea-trace-first), then [WIKI.md §4](WIKI.md#4-the-sixteen-golden-rules) |
| What does one call look like, end to end? | [WIKI.md §6](WIKI.md#6-a-call-end-to-end) |
| Which file does what? | [WIKI.md §8](WIKI.md#8-the-file-by-file-reference) |
| What did it find, and what will it never find? | [WIKI.md §9](WIKI.md#9-what-it-found) and [§10](WIKI.md#10-limitations-stated-plainly) |
| The design decisions, and the arguments for them | [../DESIGN.md](../DESIGN.md) |

## I want to add something

| your question | the answer |
| --- | --- |
| How do I add a scenario? | [adding_a_scenario.md](adding_a_scenario.md) — two files |
| How do I extend the engine? | [WIKI.md §11](WIKI.md#11-how-to-extend-it) |

## The domains and the packs

| your question | the answer |
| --- | --- |
| The advisory sales-coaching domain — the tour | [ADVISORY_DEMO.md](ADVISORY_DEMO.md) |
| What is the adviser graded on, and why that? | [SCORECARD.md](SCORECARD.md) |
| How was the advisory suite scoped — surfaces, markets, regimes? | [ADVISORY_TEST_STRATEGY.md](ADVISORY_TEST_STRATEGY.md) |
| Retrieval and groundedness — the metrics and their denominators | [RAG_NOTES.md](RAG_NOTES.md) |

## Voice

| your question | the answer |
| --- | --- |
| The finding `make start` prints — in full | [SPOKEN_CALL.md](SPOKEN_CALL.md) |
| The two real speech vendors, and what running them revealed | [AUDIO_SUITE.md](AUDIO_SUITE.md) — including the language no vendor could synthesise |
| Why three rows go through real WebRTC and the rest do not | [AUDIO_TRANSPORT.md](AUDIO_TRANSPORT.md) |

## Proposals — written, not built

Neither of these describes code that exists. Read them as arguments, not as
documentation of the repository.

| document | what it proposes |
| --- | --- |
| [ENHANCEMENT_PLAN.md](ENHANCEMENT_PLAN.md) | options for what to build next, for the owner to choose from |
| [REAL_STACK_ARCHITECTURE.md](REAL_STACK_ARCHITECTURE.md) | moving from a simulated harness to one driving a real voice stack |

`_plan/` and `_research/` are the working notes behind those two. They are kept
for provenance and are not part of the reading path.
