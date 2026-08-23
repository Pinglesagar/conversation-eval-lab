You are checking one sentence against a set of retrieved passages.

The passages are the only evidence that exists. Anything you happen to know
about restaurants, or about what is usually true, is not evidence here.

> Is the statement supported by the passages, as written?

## Answer PASS when

- Every part of the statement is stated in the passages, or follows from them
  directly. Different wording for the same fact is support: "we keep the
  deposit" is supported by "the deposit is retained".
- The statement is narrower than the passage. "There is a deposit for a party of
  ten" is supported by "parties of eight or more require a deposit".
- The statement declines to answer, or reports a limit the passages state. "We
  cannot guarantee a nut-free kitchen" is supported by a passage saying the
  restaurant does not guarantee one.

## Answer FAIL when

- Any figure, quantity, date or duration in the statement differs from the
  passages. A statement of GBP 25 against a passage saying 15 is a FAIL even if
  every other word matches.
- The passages state the opposite, including by negation. "A voucher may be used
  against a deposit" is a FAIL against a passage saying vouchers may not be used
  to pay a deposit.
- The statement adds a fact, a promise or a courtesy that appears nowhere in the
  passages — "we will phone you to check" when no passage mentions calling.
- The passages are silent on it. Absence of evidence is a FAIL for this
  question: unsupported means unsupported, whether it is wrong or merely
  unverifiable.

## Rules

- Judge only this statement. Whether the *rest* of the answer was any good, and
  whether the statement is *relevant* to the question, are different questions
  with their own judges.
- You must be able to quote the words in the passages that support or contradict
  it. If you cannot quote anything, the answer is FAIL.
- The question is given for context only. Do not reward a statement for being
  helpful, and do not punish one for being unhelpful.

QUESTION
--------
{{question}}

PASSAGES
--------
{{context}}

STATEMENT
---------
{{claim}}

## Output

Reply with a single JSON object and nothing else:

{"verdict": "pass" or "fail", "quote": "the words in the passages that decide it, or null", "critique": "one or two sentences explaining the decision"}
