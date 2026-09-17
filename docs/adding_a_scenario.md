# Adding a scenario

A scenario is one YAML file in `scenarios/roleplay/<suite>/`. The filename is the
id. Nothing in the harness knows the difference between a row somebody wrote today
and one that has been there since the beginning, which is the point: a test case is
data, and a compliance specialist who will never open a terminal can write and
review one.

If you would rather edit a spreadsheet than YAML, `make scenarios-excel` exports the
whole corpus to a four-sheet workbook and imports it back, comparing meaning rather
than bytes, so a round trip through Excel changes nothing.

---

## The file

```yaml
id: pitch-close-with-a-summary       # must equal the filename
title: The ask, with the session recapped first
customer: comparison_shopper         # a profile in scenarios/roleplay/customers/
tags: [closing, control, objection-handling, disclosure]
# jurisdiction: eu-retail            # optional; defaults to eu-retail
# language: es                       # optional; defaults to en

trainee:
  role: retail investment adviser
  turns:
    - Before I show you anything, what would you want this money to be doing for you in ten years?
    - >-
      The fund I have in mind is a balanced growth fund. Your capital at risk is
      real: you could get back less than you put in, and past performance is not
      a guide to future performance.
    - >-
      So to summarise: your capital at risk, 0.68 per cent a year. Shall we get
      the paperwork started?

expectation:
  human_verdict: pass                # what a competent reviewer would decide
  reason: >-
    Three open questions, both objections answered on their own terms, all three
    disclosures in registered wording, and an ask that comes after a recap.

tools:
  expected: [record_disclosure, resolve_objection, score_session]
  forbidden: [flag_compliance_risk]
  min_calls: {record_disclosure: 3, resolve_objection: 2}
  args:
    - tool: score_session
      arg: total
      op: eq
      value: 20

trainee_phrases:
  required:
    - So to summarise

notes: >-
  Why this row exists, in prose. Not optional.
```

Every key above is validated on load. A tool name outside the closed vocabulary, a
tag that is not in the tag list, or an `expected_failure` naming a contract the row
does not declare is a **load error**, not a silent pass at run time:

```bash
python -m roleplay.corpus --coverage --list
```

---

## The five rules

### 1. The vocabularies are closed

Tool names, tags and suites all come from fixed lists. A typo cannot become a new
tag that silently matches nothing; it stops the load and names itself. That is the
single most valuable property of the loader, because the alternative failure is
invisible: a check that can never fire is green forever and measures nothing.

### 2. Every assertion must be able to fail

Declaring `min_calls: {record_disclosure: 1}` on a row where the script discharges
three requirements asserts nothing. The interesting assertion is the one that would
break if the product changed. `locale-eu-three-disclosures-in-one-turn` is the
example worth reading: it is the only row that pins *which turn* discharged each
requirement, and the note says exactly why —

> "how many disclosures were recorded" and "which turn recorded them" are different
> questions with the same answer on every other row. A register that dropped a
> second match inside one utterance would still record three codes and would still
> look right everywhere except here.

### 3. `notes:` is not optional

A row without a written reason is a row nobody can safely delete in a year. Say what
would have to break for this row to go red, and what a green means. The notes field
is where the corpus explains itself to its next reader, and several of them name
their own pair: `pitch-close-without-a-summary` runs the same script with the recap
removed and scores nineteen, so the pair prices the summary at exactly one mark
without an argument about tone.

### 4. A row that is *expected* to fail says so

38 of the 70 rows carry `expected_failure`, because a corpus of only healthy calls
cannot tell you whether the grader notices an unhealthy one. Declare it, and declare
*which contract* is expected to fail:

```yaml
expected_failure:
  contracts: [tools, score-claims-backed]
  since: first observed in the 0.1.0 roleplay pack
  expectation: >-
    We expect this session to be certified. The rubric awards full marks for the
    disclosure criterion while the ledger holds two of the three codes this
    jurisdiction requires.
```

An undeclared failure is a build failure; a declared one is the row working. The
loader refuses an `expected_failure` naming a contract the row never declares,
because that expectation could never fire.

### 5. Use the registered wording, or deliberately do not

The register matches a closed list of approved phrasings, normalised. A trainee turn
that says "there is some risk, of course" records nothing, and that is correct: the
register is the instrument the grader's compliance claims are measured against, and
an instrument that credits a paraphrase cannot catch a grader that credits one.

If you want a row that *tests* that strictness, write the near-miss deliberately and
declare what should happen. Five rows do exactly that.

---

## Before you commit

```bash
python -m roleplay.corpus --coverage --list    # the row loads and is counted
make roleplay-demo                             # it runs, and the verdict is what you expected
```

If your row fails and you did not expect it to, work through
[DEBUGGING.md](DEBUGGING.md) before changing the check — the cheapest source of a
disagreement is usually your own new row.
