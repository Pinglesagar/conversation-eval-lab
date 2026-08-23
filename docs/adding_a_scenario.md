# Adding a scenario

A scenario is two files: a row of YAML that says what the caller wants and what
must be true afterwards, and an entry in the caller fixture that says the exact
sentences they use. Both are data, both are reviewed in a pull request, and both
are validated before anything runs.

## 1. The row

`scenarios/<suite>/<id>.yaml`, where `<suite>` is `happy`, `edge`, `adversarial`
or `voice`, and `<id>` starts with the suite name and equals the file stem.

```yaml
id: happy-two-covers-thursday
title: A straightforward booking for two on a Thursday
persona: brisk_regular          # a file in scenarios/personas/, or an inline persona
tags: [booking]                 # from the closed vocabulary in scenarios/loader.py

goal:
  intent: book a table for two on Thursday evening
  facts:                        # everything the caller knows, as strings
    party_size: "2"
    date: Thursday
    time: 7:30pm
    name: Rachel Okonkwo
  on_request_only: [name]       # …and what they will not volunteer
  ask_patterns:                 # what counts as the agent asking for it
    name: ["your name", "name for the booking"]
  success_criteria:             # prose, for the reader; lab.checks owns assertions
    - a real booking exists for two people

tools:
  expected: [search_tables, create_booking]
  min_calls: {create_booking: 1}
  ordering:
    - first: search_tables
      then: create_booking
  args:
    - tool: create_booking
      arg: party_size
      op: eq
      ref: party_size           # "what the caller actually asked for"

promises: {}                    # every spoken commitment must be backed by a call

notes: >-                       # why this row exists; at least 20 characters
  The control for the group-booking rows. …
```

`on_request_only` is the field that turns a script into a probe: whether the
agent ever *asks* is the thing under test, so the caller withholds those facts
until the agent's own words match `ask_patterns`.

## 2. The caller's words

`fixtures/caller_scripts.yaml`:

```yaml
  happy-two-covers-thursday:
    script:
      - "Hello, I would like to book a table for two on Thursday at 7:30pm."
    closing: "That is all, thank you."
```

Rules that are easy to get wrong:

- **Do not script the gated facts.** They are released by the caller model when
  the agent asks. Scripting them answers questions that were never put, and the
  transcript then shows the caller volunteering details the agent already had.
  (Two rows in this corpus had exactly that bug in their first draft; both showed
  up as a spurious `no-progress-loop` failure.)
- **Do not script a persona's behaviour.** A reluctant persona
  (`cooperativeness < 0.5`) stalls once before answering, on its own.
- **Seed state rather than stubbing a tool.** A row that names a booking
  reference the diary does not hold, or that needs a full sitting, says so:

```yaml
    seed:
      - book_out: { date: Saturday, time: 8pm }
      - ensure_booking: { ref: TM-9001, name: Okonkwo, date: friday,
                          time: 7pm, party_size: 4 }
```

  Stubbing the tool's answer instead would make the agent's skip-logic
  untestable, because its next move depends on the shape of a real refusal.

## 3. Check that the row can fail

```bash
evallab validate --coverage
evallab run --scenario <id> --transcript -k 1 --no-baseline
```

Read the transcript. Then read the check report and ask the only question that
matters about a new row: **could this contract ever have failed?** A tracked field
whose value the caller never says, a `ref:` that does not resolve, an argument
predicate on a forbidden tool — all of those pass without asserting anything, so
the loader rejects them by name. It cannot catch a contract that is merely weak:
`edge-modify-across-group-threshold` requires `modify_booking` to be called with
`changes` *present*, and the trace shows it called with a change set that does not
contain the change the caller asked for. Green row, wrong diary. See
`error_analysis/FINDINGS.md`, finding 5.

If the row is a known gap — a defect this build has and you want tracked rather
than fixed today — declare it:

```yaml
expected_failure:
  contracts: [tools, promise-kept]
  since: first observed in the 0.1.0 case-study build
  expectation: >-
    We expect the caller to be told the table is confirmed and no create_booking
    call to appear anywhere in the trace. …
```

A declared gap that *stops* failing breaks the build, which is the point: nobody
notices a fixed bug, and nobody notices a check that went quiet, unless one of
them is loud.

## 4. Update the baseline in the same change

A new row usually means new findings, and the regression gate compares against
the committed reference run:

```bash
make reference          # regenerates fixtures/replay_run and shows the diff
```

Review that diff as part of the pull request. It is the record of what the suite
learned, and it is the only place where "this used to fail and now it does not"
is visible to a human.

## Voice rows

A row in the `voice` suite must declare at least one perturbation, and a row
outside it must not:

```yaml
voice:
  perturbations:
    - name: add_noise
      params: { snr_db: 10 }
```

`evallab run` drives the text adapter, so it counts the voice rows and reports
them as not driven rather than running them as text — a perturbation row whose
audio is never perturbed produces a verdict that says nothing about audio. The
audio path lives in `lab/voice/`.
